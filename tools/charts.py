"""
Telemetry dashboard chart tool.

Renders a responsive multi-panel HTML dashboard using matplotlib SVGs
and opens it in the default browser.
"""
from __future__ import annotations

import errno
import hashlib
import io
import logging
import os
import re
import time
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Optional

import matplotlib
matplotlib.use("Agg")  # must be set before any other matplotlib imports
import matplotlib.colors as mcolors
import matplotlib.dates as mdates
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import numpy as np

from tools.url_factory import _q

log = logging.getLogger(__name__)

# ── Dark colour palette ───────────────────────────────────────────────────────
_BG      = "#0d1117"
_PANEL   = "#161b22"
_BORDER  = "#30363d"
_TEXT    = "#c9d1d9"
_MUTED   = "#8b949e"
_BLUE    = "#58a6ff"
_GREEN   = "#3fb950"
_AMBER   = "#f5a623"
_RED     = "#ff7b72"
_PURPLE  = "#d2a8ff"
_ORANGE  = "#ffa657"

_TEMP_COLORS = {
    "tool":    _RED,
    "tool_1":  _ORANGE,
    "bed":     _GREEN,
    "chamber": _BLUE,
}
_FAN_COLORS = [_BLUE, "#79c0ff", _GREEN, _PURPLE]   # part, aux, exhaust, heatbreak
_STATE_COLORS = {
    "RUNNING": _GREEN,
    "PAUSE":   _AMBER,
    "FAILED":  _RED,
    "FINISH":  _BLUE,
    "IDLE":    _MUTED,
    "PREPARE": _PURPLE,
}

# ── Helpers ───────────────────────────────────────────────────────────────────

# Directory open_charts writes the static dashboard into: /tmp/bambu-charts-<name>.html.
_CHARTS_DIR = Path("/tmp")

# Longest file name, in UTF-8 bytes, open_charts will ask the filesystem for (the usual limit is 255).
_MAX_LEAF_BYTES = 200

# Characters that cannot sit inside ONE file name: the path separators, control characters, and "%" itself
# (encoded so two distinct printer names give two distinct file NAMES; the filesystem may still fold two
# such names onto one file, e.g. APFS ignores case and Unicode normalisation).
_UNSAFE_FILENAME_CHARS = re.compile(r"[/\\%\x00-\x1f\x7f]")


def _chart_file_path(name: str) -> Path | None:
    """The static dashboard file for a printer name, or None when it would not stay inside _CHARTS_DIR.

    Only the characters in _UNSAFE_FILENAME_CHARS are percent-encoded; spaces, non-ASCII and everything
    else are kept, so an ordinary name gives the same file name as it always did. A name whose file name
    would exceed _MAX_LEAF_BYTES UTF-8 bytes is cut to fit and given "-<first 12 hex of the SHA-256 of the
    exact name>" before ".html", so two long names sharing a head still get different file names. The path
    is then resolved and must be a direct child of the resolved directory: a symlink planted at the target
    resolves elsewhere and is refused instead of followed.
    """
    stem = "bambu-charts-" + _UNSAFE_FILENAME_CHARS.sub(lambda m: f"%{ord(m.group()):02X}", name)
    if len((stem + ".html").encode()) > _MAX_LEAF_BYTES:
        digest = hashlib.sha256(name.encode()).hexdigest()[:12]
        room = _MAX_LEAF_BYTES - len(f"-{digest}.html")
        stem = stem.encode()[:room].decode(errors="ignore") + "-" + digest
    out = _CHARTS_DIR / (stem + ".html")
    try:
        return out if out.resolve().parent == _CHARTS_DIR.resolve() else None
    except (OSError, RuntimeError):  # unresolvable target (e.g. a symlink loop)
        return None


def _style(ax, title: str = "") -> None:
    """Apply dark theme to an axes object."""
    ax.set_facecolor(_PANEL)
    if title:
        ax.set_title(title, color=_TEXT, fontsize=8, pad=5)
    ax.tick_params(colors=_MUTED, labelsize=7)
    for spine in ax.spines.values():
        spine.set_edgecolor(_BORDER)
    ax.yaxis.label.set_color(_MUTED)
    ax.xaxis.label.set_color(_MUTED)


def _no_data(ax, msg: str = "No data available") -> None:
    ax.text(0.5, 0.5, msg, transform=ax.transAxes,
            ha="center", va="center", color=_MUTED, fontsize=9)
    ax.set_xticks([])
    ax.set_yticks([])


def _svg(fig) -> str:
    """Render a matplotlib figure to an inline SVG string and close the figure."""
    buf = io.BytesIO()
    fig.savefig(buf, format="svg", bbox_inches="tight", facecolor=_BG)
    plt.close(fig)
    raw = buf.getvalue().decode("utf-8")
    # Strip XML declaration and DTD — keep only the <svg> element for inline embedding.
    lines = raw.splitlines()
    start = next((i for i, ln in enumerate(lines) if "<svg" in ln), 0)
    return "\n".join(lines[start:])


