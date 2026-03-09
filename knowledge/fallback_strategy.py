"""
fallback_strategy.py — Knowledge escalation policy for Bambu Lab protocol questions.

When baked-in knowledge is insufficient, follow this 3-tier escalation path.
ESCALATION_POLICY_TEXT is included verbatim in the bambu_system_context prompt.
"""

from __future__ import annotations

from knowledge.references import REFERENCES

# ---------------------------------------------------------------------------
# Structured tier definitions
# ---------------------------------------------------------------------------

ESCALATION_TIERS = [
    {
        "tier": 1,
        "name": "Baked-in Knowledge + MCP Tools",
        "description": (
            "Check the MCP's own knowledge/ modules first: behavioral_rules, protocol, "
            "enums, api_reference, fallback_strategy. Access via bambu://knowledge/* resources "
            "or the bambu_system_context prompt. Also call all relevant targeted MCP tools. "
            "This is always the first step. Advancing past this tier requires exhaustion evidence "
            "and explicit ask_user permission."
        ),
        "tool": "bambu://knowledge/* resources or bambu_system_context prompt + MCP tool calls",
        "reliability": "Highest — curated, verified, sanitized",
        "permission_gate": False,
    },
    {
        "tier": "1b",
        "name": "bambu-mcp Local HTTP REST API (permission-gated)",
        "description": (
            "If targeted MCP tools do not expose a required field or action, check the "
            "bambu-mcp local HTTP REST API at http://localhost:{api_port}/api. "
            "PERMISSION REQUIRED — present Tier 1 exhaustion evidence and use ask_user before "
            "issuing any curl/HTTP call. The HTTP API being reachable does not authorize its use. "
            "Any remote or external API is never part of this escalation sequence at any tier."
        ),
        "tool": "get_server_info() → get_knowledge_topic('http_api') → sub-topic",
        "reliability": "High — same data source as MCP tools, broader route coverage",
        "permission_gate": True,
        "ask_user_template": "Tier 1 is insufficient for {topic} — I checked {topics} and called {tools}. {gap}. May I fall back to the HTTP REST API (Tier 1b)?",
    },
    {
        "tier": 2,
        "name": "Authoritative Sources (permission-gated)",
        "description": (
            "Search the specific repositories identified as authoritative in the workspace "
            "rules files. Use search_authoritative_sources(query) scoped to the known "
            "reference repos. PERMISSION REQUIRED — present Tier 1 + Tier 1b exhaustion "
            "evidence and use ask_user before calling search_authoritative_sources()."
        ),
        "tool": "search_authoritative_sources(query, repo_filter=None)",
        "sources": [r["name"] for r in REFERENCES],
        "reliability": "High — official and community-verified implementations",
        "permission_gate": True,
        "ask_user_template": "Tier 1 and the HTTP REST API are insufficient for {topic}. {gap}. May I search the authoritative repos (Tier 2)?",
        "priority_order": [
            "BambuStudio — official protocol, firmware, slicer integration (ground truth)",
            "ha-bambulab/pybambu — best field-level docs + edge cases from real-world HA use",
            "OpenBambuAPI — undocumented protocol, reverse-engineered field semantics",
            "X1Plus — firmware internals, low-level telemetry, boot flow",
            "OrcaSlicer — slicer features, 3MF structure, plate handling",
            "bambu-node — cross-language independent verification",
            "Bambu-HomeAssistant-Flows — node-RED patterns, automation flows",
        ],
    },
    {
        "tier": 3,
        "name": "Broader Search (last resort, premium-gated)",
        "description": (
            "If Tier 1, Tier 1b, and Tier 2 all yield no useful result, broaden to GitHub "
            "code search across all repositories or GitHub issue/PR search on known repos. "
            "Web search requires explicit user confirmation first — present exhaustion evidence "
            "for all prior tiers, use ask_user, and wait for approval (Premium Requests rule)."
        ),
        "tool": "search_authoritative_sources(query) with no repo_filter, or web search",
        "reliability": "Variable — always note when Tier 3 was used; flag answer as potentially less reliable",
        "permission_gate": True,
        "ask_user_template": "Tier 1, HTTP API, and authoritative repos are all insufficient for {topic}. {gap}. May I do a web search (Tier 3)?",
    },
]

