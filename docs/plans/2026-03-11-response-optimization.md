# bambu-mcp: Response Size Optimization Plan
**Date:** 2026-03-11 · **Repo:** `~/bambu-mcp` · **Baseline:** `rest-compliance-printer-param-2` v1.0.3

---

## Executive Summary

Two complementary features to reduce MCP token consumption and eliminate overflow failures:

| Feature | Scope | Impact | Risk |
|---------|-------|--------|------|
| **Selective URL Factory** | 1 confirmed + 2 optional tools | Eliminates overflow for `get_snapshot` at native; reduces context burn for large monitoring/project tools | Low — narrow scope |
| **Lower Gzip Threshold** | All tools returning >300 chars | 3×–5× token reduction for medium responses (`get_knowledge_topic`, AMS state, spool info) | Very low — one constant |

---

## Background: What Was Tried and Why It Was Reverted

### Full URL Factory (Reverted at `5d78bdf`)

All 39 READ tools returned `{"url": "http://localhost:<port>/api/..."}` instead of data. Architecture was sound — scope was too broad. Even tiny tools like `get_temperatures` (3-field dict) required two HTTP round trips. User felt perceptible slowness. **Reverted.**

### Current Architecture (post-revert)

```
MCP Tool Call
     │
     ├─ compress_if_large()        ← gzip+b64 if char_count > MAX_MCP_CHARS (282,904)
     │   ├─ binary exemption        ← skips gzip for data: URIs (images already compressed)
     │   └─ ResponseSizeTracker     ← auto-tunes MAX_MCP_OUTPUT_TOKENS in mcp-config.json
     │
     └─ return data or compressed envelope
```

Constants:
- `MAX_MCP_OUTPUT_TOKENS = 70726` (from `~/.copilot/mcp-config.json`)
- Char threshold = `70726 × 4 = 282,904`
- `compress_if_large` fires only when `char_count > 282,904`
- Below that: data returned **raw and uncompressed** — even if 118K chars

**The gap:** Tools like `monitoring_series` (118K chars = 29K tokens) and `get_knowledge_topic` (19K chars = 4.7K tokens) are returned raw because they fall under the 282K threshold — even though they'd compress 4× and save significant context.

---

## Empirical Payload Measurements

> All measurements against live H2D (idle). Threshold = **282,904 chars**.

### 📸 Snapshot — `get_snapshot`

| Resolution / Quality | Response (chars) | % of Limit | Fits? |
|----------------------|-----------------|------------|-------|
| 180p / q55 | 10,418 | 3.7% | ✅ |
| 360p / q65 | 34,938 | 12.3% | ✅ |
| 480p / q65 | 55,734 | 19.7% | ✅ |
| 720p / q75 | 122,747 | 43.3% | ✅ |
| 1080p / q85 | 263,306 | 92.9% | ✅ barely |
| native / q85 (idle) | 135,572 | 47.9% | ✅ |
| **native / q85 (active print)** | **~5,000,000** | **1765%** | ❌ **OVERFLOWS** |

**Key insight:** During active prints, the camera stream is full-quality JPEG — documented at ~4MB. This is the *only* tool that reliably overflows under real use conditions.

### 📋 Project Info — `get_project_info`

Files tested: H2D riser **(14 plates, 66MB)** and reptile stuff **(3 plates, 31MB)**.

| File / Plate | Raw (chars) | gz+b64 (chars) | Compression | % of Limit | Fits? |
|--------------|-------------|----------------|-------------|------------|-------|
| riser plate=1 | 22,146 | 19,987 | 0.90× | 7.8% | ✅ |
| riser plate=7 | 21,942 | 18,955 | 0.86× | 7.7% | ✅ |
| riser plate=14 | 15,450 | 12,659 | 0.82× | 5.5% | ✅ |
| reptile plate=1 | 103,441 | 99,967 | 0.97× | 36.5% | ✅ |
| reptile plate=2 | 139,534 | 136,291 | 0.98× | 49.2% | ✅ |
| **reptile plate=3** | **185,812** | **182,703** | **0.98×** | **65.5%** | ✅ (46K tokens!) |