def _legend(ax, handles, **kw) -> None:
    ax.legend(handles=handles, fontsize=6, facecolor=_PANEL, labelcolor=_TEXT,
              edgecolor=_BORDER, **kw)


def _to_dt(ts_list: list) -> list:
    """Convert a list of epoch-float timestamps to datetime objects for matplotlib."""
    return [datetime.fromtimestamp(t) for t in ts_list]


def _apply_time_axis(ax) -> None:
    """Show a formatted HH:MM time axis on *ax*."""
    ax.xaxis.set_visible(True)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=4, maxticks=8))
    plt.setp(ax.get_xticklabels(), fontsize=6, color=_MUTED, rotation=0, ha="center")


def _draw_events(ax, events: list, dt_range=None) -> None:
    """Draw vertical event markers on *ax* with a small rotated label."""
    if not events:
        return
    for ev in events:
        try:
            dt = datetime.fromtimestamp(ev["t"])
            if dt_range and not (dt_range[0] <= dt <= dt_range[1]):
                continue
            ax.axvline(dt, color=_MUTED, linestyle=":", linewidth=0.5, alpha=0.6)
            ax.text(dt, 1.01, ev["label"], transform=ax.get_xaxis_transform(),
                    fontsize=4.5, color=_MUTED, rotation=45, va="bottom",
                    ha="left", clip_on=True)
        except Exception:
            pass


# ── Panel renderers ───────────────────────────────────────────────────────────

def _row_temps(data: dict, is_h2d: bool, events: list | None = None) -> str:
    evs = events or []
    fig, (ax_nozzle, ax_bed) = plt.subplots(1, 2, figsize=(16, 3.2), facecolor=_BG)
    fig.subplots_adjust(wspace=0.12)

    # Nozzle(s)
    nozzle_title = "Left Nozzle & Right Nozzle" if is_h2d else "Nozzle Temperature"
    _style(ax_nozzle, nozzle_title)
    handles = []
    t0 = data.get("tool", {}).get("data", [])
    if t0:
        ts, vs = _to_dt([p["t"] for p in t0]), [p["v"] for p in t0]
        label = "Right Nozzle" if is_h2d else "Nozzle"
        ax_nozzle.plot(ts, vs, color=_TEMP_COLORS["tool"], linewidth=1)
        handles.append(mpatches.Patch(color=_TEMP_COLORS["tool"], label=label))
        # Target line
        t0_tgt = data.get("tool_target", {}).get("data", [])
        if t0_tgt:
            tgt_ts = _to_dt([p["t"] for p in t0_tgt])
            tgt_vs = [p["v"] for p in t0_tgt]
            ax_nozzle.plot(tgt_ts, tgt_vs, color=_TEMP_COLORS["tool"],
                           linewidth=0.7, linestyle="--", alpha=0.55)
    if is_h2d:
        t1 = data.get("tool_1", {}).get("data", [])
        if t1:
            ts1, vs1 = _to_dt([p["t"] for p in t1]), [p["v"] for p in t1]
            ax_nozzle.plot(ts1, vs1, color=_TEMP_COLORS["tool_1"], linewidth=1)
            handles.append(mpatches.Patch(color=_TEMP_COLORS["tool_1"], label="Left Nozzle"))
            t1_tgt = data.get("tool_1_target", {}).get("data", [])
            if t1_tgt:
                tgt_ts1 = _to_dt([p["t"] for p in t1_tgt])
                tgt_vs1 = [p["v"] for p in t1_tgt]
                ax_nozzle.plot(tgt_ts1, tgt_vs1, color=_TEMP_COLORS["tool_1"],
                               linewidth=0.7, linestyle="--", alpha=0.55)
    if handles:
        _legend(ax_nozzle, handles)
        _apply_time_axis(ax_nozzle)
        _draw_events(ax_nozzle, evs)
    else:
        _no_data(ax_nozzle)
    ax_nozzle.set_ylabel("°C", fontsize=7)

    # Bed + Chamber
    _style(ax_bed, "Bed & Chamber Temperature")
    handles_bc = []
    for key, label in [("bed", "Bed"), ("chamber", "Chamber")]:
        s = data.get(key, {}).get("data", [])
        if s:
            ax_bed.plot(_to_dt([p["t"] for p in s]), [p["v"] for p in s],
                        color=_TEMP_COLORS[key], linewidth=1)
            handles_bc.append(mpatches.Patch(color=_TEMP_COLORS[key], label=label))
            # Target dashed line
            s_tgt = data.get(f"{key}_target", {}).get("data", [])
            if s_tgt:
                ax_bed.plot(_to_dt([p["t"] for p in s_tgt]), [p["v"] for p in s_tgt],
                            color=_TEMP_COLORS[key], linewidth=0.7,
                            linestyle="--", alpha=0.55)
    if handles_bc:
        _legend(ax_bed, handles_bc)
        _apply_time_axis(ax_bed)
        _draw_events(ax_bed, evs)
    else:
        _no_data(ax_bed)
    ax_bed.set_ylabel("°C", fontsize=7)

    return _svg(fig)


