# Fix A1 Camera Stream — Two Bugs

| Field | Value |
|-------|-------|
| Date | 2026-03-16 13:28 EDT |
| Model | claude-opus-4-6[1m] |
| Status | PENDING APPROVAL |
| Scope | bambu-mcp |
| Repos | `bambu-mcp` |
| Files touched | 2 |

## Context

A1 camera stream shows blank/black in Safari. MCP log reveals two bugs:

1. **MQTT password type coercion** — A1 access code stored as numeric string `"12345678"` is round-tripped through `json.loads()` → returns `int 12345678` → paho-mqtt crashes on `len(self._password)`.
2. **TCP frame header parse error** — `dr[0:3]` reads 3 bytes for a little-endian uint32 payload size, dropping the 4th byte. Miscomputed size causes frame assembly to never complete → reader thread death → black stream.

### Infrastructure Leveraged

None — pure source code bug fix. No new mechanisms, stores, or enforcement points introduced.

## Approach

### Fix 1: `secrets_store.py:132` — Type-safe credential loading

In `_load_v2()`, after `json.loads(plaintext)`, coerce the result: if the original plaintext looked like a bare scalar (not a list/dict), keep it as a string. Simplest fix: coerce non-collection types back to `str`.

```python
# Before (line 132):
result[service] = json.loads(plaintext)

# After:
parsed = json.loads(plaintext)
result[service] = plaintext if not isinstance(parsed, (list, dict)) else parsed
```

This preserves lists (like `_printer_names`) while ensuring scalar values (access codes, IPs, serials) stay as strings.

### Fix 2: `camera/tcp_stream.py:175` — Correct frame header byte range

```python
# Before:
payload_size = int.from_bytes(dr[0:3], "little")

# After:
payload_size = int.from_bytes(dr[0:4], "little")
```

The frame header is a 16-byte struct with a uint32 payload size in the first 4 bytes.

## Files to Modify

| File | Line | Change |
|------|------|--------|
| `secrets_store.py` | 132 | Coerce `json.loads` scalars back to `str` |
| `camera/tcp_stream.py` | 175 | `dr[0:3]` → `dr[0:4]` |

## Verification

1. `python -m py_compile secrets_store.py` — no syntax errors
2. `python -m py_compile camera/tcp_stream.py` — no syntax errors
3. Restart MCP server (full restart sequence)
4. `view_stream(name="A1")` — should show live camera feed, not black
5. `dump_log(tail_lines=20)` — no `TypeError` or `wait_first_frame` timeouts
6. Verify H2D stream still works (regression check)

## Quality Gate Checklist

| Gate | Status |
|------|--------|
| 1. Scope Definition | PASS — two bugs, two one-line fixes |
| 2. Current State | PASS — read source files, confirmed both bugs; infrastructure audit: no existing systems apply |
| 3. Impact & Dependency | PASS — secrets_store change affects all stored scalars (safe: coercion preserves original string); tcp_stream fix is A1/P1 only (RTSPS printers unaffected) |
| 4. Approach Selection | PASS — minimal fixes at the root cause, no new abstractions |
| 5. Verification Plan | PASS — compile check + stream test + log check |
| 6. Plan Recap | This document |
| 7. Anti-Dilution | N/A — no rules changes |
