from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from .config import SECRET_KEY_PATH, ensure_directories


def _load_key() -> bytes:
    ensure_directories()
    if SECRET_KEY_PATH.exists():
        return SECRET_KEY_PATH.read_bytes()
    key = Fernet.generate_key()
    SECRET_KEY_PATH.write_bytes(key)
    return key


def _fernet() -> Fernet:
    return Fernet(_load_key())


def encrypt_value(value: str | None) -> str | None:
    if value is None or value == "":
        return None
    return _fernet().encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_value(value: str | None) -> str:
    if not value:
        return ""
    try:
        return _fernet().decrypt(value.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        return ""


def mask_secret(value: str | None, visible: int = 4) -> str:
    if not value:
        return ""
    if len(value) <= visible:
        return "*" * len(value)
    return "*" * 8 + value[-visible:]

