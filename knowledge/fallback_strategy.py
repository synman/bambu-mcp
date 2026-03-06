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
        "name": "Baked-in Knowledge",
        "description": (
            "Check the MCP's own knowledge/ modules first: behavioral_rules, protocol, "
            "enums, api_reference, fallback_strategy. Access via bambu://knowledge/* resources "
            "or the bambu_system_context prompt. This is always the first step."
        ),
        "tool": "bambu://knowledge/* resources or bambu_system_context prompt",
        "reliability": "Highest — curated, verified, sanitized",
    },
    {
        "tier": 2,
        "name": "Authoritative Sources (from rules files)",
        "description": (
            "Search the specific repositories identified as authoritative in the workspace "
            "rules files. Use search_authoritative_sources(query) scoped to the known "
            "reference repos. These sources are the ground truth for protocol behavior."
        ),
        "tool": "search_authoritative_sources(query, repo_filter=None)",
        "sources": [r["name"] for r in REFERENCES],
        "reliability": "High — official and community-verified implementations",
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
        "name": "Broader Search (last resort)",
        "description": (
            "If Tier 1 and Tier 2 yield no useful result, broaden to GitHub code search "
            "across all repositories (not filtered), GitHub issue/PR search on known repos, "
            "and community sources (Home Assistant community, Bambu Lab developer forums)."
        ),
        "tool": "search_authoritative_sources(query) with no repo_filter, or web search",
        "reliability": "Variable — always note when Tier 3 was used; flag answer as potentially less reliable",
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
Read the knowledge/ modules via bambu://knowledge/* resources:
- behavioral_rules — operating rules, write protection, interface rules
- protocol — MQTT topics, telemetry semantics, HMS, firmware, SSDP, 3MF
- enums — all enum values and meanings
- api_reference — BambuPrinter method signatures and MCP tool mapping

If Tier 1 answers the question fully, stop here.

### Tier 2 — Authoritative Sources from Rules Files (primary fallback)
Use `search_authoritative_sources(query)` scoped to the repositories identified
in the workspace rules files as authoritative. Search in this priority order:

1. BambuStudio (bambulab/BambuStudio) — official protocol, firmware, slicer
2. ha-bambulab/pybambu (greghesp/ha-bambulab) — field semantics, edge cases
3. OpenBambuAPI (Doridian/OpenBambuAPI) — undocumented protocol
4. X1Plus (X1Plus/X1Plus) — firmware internals, low-level telemetry
5. OrcaSlicer (OrcaSlicer/OrcaSlicer) — slicer, 3MF, plate handling
6. bambu-node (THE-SIMPLE-MARK/bambu-node) — cross-language verification
7. Bambu-HomeAssistant-Flows (WolfwithSword/Bambu-HomeAssistant-Flows) — node-RED

If Tier 2 answers the question, note the source and proceed.

### Tier 3 — Broader Search (last resort)
If both Tier 1 and Tier 2 fail to provide an answer:
- Broaden GitHub code search to all repositories (no repo filter)
- Search GitHub issues/PRs on the known reference repos
- Search community sources (Home Assistant community, Bambu Lab forums)

**Always flag Tier 3 answers as potentially less reliable.**
""".strip()
