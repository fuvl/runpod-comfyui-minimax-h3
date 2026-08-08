import json
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


def normalize_save_video_codec(value):
    if isinstance(value, dict):
        if value.get("class_type") == "SaveVideo":
            inputs = value.setdefault("inputs", {})
            codec = inputs.get("codec")
            if codec is None:
                inputs["codec"] = {"codec": "h264"}
            elif isinstance(codec, str):
                inputs["codec"] = {"codec": codec}

        for child in value.values():
            normalize_save_video_codec(child)
    elif isinstance(value, list):
        for child in value:
            normalize_save_video_codec(child)


def normalize_workflow(job):
    job_input = job.get("input")
    if isinstance(job_input, str):
        try:
            job_input = json.loads(job_input)
        except json.JSONDecodeError:
            return
        job["input"] = job_input

    normalize_save_video_codec(job_input)


base_queue_workflow = handler_base.queue_workflow


def queue_normalized_workflow(workflow, *args, **kwargs):
    normalize_save_video_codec(workflow)
    return base_queue_workflow(workflow, *args, **kwargs)


handler_base.queue_workflow = queue_normalized_workflow


def handler(job):
    clear_transient_files()
    try:
        normalize_workflow(job)
        return handler_base.handler(job)
    finally:
        clear_transient_files()


if __name__ == "__main__":
    runpod.serverless.start({"handler": handler})