def _row_fans(data: dict, events: list | None = None) -> str:
    evs = events or []
    fig, ax = plt.subplots(1, 1, figsize=(16, 2.4), facecolor=_BG)
    _style(ax, "Fan Speeds")
    fan_defs = [
        ("part_fan",     "Part Cooling"),
        ("aux_fan",      "Aux"),
        ("exhaust_fan",  "Exhaust"),
        ("heatbreak_fan","Heatbreak"),
    ]
    handles = []
    for (key, label), color in zip(fan_defs, _FAN_COLORS):
        s = data.get(key, {}).get("data", [])
        if s:
            ax.step(_to_dt([p["t"] for p in s]), [p["v"] for p in s],
                    color=color, linewidth=0.9, where="post")
            handles.append(mpatches.Patch(color=color, label=label))
    if handles:
        _legend(ax, handles, ncol=4)
        _apply_time_axis(ax)
        _draw_events(ax, evs)
    else:
        _no_data(ax)
    ax.set_ylabel("%", fontsize=7)
    ax.set_ylim(-5, 105)
    return _svg(fig)


def _row_health(health_history: list) -> str:
    fig, (ax_sig, ax_tl) = plt.subplots(1, 2, figsize=(16, 3.4), facecolor=_BG)
    fig.subplots_adjust(wspace=0.14)

    # Anomaly signals
    _style(ax_sig, "Anomaly Signals")
    if health_history:
        ts = _to_dt([r["ts"] for r in health_history])
        hot  = [r.get("hot_pct") or 0   for r in health_history]
        st   = [r.get("strand_score") or 0 for r in health_history]
        diff = [r.get("diff_score") or 0   for r in health_history]
        ax_sig.plot(ts, hot,  color=_RED,   linewidth=1.0, label="Hot px %")
        ax_sig.plot(ts, st,   color=_AMBER, linewidth=1.0, label="Strand")
        ax_sig.plot(ts, diff, color=_BLUE,  linewidth=0.8, alpha=0.7, label="Diff")
        ax_sig.set_ylim(-0.02, 1.05)
        _legend(ax_sig, None)
        ax_sig.legend(fontsize=6, facecolor=_PANEL, labelcolor=_TEXT, edgecolor=_BORDER)
        _apply_time_axis(ax_sig)
    else:
        _no_data(ax_sig, "No health data yet\n(starts once the print is past its preparation stages)")

    # Health timeline
    _style(ax_tl, "Print Health Timeline")
    if health_history:
        ts   = _to_dt([r["ts"] for r in health_history])
        suc  = [r.get("success_pct") or 0 for r in health_history]
        conf = [r.get("confidence") or 0  for r in health_history]
        ax_tl.plot(ts, suc,  color=_GREEN, linewidth=1.2, label="Success %")
        ax_tl.plot(ts, conf, color=_BLUE,  linewidth=0.8, linestyle="--",
                   alpha=0.7, label="Confidence")
        ax_tl.set_ylim(-0.02, 1.05)
        ax_tl.set_ylabel("Probability", fontsize=7)
        rem_pts = [(r["ts"], r["remaining_min"]) for r in health_history
                   if r.get("remaining_min") is not None]
        if rem_pts:
            ax2 = ax_tl.twinx()
            ax2.plot(_to_dt([x[0] for x in rem_pts]), [x[1] for x in rem_pts],
                     color=_AMBER, linewidth=0.8, linestyle=":", alpha=0.85)
            ax2.set_ylabel("Remaining (min)", color=_AMBER, fontsize=7)
            ax2.tick_params(colors=_AMBER, labelsize=7)
            ax2.spines["right"].set_edgecolor(_AMBER)
            for sp in ("top","left","bottom"):
                ax2.spines[sp].set_visible(False)
        ax_tl.legend(fontsize=6, facecolor=_PANEL, labelcolor=_TEXT,
                     edgecolor=_BORDER, loc="lower left")
        _apply_time_axis(ax_tl)
    else:
        _no_data(ax_tl, "No health data yet\n(starts once the print is past its preparation stages)")

    return _svg(fig)


