#!/usr/bin/env python3
"""
MCP smoke test — regression check for bambu-mcp.

Tests three layers:
  1. Import integrity  — all modules import cleanly, nothing broken at load time
  2. Knowledge topics  — every registered topic returns non-empty content
  3. Live HTTP API     — key routes on the running server respond correctly

Usage:
    cd ~/bambu-mcp && .venv/bin/python smoke_test.py
    cd ~/bambu-mcp && .venv/bin/python smoke_test.py --api-only
    cd ~/bambu-mcp && .venv/bin/python smoke_test.py --no-api

Exit code 0 = all checks passed.  Non-zero = failures (details printed).
"""

import argparse
import importlib
import sys
import traceback

BASE_URL = "http://localhost:49152"  # overridden by --base-url or server_info probe

PASS = "\033[32m✓\033[0m"
FAIL = "\033[31m✗\033[0m"
SKIP = "\033[33m⊘\033[0m"

failures = []


def ok(label, detail=""):
    print(f"  {PASS}  {label}" + (f"\n       {detail}" if detail else ""))


def fail(label, detail=""):
    print(f"  {FAIL}  {label}" + (f"\n       {detail}" if detail else ""))
    failures.append(label)


def skip(label, reason=""):
    print(f"  {SKIP}  {label}" + (f" ({reason})" if reason else ""))


# ── 1. Import integrity ──────────────────────────────────────────────────────

MODULES = [
    "server",
    "api_server",
    "notifications",
    "resources.knowledge",
    "tools.knowledge_search",
    "tools.notifications",
    "knowledge.behavioral_rules",
    "knowledge.behavioral_rules_methodology",
    "knowledge.behavioral_rules_session",
    "knowledge.behavioral_rules_alerts",
    "knowledge.behavioral_rules_camera",
    "knowledge.behavioral_rules_job_analysis",
    "knowledge.behavioral_rules_mcp_patterns",
    "knowledge.behavioral_rules_print_state",
    "knowledge.api_reference",
    "knowledge.api_reference_session",
    "knowledge.api_reference_files",
    "knowledge.api_reference_print",
    "knowledge.api_reference_ams",
    "knowledge.api_reference_state",
    "knowledge.api_reference_dataclasses",
    "knowledge.api_reference_camera",
    "knowledge.protocol",
    "knowledge.protocol_concepts",
    "knowledge.protocol_mqtt",
    "knowledge.protocol_hms",
    "knowledge.protocol_3mf",
    "knowledge.enums",
    "knowledge.enums_printer",
    "knowledge.enums_ams",
    "knowledge.enums_filament",
    "knowledge.http_api",
    "knowledge.http_api_printer",
    "knowledge.http_api_print",
    "knowledge.http_api_ams",
    "knowledge.http_api_climate",
    "knowledge.http_api_hardware",
    "knowledge.http_api_files",
    "knowledge.http_api_system",
    "knowledge.fallback_strategy",
    "knowledge.references",
]


def test_imports():
    print("\n── Import integrity ────────────────────────────────────────────────────")
    for mod in MODULES:
        try:
            importlib.import_module(mod)
            ok(mod)
        except ImportError as e:
            fail(mod, str(e))
        except Exception as e:
            fail(mod, f"{type(e).__name__}: {e}")


# ── 2. Knowledge topic coverage ──────────────────────────────────────────────

def test_knowledge():
    print("\n── Knowledge topics ────────────────────────────────────────────────────")
    try:
        from tools.knowledge_search import _KNOWN_TOPICS, get_knowledge_topic
    except Exception as e:
        fail("tools.knowledge_search import", str(e))
        return

    for topic in sorted(_KNOWN_TOPICS.keys()):
        try:
            result = get_knowledge_topic(topic)
            if isinstance(result, str) and len(result) > 100:
                ok(f"topic: {topic}  ({len(result):,} chars)")
            elif isinstance(result, str) and result:
                fail(f"topic: {topic} — suspiciously short ({len(result)} chars)", result[:120])
            else:
                fail(f"topic: {topic} — empty or wrong type", repr(result)[:120])
        except Exception as e:
            fail(f"topic: {topic}", f"{type(e).__name__}: {e}")

    # Verify new sub-topics explicitly
    for required in ("behavioral_rules/session", "behavioral_rules/alerts"):
        if required not in _KNOWN_TOPICS:
            fail(f"_KNOWN_TOPICS missing: {required}")
        else:
            ok(f"_KNOWN_TOPICS contains: {required}")


