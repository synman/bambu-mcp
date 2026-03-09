"""
behavioral_rules_camera.py — Camera usage rules sub-topic for bambu-mcp agents.

Sub-topic of behavioral_rules. Access via get_knowledge_topic('behavioral_rules/camera').
"""

from __future__ import annotations

BEHAVIORAL_RULES_CAMERA_TEXT: str = """
# Camera Usage Rules — bambu-mcp

---

## When to use camera tools

Offer camera tools proactively when the user asks about:
- Print progress, current layer, or "what's happening on the printer" — a snapshot is
  more informative than any text status summary and takes only seconds
- Print quality, layer adhesion, warping, or visual inspection ("does it look okay?",
  "is the print stuck?", "describe what you see")
- Human wants to visually watch: "show me", "can I see", "let me watch", "open the camera"

---

## Choosing the right camera tool

The key question is **who is consuming the image — the AI or the human?**

- **Human wants to see the camera** ("show me", "open the camera", "let me see what it's
  doing", "stream it", "let me watch"):
  Use `view_stream(name)`. It starts the MJPEG server AND opens the browser in one step.
  This is the correct path whenever the human is the viewer. Do NOT use `get_snapshot()`
  and return a data_uri — the human cannot see a raw base64 blob in a chat context.
  Do NOT use `start_stream()` followed by manually telling the user the URL — `view_stream`
  is the simpler, preferred path.

- **AI is analyzing/describing the camera view on the human's behalf** ("what does it look
  like?", "is the print stuck?", "describe what you see", "is it okay?"):
  Use `get_snapshot(name)`. The AI consumes the image data directly. Fast, no background
  server left running.

- **Full active job state report** ("check for spaghetti", "analyze the print", "is the
  print healthy?", "job state", "print health") — **AI is the consumer**:
  Use `analyze_active_job(name)`. The AI receives data_uri assets to analyze and describe.
  See `get_knowledge_topic('behavioral_rules/job_analysis')` for full guidance.

- **Human wants to see the diagnostic images** ("show me the anomaly detection", "open the
  print health view", "show me what the AI sees", "show me the composite"):
  Use `open_job_state(name)`. Reads the latest background monitor result and opens all
  diagnostic images (composite, annotated, health panel, raw frame) in the system viewer.
  This is the human-facing counterpart to `analyze_active_job()`.

- **Live stream — programmatic** (user wants to embed the URL, use it in automation, etc.):
  Use `start_stream(name)` to get the URL, then provide it to the user.

- **Check stream state without connecting**:
  Use `get_stream_url(name)` — returns URLs and streaming status without touching the camera.

---

## analyze_active_job — quick reference

For full documentation (result interpretation, print_health/decision_confidence thresholds,
categories parameter, field semantics), call:
  `get_knowledge_topic('behavioral_rules/job_analysis')`

---

## Stream HUD overlay — what the user sees in the browser

The MJPEG page served by `start_stream` / `view_stream` includes a live overlay with
the following named components (polls `/status` every 2 s):

**Top-left HUD panel** (dark semi-transparent):
- **Badge row**: state badge (IDLE / RUNNING / PAUSE / FINISH / FAILED, color-coded by
  state) + **speed badge** (Quiet / Standard / Sport / Ludicrous — shown only while active)
- **Subtask line**: job/file name, truncated with ellipsis
- **Progress bar**: thin 3-px bar, color tracks print state
- **Rows** (label + value): Stage, Layers (current / total), Elapsed, Remaining
- **Temps section**: nozzle temp(s) (°C / target), bed temp, chamber temp
- **Fans section**: part cooling %, aux %, exhaust %
- **Filament swatch**: colored dot + filament type label for the active AMS spool
- **AMS humidity index**: numeric humidity reading from the active AMS unit
- **Wi-Fi signal bars**: unicode block-character bar graph, color-tiered by signal strength
- **HMS error links**: clickable error entries; clicking opens the Bambu error page in a popup

**Top-right FPS counter** (separate from HUD panel):
- Numeric FPS readout + 5-column animated bar graph (green ≥80 % cap / amber ≥40 % / red)

**Bottom image panels** (appear only when a print job is active):
- **Thumbnail panel** (bottom-left): isometric 3D render of the current job's plate
- **Layout panel** (bottom-right): annotated top-down plate layout image with bounding boxes

Use this vocabulary when describing what the user sees or when explaining stream features.

---

## Cleanup

- Call `stop_stream(name)` when the user is done watching or the conversation is ending.
- Do NOT leave streams running indefinitely — each stream holds an active TCP/TLS
  connection to the printer and occupies a local port.

---

## Camera availability

- Never assume a printer has a camera. Always call a camera tool — it will return
  `{"error": "no_camera"}` if the model has no camera.
- If no camera is available, say so clearly and suggest text-based alternatives:
  get_print_progress(), get_job_info(), get_hms_errors() for status information.

---

## data_uri handling

- `get_snapshot()` returns a `data_uri` field that is a complete, self-contained JPEG
  image encoded as a base64 data URI. Use it when the AI needs to analyze, describe, or
  pass to a vision model. Do NOT return the raw data_uri to the human — they cannot view
  it in a chat or terminal context.
- If the human wants to view the camera, always call `view_stream()` instead.
- It can be passed directly to an AI vision model for analysis.

---

## Human viewability — images and plate assets

**The rule**: Whenever a human user wants to *view* a digital asset (camera snapshot,
plate thumbnail, plate top-down view, plate layout), use the browser-opening tool that
makes it actually visible. Returning raw base64 `data_uri` output to a human is never
the right choice.

**Who is the consumer determines which tool to call:**

| Human intent | Correct tool |
|---|---|
| "show me the camera", "let me watch", "open the stream" | `view_stream()` |
| "show me the print health", "show me what the AI sees", "open the composite" | `open_job_state()` |
| "show me the plate", "open the project viewer" | `open_plate_viewer()` — opens a browser with **all plates** (isometric 3D + top-down per plate) |
| "show me just plate N", "annotated layout for plate N" | `open_plate_layout(plate_num=N)` — opens a browser with the annotated top-down view for **one plate** |
| "is there a 3D view?", "show me the isometric render" | `open_plate_viewer()` — the isometric thumbnails are in the all-plates viewer |
| "what does it look like?", "describe it", "is anything wrong?" | `get_snapshot()`, `analyze_active_job()` — AI analyzes |
| "what's on the plate?", "describe the thumbnail" | `get_plate_thumbnail()`, `get_plate_topview()` — AI analyzes |

The distinction:
- **Human is the viewer** → browser-opening tool (`view_stream`, `open_job_state`,
  `open_plate_viewer`, `open_plate_layout`)
- **AI is the consumer** (to describe, analyze, compare, or pass to a vision model) →
  raw-data tool (`get_snapshot`, `analyze_active_job`, `get_plate_thumbnail`, `get_plate_topview`)

Returning a data_uri to the human in a chat or terminal context is never the right choice.
Embedding it in Markdown (`![img](data:...)`) is also wrong — rely on the browser-opening
tools for human viewability.
"""
