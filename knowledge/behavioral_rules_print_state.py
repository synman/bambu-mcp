"""
behavioral_rules_print_state.py — Printer state interpretation, gcode_state, stage codes.

Sub-topic of behavioral_rules. Access via get_knowledge_topic('behavioral_rules/print_state').
"""

from __future__ import annotations

BEHAVIORAL_RULES_PRINT_STATE_TEXT: str = """
# Behavioral Rules — Printer State Interpretation

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
code `0300-400C`). This is a UI-acknowledgment state that auto-clears within seconds.
It is NOT a hardware fault and must NOT be treated as a blocker for starting a new job.
If it has not yet self-cleared, use `clear_print_error()` to dismiss it immediately.
Never refuse to submit a new job solely because this error code is present.

**Two-command clear protocol (mandatory for reliable error dismissal).**
BambuStudio sends TWO commands when dismissing an error dialog:
  1. `clean_print_error` — clears the `print_error` integer value.
  2. `uiop` (system command, action "close") — signals "UI dialog acknowledged."
Without the `uiop` signal the printer stays in a UI-acknowledgment pending state.
Always use `clear_print_error()` — never send `clean_print_error` alone via
`send_mqtt_command`.

`gcode_state: "FAILED"` after cancellation is a terminal job state, NOT a fault. It
persists until a new print starts and does NOT block printing. Healthy post-cancel state
is `gcode_state: "FAILED"` + `print_error: 0`.

---

## gcode_state Quick Reference

| Value | Meaning | Ready to print? | Idle nozzle timeout? |
|---|---|---|---|
| IDLE | No active job | Yes | **Yes** — firmware resets nozzle target to 38°C after timeout |
| PREPARE | Pre-print setup | No | No |
| RUNNING | Actively printing | No | No |
| PAUSE | Paused (user or sensor) | No (resume or stop first) | Likely NO — firmware keeps nozzle hot for resume |
| FINISH | Completed successfully | Yes | Likely yes — same quiescent condition as IDLE |
| FAILED | Last job failed | Yes — do NOT withhold new job | Likely yes — same quiescent condition as IDLE |
| SLICING | On-device slicing | No | No |
| INIT | Initializing | No | No |

**Idle nozzle timeout:** In `IDLE`, `FINISH`, and `FAILED` states, the H2D firmware silently
resets any elevated nozzle target to **38°C** after a calibrated timeout. Calibration scripts
that heat nozzles in these states must use `heat_and_wait()` (dual-layer keepalive using Tier 1
`PATCH /api/set_tool_target_temp`) — never raw `M104` via `send_gcode`.

## Idle Nozzle Heat Timeout — Firmware Behavior

When `gcode_state` is `IDLE`, `FINISH`, or `FAILED`, the H2D firmware silently resets any
elevated nozzle target back to **38°C** after a calibrated timeout.

**Per-state timeout behavior:**

| gcode_state | Timer fires? | Notes |
|-------------|-------------|-------|
| `IDLE` | Yes | Confirmed — no active job; quiescent condition |
| `FINISH` | Likely yes | Same quiescent condition; unverified empirically |
| `FAILED` | Likely yes | Same quiescent condition; unverified empirically |
| `PAUSE` | Likely NO | Active print; firmware keeps nozzle hot for resume |
| `RUNNING` | No | Firmware maintains requested temp |
| `PREPARE` | No | Pre-print sequence; firmware maintains requested temp |

**Constants (defined in `calibration/corner_calibration.py` and `calibration/nozzle_compare.py`):**
```python
IDLE_NOZZLE_HEAT_TIMEOUT_S  = 300     # [VERIFIED: empirical 2026-03-14] 3 trials, mean=300.7s (300.91, 299.95, 301.19)
IDLE_HEAT_KEEPALIVE_S       = IDLE_NOZZLE_HEAT_TIMEOUT_S * 0.75  # = 225s proactive check threshold
IDLE_HEAT_POLL_INTERVAL_S   = 10.0                               # reactive poll interval
```

**Critical calibration finding [VERIFIED: empirical 2026-03-14, 3 trials]:**
Re-asserting the nozzle target temperature while the nozzle is **already at target temp** does NOT
reset the firmware timer. The timer starts from when the nozzle physically reaches the target
temperature, not from when the set command is sent. Confirmed via 3 clean cold-start trials:
300.91s, 299.95s, 301.19s; mean=300.7s, σ<0.6s.

Implication: the **reactive drift check** is the mechanism that prevents indefinite timeout.
When firmware resets to 38°C, the reactive poll detects the drift, re-asserts the target,
the nozzle reheats, and the firmware starts a fresh ~300s countdown. The proactive timer
(at 75% = 225s) is belt-and-suspenders and does not harm, but does not extend the countdown.

**Dual-layer keepalive pattern — `heat_and_wait(t0, t1, duration_s)` in calibration scripts:**

Both checks are independent `if` blocks (NOT `elif`) — both can fire in the same iteration.
`set_nozzle_temp()` is called only on condition, never speculatively on every tick.

```
Proactive: elapsed >= IDLE_HEAT_KEEPALIVE_S → set_nozzle_temp() for both nozzles
           (harmless no-op re-assert; does NOT extend firmware timer)
Reactive:  now >= next_poll → read targets → if target drifted → set_nozzle_temp() + log WARN
           (ACTUAL reset prevention — firmware reset detected; re-assert gives fresh ~300s)
```

Both checks share `last_assert`. Reactive re-assert also resets the proactive timer.
Inner loop cadence: 0.5s. All temperature calls use `set_nozzle_temp()` (Tier 1) — never M104.

---

## Pause State Recovery

When `gcode_state="PAUSE"`, identify the cause from `stg_cur` before choosing a resume command.

### Pause cause → resume command

| stg_cur | Pause cause | Resume command |
|---------|-------------|---------------|
| 17 | User-initiated pause | `resume_print()` |
| 6 | M400 (GCode pause point) | `resume_print()` |
| 7 | Filament runout detected by AMS | `send_ams_control_command(RESUME)` |
| 4 | Filament change in progress (stuck) | `send_ams_control_command(RESUME)` |
| 18 | Front cover removed | Replace cover, then `resume_print()` |
| 20 | Nozzle temp malfunction | Fix condition, then `resume_print()` |
| 21 | Heatbed temp malfunction | Restore temps, then `resume_print()` |

**Rule:** Use `send_ams_control_command(RESUME)` only when the pause is AMS-triggered
(filament runout, AMS fault). For all other pause causes, use `resume_print()`.

`send_ams_control_command(RESUME)` unblocks the AMS feed **and** resumes the halted
print job in a single operation. Do not also call `resume_print()` after it — that
would be a duplicate command.

### AMS fault full recovery sequence

When active HMS errors include an AMS fault (`HMS_05xx-xxxx-xxxx-xxxx`) and the print
is paused:

1. **Check errors**: `get_hms_errors()` — identify active codes and `print_error` value.
2. **Clear print error**: `clear_print_error(print_error=<code>)` — dismisses the UI fault.
3. **Restore temperatures** (if targets dropped to 0): `set_bed_temp()` and/or
   `set_chamber_temp()` to restore correct values. Wait for temps to recover before
   resuming if they dropped significantly.
4. **Resume**: `send_ams_control_command(RESUME)` — unblocks AMS and resumes print.

Do not skip step 2. The printer stays in a UI-acknowledgment pending state until the
print error is cleared, even if temps are restored.

---

## Stage Code Reference

Key `stg_cur` values (from push_status). This table covers action-relevant stages;
for the complete enumeration see `stage_id` in `get_job_info()` tool docstring.

| ID | Stage | ID | Stage |
|---|---|---|---|
| 0 | Idle/finished | 14 | Homing toolhead |
| 1 | Auto bed leveling | 15 | Cleaning nozzle |
| 2 | Heatbed preheating | 17 | Paused by user |
| 4 | Changing filament | 19 | Calibrating extrusion flow |
| 6 | M400 pause | 22 | Filament unloading |
| 7 | Paused (filament runout) | 24 | Filament loading |
| 8 | Heating nozzle | 255 | Printing normally |
| 9 | Calibrating extrusion | | |

---

## ⚠️ Session Startup — No Configured Printers

Call `get_configured_printers()` at the start of every session before doing anything
printer-related.

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
hold active MQTT sessions — opening a second BambuPrinter session creates a duplicate
MQTT client, wastes resources, and risks MQTT interference.

The only legitimate reason to instantiate BambuPrinter directly is to send a command
that has no container API endpoint (e.g. `send_anything()`).

**MCP tool functions cannot be tested standalone**: `tools/*.py` functions call
`session_manager.get_printer(name)` which is only initialized when the MCP server
process is running. Test tool logic by running the full MCP server and calling tools
through the MCP client.

---

## Active-Print Disruptive Write Gate (Mandatory)

MCP tools and HTTP routes that send disruptive commands to the printer are **blocked**
when `gcode_state` is `RUNNING` or `PREPARE`. The gate is implemented via
`tools/_guards.py:check_active_print_guard()` — a shared helper used by all gated
tools and HTTP routes.

### Gated tools (runtime block — returns error immediately)

| Tool | Tier | Reason |
|------|------|--------|
| `send_gcode()` | 1 — firmware has NO protection | G-code executes immediately mid-print; can crash toolhead |
| `swap_tool()` | 1 — firmware has NO protection | Tool swap crashes inactive nozzle into active print on H2D |
| `set_nozzle_config()` | 1 — firmware has NO protection | Internally calls `set_active_tool()` — same crash risk as `swap_tool()` |
| `print_file()` | 2 — defense-in-depth | Firmware rejects with "printer busy" but gate gives clear error |
| `load_filament()` | 2 — defense-in-depth | AMS locks slots during prints; gate prevents wasted round-trip |
| `unload_filament()` | 2 — defense-in-depth | AMS locks slots during prints; gate prevents wasted round-trip |

### Ungated tools (docstring warning only)

| Tool | Reason for no gate |
|------|-------------------|
| `send_mqtt_command()` | Last-resort bypass tool by design; gating defeats its purpose |
| `select_extrusion_calibration()` | Low risk; may interfere with flow settings but not catastrophic |

### Gated HTTP routes (return 409 Conflict)

| Route | Reason |
|-------|--------|
| `POST /api/send_gcode` | Calls `p.send_gcode()` directly on BPM — bypasses MCP tool gate |
| `POST /api/print_3mf` | Calls `p.print_3mf_file()` directly on BPM — bypasses MCP tool gate |

### Blocked states

- `RUNNING` — printer is actively executing toolpath moves
- `PREPARE` — printer is preparing to print (heating, leveling, calibrating)

### Safe states (not blocked)

- `IDLE`, `FINISH`, `FAILED` — printer is not executing a job
- `PAUSE` — job is paused; some operations (e.g., filament change) are valid while paused

### Agent behavioral rule

The agent MUST NOT use `send_mqtt_command()` to circumvent the active-print guard on
any gated tool. The guard exists to prevent hardware damage — bypassing it via the
raw MQTT tool is equivalent to removing a safety interlock.
"""
