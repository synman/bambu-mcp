# Bambu Lab MCP Server

**Version: 0.2.0** · Follows [Semantic Versioning](https://semver.org/)

A fully self-contained MCP (Model Context Protocol) server for managing Bambu Lab 3D printers.

**Only external dependency: a network connection to your printers.**

All intelligence from `bambu-printer-manager` and `bambu-printer-app` is baked in — no Docker, no Flask, no containers.

---

## Features

- **85 tools** covering discovery, state monitoring, print control, climate, filament, camera, files, detectors, and raw commands
- **9 resources** at `bambu://` URIs: live rules files + baked-in knowledge modules
- **1 system prompt** (`bambu_system_context`) that loads all behavioral rules, escalation policy, and tool guidance
- **Encrypted secrets store** — Fernet AES-256 at `~/.bambu-mcp/secrets.enc` (cross-platform)
- **Persistent MQTT sessions** — BambuPrinter sessions start at server launch, reconnect automatically
- **3-tier knowledge escalation** — baked-in → authoritative GitHub repos → broad search
- **Write protection** — all state-changing tools require `user_permission=True`

---

## Installation

### Prerequisites

- Python 3.12+
- A virtual environment

### Setup

```bash
# Clone the repo and install (creates .venv inside the project)
cd ~/bambu-mcp
python make.py
```

`make.py` creates `~/bambu-mcp/.venv/` and pip-installs the package with all dependencies. Run it again after any upstream dependency change.

### Add your first printer

Start the server and use the `discover_printers` tool, or use the MCP tools directly:

1. **Discover**: Call `discover_printers()` — finds printers on your local network via SSDP
2. **Get access code**: On the printer touchscreen → Settings → Network → Access Code
3. **Add**: Call `add_printer(name="myprinter", ip="...", serial="...", access_code="...")`
4. **Verify**: Call `get_printer_state(name="myprinter")`

---

## Client Configuration

### Claude Desktop

Copy `config/claude_desktop.json` into your Claude Desktop MCP configuration:

```
~/Library/Application Support/Claude/claude_desktop_config.json
```

Merge the `"bambu-mcp"` entry from `config/claude_desktop.json` into your existing config.

### GitHub Copilot CLI

Merge `config/copilot_mcp.json` into your Copilot MCP configuration.

---

## Tool Categories

| Category | Tools | Write Protected |
|---|---|---|
| Discovery & Management | `discover_printers`, `add_printer`, `remove_printer`, `get_configured_printers`, `get_printer_info`, `update_printer_credentials` | add/remove/update |
| State & Monitoring | `get_printer_state`, `get_job_status`, `get_temperatures`, `get_fan_speeds`, `get_ams_status`, `get_hms_errors`, `get_capabilities`, `get_monitoring_data`, + 4 more | none |
| Print Control | `pause_print`, `resume_print`, `stop_print`, `set_print_speed`, `skip_objects`, `set_print_option`, `send_gcode`, `select_extrusion_calibration` | all |
| Climate | `set_nozzle_temp`, `set_bed_temp`, `set_chamber_temp`, `set_chamber_light`, `set_fan_speed`(fan, speed_pct), `get_climate`, `get_chamber_light` | writes |
| Filament | `load_filament`, `unload_filament`, `set_ams_filament_setting`, `start_ams_dryer`, `stop_ams_dryer`, + 4 more | all |
| Nozzle | `get_nozzle_info`, `set_nozzle_config`(diameter, type, extruder), `swap_tool`(extruder_id?), `refresh_nozzles` | writes |
| Detectors | `get_detector_settings`, `set_spaghetti_detection`, `set_buildplate_marker_detection`, `set_first_layer_inspection`, `set_nozzle_clumping_detection`, `set_purge_chute_detection`, `set_air_printing_detection` | writes |
| Files | `list_sdcard_files`, `get_project_info`, `upload_file`, `download_file`, `delete_file`, `create_folder`, `rename_sdcard_file`, `print_file`(ams_mapping?), `open_plate_viewer`, `open_plate_layout`, `get_file_info` | write ops |
| Camera | `get_snapshot`, `get_stream_url`, `start_stream`, `stop_stream`, `view_stream` | none |
| System | `get_session_status`, `pause_mqtt_session`, `resume_mqtt_session`, `trigger_printer_refresh`, `force_state_refresh`, `get_firmware_version`, `get_monitoring_history`, `set_print_options`, `rename_printer` | writes |
| Raw Command | `send_mqtt_command` | always |
| Knowledge | `search_authoritative_sources`, `get_knowledge_topic` | none |

---

## Architecture

```
~/bambu-mcp/
├── server.py                    ← FastMCP entry point (85 tools, 9 resources, 1 prompt)
├── session_manager.py           ← Persistent BambuPrinter MQTT sessions
├── data_collector.py            ← Telemetry history (8 rolling time-series per printer)
├── secrets_store.py             ← Fernet-encrypted secrets at ~/.bambu-mcp/secrets.enc
├── auth.py                      ← Printer credential CRUD on top of secrets_store
├── make.py                      ← Cross-platform venv installer (python make.py)
├── camera/                      ← Self-contained camera streaming module
│   ├── protocol.py              ← Model → protocol routing (RTSPS vs TCP+TLS)
│   ├── rtsps_stream.py          ← PyAV RTSPS client (X1/H2D, port 322)
│   ├── tcp_stream.py            ← Pure-Python TLS binary client (A1/P1, port 6000)
│   └── mjpeg_server.py          ← stdlib MJPEG HTTP server (http://localhost:8090+/)
├── knowledge/                   ← Baked-in knowledge (~90KB)
│   ├── behavioral_rules.py      ← Mandatory agent behavioral rules
│   ├── protocol.py              ← MQTT/HMS/3MF/SSDP/camera protocol docs
│   ├── enums.py                 ← All bpm enum values
│   ├── api_reference.py         ← Full BambuPrinter API reference
│   ├── references.py            ← Authoritative source hierarchy
│   └── fallback_strategy.py     ← 3-tier escalation policy
├── tools/                       ← 13 tool modules
│   ├── state.py                 ← 12 read tools
│   ├── print_control.py         ← 9 print control tools
│   ├── climate.py               ← 7 climate/fan tools
│   ├── filament.py              ← 9 filament/AMS tools
│   ├── nozzle.py                ← 4 nozzle config tools
│   ├── detectors.py             ← 7 detector tools
│   ├── management.py            ← 5 printer lifecycle tools
│   ├── files.py                 ← 13 file operation tools
│   ├── system.py                ← 10 system/session tools
│   ├── camera.py                ← 5 camera/streaming tools
│   ├── discovery.py             ← 1 SSDP discovery tool
│   ├── commands.py              ← 1 raw MQTT command tool
│   └── knowledge_search.py      ← 2 knowledge escalation tools
├── resources/
│   ├── rules.py                 ← Live rules file readers (redacted)
│   └── knowledge.py             ← Knowledge module accessors
├── prompts/
│   └── context.py               ← bambu_system_context system prompt
└── config/
    ├── settings.toml.template   ← Template (copy to settings.toml, set password)
    ├── claude_desktop.json      ← Claude Desktop MCP config
    └── copilot_mcp.json         ← GitHub Copilot CLI MCP config
```

---

## Security

- Access codes and printer credentials are never stored in plaintext
- All credential storage goes through `secrets_store.py` (encrypted at rest)
- All write operations require explicit `user_permission=True` parameter
- Rules files are served with sensitive values redacted (IPs, serials, credentials)
- See `PLAN.md` for full design rationale

---

## See Also

- `PLAN.md` — Full implementation plan and design decisions
- `bambu://knowledge/behavioral-rules` — Behavioral rules for the agent
- `bambu://knowledge/protocol` — Bambu Lab MQTT/HMS protocol documentation
