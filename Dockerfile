# ============================================================================
# FeddaComfyUI — Lean ComfyUI Docker Image for RunPod
# No frontend, no backend — just ComfyUI + essential custom nodes
# ============================================================================
FROM nvidia/cuda:12.4.1-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV PYTHONIOENCODING=utf-8

# --- System packages ---
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3.11 python3.11-venv python3.11-dev python3-pip \
    git curl wget supervisor ffmpeg \
    build-essential cmake ninja-build \
    libgl1-mesa-glx libglib2.0-0 libsm6 libxrender1 libxext6 \
    libffi-dev libssl-dev \
    && rm -rf /var/lib/apt/lists/* \
    && ln -sf /usr/bin/python3.11 /usr/bin/python3 \
    && ln -sf /usr/bin/python3 /usr/bin/python

# --- Upgrade pip ---
RUN python3 -m pip install --no-cache-dir --upgrade pip wheel setuptools

# --- PyTorch + CUDA 12.4 ---
RUN python3 -m pip install --no-cache-dir \
    torch torchvision torchaudio \
    --index-url https://download.pytorch.org/whl/cu124

# --- Xformers ---
RUN python3 -m pip install --no-cache-dir \
    xformers --index-url https://download.pytorch.org/whl/cu124

# --- Clone ComfyUI ---
RUN git clone https://github.com/comfyanonymous/ComfyUI.git /app/ComfyUI \
    && cd /app/ComfyUI \
    && pip install --no-cache-dir -r requirements.txt

# --- Clone custom nodes (27 total) ---
RUN mkdir -p /app/custom_nodes && cd /app/custom_nodes \
    && git clone --depth 1 https://github.com/ltdrdata/ComfyUI-Manager.git \
    && git clone --depth 1 https://github.com/Lightricks/ComfyUI-LTXVideo.git \
    && git clone --depth 1 https://github.com/kijai/ComfyUI-KJNodes.git \
    && git clone --depth 1 https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite.git \
    && git clone --depth 1 https://github.com/comfyuistudio/ComfyUI-Studio-nodes.git \
    && git clone --depth 1 https://github.com/pythongosssss/ComfyUI-Custom-Scripts.git \
    && git clone --depth 1 https://github.com/city96/ComfyUI-GGUF.git \
    && git clone --depth 1 https://github.com/Suzie1/ComfyUI_Comfyroll_CustomNodes.git \
    && git clone --depth 1 https://github.com/jamesWalker55/comfyui-various.git \
    && git clone --depth 1 https://github.com/yolain/ComfyUI-Easy-Use.git \
    && git clone --depth 1 https://github.com/SLAPaper/ComfyUI-Image-Selector.git \
    && git clone --depth 1 https://github.com/kijai/ComfyUI-MelBandRoFormer.git \
    && git clone --depth 1 https://github.com/rgthree/rgthree-comfy.git \
    && git clone --depth 1 https://github.com/evanspearman/ComfyMath.git \
    && git clone --depth 1 https://github.com/Fannovel16/comfyui_controlnet_aux.git \
    && git clone --depth 1 https://github.com/kijai/ComfyUI-Lotus.git \
    && git clone --depth 1 https://github.com/chflame163/ComfyUI_LayerStyle.git \
    && git clone --depth 1 https://github.com/theUpsider/ComfyUI-Styles_CSV_Loader.git \
    && git clone --depth 1 https://github.com/cubiq/ComfyUI_essentials.git \
    && git clone --depth 1 https://github.com/WASasquatch/was-node-suite-comfyui.git \
    && git clone --depth 1 https://github.com/kijai/ComfyUI-WanVideoWrapper.git \
    && git clone --depth 1 https://github.com/kijai/ComfyUI-Florence2.git \
    && git clone --depth 1 https://github.com/kijai/ComfyUI-segment-anything-2.git \
    && git clone --depth 1 https://github.com/Fannovel16/ComfyUI-Frame-Interpolation.git \
    && git clone --depth 1 https://github.com/SeargeDP/ComfyUI_Searge_LLM.git \
    && git clone --depth 1 https://github.com/bash-j/mikey_nodes.git \
    && git clone --depth 1 https://github.com/alexopus/ComfyUI-Image-Saver.git

# --- Install requirements for each custom node ---
RUN for dir in /app/custom_nodes/*/; do \
        if [ -f "$dir/requirements.txt" ]; then \
            echo "Installing requirements for $(basename $dir)..." \
            && pip install --no-cache-dir -r "$dir/requirements.txt" || true; \
        fi; \
    done

# --- Extra pip deps commonly needed ---
RUN python3 -m pip install --no-cache-dir \
    gguf \
    accelerate transformers safetensors \
    huggingface-hub \
    imageio imageio-ffmpeg av \
    librosa soundfile \
    scipy scikit-image \
    simpleeval \
    jupyterlab \
    runpod requests websocket-client

# --- Re-install CUDA PyTorch (node requirements may have overwritten with CPU version) ---
RUN python3 -m pip install --no-cache-dir --force-reinstall \
    torch torchvision torchaudio \
    --index-url https://download.pytorch.org/whl/cu124

# --- Copy app files ---
COPY scripts/start.sh /app/scripts/start.sh
COPY config/supervisord.conf /etc/supervisor/conf.d/supervisord.conf
COPY config/extra_model_paths.yaml /app/ComfyUI/extra_model_paths.yaml
COPY src/rp_handler.py /app/src/rp_handler.py
COPY src/network_volume.py /app/src/network_volume.py
COPY workflows/ /app/workflows/

RUN chmod +x /app/scripts/start.sh
RUN mkdir -p /var/log

EXPOSE 8188
EXPOSE 8888

ENTRYPOINT ["/app/scripts/start.sh"]