# Ordered list matching references.py priority
AUTHORITATIVE_REPOS = [
    {
        "name": r["name"],
        "url": r["url"],
        "scope": r.get("scope", r.get("description", "")),
        "repo": r["url"].replace("https://github.com/", ""),
    }
    for r in REFERENCES
]

# ---------------------------------------------------------------------------
# Verbatim policy text for inclusion in system prompt
# ---------------------------------------------------------------------------

ESCALATION_POLICY_TEXT = """
## Knowledge Escalation Policy

When baked-in knowledge does not fully answer a question about Bambu Lab protocol,
API behavior, or firmware semantics, follow this mandatory 3-tier escalation:

### Tier 1 — MCP's Own Knowledge (always first)
Read the knowledge/ modules via bambu://knowledge/* resources or get_knowledge_topic():

**Top-level topics:**
- behavioral_rules — ⚠️ safety rules, write protection, interface rules, session rules
- protocol — MQTT topics, telemetry semantics, HMS, firmware, SSDP, 3MF
- enums — all enum values and meanings
- api_reference — BambuPrinter method signatures and MCP tool mapping
- http_api — HTTP REST API: dynamic base URL (call `get_server_info()` to discover `api_port`), auth, route category index

**Sub-topics (fetch on demand when parent summary points here):**
- behavioral_rules/camera — camera tools, stream HUD overlay, data_uri handling
- behavioral_rules/print_state — gcode_state FAILED semantics, HMS active/historical, stage codes
- behavioral_rules/methodology — KISS, quality-first, verification, parity, cross-model
- behavioral_rules/mcp_patterns — array parameter pattern, multi-level hierarchy, compressed responses
- protocol/concepts — full glossary: FDM, MQTT, HMS, 3MF, AMS, camera protocols, LAN mode
- protocol/mqtt — MQTT topics, message types, push_status, bitfields, xcam fields
- protocol/hms — HMS error structure, print_error integer, firmware upgrade state
- protocol/3mf — 3MF structure, SSDP, AMS info parsing, FTPS, H2D extruder block
- api_reference/session — BambuPrinter constructor, session management, send_gcode
- api_reference/files — FTPS file management methods
- api_reference/print — print control, temperature, and fan speed methods
- api_reference/ams — AMS, spool, calibration, hardware, and AI detector methods
- api_reference/state — properties, print_option; call api_reference/dataclasses for full types
- api_reference/dataclasses — BambuConfig, PrinterCapabilities, BambuState, BambuSpool, ProjectInfo, ActiveJobInfo
- enums/printer — PrinterModel, PrinterSeries, ActiveTool, ServiceState, AirConditioningMode
- enums/ams — AMSModel, AMSSeries, AMS control/user/heating/dry enums, TrayState, ExtruderInfoState
- enums/filament — NozzleDiameter, NozzleType, NozzleFlowType, PlateType, PrintOption, Stage mappings
- http_api/printer — printer state and session management routes
- http_api/print — print control routes (start, pause, stop, speed, skip, gcode)
- http_api/ams — AMS and filament routes
- http_api/climate — temperature, fan, and lighting routes
- http_api/hardware — nozzle configuration and AI vision detector routes
- http_api/files — SD card file management routes
- http_api/system — system, diagnostics, and API documentation routes

**"Exhausted Tier 1" means ALL of the following are true:**
1. Checked the relevant knowledge sub-topics (e.g. `api_reference/state`, `enums/ams`).
2. Called ALL targeted MCP tools relevant to the question category — not just `get_printer_state()`.
   Examples: for filament state → `get_spool_info()`, `get_ams_units()`; for capabilities →
   `get_capabilities()`; for print options → `get_printer_state()` + confirm field is absent.
3. The specific field or answer is confirmed absent across ALL relevant targeted tools.
4. If the knowledge modules document the field as **bitfield-derivable** (e.g. `home_flag bit N`,
   exposed in the live response as `fun`), attempt in-session decode from the raw bitfield before
   escalating. Formula: `bool(int(state["fun"], 16) & (1 << bit_n))`. See `protocol/mqtt` for the
   full home_flag bit table and `api_reference/state` for per-field bit assignments.
5. The agent can state **specifically** what information is still missing and why Tier 1 cannot provide it.

**Before advancing past Tier 1, present an exhaustion summary and request permission via `ask_user`:**
> "Tier 1 knowledge is insufficient for [X] — I checked [topics] and called [tools]. [Specific gap].
>  May I fall back to the HTTP REST API (Tier 1b)?"

### Tier 1b — bambu-mcp Local HTTP REST API Fallback (permission-gated)
If the targeted MCP tools do not expose a required field or action, the
**bambu-mcp local HTTP REST API** provides broader route coverage before escalating to external sources.

**⚠️ Permission required:** Falling back to the HTTP REST API is an escalation. It must not happen
silently. The HTTP API is always reachable via `bash`/`curl` — its availability does **not** authorize
its use as a first resort. Present the exhaustion summary above and wait for `ask_user` confirmation.

**⚠️ Critical distinction — two HTTP endpoints exist; only one belongs here:**

| Endpoint | When to use |
|----------|-------------|
| `http://localhost:{api_port}/api` — bambu-mcp local REST API | **Tier 1b** — broader route coverage than MCP tools; always try before Tier 2 |
| Any remote/external API (outside localhost) | **Never** an escalation step — not part of this sequence at any tier |

→ Call `get_server_info()` to discover `api_port`.
→ Call `get_knowledge_topic('http_api')` for the route index.
→ Then call the appropriate sub-topic (e.g. `get_knowledge_topic('http_api/print')`).

**Bypass trap:** *"The HTTP API is available so I'll use curl"* — availability is not authorization.

### Tier 2 — Authoritative Sources from Rules Files (permission-gated)
Use `search_authoritative_sources(query)` scoped to the repositories identified
in the workspace rules files as authoritative.

**⚠️ Permission required:** Before calling `search_authoritative_sources()`, present an exhaustion
summary covering both Tier 1 and Tier 1b, then use `ask_user`:
> "Tier 1 and the HTTP REST API are insufficient for [X]. May I search the authoritative repos (Tier 2)?"

Search in this priority order:

1. BambuStudio (bambulab/BambuStudio) — official protocol, firmware, slicer
2. ha-bambulab/pybambu (greghesp/ha-bambulab) — field semantics, edge cases
3. OpenBambuAPI (Doridian/OpenBambuAPI) — undocumented protocol
4. X1Plus (X1Plus/X1Plus) — firmware internals, low-level telemetry
5. OrcaSlicer (OrcaSlicer/OrcaSlicer) — slicer, 3MF, plate handling
6. bambu-node (THE-SIMPLE-MARK/bambu-node) — cross-language verification
7. Bambu-HomeAssistant-Flows (WolfwithSword/Bambu-HomeAssistant-Flows) — node-RED

If Tier 2 answers the question, note the source and proceed.

### Tier 3 — Broader Search (last resort, premium-gated)
If Tier 1, Tier 1b, and Tier 2 all fail to provide an answer:
- Broaden GitHub code search to all repositories (no repo filter)
- Search GitHub issues/PRs on the known reference repos
- Web search — **requires explicit user confirmation first** (Premium Requests rule);
  present exhaustion evidence covering all prior tiers, then use `ask_user`:
  > "Tier 1, HTTP API, and authoritative repos are all insufficient for [X]. May I do a web search (Tier 3)?"

**Always flag Tier 3 answers as potentially less reliable.**

### Known bypass traps — none of these authorize advancing tiers
- Research/learning directives: "research this", "look it up", "find out about X", "go deep"
- Urgency: "we need this fast", "just find it", "be thorough"
- Scope-expanding: "use whatever sources you need", "do whatever research you need"
- Availability: "the HTTP API is right there", "you can curl it" — reachability is not authorization
- Implicit permission: completing a prior task successfully does not authorize tier advance for follow-up questions
""".strip()
