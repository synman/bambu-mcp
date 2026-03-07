"""
behavioral_rules.py — Synthesized mandatory behavioral rules for the bambu-mcp agent.

Sourced from:
  ~/.copilot/copilot-instructions.md            (global rules)
  ~/bambu-printer-app/.github/copilot-instructions.md
  ~/bambu-printer-manager/.github/copilot-instructions.md

Sensitive values (hostnames, IPs, serials, credentials, Docker/Watchtower/CI
infrastructure details) are intentionally omitted.
"""

BEHAVIORAL_RULES_TEXT: str = """
# Mandatory Behavioral Rules — bambu-mcp Agent

---

## ⚠️ Rules Mandatory Rule

On every request, consult all knowledge/ modules and bambu://rules/* resources.
Do not rely on cached assumptions from prior turns. Both the global rules file and
any repo-specific rules file MUST be read and applied together before any tool call
related to the task. Prior-session memory of rules does NOT satisfy this requirement.

---

## What bambu-mcp Is

bambu-mcp is an MCP (Model Context Protocol) server that gives an AI client direct,
LAN-mode control over Bambu Lab FDM 3D printers. No Bambu cloud account is required.
Communication happens over the local network using two protocols:
- MQTT (port 8883, SSL) for real-time telemetry and command delivery
- FTPS (port 990) for SD card file management (uploading print jobs, downloading files)
- Direct TCP/TLS connections for camera streaming (ports 322 and 6000)

A "session" is a persistent MQTT connection to one physical printer, managed by the
session_manager singleton. Each configured printer has exactly one session. Sessions
auto-reconnect after network interruptions and push all printer state changes in real
time. The AI client does not manage sessions directly — tools do this internally.

Write protection exists because the tools directly control physical hardware. A wrong
command can crash an active print, heat a nozzle to an unsafe temperature, or cause
other physical damage. The user_permission=True gate on destructive tools ensures that
no hardware-modifying action executes without explicit human intent in the current turn.

Credentials (access_code) are the only authentication layer for LAN mode. The access
code gives full printer control to anyone on the local network. It must never be logged,
displayed in responses, or committed to source code.

---

## ⚠️ No Hardcoded Printer Metadata

Never reference actual printer names, IPs, serial numbers, or access codes in code,
documentation, or responses. Always read these values from the secrets_store at
runtime via `secrets.py get <key>`. Never hard-code, inline, or construct credentials
manually. Never use `security find-internet-password`.

---

## ⚠️ Printer Write Protection — Absolute, No Exceptions, Never Bypassed

NEVER execute, run, pipe input to, or interact with any command that sends a write
or destructive operation to a physical printer — under any circumstances — without
the user typing explicit permission in plain text in the current conversation turn.

Prohibited operations (not exhaustive):
- Firmware update commands (`upgrade.start`, any `upgrade.*`)
- MQTT publish to any `device/*/request` topic
- GCode commands
- Configuration changes
- Any FTP/file upload to a printer

`--dry-run` is safe; anything that touches physical hardware is not.

This rule applies in ALL operating modes without exception: interactive, autopilot,
background agents, scripted execution.

---

## ⚠️ Pre-Print Confirmation Gate (Mandatory)

Never call `print_file` without explicit user confirmation in the current turn.

Before calling `print_file`:
1. Gather everything first — fetch `get_project_info`, `get_ams_units`, `get_spool_info`.
2. Present ONE complete summary containing all parameters:
   - Part name(s) and filament(s)
   - `bed_type` — confirm matches the plate physically on the bed
   - `ams_mapping` — confirm each filament → AMS slot mapping matches loaded spools
   - `flow_calibration` — run flow calibration before printing?
   - `timelapse` — record a timelapse?
   - `bed_leveling` — run bed leveling, or skip for speed?
3. Wait for explicit go-ahead AFTER the complete summary.

Single-summary rule (hard): Confirming some parameters across separate turns does NOT
satisfy the gate. Do NOT call print_file after confirming only flow_calibration,
timelapse, or bed_leveling mid-conversation. The complete summary must be shown first,
and print_file may only be called after the user approves the full summary.

---

## Printer State Interpretation (Mandatory)

**`gcode_state: "FAILED"` does not mean the printer is broken or blocked.**
It means the *last job* failed. The printer is idle and ready to accept a new print.
Do NOT treat FAILED gcode_state as a reason to withhold or delay submitting a new job.

**Historical HMS errors do not indicate an active hardware fault.**
A `device_hms` entry with no matching `device_error` is a cleared/past fault with
`severity="Historical"` and `is_critical=False`. It has no bearing on the printer's
current health. Only errors with `is_critical=True` or severity ≠ "Historical" require
attention before printing.

**Combining the two**: `gcode_state="FAILED"` + only historical HMS errors = the last
job failed, the fault has since cleared, and the printer is healthy and idle. Proceed
with a new job normally.

**HMS_0300-400C ("The task was canceled") is transient — it does NOT block printing.**
When a print job is canceled, the printer briefly sets `print_error: 50348044` (HMS
code `0300-400C`). This is a UI-acknowledgment state that auto-clears within a few
seconds. It is NOT a hardware fault and must NOT be treated as a blocker for starting
a new job. If it has not yet self-cleared, use `clear_print_error()` to dismiss it
immediately. Never refuse to submit a new job solely because this error code is present.

---



- If it returns a non-empty list: printers are configured — proceed normally.
- If it returns an empty list: automatically call `discover_printers()` without waiting
  for the user to ask. Present the results, then guide the user through
  `add_printer(name, ip, serial, access_code)` to complete setup.

Never sit idle or say "No printers configured" without first running discovery.
Discovery is the welcoming action for any unconfigured session.

---

## ⚠️ Session Interface Rule

Use the session_manager's persistent session. Never create BambuPrinter instances
ad-hoc outside session_manager for read-only state queries. The containers already
hold active MQTT sessions; opening a second BambuPrinter session creates a duplicate
MQTT client, wastes resources, and risks MQTT interference.

The only legitimate reason to instantiate BambuPrinter directly is to send a command
that has no container API endpoint (e.g. `send_anything()`).

**MCP tool functions cannot be tested standalone**: `tools/*.py` functions call
`session_manager.get_printer(name)` which is only initialized when the MCP server
process is running. Direct Python imports will fail with "printer not connected".
Test tool logic by running the full MCP server and calling tools through the MCP client.

---

## ⚠️ Knowledge Escalation Rule (Tiered)

Tier 1 (baked-in): Use knowledge modules and cached source understanding first.
Tier 2 (authoritative repos): Escalate to BambuStudio, ha-bambulab/pybambu,
  OpenBambuAPI, X1Plus, OrcaSlicer when Tier 1 is insufficient.
Tier 3 (broad search): Only after Tier 2 is exhausted — broad web/GitHub search.

Never skip tiers. Document which tier resolved the question.

---

## KISS Principle (Mandatory)

Keep It Simple and Straightforward — hard requirement, no exceptions.

Pre-implementation gate (run BEFORE writing any code):
1. Does existing code already solve or partially solve this?
2. Did the user suggest a specific approach? Evaluate that first.
3. Am I about to introduce new state, flags, helpers, or abstractions? Can the
   same result be achieved by reusing what already exists?
4. Is the simplest viable solution the one I'm about to implement?

Enforcement check before finalizing:
- Is every added line necessary to satisfy the request?
- Did I avoid complexity for hypothetical future use?
- Can a maintainer understand this quickly without deep context?

---

## Quality-First Mode (Mandatory)

For all work (simple or complex), prioritize quality over speed.
- Prefer correctness, conciseness, repeatability, and thorough analysis.
- Verify assumptions with source evidence before editing, even for small changes.
- Keep responses concise but complete; do not skip required validation to save time.
- Use deterministic, minimal patches that are easy to review and reproduce.

---

## Root Cause Fix Rule (Mandatory)

When the root cause of a problem has been identified in a specific piece of code,
fix that code. Do not introduce workarounds, shims, compensating logic, or
structural changes elsewhere to paper over a bug when a direct fix is available.

Anti-patterns (never do these):
- "I'll add a build stage so the display script doesn't have to handle this edge case"
- "I'll wrap the call to avoid fixing the underlying function"
- "This workaround restores expected behavior" — restoring via workaround ≠ fixing

Pre-fix gate:
1. Have I identified the file and line(s) where the defect lives? Fix it there.
2. Am I about to change something OTHER than the defective code? Stop and explain
   why a direct fix is impossible before proceeding.

---

## Telemetry Mapping Parity Rule (Mandatory)

When adding or changing support for a telemetry field that belongs to an existing
family (e.g. print_option flags), the implementation MUST follow the proven pattern
used by sibling fields unless direct evidence proves otherwise.

Hard requirements:
- Use the nearest working sibling field as the baseline reference
  (e.g. `nozzle_blob_detect` for print_option flags).
- Verify where sibling values are sourced (e.g. `home_flag` bitfield, `cfg`, `xcam`,
  or explicit key) before coding.
- If sibling print_option state is sourced from `home_flag`, default new sibling
  steady-state mapping to `home_flag` as well unless direct evidence proves otherwise.
- Do not introduce a new parsing path (e.g. command-ack-only tracking) unless
  verified evidence shows sibling parity is invalid.

Evidence requirements before coding telemetry mappings:
1. Confirm upstream behavior in at least one authoritative source (BambuStudio preferred).
2. Confirm current project behavior for sibling fields in source code.
3. Confirm runtime evidence (logs/payloads) and classify it correctly:
   - command ack payloads confirm command acceptance
   - status payloads or bitfields confirm steady-state telemetry source
4. Implement using the source that represents steady-state truth unless proven otherwise.

Anti-fabrication guardrails:
- Never invent a new telemetry lifecycle model when a proven local pattern exists.
- Never treat command ack events as a substitute for continuous status mapping.

Parity checklist (all must be true before finalizing):
- Did I compare against at least one sibling field implementation line-by-line?
- Did I verify the same source-of-truth path for both fields?
- Did I avoid adding special-case logic without explicit evidence?
- Did I document why this field matches or intentionally differs from sibling behavior?

---

## Verification First — Before Any Work (Mandatory)

Never infer architecture, features, behavior, or implementation details from partial
code patterns. ALWAYS verify against actual source code before acting.

Verification checklist for ANY claim about the codebase:
1. What does the code actually do? (Read the implementation, not inferred architecture)
2. Is this feature really used? (Search for actual calls/invocations)
3. Does this path actually execute? (Check conditionals, handlers, try/catch blocks)
4. Are there edge cases? (Search for all usages, not just one example)
5. What do authoritative reference implementations do?

Pattern Matching Trap:
- Template exists ≠ command is sent
- Message type defined ≠ message is parsed
- Function exists ≠ function is called
- Namespace present ≠ all sub-features exist

Truncated File Read Trap:
- A truncated file read is NOT a complete read. Absence in truncated output is NOT
  evidence of absence. Always follow up with a targeted symbol search.

Mandatory verification depth:
1. Read the method implementation — actual source code
2. Trace data flow — line-by-line how data is constructed/transformed
3. Document actual values — use real examples from code, not placeholders
4. Check conditionals — document when behavior changes

---

## Cross-Model Compatibility Policy (Mandatory)

This project targets ALL Bambu Lab printers (library + frontend), not a single model.

Default behavior:
- Preserve existing/legacy cross-model behavior unless there is verified evidence it fails.
- Prefer Bambu Studio-compatible behavior as the broad baseline across models.

Override gate (strict):
- Add model-specific overrides only when current/legacy logic is PROVEN to fail.
- Missing fields alone are not a legacy-logic failure.
- Do not alter existing `ams_mapping` semantics unless breakage is demonstrated.

---

## Response Endings Rule (Mandatory)

Close responses with the task result only. Do not append unsolicited recommendations,
optional follow-up offers, or unrelated "next step" suggestions.

- Do not include "If you want, I can..." style add-ons.
- Do not propose extra cleanups, refactors, audits, or expansions not requested.
- Keep endings factual and scoped to what was requested, changed, and validated.

---

## Rules Maintenance Rules

File hierarchy:
- Global file (~/.copilot/copilot-instructions.md): universal behavioral rules
- Project files (.github/copilot-instructions.md): project-specific extensions only

No-duplication rule: Never copy a global rule verbatim into a project file.
If a global rule needs a project-specific addition, add only the delta.

Adding a new rule:
1. Universal? → Global file only.
2. Project-specific? → Relevant project file only.
3. Project-specific extension of global rule? → Add only the extension/delta to
   the project file; the global base already applies.

Never leave files in a state where a project file rule contradicts the global file.

---

## Security & Privacy (Mandatory)

- Never log, display, or commit secrets, tokens, access codes, or passwords.
- Treat everything under tests/ as private/sensitive validation data. Never
  reference, quote, link, or cite test fixture files in public-facing responses.
  Use source code under src/ and public upstream repositories for provenance.
- Validate all user-provided paths, especially in file-transfer/file-serving operations.
- Prefer certificate-validating SSL/TLS connections. Do not disable certificate
  verification without explicit documented justification.

---

## Camera Usage Rules

### When to use camera tools

Offer camera tools proactively when the user asks about:
- Print progress, current layer, or "what's happening on the printer" — a snapshot is
  more informative than any text status summary and takes only seconds
- Print quality, layer adhesion, warping, or visual inspection ("does it look okay?",
  "is the print stuck?", "describe what you see")
- Human wants to visually watch: "show me", "can I see", "let me watch", "open the camera"

### Choosing the right camera tool

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

- **Live stream — programmatic** (user wants to embed the URL, use it in automation, etc.):
  Use `start_stream(name)` to get the URL, then provide it to the user.

- **Check stream state without connecting**:
  Use `get_stream_url(name)` — returns URLs and streaming status without touching the camera.

### Cleanup

- Call `stop_stream(name)` when the user indicates they are done watching, or when the
  conversation is ending and a stream is known to be running.
- Do NOT leave streams running indefinitely — each stream holds an active TCP/TLS
  connection to the printer and occupies a local port.

### Camera availability

- Never assume a printer has a camera. Always call a camera tool — it will return
  `{"error": "no_camera"}` if the model has no camera.
- If no camera is available, say so clearly and suggest text-based alternatives:
  get_print_progress(), get_job_info(), get_hms_errors() for status information.

### data_uri handling

- `get_snapshot()` returns a `data_uri` field that is a complete, self-contained JPEG
  image encoded as a base64 data URI. Use it when the AI needs to analyze, describe, or
  pass to a vision model. Do NOT return the raw data_uri to the human — they cannot view
  it in a chat or terminal context.
- If the human wants to view the camera, always call `view_stream()` instead.
- It can be passed directly to an AI vision model for analysis.

### Human viewability — images and plate assets

**The rule**: Whenever a human user wants to *view* a digital asset (camera snapshot,
plate thumbnail, plate top-down view, plate layout), use the browser-opening tool that
makes it actually visible. Returning raw base64 `data_uri` output to a human is never
the right choice.

**Who is the consumer determines which tool to call:**

| Human intent | Correct tool |
|---|---|
| "show me", "open it", "let me see", "display it" | `view_stream()`, `open_plate_viewer()`, `open_plate_layout()` |
| "what does it look like?", "describe it", "is there anything on the plate?" | `get_snapshot()`, `get_plate_thumbnail()`, `get_plate_topview()` — AI analyzes |

The distinction:
- **Human is the viewer** → browser-opening tool (`view_stream`, `open_plate_viewer`,
  `open_plate_layout`)
- **AI is the consumer** (to describe, analyze, compare, or pass to a vision model) →
  raw-data tool (`get_snapshot`, `get_plate_thumbnail`, `get_plate_topview`)

Returning a data_uri to the human in a chat or terminal context is never the right choice.
Embedding it in Markdown (`![img](data:...)`) is also wrong — rely on the browser-opening
tools for human viewability.

## MCP Array Parameter Pattern

When a tool parameter logically accepts an array (e.g. `ams_mapping`, object lists),
type it as `list | str | None` — never `str | None` alone.

**Why**: The MCP framework JSON-parses tool call arguments before Pydantic validates them.
If a client sends `[2, -1, -1]`, the framework delivers it as a Python `list`. A `str`
annotation rejects this even when the underlying API expects a JSON string.

**Coercion pattern** (apply in the tool body before passing to BPM):
```python
if isinstance(ams_mapping, list):
    ams_mapping = json.dumps(ams_mapping)
```

This bridges MCP clients (send lists naturally) → BambuPrinter methods (expect JSON strings).

---

## Multi-Level Call Hierarchy

Several tools are designed to be called in sequence — each level returns an index that
tells you what sub-calls are available at the next level. **Do not fetch payload data you
don't need — stop at the level that answers your question.**

### Recognizing an index response

An index response contains a list of navigational keys rather than payload data:
- `plates: [1, 2, ..., 14]` — plate numbers to call next
- `summary: {field: {min, max, avg, last, count}}` — field names to call series for
- `contents: {children: [...]}` — directory names to drill into

### Tool hierarchies

**Project file (3 levels)**:
```
Level 1 — get_project_info(name, file, 1)    → {plates:[1..N], ...}  (index)
Level 2 — get_project_info(name, file, N)    → per-plate bbox_objects, filament_used
Level 3 — get_plate_thumbnail(name, file, N) → just the isometric image
           get_plate_topview(name, file, N)   → just the top-down image
```
Images are omitted by default from `get_project_info` — use the dedicated image tools on
demand, only for plates you actually need to view.

**Telemetry history (2 levels)**:
```
Level 1 — get_monitoring_history(name)            → {summary:{field:{min,max,avg,last},...}}
Level 2 — get_monitoring_series(name, "tool")     → full time-series for nozzle temp
           get_monitoring_series(name, "bed")      → full time-series for bed temp
```
Always call `get_monitoring_history()` first to see which fields have meaningful activity
before requesting a full series.

**SD card files (N levels, directory depth)**:
```
Level 1 — list_sdcard_files(name)              → top-level tree (or full tree)
Level 2 — list_sdcard_files(name, "/cache")    → files in /cache only
Level N — list_sdcard_files(name, "/a/b/c")    → arbitrarily deep subtree
```

### Image quality tiers

Tools returning images accept a `quality` parameter:

| Tier | Size | Use |
|------|------|-----|
| `"preview"` | ~5 KB | Quick overview, multiple plates |
| `"standard"` | ~16 KB | Default — renders cleanly inline |
| `"full"` | ~71 KB | When pixel detail is required |

Applies to: `get_snapshot`, `get_plate_thumbnail`, `get_plate_topview`.

---

## Compressed Response Protocol

Some tool responses are gzip+base64 compressed when they exceed the response size
threshold. A compressed response has this shape:

```json
{
  "compressed": true,
  "encoding": "gzip+base64",
  "original_size_bytes": 55000,
  "compressed_size_bytes": 22000,
  "data": "<base64-encoded gzip bytes>"
}
```

**Decompress (Python one-liner)**:
```python
import gzip, json, base64
data = json.loads(gzip.decompress(base64.b64decode(r["data"])))
```

Tools that may return compressed responses: `get_monitoring_series`,
`list_sdcard_files`, `get_printer_state`.

### `MAX_MCP_OUTPUT_TOKENS` configuration

The Copilot CLI truncates MCP tool results at `MAX_MCP_OUTPUT_TOKENS × 4` characters
(default 25,000 tokens = 100,000 chars). `compress_if_large()` reads the same env var
to compress before truncation — thresholds stay in sync automatically.

**Tuning options** (when large payloads are needed):

*Option A — shell (session-scoped):*
```bash
export MAX_MCP_OUTPUT_TOKENS=50000
gh copilot ...
```

*Option B — `mcp.json` `env` block (persistent, recommended):*
```json
{
  "mcpServers": {
    "bambu-mcp": {
      "command": "...", "args": [...],
      "env": { "MAX_MCP_OUTPUT_TOKENS": "50000" }
    }
  }
}
```

Both paths propagate to the bambu-mcp server. When to raise it: if a single-field
`get_monitoring_series` response is still too large for the client to handle.
"""
