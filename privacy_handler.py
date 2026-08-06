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


def handler(job):
    clear_transient_files()
    try:
        return handler_base.handler(job)
    finally:
        clear_transient_files()


if __name__ == "__main__":
    runpod.serverless.start({"handler": handler})

