import base64
import binascii
import json
import os
import shutil
from pathlib import Path

import runpod
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

import handler_base


TRANSIENT_DIRECTORIES = (
    Path("/comfyui/input"),
    Path("/comfyui/output"),
    Path("/comfyui/temp"),
)
MAX_ENCRYPTED_PAYLOAD_BYTES = 220 * 1024 * 1024
KEY_DERIVATION_INFO = b"minimax-h3-runpod-v1"
PRIVATE_KEY_ENV = "MINIMAX_RUNPOD_PAYLOAD_PRIVATE_KEY"


def clear_transient_files():
    for directory in TRANSIENT_DIRECTORIES:
        directory.mkdir(parents=True, exist_ok=True)
        for item in directory.iterdir():
            if item.is_dir() and not item.is_symlink():
                shutil.rmtree(item)
            else:
                item.unlink(missing_ok=True)


def decode_base64_field(container, name, expected_size=None):
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


def decrypt_job_input(job):
    job_input = job.get("input")
    if isinstance(job_input, str):
        try:
            job_input = json.loads(job_input)
        except json.JSONDecodeError as error:
            raise ValueError("Encrypted RunPod input is invalid") from error

    envelope = job_input.get("encrypted") if isinstance(job_input, dict) else None
    if not isinstance(envelope, dict) or envelope.get("version") != 1:
        raise ValueError("An encrypted version 1 RunPod input is required")

    private_key_text = os.getenv(PRIVATE_KEY_ENV, "")
    try:
        private_key_bytes = base64.b64decode(private_key_text, validate=True)
    except (binascii.Error, ValueError) as error:
        raise RuntimeError("RunPod payload decryption key is invalid") from error
    if len(private_key_bytes) != 32:
        raise RuntimeError("RunPod payload decryption key is missing")

    ephemeral_public_key = decode_base64_field(envelope, "ephemeral_public_key", 32)
    nonce = decode_base64_field(envelope, "nonce", 12)
    ciphertext = decode_base64_field(envelope, "ciphertext")
    if not ciphertext or len(ciphertext) > MAX_ENCRYPTED_PAYLOAD_BYTES:
        raise ValueError("Encrypted RunPod input is empty or too large")

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
        raise ValueError("RunPod input could not be decrypted") from error
    if not isinstance(decoded, dict):
        raise ValueError("Decrypted RunPod input is invalid")

    job["input"] = decoded


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
        decrypt_job_input(job)
        normalize_workflow(job)
        return handler_base.handler(job)
    finally:
        clear_transient_files()


if __name__ == "__main__":
    runpod.serverless.start({"handler": handler})
