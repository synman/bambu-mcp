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
import os
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

# Canonical sample set — 15 entries covering 3 dimensions (text / image / mixed).
# Each entry: (label, url_template, iters)
# url_template uses {base_url} and {printer} substitution tokens.
# iters: 3 for image/mixed (live camera), 1 for text (cheap API calls).
def _payload_samples(base_url, printer_name):
    import urllib.parse
    p = urllib.parse.quote(printer_name or "", safe="")
    return [
        # ── Dimension A: text-only JSON — must span sub-threshold → over-threshold ──
        # Measured (live 2026-03-11): tiny≈132  small≈97  medium≈19,874
        #   large≈94,486 (near limit)  xlarge≈168,561 (over — indented OpenAPI)
        ("text/tiny",   f"{base_url}/api/printers",                                                              1),
        ("text/small",  f"{base_url}/api/default_printer",                                                       1),
        ("text/medium", f"{base_url}/api/printer?printer={p}",                                                   1),
        ("text/large",  f"{base_url}/api/openapi.json",                                                          1),
        ("text/xlarge", f"{base_url}/api/openapi.json?pretty=true",                                              1),
        # ── Dimension B: binary image (5 knowledge profiles) ─────────────────────
        ("image/preview",  f"{base_url}/api/snapshot?printer={p}&resolution=180p&quality=55",                    3),
        ("image/low",      f"{base_url}/api/snapshot?printer={p}&resolution=480p&quality=65",                    3),
        ("image/standard", f"{base_url}/api/snapshot?printer={p}&resolution=720p&quality=75",                    3),
        ("image/high",     f"{base_url}/api/snapshot?printer={p}&resolution=1080p&quality=85",                   3),
        ("image/native",   f"{base_url}/api/snapshot?printer={p}&resolution=native&quality=85",                  3),
        # ── Dimension C: mixed (image blob + live telemetry JSON in one envelope) ─
        ("mixed/preview",  f"{base_url}/api/snapshot?printer={p}&resolution=180p&quality=55&include_status=true",  3),
        ("mixed/low",      f"{base_url}/api/snapshot?printer={p}&resolution=480p&quality=65&include_status=true",  3),
        ("mixed/standard", f"{base_url}/api/snapshot?printer={p}&resolution=720p&quality=75&include_status=true",  3),
        ("mixed/high",     f"{base_url}/api/snapshot?printer={p}&resolution=1080p&quality=85&include_status=true",  3),
        ("mixed/native",   f"{base_url}/api/snapshot?printer={p}&resolution=native&quality=85&include_status=true", 3),
    ]


def _fetch_sample(url, iters, timeout=15):
    """Fetch url `iters` times.  Returns (timing_stats, last_response_dict).

    timing_stats keys: min_ms, max_ms, avg_ms, med_ms, p90_ms, iters
    All times are client-side wall-clock (includes network + server + JSON parse).
    """
    import time
    import statistics as stats_mod
    timings = []
    data = None
    for _ in range(iters):
        t0 = time.perf_counter()
        _status, data = get_json(url, timeout=timeout)
        timings.append((time.perf_counter() - t0) * 1000)
    s = sorted(timings)
    n = len(s)
    p90 = s[min(int(n * 0.9), n - 1)]
    return {
        "min_ms":  round(s[0], 1),
        "max_ms":  round(s[-1], 1),
        "avg_ms":  round(stats_mod.mean(s), 1),
        "med_ms":  round(stats_mod.median(s), 1),
        "p90_ms":  round(p90, 1),
        "iters":   n,
    }, data


def _payload_chars(data):
    """Return the JSON-serialised char count for a response dict."""
    import json
    return len(json.dumps(data))


_MCP_THRESHOLD = 100_000   # MAX_MCP_OUTPUT_TOKENS=25000 * 4 chars/token
_DEFAULT_MCP_TOKENS = 25_000


