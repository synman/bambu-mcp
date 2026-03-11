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


def ok(label):
    print(f"  {PASS}  {label}")


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
