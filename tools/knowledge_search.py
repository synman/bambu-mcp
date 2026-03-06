"""
tools/knowledge_search.py — Knowledge escalation tools for Bambu Lab protocol/API work.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

_KNOWN_TOPICS: dict[str, tuple[str, str]] = {
    "behavioral_rules": ("knowledge.behavioral_rules", "BEHAVIORAL_RULES_TEXT"),
    "protocol":         ("knowledge.protocol",          "PROTOCOL_TEXT"),
    "enums":            ("knowledge.enums",             "ENUMS_TEXT"),
    "api_reference":    ("knowledge.api_reference",     "API_REFERENCE_TEXT"),
    "references":       ("knowledge.references",        "REFERENCES_TEXT"),
    "fallback_strategy":("knowledge.fallback_strategy", "ESCALATION_POLICY_TEXT"),
}


def search_authoritative_sources(
    query: str,
    repo_filter: str | None = None,
) -> dict:
    """
    Return structured guidance on searching the authoritative Bambu Lab repos for a query.

    Lists which repos to search in priority order (Tier 1 → Tier 3) with GitHub
    search URL patterns. If repo_filter is provided, narrows guidance to that repo.
    This tool does not perform the actual search — it provides instructions and URLs
    for the caller to execute. Use search_code or a browser for the actual lookup.
    Follows a 3-tier escalation policy: Tier 1 = baked-in knowledge modules (fastest,
    offline). Tier 2 = authoritative repos (BambuStudio, ha-bambulab, OpenBambuAPI).
    Tier 3 = broad web/GitHub search (last resort). This tool returns guidance for
    Tier 2+ searches — it does not perform the search itself.
    """
    log.debug("search_authoritative_sources: query=%s repo_filter=%s", query, repo_filter)
    from knowledge.fallback_strategy import AUTHORITATIVE_REPOS

    repos = AUTHORITATIVE_REPOS
    if repo_filter:
        repos = [
            r for r in repos
            if repo_filter.lower() in r["name"].lower()
            or repo_filter.lower() in r.get("repo", "").lower()
        ]
        if not repos:
            return {
                "error": f"No repos matched filter '{repo_filter}'",
                "available_repos": [r["name"] for r in AUTHORITATIVE_REPOS],
            }

    encoded_query = query.replace(" ", "+")
    search_guidance = []
    for r in repos:
        repo_path = r.get("repo", "")
        search_guidance.append({
            "name": r["name"],
            "url": r.get("url", ""),
            "repo": repo_path,
            "scope": r.get("scope", ""),
            "github_search_url": (
                f"https://github.com/search?q={encoded_query}+repo:{repo_path}&type=code"
                if repo_path else ""
            ),
            "github_code_search": (
                f"https://github.com/{repo_path}/search?q={encoded_query}"
                if repo_path else ""
            ),
        })

    log.debug("search_authoritative_sources: returning %d repos", len(search_guidance))
    return {
        "query": query,
        "repo_filter": repo_filter,
        "search_guidance": search_guidance,
        "instructions": (
            "Search each repo in priority order. Prefer Tier 1 (official vendor) sources first. "
            "Use github_search_url for GitHub code search or clone locally for grep. "
            "Verify field semantics using steady-state status payloads, not command acks."
        ),
    }

def get_knowledge_topic(topic: str) -> dict | str:
    """
    Return the full text of a knowledge module by topic name.

    topic must be one of: behavioral_rules, protocol, enums, api_reference,
    references, fallback_strategy. Returns a list of available topics if the
    given topic is not recognized.
    Returns the full text of the named knowledge module as a string. Returns a list
    of available topic names if the given topic is not recognized.
    """
    log.debug("get_knowledge_topic: topic=%s", topic)
    if topic not in _KNOWN_TOPICS:
        log.warning("get_knowledge_topic: unknown topic '%s'", topic)
        return {
            "error": f"Unknown topic '{topic}'",
            "available_topics": list(_KNOWN_TOPICS.keys()),
        }
    module_name, attr_name = _KNOWN_TOPICS[topic]
    try:
        import importlib
        log.debug("get_knowledge_topic: loading module %s", module_name)
        mod = importlib.import_module(module_name)
        text = getattr(mod, attr_name, None)
        if text is None:
            return {"error": f"Attribute '{attr_name}' not found in module '{module_name}'"}
        log.debug("get_knowledge_topic: returning text length=%d", len(text) if text else 0)
        return text
    except Exception as e:
        return {"error": f"Error loading knowledge topic '{topic}': {e}"}
