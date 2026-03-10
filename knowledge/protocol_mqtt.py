"""
protocol_mqtt.py — MQTT topics, message types, home_flag bitfield, and xcam fields.

Sub-topic of protocol. Access via get_knowledge_topic('protocol/mqtt').
"""

from __future__ import annotations

PROTOCOL_MQTT_TEXT: str = """
# Bambu Lab Protocol — MQTT Topics, Messages & Bitfields

---

## MQTT Topics

All communication uses SSL-encrypted MQTT on port 8883.

| Direction | Topic pattern | Purpose |
|---|---|---|
| Commands (out) | `device/{serial}/request` | Send commands to printer |
| Telemetry (in) | `device/{serial}/report` | Receive all telemetry from printer |

Authentication: username=`bblp`, password=access_code (8-char string).
Client ID: `studio_client_id:0c1f` (configurable via BambuConfig.mqtt_client_id).

The push message types (push_status, push_info, push_full) are message types
WITHIN the report topic — NOT separate subscription topics.

---

## Message Types (inbound on report topic)

### `push_status`
Primary continuous telemetry stream. Contains `print` key at root.
Key fields: `gcode_state`, `bed_temper`, `bed_target_temper`, `nozzle_temper`,
`nozzle_target_temper`, `home_flag`, `mc_percent`, `mc_remaining_time`, `layer_num`,
`total_layer_num`, `stg_cur`, `spd_lvl`, `subtask_name`, `gcode_file`,
`lights_report`, `print_error`, `hms`, `ams`, `xcam`, `wifi_signal`, `fun`, `stat`.

### `push_info` / info module messages
Contains `info` key with `module` array. Used for firmware version reporting.
Each module entry: `{"name": "ota", "sn": "<serial>", "sw_ver": "<version>",
"product_name": "..."}`. AMS firmware version sourced from modules where
`product_name.lower()` contains `"ams"`.

### `push_full`
Full state snapshot; parsed identically to `push_status`.

### Command Acknowledgment (ack)
When a command is accepted, printer echoes back with `"result": "success"` in
the `print` block. These are TRANSIENT acks — confirm acceptance but are NOT
the steady-state source for flags/states. Steady-state truth = `push_status` bitfields.

Example: `{"print": {"command": "ams_filament_setting", "result": "success", ...}}`

### xcam result messages
`{"xcam": {"result": "SUCCESS", ...}}` — result of an XCAM_CONTROL_SET command.

### update messages
`{"update": {"name": "<name>", "reason": "success", "result": "success"}}` —
response to printer rename. No dedicated handler (logged as "unknown message type").

---

## ANNOUNCE_VERSION / ANNOUNCE_PUSH (watchdog / refresh)

On connect and periodically (watchdog interval, default 30s), publish both:
- `ANNOUNCE_VERSION` → triggers printer to send firmware/module info
- `ANNOUNCE_PUSH` → triggers full push_status telemetry refresh

The watchdog thread publishes these when no message received within
`watchdog_timeout` seconds. `refresh()` and `trigger_printer_refresh` both
publish these commands explicitly.

---

## Print Control Commands

These are the commands sent to `device/{serial}/request` to control print jobs.
`print.resume` and `print.ams_control` are **independent protocol commands** — they
are not automatically paired; each does only what it says.

### print.resume

Resumes a paused print job. Sent with **QoS 1** (higher delivery priority).

```json
{
    "print": {
        "sequence_id": "0",
        "command": "resume",
        "param": ""
    }
}
```

Use for: user pauses (`stg_cur=17`), M400 pauses (`stg_cur=6`), and most non-AMS
sensor pauses. See behavioral_rules/print_state for the full pause-cause decision table.

### print.pause

Pauses the active print job.

```json
{
    "print": {
        "sequence_id": "0",
        "command": "pause",
        "param": ""
    }
}
```

### print.ams_control

Controls the AMS feed state. Operates on the AMS independently of the print job state.

```json
{
    "print": {
        "sequence_id": "0",
        "command": "ams_control",
        "param": "resume"
    }
}
```

`param` values: `"resume"` / `"pause"` / `"reset"`.

**`param: "resume"`** unblocks the AMS feed after a filament runout or AMS fault.
This is the correct command when `stg_cur=7` (filament runout) or when an active AMS
HMS error (`HMS_05xx`) is present. In the MCP, calling `send_ams_control_command(RESUME)`
sends this command and also resumes the print job — use it as the single recovery action
for AMS-triggered pauses.

**`param: "reset"`** resets the AMS to its idle/ready state (use after mechanical jam
or to clear AMS state without resuming).

**`param: "pause"`** pauses the AMS feed mid-print.

---

## home_flag Bitfield (from push_status)

The `home_flag` integer in `push_status` is the steady-state source for these
BambuConfig fields and PrinterCapabilities:

| Bit | Field | Type |
|---|---|---|
| 4 | `config.auto_recovery` | bool (state) |
| 7 | `config.calibrate_remain_flag` | bool (state) |
| 10 | `config.auto_switch_filament` | bool (state) |
| 17 | `config.sound_enable` | bool (state) |
| 18 | `has_sound_enable_support` | bool (cap) |
| 19 | `has_filament_tangle_detect_support` | bool (cap) |
| 20 | `config.filament_tangle_detect` | bool (state) |
| 24 | `config.nozzle_blob_detect` | bool (state) |
| 25 | `has_nozzle_blob_detect_support` | bool (cap) |
| 28 | `config.air_print_detect` | bool (state) |
| 29 | `has_air_print_detect_support` | bool (cap) |

Reading pattern (from bambuprinter.py `_on_message`):
```python
flag = int(status["home_flag"])
config.auto_recovery = (flag >> 4) & 0x1 != 0
config.auto_switch_filament = (flag >> 10) & 0x1 != 0
config.capabilities.has_sound_enable_support = (flag >> 18) & 0x1 != 0
```

Telemetry Mapping Parity: all print_option flags sourced from `home_flag` by
default. New sibling flags should follow the same pattern unless evidence proves otherwise.

---

## xcam Fields and Detection Features

The `xcam` block in `push_status` drives X-Cam AI vision detector state.

### xcam.cfg bitfield (modern firmware — H2D and newer)

The `xcam.cfg` integer encodes all detector states and sensitivities:

| Bits | Field | Notes |
|---|---|---|
| 7 | spaghetti_detector (enable) | bool |
| 8-9 | spaghetti sensitivity | 0=low, 1=medium, 2=high |
| 10 | purgechutepileup_detector | bool |
| 11-12 | pileup sensitivity | 0=low, 1=medium, 2=high |
| 13 | nozzleclumping_detector | bool |
| 14-15 | clump sensitivity | 0=low, 1=medium, 2=high |
| 16 | airprinting_detector | bool |
| 17-18 | airprint sensitivity | 0=low, 1=medium, 2=high |

### xcam explicit keys (legacy firmware — X1/P1/A1 series)

When `xcam.cfg` is absent, individual keys are used:
- `xcam.spaghetti_detector` → bool
- `xcam.pileup_detector` → bool
- `xcam.clump_detector` → bool
- `xcam.airprint_detector` → bool
- `xcam.buildplate_marker_detector` → bool
- `xcam.print_halt` → bool (sensitivity hint: True = medium)
- `xcam.first_layer_inspector` → bool (used for `has_lidar` capability)

### fun bitfield (capability flags)

The `fun` hex string from `push_status` encodes printer capability flags:

| Bit | Capability |
|---|---|
| 12 | `has_chamber_door_sensor` |
| 42 | `has_spaghetti_detector_support` |
| 43 | `has_purgechutepileup_detector_support` |
| 44 | `has_nozzleclumping_detector_support` |
| 45 | `has_airprinting_detector_support` |

### stat bitfield (door/lid sensor state)

When `has_chamber_door_sensor` is set, `stat` hex from `push_status` contains:

| Bit | Field |
|---|---|
| 23 | `is_chamber_door_open` |
| 24 | `is_chamber_lid_open` |
"""
