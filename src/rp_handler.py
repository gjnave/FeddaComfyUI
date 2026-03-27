"""
RunPod Serverless Handler for FeddaComfyUI.

Adapted from the official runpod-workers/worker-comfyui handler.
Receives workflow JSON via RunPod API, submits to local ComfyUI,
monitors execution via WebSocket, and returns output images.
"""

import runpod
from runpod.serverless.utils import rp_upload
import json
import urllib.parse
import time
import os
import requests
import base64
from io import BytesIO
import websocket
import uuid
import tempfile
import traceback
import logging

from network_volume import is_network_volume_debug_enabled, run_network_volume_diagnostics

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

COMFY_API_AVAILABLE_INTERVAL_MS = int(os.environ.get("COMFY_API_AVAILABLE_INTERVAL_MS", 50))
COMFY_API_AVAILABLE_MAX_RETRIES = int(os.environ.get("COMFY_API_AVAILABLE_MAX_RETRIES", 0))
COMFY_API_FALLBACK_MAX_RETRIES = 500
COMFY_PID_FILE = "/tmp/comfyui.pid"
WEBSOCKET_RECONNECT_ATTEMPTS = int(os.environ.get("WEBSOCKET_RECONNECT_ATTEMPTS", 5))
WEBSOCKET_RECONNECT_DELAY_S = int(os.environ.get("WEBSOCKET_RECONNECT_DELAY_S", 3))
COMFY_HOST = "127.0.0.1:8188"
REFRESH_WORKER = os.environ.get("REFRESH_WORKER", "false").lower() == "true"


def _comfy_server_status():
    try:
        resp = requests.get(f"http://{COMFY_HOST}/", timeout=5)
        return {"reachable": resp.status_code == 200, "status_code": resp.status_code}
    except Exception as exc:
        return {"reachable": False, "error": str(exc)}


def _get_comfyui_pid():
    try:
        with open(COMFY_PID_FILE, "r") as f:
            return int(f.read().strip())
    except (FileNotFoundError, ValueError):
        return None


def _is_comfyui_process_alive():
    pid = _get_comfyui_pid()
    if pid is None:
        return None
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _attempt_websocket_reconnect(ws_url, max_attempts, delay_s, initial_error):
    last_error = initial_error
    for attempt in range(max_attempts):
        srv_status = _comfy_server_status()
        if not srv_status["reachable"]:
            print(f"[HANDLER] ComfyUI HTTP unreachable — aborting reconnect")
            raise websocket.WebSocketConnectionClosedException(
                "ComfyUI HTTP unreachable during websocket reconnect"
            )
        print(f"[HANDLER] Reconnect attempt {attempt + 1}/{max_attempts}...")
        try:
            new_ws = websocket.WebSocket()
            new_ws.connect(ws_url, timeout=10)
            print("[HANDLER] Websocket reconnected.")
            return new_ws
        except (websocket.WebSocketException, ConnectionRefusedError, OSError) as e:
            last_error = e
            if attempt < max_attempts - 1:
                time.sleep(delay_s)

    raise websocket.WebSocketConnectionClosedException(
        f"Failed to reconnect. Last error: {last_error}"
    )


def validate_input(job_input):
    if job_input is None:
        return None, "Please provide input"
    if isinstance(job_input, str):
        try:
            job_input = json.loads(job_input)
        except json.JSONDecodeError:
            return None, "Invalid JSON format in input"

    workflow = job_input.get("workflow")
    if workflow is None:
        return None, "Missing 'workflow' parameter"

    images = job_input.get("images")
    if images is not None:
        if not isinstance(images, list) or not all(
            "name" in img and "image" in img for img in images
        ):
            return None, "'images' must be a list of objects with 'name' and 'image' keys"

    return {"workflow": workflow, "images": images}, None


def check_server(url, retries=0, delay=50):
    print(f"[HANDLER] Checking ComfyUI API at {url}...")
    delay = max(1, delay)
    log_every = max(1, int(10_000 / delay))
    attempt = 0

    while True:
        process_status = _is_comfyui_process_alive()
        if process_status is False:
            print("[HANDLER] ComfyUI process has exited.")
            return False

        try:
            response = requests.get(url, timeout=5)
            if response.status_code == 200:
                print("[HANDLER] ComfyUI API is reachable.")
                return True
        except requests.RequestException:
            pass

        attempt += 1
        fallback = retries if retries > 0 else COMFY_API_FALLBACK_MAX_RETRIES
        if process_status is None and attempt >= fallback:
            print(f"[HANDLER] Failed to connect after {fallback} attempts.")
            return False

        if attempt % log_every == 0:
            elapsed_s = (attempt * delay) / 1000
            print(f"[HANDLER] Still waiting for ComfyUI... ({elapsed_s:.0f}s elapsed)")

        time.sleep(delay / 1000)


