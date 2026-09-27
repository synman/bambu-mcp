# Bambu Lab MCP Server

**Version: 0.9.0** · Follows [Semantic Versioning](https://semver.org/)

An MCP (Model Context Protocol) server that lets AI agents monitor and control Bambu Lab 3D
printers. It is built on [bambu-printer-manager](https://github.com/synman/bambu-printer-manager)
and keeps one live MQTT session per printer. The same process serves a REST API and MJPEG
camera streams. No Docker or container is needed.

**Documentation:** <https://synman.github.io/bambu-printer-manager/bambu-mcp/>, with the full
MCP tool reference and REST API reference.

---

## Features

- MCP tools for discovery, state, print control, climate, filament and AMS, nozzles,
  detectors, files, camera, alerts and charts. Every tool carries a title and read-only,
  destructive, idempotent and open-world annotations.
- Write protection: every state-changing tool refuses unless called with `user_permission=True`,
  and tools that would disturb a print refuse while the printer is printing.
- A REST API over the same sessions, with a Swagger UI at `/api/docs`.
- MJPEG camera streams: RTSPS for X1, P2S and H2 series, TLS on port 6000 for A1 and P1.
- Printer credentials encrypted at rest with AES-256-GCM, keychain-backed master key.
- 4 `bambu://rules/*` resources, a `bambu://alerts/{name}` alert feed and one prompt,
  `bambu_system_context`.

> **Security:** the REST API and camera streams listen on all network interfaces with no
> authentication. Run bambu-mcp only on a trusted network.

---

## Installation

### Prerequisites

- Python 3.12+
- LAN access to your printers

### Setup

```bash
git clone https://github.com/synman/bambu-mcp.git
cd bambu-mcp
python3 make.py
```

`make.py` creates `.venv/` inside the project and installs the package with all dependencies.
Run it again after any upstream dependency change.

### Add your first printer

1. **Discover**: call `discover_printers()` to find printers on your network by SSDP.
2. **Get access code**: on the printer touchscreen, Settings, Network, Access Code.
3. **Add**: call `add_printer(...)` with the name, IP, serial and access code, and
   `user_permission=True`.
4. **Verify**: call `get_printer_state(name="myprinter")`.

---

## Client Configuration

### Claude Desktop

Merge the `"bambu-mcp"` entry from `config/claude_desktop.example.json` into
`~/Library/Application Support/Claude/claude_desktop_config.json`, replacing `<install-dir>`.

### GitHub Copilot CLI

Merge `config/copilot_mcp.example.json` into your Copilot MCP configuration.

### Streamable HTTP

Run `.venv/bin/python3 server.py --transport streamable-http` and point the client at
`http://127.0.0.1:25099/mcp`. Running it as a supervised daemon: `docs/operators-guide.md`.

---

## Tools

<!-- gen_docs:tools:begin -->
104 tools (51 read-only, 53 write) and 87 REST routes. Full reference: [bambu-mcp docs](https://synman.github.io/bambu-printer-manager/bambu-mcp/).

| Category | Tools |
|---|---|
| [Printer State](https://synman.github.io/bambu-printer-manager/bambu-mcp/tools/state/) | `get_ams_status`, `get_capabilities`, `get_fan_speeds`, `get_hms_errors`, `get_job_info`, `get_print_progress`, `get_printer_info`, `get_printer_state`, `get_spool_info`, `get_temperatures`, `get_wifi_signal` |
| [Print Control](https://synman.github.io/bambu-printer-manager/bambu-mcp/tools/print-control/) | `clear_print_error`, `pause_print`, `resume_print`, `select_extrusion_calibration`, `send_gcode`, `set_print_option`, `set_print_speed`, `skip_objects`, `stop_print` |
| [Climate](https://synman.github.io/bambu-printer-manager/bambu-mcp/tools/climate/) | `get_chamber_light`, `get_climate`, `set_bed_temp`, `set_chamber_light`, `set_chamber_temp`, `set_fan_speed`, `set_nozzle_temp` |
| [Filament & AMS](https://synman.github.io/bambu-printer-manager/bambu-mcp/tools/filament/) | `calibrate_ams_remaining`, `get_ams_units`, `get_external_spool`, `get_filament_catalog`, `load_filament`, `send_ams_control_command`, `set_ams_filament_setting`, `set_ams_user_setting`, `start_ams_dryer`, `stop_ams_dryer`, `unload_filament` |
| [Nozzles](https://synman.github.io/bambu-printer-manager/bambu-mcp/tools/nozzle/) | `get_nozzle_info`, `refresh_nozzles`, `set_nozzle_config`, `swap_tool` |
| [Detectors](https://synman.github.io/bambu-printer-manager/bambu-mcp/tools/detectors/) | `get_detector_settings`, `set_air_printing_detection`, `set_buildplate_marker_detection`, `set_first_layer_inspection`, `set_nozzle_clumping_detection`, `set_purge_chute_detection`, `set_spaghetti_detection` |
| [Printer Management](https://synman.github.io/bambu-printer-manager/bambu-mcp/tools/management/) | `add_printer`, `disconnect_printer`, `get_configured_printers`, `get_printer_connection_status`, `remove_printer`, `start_printer`, `update_printer_credentials` |
| [Files](https://synman.github.io/bambu-printer-manager/bambu-mcp/tools/files/) | `create_folder`, `delete_file`, `download_file`, `get_3mf_entry_by_id`, `get_3mf_entry_by_name`, `get_all_project_info`, `get_current_job_project_info`, `get_file_info`, `get_plate_thumbnail`, `get_plate_topview`, `get_project_info`, `list_sdcard_files`, `open_plate_layout`, `open_plate_viewer`, `preview_ams_mapping`, `print_file`, `refresh_sdcard`, `rename_sdcard_file`, `upload_file` |
| [System](https://synman.github.io/bambu-printer-manager/bambu-mcp/tools/system/) | `dump_log`, `force_state_refresh`, `get_firmware_version`, `get_server_info`, `get_session_status`, `get_user_pref`, `pause_mqtt_session`, `rename_printer`, `resume_mqtt_session`, `set_print_options`, `set_user_pref`, `trigger_printer_refresh`, `truncate_log` |
| [Discovery](https://synman.github.io/bambu-printer-manager/bambu-mcp/tools/discovery/) | `discover_printers` |
| [Raw Commands](https://synman.github.io/bambu-printer-manager/bambu-mcp/tools/commands/) | `send_mqtt_command` |
| [Camera](https://synman.github.io/bambu-printer-manager/bambu-mcp/tools/camera/) | `analyze_active_job`, `get_stream_url`, `open_job_state`, `start_stream`, `stop_stream`, `view_stream` |
| [Alerts](https://synman.github.io/bambu-printer-manager/bambu-mcp/tools/notifications/) | `get_pending_alerts` |
| [Snapshots & Monitoring Data](https://synman.github.io/bambu-printer-manager/bambu-mcp/tools/snapshots/) | `get_monitoring_data`, `get_monitoring_history`, `get_monitoring_series`, `get_snapshot` |
| [Charts](https://synman.github.io/bambu-printer-manager/bambu-mcp/tools/charts/) | `open_charts`, `render_charts_html`, `render_charts_panels` |
<!-- gen_docs:tools:end -->

---

## Architecture

```
bambu-mcp/
├── server.py            ← FastMCP entry point: tools, resources, prompt, startup
├── api_server.py        ← REST API and OpenAPI document
├── session_manager.py   ← Persistent BambuPrinter MQTT sessions
├── data_collector.py    ← Telemetry history per printer
├── notifications.py     ← State-change alert queue
├── secrets_store.py     ← AES-256-GCM secrets store
├── auth.py              ← Printer credential CRUD on top of secrets_store
├── job_project.py       ← Running job's resolved .3mf path per printer
├── port_pool.py         ← Shared port pool for the REST API and camera streams
├── daemon_port.py       ← Streamable HTTP port and singleton handling
├── gen_docs.py          ← Generates the published docs pages and the Tools section above
├── make.py              ← Cross-platform venv installer
├── camera/              ← RTSPS and TCP-TLS clients, MJPEG server, job monitor and analyzer
├── calibration/         ← Calibration scripts
├── tools/               ← Tool modules, plus _registry.py (registration and annotations)
├── resources/rules.py   ← bambu://rules/* readers (redacted)
├── prompts/context.py   ← bambu_system_context prompt
├── docs/                ← Operators guide, LAN file tunnel protocol, site overview source
└── config/              ← Example client configs and settings template
```

---

## Documentation

- Published docs: <https://synman.github.io/bambu-printer-manager/bambu-mcp/>. The pages are
  generated by `gen_docs.py` from the tool docstrings, `api_server.py` and
  `docs/site/overview.md`. After changing any of those, run `.venv/bin/python3 gen_docs.py`
  and commit the regenerated pages in bambu-printer-manager.
- `docs/operators-guide.md`: daemon administration, restarts, logging and ports.
- `docs/lan-file-tunnel-protocol.md`: the port 6000 LAN file tunnel on H2-series, P2S and X2D.
- `PLAN.md`: implementation plan and design decisions.
