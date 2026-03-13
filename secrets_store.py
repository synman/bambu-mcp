"""
Cross-platform encrypted secrets store for bambu-mcp.

Secrets are stored in ~/.bambu-mcp/secrets.enc as a Fernet-encrypted JSON file.

Master key resolution order:
  1. BAMBU_MCP_FERNET_KEY env var — raw URL-safe base64 Fernet key (container/CI injection).
  2. OS keychain via the `keyring` library — macOS Keychain, libsecret (Linux),
     Windows Credential Manager. Key is generated on first use and stored as
     service="bambu-mcp", username="master_key". Session-sealed by the OS.
  3. File fallback — ~/.bambu-mcp/master.key (mode 0o400) for headless servers
     where no keychain backend is available.

Migration: if the resolved key cannot decrypt an existing store, a one-time
attempt is made with the legacy PBKDF2("changeit") key. On success the store
is re-encrypted with the new key automatically.
"""

import json
import logging
import os
import secrets
import sys
from pathlib import Path

import base64
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

log = logging.getLogger(__name__)

_STORE_PATH = Path.home() / ".bambu-mcp" / "secrets.enc"
_KEY_PATH = Path.home() / ".bambu-mcp" / "master.key"

_KEYRING_SERVICE = "bambu-mcp"
_KEYRING_USERNAME = "master_key"

# Legacy constants — kept only for one-time migration detection.
_LEGACY_KDF_SALT = b"bambu-mcp"
_LEGACY_KDF_ITERATIONS = 100_000


def _legacy_fernet() -> Fernet:
    """Return the Fernet instance for the legacy "changeit" key (migration only)."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=_LEGACY_KDF_SALT,
        iterations=_LEGACY_KDF_ITERATIONS,
    )
    return Fernet(base64.urlsafe_b64encode(kdf.derive(b"changeit")))


def _store_in_keychain(key: bytes) -> bool:
    """Store *key* in the OS keychain. Returns True on success."""
    try:
        import keyring  # type: ignore[import]
        keyring.set_password(_KEYRING_SERVICE, _KEYRING_USERNAME, key.decode())
        return True
    except Exception as exc:  # noqa: BLE001
        log.debug("keychain store failed: %s", exc)
        return False


def _get_from_keychain() -> bytes | None:
    """Retrieve master key from the OS keychain. Returns None if unavailable."""
    try:
        import keyring  # type: ignore[import]
        stored = keyring.get_password(_KEYRING_SERVICE, _KEYRING_USERNAME)
        if stored:
            return stored.encode()
        return None
    except Exception as exc:  # noqa: BLE001
        log.debug("keychain retrieve failed: %s", exc)
        return None


def _get_or_create_file_key() -> bytes:
    """Read ~/.bambu-mcp/master.key, creating it (mode 0o400) if absent."""
    _KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    if _KEY_PATH.exists():
        return _KEY_PATH.read_bytes().strip()
    key = Fernet.generate_key()
    _KEY_PATH.write_bytes(key)
    _KEY_PATH.chmod(0o400)
    log.info("Generated new master key at %s", _KEY_PATH)
    return key


def _get_fernet() -> Fernet:
    """
    Resolve the master Fernet key using the priority chain:
      1. BAMBU_MCP_FERNET_KEY env var (raw base64 Fernet key)
      2. OS keychain (generate + store on first use)
      3. File fallback ~/.bambu-mcp/master.key (mode 0o400)
    """
    # 1. Environment variable injection (containers / CI)
    env_key = os.environ.get("BAMBU_MCP_FERNET_KEY")
    if env_key:
        return Fernet(env_key.encode() if isinstance(env_key, str) else env_key)

    # 2. OS keychain
    key = _get_from_keychain()
    if key is None:
        # First use or new install — generate and store
        key = Fernet.generate_key()
        if _store_in_keychain(key):
            log.info("Generated and stored new master key in OS keychain")
        else:
            # No keychain backend — fall through to file
            key = None

    if key is not None:
        return Fernet(key)

    # 3. File fallback (headless servers / no keychain)
    return Fernet(_get_or_create_file_key())


def _load_with_migration(fernet: Fernet) -> dict:
    """
    Load and decrypt the store. On InvalidToken, attempt one-time migration
    from the legacy "changeit" key. If migration succeeds, re-encrypt in-place.
    """
    if not _STORE_PATH.exists():
        return {}

    ciphertext = _STORE_PATH.read_bytes()
    try:
        return json.loads(fernet.decrypt(ciphertext))
    except InvalidToken:
        pass

    # Attempt legacy migration
    log.info("Primary key failed — attempting migration from legacy 'changeit' key")
    try:
        legacy = _legacy_fernet()
        data = json.loads(legacy.decrypt(ciphertext))
        # Re-encrypt with the new key
        _STORE_PATH.write_bytes(fernet.encrypt(json.dumps(data).encode()))
        log.info("Migration complete: store re-encrypted with new master key")
        return data
    except InvalidToken:
        print(
            f"ERROR: Failed to decrypt {_STORE_PATH} — "
            "set BAMBU_MCP_FERNET_KEY or delete the store to start fresh.",
            file=sys.stderr,
        )
        return {}


def _load() -> tuple[dict, Fernet]:
    f = _get_fernet()
    return _load_with_migration(f), f


def _save(data: dict, fernet: Fernet) -> None:
    _STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _STORE_PATH.write_bytes(fernet.encrypt(json.dumps(data).encode()))


def get(key: str, default=None):
    """Return the value for *key*, or *default* if not found."""
    data, _ = _load()
    return data.get(key, default)


def set(key: str, value) -> None:  # noqa: A001
    """Store *value* under *key*."""
    data, f = _load()
    data[key] = value
    _save(data, f)


def delete(key: str) -> bool:
    """Delete *key*. Returns True if the key existed."""
    data, f = _load()
    if key not in data:
        return False
    del data[key]
    _save(data, f)
    return True


def list_keys() -> list[str]:
    """Return all stored key names."""
    data, _ = _load()
    return list(data.keys())


def get_all() -> dict:
    """Return a copy of the full secrets dict."""
    data, _ = _load()
    return dict(data)
