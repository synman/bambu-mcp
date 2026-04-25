# Tighten MCP Knowledge: Empty Job Info Semantics

| Field | Value |
|-------|-------|
| Date | 2026-03-16 17:54 EDT |
| Model | claude-opus-4-6[1m] |
| Status | APPROVED — 2026-03-16 18:03 EDT |
| Scope | bambu-mcp |
| Repos | bambu-mcp |
| Files touched | 2 |

## Context

Agent misinterpreted empty/zeroed `get_job_info()` response as "printer idle long enough that data cleared." The correct meaning: **no job has been executed since the printer's last reboot**. Job info from the last print persists across idle periods — it only resets on power cycle/reboot.

No knowledge module or tool docstring documents this, so the agent had no basis for correct interpretation.

## Approach

Two targeted additions — put the knowledge where the agent will see it at the moment of decision:

### 1. `tools/state.py` — `get_job_info()` docstring (primary fix)

Add an "Empty result interpretation" section to the existing docstring, between the `stage_id` block and the "Shortcuts" block:

```
Empty result interpretation:
- All fields empty/zeroed (subtask_name="", gcode_file="", print_percentage=0,
  stage_id=-1) means NO JOB has been executed since the printer's last
  power cycle or reboot. Job info from the last print persists across idle
  periods — it does NOT clear over time. An empty result always indicates
  a reboot boundary, never "idle too long."
- If gcode_state is IDLE/FINISH/FAILED but fields are populated, that is
  the last completed job's data — still valid and queryable.
```

This is the highest-impact location because the agent reads the tool docstring on every call.

### 2. `knowledge/behavioral_rules_print_state.py` — new section (supplementary)

Add a "Job Info Lifecycle" section after the "Printer State Interpretation" block (before gcode_state Quick Reference):

```
## Job Info Lifecycle (ActiveJobInfo Persistence)

ActiveJobInfo fields persist across printer state transitions (RUNNING → FINISH → IDLE).
The last job's data remains queryable until:
- A new print starts (fields overwritten with new job data), or
- The printer is rebooted/power-cycled (fields reset to defaults: empty strings,
  zeros, stage_id=-1).

**Empty job info = no job since last reboot.** Job data does NOT decay or expire
over time. An empty result always indicates a reboot boundary.
```

## Files to Modify

| File | Change |
|------|--------|
| `tools/state.py:147-175` | Add "Empty result interpretation" block to `get_job_info()` docstring |
| `knowledge/behavioral_rules_print_state.py:28-29` | Add "Job Info Lifecycle" section after printer state interpretation block |

## Verification

1. `python -m py_compile tools/state.py` — syntax check
2. `python -m py_compile knowledge/behavioral_rules_print_state.py` — syntax check
3. Full MCP restart sequence (per shared rules)
4. Call `get_job_info("H2D")` — verify the tool description now includes empty result semantics
5. Call `get_knowledge_topic("behavioral_rules/print_state")` — verify new section appears

## Infrastructure Leveraged

No new infrastructure required. Changes are pure content additions to existing knowledge modules and tool docstrings within the established bambu-mcp knowledge architecture (41 modules, registry-based topic lookup).

## Quality Gate Checklist

| Gate | Status | Notes |
|------|--------|-------|
| 1. Scope Definition | PASS | Add empty job info semantics to 2 files in bambu-mcp |
| 2. Current State Assessment | PASS | Read both target files; infrastructure audit — no existing coverage found |
| 3. Impact & Dependency Analysis | PASS | Docstring change propagates to MCP tool description automatically; knowledge module served via existing registry — no downstream breakage |
| 4. Approach Selection | PASS | Two locations (tool docstring + knowledge module) vs knowledge module only. Chose both — docstring is highest-impact (agent sees it on every call), knowledge module provides supplementary context |
| 5. Verification Plan | PASS | py_compile both files, full MCP restart, verify via tool call and knowledge topic |
| 6. Plan Recap | PASS | Rendered to HTML and opened in browser |
| 7. Anti-Dilution | N/A | No rules changes — knowledge content only |
