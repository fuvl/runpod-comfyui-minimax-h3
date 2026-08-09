#!/bin/bash
set -euo pipefail

COMFYUI_DIR="${WORKSPACE:-/workspace}/ComfyUI"
MODEL_DIR="$COMFYUI_DIR/models"
LOG_FILE="${MODEL_LOG:-/var/log/portal/comfyui.log}"
COMFYUI_COMMIT="2eb609766a749e3104485979615e062e401bab97"
KJNODES_COMMIT="35e5956193769d18a13136cdedb73a36a05c73e6"

mkdir -p "$(dirname "$LOG_FILE")"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"
}

verify_model() {
    local relative_path="$1"
    local expected_size="$2"
    local output="$MODEL_DIR/$relative_path"

    local actual_size
    actual_size="$(stat -c %s "$output")"
    if [[ "$actual_size" != "$expected_size" ]]; then
        log "Wrong size for $relative_path: expected $expected_size, got $actual_size"
        return 1
    fi
    log "Verified $relative_path"
}

log "Pinning ComfyUI and MiniMax dependencies"
source /venv/main/bin/activate

git -C "$COMFYUI_DIR" fetch --depth 1 origin "$COMFYUI_COMMIT"
git -C "$COMFYUI_DIR" checkout --detach "$COMFYUI_COMMIT"

if [[ ! -d "$COMFYUI_DIR/custom_nodes/ComfyUI-KJNodes/.git" ]]; then
    rm -rf "$COMFYUI_DIR/custom_nodes/ComfyUI-KJNodes"
    git clone https://github.com/kijai/ComfyUI-KJNodes.git "$COMFYUI_DIR/custom_nodes/ComfyUI-KJNodes"
fi
git -C "$COMFYUI_DIR/custom_nodes/ComfyUI-KJNodes" fetch --depth 1 origin "$KJNODES_COMMIT"
git -C "$COMFYUI_DIR/custom_nodes/ComfyUI-KJNodes" checkout --detach "$KJNODES_COMMIT"

sed -i -E '/^(torch|torchvision|torchaudio|torchcodec)([[:space:]]|[<>=!])/d' "$COMFYUI_DIR/requirements.txt"
sed -i -E '/^(torch|torchvision|torchaudio|torchcodec)([[:space:]]|[<>=!])/d' "$COMFYUI_DIR/custom_nodes/ComfyUI-KJNodes/requirements.txt"
uv pip install --no-cache-dir -r "$COMFYUI_DIR/requirements.txt"
uv pip install --no-cache-dir -r "$COMFYUI_DIR/custom_nodes/ComfyUI-KJNodes/requirements.txt"
uv pip install --no-cache-dir --reinstall \
    torch==2.11.0 torchvision==0.26.0 torchaudio==2.11.0 \
    --index-url https://download.pytorch.org/whl/cu130
uv pip install --no-cache-dir \
    "https://github.com/Comfy-Org/wheels/releases/download/sageattention-latest/sageattention-2.2.0%2Bcu130torch2.11-cp312-cp312-manylinux_2_34_x86_64.manylinux_2_35_x86_64.whl"

sed -i 's|codec: io.DynamicCombo.Type) -> io.NodeOutput:|codec: io.DynamicCombo.Type = {"codec": "h264"}) -> io.NodeOutput:|' "$COMFYUI_DIR/comfy_extras/nodes_video.py"

log "Downloading REF2VA models with the Hugging Face HTTPS downloader"
find "$MODEL_DIR" -type f -name '*.part' -delete
export HF_HUB_DISABLE_XET=1
export HF_HUB_DOWNLOAD_TIMEOUT=1800
hf download Comfy-Org/MiniMax-H3 \
    diffusion_models/minimax_h3_ref2va_pruned_fp8_scaled.safetensors \
    text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors \
    vae/minimax_h3_video_vae_fp16.safetensors \
    vae/minimax_h3_audio_vae_fp32.safetensors \
    --local-dir "$MODEL_DIR" \
    --max-workers 4

verify_model "diffusion_models/minimax_h3_ref2va_pruned_fp8_scaled.safetensors" 20958205608
verify_model "text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors" 15687142551
verify_model "vae/minimax_h3_video_vae_fp16.safetensors" 5207808496
verify_model "vae/minimax_h3_audio_vae_fp32.safetensors" 605254808

rm -rf /root/.cache/huggingface /root/.cache/uv
log "MiniMax H3 provisioning complete"