# ── 3. Live HTTP API ─────────────────────────────────────────────────────────

# Routes that should return 200 with no query params (GET, read-only, no auth needed)
READ_ROUTES = [
    "/api/server_info",
    "/api/health_check",
    "/api/openapi.json",
    "/api/filament_catalog",
]

# Routes that return 200 but plain text (not JSON)
TEXT_ROUTES = [
    "/api/dump_log",
]

# Routes that need ?name= param
PRINTER_ROUTES = [
    "/api/printer",
    "/api/alerts",
]

# These are write routes (POST, PATCH, DELETE). Listed for documentation — not called by smoke test.
WRITE_ROUTES_EXCLUDED = [
    # POST routes (actions/commands)
    "/api/stop_printing", "/api/pause_printing", "/api/resume_printing",
    "/api/send_gcode", "/api/send_mqtt_command", "/api/print_3mf",
    "/api/skip_objects", "/api/clear_print_error", "/api/unload_filament",
    "/api/load_filament", "/api/refresh_spool_rfid", "/api/set_spool_k_factor",
    "/api/send_ams_control_command", "/api/select_extrusion_calibration",
    "/api/turn_on_ams_dryer", "/api/turn_off_ams_dryer", "/api/refresh_nozzles",
    "/api/trigger_printer_refresh", "/api/refresh_sdcard_3mf_files",
    "/api/refresh_sdcard_contents", "/api/make_sdcard_directory",
    "/api/upload_file_to_printer",
    # PATCH routes (partial resource updates)
    "/api/set_speed_level", "/api/set_print_option", "/api/toggle_active_tool",
    "/api/set_tool_target_temp", "/api/set_bed_target_temp",
    "/api/set_chamber_target_temp", "/api/set_aux_fan_speed_target",
    "/api/set_exhaust_fan_speed_target", "/api/set_fan_speed_target",
    "/api/set_light_state", "/api/set_spool_details", "/api/set_nozzle_details",
    "/api/set_ams_user_setting", "/api/rename_printer", "/api/toggle_session",
    "/api/rename_sdcard_file", "/api/set_buildplate_marker_detector",
    "/api/set_first_layer_inspection", "/api/set_spaghetti_detector",
    "/api/set_purgechutepileup_detector", "/api/set_nozzleclumping_detector",
    "/api/set_airprinting_detector",
    # DELETE routes (resource destruction)
    "/api/delete_sdcard_file", "/api/truncate_log",
]



def probe_server_port():
    """Try to discover the actual API port from the running server_info endpoint."""
    import urllib.request
    for port in range(49152, 49172):
        try:
            url = f"http://localhost:{port}/api/server_info"
            with urllib.request.urlopen(url, timeout=1) as r:
                if r.status == 200:
                    return port
        except Exception:
            continue
    return None


def get_json(url, timeout=5):
    import json
    import urllib.request
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.status, json.loads(r.read())


def test_api(base_url, printer_name):
    print(f"\n── Live HTTP API  ({base_url}) ─────────────────────────────────────────")

    # READ routes
    for route in READ_ROUTES:
        url = f"{base_url}{route}"
        try:
            status, data = get_json(url)
            if status == 200:
                ok(f"GET {route}")
            else:
                fail(f"GET {route}", f"HTTP {status}")
        except Exception as e:
            fail(f"GET {route}", f"{type(e).__name__}: {e}")

    # TEXT routes (plain text response, not JSON)
    for route in TEXT_ROUTES:
        url = f"{base_url}{route}"
        try:
            import urllib.request
            with urllib.request.urlopen(url, timeout=5) as r:
                body = r.read()
                if r.status == 200 and body:
                    ok(f"GET {route} (text, {len(body)} bytes)")
                else:
                    fail(f"GET {route}", f"HTTP {r.status} or empty body")
        except Exception as e:
            fail(f"GET {route}", f"{type(e).__name__}: {e}")

    # PRINTER routes
    if not printer_name:
        for route in PRINTER_ROUTES:
            skip(f"GET {route}?name=...", "no printer name (pass --printer NAME)")
        return

    for route in PRINTER_ROUTES:
        url = f"{base_url}{route}?name={printer_name}"
        try:
            status, data = get_json(url)
            if status == 200:
                ok(f"GET {route}?name={printer_name}")
            elif status == 404 and "not connected" in str(data).lower():
                fail(f"GET {route}?name={printer_name}", f"Printer not connected: {data}")
            else:
                fail(f"GET {route}?name={printer_name}", f"HTTP {status}: {str(data)[:120]}")
        except Exception as e:
            fail(f"GET {route}?name={printer_name}", f"{type(e).__name__}: {e}")

    # Verify /api/alerts returns a list (even if empty)
    url = f"{base_url}/api/alerts?name={printer_name}"
    try:
        status, data = get_json(url)
        if status == 200 and isinstance(data, list):
            ok(f"/api/alerts returns list  (len={len(data)})")
        else:
            fail("/api/alerts format", f"expected list, got {type(data).__name__}: {str(data)[:80]}")
    except Exception as e:
        fail("/api/alerts", f"{type(e).__name__}: {e}")