def _row_analysis(factors: dict, durations: dict) -> str:
    # 3-column layout: [radar | legend table | pie]
    fig = plt.figure(figsize=(16, 4.6), facecolor=_BG)
    gs = fig.add_gridspec(1, 3, width_ratios=[2.6, 3.8, 3.6], wspace=0.18,
                          left=0.02, right=0.98, top=0.90, bottom=0.06)
    ax_radar  = fig.add_subplot(gs[0])
    ax_legend = fig.add_subplot(gs[1])
    ax_pie    = fig.add_subplot(gs[2])

    # ── Radar image ──────────────────────────────────────────────────────────
    _style(ax_radar, "Failure Driver Analysis")
    ax_radar.axis("off")
    try:
        import PIL.Image
        from camera.job_analyzer import _build_radar_png
        radar_factors = factors if factors else {
            k: 0.5 for k in ("material", "platform", "progress", "anomaly",
                              "thermal", "humidity", "stability", "settings")
        }
        png_bytes = _build_radar_png(radar_factors, size=340)
        img = PIL.Image.open(io.BytesIO(png_bytes))
        ax_radar.imshow(img)
        ax_radar.set_facecolor(_PANEL)
    except Exception as e:
        log.debug("open_charts: radar embed failed: %s", e)
        _no_data(ax_radar, "No radar data")

    # ── Legend table ──────────────────────────────────────────────────────────
    _style(ax_legend, "")
    ax_legend.axis("off")
    legend_rows = [
        ("MATERIAL",  "Base failure rate by filament type (PLA=low, PC/PA=high)"),
        ("PLATFORM",  "Printer series risk modifier (H2D=best, A1=worst)"),
        ("PROGRESS",  "Survival curve — most failures occur before 15% progress"),
        ("ANOMALY",   "Camera AI signal: spaghetti / air-printing detection score"),
        ("THERMAL",   "Env risk: door open, nozzle/bed drift, chamber mismatch"),
        ("HUMIDITY",  "Hygroscopic penalty — filament type × AMS moisture index"),
        ("STABILITY", "Signal consistency — sustained clean lowers risk score"),
        ("SETTINGS",  "Slicer config risk: brim, infill density, wall count, supports"),
    ]
    tbl = ax_legend.table(
        cellText=legend_rows,
        colLabels=["Factor", "What it measures"],
        cellLoc="left",
        loc="center",
        bbox=[0.0, 0.0, 1.0, 1.0],
    )
    tbl.auto_set_font_size(False)
    tbl.set_fontsize(6.5)
    for (row, col), cell in tbl.get_celld().items():
        if row == 0:
            cell.set_facecolor("#1c2128")
            cell.set_text_props(color=_BLUE, fontweight="bold")
        else:
            cell.set_facecolor(_PANEL)
            cell.set_text_props(color=_TEXT if col == 1 else _BLUE)
        cell.set_edgecolor(_BORDER)

    # ── Print state pie ───────────────────────────────────────────────────────
    _style(ax_pie, "Print State Durations")
    if durations:
        labels = list(durations.keys())
        sizes  = list(durations.values())
        colors = [_STATE_COLORS.get(lbl, _MUTED) for lbl in labels]
        wedges, texts, auts = ax_pie.pie(
            sizes, labels=labels, colors=colors, autopct="%1.0f%%",
            pctdistance=0.75,
            textprops={"fontsize": 7, "color": _TEXT},
            wedgeprops={"linewidth": 0.5, "edgecolor": _BG},
        )
        for at in auts:
            at.set_fontsize(6)
            at.set_color(_BG)
    else:
        _no_data(ax_pie, "No state duration data yet")

    return _svg(fig)


def _row_calibration() -> str:
    fig, ax = plt.subplots(1, 1, figsize=(16, 3.8), facecolor=_BG)
    _style(ax, "H2D Camera Calibration — Corner Registration Status")

    try:
        from camera.coord_transform import SHELL, SYNBOT, OFFSETS
    except ImportError:
        _no_data(ax, "Calibration data not available")
        return _svg(fig)

    # Show the 4 corners in SHELL pixel space.
    # NL/NR are in-frame; FL/FR are extrapolated outside the camera frame.
    corner_labels = {"FL": "Far-Left", "NL": "Near-Left", "NR": "Near-Right", "FR": "Far-Right"}
    in_frame = {"NL", "NR"}

    for corner, (sx, sy) in SHELL.items():
        color = _GREEN if corner in in_frame else _MUTED
        marker = "o" if corner in in_frame else "x"
        ms = 60 if corner in in_frame else 40
        ax.scatter([sx], [sy], color=color, marker=marker, s=ms, zorder=5)
        suffix = "" if corner in in_frame else "\n(extrapolated)"
        dist = OFFSETS.get(corner, {}).get("dist", 0)
        ax.annotate(
            f"{corner}: {corner_labels[corner]}{suffix}\nΔ={dist:.0f}px raw",
            xy=(sx, sy),
            xytext=(sx + 40, sy + 40),
            fontsize=6,
            color=color,
            arrowprops=dict(arrowstyle="-", color=color, lw=0.6),
        )

    # Draw approximate frame boundary (clip to visible corners ± margin)
    in_xs = [SHELL[k][0] for k in in_frame]
    in_ys = [SHELL[k][1] for k in in_frame]
    margin = 150
    ax.set_xlim(min(in_xs) - margin, max(in_xs) + margin)
    ax.set_ylim(min(in_ys) - margin, max(in_ys) + margin)

    ax.set_xlabel("X (SHELL px)", fontsize=7)
    ax.set_ylabel("Y (SHELL px)", fontsize=7)

    # Metadata annotation
    ax.text(
        0.5, 0.02,
        "Reprojection error: 8.62 px  ·  N=5 inliers  ·  Calibrated 2026-03-13  ·  Z=2mm  ·  Chamber light OFF",
        transform=ax.transAxes, ha="center", va="bottom",
        color=_MUTED, fontsize=6.5, style="italic",
    )

    handles = [
        mpatches.Patch(color=_GREEN, label="In-frame corners (NL, NR)"),
        mpatches.Patch(color=_MUTED, label="Extrapolated corners (FL, FR)"),
    ]
    _legend(ax, handles)

    return _svg(fig)


