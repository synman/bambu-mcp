"""
Cross-platform encrypted secrets store for bambu-mcp.

Secrets are stored in ~/.bambu-mcp/secrets.enc as a v2 JSON vault.
Each entry is encrypted individually with AES-256-GCM using the entry name
as additional authenticated data (AAD), preventing ciphertext swapping.

Master key resolution order:
  1. BAMBU_MCP_SECRETS_KEY env var — 32-byte key as hex (64 chars) or base64
     (44 chars). For container / CI injection.
  2. OS keychain via the `keyring` library — macOS Keychain, libsecret (Linux),
     Windows Credential Manager. 256-bit CSPRNG key generated on first use,
     stored as service="bambu-mcp", username="master_key".
  3. File fallback — ~/.bambu-mcp/master.key (mode 0o400, raw 32 bytes).

v1 migration: a vault encrypted with the previous Fernet scheme (including the
legacy PBKDF2/"changeit" key) is detected automatically and migrated to v2
on first access. The old BAMBU_MCP_FERNET_KEY env var is also tried during
migration for any vault that predates keychain storage.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

log = logging.getLogger(__name__)

_STORE_PATH = Path.home() / ".bambu-mcp" / "secrets.enc"
_KEY_PATH = Path.home() / ".bambu-mcp" / "master.key"

_KEYRING_SERVICE = "bambu-mcp"
_KEYRING_USERNAME = "master_key"
_KEY_LEN = 32  # 256-bit


# ── Key management ────────────────────────────────────────────────────────────

def _decode_env_key(raw: str) -> bytes:
    raw = raw.strip()
    try:
        decoded = bytes.fromhex(raw)
        if len(decoded) == _KEY_LEN:
            return decoded
    except ValueError:
        pass
    decoded = base64.b64decode(raw + "==")
    if len(decoded) != _KEY_LEN:
        raise ValueError(f"BAMBU_MCP_SECRETS_KEY must be {_KEY_LEN} bytes; got {len(decoded)}")
    return decoded


def _store_key(key: bytes) -> None:
    try:
        import keyring  # type: ignore[import]
        keyring.set_password(_KEYRING_SERVICE, _KEYRING_USERNAME, base64.b64encode(key).decode())
        log.info("Master key stored in OS keychain.")
        return
    except Exception as exc:  # noqa: BLE001
        log.debug("Keychain write failed (%s); falling back to file.", exc)
    _STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _KEY_PATH.write_bytes(key)
    _KEY_PATH.chmod(0o400)
    log.info("Master key written to %s (mode 0400).", _KEY_PATH)


def _get_key() -> bytes:
    """Resolve the 256-bit master key using the priority chain."""
    env = os.environ.get("BAMBU_MCP_SECRETS_KEY")
    if env:
        return _decode_env_key(env)

    try:
        import keyring  # type: ignore[import]
        stored = keyring.get_password(_KEYRING_SERVICE, _KEYRING_USERNAME)
        if stored:
            raw = base64.b64decode(stored + "==")
            if len(raw) == _KEY_LEN:
                return raw
            # Value present but wrong length — likely an old Fernet key; fall through
            log.debug("Keychain value is not a 32-byte AES key; will trigger migration.")
    except Exception as exc:  # noqa: BLE001
        log.debug("Keychain read failed: %s", exc)

    if _KEY_PATH.exists():
        raw = _KEY_PATH.read_bytes()
        if len(raw) == _KEY_LEN:
            return raw

    key = os.urandom(_KEY_LEN)
    _store_key(key)
    return key


# ── v2 AES-256-GCM per-entry encryption ──────────────────────────────────────

def _encrypt_entry(key: bytes, service: str, plaintext: str) -> dict:
    nonce = os.urandom(12)
    ct = AESGCM(key).encrypt(nonce, plaintext.encode(), service.encode())
    return {"nonce": base64.b64encode(nonce).decode(), "ct": base64.b64encode(ct).decode()}


def _decrypt_entry(key: bytes, service: str, entry: dict) -> str:
    nonce = base64.b64decode(entry["nonce"])
    ct = base64.b64decode(entry["ct"])
    return AESGCM(key).decrypt(nonce, ct, service.encode()).decode()


def _save(data: dict, key: bytes) -> None:
    _STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    entries = {}
    for service, value in data.items():
        plaintext = value if isinstance(value, str) else json.dumps(value)
        entries[service] = _encrypt_entry(key, service, plaintext)
    vault = {"meta": {"version": "2", "algo": "AES-256-GCM"}, "entries": entries}
    _STORE_PATH.write_text(json.dumps(vault))


def _load_v2(vault: dict, key: bytes) -> dict:
    result = {}
    for service, entry in vault.get("entries", {}).items():
        try:
            plaintext = _decrypt_entry(key, service, entry)
            try:
                result[service] = json.loads(plaintext)
            except json.JSONDecodeError:
                result[service] = plaintext
        except Exception as exc:  # noqa: BLE001
            log.error("Failed to decrypt entry '%s': %s", service, exc)
    return result


# ── v1 Fernet migration ───────────────────────────────────────────────────────

def _migrate_fernet(fernet_token: bytes) -> dict:
    """Try all known Fernet keys against a v1 vault. Returns decrypted dict or {}."""
    from cryptography.fernet import Fernet, InvalidToken  # noqa: PLC0415
    from cryptography.hazmat.primitives import hashes as _hashes  # noqa: PLC0415
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC  # noqa: PLC0415

    candidates: list[bytes] = []

    old_env = os.environ.get("BAMBU_MCP_FERNET_KEY")
    if old_env:
        candidates.append(old_env.encode() if isinstance(old_env, str) else old_env)

    try:
        import keyring  # type: ignore[import]
        stored = keyring.get_password(_KEYRING_SERVICE, _KEYRING_USERNAME)
        if stored:
            candidates.append(stored.encode())
    except Exception:  # noqa: BLE001
        pass

    if _KEY_PATH.exists():
        candidates.append(_KEY_PATH.read_bytes().strip())

    # Legacy PBKDF2 "changeit"
    kdf = PBKDF2HMAC(algorithm=_hashes.SHA256(), length=32, salt=b"bambu-mcp", iterations=100_000)
    candidates.append(base64.urlsafe_b64encode(kdf.derive(b"changeit")))

    for candidate in candidates:
        try:
            plaintext = Fernet(candidate).decrypt(fernet_token)
            log.info("Migrated v1 Fernet vault → v2 AES-256-GCM.")
            return json.loads(plaintext)
        except (InvalidToken, Exception):  # noqa: BLE001
            continue

    print(
        f"ERROR: Failed to decrypt {_STORE_PATH} with any known key. "
        "Set BAMBU_MCP_SECRETS_KEY or delete the store to start fresh.",
        file=sys.stderr,
    )
    return {}


# ── Load ──────────────────────────────────────────────────────────────────────

def _load() -> tuple[dict, bytes]:
    """Return (data dict, active key). Handles v1→v2 migration transparently."""
    if not _STORE_PATH.exists():
        return {}, _get_key()

    raw = _STORE_PATH.read_bytes()

    if raw[:1] == b"{":
        try:
            vault = json.loads(raw)
            if vault.get("meta", {}).get("version") == "2":
                key = _get_key()
                return _load_v2(vault, key), key
        except Exception as exc:  # noqa: BLE001
            log.warning("Failed to parse vault JSON: %s", exc)

    # v1 Fernet vault — migrate to v2 with a fresh 256-bit key
    old_data = _migrate_fernet(raw)
    new_key = os.urandom(_KEY_LEN)
    _store_key(new_key)
    _save(old_data, new_key)
    return old_data, new_key


# ── Public API (unchanged) ────────────────────────────────────────────────────

def get(key: str, default: Any = None) -> Any:
    """Return the value for *key*, or *default* if not found."""
    data, _ = _load()
    return data.get(key, default)


def set(key: str, value: Any) -> None:  # noqa: A001
    """Store *value* under *key*."""
    data, master_key = _load()
    data[key] = value
    _save(data, master_key)


def delete(key: str) -> bool:
    """Delete *key*. Returns True if the key existed."""
    data, master_key = _load()
    if key not in data:
        return False
    del data[key]
    _save(data, master_key)
    return True


def list_keys() -> list[str]:
    """Return all stored key names."""
    data, _ = _load()
    return list(data.keys())


def get_all() -> dict:
    """Return a copy of the full secrets dict."""
    data, _ = _load()
    return dict(data)