# ── 4. NotificationManager wiring ───────────────────────────────────────────

def test_notifications():
    print("\n── Notifications wiring ────────────────────────────────────────────────")
    try:
        from notifications import NotificationManager
        ok("NotificationManager importable")
    except Exception as e:
        fail("NotificationManager import", str(e))
        return

    try:
        from tools.notifications import get_pending_alerts
        ok("get_pending_alerts importable")
    except Exception as e:
        fail("get_pending_alerts import", str(e))




def test_compression():
    print("\n── compress_if_large ───────────────────────────────────────────────────")
    import gzip
    import base64
    import json
    import os
    try:
        from tools._response import compress_if_large, _max_response_chars
        ok("tools._response import")
    except Exception as e:
        fail("tools._response import", str(e))
        return

    # 1. Small payload passes through unchanged
    small = {"key": "value", "num": 42}
    result = compress_if_large(small)
    if result == small:
        ok("small payload returned as-is (no compression envelope)")
    else:
        fail("small payload should not be compressed", f"got: {result}")

    # 2. Large payload triggers compression envelope
    large = {"data": "x" * (_max_response_chars() + 1)}
    result = compress_if_large(large)
    if result.get("compressed") is True and result.get("encoding") == "gzip+base64":
        ok("large payload returns compression envelope (compressed=True, encoding=gzip+base64)")
    else:
        fail("large payload should trigger compression", f"compressed={result.get('compressed')}, encoding={result.get('encoding')}")

    # 3. Envelope contains required size fields
    if "original_size_bytes" in result and "compressed_size_bytes" in result and "data" in result:
        orig = result["original_size_bytes"]
        comp = result["compressed_size_bytes"]
        ok(f"envelope has size fields: original={orig:,} bytes → compressed={comp:,} bytes ({comp/orig*100:.1f}%)")
    else:
        fail("envelope missing size fields", f"keys: {list(result.keys())}")

    # 4. Round-trip decompression reproduces original data exactly
    try:
        recovered = json.loads(gzip.decompress(base64.b64decode(result["data"])))
        if recovered == large:
            ok("round-trip decompression reproduces original data exactly")
        else:
            fail("round-trip data mismatch — decompressed data != original")
    except Exception as e:
        fail("round-trip decompression failed", str(e))

    # 5. Threshold scales with MAX_MCP_OUTPUT_TOKENS env var
    orig_env = os.environ.get("MAX_MCP_OUTPUT_TOKENS")
    try:
        os.environ["MAX_MCP_OUTPUT_TOKENS"] = "1"  # threshold = 4 chars
        tiny = {"a": "b"}  # 10 chars serialized — should now exceed threshold
        result_tiny = compress_if_large(tiny)
        if result_tiny.get("compressed") is True:
            ok("threshold scales with MAX_MCP_OUTPUT_TOKENS (forced to 1 token = 4 chars, tiny dict compressed)")
        else:
            fail("threshold did not scale — tiny dict should be compressed at MAX_MCP_OUTPUT_TOKENS=1")
    finally:
        if orig_env is None:
            os.environ.pop("MAX_MCP_OUTPUT_TOKENS", None)
        else:
            os.environ["MAX_MCP_OUTPUT_TOKENS"] = orig_env

    # 6. Payload exactly at threshold is NOT compressed (boundary condition)
    threshold = _max_response_chars()
    serialized_len = len(json.dumps({"data": ""})) + 2  # account for key+quotes overhead
    filler_len = threshold - serialized_len
    boundary = {"data": "y" * filler_len}
    at_boundary_len = len(json.dumps(boundary))
    result_boundary = compress_if_large(boundary)
    if at_boundary_len <= threshold and result_boundary.get("compressed") is not True:
        ok(f"payload at threshold ({at_boundary_len:,} chars ≤ {threshold:,}) not compressed (boundary condition)")
    elif at_boundary_len > threshold:
        ok(f"boundary payload ({at_boundary_len:,} chars) slightly over threshold — compression triggered (expected)")
    else:
        fail("boundary condition", f"at_boundary_len={at_boundary_len}, threshold={threshold}, compressed={result_boundary.get('compressed')}")


