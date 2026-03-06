# Bambu MCP Plan

> Standalone reference for the self-contained Bambu Lab MCP server.
> Committed to `~/bambu-mcp/PLAN.md` — the authoritative version lives in the project.
> Session state copy: `files/bambu-mcp.md` (kept in sync).

# Bambu Lab MCP Server

## Problem & Goal

A fully self-contained MCP server that bundles the complete functionality of `bambu-printer-manager` and `bambu-printer-app` directly. No containers, no Docker, no HTTP to a remote API. The only external dependency is a network connection to the printers. The MCP manages its own persistent MQTT sessions, replicates all monitoring state from `api.py`, and exposes the entire API surface as ~55 tools.


## Incorporated User Requirements

All of the following user-requested changes are captured in this plan:

### R1 — Full API surface coverage
*"Must offer and fully understand every bambu-printer-manager and bambu-printer-app function, method, property, class"*

Captured in:
- `knowledge/api_reference.py` — complete inventory of all ~40 BambuPrinter methods, all ~30 BambuConfig properties, all BambuState fields, BambuDiscovery/BambuSpool/BambuProject/ActiveJobInfo docs, **and** all ~50 bambu-printer-app API endpoint behaviors (without hostnames)
- `tools/` — 12 tool modules (~68 tools) exposing every operation from both libraries as callable MCP tools
- `prompts/context.py` — `bambu_system_context` prompt includes the full tool guide

### R2 — Rules files knowledge fully integrated
*"All knowledge captured within our rules files must be integrated into the MCP minus sensitive information"*

Captured in:
- `knowledge/behavioral_rules.py` — complete sanitized synthesis of all 3 rules files: `~/.copilot/copilot-instructions.md`, `bambu-printer-app/.github/copilot-instructions.md`, `bambu-printer-manager/.github/copilot-instructions.md`
- `knowledge/protocol.py` — all protocol knowledge from rules files (MQTT, HMS, firmware, SSDP, 3MF)
- `knowledge/enums.py` — all enum/type definitions from rules files and bpm library
- `knowledge/api_reference.py` — all API surface documentation from rules files
- `knowledge/references.py` — all authoritative references listed in rules files
- `resources/rules.py` — live reads of the actual rules files at `bambu://rules/*` URIs
- Sensitive info stripped: actual hostnames, IPs, serials, credential values, Docker/CI details

### R3 — Rules-for-rules: rules must be fully read on every request
*"The rules should be fully read as part of handling every request I make"*

Captured in:
- `knowledge/behavioral_rules.py` — includes ⚠️ **Rules Mandatory Rule**: "On every request, consult all knowledge/ modules and bambu://rules/* resources. Do not rely on cached assumptions from prior turns."
- `prompts/context.py` — `bambu_system_context` opens with this mandatory rule verbatim
- `resources/rules.py` — provides `bambu://rules/global`, `bambu://rules/printer-app`, `bambu://rules/printer-manager` for live reads

### R4 — Self-contained architecture (no containers, no Docker)
*"No reference to actual containers — all functionality from bambu-printer-app API and bambu-printer-manager library must be baked in/bundled. The only external dependency is a network connection."*

Captured in:
- No `registry.py` — printer metadata is never hardcoded; always loaded from `secrets_store.py`
- `session_manager.py` — manages persistent `BambuPrinter` MQTT sessions; reads config from secrets_store
- `data_collector.py` — **self-contained wrapper** for extended functionality (telemetry history, job tracking) built directly on `BambuPrinter` — not a copy of Flask code
- No `container_client.py`, no `direct_client.py`, no HTTP calls
- All tools call `BambuPrinter` directly via session_manager
- Only external deps: network connection to printer (MQTT port 8883, FTPS port 21)

### R5 — Knowledge escalation strategy
*"When all else fails, rely on information sourced from authoritative sources captured within rules files — integrated into the MCP's own core rules. When that path fails, fall back to broader search terms."*

Captured in:
- `knowledge/fallback_strategy.py` — formalizes the 3-tier escalation:
  - **Tier 1**: MCP's own `knowledge/` modules (always first)
  - **Tier 2**: `search_authoritative_sources()` scoped to repos listed in `references.py` (BambuStudio → ha-bambulab → OpenBambuAPI → X1Plus → OrcaSlicer → bambu-node → Bambu-HomeAssistant-Flows)
  - **Tier 3**: Broad GitHub/web search (last resort; flagged as lower reliability)
- `tools/knowledge_search.py` — `search_authoritative_sources(query, repo_filter?)` tool
- `knowledge/behavioral_rules.py` — includes ⚠️ **Knowledge Escalation Rule** as a named rule
- `prompts/context.py` — includes `ESCALATION_POLICY_TEXT` verbatim in system context

---
## Installation

### Fully self-contained — no OS-specific dependencies

The MCP has zero OS-specific or local-path dependencies. All secrets are managed by a cross-platform encrypted file store (`secrets_store.py`).

### `pyproject.toml` — dependencies