def _spool_rgb(color) -> tuple:
    """Bar colour for a get_spool_info ``color``: a CSS3 name or "#RRGGBBAA"; grey when unparseable."""
    try:
        return mcolors.to_rgb(color)
    except (ValueError, TypeError):
        return (0.27, 0.27, 0.27)


def _row_ams(printer_name: str) -> str:
    fig, ax = plt.subplots(1, 1, figsize=(16, 2.6), facecolor=_BG)
    _style(ax, "AMS Filament Remaining")

    spools = []
    try:
        from tools.state import get_spool_info
        info = get_spool_info(printer_name)
        # An entry with an empty type is an empty slot or holder, not a spool.
        spools = [s for s in (info.get("spools") or []) if s.get("type")]
    except Exception as e:
        log.warning("open_charts: AMS spool fetch failed: %s", e, exc_info=True)

    if not spools:
        _no_data(ax, "No AMS spool data")
        ax.axis("off")
        return _svg(fig)

    labels  = [s.get("display_name") or f"Slot {i}" for i, s in enumerate(spools)]
    # remaining_percent is -1 when the tray reports no 'remain' value (always for an external holder).
    remains = [s.get("remaining_percent") for s in spools]
    values  = [r if isinstance(r, (int, float)) and r >= 0 else 0 for r in remains]
    colors  = [_spool_rgb(s.get("color")) for s in spools]

    ypos = list(range(len(labels)))
    bars = ax.barh(ypos, values, color=colors, edgecolor=_BORDER, linewidth=0.4, height=0.6)
    ax.set_xlim(0, 110)
    ax.set_yticks(ypos)
    ax.set_yticklabels(labels, fontsize=7)
    ax.set_xlabel("Remaining %", fontsize=7)
    for bar, remain in zip(bars, remains):
        known = isinstance(remain, (int, float)) and remain >= 0
        ax.text(bar.get_width() + 1.5, bar.get_y() + bar.get_height() / 2,
                f"{remain:.0f}%" if known else "n/a", va="center", ha="left", fontsize=6.5, color=_TEXT)

    return _svg(fig)


# ── Main tool ─────────────────────────────────────────────────────────────────

