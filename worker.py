import base64
import binascii
import json
import logging
import os
import random
import shutil
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from aiohttp import web
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from vastai import BenchmarkConfig, HandlerConfig, LogActionConfig, Worker, WorkerConfig


COMFYUI_DIR = Path("/workspace/ComfyUI")
INPUT_DIR = COMFYUI_DIR / "input"
OUTPUT_DIR = COMFYUI_DIR / "output"
TEMP_DIR = COMFYUI_DIR / "temp"
TRANSIENT_DIRECTORIES = (INPUT_DIR, OUTPUT_DIR, TEMP_DIR)

MODEL_SERVER_URL = "http://127.0.0.1"
MODEL_SERVER_PORT = 18288
MODEL_LOG_FILE = "/var/log/portal/api-wrapper.log"
MODEL_HEALTHCHECK_ENDPOINT = "/health"
COMFY_BACKEND_URL = "http://127.0.0.1:18188/system_stats"
WRAPPER_HEALTH_URL = f"{MODEL_SERVER_URL}:{MODEL_SERVER_PORT}{MODEL_HEALTHCHECK_ENDPOINT}"

MAX_IMAGES = 9
MAX_IMAGE_BYTES = 30 * 1024 * 1024
MAX_TOTAL_IMAGE_BYTES = 150 * 1024 * 1024
MAX_ENCRYPTED_PAYLOAD_BYTES = 220 * 1024 * 1024
KEY_DERIVATION_INFO = b"minimax-h3-vast-v1"

ALLOWED_NODE_TYPES = {
    "BasicGuider",
    "BasicScheduler",
    "CLIPLoader",
    "CreateVideo",
    "KSamplerSelect",
    "LoadImage",
    "MiniMaxH3MemoryEfficientSageAttentionPatch",
    "MiniMaxH3ReferenceToVideo",
    "RandomNoise",
    "SamplerCustomAdvanced",
    "SaveVideo",
    "UNETLoader",
    "VAEDecode",
    "VAEDecodeAudio",
    "VAELoader",
}

log = logging.getLogger(__name__)


def clear_transient_files() -> None:
    for directory in TRANSIENT_DIRECTORIES:
        directory.mkdir(parents=True, exist_ok=True)
        for item in directory.iterdir():
            if item.is_dir() and not item.is_symlink():
                shutil.rmtree(item)
            else:
                item.unlink(missing_ok=True)


def normalize_save_video_codec(value) -> None:
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


def validate_workflow(workflow: dict, uploaded_names: set[str]) -> None:
    if not workflow or len(workflow) > 40:
        raise ValueError("Invalid workflow")

    for node in workflow.values():
        if not isinstance(node, dict) or node.get("class_type") not in ALLOWED_NODE_TYPES:
            raise ValueError(f"Unsupported workflow node: {node.get('class_type') if isinstance(node, dict) else 'invalid'}")
        if node.get("class_type") == "LoadImage":
            image_name = node.get("inputs", {}).get("image")
            if image_name not in uploaded_names:
                raise ValueError(f"Missing uploaded image: {image_name}")

    unet_nodes = [node for node in workflow.values() if node.get("class_type") == "UNETLoader"]
    if len(unet_nodes) != 1 or unet_nodes[0].get("inputs", {}).get("unet_name") != "minimax_h3_ref2va_pruned_fp8_scaled.safetensors":
        raise ValueError("Only the MiniMax H3 REF2VA model is available")


def decode_image(item: dict) -> tuple[str, bytes]:
    name = item.get("name")
    encoded = item.get("image")
    if not isinstance(name, str) or Path(name).name != name:
        raise ValueError("Invalid image filename")
    if Path(name).suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
        raise ValueError("Unsupported image format")
    if not isinstance(encoded, str):
        raise ValueError("Missing image data")
    if "," in encoded:
        encoded = encoded.split(",", 1)[1]
    try:
        data = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as error:
        raise ValueError("Invalid image data") from error
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise ValueError("Image is empty or too large")
    return name, data


def decode_base64_field(container: dict, name: str, expected_size: int | None = None) -> bytes:
    value = container.get(name)
    if not isinstance(value, str):
        raise ValueError(f"Missing encrypted field: {name}")
    try:
        decoded = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as error:
        raise ValueError(f"Invalid encrypted field: {name}") from error
    if expected_size is not None and len(decoded) != expected_size:
        raise ValueError(f"Invalid encrypted field size: {name}")
    return decoded


def decrypt_payload(payload: dict) -> dict:
    envelope = payload.get("encrypted")
    if not isinstance(envelope, dict) or envelope.get("version") != 1:
        raise ValueError("An encrypted version 1 payload is required")

    private_key_text = os.getenv("MINIMAX_PAYLOAD_PRIVATE_KEY", "")
    try:
        private_key_bytes = base64.b64decode(private_key_text, validate=True)
    except (binascii.Error, ValueError) as error:
        raise RuntimeError("Worker payload decryption key is invalid") from error
    if len(private_key_bytes) != 32:
        raise RuntimeError("Worker payload decryption key is missing")

    ephemeral_public_key = decode_base64_field(envelope, "ephemeral_public_key", 32)
    nonce = decode_base64_field(envelope, "nonce", 12)
    ciphertext = decode_base64_field(envelope, "ciphertext")
    if not ciphertext or len(ciphertext) > MAX_ENCRYPTED_PAYLOAD_BYTES:
        raise ValueError("Encrypted payload is empty or too large")

    private_key = X25519PrivateKey.from_private_bytes(private_key_bytes)
    shared_secret = private_key.exchange(X25519PublicKey.from_public_bytes(ephemeral_public_key))
    symmetric_key = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=KEY_DERIVATION_INFO,
    ).derive(shared_secret)
    try:
        plaintext = ChaCha20Poly1305(symmetric_key).decrypt(nonce, ciphertext, KEY_DERIVATION_INFO)
        decoded = json.loads(plaintext)
    except Exception as error:
        raise ValueError("Encrypted payload could not be decrypted") from error
    if not isinstance(decoded, dict):
        raise ValueError("Decrypted payload is invalid")
    return decoded


