"""Crypto helpers for the WhatsApp Flow data-exchange endpoint.

Python port of Meta's reference Node.js implementation:
https://github.com/WhatsApp/WhatsApp-Flows-Tools/tree/main/examples/endpoint/nodejs/basic

Wire format (Meta → us):
    POST body JSON: { encrypted_aes_key, encrypted_flow_data, initial_vector }
    - encrypted_aes_key: base64, RSA-OAEP-SHA256 wrapped 16-byte AES key
    - encrypted_flow_data: base64, AES-128-GCM ciphertext || 16-byte tag
    - initial_vector: base64, 16-byte GCM IV (per Meta's spec, despite GCM
      typically using 12; do not truncate — match the reference exactly).

Response (us → Meta): base64 of AES-128-GCM(ciphertext || tag) using the
SAME AES key but with each byte of the IV bitwise-NOT-flipped.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any, cast

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class FlowEndpointException(Exception):  # noqa: N818 — matches Meta reference name
    """Raised when the endpoint must respond with a specific HTTP status.

    Per Meta's spec: 421 if decryption fails (signals Meta to refresh the
    public key on the client), 432 if signature is invalid, 427 if the
    flow token is no longer valid.
    """

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class DecryptedRequest:
    body: dict[str, Any]
    aes_key: bytes
    iv: bytes


def load_private_key(pem: str, passphrase: str) -> RSAPrivateKey:
    pwd = passphrase.encode("utf-8") if passphrase else None
    key = serialization.load_pem_private_key(pem.encode("utf-8"), password=pwd)
    if not isinstance(key, RSAPrivateKey):
        raise FlowEndpointException(500, "private_key_not_rsa")
    return key


def decrypt_request(
    body: dict[str, Any],
    private_key: RSAPrivateKey,
) -> DecryptedRequest:
    """Decrypt an incoming Flow data-exchange request.

    Raises FlowEndpointException(421) when RSA unwrap fails — Meta uses 421
    as the signal to refresh its cached public key.
    """
    try:
        encrypted_aes_key = base64.b64decode(body["encrypted_aes_key"])
        encrypted_flow_data = base64.b64decode(body["encrypted_flow_data"])
        iv = base64.b64decode(body["initial_vector"])
    except (KeyError, ValueError) as exc:
        raise FlowEndpointException(400, f"bad_request_envelope: {exc}") from exc

    try:
        aes_key = private_key.decrypt(
            encrypted_aes_key,
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None,
            ),
        )
    except Exception as exc:
        raise FlowEndpointException(421, "decrypt_failed_refresh_public_key") from exc

    # AES-128-GCM: ciphertext || 16-byte tag. cryptography.AESGCM expects
    # the same concatenated form, so we can pass the whole buffer.
    try:
        plaintext = AESGCM(aes_key).decrypt(iv, encrypted_flow_data, None)
    except Exception as exc:
        raise FlowEndpointException(421, "aes_gcm_decrypt_failed") from exc

    decoded = json.loads(plaintext.decode("utf-8"))
    if not isinstance(decoded, dict):
        raise FlowEndpointException(400, "decrypted_body_not_object")
    decoded_dict: dict[str, Any] = cast("dict[str, Any]", decoded)
    return DecryptedRequest(body=decoded_dict, aes_key=aes_key, iv=iv)


def encrypt_response(
    response: dict[str, Any],
    aes_key: bytes,
    iv: bytes,
) -> str:
    """Encrypt the response with the SAME AES key and a BITWISE-NOT-FLIPPED IV.

    Returns base64 of (ciphertext || 16-byte GCM tag).
    """
    flipped_iv = bytes(b ^ 0xFF for b in iv)
    payload = json.dumps(response, separators=(",", ":")).encode("utf-8")
    blob = AESGCM(aes_key).encrypt(flipped_iv, payload, None)
    return base64.b64encode(blob).decode("ascii")


__all__ = [
    "DecryptedRequest",
    "FlowEndpointException",
    "decrypt_request",
    "encrypt_response",
    "load_private_key",
]