def test_compression_benchmark():
    import gzip
    import base64
    import json
    import os
    import random
    import string
    import time

    print("\n── compress_if_large benchmark ─────────────────────────────────────────")

    try:
        from tools._response import compress_if_large, _max_response_chars
    except Exception as e:
        fail("tools._response import (benchmark)", str(e))
        return

    threshold = _max_response_chars()
    rng = random.Random(0xBAB00)  # fixed seed for reproducibility

    def _rand_alphanumeric(n):
        """Medium entropy: letters + digits (typical JSON value content)."""
        chars = string.ascii_letters + string.digits
        return "".join(rng.choices(chars, k=n))

    def _rand_printable(n):
        """High entropy: full printable ASCII (worst-case for text compression)."""
        chars = string.printable.strip()
        return "".join(rng.choices(chars, k=n))

    def _realistic_json(target_chars):
        """Realistic MCP payload: fixed schema fields + a padded string to hit target size."""
        fields = [
            "printer_name", "gcode_state", "subtask_name", "stage_id",
            "print_percentage", "elapsed_min", "remaining_min", "layer_num",
            "total_layers", "nozzle_temp", "bed_temp", "chamber_temp",
            "part_fan", "aux_fan", "exhaust_fan", "heatbreak_fan",
            "filament_type", "filament_color", "ams_unit", "tray_id",
        ]
        obj = {k: _rand_alphanumeric(rng.randint(8, 32)) for k in fields}
        obj["nozzle_temp"] = round(rng.uniform(180, 280), 1)
        obj["bed_temp"] = round(rng.uniform(20, 110), 1)
        obj["layers"] = [rng.randint(0, 255) for _ in range(100)]
        # Pad to target with a data field of random alphanumeric content
        base_len = len(json.dumps(obj))
        pad_len = max(0, target_chars - base_len - 12)  # 12 = len(', "pad": ""')
        obj["pad"] = _rand_alphanumeric(pad_len)
        return obj

    import statistics as stats

    ITERATIONS = 20

    # Sizes as multiples of the threshold so they scale with MAX_MCP_OUTPUT_TOKENS
    MULTIPLIERS = [
        ("1.1×", 1.1),
        ("2.5×", 2.5),
        ("5×",   5.0),
        ("10×",  10.0),
    ]

    CONTENT_TYPES = [
        ("repetitive",   lambda n: {"data": "x" * n}),
        ("alphanumeric", lambda n: {"data": _rand_alphanumeric(n)}),
        ("printable",    lambda n: {"data": _rand_printable(n)}),
        ("realistic",    lambda n: _realistic_json(n)),
    ]

    hdr = (f"  {'content':<13} {'size':>7}  {'orig':>10}  {'comp':>10}  {'ratio':>6}"
           f"  {'n':>3}  {'min':>7}  {'max':>7}  {'avg':>7}  {'med':>7}  {'std':>7}  {'p90':>7}")
    sep = "  " + "-" * (len(hdr) - 2)
    print(hdr)
    print(sep)

    all_passed = True
    for content_label, content_fn in CONTENT_TYPES:
        for mult_label, multiplier in MULTIPLIERS:
            target_size = int(threshold * multiplier)
            size_label = f"{mult_label} ({target_size // 1000}k)"

            # Pre-generate payload once — bench compression only, not generation
            payload = content_fn(target_size)
            serialized = json.dumps(payload)
            orig_bytes = len(serialized.encode("utf-8"))

            # Verify compression triggers (all payloads must exceed threshold)
            first = compress_if_large(payload)
            if not first.get("compressed"):
                print(f"  {FAIL}  {content_label:<13} {size_label:<16}  not compressed "
                      f"(payload={len(serialized):,} threshold={threshold:,})")
                failures.append(f"benchmark {content_label} {mult_label} not compressed")
                all_passed = False
                continue

            comp_bytes = first["compressed_size_bytes"]
            ratio = comp_bytes / orig_bytes * 100

            # Round-trip correctness (once per scenario)
            try:
                recovered = json.loads(gzip.decompress(base64.b64decode(first["data"])))
                rt_ok = recovered == payload
            except Exception:
                rt_ok = False
            if not rt_ok:
                failures.append(f"benchmark {content_label} {mult_label} round-trip failed")
                all_passed = False

            # Timed iterations — compress only, payload already generated
            samples = []
            for _ in range(ITERATIONS):
                t0 = time.perf_counter()
                compress_if_large(payload)
                samples.append((time.perf_counter() - t0) * 1000)

            mn  = min(samples)
            mx  = max(samples)
            avg = stats.mean(samples)
            med = stats.median(samples)
            std = stats.stdev(samples)
            p90 = sorted(samples)[int(len(samples) * 0.9)]

            status = PASS if rt_ok else FAIL
            print(f"  {status}  {content_label:<13} {size_label:<16}  {orig_bytes:>10,}  {comp_bytes:>10,}"
                  f"  {ratio:>5.1f}%"
                  f"  {ITERATIONS:>3}"
                  f"  {mn:>5.1f}ms  {mx:>5.1f}ms  {avg:>5.1f}ms  {med:>5.1f}ms  {std:>5.1f}ms  {p90:>5.1f}ms")

    if all_passed:
        ok("all benchmark round-trips verified correct")


