"""
Cross-platform encrypted secrets store for bambu-mcp.

Secrets are stored in ~/.bambu-mcp/secrets.enc as a Fernet-encrypted JSON file.
Key derivation: PBKDF2HMAC(SHA-256, salt=b"bambu-mcp", 100_000 iterations).
Default password: read from config/settings.toml (default: "changeit").
"""

import json
import os
import sys
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
import base64

_STORE_PATH = Path.home() / ".bambu-mcp" / "secrets.enc"
_KDF_SALT = b"bambu-mcp"
_KDF_ITERATIONS = 100_000
_DEFAULT_PASSWORD = "changeit"


def _get_password() -> str:
    """Return the secrets store password from config/settings.toml, env var, or default."""
    # 1. Environment variable override
    env = os.environ.get("BAMBU_MCP_SECRETS_PASSWORD")
    if env:
        return env
    # 2. config/settings.toml next to this file's project root
    settings_path = Path(__file__).parent / "config" / "settings.toml"
    if settings_path.exists():
        for line in settings_path.read_text().splitlines():
            line = line.strip()
            if line.startswith("password") and "=" in line:
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return _DEFAULT_PASSWORD


def _make_fernet(password: str | None = None) -> Fernet:
    pwd = (password or _get_password()).encode()
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=_KDF_SALT,
        iterations=_KDF_ITERATIONS,
    )
    key = base64.urlsafe_b64encode(kdf.derive(pwd))
    return Fernet(key)


def _load(fernet: Fernet) -> dict:
    if not _STORE_PATH.exists():
        return {}
    try:
        return json.loads(fernet.decrypt(_STORE_PATH.read_bytes()))
    except InvalidToken:
        print(
            f"ERROR: Failed to decrypt {_STORE_PATH} — wrong password?",
            file=sys.stderr,
        )
        return {}


def _save(data: dict, fernet: Fernet) -> None:
    _STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _STORE_PATH.write_bytes(fernet.encrypt(json.dumps(data).encode()))


def get(key: str, default=None, password: str | None = None):
    """Return the value for *key*, or *default* if not found."""
    f = _make_fernet(password)
    return _load(f).get(key, default)


def set(key: str, value, password: str | None = None) -> None:  # noqa: A001
    """Store *value* under *key*."""
    f = _make_fernet(password)
    data = _load(f)
    data[key] = value
    _save(data, f)


def delete(key: str, password: str | None = None) -> bool:
    """Delete *key*. Returns True if the key existed."""
    f = _make_fernet(password)
    data = _load(f)
    if key not in data:
        return False
    del data[key]
    _save(data, f)
    return True


def list_keys(password: str | None = None) -> list[str]:
    """Return all stored key names."""
    return list(_load(_make_fernet(password)).keys())


def get_all(password: str | None = None) -> dict:
    """Return a copy of the full secrets dict."""
    return dict(_load(_make_fernet(password)))