> **Why gzip barely helps:** Response dominated by embedded `thumbnail` + `topimg` base64 fields (75K + 109K chars for reptile plate=3). JPEG/PNG base64 is pre-compressed — gzip achieves only 0.97–0.98×.

> **Bug found:** HTTP route `GET /api/get_3mf_props_for_file` ignores `include_images=false` — always embeds thumbnails. MCP tool correctly strips them; HTTP route does not. **Must fix before any URL factory for this tool.**

### 📊 Monitoring Tools

| Tool | Raw (chars) | gz+b64 (chars) | Compression | % of Limit | Fits? |
|------|-------------|----------------|-------------|------------|-------|
| `monitoring_history(raw=True)` | 282,901 | ~55,000 | **0.19×** | **99.999%** | ✅ (3 chars from edge!) |
| `monitoring_series(bed)` | 118,417 | 27,183 | 0.23× | 41.8% | ✅ (returned raw — below threshold!) |
| `monitoring_history(raw=False)` | ~500 | ~350 | — | <1% | ✅ trivial |

> `monitoring_history(raw=True)` at 282,901 chars is **3 chars under the overflow threshold**. A busier printer will push it over. It compresses 5× with gzip — `compress_if_large` currently saves it, but only barely.

> `monitoring_series` at 118K chars is NOT compressed today (below 282K threshold). It consumes 29K tokens raw. After gzip threshold change, it compresses to 27K chars — a 77% saving.

### 🖥️ Other State Tools

| Tool | Raw (chars) | gz+b64 | Compression | Notes |
|------|-------------|--------|-------------|-------|
| `get_printer_state` H2D | 7,638 | 2,955 | 0.39× | Original estimates were ~5× too high |
| `list_sdcard_files` H2D | 6,161 | 2,291 | 0.37× | Same |
| `get_knowledge_topic` (large) | ~19,000 | ~4,750 | ~0.25× | Currently returned raw |
| `get_ams_units` (2× AMS) | ~3,000 | ~750 | ~0.25× | Currently returned raw |

---

## Tool Classification (After Empirical Testing)

### Tier 1 — Reliably overflows (URL factory justified)

| Tool | Overflow condition | HTTP route |
|------|--------------------|-----------|
| `get_snapshot` | Native/active print = ~4MB JPEG | `/api/snapshot` ✅ exists |

### Tier 2 — Fits but burns significant context (URL factory optional)

| Tool | Raw chars | Tokens used | % of budget | HTTP route |
|------|-----------|-------------|------------|-----------|
| `monitoring_history(raw=True)` | 282,901 | ~70,725 | **100%** | ❌ Missing |
| `get_snapshot` 1080p/idle | 263,306 | ~65,827 | 93% | ✅ exists |
| `get_project_info` (large) | 185,812 | ~46,453 | 66% | ✅ exists (broken) |
| `monitoring_series` | 118,417 | ~29,604 | 42% | ❌ Missing |

### Tier 3 — Compresses well, gzip threshold change helps

| Tool | Raw | After | Saving |
|------|-----|-------|--------|
| `monitoring_series` | 118K | 27K | 77% |
| `dump_log(200)` | 40K | 6K | 85% |
| `get_knowledge_topic` | 5–19K | 1.2–4.7K | 75% |
| `get_printer_state` | 7.6K | 2.9K | 62% |
| `get_ams_units` | 3K | 750 | 75% |

### Tier 4 — Tiny, completely untouched (28+ tools)

`get_temperatures`, `get_fan_speeds`, `get_wifi_signal`, `get_print_progress`, `get_job_info`,
`get_printer_info`, `get_nozzle_info`, `get_firmware_version`, `get_stream_url`, `get_server_info`,
`get_pending_alerts`, `get_configured_printers`, `get_printer_connection_status`, `get_session_status`,
`get_external_spool`, `get_chamber_light`, and more.