def upload_images(images):
    if not images:
        return {"status": "success", "message": "No images to upload", "details": []}

    responses = []
    errors = []
    print(f"[HANDLER] Uploading {len(images)} image(s)...")

    for image in images:
        try:
            name = image["name"]
            image_data_uri = image["image"]
            base64_data = image_data_uri.split(",", 1)[1] if "," in image_data_uri else image_data_uri
            blob = base64.b64decode(base64_data)
            files = {"image": (name, BytesIO(blob), "image/png"), "overwrite": (None, "true")}
            response = requests.post(f"http://{COMFY_HOST}/upload/image", files=files, timeout=30)
            response.raise_for_status()
            responses.append(f"Uploaded {name}")
            print(f"[HANDLER] Uploaded {name}")
        except Exception as e:
            error_msg = f"Error uploading {image.get('name', 'unknown')}: {e}"
            print(f"[HANDLER] {error_msg}")
            errors.append(error_msg)

    if errors:
        return {"status": "error", "message": "Some images failed to upload", "details": errors}
    return {"status": "success", "message": "All images uploaded", "details": responses}


def queue_workflow(workflow, client_id):
    payload = {"prompt": workflow, "client_id": client_id}
    data = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    response = requests.post(f"http://{COMFY_HOST}/prompt", data=data, headers=headers, timeout=30)

    if response.status_code == 400:
        print(f"[HANDLER] ComfyUI returned 400: {response.text}")
        try:
            error_data = response.json()
            error_message = "Workflow validation failed"
            if "error" in error_data:
                error_info = error_data["error"]
                if isinstance(error_info, dict):
                    error_message = error_info.get("message", error_message)
                else:
                    error_message = str(error_info)
            error_details = []
            if "node_errors" in error_data:
                for node_id, node_error in error_data["node_errors"].items():
                    if isinstance(node_error, dict):
                        for error_type, error_msg in node_error.items():
                            error_details.append(f"Node {node_id} ({error_type}): {error_msg}")
                    else:
                        error_details.append(f"Node {node_id}: {node_error}")
            if error_details:
                raise ValueError(f"{error_message}:\n" + "\n".join(f"  - {d}" for d in error_details))
            raise ValueError(f"{error_message}. Response: {response.text}")
        except json.JSONDecodeError:
            raise ValueError(f"ComfyUI validation failed: {response.text}")

    response.raise_for_status()
    return response.json()


def get_history(prompt_id):
    response = requests.get(f"http://{COMFY_HOST}/history/{prompt_id}", timeout=30)
    response.raise_for_status()
    return response.json()


def get_image_data(filename, subfolder, image_type):
    data = {"filename": filename, "subfolder": subfolder, "type": image_type}
    url_values = urllib.parse.urlencode(data)
    try:
        response = requests.get(f"http://{COMFY_HOST}/view?{url_values}", timeout=60)
        response.raise_for_status()
        return response.content
    except Exception as e:
        print(f"[HANDLER] Error fetching {filename}: {e}")
        return None