```toml
[project]
name = "bambu-mcp"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "mcp[cli]==1.26.0",
    "cryptography",
    "bambu-printer-manager @ git+https://github.com/synman/bambu-printer-manager.git@devel",
]
```

- **`mcp[cli]`** — MCP server framework
- **`cryptography`** — Fernet (AES-256) for cross-platform secrets encryption
- **`bambu-printer-manager@devel`** — `bpm` library from GitHub `devel` branch (default HEAD); `paho-mqtt` and other transitive deps pulled in automatically

**Re-install `bpm` to get latest:**
```bash
pip install --force-reinstall "bambu-printer-manager @ git+https://github.com/synman/bambu-printer-manager.git@devel"
```

### Cross-platform secrets store (`secrets_store.py`)

Stores secrets in `~/.bambu-mcp/secrets.enc` — a Fernet-encrypted JSON file. No macOS Keychain, no OS-specific API.

```python
# Default password hardwired in config/settings.toml:
[secrets]
password = "changeit"   # ← change this before use
```

**Key design:**
- Key derivation: `PBKDF2HMAC(SHA-256, salt="bambu-mcp", iterations=100000)` → Fernet key
- Storage: `~/.bambu-mcp/secrets.enc` (AES-256 GCM, Fernet format)
- API: `get(key)`, `set(key, value)`, `list()`, `delete(key)` — all purely Python

### Populating secrets (CLI)

```bash
python -m bambu_mcp secrets set bambu-h2d-printer_ip       <ip>
python -m bambu_mcp secrets set bambu-h2d-printer_access_code <code>
python -m bambu_mcp secrets set bambu-h2d-printer_serial   <serial>
python -m bambu_mcp secrets set bambu-a1-printer_ip        <ip>
python -m bambu_mcp secrets set bambu-a1-printer_access_code <code>
python -m bambu_mcp secrets set bambu-a1-printer_serial    <serial>
```

Same key naming convention as the existing workspace (compatible with `secrets.py`).

### `make.py` — project-local venv installer

A `make.py` script manages the installation. It creates a `.venv` directory inside `~/bambu-mcp/` (project-local, not a shared virtualenv) and pip-installs the package editable with all dependencies.

```bash
cd ~/bambu-mcp/
python make.py          # creates .venv/, installs bpm@devel + mcp + cryptography
```

The MCP config should reference `~/bambu-mcp/.venv/bin/python3` — not any system or shared virtualenv.

### Full setup sequence (for README)

```bash
cd ~/bambu-mcp/
python make.py                                            # creates .venv, installs bpm@devel + mcp + cryptography
cp config/settings.toml.template config/settings.toml   # set secrets password (default: "changeit")
python server.py                                     # start MCP server
```

**First-time printer setup** happens at runtime via tool calls:
1. Use `discover_printers()` to find printers on the network (SSDP) — returns IP + serial
2. Provide access code when prompted
3. `add_printer(name, ip, serial, access_code)` saves to encrypted secrets store + starts session
4. All future sessions auto-load from the store

Or manually: `add_printer(name, ip, serial, access_code)` without discovery.

No hardcoded printer metadata. No registry file. No local `~/bambu-printer-manager/` clone required.




## Architecture Principle

**The MCP calls `bambu-printer-manager` directly.** It holds persistent MQTT sessions (one per printer), and builds self-contained Python wrappers for the extended functionality the HTTP API provides today (telemetry history, job state tracking) — not copies of Flask code, but clean modules built directly on `BambuPrinter`.

```
MCP Process (persistent)
├── session_manager.py      ─── BambuPrinter(h2d)  ←─ MQTT/FTPS ──→ H2D printer
│                           ─── BambuPrinter(a1)   ←─ MQTT/FTPS ──→ A1 printer
├── data_collector.py       ─── self-contained wrapper: subscribes to on_update,
│                               builds telemetry history from BambuState directly
└── FastMCP tools           ─── all operations via session_manager (no HTTP)
```

**No HTTP calls. No containers. No SSH. Call `BambuPrinter` directly. MQTT (port 8883) + FTPS to each printer.**

## Credentials

All from `secrets.py` — same store, different keys than the old container API auth:

| Key | Description |
|---|---|
| `bambu-h2d-printer_ip` | H2D printer hostname/IP |
| `bambu-h2d-printer_access_code` | H2D 8-char LAN access code |
| `bambu-h2d-printer_serial` | H2D serial number |
| `bambu-a1-printer_ip` | A1 printer hostname/IP |
| `bambu-a1-printer_access_code` | A1 8-char LAN access code |
| `bambu-a1-printer_serial` | A1 serial number |

`auth.py` resolves `BPM_SECRETS_PASS` from `~/.zshenv` and returns `{ip, access_code, serial}` per printer name.

## File Structure

