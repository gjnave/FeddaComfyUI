#!/bin/bash
set -e

echo "========================================="
echo "  FeddaComfyUI - Docker Startup"
echo "========================================="
echo "GPU: $(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null || echo 'none detected')"
echo ""

# --- 1. Model directories on network volume ---
MODELS_DIR="/workspace/models"
mkdir -p "$MODELS_DIR"/{checkpoints,diffusion_models,clip,text_encoders,vae,loras,model_patches}
mkdir -p /workspace/output
mkdir -p /workspace/input

# --- 2. Symlink ComfyUI model directories to network volume ---
echo "[SETUP] Symlinking model directories to /workspace..."
for dir in checkpoints diffusion_models clip text_encoders vae loras model_patches; do
    rm -rf "/app/ComfyUI/models/$dir"
    ln -sf "$MODELS_DIR/$dir" "/app/ComfyUI/models/$dir"
done

# Symlink output and input
rm -rf /app/ComfyUI/output
ln -sf /workspace/output /app/ComfyUI/output
rm -rf /app/ComfyUI/input
ln -sf /workspace/input /app/ComfyUI/input

# --- 3. Custom nodes: copy from image to workspace on first boot ---
mkdir -p /workspace/custom_nodes

for node_dir in /app/custom_nodes/*/; do
    node_name=$(basename "$node_dir")
    if [ ! -d "/workspace/custom_nodes/$node_name" ]; then
        echo "[NODES] Copying $node_name to workspace..."
        cp -r "$node_dir" "/workspace/custom_nodes/"
    fi
done

# Symlink custom_nodes into ComfyUI
rm -rf /app/ComfyUI/custom_nodes
ln -sf /workspace/custom_nodes /app/ComfyUI/custom_nodes

echo "[NODES] $(ls /workspace/custom_nodes | wc -l) custom nodes ready."

# --- 4. Copy bundled workflows ---
WORKFLOW_DEST="/app/ComfyUI/user/default/workflows"
mkdir -p "$WORKFLOW_DEST"
if [ -d "/app/workflows" ]; then
    cp -n /app/workflows/*.json "$WORKFLOW_DEST/" 2>/dev/null || true
    cp -n /app/workflows/*.JSON "$WORKFLOW_DEST/" 2>/dev/null || true
    echo "[WORKFLOWS] Bundled workflows copied to ComfyUI."
fi

# --- 5. Configure ComfyUI-Manager ---
MANAGER_DIR="/workspace/custom_nodes/ComfyUI-Manager"
if [ -d "$MANAGER_DIR" ]; then
    mkdir -p "$MANAGER_DIR/user"
    cat > "$MANAGER_DIR/user/config.ini" << 'EOF'
[default]
security_level = weak
network_mode = public
EOF
fi

# --- 6. Launch services ---
echo ""
echo "========================================="
echo "  Starting services..."
echo "  ComfyUI: port 8188"
echo "  Jupyter:  port 8888"
echo "========================================="
exec supervisord -c /etc/supervisor/conf.d/supervisord.conf
