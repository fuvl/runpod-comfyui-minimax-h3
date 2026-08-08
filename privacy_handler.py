import shutil
from pathlib import Path

import runpod

import handler_base


TRANSIENT_DIRECTORIES = (
    Path("/comfyui/input"),
    Path("/comfyui/output"),
    Path("/comfyui/temp"),
)


def clear_transient_files():
    for directory in TRANSIENT_DIRECTORIES:
        directory.mkdir(parents=True, exist_ok=True)
        for item in directory.iterdir():
            if item.is_dir() and not item.is_symlink():
                shutil.rmtree(item)
            else:
                item.unlink(missing_ok=True)


def normalize_workflow(job):
    workflow = job.get("input", {}).get("workflow", {})
    if not isinstance(workflow, dict):
        return

    for node in workflow.values():
        if not isinstance(node, dict) or node.get("class_type") != "SaveVideo":
            continue

        inputs = node.setdefault("inputs", {})
        codec = inputs.get("codec")
        if codec is None:
            inputs["codec"] = {"codec": "h264"}
        elif isinstance(codec, str):
            inputs["codec"] = {"codec": codec}


def handler(job):
    clear_transient_files()
    try:
        normalize_workflow(job)
        return handler_base.handler(job)
    finally:
        clear_transient_files()


if __name__ == "__main__":
    runpod.serverless.start({"handler": handler})