```
~/bambu-mcp/
├── make.py                         # Cross-platform venv installer (python make.py)
│                                   # creates .venv/, pip install -e . (bpm@devel + mcp + cryptography)
│                                   # MCP config must reference .venv/bin/python3
├── README.md
├── PLAN.md                         # This planning document — committed to repo
├── pyproject.toml                  # deps: mcp[cli]==1.26.0, cryptography
│                                   # + bambu-printer-manager@git+github:synman/devel
│                                   # no OS-specific deps; no hardcoded printer metadata
├── .gitignore                      # registry.py, *.pyc, __pycache__
├── server.py                       # FastMCP entry point
│                                   # On init: session_manager.start_all()
│                                   # On shutdown: session_manager.stop_all()
├── secrets_store.py                # Cross-platform encrypted secrets store
│                                   # Fernet (AES-256) over ~/.bambu-mcp/secrets.enc
│                                   # get(key)/set(key,val)/list() — no OS Keychain, no macOS dep
│                                   # Default password: "changeit" (in config/settings.toml)
├── auth.py                         # get_printer_credentials(name) → {ip, access_code, serial}
│                                   # reads from secrets_store; no subprocess, no local clone needed
├── registry.py                     # [GITIGNORED] Printer definitions:
│                                   #   name → secret key prefix + model metadata
├── session_manager.py              # Manages persistent BambuPrinter sessions
│                                   # start_all(), stop_all()
│                                   # get_printer(name) → BambuPrinter
│                                   # get_state(name) → BambuState
│                                   # get_job(name) → ActiveJobInfo
│                                   # on_update(name) callback → feeds data_collector
├── data_collector.py               # Self-contained telemetry history wrapper
│                                   # Subscribes to session_manager on_update callbacks
│                                   # Reads BambuState directly — no HTTP, no Flask
│                                   # Collects: tool, bed, chamber, fans (per printer)
│                                   # Tracks: gcode_state_durations, job transitions
│                                   # Design is clean Python — not a copy of api.py
├── knowledge/
│   ├── behavioral_rules.py         # Sanitized all-rules synthesis (no hostnames/IPs/creds)
│   ├── protocol.py                 # MQTT topics, telemetry semantics, HMS, firmware, SSDP, 3MF
│   ├── enums.py                    # Import + document all enums from bambu-printer-manager
│   ├── api_reference.py            # BambuPrinter methods + params + returns (no endpoints)
│   └── references.py               # Public GitHub reference implementations + URLs
├── tools/
│   ├── state.py                    # 12 read tools — read from session_manager state
│   ├── print_control.py            # 6 print tools — BambuPrinter methods on managed session
│   ├── climate.py                  # 6 climate tools
│   ├── filament.py                 # 9 filament/AMS tools (dryer: BambuPrinter direct — same session)
│   ├── nozzle.py                   # 3 nozzle tools
│   ├── detectors.py                # 5 AI detector tools
│   ├── files.py                    # 10 file tools — BambuPrinter FTPS methods
│   │                               # + open_plate_viewer: HTML viewer for all plates in a 3MF
│   │                               # + open_plate_layout: annotated top-down PNG with bounding boxes
│   ├── system.py                   # 8 system tools — session control, data_collector, telemetry
│   │                               # get_session_status, pause/resume_mqtt_session
│   │                               # get_monitoring_history, get_firmware_version
│   │                               # trigger_printer_refresh, force_state_refresh
│   │                               # set_print_options (auto_recovery, sound)
│   ├── discovery.py                # discover_printers() — BambuDiscovery SSDP (ephemeral, safe)
│   ├── management.py               # 5 printer management tools
│   │                               # get_configured_printers, add_printer, remove_printer
│   │                               # update_printer_credentials, get_printer_connection_status
│   └── knowledge_search.py         # 2 knowledge tools
│                                   # search_authoritative_sources(), get_knowledge_topic()
├── resources/
│   ├── rules.py                    # bambu://rules/* (live file reads, redacts sensitive values)
│   └── knowledge.py                # bambu://knowledge/* (all knowledge module content)
└── config/
    ├── claude_desktop.json         # Drop-in config for Claude Desktop
    └── copilot_mcp.json            # GitHub Copilot CLI MCP config
```

## First-Time Printer Setup Flow

No printer metadata is hardcoded anywhere. On every session start the agent calls
`get_configured_printers()` first. If the list is empty, `discover_printers()` runs
automatically — this is the welcoming action, not a user-initiated step.

```
Session start (always):
  get_configured_printers()
  → non-empty: printers already configured, proceed normally
  → empty:     automatically run discover_printers()
               → presents found printers (ip, serial, model) to user
               → user provides access_code from printer touchscreen
               → add_printer(name, ip, serial, access_code)
               → session_manager starts BambuPrinter session → connected
               → stored in secrets_store: bambu-{name}_ip, _serial, _access_code, printers=[name]

Subsequent sessions:
  session_manager reads secrets_store → starts all configured printers automatically
  → tools work immediately; no setup needed
```

### Management tools (in `tools/management.py`)