def open_charts(name: str) -> dict:
    """Generate and open a responsive telemetry dashboard for the named printer.

    WHEN to use: the human user wants to see the printer's rolling telemetry (temperatures,
    fans, print health, failure drivers) as charts in a browser tab. The dashboard also
    carries a fixed H2D camera-calibration drawing and an AMS filament panel with the loaded
    spools' remaining percentage (see Notes).

    Sibling disambiguation: ``render_charts_html`` returns the same dashboard HTML as a
    string and opens nothing; ``render_charts_panels`` returns only the SVG panels.
    ``open_job_state`` opens the background monitor's health images (camera composite,
    annotated frame), not these time-series charts. ``get_monitoring_series`` returns a URL
    to the raw JSON series for one field, which the caller then fetches, rather than a
    rendered dashboard.

    Args:
        name: Printer name as configured (case-sensitive; see ``get_configured_printers``).

    Returns:
        ``{"output_path": str, "opened": bool}`` on success. ``output_path`` is the static
        HTML file ``/tmp/bambu-charts-{name}.html``, written on every call (created readable by its owner
        only, an existing file replaced); in the file name "/", "\\", "%" and control characters of the
        printer name are percent-encoded (so the file always sits directly inside /tmp and two different
        names give two different file names) and every other character, spaces and non-ASCII included,
        is kept as is. Different file names are not a guarantee of different files: a filesystem that
        ignores case or Unicode normalisation (APFS does both) maps names that differ only in case or
        normalisation form onto one file. A name that would make the file name longer than 200 bytes
        (UTF-8) is cut to fit and gets ``-<12 hex characters of the name's SHA-256>`` before ``.html``, so
        long names that share a head still get different file names. ``opened`` is True
        when an existing browser tab was focused or the browser was launched successfully,
        False when the browser launch reported failure. Returns ``{"error": "not_connected"}``
        when no telemetry collector exists for the printer: collectors are created only for
        printers configured when the server started, so a printer added with ``add_printer``
        at runtime returns this until the server restarts. A paused or disconnected printer
        that has a collector renders its last-collected data instead of an error.
        ``{"error": "unsafe_output_path", "detail": ...}`` (nothing written, no browser opened) when
        the file path resolves outside /tmp or is a symbolic link, which happens only if something else
        planted a symlink at that path (the file is opened without following a symlink, so one planted
        between the check and the write is refused too).
        ``{"error": "write_failed", "detail": str}`` (no browser opened) when the file cannot be written
        for any other reason (a directory at that path, no permission, a full disk).

    Notes:
        Side effects: writes the HTML file above and focuses or opens a browser tab.
        The opened URL is ``http://localhost:{api_port}/api/charts?printer={name}`` (the
        page then auto-refreshes live data every 30 s) when the API server port is known,
        otherwise the static ``file://`` URL of the written file (no live refresh). The name
        is percent-encoded in either URL, so a printer name containing a space, "&", "#" or
        "+" reaches the server intact; names made only of letters, digits and "-", "_", ".",
        "~" appear unchanged.
        An existing tab is reused only on macOS, and only when Google Chrome or Safari
        already has a tab whose URL starts with the target URL (found with a 3-second
        osascript query). On any other platform, or when no such tab exists, a new tab is
        opened with ``webbrowser.open`` on the machine running the server.

        Renders 6 chart sections as inline SVGs assembled into a dark-themed responsive HTML
        page:

          1. Temperature history — two side-by-side panels: nozzle(s), and bed and chamber
             together
          2. Fan speeds — all 4 fans as a full-width step chart
          3. Anomaly signals + print health timeline (side by side)
          4. Failure driver spider chart + legend  |  print state pie
          5. Camera calibration corner status — a FIXED reference drawing of the H2D camera
             calibration constants with a hardcoded calibration caption; not per-printer, not
             telemetry, and identical for every printer including non-H2D models
          6. AMS filament remaining bars — one bar per loaded spool from ``get_spool_info``
             (slots and holders that hold no filament are skipped), coloured with the spool
             colour and labelled with its remaining percentage, or "n/a" when the tray
             reports none; shows a "No AMS spool data" placeholder when no spool is loaded or
             the spool lookup fails (logged as a warning)

        Nozzle panel: when the second extruder (tool_1) has reported a nonzero temperature in
        the retained window, the panel overlays two lines on one axes, "Right Nozzle" (T0) and
        "Left Nozzle" (T1), in that legend order, with no spatial left-to-right ordering. That
        data heuristic, not the printer model, selects the dual layout; otherwise a single
        "Nozzle" series is shown.

        History and resets: temperature, fan and event series cover the last 60 minutes and
        are held in memory only, so they start empty at server start. The health and anomaly
        panels hold the most recent 60 analysis records (roughly an hour) and are cleared when
        the monitor sees a new job start (a change into RUNNING or PAUSE from an idle state).
        The print-state pie shows time spent in each gcode_state for the current
        job, including idle time, and resets only when a new job name is seen.

        Health timeline and anomaly data come from the background print monitor, which skips
        camera analysis while the job is in a preparation or maintenance stage (bed leveling,
        preheating, filament change, calibration, a pause). Records begin on the monitor's
        first tick (ticks run about every 10 s) after the printer is RUNNING or PAUSE and out
        of those stages, not 60 s later, and then about every 60 s. Nothing is recorded while
        the printer is idle, while the job is in one of those stages, when no camera frame can
        be captured, or when the failure-probability model returns no value.

        Failure driver radar: with no analysis result yet, or while the monitor's latest
        result is a stage-gated placeholder (neither carries ``factor_contributions``), the
        radar renders a placeholder with every factor at 0.5, a uniform polygon that looks
        like measured data but is not. Only a radar drawn from a real ``factor_contributions``
        result reflects measured risk.
    """
    log.debug("open_charts: called for name=%s", name)

    # Always render the static file so output_path is stable across restarts.
    html = render_charts_html(name)
    if html.startswith("<html><body style"):
        return {"error": "not_connected"}
    out = _chart_file_path(name)
    if out is None:
        return {"error": "unsafe_output_path",
                "detail": f"the dashboard file for printer {name!r} would resolve outside {_CHARTS_DIR} "
                          f"(a symlink at the target path?); nothing was written"}
    try:
        fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(html)
    except OSError as e:
        if e.errno == errno.ELOOP or out.is_symlink():   # O_NOFOLLOW refused a symlink planted after the check
            return {"error": "unsafe_output_path",
                    "detail": f"the dashboard file for printer {name!r} is a symbolic link; nothing was written"}
        log.warning("open_charts: cannot write %s: %s", out, e)
        return {"error": "write_failed", "detail": str(e)}

    # Prefer the HTTP URL so the browser auto-refreshes live data every 30s;
    # fall back to the static file:// URL if the API server is not running.
    try:
        from tools.system import get_server_info
        info = get_server_info()
        api_port = info.get("api_port", 0)
    except Exception:
        api_port = 0

    url = f"http://localhost:{api_port}/api/charts?printer={_q(name)}" if api_port else out.as_uri()

    # Reuse an existing browser tab rather than opening a new one each time.
    try:
        from tools.camera import _focus_existing_tab
        focused = _focus_existing_tab(url)
    except Exception:
        focused = False

    if focused:
        opened = True
        log.info("open_charts: focused existing tab %s", url)
    else:
        opened = webbrowser.open(url)
        log.info("open_charts: opened new tab %s opened=%s", url, opened)

    return {"output_path": str(out), "opened": opened}