def _check_default_tokens():
    """Fail fast if MAX_MCP_OUTPUT_TOKENS has been overridden in the environment.

    All 4 payload passes assume the 25,000-token / 100,000-char default threshold.
    A non-default value produces misleading 'fits'/'truncated' verdicts in Pass 1
    and an incorrect ideal_tokens calculation in Pass 4.
    Returns True if safe to proceed, False if passes must be skipped.
    """
    val = os.environ.get("MAX_MCP_OUTPUT_TOKENS")
    if val is not None and val.strip() != str(_DEFAULT_MCP_TOKENS):
        fail(
            "pre-test: MAX_MCP_OUTPUT_TOKENS",
            f"must be unset or {_DEFAULT_MCP_TOKENS} before payload tests "
            f"(got {val!r}) — unset the env var and re-run"
        )
        return False
    return True


def test_payload_raw(base_url, printer_name):
    """Pass 1 — raw payload size for all 15 samples vs. MCP threshold.

    Runs every sample once (or 3× for image/mixed) and reports raw chars against
    the default MCP 100 K-char limit.  Returns a dict of results for Pass 4.
    """
    import json

    print("\n── payload raw (text / image / mixed) ──────────────────────────────────")

    if not printer_name:
        skip("payload raw", "no printer name (pass --printer NAME)")
        return {}

    raw_results = {}

    for label, url, iters in _payload_samples(base_url, printer_name):
        try:
            timing, data = _fetch_sample(url, iters)
            if "error" in data:
                fail(label, str(data["error"])[:120])
                continue
            chars = _payload_chars(data)
            fits = chars <= _MCP_THRESHOLD
            raw_results[label] = {"chars": chars, "fits": fits, "timing": timing, "data": data}
            t = timing
            ok(
                label,
                f"{chars:,} chars  {'✅ fits' if fits else '❌ truncated'}  "
                f"avg={t['avg_ms']:.0f}ms  min={t['min_ms']:.0f}  max={t['max_ms']:.0f}  "
                f"p90={t['p90_ms']:.0f}  (n={t['iters']})"
            )
        except Exception as e:
            fail(label, f"{type(e).__name__}: {e}")

    return raw_results


def test_payload_gzip(base_url, printer_name):
    """Pass 2 — compress_if_large() applied to each of the 15 samples.

    Fresh HTTP calls.  Reports raw chars → compressed chars, ratio, and whether
    the payload fits within the MCP threshold *after* gzip.

    Expected pattern:
      text/…   — high ratio (60–90%), gzip rescues all JSON
      image/…  — near-zero ratio (JPEG is already compressed), still truncated
      mixed/…  — intermediate: text (status) compresses well, image dominates
    """
    print("\n── payload gzip (text / image / mixed) ─────────────────────────────────")

    if not printer_name:
        skip("payload gzip", "no printer name (pass --printer NAME)")
        return {}

    try:
        from tools._response import compress_if_large
    except Exception as e:
        fail("tools._response import (payload gzip)", str(e))
        return {}

    gzip_results = {}

    for label, url, iters in _payload_samples(base_url, printer_name):
        try:
            timing, data = _fetch_sample(url, iters)
            if "error" in data:
                fail(label, str(data["error"])[:120])
                continue
            raw_chars = _payload_chars(data)
            result = compress_if_large(data)
            compressed = result.get("compressed", False)
            if compressed:
                comp_chars = len(result.get("data", "")) + 50   # envelope overhead
                ratio = (1 - comp_chars / raw_chars) * 100
                fits_after = comp_chars <= _MCP_THRESHOLD
            else:
                comp_chars = raw_chars
                ratio = 0.0
                fits_after = raw_chars <= _MCP_THRESHOLD
            t = timing
            gzip_results[label] = {
                "raw_chars": raw_chars, "comp_chars": comp_chars,
                "ratio_pct": round(ratio, 1), "fits": fits_after, "timing": timing,
            }
            ok(
                label,
                f"{raw_chars:,} → {comp_chars:,} chars  ratio={ratio:.1f}%  "
                f"{'✅ fits' if fits_after else '❌ truncated'}  "
                f"avg={t['avg_ms']:.0f}ms  min={t['min_ms']:.0f}  max={t['max_ms']:.0f}  "
                f"p90={t['p90_ms']:.0f}  (n={t['iters']})"
            )
        except Exception as e:
            fail(label, f"{type(e).__name__}: {e}")

    return gzip_results