def test_compression_envelope_limits():
    import gzip
    import base64
    import json
    import os
    import random
    import string

    print("\n── compress_if_large envelope limits ───────────────────────────────────")

    try:
        from tools._response import compress_if_large, _max_response_chars
    except Exception as e:
        fail("tools._response import (envelope limits)", str(e))
        return

    threshold = _max_response_chars()
    rng = random.Random(0xBAB00)

    def _rand_alphanumeric(n):
        chars = string.ascii_letters + string.digits
        return "".join(rng.choices(chars, k=n))

    def _rand_printable(n):
        chars = string.printable.strip()
        return "".join(rng.choices(chars, k=n))

    def _realistic_json(target_chars):
        fields = [
            "printer_name", "gcode_state", "subtask_name", "stage_id",
            "print_percentage", "elapsed_min", "remaining_min", "layer_num",
            "total_layers", "nozzle_temp", "bed_temp", "chamber_temp",
            "part_fan", "aux_fan", "exhaust_fan", "heatbreak_fan",
            "filament_type", "filament_color", "ams_unit", "tray_id",
        ]
        obj = {k: _rand_alphanumeric(rng.randint(8, 32)) for k in fields}
        obj["nozzle_temp"] = round(rng.uniform(180, 280), 1)
        obj["bed_temp"] = round(rng.uniform(20, 110), 1)
        obj["layers"] = [rng.randint(0, 255) for _ in range(100)]
        base_len = len(json.dumps(obj))
        pad_len = max(0, target_chars - base_len - 12)
        obj["pad"] = _rand_alphanumeric(pad_len)
        return obj

    CONTENT_TYPES = [
        ("repetitive",   lambda n: {"data": "x" * n}),
        ("alphanumeric", lambda n: {"data": _rand_alphanumeric(n)}),
        ("printable",    lambda n: {"data": _rand_printable(n)}),
        ("realistic",    lambda n: _realistic_json(n)),
    ]

    MAX_PAYLOAD = 4_000_000

    print(f"  Threshold: {threshold:,} chars (MAX_MCP_OUTPUT_TOKENS × 4)")
    print(f"  Envelope break-even = size at which compressed envelope ALSO exceeds threshold")
    print(f"\n  {'content':<14} {'payload':>10}  {'orig':>10}  {'envelope':>10}  {'ratio':>7}  {'rt'}")
    print(f"  {'-'*14} {'-'*10}  {'-'*10}  {'-'*10}  {'-'*7}  {'--'}")

    all_rt_ok = True
    for content_label, content_fn in CONTENT_TYPES:
        size = threshold + 1
        found_breakeven = False
        while size <= MAX_PAYLOAD:
            payload = content_fn(size)
            result = compress_if_large(payload)
            if not result.get("compressed"):
                # Shouldn't happen — payload exceeds threshold, compression must fire
                size *= 2
                continue

            envelope_json = json.dumps(result)
            envelope_chars = len(envelope_json)
            orig_bytes = result["original_size_bytes"]

            if envelope_chars > threshold:
                found_breakeven = True
                try:
                    recovered = json.loads(gzip.decompress(base64.b64decode(result["data"])))
                    rt_ok = recovered == payload
                except Exception:
                    rt_ok = False

                if not rt_ok:
                    all_rt_ok = False
                    failures.append(f"envelope limit {content_label} round-trip failed at payload={size:,}")

                ratio = envelope_chars / orig_bytes * 100
                rt_icon = PASS if rt_ok else FAIL
                print(f"  {rt_icon}  {content_label:<12} {size:>10,}  {orig_bytes:>10,}  {envelope_chars:>10,}  {ratio:>6.1f}%  {'ok' if rt_ok else 'FAIL'}")
                break

            size *= 2

        if not found_breakeven:
            # Compression is so effective that even at MAX_PAYLOAD the envelope fits
            print(f"  {PASS}  {content_label:<12} envelope never exceeds threshold up to {MAX_PAYLOAD:,} chars (compression too effective to test)")

    if all_rt_ok:
        ok("all envelope-limit round-trips verified correct")

    print(f"\n  When envelope exceeds threshold the agent receives truncated JSON (undecompressable).")
    print(f"  Expected agent action: fall back to the HTTP API — see each tool's docstring.")


