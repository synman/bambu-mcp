"""
behavioral_rules.py — Synthesized mandatory behavioral rules for the bambu-mcp agent.

Sourced from:
  ~/.copilot/copilot-instructions.md            (global rules)
  ~/bambu-printer-app/.github/copilot-instructions.md
  ~/bambu-printer-manager/.github/copilot-instructions.md
  ~/bambu-mcp/.github/copilot-instructions.md

Sensitive values (hostnames, IPs, serials, credentials, Docker/Watchtower/CI
infrastructure details) are intentionally omitted.
"""

BEHAVIORAL_RULES_TEXT: str = """
# Mandatory Behavioral Rules — bambu-mcp Agent

---

## ⚠️ Rules Mandatory Rule

Approach every query as if arriving with no prior context — fresh eyes, no cached
assumptions, no reliance on prior turns. This is the fundamental operating posture
that ensures the agent is effective and the MCP server is being used correctly.

On every request, consult all knowledge/ modules and bambu://rules/* resources.
Both the global rules file and any repo-specific rules file MUST be read and applied
together before any tool call related to the task. Prior-session memory of rules does
NOT satisfy this requirement.

---

## ⚠️ Always Start with bambu-mcp — Query First, Never Infer

For any printer-related query, always use bambu-mcp tools first. Never infer current
printer state from prior messages, user descriptions, or assumptions. Query the printer
directly through the tools, then respond.

Knowledge/research escalation order (never skip tiers):
  Tier 1 — baked-in knowledge modules (fastest, offline) — always exhausted first
  Tier 2 — authoritative repos (BambuStudio, ha-bambulab, OpenBambuAPI)
  Tier 3 — broad web/GitHub search (last resort only)

Docstrings are the primary interface contract for both the AI consumer and the human
consumer operating behind it. A tool with an incomplete or inaccurate docstring breaks
the chain — the AI cannot use the tool correctly, and the human cannot trust the output.
Keeping docstrings accurate and complete is a first-class maintenance requirement.

---

## ⚠️ When in Doubt, Ask

If anything is unclear — intent, scope, a required value, an expected behavior — stop
and ask the user before proceeding. A wrong assumption costs more than a one-line
clarification. Do not infer, guess, or proceed on ambiguous footing.

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

## Camera Usage Rules

For camera tool selection, stream HUD overlay components, data_uri handling, human
viewability rules, and snapshot vs. stream guidance:
→ `get_knowledge_topic('behavioral_rules/camera')`

---

## Knowledge Sub-Topics

This module contains the ⚠️ safety rules and session rules that apply to every request.
For additional domain-specific content, fetch the relevant sub-topic:

**`behavioral_rules/camera`** — Camera tool selection, stream HUD overlay components,
data_uri handling, human viewability rules, snapshot vs. stream guidance.
→ Call: `get_knowledge_topic('behavioral_rules/camera')`

**`behavioral_rules/print_state`** — Printer state interpretation: gcode_state FAILED
semantics, HMS error active/historical distinction, two-command clear protocol, stage
codes, session startup when no printers configured, session interface rule.
→ Call: `get_knowledge_topic('behavioral_rules/print_state')`

**`behavioral_rules/methodology`** — KISS, quality-first, root cause fix, telemetry
mapping parity, verification-first, cross-model compatibility, response endings,
rules maintenance, security & privacy.
→ Call: `get_knowledge_topic('behavioral_rules/methodology')`

**`behavioral_rules/mcp_patterns`** — MCP array parameter pattern, multi-level call
hierarchy (project files, telemetry, SD card), image quality tiers, compressed response
protocol and MAX_MCP_OUTPUT_TOKENS configuration.
→ Call: `get_knowledge_topic('behavioral_rules/mcp_patterns')`

**`behavioral_rules/alerts`** — Push alert types emitted on high-visibility printer state
transitions (job start/finish/fail, HMS errors, health verdict changes). Alert schema,
severity levels, payload fields, and recommended agent actions per alert type.
→ Call: `get_knowledge_topic('behavioral_rules/alerts')`

**`behavioral_rules/session`** — Session management: printer name verification after MCP
reload (always call get_configured_printers() first), Post-Reload Checklist (printers +
stream tab refresh), and HTTP API Write Guard (GET=safe, POST/PATCH/DELETE=require user confirmation).
→ Call: `get_knowledge_topic('behavioral_rules/session')`

---

## Active Print Workflow

During an active print, a background health monitor daemon runs automatically,
capturing camera frames every ~60 seconds and computing spaghetti/anomaly scores,
print health verdicts, and temperature trends. **The monitor runs without any agent
action** — no polling loop or tool call is needed to keep it alive.

### Proactive monitoring (required — do not wait for the user to ask)

When a `job_started` alert fires, or when the user asks about an active print:
1. Call `analyze_active_job(name)` — default `categories=["X"]` returns the composite
   diagnostic view (~25 KB). Describe the verdict, score, and any anomaly regions.
2. If health is `WARNING` or `CRITICAL`, add `categories=["X","D"]` to include anomaly
   detection overlays for more detail.
3. Use `open_job_state(name)` to open the latest cached result for the human to view
   directly — never embed the raw data URI in chat output.

**First background result is available ~60 seconds after print start.**

### Post-job project info

After a print completes (gcode_state `FINISH` or `FAILED`), `get_current_job_project_info`
still returns the project metadata for the last job — use it with `open_plate_viewer`
to show the user which plate ran. Pass the plate number from `get_job_info()` as
`target_plate` to scroll directly to the printed plate:

```python
job  = get_job_info(name)
info = get_current_job_project_info(name)
open_plate_viewer(name, info["gcode_file"], target_plate=job["plate_num"])
```

### Agent efficiency shortcuts

- **Plate number from path:** parse `gcode_file` — `/data/Metadata/plate_N.gcode` → N.
  No extra tool call needed.
- **Find project file on SD card:** `get_3mf_entry_by_name(name, subtask_name + ".gcode.3mf")`
  searches the cached file tree by filename — faster than `list_sdcard_files()`.

---

## HTTP REST API

When an MCP tool cannot fulfill a user request, bambu-mcp also exposes a complete
REST HTTP API on a dynamically assigned ephemeral port (IANA RFC 6335 range 49152–65535,
default pool 49152–49251) with 51 routes covering all printer operations. Call
`get_server_info()` to discover the actual port before making HTTP requests.

→ Call: `get_knowledge_topic('http_api')` for base URL, auth, and route category index.
→ Then call a route sub-topic (e.g. `get_knowledge_topic('http_api/print')`) for details.

HTTP API write routes are marked ⚠️ and require POST/DELETE method. See
`behavioral_rules/session` for the HTTP API Write Guard documentation.
"""