---

## Decision: What Gets the URL Factory?

Original plan: 11 tools. Empirical data reduces that to **1 confirmed + 2 optional**.

### ✅ Confirmed: `get_snapshot`

**Justification:** Native/active print = ~4MB JPEG = 5M chars = 1765% of limit. The docstring explicitly warns *"Never use native in polling loops — payload reaches 4 MB per call."* MCP path is broken-by-design for this use case at its intended resolution.

- Returns `{"url": "http://localhost:<port>/api/snapshot?printer=H2D&resolution=native&quality=85"}`
- **Edge case:** 180p/q55 fits fine (10K chars). URL factory applies uniformly — the round-trip for a dedicated snapshot call is acceptable.
- **HTTP route:** `/api/snapshot` ✅ already exists

### 🤔 Optional: `monitoring_history(raw=True)`

**Justification:** 282,901 chars = 99.999% of limit. Will overflow with a busier printer or longer print history.

- **Complication:** `raw=False` returns ~500 chars (trivially small). URL factory for both? Or only `raw=True`?
  - **Recommended:** conditional — return URL only when `raw=True`; return data when `raw=False`
- **HTTP route:** ❌ Missing — needs `GET /api/monitoring_history?printer=X&raw=true`
- **Verdict:** Add in Phase 4, after Phase 1 is validated in production

### 🤔 Optional: `get_project_info`

**Justification:** Largest plate seen at 185K chars (66% of limit). Consumes 46K tokens per call. For a 14-plate iteration, that's 14 × 46K = 644K tokens.

- **Prerequisite:** Fix `include_images` HTTP route bug first (route ignores the parameter)
- **Verdict:** Defer until bug is fixed (Phase 2); then reassess

---

## Feature 2: Lower Gzip Threshold to 300 Chars

### Why the current threshold is wrong

`compress_if_large` fires at `char_count > 282,904`. This means:
- `monitoring_series` (118K chars) → returned **raw** — 29K tokens wasted
- `get_knowledge_topic` (19K chars) → returned **raw** — 4.7K tokens wasted
- `get_ams_units` (3K chars) → returned **raw** — 750 tokens wasted

All compress 3×–5× with gzip. The 282K threshold was set for outlier protection — URL factory now handles those outliers.

### Breakeven: Gzip is net-positive above ~300 chars

| Raw size | gz+b64 envelope | Ratio | Verdict |
|----------|----------------|-------|---------|
| ~58 chars | 135 chars | 2.33× | ❌ Worse |
| ~145 chars | 159 chars | 1.10× | ❌ Worse |
| ~290 chars | 195 chars | 0.67× | ✅ Better |
| ~755 chars | 283 chars | 0.37× | ✅ Better |
| ~3,080 chars | 699 chars | 0.23× | ✅ Better |

### Implementation: One constant + one line change

```python
# tools/_response.py

_MIN_COMPRESS_SIZE = 300  # gzip is net-positive above this threshold

def compress_if_large(data: dict) -> dict:
    serialized = json.dumps(data)
    char_count = len(serialized)
    if _has_binary_data(data):
        record_response_size(char_count, label="binary")
        return data
    record_response_size(char_count)
    if char_count <= _MIN_COMPRESS_SIZE:   # ← was: _max_response_chars()
        return data
    # ... compress ...
```

`_max_response_chars()` stays for `ResponseSizeTracker` — just no longer drives the compress trigger.

---

## Phased Implementation Plan

### Phase 1 — Gzip threshold (10 min, very low risk) ✅ START HERE

| # | File | Change |
|---|------|--------|
| 1 | `tools/_response.py` | Add `_MIN_COMPRESS_SIZE = 300`; change compress trigger from `_max_response_chars()` to `_MIN_COMPRESS_SIZE` |
| 2 | Test | `get_knowledge_topic` → compressed envelope; `get_temperatures` → raw JSON |
| 3 | Version | PATCH bump |

