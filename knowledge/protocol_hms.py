"""
protocol_hms.py — HMS error structure and firmware upgrade state fields.

Sub-topic of protocol. Access via get_knowledge_topic('protocol/hms').
"""

from __future__ import annotations

PROTOCOL_HMS_TEXT: str = """
# Bambu Lab Protocol — HMS Errors & Firmware Upgrade State

---

## HMS Error Structure

### HMS list (hms array in push_status)

Each HMS entry has `attr` and `code` integer fields:
```json
{"attr": 0x03010000, "code": 0x00010001}
```

Combined ecode = `f"{attr:08X}{code:08X}"` — 16 hex chars.
wiki_slug = XXXX-XXXX-XXXX-XXXX format.
URL pattern: `https://e.bambulab.com/?e={ecode}`

Module encoding (bits 24-31 of attr):
| Byte | Module |
|---|---|
| 0x03 | Mainboard |
| 0x05 | AMS |
| 0x07 | Toolhead |
| 0x0B | Webcam |
| 0x10 | HMS |
| 0x12 | AMS |

Severity encoding (bits 16-23 of attr):
| Value | Severity | is_critical |
|---|---|---|
| 0x00 | Fatal | True |
| 0x01 | Error | True |
| 0x02 | Warning | False |
| other | Info | False |

Active vs. historical:
- **Actively faulted**: BOTH a `device_hms` entry AND a matching `device_error` entry
  are present. Only actively faulted errors require attention before printing.
- **Historical/cleared**: `device_hms` entry with no matching `device_error` →
  `severity="Historical"`, `is_critical=False`. Does NOT block printing.

### print_error (single integer in push_status)

Decoded via `decodeError()` in bambutools.py. Format: 8-char hex, same module and
severity encoding as HMS. URL: `https://e.bambulab.com/?e={raw_hex}`.

**HMS_0300-400C ("task was canceled")**: Transient error set when a print is canceled.
Value: `print_error: 50348044`. Auto-clears within seconds. NOT a hardware fault.
If not yet cleared, use `clear_print_error()` — never refuse to start a new print
solely because this error code is present.

**Two-command clear protocol**: BambuStudio sends TWO commands to dismiss an error:
1. `clean_print_error` — clears the print_error integer.
2. `uiop` (system command, action "close") — signals "UI dialog acknowledged."
Without `uiop`, the printer stays in UI-acknowledgment pending state and re-raises
print_error on every push_status until the signal is received.
Always use `clear_print_error()` — never send `clean_print_error` alone.

---

## Firmware Upgrade State Fields (push_status)

Fields in `upgrade_state` within `push_status`:

| Field | Values | Description |
|---|---|---|
| upgrade_state.status | "FLASH_START", "UPGRADE_SUCCESS", etc. | Current upgrade phase |
| upgrade_state.progress | 0-100 | Upgrade progress percent |
| upgrade_state.module | "ap" = main Linux image | Which module is upgrading |
| upgrade_state.message | string | Human-readable status |
| dis_state | 2 = actively upgrading, 3 = complete/failed | Display state |
"""
