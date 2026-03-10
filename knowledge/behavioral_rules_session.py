"""
behavioral_rules_session.py — Session start, MCP reload, and printer name rules.

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
"""