| Tool | Description |
|---|---|
| `get_configured_printers()` | Returns list of configured printer names + models from secrets_store |
| `add_printer(name, ip, serial, access_code)` | Saves credentials, starts session immediately |
| `remove_printer(name)` | Deletes credentials, stops session |
| `update_printer_credentials(name, ...)` | Updates one or more credential fields |
| `get_printer_connection_status(name)` | Session state + connectivity |

## session_manager.py Design

```python
# Manages one BambuPrinter per configured printer, started at MCP init
# Tools read state directly from printer.printer_state (BambuState)
# No polling — MQTT push updates keep state current

class SessionManager:
    def start_all(self):
        # Load configured printer names from secrets_store (no hardcoded registry)
        for name in secrets_store.get("_printer_names", default=[]):
            creds = auth.get_printer_credentials(name)
            config = BambuConfig(hostname=creds["ip"],
                                 access_code=creds["access_code"],
                                 serial_number=creds["serial"])
            printer = BambuPrinter(config=config)
            printer.on_update = lambda: data_collector.on_update(name, printer)
            printer.start_session()
            self._printers[name] = printer

    def get_printer(self, name) -> BambuPrinter: ...
    def get_state(self, name) -> BambuState: ...    # printer.printer_state
    def get_job(self, name) -> ActiveJobInfo: ...    # printer.active_job_info
    def pause_session(self, name): ...
    def resume_session(self, name): ...
```

## data_collector.py Design

Self-contained wrapper over `BambuPrinter.printer_state` — not a copy of `api.py`:
- Subscribes to `session_manager.on_update(name, printer)` callback
- Reads `BambuState` fields directly from the live `BambuPrinter` instance
- Maintains rolling history per printer: tool temp, bed temp, chamber temp, all fans
- Tracks `gcode_state_durations` (accumulated time per state, reset on new job)
- `get_all_data(name)` → returns history + durations dict (feeds `get_monitoring_data` tool)
- **Design principle**: clean Python class; not bound to Flask/DataCollector structure

## Tool Changes vs Previous Plan

