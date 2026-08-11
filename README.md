# RunPod ComfyUI worker for MiniMax H3

RunPod's official ComfyUI worker with a pinned ComfyUI revision containing the
MiniMax H3 nodes. Input, output, and temporary ComfyUI files are cleared before
and after every serverless job. Network-volume model weights are not touched.
ComfyUI runs in high-VRAM mode with its classic execution cache so the FL2VA
and REF2VA loaders can remain resident after each model has been used once.
The image also includes a pinned MiniMax H3 hybrid loader for the experimental
FL2VA-base/REF2VA-blocks-30-through-49 workflow.
