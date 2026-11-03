"""resources/rules.py reads each rules file from the setting that names it, and nothing else.

A slot with no setting answers "not configured" and never looks in a home directory. Values the
file holds that look like addresses, serial numbers, access codes or credentials are redacted,
and one extra hostname pattern can come from BAMBU_MCP_REDACT_HOST_PATTERN. Nothing here touches
a printer.

Run directly (no pytest needed):  .venv/bin/python3 test_rules_resource.py
"""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from resources import rules  # noqa: E402

SLOTS = ("BAMBU_MCP_RULES_GLOBAL", "BAMBU_MCP_RULES_PRINTER_APP",
         "BAMBU_MCP_RULES_PRINTER_MANAGER", "BAMBU_MCP_RULES_BAMBU_MCP", "BAMBU_MCP_REDACT_HOST_PATTERN")


class _Env:
    def __init__(self, **values):
        self.values = values

    def __enter__(self):
        self.saved = {k: os.environ.pop(k, None) for k in SLOTS}
        os.environ.update(self.values)

    def __exit__(self, *exc):
        for k in SLOTS:
            os.environ.pop(k, None)
        os.environ.update({k: v for k, v in self.saved.items() if v is not None})


def test_unset_slots_answer_not_configured_for_all_four():
    with _Env():
        for fn in (rules.get_global_rules, rules.get_printer_app_rules,
                   rules.get_printer_manager_rules, rules.get_bambu_mcp_rules):
            out = fn()
            assert out.startswith("Rules file not configured: set BAMBU_MCP_RULES_"), out


def test_a_configured_file_is_served_redacted():
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "rules.md"
        f.write_text("printer at 192.0.2.7 serial ABCDEF012345678 code AB12CD34 token: hunter2\n"
                     "visit bambu-h2d.example.test for the container\n")
        with _Env(BAMBU_MCP_RULES_GLOBAL=str(f), BAMBU_MCP_REDACT_HOST_PATTERN=r"bambu-[a-z0-9]+\.example\.test"):
            out = rules.get_global_rules()
        for leaked in ("192.0.2.7", "ABCDEF012345678", "AB12CD34", "hunter2", "bambu-h2d.example.test"):
            assert leaked not in out, f"{leaked} leaked: {out}"
        for marker in ("<ip-redacted>", "<serial-redacted>", "<access-code-redacted>", "<hostname-redacted>"):
            assert marker in out, f"{marker} missing: {out}"


def test_without_the_host_pattern_hostnames_are_left_alone():
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "rules.md"
        f.write_text("visit bambu-h2d.example.test\n")
        with _Env(BAMBU_MCP_RULES_GLOBAL=str(f)):
            assert "bambu-h2d.example.test" in rules.get_global_rules()


def test_a_missing_file_is_named_not_guessed():
    with _Env(BAMBU_MCP_RULES_PRINTER_APP="/nonexistent/rules.md"):
        assert rules.get_printer_app_rules() == "Rules file not found: /nonexistent/rules.md"


def test_no_default_path_points_into_a_home_directory():
    src = (Path(rules.__file__)).read_text()
    assert "Path.home()" not in src and "expanduser" in src and ".copilot" not in src


if __name__ == "__main__":
    import traceback

    failed = 0
    for tname, fn in sorted(globals().items()):
        if tname.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"ok   {tname}")
            except Exception:
                failed += 1
                print(f"FAIL {tname}")
                traceback.print_exc()
    print(f"{failed} failed")
    sys.exit(1 if failed else 0)
