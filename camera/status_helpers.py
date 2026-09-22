"""
camera/status_helpers.py — Shared status rendering helpers for camera surfaces.
"""

from __future__ import annotations

import re

import webcolors

_HEX_COLOUR = re.compile(r"#?([0-9a-fA-F]{6}|[0-9a-fA-F]{8})")


def get_active_spool(state):
    """Return the spool object for the currently active tray, or None.

    Same rule as ``tools.state.get_spool_info`` (kept in step by test_status_helpers.py, which
    runs both over one table of states). bpm reports the active tray two ways: a
    single-extruder printer's ``active_tray_id`` is the raw ``tray_now``, an ABSOLUTE tray id
    (AMS unit n slot s is 4n+s, the spool's own ``id``), while a dual-extruder printer's is the
    slot inside the active unit (the H2D's AMS HT reads ``active_ams_id`` 128 with
    ``active_tray_id`` 0). So a spool is active only when it sits in the active unit
    (``ams_id == active_ams_id``) and the tray names it by ``slot_id`` or, for a real AMS tray
    (not the external holders 254/255), by ``id``. The unit is always required: matching the id
    on its own would name AMS unit 0 slot 0 (id 0) as the active spool while the AMS HT feeds.
    External holders match by ``slot_id`` alone, so bpm's empty placeholder (slot_id -1) is
    never returned. ``active_tray_id`` -1 means no tray is active and nothing is searched.
    Returns None when nothing matches, never another unit's spool.
    """
    active_tray_id = getattr(state, "active_tray_id", -1)
    if active_tray_id == -1:
        return None

    active_ams_id = getattr(state, "active_ams_id", -1)
    match_by_id = active_tray_id not in (254, 255)
    return next(
        (
            spool
            for spool in getattr(state, "spools", None) or []
            if getattr(spool, "ams_id", None) == active_ams_id
            and (
                getattr(spool, "slot_id", None) == active_tray_id
                or (match_by_id and getattr(spool, "id", None) == active_tray_id)
            )
        ),
        None,
    )


def _safe_colour(color) -> str:
    """Return ``color`` as ``#`` plus 6 or 8 hex digits, or "" when it is not one.

    The HUD writes the colour into a style attribute, so it must not carry anything but a colour.
    bpm stores a spool colour as the CSS3 name when webcolors knows the hex ("white", "gray") and as
    "#" + the raw printer text when it does not, so a CSS3 name is resolved to its hex and any other
    text, the raw printer text included, comes back empty. ``fullmatch``: ``$`` would let a trailing
    newline through.
    """
    if not isinstance(color, str) or not color:
        return ""
    match = _HEX_COLOUR.fullmatch(color)
    if match:
        return "#" + match.group(1)
    try:
        return webcolors.name_to_hex(color)
    except ValueError:
        return ""


def build_active_filament(state) -> dict | None:
    """Return the active filament HUD payload for the current printer state."""
    active_spool = get_active_spool(state)
    if active_spool is None:
        return None

    return {
        "type": getattr(active_spool, "type", "") or "",
        "color": _safe_colour(getattr(active_spool, "color", "")),
        "remaining_pct": getattr(active_spool, "remaining_percent", 0),
    }