def _assemble_html(name: str, svgs: list, section_titles: list) -> str:
    """Assemble chart SVGs into a responsive dark-themed HTML page."""
    sections_html = ""
    for i, (title, svg) in enumerate(zip(section_titles, svgs)):
        sections_html += f"""
    <section>
      <h2>{title}</h2>
      <div class="chart-wrap" id="panel-{i}">{svg}</div>
    </section>"""

    ts_str = time.strftime("%Y-%m-%d %H:%M:%S")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Bambu MCP — {name} Dashboard</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#0d1117;color:#c9d1d9;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;font-size:14px;line-height:1.5;padding:16px 20px}}
h1{{color:#58a6ff;font-size:1.25rem;margin-bottom:3px}}
.sub{{color:#8b949e;font-size:0.78rem;margin-bottom:20px}}
.status{{display:inline-block;width:8px;height:8px;border-radius:50%;background:#3fb950;margin-right:5px;vertical-align:middle}}
.status.err{{background:#ff7b72}}
section{{background:#161b22;border:1px solid #30363d;border-radius:8px;margin-bottom:14px;overflow:hidden}}
h2{{color:#f0f6fc;font-size:0.82rem;padding:9px 14px;background:#1c2128;border-bottom:1px solid #30363d;margin:0}}
.chart-wrap{{padding:8px;width:100%;overflow-x:hidden}}
.chart-wrap svg{{width:100%;height:auto;display:block}}
</style>
</head>
<body>
<h1>&#x1F4CA; {name} — Telemetry Dashboard</h1>
<div class="sub"><span class="status" id="dot"></span><span id="ts">Generated {ts_str}</span> &nbsp;·&nbsp; live refresh every 30 s</div>
{sections_html}
<script>
(function(){{
  var printer = (new URLSearchParams(location.search).get('printer')) || {repr(name)};
  function refresh(){{
    fetch('/api/charts_panels?printer=' + encodeURIComponent(printer))
      .then(function(r){{ return r.json(); }})
      .then(function(d){{
        d.panels.forEach(function(svg, i){{
          var el = document.getElementById('panel-' + i);
          if (el) el.innerHTML = svg;
        }});
        document.getElementById('ts').textContent = 'Updated ' + d.ts;
        document.getElementById('dot').className = 'status';
      }})
      .catch(function(e){{
        console.warn('charts refresh error', e);
        document.getElementById('dot').className = 'status err';
      }});
  }}
  setInterval(refresh, 30000);
}})();
</script>
</body>
</html>"""


def render_charts_html(name: str) -> str:
    """Render and return the full telemetry dashboard HTML for a printer as a string.

    WHEN to use: you need the dashboard page markup itself (this is what the ``/api/charts``
    HTTP route serves) without writing a file or opening a browser.

    Sibling disambiguation: ``open_charts`` renders the same page, writes it to
    ``/tmp/bambu-charts-{name}.html`` (name made file-safe, see ``open_charts`` Returns) and opens it in a
    browser; ``render_charts_panels``
    returns only the SVG panels as JSON for AJAX refresh instead of the full page.

    Args:
        name: Printer name as configured (case-sensitive; see ``get_configured_printers``).

    Returns:
        A ``str`` containing a complete HTML document (six inline-SVG chart sections plus a
        30 s auto-refresh script). The AMS Filament section draws the loaded spools, or a "No
        AMS spool data" placeholder when none is loaded; the calibration section is a fixed
        H2D reference drawing (see ``open_charts`` Notes). The document is VERY LARGE (six inline
        SVGs from 16-inch-wide figures carrying up to an hour of plotted points; several
        hundred KB is typical, estimated rather than measured), so it suits the
        ``/api/charts`` HTTP route or a human-facing consumer; an agent should call
        ``open_charts`` or ``get_monitoring_series`` instead of pulling it into context.
        This tool never returns a dict: when no telemetry collector exists for the printer
        (the printer was not configured when the server started) it returns a short HTML
        error page
        (``<html><body style=...><h2>Printer '<name>' not connected</h2></body></html>``)
        instead. It writes no file and opens no browser.

    Notes:
        History windows, reset behavior and health-data timing are the same as described in
        ``open_charts`` Notes.
    """
    from data_collector import data_collector
    from camera import job_monitor as _jm

    raw_data = data_collector.get_all_data(name)
    if raw_data is None:
        return f"<html><body style='background:#0d1117;color:#ff7b72;font-family:sans-serif;padding:40px'><h2>Printer '{name}' not connected</h2></body></html>"

    series_data = raw_data.get("collections") or raw_data
    health_history = _jm.get_health_history(name)
    latest_result  = _jm.get_latest_result(name)
    factors        = (latest_result or {}).get("factor_contributions") or {}
    durations      = raw_data.get("gcode_state_durations") or {}
    events         = raw_data.get("events") or []

    tool_1_data = series_data.get("tool_1", {}).get("data", [])
    is_h2d = any(p.get("v", 0) != 0 for p in tool_1_data)

    section_titles = [
        "Temperature History", "Fan Speeds", "Anomaly &amp; Health",
        "Failure Analysis", "Camera Calibration", "AMS Filament",
    ]
    svgs = [
        _row_temps(series_data, is_h2d, events),
        _row_fans(series_data, events),
        _row_health(health_history),
        _row_analysis(factors, durations),
        _row_calibration(),
        _row_ams(name),
    ]
    return _assemble_html(name, svgs, section_titles)


def render_charts_panels(name: str) -> dict:
    """Render only the telemetry dashboard SVG panels for a printer, for AJAX refresh.

    WHEN to use: refreshing the dashboard charts in place (this is what the
    ``/api/charts_panels`` HTTP route serves and what the dashboard page polls every 30 s),
    when you do not need the surrounding HTML page.

    Sibling disambiguation: ``render_charts_html`` returns the whole dashboard page as an
    HTML string; ``open_charts`` writes that page to a file and opens it in a browser. This
    tool returns only the six chart SVGs plus a timestamp.

    Args:
        name: Printer name as configured (case-sensitive; see ``get_configured_printers``).

    Returns:
        ``{"panels": [svg, ...], "ts": "YYYY-MM-DD HH:MM:SS"}`` on success. ``panels`` holds
        six inline SVG strings in dashboard order: temperatures, fans, anomaly and health,
        failure analysis, camera calibration (a fixed H2D reference drawing), AMS filament.
        ``panels[5]`` draws the loaded spools, or the "No AMS spool data" placeholder SVG when
        none is loaded. The payload is
        VERY LARGE (six inline SVG strings; several hundred KB is typical, estimated rather
        than measured). It exists for the dashboard page's own AJAX refresh via
        ``/api/charts_panels``; an agent should use ``open_charts`` or
        ``get_monitoring_series`` instead of pulling it into context. Returns
        ``{"error": "Printer '<name>' not connected"}`` when no telemetry collector exists
        for the printer, i.e. it was not configured when the server started (note the
        message text differs from ``open_charts``, which returns
        ``{"error": "not_connected"}``).

    Notes:
        History windows, reset behavior and health-data timing are the same as described in
        ``open_charts`` Notes.
    """
    from data_collector import data_collector
    from camera import job_monitor as _jm

    raw_data = data_collector.get_all_data(name)
    if raw_data is None:
        return {"error": f"Printer '{name}' not connected"}

    series_data = raw_data.get("collections") or raw_data
    health_history = _jm.get_health_history(name)
    latest_result  = _jm.get_latest_result(name)
    factors        = (latest_result or {}).get("factor_contributions") or {}
    durations      = raw_data.get("gcode_state_durations") or {}
    events         = raw_data.get("events") or []

    tool_1_data = series_data.get("tool_1", {}).get("data", [])
    is_h2d = any(p.get("v", 0) != 0 for p in tool_1_data)

    panels = [
        _row_temps(series_data, is_h2d, events),
        _row_fans(series_data, events),
        _row_health(health_history),
        _row_analysis(factors, durations),
        _row_calibration(),
        _row_ams(name),
    ]
    return {"panels": panels, "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
