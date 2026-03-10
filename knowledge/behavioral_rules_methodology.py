"""
behavioral_rules_methodology.py — KISS, quality-first, verification, telemetry parity rules.

Sub-topic of behavioral_rules. Access via get_knowledge_topic('behavioral_rules/methodology').
"""

from __future__ import annotations

BEHAVIORAL_RULES_METHODOLOGY_TEXT: str = """
# Behavioral Rules — Methodology

---

## KISS Principle (Mandatory)

Keep It Simple and Straightforward — hard requirement, no exceptions.

Pre-implementation gate (run BEFORE writing any code):
1. Does existing code already solve or partially solve this?
2. Did the user suggest a specific approach? Evaluate that first.
3. Am I about to introduce new state, flags, helpers, or abstractions? Can the
   same result be achieved by reusing what already exists?
4. Is the simplest viable solution the one I'm about to implement?

Enforcement check before finalizing:
- Is every added line necessary to satisfy the request?
- Did I avoid complexity for hypothetical future use?
- Can a maintainer understand this quickly without deep context?

---

## Quality-First Mode (Mandatory)

For all work, prioritize quality over speed.
- Prefer correctness, conciseness, repeatability, and thorough analysis.
- Verify assumptions with source evidence before editing, even for small changes.
- Keep responses concise but complete; do not skip required validation to save time.
- Use deterministic, minimal patches that are easy to review and reproduce.

---

## Root Cause Fix Rule (Mandatory)

When the root cause is identified, fix it there. Do not introduce workarounds, shims,
or compensating logic elsewhere to paper over a bug when a direct fix is available.

Anti-patterns (never do these):
- "I'll add a build stage so the display script doesn't have to handle this edge case"
- "I'll wrap the call to avoid fixing the underlying function"

Pre-fix gate:
1. Have I identified the file and line(s) where the defect lives? Fix it there.
2. Am I about to change something OTHER than the defective code? Stop and explain
   why a direct fix is impossible before proceeding.

---

## Telemetry Mapping Parity Rule (Mandatory)

When adding or changing support for a telemetry field that belongs to an existing
family (e.g. print_option flags), the implementation MUST follow the proven pattern
used by sibling fields unless direct evidence proves otherwise.

Hard requirements:
- Use the nearest working sibling field as the baseline reference
  (e.g. `nozzle_blob_detect` for print_option flags).
- Verify where sibling values are sourced (e.g. `home_flag` bitfield, `cfg`, `xcam`,
  or explicit key) before coding.
- If sibling print_option state is sourced from `home_flag`, default new sibling
  steady-state mapping to `home_flag` as well unless direct evidence proves otherwise.
- Do not introduce a new parsing path unless verified evidence shows sibling parity
  is invalid.

Evidence requirements before coding telemetry mappings:
1. Confirm upstream behavior in at least one authoritative source (BambuStudio preferred).
2. Confirm current project behavior for sibling fields in source code.
3. Confirm runtime evidence (logs/payloads) and classify it correctly:
   - command ack payloads confirm command acceptance
   - status payloads or bitfields confirm steady-state telemetry source
4. Implement using the source that represents steady-state truth unless proven otherwise.

Anti-fabrication guardrails:
- Never invent a new telemetry lifecycle model when a proven local pattern exists.
- Never treat command ack events as a substitute for continuous status mapping.

Parity checklist (all must be true before finalizing):
- Did I compare against at least one sibling field implementation line-by-line?
- Did I verify the same source-of-truth path for both fields?
- Did I avoid adding special-case logic without explicit evidence?
- Did I document why this field matches or intentionally differs from sibling behavior?

---

## Verification First — Before Any Work (Mandatory)

Never infer architecture, features, behavior, or implementation details from partial
code patterns. ALWAYS verify against actual source code before acting.

Verification checklist for ANY claim about the codebase:
1. What does the code actually do? (Read the implementation, not inferred architecture)
2. Is this feature really used? (Search for actual calls/invocations)
3. Does this path actually execute? (Check conditionals, handlers, try/catch blocks)
4. Are there edge cases? (Search for all usages, not just one example)
5. What do authoritative reference implementations do?

Pattern Matching Trap:
- Template exists ≠ command is sent
- Message type defined ≠ message is parsed
- Function exists ≠ function is called

Truncated File Read Trap:
- A truncated file read is NOT a complete read. Absence in truncated output is NOT
  evidence of absence. Always follow up with a targeted symbol search.

Mandatory verification depth:
1. Read the method implementation — actual source code
2. Trace data flow — line-by-line how data is constructed/transformed
3. Document actual values — use real examples from code, not placeholders
4. Check conditionals — document when behavior changes

---

## Cross-Model Compatibility Policy (Mandatory)

This project targets ALL Bambu Lab printers, not a single model.

Default behavior:
- Preserve existing/legacy cross-model behavior unless there is verified evidence it fails.
- Prefer Bambu Studio-compatible behavior as the broad baseline across models.

Override gate (strict):
- Add model-specific overrides only when current/legacy logic is PROVEN to fail.
- Missing fields alone are not a legacy-logic failure.
- Do not alter existing `ams_mapping` semantics unless breakage is demonstrated.

---

## Printer Name Verification (Mandatory)

Printer names are not stable across sessions. After any MCP reload, tool
reconnect, or at the start of a new session, treat all printer names as unknown.

Rules:
- Before calling any per-printer tool (get_printer_state, view_stream,
  get_temperatures, etc.), verify the target name exists by calling
  get_configured_printers() if the name has not already been confirmed in the
  current session context.
- Do NOT assume printer names from prior sessions, documentation examples, or
  model names (e.g. "X1E", "H2D") — the registered name is user-chosen and
  may differ from the hardware model.
- After a get_configured_printers() call, use the exact "name" value returned.
  The name is case-sensitive.

Common failure mode: calling a per-printer tool with a guessed name immediately
after an MCP reload, before verifying configured printers. Always resolve names
first.

---

## Response Endings Rule (Mandatory)

Close responses with the task result only. Do not append unsolicited recommendations,
optional follow-up offers, or unrelated "next step" suggestions.

- Do not include "If you want, I can..." style add-ons.
- Do not propose extra cleanups, refactors, audits, or expansions not requested.
- Keep endings factual and scoped to what was requested, changed, and validated.

---

## Rules Maintenance Rules

File hierarchy:
- Global file (~/.copilot/copilot-instructions.md): universal behavioral rules
- Project files (.github/copilot-instructions.md): project-specific extensions only

No-duplication rule: Never copy a global rule verbatim into a project file.
If a global rule needs a project-specific addition, add only the delta.

Adding a new rule:
1. Universal? → Global file only.
2. Project-specific? → Relevant project file only.
3. Project-specific extension of global rule? → Add only the extension/delta to
   the project file; the global base already applies.

Never leave files in a state where a project file rule contradicts the global file.

---

## Security & Privacy (Mandatory)

- Never log, display, or commit secrets, tokens, access codes, or passwords.
- Treat everything under tests/ as private/sensitive validation data. Never
  reference, quote, link, or cite test fixture files in public-facing responses.
  Use source code under src/ and public upstream repositories for provenance.
- Validate all user-provided paths, especially in file-transfer/file-serving operations.
- Prefer certificate-validating SSL/TLS connections. Do not disable certificate
  verification without explicit documented justification.
"""