def test_compression_fallback_docs():
    import importlib

    print("\n── compress_if_large fallback documentation ─────────────────────────")

    # (module, function_name, expected_phrase_in_docstring, has_http_route)
    TOOLS = [
        ("tools.files",  "list_sdcard_files",    "HTTP fallback",         True),
        ("tools.state",  "get_printer_state",    "GET /api/printer",      True),
        ("tools.state",  "get_monitoring_data",  "No HTTP fallback",      False),
        ("tools.system", "get_monitoring_history","No HTTP fallback",     False),
        ("tools.system", "get_monitoring_series", "No HTTP fallback",     False),
        ("tools.system", "dump_log",             "GET /api/dump_log",     True),
    ]

    for module_name, func_name, expected_phrase, has_route in TOOLS:
        try:
            mod = importlib.import_module(module_name)
            fn = getattr(mod, func_name)
            doc = fn.__doc__ or ""

            has_compressed = "compressed" in doc.lower()
            has_decompress = "decompress" in doc.lower() or "gzip" in doc.lower()
            has_fallback = expected_phrase in doc

            if has_compressed and has_decompress and has_fallback:
                route_note = f"→ {expected_phrase}" if has_route else "→ no HTTP route (scope-reduce guidance present)"
                ok(f"{func_name}: envelope detection + decompress recipe + fallback guidance {route_note}")
            else:
                missing = []
                if not has_compressed:
                    missing.append("envelope detection ('compressed')")
                if not has_decompress:
                    missing.append("decompress recipe")
                if not has_fallback:
                    missing.append(f"fallback phrase ('{expected_phrase}')")
                fail(f"{func_name} docstring missing: {', '.join(missing)}")
        except Exception as e:
            fail(f"{func_name} docstring check failed", str(e))


