FROM runpod/worker-comfyui:5.8.6-base

ARG COMFYUI_COMMIT=2eb609766a749e3104485979615e062e401bab97

RUN cp /comfyui/extra_model_paths.yaml /tmp/extra_model_paths.yaml \
    && rm -rf /comfyui \
    && git clone https://github.com/Comfy-Org/ComfyUI.git /comfyui \
    && git -C /comfyui checkout "${COMFYUI_COMMIT}" \
    && mv /tmp/extra_model_paths.yaml /comfyui/extra_model_paths.yaml \
    && uv pip install -r /comfyui/requirements.txt \
    && rm -rf /comfyui/.git /root/.cache

RUN mv /handler.py /handler_base.py
COPY privacy_handler.py /handler.py

