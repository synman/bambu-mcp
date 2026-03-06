"""
auth.py — Printer credential resolution from secrets_store.

Credentials are stored per-printer under keys:
  bambu-{name}_ip, bambu-{name}_access_code, bambu-{name}_serial

Configured printer names are stored as a JSON list under key "_printer_names".
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

import secrets_store


def get_printer_credentials(name: str) -> dict:
    """
    Return {ip, access_code, serial} for a configured printer.
    Raises KeyError if the printer has no stored credentials.
    """
    log.debug("get_printer_credentials: called for name=%s", name)
    prefix = f"bambu-{name}"
    ip = secrets_store.get(f"{prefix}_ip")
    access_code = secrets_store.get(f"{prefix}_access_code")
    serial = secrets_store.get(f"{prefix}_serial")
    if not ip or not access_code or not serial:
        log.warning("get_printer_credentials: printer '%s' not fully configured (ip=%s serial=%s)", name, bool(ip), bool(serial))
        raise KeyError(
            f"Printer '{name}' not fully configured. "
            f"Use add_printer('{name}', ip, serial, access_code) to set it up."
        )
    log.debug("get_printer_credentials: returning ip=%s serial=%s access_code=<redacted>", ip, serial)
    return {"ip": ip, "access_code": access_code, "serial": serial}


def save_printer_credentials(name: str, ip: str, access_code: str, serial: str) -> None:
    """Save credentials for a printer and add it to the configured printer list."""
    log.debug("save_printer_credentials: called for name=%s ip=%s serial=%s access_code=<redacted>", name, ip, serial)
    prefix = f"bambu-{name}"
    secrets_store.set(f"{prefix}_ip", ip)
    secrets_store.set(f"{prefix}_access_code", access_code)
    secrets_store.set(f"{prefix}_serial", serial)
    log.debug("save_printer_credentials: credentials saved for '%s'", name)
    names = secrets_store.get("_printer_names", default=[])
    if name not in names:
        names.append(name)
        secrets_store.set("_printer_names", names)
        log.debug("save_printer_credentials: updated printer names list, count=%d", len(names))
    log.debug("save_printer_credentials: → done for name=%s", name)


def delete_printer_credentials(name: str) -> None:
    """Remove all credentials for a printer."""
    log.debug("delete_printer_credentials: called for name=%s", name)
    prefix = f"bambu-{name}"
    for suffix in ("_ip", "_access_code", "_serial"):
        secrets_store.delete(f"{prefix}{suffix}")
    log.debug("delete_printer_credentials: deleted credentials for '%s'", name)
    names = secrets_store.get("_printer_names", default=[])
    if name in names:
        names.remove(name)
        secrets_store.set("_printer_names", names)
        log.debug("delete_printer_credentials: updated printer names list, count=%d", len(names))
    log.debug("delete_printer_credentials: → done for name=%s", name)


def get_configured_printer_names() -> list[str]:
    """Return the list of configured printer names."""
    log.debug("get_configured_printer_names: called")
    names = secrets_store.get("_printer_names", default=[])
    log.debug("get_configured_printer_names: → %s", names)
    return names
