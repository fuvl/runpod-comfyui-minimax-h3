FROM runpod/worker-comfyui:5.8.6-base-cuda12.8.1

ARG COMFYUI_COMMIT=2eb609766a749e3104485979615e062e401bab97
ARG KJNODES_COMMIT=35e5956193769d18a13136cdedb73a36a05c73e6

RUN cp /comfyui/extra_model_paths.yaml /tmp/extra_model_paths.yaml \
    && rm -rf /comfyui \
    && git clone https://github.com/Comfy-Org/ComfyUI.git /comfyui \
    && git -C /comfyui checkout "${COMFYUI_COMMIT}" \
    && mv /tmp/extra_model_paths.yaml /comfyui/extra_model_paths.yaml \
    && sed -i 's|clip: models/clip/|clip: models/text_encoders/|' /comfyui/extra_model_paths.yaml \
    && sed -i 's|unet: models/unet/|unet: models/diffusion_models/|' /comfyui/extra_model_paths.yaml \
    && uv pip install -r /comfyui/requirements.txt \
    && git clone https://github.com/kijai/ComfyUI-KJNodes.git /comfyui/custom_nodes/ComfyUI-KJNodes \
    && git -C /comfyui/custom_nodes/ComfyUI-KJNodes checkout "${KJNODES_COMMIT}" \
    && uv pip install -r /comfyui/custom_nodes/ComfyUI-KJNodes/requirements.txt \
    && torch_mm="$(python -c 'import torch; print(".".join(torch.__version__.split("+")[0].split(".")[:2]))')" \
    && uv pip install "https://github.com/Comfy-Org/wheels/releases/download/sageattention-latest/sageattention-2.2.0%2Bcu128torch${torch_mm}-cp312-cp312-manylinux_2_34_x86_64.manylinux_2_35_x86_64.whl" \
    && sed -i 's|python -u /comfyui/main.py |python -u /comfyui/main.py --highvram |g' /start.sh \
    && rm -rf /comfyui/.git /comfyui/custom_nodes/ComfyUI-KJNodes/.git /root/.cache

RUN mv /handler.py /handler_base.py
COPY privacy_handler.py /handler.py