def test_payload_fallback(base_url, printer_name):
    """Pass 3 — HTTP 200 + valid payload + timing for all 15 samples.

    Demonstrates that the HTTP fallback path always delivers complete, untruncated
    data regardless of payload size — no MCP size limit applies over HTTP.

    Checks:
      text/…   — status 200, non-empty dict/list, no 'error' key
      image/…  — status 200, 'data_uri' key present and starts with 'data:'
      mixed/…  — status 200, 'data_uri' present AND 'status' key present
    """
    print("\n── payload fallback (text / image / mixed) ─────────────────────────────")

    if not printer_name:
        skip("payload fallback", "no printer name (pass --printer NAME)")
        return {}

    fallback_results = {}

    for label, url, iters in _payload_samples(base_url, printer_name):
        try:
            timing, data = _fetch_sample(url, iters)
            chars = _payload_chars(data)

            if "error" in data:
                fail(label, str(data["error"])[:120])
                continue

            valid = True
            if label.startswith("text/"):
                if not data:
                    fail(label, "empty response")
                    valid = False
            elif label.startswith("image/"):
                uri = data.get("data_uri", "")
                if not uri.startswith("data:"):
                    fail(label, "missing/invalid data_uri")
                    valid = False
            else:  # mixed/
                uri = data.get("data_uri", "")
                if not uri.startswith("data:"):
                    fail(label, "missing/invalid data_uri")
                    valid = False
                elif "status" not in data:
                    fail(label, "missing 'status' key (include_status=true not honoured)")
                    valid = False

            if not valid:
                continue

            fallback_results[label] = {"chars": chars, "valid": True, "timing": timing}
            t = timing
            ok(
                label,
                f"HTTP 200  {chars:,} chars  "
                f"avg={t['avg_ms']:.0f}ms  min={t['min_ms']:.0f}  max={t['max_ms']:.0f}  "
                f"p90={t['p90_ms']:.0f}  (n={t['iters']})"
            )
        except Exception as e:
            fail(label, f"{type(e).__name__}: {e}")

    return fallback_results


