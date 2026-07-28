"""Temporary-password encoder compatible with XHome's native IVIEWSPassword."""

from __future__ import annotations

import base64
import secrets
import string

from .exceptions import XHomeError

RAND_KEY_ALPHABET = string.digits + string.ascii_lowercase + string.ascii_uppercase


class XHomePasswordError(XHomeError):
    """Temporary-password encoding or decoding failed."""


def generate_rand_key(length: int = 16) -> str:
    """Return an app-compatible temporary-password random key."""

    return "".join(secrets.choice(RAND_KEY_ALPHABET) for _ in range(length))


def encode_temporary_password(password: str, uuid: str, rand_key: str | None = None) -> tuple[str, str]:
    """Return ``(data, rand_key)`` for ``v1/api/device/iviews/auth/add``.

    The Android native call is ``IVIEWSPassword.encodeTemporaryPassword(password,
    uuid, rand_key)``. It AES-CBC encrypts the password using ``uuid[4:20]`` as
    the 16-byte key and the 16-byte ``rand_key`` as IV, then base64-encodes the
    padded ciphertext.
    """

    rand_key = rand_key or generate_rand_key()
    key, iv = _aes_key_iv(uuid, rand_key)
    ciphertext = _aes_cbc_encrypt(_utf8(password), key, iv)
    return base64.b64encode(ciphertext).decode("ascii"), rand_key


def decode_temporary_password(data: str, uuid: str, rand_key: str) -> str:
    """Decode a temporary-password ``data`` blob."""

    key, iv = _aes_key_iv(uuid, rand_key)
    plaintext = _aes_cbc_decrypt(base64.b64decode(data), key, iv)
    return plaintext.decode("utf-8")


def _aes_key_iv(uuid: str, rand_key: str) -> tuple[bytes, bytes]:
    key = _utf8(uuid[4:20])
    iv = _utf8(rand_key)
    if len(key) != 16:
        raise XHomePasswordError("XHome temporary-password uuid must provide at least 20 ASCII bytes")
    if len(iv) != 16:
        raise XHomePasswordError("XHome temporary-password rand_key must be exactly 16 ASCII bytes")
    return key, iv


def _aes_cbc_encrypt(plaintext: bytes, key: bytes, iv: bytes) -> bytes:
    padding, Cipher, algorithms, modes = _cryptography()
    padder = padding.PKCS7(128).padder()
    padded = padder.update(plaintext) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    return encryptor.update(padded) + encryptor.finalize()


def _aes_cbc_decrypt(ciphertext: bytes, key: bytes, iv: bytes) -> bytes:
    padding, Cipher, algorithms, modes = _cryptography()
    decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    padded = decryptor.update(ciphertext) + decryptor.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    return unpadder.update(padded) + unpadder.finalize()


def _cryptography():
    try:
        from cryptography.hazmat.primitives import padding
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    except ImportError as err:
        raise XHomePasswordError("Install cryptography to encode XHome temporary passwords") from err
    return padding, Cipher, algorithms, modes


def _utf8(value: str) -> bytes:
    try:
        return value.encode("ascii")
    except UnicodeEncodeError as err:
        raise XHomePasswordError("XHome temporary-password encoder expects ASCII input") from err
