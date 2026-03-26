"""
Network Volume diagnostics for FeddaComfyUI serverless.
Enable via NETWORK_VOLUME_DEBUG=true environment variable.
"""

import os

MODEL_TYPES = {
    "checkpoints": [".safetensors", ".ckpt", ".pt", ".pth", ".bin"],
    "clip": [".safetensors", ".pt", ".bin"],
    "diffusion_models": [".safetensors", ".pt", ".bin"],
    "text_encoders": [".safetensors", ".pt", ".bin"],
    "vae": [".safetensors", ".pt", ".bin"],
    "loras": [".safetensors", ".pt"],
    "model_patches": [".safetensors", ".pt", ".bin"],
    "controlnet": [".safetensors", ".pt", ".pth", ".bin"],
    "embeddings": [".safetensors", ".pt", ".bin"],
    "upscale_models": [".safetensors", ".pt", ".pth"],
}


def is_network_volume_debug_enabled():
    return os.environ.get("NETWORK_VOLUME_DEBUG", "false").lower() == "true"


def _format_size(size_bytes):
    for unit in ["B", "KB", "MB", "GB"]:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"


def run_network_volume_diagnostics():
    print("=" * 60)
    print("NETWORK VOLUME DIAGNOSTICS")
    print("=" * 60)

    # Check both possible volume mount points
    for volume_path in ["/runpod-volume", "/workspace"]:
        print(f"\n[CHECK] {volume_path}")
        if not os.path.isdir(volume_path):
            print(f"  NOT MOUNTED")
            continue

        print(f"  MOUNTED")
        models_dir = os.path.join(volume_path, "models")
        if not os.path.isdir(models_dir):
            print(f"  No models/ directory found")
            continue

        found_any = False
        for model_type, extensions in MODEL_TYPES.items():
            model_path = os.path.join(models_dir, model_type)
            if not os.path.isdir(model_path):
                continue
            try:
                files = []
                for f in os.listdir(model_path):
                    fp = os.path.join(model_path, f)
                    if os.path.isfile(fp) and os.path.splitext(f)[1].lower() in extensions:
                        files.append(f"{f} ({_format_size(os.path.getsize(fp))})")
                        found_any = True
                if files:
                    print(f"\n  {model_type}/:")
                    for f in files:
                        print(f"    - {f}")
            except Exception as e:
                print(f"  {model_type}/: Error - {e}")

        if not found_any:
            print(f"  No model files found in {models_dir}")

    print("=" * 60)
