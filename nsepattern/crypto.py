"""AES-256-GCM encryption with a key derived from the app password (PBKDF2-SHA256).

The browser decrypts the same format with WebCrypto (see dashboard template), so:
  key  = PBKDF2-HMAC-SHA256(password, salt 16 bytes, ITERATIONS) -> 32 bytes
  data = AES-GCM(key, iv 12 bytes) -> ciphertext || 16-byte tag   (WebCrypto uses the same layout)
Everything is base64 in a small JSON envelope.
"""
from __future__ import annotations

import base64
import gzip
import json
import os

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

ITERATIONS = 250_000


def _key(password: str, salt: bytes, iterations: int) -> bytes:
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=iterations)
    return kdf.derive(password.encode("utf-8"))


def encrypt_bytes(data: bytes, password: str, iterations: int = ITERATIONS) -> dict:
    salt, iv = os.urandom(16), os.urandom(12)
    ct = AESGCM(_key(password, salt, iterations)).encrypt(iv, data, None)
    b64 = lambda b: base64.b64encode(b).decode("ascii")  # noqa: E731
    return {"v": 1, "kdf": "PBKDF2-SHA256", "iter": iterations, "salt": b64(salt), "iv": b64(iv), "ct": b64(ct)}


def decrypt_bytes(env: dict, password: str) -> bytes:
    d = base64.b64decode
    key = _key(password, d(env["salt"]), int(env["iter"]))
    return AESGCM(key).decrypt(d(env["iv"]), d(env["ct"]), None)


def encrypt_json(obj, password: str, compress: bool = False) -> dict:
    """compress=True gzips before encrypting (encrypted data can't be compressed later, so this is
    what keeps the dashboard light on a phone). The browser un-gzips after decrypting."""
    raw = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if compress:
        env = encrypt_bytes(gzip.compress(raw, 6), password)
        env["z"] = "gzip"
        return env
    return encrypt_bytes(raw, password)


def decrypt_json(env: dict, password: str):
    raw = decrypt_bytes(env, password)
    if env.get("z") == "gzip":
        raw = gzip.decompress(raw)
    return json.loads(raw.decode("utf-8"))
