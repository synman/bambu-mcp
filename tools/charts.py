"""
Telemetry dashboard chart tool.

Renders a responsive multi-panel HTML dashboard using matplotlib SVGs
and opens it in the default browser.
"""
from __future__ import annotations

import io
import logging
import time
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Optional

import matplotlib
matplotlib.use("Agg")  # must be set before any other matplotlib imports
import matplotlib.dates as mdates
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import numpy as np

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


# ── Panel renderers ───────────────────────────────────────────────────────────

def _row_temps(data: dict, is_h2d: bool) -> str:
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
    if is_h2d:
        t1 = data.get("tool_1", {}).get("data", [])
        if t1:
            ts1, vs1 = _to_dt([p["t"] for p in t1]), [p["v"] for p in t1]
            ax_nozzle.plot(ts1, vs1, color=_TEMP_COLORS["tool_1"], linewidth=1)
            handles.append(mpatches.Patch(color=_TEMP_COLORS["tool_1"], label="Left Nozzle"))
    if handles:
        _legend(ax_nozzle, handles)
        _apply_time_axis(ax_nozzle)
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
    if handles_bc:
        _legend(ax_bed, handles_bc)
        _apply_time_axis(ax_bed)
    else:
        _no_data(ax_bed)
    ax_bed.set_ylabel("°C", fontsize=7)

    return _svg(fig)


def _row_fans(data: dict) -> str:
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
        _no_data(ax_sig, "No health data yet\n(starts after first print cycle)")

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
        _no_data(ax_tl, "No health data yet\n(starts after first print cycle)")

    return _svg(fig)


def _row_analysis(factors: dict, durations: dict) -> str:
    fig, (ax_spider, ax_pie) = plt.subplots(1, 2, figsize=(16, 4.6), facecolor=_BG)
    fig.subplots_adjust(wspace=0.14)

    # Spider chart (embedded PNG) + legend table
    _style(ax_spider, "Failure Driver Analysis")
    _draw_spider_panel(ax_spider, fig, factors)

    # Print state pie
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