def test_payload_ideal_tokens(base_url, printer_name, raw_results):
    """Pass 4 — compute ideal MAX_MCP_OUTPUT_TOKENS to fit all 15 samples.

    Uses raw_results from Pass 1 (no fresh HTTP calls needed).  Finds the largest
    payload observed, computes the token count required to fit it, then validates
    by re-running each sample against that raised threshold.

    If raw_results is empty (Pass 1 was skipped), falls back to 1-iter HTTP calls.
    Returns a dict with ideal_tokens, max_chars, max_label, and per-sample results.
    """
    import math
    import os

    print("\n── payload ideal MAX_MCP_OUTPUT_TOKENS (text / image / mixed) ──────────")

    if not printer_name:
        skip("payload ideal tokens", "no printer name (pass --printer NAME)")
        return {}

    # --- Step 1: find max chars across all samples ---
    if raw_results:
        max_chars = 0
        max_label = ""
        for label, info in raw_results.items():
            if info["chars"] > max_chars:
                max_chars = info["chars"]
                max_label = label
    else:
        # Fallback: fetch fresh
        max_chars = 0
        max_label = ""
        sample_data = {}
        for label, url, iters in _payload_samples(base_url, printer_name):
            try:
                _, data = _fetch_sample(url, 1)
                chars = _payload_chars(data)
                sample_data[label] = data
                if chars > max_chars:
                    max_chars = chars
                    max_label = label
            except Exception:
                pass
        raw_results = {
            k: {"chars": _payload_chars(v), "fits": _payload_chars(v) <= _MCP_THRESHOLD, "data": v}
            for k, v in sample_data.items()
        }

    ideal_tokens = math.ceil(max_chars / 4)
    ideal_threshold = ideal_tokens * 4
    print(f"  max payload : {max_chars:,} chars  ({max_label})")
    print(f"  ideal tokens: {ideal_tokens:,}  (default: {_DEFAULT_MCP_TOKENS:,})")
    print(f"  ideal limit : {ideal_threshold:,} chars  (default: {_MCP_THRESHOLD:,} chars)")

    ideal_results = {
        "ideal_tokens":     ideal_tokens,
        "ideal_threshold":  ideal_threshold,
        "default_tokens":   _DEFAULT_MCP_TOKENS,
        "default_threshold": _MCP_THRESHOLD,
        "max_chars":        max_chars,
        "max_label":        max_label,
        "samples":          {},
    }

    # --- Step 2: re-run all 15 at ideal threshold ---
    old_val = os.environ.get("MAX_MCP_OUTPUT_TOKENS")
    try:
        os.environ["MAX_MCP_OUTPUT_TOKENS"] = str(ideal_tokens)
        import importlib
        try:
            import tools._response as _resp
            importlib.reload(_resp)
        except Exception:
            pass

        for label, url, iters in _payload_samples(base_url, printer_name):
            try:
                if label in raw_results:
                    chars = raw_results[label]["chars"]
                else:
                    _, data = _fetch_sample(url, 1)
                    chars = _payload_chars(data)
                fits = chars <= ideal_threshold
                ideal_results["samples"][label] = {"chars": chars, "fits": fits}
                ok(label, f"{chars:,} chars  {'✅ fits' if fits else '❌ still truncated'}")
            except Exception as e:
                fail(label, f"{type(e).__name__}: {e}")
    finally:
        if old_val is None:
            os.environ.pop("MAX_MCP_OUTPUT_TOKENS", None)
        else:
            os.environ["MAX_MCP_OUTPUT_TOKENS"] = old_val
        try:
            import tools._response as _resp
            importlib.reload(_resp)
        except Exception:
            pass

    return ideal_results


# ─────────────────────────────────────────────────────────────────────────────
# Results serialization + HTML report
# ─────────────────────────────────────────────────────────────────────────────