def request_parser(payload: dict) -> dict:
    clear_transient_files()
    try:
        source = decrypt_payload(payload)
        workflow = source.get("workflow")
        images = source.get("images", [])
        if not isinstance(workflow, dict) or not isinstance(images, list):
            raise ValueError("Expected workflow and images")
        if len(images) > MAX_IMAGES:
            raise ValueError(f"At most {MAX_IMAGES} images are allowed")

        decoded = [decode_image(item) for item in images]
        if sum(len(data) for _, data in decoded) > MAX_TOTAL_IMAGE_BYTES:
            raise ValueError("Combined image upload is too large")

        names = {name for name, _ in decoded}
        if len(names) != len(decoded):
            raise ValueError("Duplicate image filename")
        validate_workflow(workflow, names)
        normalize_save_video_codec(workflow)

        for name, data in decoded:
            (INPUT_DIR / name).write_bytes(data)

        return {
            "input": {
                "request_id": str(uuid.uuid4()),
                "workflow_json": workflow,
                "return_outputs_as_base64": True,
            }
        }
    except Exception:
        clear_transient_files()
        raise


async def response_generator(client_request, model_response):
    try:
        raw = await model_response.read()
        try:
            result = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return web.Response(body=raw, status=model_response.status, content_type=model_response.content_type)

        if model_response.status >= 400 or result.get("status") != "completed":
            return web.json_response(result, status=model_response.status)

        files = []
        for item in result.get("output", []):
            data = item.get("data")
            if data:
                files.append(
                    {
                        "filename": item.get("filename", "MiniMaxH3.mp4"),
                        "type": "base64",
                        "data": data,
                    }
                )

        if not files:
            result["status"] = "failed"
            result["message"] = result.get("message") or "Generation completed without a video output"
            return web.json_response(result, status=500)

        return web.json_response(
            {
                "images": files,
                "timings": result.get("timings", {}),
            },
            status=200,
        )
    finally:
        clear_transient_files()


def benchmark_payload() -> dict:
    # The Vast ComfyUI image includes this tiny checkpoint specifically for
    # worker qualification. H3 itself is loaded only for real requests.
    return {
        "input": {
            "request_id": f"benchmark-{random.randint(1000, 99999)}",
            "workflow_json": {
                "1": {
                    "class_type": "CheckpointLoaderSimple",
                    "inputs": {"ckpt_name": "v1-5-pruned-emaonly-fp16.safetensors"},
                },
                "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "test", "clip": ["1", 1]}},
                "3": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["1", 1]}},
                "4": {"class_type": "EmptyLatentImage", "inputs": {"width": 256, "height": 256, "batch_size": 1}},
                "5": {
                    "class_type": "KSampler",
                    "inputs": {
                        "seed": random.randint(0, 2**32 - 1),
                        "steps": 1,
                        "cfg": 1.0,
                        "sampler_name": "euler",
                        "scheduler": "normal",
                        "denoise": 1.0,
                        "model": ["1", 0],
                        "positive": ["2", 0],
                        "negative": ["3", 0],
                        "latent_image": ["4", 0],
                    },
                },
                "6": {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
                "7": {"class_type": "PreviewImage", "inputs": {"images": ["6", 0]}},
            },
        }
    }


def probe(url: str, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return 200 <= response.status < 300
    except (urllib.error.URLError, OSError):
        return False


def readiness_shim() -> None:
    time.sleep(2)
    deadline = time.monotonic() + 1_800
    while time.monotonic() < deadline:
        if probe(COMFY_BACKEND_URL) and probe(WRAPPER_HEALTH_URL):
            Path(MODEL_LOG_FILE).parent.mkdir(parents=True, exist_ok=True)
            with open(MODEL_LOG_FILE, "a") as log_file:
                log_file.write("BACKENDS_READY (MiniMax H3 readiness shim)\n")
            return
        time.sleep(5)


worker_config = WorkerConfig(
    model_server_url=MODEL_SERVER_URL,
    model_server_port=MODEL_SERVER_PORT,
    model_log_file=MODEL_LOG_FILE,
    model_healthcheck_url=MODEL_HEALTHCHECK_ENDPOINT,
    handlers=[
        HandlerConfig(
            route="/generate/sync",
            allow_parallel_requests=False,
            max_queue_time=1_800.0,
            request_parser=request_parser,
            response_generator=response_generator,
            benchmark_config=BenchmarkConfig(
                generator=benchmark_payload,
                runs=1,
                concurrency=1,
            ),
        )
    ],
    log_action_config=LogActionConfig(
        on_load=["BACKENDS_READY"],
        on_error=["BACKENDS_READY_TIMEOUT", "BACKEND_UNRECOVERABLE", "Application startup failed"],
    ),
)

threading.Thread(target=readiness_shim, name="readiness-shim", daemon=True).start()
Worker(worker_config).run()