def handler(job):
    if is_network_volume_debug_enabled():
        run_network_volume_diagnostics()

    job_input = job["input"]
    job_id = job["id"]

    validated_data, error_message = validate_input(job_input)
    if error_message:
        return {"error": error_message}

    workflow = validated_data["workflow"]
    input_images = validated_data.get("images")

    print("[DEBUG] input_images:", json.dumps(input_images, indent=2) if input_images else "None")

    for node_id, node in workflow.items():
        if node.get("class_type") == "LoadImage":
           print(f"[DEBUG] LoadImage node {node_id}: {json.dumps(node, indent=2)}")

    if not check_server(
        f"http://{COMFY_HOST}/",
        COMFY_API_AVAILABLE_MAX_RETRIES,
        COMFY_API_AVAILABLE_INTERVAL_MS,
    ):
        return {"error": f"ComfyUI server ({COMFY_HOST}) not reachable."}

    if input_images:
        upload_result = upload_images(input_images)
        if upload_result["status"] == "error":
            return {"error": "Failed to upload input images", "details": upload_result["details"]}

    ws = None
    client_id = str(uuid.uuid4())
    prompt_id = None
    output_data = []
    errors = []

    try:
        ws_url = f"ws://{COMFY_HOST}/ws?clientId={client_id}"
        print(f"[HANDLER] Connecting to websocket...")
        ws = websocket.WebSocket()
        ws.connect(ws_url, timeout=10)

        try:
            queued = queue_workflow(workflow, client_id)
            prompt_id = queued.get("prompt_id")
            if not prompt_id:
                raise ValueError(f"Missing 'prompt_id' in response: {queued}")
            print(f"[HANDLER] Queued workflow: {prompt_id}")
        except Exception as e:
            if isinstance(e, ValueError):
                raise
            raise ValueError(f"Error queuing workflow: {e}")

        print(f"[HANDLER] Waiting for execution...")
        execution_done = False
        while True:
            try:
                out = ws.recv()
                if isinstance(out, str):
                    message = json.loads(out)
                    msg_type = message.get("type")
                    if msg_type == "executing":
                        data = message.get("data", {})
                        if data.get("node") is None and data.get("prompt_id") == prompt_id:
                            print(f"[HANDLER] Execution finished.")
                            execution_done = True
                            break
                    elif msg_type == "execution_error":
                        data = message.get("data", {})
                        if data.get("prompt_id") == prompt_id:
                            error_details = f"Node: {data.get('node_type')} ({data.get('node_id')}), Error: {data.get('exception_message')}"
                            print(f"[HANDLER] Execution error: {error_details}")
                            errors.append(error_details)
                            break
            except websocket.WebSocketTimeoutException:
                continue
            except websocket.WebSocketConnectionClosedException as closed_err:
                ws = _attempt_websocket_reconnect(
                    ws_url, WEBSOCKET_RECONNECT_ATTEMPTS, WEBSOCKET_RECONNECT_DELAY_S, closed_err
                )
                continue

        if not execution_done and not errors:
            raise ValueError("Execution loop exited without completion or error.")

        print(f"[HANDLER] Fetching history...")
        history = get_history(prompt_id)
        if prompt_id not in history:
            if not errors:
                return {"error": f"Prompt {prompt_id} not found in history."}
            errors.append("Prompt not found in history.")
            return {"error": "Job failed", "details": errors}

        outputs = history.get(prompt_id, {}).get("outputs", {})
        print(f"[HANDLER] Processing {len(outputs)} output node(s)...")

        for node_id, node_output in outputs.items():
            print("DEBUG OUTPUTS:", outputs)

    # -------- IMAGES --------
        if "images" in node_output:
            for image_info in node_output["images"]:
                filename = image_info.get("filename")
                subfolder = image_info.get("subfolder", "")
                img_type = image_info.get("type")

                if img_type == "temp" or not filename:
                    continue

                image_bytes = get_image_data(filename, subfolder, img_type)
                if not image_bytes:
                    errors.append(f"Failed to fetch {filename}")
                    continue

                file_extension = os.path.splitext(filename)[1] or ".png"

                if os.environ.get("BUCKET_ENDPOINT_URL"):
                    try:
                        with tempfile.NamedTemporaryFile(suffix=file_extension, delete=False) as tmp:
                            tmp.write(image_bytes)
                          tmp_path = tmp.name
                        s3_url = rp_upload.upload_image(job_id, tmp_path)
                        os.remove(tmp_path)

                        output_data.append({
                            "filename": filename,
                            "type": "s3_url",
                            "data": s3_url
                        })

                    except Exception as e:
                        errors.append(f"S3 upload error for {filename}: {e}")

                else:
                    b64 = base64.b64encode(image_bytes).decode("utf-8")
                    output_data.append({
                        "filename": filename,
                        "type": "base64",
                        "data": b64
                    })

    # -------- VIDEOS (VHS_VideoCombine) --------
             if "videos" in node_output:
        for video_info in node_output["videos"]:
            filename = video_info.get("filename")
            subfolder = video_info.get("subfolder", "")
            vid_type = video_info.get("type")

            if not filename:
                continue

            video_bytes = get_image_data(filename, subfolder, vid_type)
            if not video_bytes:
                errors.append(f"Failed to fetch {filename}")
                continue

            file_extension = os.path.splitext(filename)[1] or ".mp4"

            if os.environ.get("BUCKET_ENDPOINT_URL"):
                try:
                    with tempfile.NamedTemporaryFile(suffix=file_extension, delete=False) as tmp:
                        tmp.write(video_bytes)
                        tmp_path = tmp.name
                    s3_url = rp_upload.upload_image(job_id, tmp_path)
                    os.remove(tmp_path)

                    output_data.append({
                        "filename": filename,
                        "type": "s3_url",
                        "media": "video",
                        "data": s3_url
                    })

                except Exception as e:
                    errors.append(f"S3 upload error for {filename}: {e}")

            else:
                b64 = base64.b64encode(video_bytes).decode("utf-8")
                output_data.append({
                    "filename": filename,
                    "type": "base64",
                    "media": "video",
                    "data": b64
                })

    except websocket.WebSocketException as e:
        print(f"[HANDLER] WebSocket error: {e}")
        print(traceback.format_exc())
        return {"error": f"WebSocket error: {e}"}
    except requests.RequestException as e:
        print(f"[HANDLER] HTTP error: {e}")
        return {"error": f"HTTP error: {e}"}
    except ValueError as e:
        print(f"[HANDLER] {e}")
        return {"error": str(e)}
    except Exception as e:
        print(f"[HANDLER] Unexpected error: {e}")
        print(traceback.format_exc())
        return {"error": f"Unexpected error: {e}"}
    finally:
        if ws and ws.connected:
            ws.close()

    result = {}
    if output_data:
        result["images"] = output_data
    if errors:
        result["errors"] = errors
    if not output_data and errors:
        return {"error": "Job failed with no output", "details": errors}
    if not output_data and not errors:
        result["status"] = "success_no_images"
        result["images"] = []

    print(f"[HANDLER] Done. Returning {len(output_data)} image(s).")
    return result


if __name__ == "__main__":
    print("[HANDLER] Starting FeddaComfyUI serverless handler...")
    runpod.serverless.start({"handler": handler})
