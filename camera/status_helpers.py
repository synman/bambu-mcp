"""
camera/status_helpers.py — Shared status rendering helpers for camera surfaces.
"""

from __future__ import annotations


def get_active_spool(state):
    """Return the spool object for the currently active tray, if any.

    H2D telemetry reports the active AMS unit separately from the active tray slot.
    Matching by tray id alone can select the wrong spool entry (for example the
    placeholder spool with id=0 instead of the AMS HT slot-0 spool). Prefer the
    (active_ams_id, slot_id) pair when available, then fall back to legacy id matching.
    """
    active_tray_id = getattr(state, "active_tray_id", -1)
    if active_tray_id in (-1, 255):
        return None

    spools = getattr(state, "spools", None) or []
    active_ams_id = getattr(state, "active_ams_id", -1)

    if active_ams_id >= 0:
        active_spool = next(
            (
                spool
                for spool in spools
                if getattr(spool, "ams_id", None) == active_ams_id
                and getattr(spool, "slot_id", None) == active_tray_id
            ),
            None,
        )
        if active_spool is not None:
            return active_spool

    return next(
        (spool for spool in spools if getattr(spool, "id", None) == active_tray_id),
        None,
    )


def build_active_filament(state) -> dict | None:
    """Return the active filament HUD payload for the current printer state."""
    active_spool = get_active_spool(state)
    if active_spool is None:
        return None

    color = getattr(active_spool, "color", "") or ""
    if color and not color.startswith("#") and len(color) == 6 and all(
        c in "0123456789abcdefABCDEF" for c in color
    ):
        color = "#" + color

    return {
        "type": getattr(active_spool, "type", "") or "",
        "color": color,
        "remaining_pct": getattr(active_spool, "remaining_percent", 0),
    }