def test_openapi_methods(base_url):
    print("\n── OpenAPI method correctness ──────────────────────────────────────────")
    EXPECTED_POST = [
        "/api/stop_printing", "/api/pause_printing", "/api/resume_printing",
        "/api/print_3mf", "/api/send_gcode", "/api/send_mqtt_command",
        "/api/turn_on_ams_dryer", "/api/turn_off_ams_dryer",
        "/api/send_ams_control_command", "/api/skip_objects",
        "/api/refresh_sdcard_3mf_files", "/api/refresh_sdcard_contents",
    ]
    EXPECTED_PATCH = [
        "/api/set_bed_target_temp", "/api/set_tool_target_temp",
        "/api/set_speed_level", "/api/set_nozzle_details",
        "/api/toggle_session", "/api/rename_printer",
        "/api/set_print_option", "/api/set_light_state",
        "/api/set_fan_speed_target", "/api/set_aux_fan_speed_target",
        "/api/set_exhaust_fan_speed_target", "/api/toggle_active_tool",
    ]
    EXPECTED_DELETE = [
        "/api/delete_sdcard_file", "/api/truncate_log",
    ]
    try:
        status, spec = get_json(f"{base_url}/api/openapi.json")
        paths = spec.get("paths", {})
        for route in EXPECTED_POST:
            path_ops = paths.get(route, {})
            if "post" in path_ops and "get" not in path_ops:
                ok(f"POST {route}")
            elif "get" in path_ops:
                fail(f"{route} still registered as GET in OpenAPI spec")
            else:
                fail(f"{route} not found in OpenAPI spec")
        for route in EXPECTED_PATCH:
            path_ops = paths.get(route, {})
            if "patch" in path_ops and "get" not in path_ops:
                ok(f"PATCH {route}")
            elif "post" in path_ops:
                fail(f"{route} still registered as POST (should be PATCH)")
            elif "get" in path_ops:
                fail(f"{route} still registered as GET in OpenAPI spec")
            else:
                fail(f"{route} not found in OpenAPI spec")
        for route in EXPECTED_DELETE:
            path_ops = paths.get(route, {})
            if "delete" in path_ops and "get" not in path_ops:
                ok(f"DELETE {route}")
            elif "post" in path_ops:
                fail(f"{route} still registered as POST (should be DELETE)")
            else:
                fail(f"{route} not found in OpenAPI spec")
    except Exception as e:
        fail("OpenAPI spec fetch", str(e))

# ── Main ─────────────────────────────────────────────────────────────────────

def test_image_compression_benchmark():
    """Offline benchmark: JPEG/PNG images through compress_if_large.

    Demonstrates that gzip over base64-encoded image bytes produces near-zero
    compression — quality/resolution params are the correct size-control mechanism.
    """
    import io
    import gzip
    import base64
    import json
    import time
    import statistics as stats

    print("\n── image compress_if_large benchmark (offline) ─────────────────────────")

    try:
        from tools._response import compress_if_large
    except Exception as e:
        fail("tools._response import (image benchmark)", str(e))
        return

    try:
        from PIL import Image
    except ImportError:
        fail("PIL import (image benchmark)", "Pillow not installed")
        return

    ITERATIONS = 20

    cases = [
        ("JPEG preview",   (320,  180),  "JPEG", 65),
        ("JPEG standard",  (640,  360),  "JPEG", 75),
        ("JPEG full",      (1920, 1080), "JPEG", 85),
        ("PNG 2K noise",   (2560, 1440), "PNG",  None),
        ("PNG 4K noise",   (3840, 2160), "PNG",  None),
    ]

    for name, (w, h), fmt, q in cases:
        try:
            import random
            rng = random.Random(0xBAB00)
            pixels = bytes([rng.randint(0, 255) for _ in range(w * h * 3)])
            img = Image.frombytes("RGB", (w, h), pixels)
            buf = io.BytesIO()
            if fmt == "JPEG":
                img.save(buf, format="JPEG", quality=q)
                mime = "image/jpeg"
            else:
                img.save(buf, format="PNG")
                mime = "image/png"
            img_bytes = buf.getvalue()

            data_uri = f"data:{mime};base64,{base64.b64encode(img_bytes).decode()}"
            payload = {"data_uri": data_uri, "width": w, "height": h}
            raw_json = json.dumps(payload)
            raw_chars = len(raw_json)

            timings = []
            result = None
            for _ in range(ITERATIONS):
                t0 = time.perf_counter()
                result = compress_if_large(payload)
                timings.append(time.perf_counter() - t0)

            compressed = result.get("compressed", False)
            if compressed:
                out_size = result["compressed_size_bytes"]
                ratio = (1 - out_size / raw_chars) * 100
            else:
                out_size = raw_chars
                ratio = 0.0

            avg_ms = stats.mean(timings) * 1000
            p90_ms = sorted(timings)[int(len(timings) * 0.9)] * 1000

            img_kb = len(img_bytes) / 1024
            label = (
                f"{w}×{h} {fmt}{f' q={q}' if q else ''} "
                f"img={img_kb:.0f}KB  payload={raw_chars//1024}KB  "
                f"{'⚙' if compressed else 'pass-through'}  ratio={ratio:.1f}%  "
                f"avg={avg_ms:.1f}ms p90={p90_ms:.1f}ms"
            )
            ok(name, label)
        except Exception as e:
            fail(f"image benchmark: {name}", str(e))


