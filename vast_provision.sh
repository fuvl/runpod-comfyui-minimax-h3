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

download_model() {
    local relative_path="$1"
    local expected_size="$2"
    local output="$MODEL_DIR/$relative_path"
    local partial="$output.part"
    local url="https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/$relative_path"

    mkdir -p "$(dirname "$output")"
    if [[ -f "$output" ]] && [[ "$(stat -c %s "$output")" == "$expected_size" ]]; then
        log "Already present: $relative_path"
        return 0
    fi

    rm -f "$output"
    log "Downloading $relative_path"
    curl --fail --location --retry 8 --retry-all-errors --continue-at - \
        --output "$partial" "$url"

    local actual_size
    actual_size="$(stat -c %s "$partial")"
    if [[ "$actual_size" != "$expected_size" ]]; then
        log "Wrong size for $relative_path: expected $expected_size, got $actual_size"
        return 1
    fi
    mv "$partial" "$output"
    log "Downloaded $relative_path"
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

download_model "diffusion_models/minimax_h3_ref2va_pruned_fp8_scaled.safetensors" 20958205608 &
pid_ref2va=$!
download_model "text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors" 15687142551 &
pid_text=$!
download_model "vae/minimax_h3_video_vae_fp16.safetensors" 5207808496 &
pid_video_vae=$!
download_model "vae/minimax_h3_audio_vae_fp32.safetensors" 605254808 &
pid_audio_vae=$!

failed=0
for pid in "$pid_ref2va" "$pid_text" "$pid_video_vae" "$pid_audio_vae"; do
    if ! wait "$pid"; then
        failed=1
    fi
done
if [[ "$failed" != 0 ]]; then
    log "One or more model downloads failed"
    exit 1
fi

rm -rf /root/.cache/huggingface /root/.cache/uv
log "MiniMax H3 provisioning complete"
