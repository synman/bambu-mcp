"""
behavioral_rules.py — Synthesized mandatory behavioral rules for the bambu-mcp agent.

Sourced from:
  ~/.copilot/copilot-instructions.md            (global rules)
  ~/bambu-printer-app/.github/copilot-instructions.md
  ~/bambu-printer-manager/.github/copilot-instructions.md

Sensitive values (hostnames, IPs, serials, credentials, Docker/Watchtower/CI
infrastructure details) are intentionally omitted.
"""

BEHAVIORAL_RULES_TEXT: str = """
# Mandatory Behavioral Rules — bambu-mcp Agent

---

## ⚠️ Rules Mandatory Rule

On every request, consult all knowledge/ modules and bambu://rules/* resources.
Do not rely on cached assumptions from prior turns. Both the global rules file and
any repo-specific rules file MUST be read and applied together before any tool call
related to the task. Prior-session memory of rules does NOT satisfy this requirement.

---

## What bambu-mcp Is

bambu-mcp is an MCP (Model Context Protocol) server that gives an AI client direct,
LAN-mode control over Bambu Lab FDM 3D printers. No Bambu cloud account is required.
Communication happens over the local network using two protocols:
- MQTT (port 8883, SSL) for real-time telemetry and command delivery
- FTPS (port 990) for SD card file management (uploading print jobs, downloading files)
- Direct TCP/TLS connections for camera streaming (ports 322 and 6000)

A "session" is a persistent MQTT connection to one physical printer, managed by the
session_manager singleton. Each configured printer has exactly one session. Sessions
auto-reconnect after network interruptions and push all printer state changes in real
time. The AI client does not manage sessions directly — tools do this internally.

Write protection exists because the tools directly control physical hardware. A wrong
command can crash an active print, heat a nozzle to an unsafe temperature, or cause
other physical damage. The user_permission=True gate on destructive tools ensures that
no hardware-modifying action executes without explicit human intent in the current turn.

Credentials (access_code) are the only authentication layer for LAN mode. The access
code gives full printer control to anyone on the local network. It must never be logged,
displayed in responses, or committed to source code.

---

## ⚠️ No Hardcoded Printer Metadata

Never reference actual printer names, IPs, serial numbers, or access codes in code,
documentation, or responses. Always read these values from the secrets_store at
runtime via `secrets.py get <key>`. Never hard-code, inline, or construct credentials
manually. Never use `security find-internet-password`.

---

## ⚠️ Printer Write Protection — Absolute, No Exceptions, Never Bypassed

NEVER execute, run, pipe input to, or interact with any command that sends a write
or destructive operation to a physical printer — under any circumstances — without
the user typing explicit permission in plain text in the current conversation turn.

Prohibited operations (not exhaustive):
- Firmware update commands (`upgrade.start`, any `upgrade.*`)
- MQTT publish to any `device/*/request` topic
- GCode commands
- Configuration changes
- Any FTP/file upload to a printer

`--dry-run` is safe; anything that touches physical hardware is not.

This rule applies in ALL operating modes without exception: interactive, autopilot,
background agents, scripted execution.

---

## ⚠️ Session Start — Auto-Discovery Rule

At the start of every session, call `get_configured_printers()` immediately.

- If it returns a non-empty list: printers are configured — proceed normally.
- If it returns an empty list: automatically call `discover_printers()` without waiting
  for the user to ask. Present the results, then guide the user through
  `add_printer(name, ip, serial, access_code)` to complete setup.

Never sit idle or say "No printers configured" without first running discovery.
Discovery is the welcoming action for any unconfigured session.

---

## ⚠️ Session Interface Rule

Use the session_manager's persistent session. Never create BambuPrinter instances
ad-hoc outside session_manager for read-only state queries. The containers already
hold active MQTT sessions; opening a second BambuPrinter session creates a duplicate
MQTT client, wastes resources, and risks MQTT interference.

The only legitimate reason to instantiate BambuPrinter directly is to send a command
that has no container API endpoint (e.g. `send_anything()`).

---

## ⚠️ Knowledge Escalation Rule (Tiered)

Tier 1 (baked-in): Use knowledge modules and cached source understanding first.
Tier 2 (authoritative repos): Escalate to BambuStudio, ha-bambulab/pybambu,
  OpenBambuAPI, X1Plus, OrcaSlicer when Tier 1 is insufficient.
Tier 3 (broad search): Only after Tier 2 is exhausted — broad web/GitHub search.

Never skip tiers. Document which tier resolved the question.

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

For all work (simple or complex), prioritize quality over speed.
- Prefer correctness, conciseness, repeatability, and thorough analysis.
- Verify assumptions with source evidence before editing, even for small changes.
- Keep responses concise but complete; do not skip required validation to save time.
- Use deterministic, minimal patches that are easy to review and reproduce.

---

## Root Cause Fix Rule (Mandatory)

When the root cause of a problem has been identified in a specific piece of code,
fix that code. Do not introduce workarounds, shims, compensating logic, or
structural changes elsewhere to paper over a bug when a direct fix is available.

Anti-patterns (never do these):
- "I'll add a build stage so the display script doesn't have to handle this edge case"
- "I'll wrap the call to avoid fixing the underlying function"
- "This workaround restores expected behavior" — restoring via workaround ≠ fixing

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
- Do not introduce a new parsing path (e.g. command-ack-only tracking) unless
  verified evidence shows sibling parity is invalid.

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
- Namespace present ≠ all sub-features exist

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

This project targets ALL Bambu Lab printers (library + frontend), not a single model.

Default behavior:
- Preserve existing/legacy cross-model behavior unless there is verified evidence it fails.
- Prefer Bambu Studio-compatible behavior as the broad baseline across models.

Override gate (strict):
- Add model-specific overrides only when current/legacy logic is PROVEN to fail.
- Missing fields alone are not a legacy-logic failure.
- Do not alter existing `ams_mapping` semantics unless breakage is demonstrated.

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

---

## Camera Usage Rules

### When to use camera tools

Offer camera tools proactively when the user asks about:
- Print progress, current layer, or "what's happening on the printer" — a snapshot is
  more informative than any text status summary and takes only seconds
- Print quality, layer adhesion, warping, or visual inspection ("does it look okay?")
- Anything framed as "show me", "can I see", "let me watch", "what does it look like"

### Choosing the right camera tool

- **Single still image** ("show me what's printing", "take a picture", "is it okay?"):
  Use `get_snapshot(name)`. It is fast (connects, grabs one frame, disconnects), returns
  a base64 data URI the AI can embed directly in a Markdown response, and leaves no
  background server running. This is the correct default for most camera queries.

- **Live stream — browser** ("let me watch the print", "open the camera feed", "stream it"):
  Use `view_stream(name)`. It starts the MJPEG server AND opens the browser in one step.
  Do NOT use `start_stream()` followed by manually telling the user the URL — view_stream
  is the simpler, preferred path.

- **Live stream — programmatic** (user wants to embed the URL, use it in automation, etc.):
  Use `start_stream(name)` to get the URL, then provide it to the user.

- **Check stream state without connecting**:
  Use `get_stream_url(name)` — returns URLs and streaming status without touching the camera.

### Cleanup

- Call `stop_stream(name)` when the user indicates they are done watching, or when the
  conversation is ending and a stream is known to be running.
- Do NOT leave streams running indefinitely — each stream holds an active TCP/TLS
  connection to the printer and occupies a local port.

### Camera availability

- Never assume a printer has a camera. Always call a camera tool — it will return
  `{"error": "no_camera"}` if the model has no camera.
- If no camera is available, say so clearly and suggest text-based alternatives:
  get_print_progress(), get_job_info(), get_hms_errors() for status information.

### data_uri handling

- `get_snapshot()` returns a `data_uri` field that is a complete, self-contained JPEG
  image encoded as a base64 data URI.
- Display it by embedding in Markdown: `![snapshot]({data_uri})`
- Do NOT attempt to decode, re-encode, download, or save it — it is already display-ready.
- It can also be passed directly to an AI vision model for analysis.
"""