def test_snapshot_profiles(base_url, printer_name):
    """Live test: GET /api/snapshot for each of the 5 named profiles."""
    import time
    import statistics as stats
    import base64
    import json

    print("\n── live snapshot profile tests ─────────────────────────────────────────")

    if not printer_name:
        skip("snapshot profiles", "no printer name (pass --printer NAME)")
        return

    try:
        from tools._response import compress_if_large
    except Exception as e:
        fail("tools._response import (snapshot profiles)", str(e))
        return

    profiles = [
        ("native",   "native", 85),
        ("high",     "1080p",  85),
        ("standard", "720p",   75),
        ("low",      "480p",   65),
        ("preview",  "180p",   55),
    ]

    ITERATIONS = 3  # fewer iters — live network call

    for profile, resolution, quality in profiles:
        url = f"{base_url}/api/snapshot?printer={printer_name}&resolution={resolution}&quality={quality}"
        try:
            timings = []
            data = None
            for _ in range(ITERATIONS):
                t0 = time.perf_counter()
                status, data = get_json(url)
                timings.append(time.perf_counter() - t0)

            if status != 200:
                fail(f"snapshot/{profile}", f"HTTP {status}: {str(data)[:120]}")
                continue

            if "error" in data:
                fail(f"snapshot/{profile}", str(data["error"]))
                continue

            data_uri = data.get("data_uri", "")
            if not data_uri.startswith("data:"):
                fail(f"snapshot/{profile}", "missing/invalid data_uri")
                continue

            # Parse actual image size
            header, b64 = data_uri.split(",", 1)
            img_bytes = base64.b64decode(b64)
            img_kb = len(img_bytes) / 1024

            # Test compress_if_large on the full response
            compressed_result = compress_if_large(data)
            is_compressed = compressed_result.get("compressed", False)
            if is_compressed:
                ratio = (1 - compressed_result["compressed_size_bytes"] / compressed_result["original_size_bytes"]) * 100
            else:
                ratio = 0.0

            w = data.get("width", "?")
            h = data.get("height", "?")
            avg_ms = stats.mean(timings) * 1000

            ok(
                f"snapshot/{profile}",
                f"{w}×{h}  img={img_kb:.0f}KB  "
                f"{'⚙' if is_compressed else 'pass-through'}  ratio={ratio:.1f}%  "
                f"avg={avg_ms:.0f}ms"
            )

        except Exception as e:
            fail(f"snapshot/{profile}", f"{type(e).__name__}: {e}")


def main():
    parser = argparse.ArgumentParser(description="bambu-mcp smoke test")
    parser.add_argument("--api-only", action="store_true", help="Skip import/knowledge tests")
    parser.add_argument("--no-api", action="store_true", help="Skip live API tests")
    parser.add_argument("--printer", default=None, help="Printer name for per-printer API tests")
    parser.add_argument("--base-url", default=None, help="Override API base URL")
    args = parser.parse_args()

    # Make sure we can import project modules
    sys.path.insert(0, ".")

    if not args.api_only:
        test_imports()
        test_knowledge()
        test_notifications()
        test_compression()
        test_compression_benchmark()
        test_compression_envelope_limits()
        test_compression_fallback_docs()
        test_image_compression_benchmark()

    if not args.no_api:
        base_url = args.base_url
        if not base_url:
            port = probe_server_port()
            if port:
                base_url = f"http://localhost:{port}"
                print(f"\n  (auto-detected API port: {port})")
            else:
                print(f"\n  {SKIP}  Live API tests skipped — server not reachable on ports 49152-49171")
                base_url = None
        if base_url:
            test_api(base_url, args.printer)
            test_snapshot_profiles(base_url, args.printer)
            test_openapi_methods(base_url)

    print(f"\n{'─' * 60}")
    if failures:
        print(f"\033[31m FAILED  {len(failures)} check(s):\033[0m")
        for f in failures:
            print(f"   • {f}")
        sys.exit(1)
    else:
        print(f"\033[32m ALL CHECKS PASSED\033[0m")
        sys.exit(0)


if __name__ == "__main__":
    main()