| Tool | Old source | New source |
|---|---|---|
| All read tools | container API GET /api/* | `session_manager.get_state()` / `get_job()` |
| All write tools | container API GET /api/* | `BambuPrinter` method on managed session |
| `get_monitoring_data` | container API GET /api/get_all_data | `data_collector.get_all_data()` |
| `dump_log` / `truncate_log` | container log file | MCP-internal log (Python `logging` handler) |
| `toggle_session` | container API | `session_manager.pause/resume_session()` |
| `upload_file_to_host` | POST to Flask server | Accept local path → pass to `upload_file_to_printer` (staging not needed) |
| `turn_on/off_ams_dryer` | BambuPrinter direct (ephemeral) | BambuPrinter on managed session |
| `send_mqtt_command` | BambuPrinter direct (ephemeral) | `printer.send_anything()` on managed session |


## Knowledge Escalation Strategy

When the MCP's baked-in knowledge is insufficient to answer a question about Bambu Lab protocol, API, or firmware behavior, the agent must follow a **3-tier escalation path** — documented in `knowledge/fallback_strategy.py` and included verbatim in `prompts/context.py`.

### Tier 1 — Baked-in knowledge (always first)
Read the MCP's own `knowledge/` modules:
- `behavioral_rules` → operating rules, write protection, interface rules
- `protocol` → MQTT topics, telemetry semantics, HMS, firmware fields, SSDP, 3MF
- `enums` → all enum values + meanings
- `api_reference` → BambuPrinter method signatures, MCP tool mapping

**Tool:** `bambu://knowledge/*` resources; the synthesized context in `bambu_system_context` prompt.

### Tier 2 — Authoritative sources captured in rules files (primary fallback)
Search the **specific repositories listed in `knowledge/references.py`** — these are the exact sources the workspace rules files identify as authoritative. Use `search_authoritative_sources(query)` tool.

Priority order within Tier 2:
1. **BambuStudio** (`bambulab/BambuStudio`) — official client; protocol ground truth
2. **ha-bambulab / pybambu** (`greghesp/ha-bambulab`) — best field-level docs + edge cases
3. **OpenBambuAPI** (`Doridian/OpenBambuAPI`) — undocumented protocol coverage
4. **X1Plus** (`X1Plus/X1Plus`) — firmware internals, low-level telemetry
5. **OrcaSlicer** (`OrcaSlicer/OrcaSlicer`) — slicer integration, 3MF structure
6. **bambu-node** (`THE-SIMPLE-MARK/bambu-node`) — independent cross-language verification
7. **Bambu-HomeAssistant-Flows** (`WolfwithSword/Bambu-HomeAssistant-Flows`) — node-RED flow patterns

`search_authoritative_sources(query, repo_filter?)` takes a search query and optional repo scope; returns matched code/docs from the known reference set.

### Tier 3 — Broader search (last resort)
If Tier 2 yields no useful results, broaden to:
- GitHub code search across all repos (not filtered to known references)
- GitHub issue/PR search on the known repos
- Community sources: Home Assistant community forums, Bambu Lab developer community

Document clearly when Tier 3 is used — the answer is potentially less reliable.

### `knowledge/fallback_strategy.py`
Python module that exports:
- `ESCALATION_TIERS` — structured list of tier definitions (name, description, sources, tool to use)
- `AUTHORITATIVE_REPOS` — ordered list matching `references.py` with per-repo scope descriptions
- `ESCALATION_POLICY_TEXT` — verbatim text for inclusion in system prompt

### `tools/knowledge_search.py`
Two tools:
- `search_authoritative_sources(query, repo_filter=None)` — GitHub code search scoped to known reference repos; returns top matches with repo, path, and snippet
- `get_knowledge_topic(topic)` — returns the relevant `knowledge/` module section for a named topic (protocol, enums, api_reference, behavioral_rules, fallback_strategy)

## Knowledge Module Updates

### `knowledge/behavioral_rules.py`
Full sanitized synthesis of all three rules files. Rules included:
- ⚠️ **Rules Mandatory Rule** *(new — R3)*: "On every request, consult all knowledge/ modules and bambu://rules/* resources. Do not rely on cached assumptions from prior turns."
- ⚠️ **No Hardcoded Printer Metadata**: Never reference actual printer names, IPs, serials, or access codes in code or docs — always read from secrets_store at runtime
- ⚠️ **Printer Write Protection**: Absolute; no exceptions for any pretext or mode
- ⚠️ **Session Interface Rule** *(updated from R4)*: "Use session_manager's persistent session; never create BambuPrinter instances ad-hoc outside session_manager — the MCP IS the session layer"
- ⚠️ **Knowledge Escalation Rule** *(new — R5)*: Tier 1 → Tier 2 → Tier 3 escalation path
- **KISS Principle**: Simplest solution, no speculative architecture
- **Quality-First Mode**: Correctness over speed, verify before acting
- **Root Cause Fix Rule**: Fix the bug, not a workaround
- **Telemetry Mapping Parity Rule**: Sibling field baseline, steady-state truth
- **Verification First**: Verify claims in source, not from patterns
- **Cross-Model Compatibility Policy**: Preserve legacy behavior, BambuStudio baseline
- **Response Endings Rule**: No unsolicited follow-up offers
- **Rules Maintenance Rules**: How rules files should be updated (from rules files themselves)

### `knowledge/api_reference.py`
Complete API surface from both repos *(R1)*:

**From `bambu-printer-manager`:**
- Full BambuPrinter method inventory: all ~40 methods, param names/types/defaults, return types
- BambuConfig: all ~30 properties with types, defaults, setter behavior
- BambuState: all fields with telemetry source paths (e.g. `printer_state.gcode_state`, `active_job_info.print_percentage`)
- BambuDiscovery / BambuSpool / BambuProject / ProjectInfo / ActiveJobInfo: all fields + types
- `getPrinterSeriesByModel()` + all PrinterSeries/PrinterModel mappings

**From `bambu-printer-app` (all ~50 endpoint behaviors):**
- Every `/api/*` route: operation, BambuPrinter method it calls, parameters, return shape
- DataCollector behavior: collection names, interval, retention, pruning logic
- `gcode_state_durations` tracking: accumulation rule, reset trigger
- Session lifecycle: start_session, ServiceState transitions, on_update callback pattern

**Cross-reference:**
- MCP tool → BambuPrinter method mapping table (which tool calls which method)


### `knowledge/references.py`
The **definitive ordered list** of authoritative sources — the same sources captured in the workspace rules files:
- `BambuStudio` + GitHub URL + scope: official protocol, firmware, slicer integration
- `ha-bambulab (pybambu)` + GitHub URL + scope: field semantics, edge cases, HA integration
- `OpenBambuAPI` + GitHub URL + scope: undocumented protocol, reverse-engineered fields
- `X1Plus` + GitHub URL + scope: firmware internals, low-level telemetry, boot flow
- `OrcaSlicer` + GitHub URL + scope: slicer features, 3MF structure, plate handling
- `bambu-node` + GitHub URL + scope: cross-language verification
- `Bambu-HomeAssistant-Flows` + GitHub URL + scope: node-RED patterns

### `knowledge/fallback_strategy.py`
Exports `ESCALATION_TIERS`, `AUTHORITATIVE_REPOS` (ordered), and `ESCALATION_POLICY_TEXT` for inclusion in the system prompt. This is the structured form of the 3-tier escalation policy.

## Resources (unchanged URIs, updated content)

| URI | Content |
|---|---|
| `bambu://rules/global` | `~/.copilot/copilot-instructions.md` (redacts sensitive values) |
| `bambu://rules/printer-app` | `~/bambu-printer-app/.github/copilot-instructions.md` |
| `bambu://rules/printer-manager` | `~/bambu-printer-manager/.github/copilot-instructions.md` |
| `bambu://knowledge/behavioral-rules` | Sanitized all-rules synthesis |
| `bambu://knowledge/protocol` | MQTT + telemetry reference |
| `bambu://knowledge/enums` | All enum values with descriptions |
| `bambu://knowledge/api-reference` | BambuPrinter methods + MCP tool mapping |
| `bambu://knowledge/references` | Public GitHub reference implementations |
| `bambu://printers` | Configured printers from secrets_store (names + models; no IPs/serials/codes) |

## Client Configs

```json
// Claude Desktop: ~/Library/Application Support/Claude/claude_desktop_config.json
{
  "mcpServers": {
    "bambu-mcp": {
      "command": "/Users/shell/bambu-mcp/.venv/bin/python3",
      "args": ["/Users/shell/bambu-mcp/server.py"],
      "env": { "PYTHONPATH": "/Users/shell/bambu-mcp" }
    }
  }
}
```

```json
// Copilot CLI: ~/.copilot/mcp-config.json
{
  "mcpServers": {
    "bambu-mcp": {
      "command": "/Users/shell/bambu-mcp/.venv/bin/python3",
      "args": ["/Users/shell/bambu-mcp/server.py"],
      "env": { "PYTHONPATH": "/Users/shell/bambu-mcp" }
    }
  }
}
```

> **Note**: The server name is `bambu-mcp`. Both configs use the project-local `.venv`.
> Reference copies live at `config/claude_desktop.json` and `config/copilot_mcp.json`.

## Todos

- [x] mcp-setup: Create ~/bambu-mcp/ structure, pyproject.toml, .gitignore; copy PLAN.md from files/bambu-mcp.md; pip install -e .; verify bpm importable
- [x] mcp-secrets-store: secrets_store.py — Fernet (AES-256) over ~/.bambu-mcp/secrets.enc; get/set/list/delete; PBKDF2 key derivation; default password "changeit" from config/settings.toml; secrets CLI
- [x] mcp-auth: auth.py — get_printer_credentials(name) → {ip, access_code, serial} from secrets_store; no OS-specific deps
- [x] mcp-management: tools/management.py — add/remove/update/list printer tools; first-time setup flow
- [x] mcp-session-manager: session_manager.py — persistent BambuPrinter sessions, start/stop/get
- [x] mcp-data-collector: data_collector.py — self-contained wrapper; subscribes to on_update, reads BambuState directly, maintains telemetry history + gcode_state_durations, get_all_data()
- [x] mcp-knowledge-rules: knowledge/behavioral_rules.py — all rules, updated interface rule
- [x] mcp-knowledge-protocol: knowledge/protocol.py — MQTT, HMS, firmware, SSDP, 3MF, telemetry
- [x] mcp-knowledge-enums: knowledge/enums.py — all bpm enums imported + documented
- [x] mcp-knowledge-api: knowledge/api_reference.py — BambuPrinter methods + MCP tool mapping
- [x] mcp-knowledge-refs: knowledge/references.py — ordered authoritative sources list
- [x] mcp-knowledge-fallback: knowledge/fallback_strategy.py — 3-tier escalation policy + ESCALATION_POLICY_TEXT
- [x] mcp-tools-state: tools/state.py — 12 read tools from session_manager state
- [x] mcp-tools-print: tools/print_control.py — 6 tools via BambuPrinter methods
- [x] mcp-tools-climate: tools/climate.py — 6 tools
- [x] mcp-tools-filament: tools/filament.py — 9 tools (dryer on managed session, not ephemeral)
- [x] mcp-tools-nozzle: tools/nozzle.py — 3 tools
- [x] mcp-tools-detectors: tools/detectors.py — 5 tools
- [x] mcp-tools-files: tools/files.py — 8 tools via BambuPrinter FTPS
- [x] mcp-tools-system: tools/system.py — 8 tools (session control + data_collector zoom + logging)
- [x] mcp-tools-discovery: tools/discovery.py — BambuDiscovery SSDP (ephemeral, leads to add_printer flow)
- [x] mcp-tools-commands: tools/commands.py — send_anything on managed session, user_permission gate
- [x] mcp-tools-knowledge: tools/knowledge_search.py — search_authoritative_sources() + get_knowledge_topic()
- [x] mcp-resources: resources/rules.py + resources/knowledge.py (9 bambu:// URIs)
- [x] mcp-prompts: prompts/context.py — bambu_system_context: full synthesis including ESCALATION_POLICY_TEXT verbatim
- [x] mcp-server: server.py — FastMCP wiring, session lifecycle, stdio transport
- [x] mcp-config: Claude Desktop + Copilot CLI configs installed
- [x] mcp-readme: README.md — tool reference, setup, write protection, no-container design
- [x] mcp-test: Smoke-test all tools directly against live printers; verify no container calls

---

## Post-Plan Additions

The following were added after the initial plan was written, based on user requests during implementation and testing.

### PA1 — Visual plate inspection tools (`tools/files.py`)

Two tools added to `tools/files.py` (total 8 → 10):

| Tool | Description |
|---|---|
| `open_plate_viewer(name, file_path)` | Builds and opens an HTML page showing the isometric thumbnail + top-down image for every plate in a 3MF. Embeds base64 images directly; opens in default browser. Returns `{output_path, plate_count}`. |
| `open_plate_layout(name, file_path, plate_num)` | Generates an annotated top-down PNG overlaying each object's bounding box on the `topimg`. Applies slicer-to-pixel coordinate transform (Y-flip, uniform scale, centring). Appends a colour-coded legend. Opens in default viewer. Returns `{output_path, object_count}`. |

**Coordinate mapping** (baked into `_build_layout_uri` / `open_plate_layout`):
- Slicer bbox: bottom-left origin, mm. Image: top-left origin, pixels.
- `scale = min(img_w / bed_w, img_h / bed_h)` — uniform, no distortion.
- Bed sizes by model (W×H mm): H2D/H2S=350×320, X1C/X1/X1E/P1S/P1P/P2S/A1=256×256, A1_MINI=180×180.

### PA2 — `make.py` project-local venv installer

Added `make.py` to the project root. Creates `~/bambu-mcp/.venv/` (project-local virtualenv) and pip-installs the package editable. Replaces the earlier practice of using `~/.virtualenvs/main`.

All client configs updated to reference `.venv/bin/python3`.

### PA3 — Extended `tools/system.py` surface

Four tools present in `system.py` that were not individually called out in the plan:

| Tool | Description |
|---|---|
| `get_firmware_version(name)` | Returns firmware + AMS firmware version from `printer.config`. |
| `trigger_printer_refresh(name)` | Sends ANNOUNCE_VERSION + ANNOUNCE_PUSH via MQTT; requires `user_permission`. |
| `force_state_refresh(name)` | Sends push_all / ANNOUNCE_PUSH without permission gate (read-triggering only). |
| `set_print_options(name, auto_recovery, sound)` | Sets `auto_recovery` and/or `sound` option flags via MQTT; requires `user_permission`. |

### PA4 — `get_monitoring_history` in `tools/system.py`

`get_monitoring_history(name)` mirrors the same data as `get_monitoring_data` in `state.py` but is exposed under `system.py` as a history-oriented alias. Both delegate to `data_collector.get_all_data(name)`.

---

### PA5 — Server name corrected to `bambu-mcp`

All MCP config references renamed from `"bambu"` to `"bambu-mcp"` to match the project name:

- `config/claude_desktop.json` — `mcpServers` key updated
- `config/copilot_mcp.json` — `mcpServers` key updated
- `~/.copilot/mcp-config.json` — live Copilot CLI config updated

Correct config (all three files use this shape):

```json
{
  "mcpServers": {
    "bambu-mcp": {
      "command": "/Users/shell/bambu-mcp/.venv/bin/python3",
      "args": ["/Users/shell/bambu-mcp/server.py"],
      "env": { "PYTHONPATH": "/Users/shell/bambu-mcp" }
    }
  }
}
```

---

### PA6 — Auto-discovery welcoming action

Added a `⚠️ Session Start — Auto-Discovery Rule` to `knowledge/behavioral_rules.py`:

> At the start of every session, call `get_configured_printers()` immediately. If the list
> is empty, automatically call `discover_printers()` without waiting for the user to ask.
> Never sit idle or say "No printers configured" without first running discovery.

Also added the equivalent `## Session Start Protocol` block to `prompts/context.py` so
the system prompt enforces the same behaviour at session start.

---

### PA7 — `tools/files.py` platform portability

Both `open_plate_viewer` and `open_plate_layout` previously launched files using
`subprocess.Popen(["open", ...])`, which is macOS-only.

Replaced with `webbrowser.open(f"file://{out_path}")` — Python stdlib, works on macOS,
Linux, and Windows without any extra dependencies.

---

### PA8 — Camera / live streaming integration

**New module: `camera/`**

All streaming complexity is self-contained within the MCP — no system ffmpeg required.
`av` (PyAV) bundles libav natively as a pip wheel.

| File | Purpose |
|---|---|
| `camera/__init__.py` | Package marker |
| `camera/protocol.py` | Model → protocol routing: `RTSPS_MODELS` (X1C/X1/X1E/P2S/H2D/H2S), `TCP_TLS_MODELS` (A1/A1_MINI/P1P/P1S) |
| `camera/rtsps_stream.py` | PyAV RTSPS client (port 322, TLS cert disabled); H264 → yuvj420p → MJPEG via av re-encode |
| `camera/tcp_stream.py` | Pure-Python TLS binary client (port 6000); 64-byte auth packet + 16-byte frame header; no external deps |
| `camera/mjpeg_server.py` | stdlib `http.server` MJPEG server; serves `/stream` as `application/octet-stream` (Safari compat); JS fetch-based multipart parser in HTML page; port allocation from 8090; `MJPEGServer` singleton + module-level `mjpeg_server` |

**New tool module: `tools/camera.py`** — 5 tools:

| Tool | Description |
|---|---|
| `get_snapshot(name)` | Connect, grab one JPEG frame, disconnect. Returns `data_uri` (complete `data:image/jpeg;base64,...`), `width`, `height`, `protocol`, `timestamp`. |
| `get_stream_url(name)` | Return URL info without connecting to camera. Returns `protocol`, `rtsps_url` (X1/H2D only), `local_mjpeg_url`, `streaming`. |
| `start_stream(name, port?)` | Start MJPEG HTTP server. Returns `url`, `port`, `protocol`. Idempotent — returns existing URL if already running. |
| `stop_stream(name)` | Stop MJPEG server. Returns `{stopped: bool, name: str}`. |
| `view_stream(name)` | `start_stream` + `webbrowser.open(url)`. Returns `url`, `port`, `protocol`, `opened`. |

**Protocol details:**

RTSPS (X1/H2D, port 322):
- URL: `rtsps://bblp:{access_code}@{ip}:322/streaming/live/1`
- TLS cert verification disabled (Bambu self-signed CA)
- Decoded via PyAV: `av.open(url, options={"rtsp_transport": "tcp", "tls_verify": "0"})`
- Frame extraction: H264 decode → reformat to `yuvj420p` → encode as MJPEG

TCP+TLS binary (A1/P1, port 6000):
- TLS cert verification disabled
- Auth packet: `struct.pack("<4I", 0x40, 0x3000, 0, 0)` + `b"bblp".ljust(32)` + `access_code.encode().ljust(32)`
- Frame header: `struct.unpack("<4I", 16_bytes)` → `(jpeg_size, 0, 1, 0)`
- JPEG magic: starts `\xff\xd8`, ends `\xff\xd9`

**`server.py` changes:**
- `tools.camera` added to `_TOOL_MODULES`
- `camera.mjpeg_server.mjpeg_server.stop_all()` added to `_shutdown()`

**`pyproject.toml` change:**
- `"av>=14.0"` added to dependencies (av-16.1.0 installs, bundles libav)

**Smoke test results:**
- H2D (RTSPS): 83KB JPEG, 1680×1080
- A1 (TCP+TLS): 174KB JPEG, 1920×1080
- Both MJPEG servers: `application/octet-stream` stream endpoint; JS fetch multipart parser; Safari + Chrome first-load confirmed
- H2D multi-client: `RTSPSFrameBuffer` single-thread PyAV architecture (prevents libav segfault)
- A1 multi-client: `TCPFrameBuffer` (mirrors webcamd); Safari + Chrome + multiple tabs confirmed

---

### PA9 — Zero-knowledge AI client standard

Applied the "zero-knowledge AI client" standard across all knowledge and context modules.
The requirement: any AI consuming this MCP needs NO prior knowledge of Bambu Lab, 3D
printing, MQTT, AMS, HMS, or streaming protocols.

**`knowledge/protocol.py`** — prepended a 28-entry Concepts & Terminology glossary
covering: Bambu Lab, FDM, filament, nozzle, bed, AMS, spool, HMS, G-code, gcode_state
(8 values + semantics), Stage (full integer table), MQTT, push_status, bitfields, SSDP,
FTPS, 3MF, plates, timelapse, xcam, RTSPS, TCP+TLS binary camera protocol (full auth
packet + frame header layout), MJPEG, Access Code, LAN Mode.

**`knowledge/enums.py`** — added 1–2 sentence introductory paragraphs before 14 enum
family headings explaining what each enum represents and when to use it.

**`knowledge/behavioral_rules.py`** — added two new sections:
1. `## What bambu-mcp Is` — explains MCP server purpose, MQTT/FTPS/camera architecture,
   session model, write protection rationale, credential security.
2. `## Camera Usage Rules` — when to proactively offer camera tools, how to choose the
   right tool (snapshot vs. view_stream vs. start_stream), cleanup discipline,
   availability checking, `data_uri` handling.

**`prompts/context.py`** — expanded with:
1. `## About bambu-mcp` primer block: "What is a Bambu Lab printer?", key concepts
   (filament, AMS, nozzle, bed, gcode_state, HMS, xcam, SD card, 3MF, access code),
   how to identify a printer by `name`, write protection summary.
2. Per-tool-group context sentences: one sentence before each tool group heading
   explaining its purpose and safety profile.
3. `### Camera (tools/camera.py)` block in Tool Inventory with all 5 tools documented.

**Tool docstrings** — 44 surgical additions across all 12 non-camera tool files:
speed level meanings, all `set_print_option` options explained, bed type values,
`use_ams` behaviour, AMS dryer unsupported-model behaviour, RFID/remaining calibration,
detector names and behaviours (buildplate marker, purge chute pile-up, nozzle clumping,
first layer LiDAR), tray state enum values, `flow_type` metadata-only caveat,
session `connected` vs `session_active` distinction, `update_printer_credentials`
effect on active prints, `set_chamber_temp` "injects" ambiguity resolved,
discovery `bind_state`/`connect_state` defined, and more.

**README.md** — updated tool count (66 → 73), install instructions (make.py),
server key name (`"bambu"` → `"bambu-mcp"`), Camera row in tool categories table,
architecture file tree updated with `camera/` module and corrected tool counts.