### Phase 2 — Fix `include_images` HTTP route bug (prerequisite)

| # | File | Change |
|---|------|--------|
| 1 | `api_server.py` | Pass `include_images` param through to `get_project_info()` in route handler |
| 2 | Test | `?include_images=false` returns no `thumbnail`/`topimg` fields |

### Phase 3 — `get_snapshot` URL factory (confirmed candidate)

| # | File | Change |
|---|------|--------|
| 1 | `tools/camera.py` | `get_snapshot` returns `{"url": "http://localhost:<port>/api/snapshot?printer=...&resolution=...&quality=..."}` |
| 2 | `tools/camera.py` | Update docstring to reflect URL return format |
| 3 | `knowledge/behavioral_rules_camera.py` | Document URL return, how agent uses it |
| 4 | Test | MCP call → URL; curl URL → full JPEG JSON response |
| 5 | Version | MINOR bump |

### Phase 4 — Optional: `monitoring_history` URL factory (defer)

> Add only if `compress_if_large` is observed to overflow in production after Phase 1.

| # | File | Change |
|---|------|--------|
| 1 | `api_server.py` | Add `GET /api/monitoring_history?printer=X&raw=false` |
| 2 | `tools/system.py` | `get_monitoring_history` returns URL |
| 3 | Consider same for `monitoring_series` and `monitoring_data` |

### Phase 5 — Optional: `get_project_info` URL factory (after Phase 2)

> 65% of context limit, doesn't overflow. Revisit if larger files are encountered.

---

## Open Questions

| # | Question | Recommended Answer |
|---|----------|-------------------|
| 1 | `get_snapshot`: URL at ALL resolutions or only ≥720p? | **All** — simpler; overhead on 180p is acceptable for a snapshot call |
| 2 | `monitoring_history`: URL for `raw=True` only? | **Yes** — `raw=False` is ~500 chars, forcing HTTP for summary is wasteful |
| 3 | `get_project_info` URL factory: now or after Phase 2? | **After Phase 2** — fix the HTTP bug first; 65% limit isn't overflowing |
| 4 | `monitoring_series`: URL factory or let Phase 1 gzip handle it? | **Let gzip handle it** — 118K → 27K chars via Phase 1; no new HTTP route needed |

---

## Files Changed Per Phase

| File | Phase | Change |
|------|-------|--------|
| `tools/_response.py` | 1 | `_MIN_COMPRESS_SIZE = 300`; compress trigger change |
| `api_server.py` | 2 | Fix `include_images` param pass-through |
| `api_server.py` | 4 | Add monitoring routes (optional) |
| `tools/camera.py` | 3 | `get_snapshot` → URL return |
| `knowledge/behavioral_rules_camera.py` | 3 | Document URL return |
| `knowledge/behavioral_rules_mcp_patterns.py` | 3 | Document selective URL factory pattern |

## What Does NOT Change

- `compress_if_large` stays — still handles `dump_log`, `monitoring_history(raw=True)`, all Tier 3 tools
- `ResponseSizeTracker` stays — valuable telemetry
- All 28+ Tier 4 targeted state query tools — completely untouched
- HTTP fallback docstrings — still relevant for `analyze_active_job`, plate thumbnail tools, etc.

---

## Risk Assessment

| Change | Risk | Mitigation |
|--------|------|-----------|
| Gzip threshold (300 chars) | **Very Low** — only changes response format, not data | Decompress path already exercised daily by monitoring tools |
| `get_snapshot` URL factory | **Low** — existing HTTP route, clear overflow justification | Smoke test at all resolutions |
| `include_images` HTTP bug fix | **Low** — only changes HTTP route, MCP tool unaffected | Verify with `?include_images=false` |
| Monitoring URL factory (optional) | **Medium** — new HTTP route | Defer until overflow observed in production |

---

*Generated 2026-03-11 from empirical measurements against live H2D and A1 printers*
