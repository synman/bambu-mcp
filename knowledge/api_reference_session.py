"""
api_reference_session.py — BambuPrinter session management sub-topic.

Sub-topic of api_reference. Access via get_knowledge_topic('api_reference/session').
"""

from __future__ import annotations

API_REFERENCE_SESSION_TEXT: str = """
# BambuPrinter API — Session Management & Commands

All signatures sourced from bambuprinter.py. Do not invent parameter names or defaults.
Located at: bambu-printer-manager/src/bpm/bambuprinter.py

---

## BambuPrinter Class

Central class for all printer interaction in bambu-mcp. All printer operations in the
MCP layer route through a BambuPrinter instance obtained via session_manager. Never
instantiate BambuPrinter directly unless sending a command with no MCP tool equivalent.

### Constructor

```python
BambuPrinter(config: BambuConfig | None = None)
```
Sets up internal storage and bootstraps logging. If config=None, a default
BambuConfig("", "", "") is created. BambuConfig carries all connection parameters —
see `get_knowledge_topic('api_reference/state')` for BambuConfig field details.

---

## Session Management Methods

#### start_session() -> None
Initiates an SSL MQTT connection to the printer. Starts the watchdog thread.
Subscribes to `device/{serial}/report` on connect. Must be called before any
commands or data collection. Raises if hostname/access_code/serial_number missing
or if session already active.

#### pause_session() -> None
Unsubscribes from the /report topic, disabling telemetry updates. Sets
ServiceState.PAUSED.

#### resume_session() -> None
Re-subscribes to /report topic. Sets ServiceState.CONNECTED.
Sets ServiceState.QUIT if client not connected or not in PAUSED state.

#### quit() -> None
Disconnects MQTT client, sets ServiceState.QUIT, notifies update callback,
joins all threads (mqtt_client_thread, watchdog_thread).

#### refresh() -> None
Publishes ANNOUNCE_VERSION and ANNOUNCE_PUSH to trigger a full state refresh.
Only acts if ServiceState.CONNECTED.

---

## Raw Command Methods

These bypass all MCP-layer safety checks and require explicit user permission.

#### send_gcode(gcode: str) -> None
Submits G-code commands directly to the printer. Multiple commands separated by \\n.
Format: `f"M104 S{value}\\n"`. Publishes SEND_GCODE_TEMPLATE.
⚠️ REQUIRES explicit user permission — Printer Write Protection applies.

#### send_anything(anything: str) -> None
Publishes an arbitrary valid JSON string to `device/{serial}/request`.
Parses then re-serialises before publishing (must be valid JSON).
⚠️ REQUIRES explicit user permission — Printer Write Protection applies.
"""