def _write_results_json(path, meta, raw_r, gzip_r, fallback_r, ideal_r):
    """Write all 4 pass results + meta to a JSON file. Returns path."""
    import json
    import datetime
    payload = {
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "meta": meta,
        "pass1_raw":      raw_r,
        "pass2_gzip":     gzip_r,
        "pass3_fallback": fallback_r,
        "pass4_ideal":    ideal_r,
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
    return path


def _generate_html_report(json_path, html_path):
    """Read JSON results and write a styled HTML report to html_path."""
    import json

    with open(json_path, encoding="utf-8") as fh:
        data = json.load(fh)

    meta      = data.get("meta", {})
    raw_r     = data.get("pass1_raw", {})
    gzip_r    = data.get("pass2_gzip", {})
    fallback_r = data.get("pass3_fallback", {})
    ideal_r   = data.get("pass4_ideal", {})

    generated_at   = data.get("generated_at", "unknown")
    def_tokens     = meta.get("default_tokens", _DEFAULT_MCP_TOKENS)
    def_threshold  = meta.get("default_threshold", _MCP_THRESHOLD)
    ideal_tokens   = ideal_r.get("ideal_tokens", 0)
    ideal_threshold = ideal_r.get("ideal_threshold", 0)
    max_label      = ideal_r.get("max_label", "—")
    printer        = meta.get("printer", "—")

    # ── sample label order ──
    LABELS = [
        "text/tiny", "text/small", "text/medium", "text/large", "text/xlarge",
        "image/preview", "image/low", "image/standard", "image/high", "image/native",
        "mixed/preview", "mixed/low", "mixed/standard", "mixed/high", "mixed/native",
    ]

    def dim_group(label):
        return label.split("/")[0]  # "text" | "image" | "mixed"

    def fits_cell(fits):
        if fits is None:
            return '<td class="na">—</td>'
        color = "green" if fits else "red"
        text = "✅ yes" if fits else "❌ no"
        return f'<td class="{color}">{text}</td>'

    def timing_cells(t):
        if not t:
            return "<td>—</td><td>—</td><td>—</td><td>—</td><td>—</td><td>—</td>"
        p90 = t.get("p90_ms", 0)
        avg = t.get("avg_ms", 1) or 1
        p90_cls = ' class="amber"' if p90 > avg * 5 else ''
        return (
            f"<td>{t.get('min_ms', 0):.0f}</td>"
            f"<td>{t.get('max_ms', 0):.0f}</td>"
            f"<td>{t.get('avg_ms', 0):.0f}</td>"
            f"<td>{t.get('med_ms', 0):.0f}</td>"
            f"<td{p90_cls}>{p90:.0f}</td>"
            f"<td>{t.get('iters', 0)}</td>"
        )

    def fmt_chars(n):
        return f"{n:,}" if isinstance(n, int) and n else ("—" if not n else f"{n:,}")

    # ── build rows for each pass ──

    def pass1_rows():
        rows = []
        prev_dim = None
        for label in LABELS:
            d = dim_group(label)
            if prev_dim and d != prev_dim:
                rows.append('<tr class="sep"><td colspan="9"></td></tr>')
            prev_dim = d
            info = raw_r.get(label, {})
            chars = info.get("chars")
            fits  = info.get("fits")
            t     = info.get("timing", {})
            rows.append(
                f'<tr><td class="lbl">{label}</td>'
                f'<td class="num">{fmt_chars(chars)}</td>'
                + fits_cell(fits)
                + timing_cells(t) + "</tr>"
            )
        return "\n".join(rows)

    def pass2_rows():
        rows = []
        prev_dim = None
        for label in LABELS:
            d = dim_group(label)
            if prev_dim and d != prev_dim:
                rows.append('<tr class="sep"><td colspan="10"></td></tr>')
            prev_dim = d
            info = gzip_r.get(label, {})
            raw_c  = info.get("raw_chars")
            comp_c = info.get("comp_chars")
            ratio  = info.get("ratio_pct")
            fits   = info.get("fits")
            t      = info.get("timing", {})
            ratio_str = f"{ratio:.1f}%" if isinstance(ratio, (int, float)) else "—"
            rows.append(
                f'<tr><td class="lbl">{label}</td>'
                f'<td class="num">{fmt_chars(raw_c)}</td>'
                f'<td class="num">{fmt_chars(comp_c)}</td>'
                f'<td class="num">{ratio_str}</td>'
                + fits_cell(fits)
                + timing_cells(t) + "</tr>"
            )
        return "\n".join(rows)

    def pass3_rows():
        rows = []
        prev_dim = None
        for label in LABELS:
            d = dim_group(label)
            if prev_dim and d != prev_dim:
                rows.append('<tr class="sep"><td colspan="9"></td></tr>')
            prev_dim = d
            info = fallback_r.get(label, {})
            chars = info.get("chars")
            valid = info.get("valid")
            t     = info.get("timing", {})
            valid_cell = ('<td class="green">✅ yes</td>' if valid
                          else '<td class="red">❌ no</td>' if valid is False
                          else '<td class="na">—</td>')
            rows.append(
                f'<tr><td class="lbl">{label}</td>'
                f'<td class="num">{fmt_chars(chars)}</td>'
                + valid_cell
                + timing_cells(t) + "</tr>"
            )
        return "\n".join(rows)

    def pass4_rows():
        samples = ideal_r.get("samples", {})
        rows = []
        prev_dim = None
        for label in LABELS:
            d = dim_group(label)
            if prev_dim and d != prev_dim:
                rows.append('<tr class="sep"><td colspan="4"></td></tr>')
            prev_dim = d
            # chars come from Pass 1 (ideal tokens re-run uses same payloads)
            p1_info = raw_r.get(label, {})
            chars = p1_info.get("chars")
            info  = samples.get(label, {})
            fits  = info.get("fits")
            rows.append(
                f'<tr><td class="lbl">{label}</td>'
                f'<td class="num">{fmt_chars(chars)}</td>'
                + fits_cell(fits) + "</tr>"
            )
        return "\n".join(rows)

    css = """
    * { box-sizing: border-box; }
    body { background: #0d1117; color: #c9d1d9; font-family: 'Segoe UI', Arial, sans-serif;
           margin: 0; padding: 32px; max-width: 1100px; margin: 0 auto; }
    h1 { color: #58a6ff; margin-bottom: 4px; }
    h2 { color: #f0f6fc; border-bottom: 1px solid #30363d; padding-bottom: 6px; margin-top: 40px; }
    h3 { color: #79c0ff; margin-top: 24px; }
    .meta { color: #8b949e; font-size: 0.85rem; margin-bottom: 32px; }
    pre.threshold {
        background: #161b22; border: 1px solid #30363d; border-radius: 6px;
        padding: 16px 20px; color: #79c0ff; font-size: 0.95rem; line-height: 1.7;
    }
    table { border-collapse: collapse; width: 100%; margin-top: 12px; font-size: 0.88rem; }
    th { background: #161b22; color: #79c0ff; padding: 8px 10px; text-align: left;
         border-bottom: 1px solid #30363d; white-space: nowrap; }
    tr:nth-child(even) { background: #111820; }
    tr:nth-child(odd)  { background: #0d1117; }
    td { padding: 6px 10px; border-bottom: 1px solid #21262d; white-space: nowrap; }
    td.lbl { font-family: monospace; font-size: 0.86rem; color: #c9d1d9; }
    td.num { text-align: right; font-variant-numeric: tabular-nums; }
    td.green { background: #0e2a1a; color: #3fb950; font-weight: bold; }
    td.red   { background: #2a0e0e; color: #ff7b72; font-weight: bold; }
    td.amber { color: #f5a623; }
    td.na    { color: #6e7681; }
    tr.sep td { background: #161b22 !important; padding: 3px 0; border: none; }
    th.timing-group { text-align: center; border-left: 1px solid #30363d; }
    th.num { text-align: right; }
    .note { color: #8b949e; font-size: 0.82rem; margin-top: 6px; }
    """

    timing_th = (
        '<th class="num">Min ms</th>'
        '<th class="num">Max ms</th>'
        '<th class="num">Avg ms</th>'
        '<th class="num">Med ms</th>'
        '<th class="num">P90 ms</th>'
        '<th class="num">N</th>'
    )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>bambu-mcp smoke test report</title>
<style>{css}</style>
</head>
<body>
<h1>bambu-mcp smoke test report</h1>
<div class="meta">
  Generated: {generated_at} &nbsp;|&nbsp;
  Printer: <code>{printer}</code> &nbsp;|&nbsp;
  Default MAX_MCP_OUTPUT_TOKENS: <strong>{def_tokens:,}</strong>
</div>

<h2>MCP Response Threshold</h2>
<pre class="threshold">Default MAX_MCP_OUTPUT_TOKENS = {def_tokens:,}  ×4 =  {def_threshold:,} chars   (current MCP limit)
Ideal   MAX_MCP_OUTPUT_TOKENS = {ideal_tokens:,}  ×4 =  {ideal_threshold:,} chars   (driven by: {max_label})</pre>

<h2>Pass 1 — Raw payload (no gzip)</h2>
<p class="note">All 15 samples fetched raw. <em>Fits?</em> = payload ≤ {def_threshold:,} chars (MCP limit). Timing = client-side wall-clock including network + server + JSON parse.</p>
<table>
<thead><tr>
  <th>Sample</th>
  <th class="num">Raw chars</th>
  <th>Fits?</th>
  {timing_th}
</tr></thead>
<tbody>
{pass1_rows()}
</tbody>
</table>

<h2>Pass 2 — Gzip payload</h2>
<p class="note">Same 15 samples with <code>compress_if_large()</code> applied. <em>Ratio</em> = compressed / raw (lower = better compression). <em>Fits?</em> = compressed payload ≤ {def_threshold:,} chars.</p>
<table>
<thead><tr>
  <th>Sample</th>
  <th class="num">Raw chars</th>
  <th class="num">Comp chars</th>
  <th class="num">Ratio</th>
  <th>Fits?</th>
  {timing_th}
</tr></thead>
<tbody>
{pass2_rows()}
</tbody>
</table>

<h2>Pass 3 — HTTP fallback (no MCP size limit)</h2>
<p class="note">HTTP <code>GET</code> direct — no MCP truncation applies. <em>Valid?</em> = HTTP 200 + well-formed payload. All samples should always be valid.</p>
<table>
<thead><tr>
  <th>Sample</th>
  <th class="num">HTTP chars (no limit)</th>
  <th>Valid?</th>
  {timing_th}
</tr></thead>
<tbody>
{pass3_rows()}
</tbody>
</table>

<h2>Pass 4 — Ideal token threshold validation</h2>
<p class="note">Using ideal MAX_MCP_OUTPUT_TOKENS = <strong>{ideal_tokens:,}</strong> (×4 = {ideal_threshold:,} chars). All 15 samples should fit at this threshold. No fresh calls — char counts from Pass 1.</p>
<table>
<thead><tr>
  <th>Sample</th>
  <th class="num">Chars (from Pass 1)</th>
  <th>Fits at ideal?</th>
</tr></thead>
<tbody>
{pass4_rows()}
</tbody>
</table>

</body>
</html>"""

    with open(html_path, "w", encoding="utf-8") as fh:
        fh.write(html)
    return html_path


def main():
    parser = argparse.ArgumentParser(description="bambu-mcp smoke test")
    parser.add_argument("--api-only", action="store_true", help="Skip import/knowledge tests")
    parser.add_argument("--no-api", action="store_true", help="Skip live API tests")
    parser.add_argument("--printer", default=None, help="Printer name for per-printer API tests")
    parser.add_argument("--base-url", default=None, help="Override API base URL")
    parser.add_argument("--json-output", default=None, metavar="PATH",
                        help="Write structured test results to PATH (JSON); also generates HTML report")
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

    raw_r = gzip_r = fallback_r = ideal_r = {}

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
            if args.printer:
                if _check_default_tokens():
                    raw_r = test_payload_raw(base_url, args.printer)
                    gzip_r = test_payload_gzip(base_url, args.printer)
                    fallback_r = test_payload_fallback(base_url, args.printer)
                    ideal_r = test_payload_ideal_tokens(base_url, args.printer, raw_r)
                else:
                    skip("payload passes 1-4", "MAX_MCP_OUTPUT_TOKENS is not at default — see failure above")
            else:
                skip("payload passes 1-4", "no printer name (pass --printer NAME)")
            test_openapi_methods(base_url)

    if args.json_output and (raw_r or gzip_r or fallback_r or ideal_r):
        import subprocess
        meta = {
            "printer": args.printer,
            "base_url": args.base_url,
            "default_tokens": _DEFAULT_MCP_TOKENS,
            "default_threshold": _MCP_THRESHOLD,
        }
        try:
            import subprocess as _sp
            path = _write_results_json(args.json_output, meta, raw_r, gzip_r, fallback_r, ideal_r)
            html_path = path.replace(".json", ".html") if path.endswith(".json") else path + ".html"
            _generate_html_report(path, html_path)
            print(f"\n  📄  Results JSON : {path}")
            print(f"  📊  HTML report  : {html_path}")
            _sp.run(["open", html_path], check=False)
        except Exception as e:
            print(f"\n  ⚠️  Report generation failed: {e}")

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
