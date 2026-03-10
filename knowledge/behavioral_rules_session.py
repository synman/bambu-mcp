"""
behavioral_rules_session.py — Session start, MCP reload, printer name, and HTTP API write guard rules.

Sub-topic of behavioral_rules. Access via get_knowledge_topic('behavioral_rules/session').
"""

from __future__ import annotations

BEHAVIORAL_RULES_SESSION_TEXT: str = """
# Behavioral Rules — Session Management

---

## Printer Name Verification (Mandatory)

Printer names are not stable across sessions. After any MCP reload, tool
reconnect, or at the start of a new session, treat all printer names as unknown.

Rules:
- Before calling any per-printer tool (get_printer_state, view_stream,
  get_temperatures, etc.), verify the target name exists by calling
  get_configured_printers() if the name has not already been confirmed in the
  current session context.
- Do NOT assume printer names from prior sessions, documentation examples, or
  model names (e.g. "X1E", "H2D") — the registered name is user-chosen and
  may differ from the hardware model.
- After a get_configured_printers() call, use the exact "name" value returned.
  The name is case-sensitive.

Common failure mode: calling a per-printer tool with a guessed name immediately
after an MCP reload, before verifying configured printers. Always resolve names
first.

## Post-Reload Checklist (Mandatory)

After every MCP reload (mcp-reload command, -i done acknowledgment, or any
tool reconnect), execute ALL of the following steps in order before proceeding:

1. Call get_configured_printers() — establish printer names for this session.
2. Check Safari for open stream tabs (AppleScript: URL of t starts with
   "http://localhost:"). If a tab is found:
   a. Call start_stream(name, port=<port from tab URL>) to restart the stream.
   b. Reload the tab: set URL of t to "<stream url>/" (URL reassignment only —
      do JavaScript "location.reload()" is blocked on this machine).
3. If no stream tab is open but get_stream_url() shows streaming: true,
   call view_stream() to open a fresh tab.

These steps are gates — do not skip any of them regardless of how simple the
user's follow-up request appears.

## HTTP API Write Guard (Mandatory)

HTTP API write routes (POST, PATCH, DELETE) require the same explicit user
confirmation gate as MCP tools with `user_permission=True`. Never call a write
route on behalf of the user without first presenting a summary and receiving
explicit go-ahead in the current conversation turn.

**Write vs. read identification:**
- GET routes are read-only — safe to call without confirmation.
- POST routes are action/command operations — always require user confirmation.
- PATCH routes are partial resource updates — always require user confirmation.
- DELETE routes are resource-destruction operations — always require user confirmation.
- The OpenAPI spec (`GET /api/openapi.json`) and the Swagger UI (`GET /api/docs`) reflect
  the correct method for every route. When in doubt, check the spec.

**Write guard applies equally whether you are:**
- Calling the HTTP API directly (e.g. via `send_gcode` wrapper, curl, httpx)
- Constructing a URL and calling it via bash or a script

**Destructive operations (irreversible — extra caution required):**
- `POST /api/stop_printing` — cancels the print; cannot be resumed
- `DELETE /api/delete_sdcard_file` — permanently deletes the file
- `POST /api/print_3mf` — starts a physical print; follow the full print_file confirmation flow
- `POST /api/send_gcode` — bypasses all safety checks; present the exact gcode to the user
- `POST /api/send_mqtt_command` — last-resort raw command; present full JSON to the user
"""
