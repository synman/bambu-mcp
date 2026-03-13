# Create `calibration/` Directory — Move Calibration Scripts Out of `camera/`

## Background

Four scripts currently live in `camera/` whose primary purpose is calibration,
not camera streaming or real-time analysis. Two of them (`calibrate_idle_nozzle_timeout.py`,
`nozzle_compare.py`) don't even use camera frames for their core logic. Placing them
in `camera/` makes the module structure misleading and caused the idle timeout knowledge
documentation to land in the wrong knowledge module.

This plan also fixes the knowledge/rules misplacement that followed from the wrong file location.

## Current state

| Script | Location | Camera frames? | Notes |
|--------|----------|---------------|-------|
| `calibrate_idle_nozzle_timeout.py` | `camera/` ❌ | None | Pure HTTP API + firmware state |
| `calibrate_tool_change_settle.py` | `camera/` ❌ | Yes (480p snapshots) | Camera used as measurement instrument |
| `corner_calibration.py` | `camera/` ❌ | Yes (corner frames) | Primary geometry calibration |
| `nozzle_compare.py` | `camera/` ❌ | Yes (hotspot frames) | Nozzle hotspot calibration |
| `coord_transform.py` | `camera/` ✅ | N/A | Used by `job_analyzer.py` — stays |

## What Moves

```
camera/calibrate_idle_nozzle_timeout.py  →  calibration/calibrate_idle_nozzle_timeout.py
camera/calibrate_tool_change_settle.py  →  calibration/calibrate_tool_change_settle.py
camera/corner_calibration.py            →  calibration/corner_calibration.py
camera/nozzle_compare.py                →  calibration/nozzle_compare.py
```

None of these import from within the `camera/` package — all imports are stdlib, numpy, PIL,
urllib. No import paths need to change inside the scripts themselves.

## Per-File Changes

| File | What changes | Why |
|------|-------------|-----|
| `calibration/` (new dir) | Create with empty `__init__.py` | Python package for the scripts |
| `camera/calibrate_idle_nozzle_timeout.py` | `git mv` to `calibration/` | Wrong location |
| `camera/calibrate_tool_change_settle.py` | `git mv` to `calibration/` | Wrong location |
| `camera/corner_calibration.py` | `git mv` to `calibration/` | Wrong location |
| `camera/nozzle_compare.py` | `git mv` to `calibration/` | Wrong location |
| `knowledge/behavioral_rules_camera_calibration.py` | Fix 8 path refs (`camera/X` → `calibration/X`); fix command audit heading; **remove** "## Idle Nozzle Heat Timeout" section → replace with 1-line cross-ref | Paths stale after move; idle timeout is firmware behavior, not camera calibration |
| `knowledge/behavioral_rules_print_state.py` | **Add** full "## Idle Nozzle Heat Timeout" section (moved from camera_calibration module) with correct script path | Correct home for gcode_state-dependent firmware behavior |
| `.github/copilot-instructions.md` | Fix 6 path refs (`camera/X` → `calibration/X`); split "Camera light and firmware idle timeout" heading (camera light stays; idle timeout moves to print-state section) | Paths stale after move; heading conflates two unrelated concerns |
| `bambu-rules` sync | `cp + git commit + push` | Rules file changed |

## Risk Assessment

| Change | Risk | Mitigation |
|--------|------|-----------|
| `git mv` 4 scripts | Low — no internal imports to break | Verify with `python3 -c "import calibration.corner_calibration"` after move |
| Knowledge path refs | Low — documentation only | Grep for remaining `camera/corner_calibration`, `camera/nozzle_compare`, etc. after edit |
| Moving idle timeout section | Low — content is just text migration | Cross-reference left in camera_calibration module so no knowledge is lost |
| Rules file split | Low — additive | Verify both sections read coherently after split |

## Open Questions

None — `coord_transform.py` stays in `camera/` (confirmed used by `job_analyzer.py`).

## Not In This Plan

- Re-running the calibration script (separate step after location fix)
- Baking verified constants into `corner_calibration.py` / `nozzle_compare.py`
- 9-point DLT calibration run