def _draw_spider_panel(ax, fig, factors: dict) -> None:
    """Embed the radar PNG (if available) and draw an 8-row legend table."""
    try:
        import PIL.Image
        from camera.job_analyzer import _build_radar_png
        radar_factors = factors if factors else {
            k: 0.5 for k in ("material", "platform", "progress", "anomaly",
                              "thermal", "humidity", "stability", "settings")
        }
        png_bytes = _build_radar_png(radar_factors, size=300)
        img = PIL.Image.open(io.BytesIO(png_bytes))
        pos = ax.get_position()
        # Radar occupies left 45% of the axes area
        radar_ax = fig.add_axes([pos.x0, pos.y0, pos.width * 0.42, pos.height])
        radar_ax.imshow(img)
        radar_ax.set_facecolor(_PANEL)
        radar_ax.axis("off")
    except Exception as e:
        log.debug("open_charts: radar embed failed: %s", e)

    # Legend table occupies right portion
    ax.axis("off")
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
    tbl = ax.table(
        cellText=legend_rows,
        colLabels=["Factor", "What it measures"],
        cellLoc="left",
        loc="right",
        bbox=[0.44, 0.0, 0.56, 1.0],
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


def _row_ams(printer_name: str) -> str:
    fig, ax = plt.subplots(1, 1, figsize=(16, 2.6), facecolor=_BG)
    _style(ax, "AMS Filament Remaining")

    spools = []
    try:
        from tools.filament import get_spool_info
        info = get_spool_info(printer_name)
        spools = info.get("spools") or []
    except Exception as e:
        log.debug("open_charts: AMS spool fetch failed: %s", e)

    if not spools:
        _no_data(ax, "No AMS spool data")
        ax.axis("off")
        return _svg(fig)

    labels  = [s.get("display_name") or f"Slot {i}" for i, s in enumerate(spools)]
    values  = [s.get("remaining_percent") or 0 for s in spools]
    colors  = []
    for s in spools:
        hx = (s.get("color") or "#444444").lstrip("#")
        try:
            colors.append((int(hx[0:2], 16) / 255, int(hx[2:4], 16) / 255, int(hx[4:6], 16) / 255))
        except Exception:
            colors.append((0.27, 0.27, 0.27))

    ypos = list(range(len(labels)))
    bars = ax.barh(ypos, values, color=colors, edgecolor=_BORDER, linewidth=0.4, height=0.6)
    ax.set_xlim(0, 110)
    ax.set_yticks(ypos)
    ax.set_yticklabels(labels, fontsize=7)
    ax.set_xlabel("Remaining %", fontsize=7)
    for bar, val in zip(bars, values):
        ax.text(bar.get_width() + 1.5, bar.get_y() + bar.get_height() / 2,
                f"{val:.0f}%", va="center", ha="left", fontsize=6.5, color=_TEXT)

    return _svg(fig)


# ── Main tool ─────────────────────────────────────────────────────────────────

def open_charts(name: str) -> dict:
    """
    Generate and open a responsive telemetry dashboard for the named printer.

    Renders 6 chart sections as inline SVGs assembled into a dark-themed
    responsive HTML page and opens it in the default browser:

      1. Temperature history — nozzle(s), bed, chamber (separate panels)
      2. Fan speeds — all 4 fans as a full-width step chart
      3. Anomaly signals + print health timeline (side by side)
      4. Failure driver spider chart + legend  |  print state pie
      5. Camera calibration corner status
      6. AMS filament remaining bars

    On H2D printers the nozzle panel shows "Left Nozzle" and "Right Nozzle"
    ordered left-to-right as installed (T1 left, T0 right). On single-extruder
    printers it shows a single "Nozzle" series.

    Health timeline and anomaly data are populated by the background print
    monitor and appear after the first active print analysis cycle (~60s into
    a print).

    Returns:
      output_path — path to the generated HTML file (/tmp/bambu-charts-{name}.html)
      opened      — True if the browser was launched successfully
    """
    log.debug("open_charts: called for name=%s", name)

    # Always render the static file so output_path is stable across restarts.
    html = render_charts_html(name)
    if html.startswith("<html><body style"):
        return {"error": "not_connected"}
    out = Path(f"/tmp/bambu-charts-{name}.html")
    out.write_text(html, encoding="utf-8")

    # Prefer the HTTP URL so the browser auto-refreshes live data every 30s;
    # fall back to the static file:// URL if the API server is not running.
    try:
        from tools.system import get_server_info
        info = get_server_info()
        api_port = info.get("api_port", 0)
    except Exception:
        api_port = 0

    if api_port:
        url = f"http://localhost:{api_port}/api/charts?printer={name}"
        opened = webbrowser.open(url)
        log.info("open_charts: opened live URL %s (static: %s) opened=%s", url, out, opened)
    else:
        opened = webbrowser.open(f"file://{out}")
        log.info("open_charts: opened static file %s opened=%s", out, opened)

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
    """Render and return the full dashboard HTML for *name* (used by HTTP route)."""
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

    tool_1_data = series_data.get("tool_1", {}).get("data", [])
    is_h2d = any(p.get("v", 0) != 0 for p in tool_1_data)

    section_titles = [
        "Temperature History", "Fan Speeds", "Anomaly &amp; Health",
        "Failure Analysis", "Camera Calibration", "AMS Filament",
    ]
    svgs = [
        _row_temps(series_data, is_h2d),
        _row_fans(series_data),
        _row_health(health_history),
        _row_analysis(factors, durations),
        _row_calibration(),
        _row_ams(name),
    ]
    return _assemble_html(name, svgs, section_titles)


def render_charts_panels(name: str) -> dict:
    """Render only the SVG panels for AJAX refresh (used by /api/charts_panels route).

    Returns {"panels": [svg, ...], "ts": "<timestamp string>"} or {"error": "..."}.
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

    tool_1_data = series_data.get("tool_1", {}).get("data", [])
    is_h2d = any(p.get("v", 0) != 0 for p in tool_1_data)

    panels = [
        _row_temps(series_data, is_h2d),
        _row_fans(series_data),
        _row_health(health_history),
        _row_analysis(factors, durations),
        _row_calibration(),
        _row_ams(name),
    ]
    return {"panels": panels, "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
