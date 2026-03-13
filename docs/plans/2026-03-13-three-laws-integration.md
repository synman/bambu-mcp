# Three Laws Integration + Gist — 2026-03-13

## What this is

Asimov's Three Laws of Robotics provide a rigorous, philosophically grounded framework for the exact authorization question at the heart of our protection model. The goal is to:

1. **Integrate the Three Laws as the foundational principle** in the global ruleset — replacing the ad-hoc "hard block vs. soft guard" distinction with a principled hierarchy
2. **Update the mcp rules** to cross-reference the global principle at the relevant guards
3. **Re-scope issue #35** from a code-level blocklist to a consequence-disclosure behavioral rule
4. **Publish a Gist** on synman's GitHub: the Three Laws as a design pattern for AI agents controlling physical hardware
5. **Close #29** (trivial housekeeping)

---

## The Rules Gap — Why This Is Needed

Our system has two protection tiers with no stated principle connecting them:

| Tier | Example | What makes it different |
|------|---------|------------------------|
| Hard block — cannot override | Active print guard | ??? |
| Soft guard — `user_permission=True` + `ask_user` | stop_print, delete_file, send_gcode | ??? |

Without the principle, future agents (and contributors) have no framework for deciding which tier a new dangerous operation belongs in. The drift risk: adding hard blocks where they don't belong (violating human autonomy) or soft guards where they don't belong (allowing injury).

---

## The Three Laws, Applied

**First Law** — A robot may not injure a human being, or through inaction allow a human being to come to harm.

Applied: **Justifies hard, non-overridable blocks.** Mid-print GCode injection can crash the toolhead at speed, shatter the print, cause fire, or physically injure someone near the machine. The active print guard is a First Law gate. It is correct, and it cannot be unlocked by `user_permission=True` — because no human authorization overrides First Law.

**Second Law** — A robot must obey orders given by human beings except where such orders would conflict with the First Law.

Applied: **Once a human authorizes a destructive action with full information, the agent must execute.** M997 (firmware update), M502 (factory reset), M500/M501 (EEPROM) in idle state harm the printer (property), not humans. First Law doesn't apply. A hard code-level block that refuses after `user_permission=True` + informed `ask_user` confirmation says: *"I know you explicitly authorized this, I know it won't hurt anyone, but I'm refusing anyway."* That's a Second Law violation. The correct model: disclose consequences clearly, get confirmation, then execute without further resistance.

**Third Law** — A robot must protect its own existence as long as such protection does not conflict with the First or Second Law.

Applied: **No analogue.** The agent has no existence to protect. More importantly: protecting the *printer* from the user's authorized choices is not the agent's prerogative — the printer belongs to the user. Paternalistic blocks that "protect the printer" from its owner after explicit authorization are Third Law category errors.

---

## The Consent Model (derived from Second Law)

For any destructive-but-authorized operation, the complete safety model is:

1. `user_permission=True` gate — prevents accidental agent-autonomous sends
2. `ask_user` with explicit consequence disclosure — agent must name the command and describe the irreversible consequence in plain language
3. After confirmation — **execute without further resistance**

Hard blocks beyond this model require First Law justification (specific, credible risk of physical harm to humans). Otherwise they violate Second Law.

---

## Work Items

### 1. Global rules: add "Asimov Three Laws — Authorization Model (Mandatory)"

**Location:** After `## ⚠️ PRINTER WRITE PROTECTION` (~line 415)

**Content:**
- Articulate each law's application to AI hardware agents
- Establish the two tiers and their justifications explicitly
- State the consent model derived from Second Law
- Call out the anti-pattern: hard blocks that refuse after authorized consent
- Note: "Protecting the printer from its owner" = Third Law category error

### 2. MCP rules: cross-reference Three Laws at Active-Print Disruptive Write Gate

**Location:** `~/bambu-mcp/.github/copilot-instructions.md` → `Active-Print Disruptive Write Gate` section

**Add:** 2–3 sentence note — this gate is a First Law hard block; mid-print crashes can cause physical harm; `user_permission=True` cannot unlock it because First Law supersedes Second Law.

**Also add:** High-consequence GCode consequence-disclosure rule in or near the Tier 2 routing table — M997/M502/M999/M112/M500/M501 require `ask_user` with explicit consequence description before sending; must execute after confirmation.

### 3. Update issue #35 (synman/bambu-mcp)

Re-scope from "hard code blocklist" to "consequence disclosure behavioral rule." Reference the Three Laws justification. Note resolution path: rules addition (this work), not a code change.

### 4. Close issue #29 (synman/bambu-mcp)

`gh issue close 29` — work committed in 505ea62, never closed.

### 5. Publish Gist (synman's GitHub)

**Title:** "Asimov's Three Laws as a Design Pattern for AI Agents Controlling Physical Hardware"

**Audience:** AI agent developers building tools that interact with physical systems (printers, robots, lab equipment, home automation, industrial controllers)

**Outline:**
- Introduction: the paternalism problem — when AI agents refuse to execute explicitly authorized commands
- The Three Laws as a hierarchy for authorization decisions
- Concrete example: bambu-mcp's print guard vs. proposed M997 blocklist
- The consent model: user_permission + informed ask_user + unconditional execution
- What "First Law hard block" looks like in practice vs. "Second Law consent flow"
- The Third Law error: protecting hardware from its owner
- Closing: why this matters for any agent controlling real-world hardware

### 6. Sync bambu-rules

After global rules edit: copy + commit + push to `~/GitHub/bambu-rules`.

---

## File Change Table

| File | Change | Risk |
|------|--------|------|
| `~/.copilot/copilot-instructions.md` | Add new section "Asimov Three Laws — Authorization Model" (~60 lines) | Low — additive only |
| `~/bambu-mcp/.github/copilot-instructions.md` | Add cross-reference + consequence-disclosure rule (~15 lines) | Low — additive |
| `~/GitHub/bambu-rules/global/copilot-instructions.md` | Sync (copy) | Low |
| `~/GitHub/bambu-rules/projects/bambu-mcp/copilot-instructions.md` | Sync (copy) | Low |
| GitHub issue #35 | Edit body text | Low |
| GitHub issue #29 | Close | Low |
| GitHub Gist (new) | Create public gist | Low |

---

## Lateral Impact Assessment (Rules)

Changes touch the global rules file. Sections reviewed for lateral impact:

| Section | Impact | Action |
|---------|--------|--------|
| `⚠️ PRINTER WRITE PROTECTION` | The new section provides the philosophical foundation for it. No contradiction — the Printer Write Protection rule governs *agent-initiated* writes without user permission; the Three Laws section governs the authorization model once a human grants permission. Complementary. | None — no change needed |
| `GCode Calibration Motion Safety` | Already correctly restricts commands to a known-safe set. The Three Laws rule adds: when a *user* requests a high-consequence command, the consent model applies. No conflict. | None |
| `Git Commit Policy` — hard blocks on commit | This is a workflow rule, not a hardware-safety rule. The Asimov model doesn't apply to git workflow rules — those are agent design constraints, not human-robot safety constraints. Must explicitly exclude git workflow rules from Asimov scope. | Add exclusion note in new section |
| `BPM Write Scope Lock` | Same — workflow constraint, not Asimov-scoped. | Same exclusion |
| `Scientific Method` | No conflict. Asimov section is about authorization model; scientific method is about epistemology. | None |

---

## Open Questions

None — scope is clear. No user decisions required before implementation.


### The gap

Our rules describe two protection layers but never articulate **why** they differ:

| Layer | Example | Justification |
|-------|---------|---------------|
| Hard block (cannot override) | Active print guard | Risk of physical harm — First Law |
| Soft guard (user_permission + ask_user) | stop_print, delete_file | Requires informed authorization — Second Law |

**Missing rule:** No rule states the *principle* separating these two categories. Without it, a well-intentioned blocklist for idle-state M997/M502 violates the Second Law — the human explicitly authorized the action.

### Asimov applied

1. **First Law** (don't injure humans) → justifies **hard, non-overridable blocks**. Mid-print GCode can crash toolheads, cause fires. Active print guard = correct First Law gate.
2. **Second Law** (obey human orders, unless First Law) → once a human authorizes destructive action via `user_permission=True` + informed `ask_user`, **the agent must execute**. A code block that refuses after authorization violates Second Law — M997/M502 in idle state harms the printer (property), not humans. First Law doesn't override.
3. **Third Law** (self-preservation) → no analogue; protecting the printer from the user's choices is not the agent's prerogative.

### #35 re-scope: blocklist → consequence disclosure

**Wrong model (was proposed):** hard code-level block for M997/M502/M999/M112 in idle state.
**Correct model:** behavioral rule requiring **explicit consequence disclosure** in the `ask_user` call.

When asked to send high-consequence GCode (M997, M502, M500/M501, M112, M999), the agent MUST:
1. Name the command explicitly
2. Describe the consequence plainly ("this erases factory defaults — cannot be undone without Bambu support")
3. Ask via `ask_user`
4. **Once confirmed — execute without further resistance**

### Rule to add (global rules)

**"Second Law Authorization Model"** → `~/.copilot/copilot-instructions.md`:

> Hard code-level blocks that prevent execution of explicitly authorized commands are prohibited unless the action creates risk of physical harm to humans (First Law). The `user_permission=True` + informed `ask_user` flow is the correct model for all destructive operations — once a human authorizes with full information, execute.

---

## Open issue backlog (ordered by effort/impact)

| # | Title | Effort | Notes |
|---|-------|--------|-------|
| #29 | Close stale issue (work done, forgot to close) | Trivial | `gh issue close 29` |
| #35 | GCode consequence disclosure (re-scoped from blocklist) | Small | Rules + docstring + knowledge — no code change |
| #28 | Stream HUD: dual nozzle temps (H2D) | Small | mjpeg_server.py HUD template |
| #8 | Knowledge gap: agent doesn't know about own failure detection | Medium | Knowledge module + behavioral rules update |
| #27 | Perf: 3-call reduction + plate_viewer section anchors | Medium | Tool-level caching + HTML template |
| #30 | Cloud auth: X.509 cert signing | Large | New dependency, complex auth flow |
| #31 | Cloud API integration | Large | New tools + Bambu cloud endpoints |
| #32 | MakerWorld pipeline | Large | Multi-step orchestration |
| #33 | Local slicer integration | Large | External process / CLI |

---

## Recommended next: #35 → #28 → #8 → #27

**#35 (GCode safety)** is the highest-value/lowest-effort item:
- `send_gcode` currently passes raw GCode to the printer with **zero validation** — a confused agent can send `M997` (firmware update) or `M502` (factory reset)
- Fix: add a `validate_gcode()` guard before forwarding:
  - **Blocklist** 6 prefixes: `M112` (emergency stop), `M502` (factory reset), `M500`/`M501` (EEPROM), `M997` (firmware update), `M999` (restart)
  - **Temperature caps**: nozzle max 300°C (`M104`/`M109`), bed max 120°C (`M140`)
- ~30 lines, pure logic, no new deps — reference: `schwarztim/bambu-mcp/src/safety.ts`
- The `user_permission=True` guard blocks *accidental* sends but doesn't validate *content*

**#28 (dual nozzle HUD)** is the next quickest win — targeted mjpeg_server.py template change.

**#8 (knowledge gap)** is a documentation/rules fix, no code.

**#27 (perf)** requires reading the current tool call trace to understand exactly what to cache.



### Current state
`secrets_store.py` already exists and `auth.py` uses it for all printer credentials. The store is Fernet-encrypted at `~/.bambu-mcp/secrets.enc`. **The only real gap:** `_DEFAULT_PASSWORD = "changeit"` — no `settings.toml` exists and no env var is set, so live credentials are currently encrypted with a known-weak literal string.

### Proposed approach: OS keychain via `keyring` library

`keyring` (Python) is a cross-platform abstraction over the host system's native credential store:
| Platform | Backend |
|----------|---------|
| macOS | Keychain Services (session-scoped, sealed to user login) |
| Linux | libsecret / GNOME Keyring / KWallet |
| Windows | Windows Credential Manager |
| Headless / container | Fallback (see below) |

The master key stored in the keychain is a **raw Fernet key** (`Fernet.generate_key()` = 32 bytes CSPRNG, URL-safe base64). This eliminates the PBKDF2 layer for the keychain path — the OS keychain IS the sealing mechanism; no need to derive from a password. The key is bound to the user session by the OS.

### Priority order (new `_get_fernet()` function)

```
1. BAMBU_MCP_FERNET_KEY env var → direct Fernet key (raw base64, containerized envs)
2. BAMBU_MCP_SECRETS_PASSWORD env var → PBKDF2 derive (legacy/scripted compat)
3. OS keychain (keyring) → retrieve stored Fernet key; generate + store on first use
4. File fallback ~/.bambu-mcp/master.key (mode 0o400) → if no keychain backend available
```

**Migration from "changeit":** After getting the new Fernet key, attempt to decrypt. If `InvalidToken`, try the PBKDF2 Fernet key derived from `"changeit"`. If that succeeds → re-encrypt the store with the new key and discard `"changeit"` path permanently.

### Files to change
| File | Change |
|------|--------|
| `pyproject.toml` | Add `"keyring"` to `dependencies` |
| `secrets_store.py` | Replace `_get_password()` + `_make_fernet()` with `_get_fernet()` using keyring. Keep PBKDF2 helper for env var compat. Add migration logic. |

### Why raw Fernet key beats PBKDF2 passphrase

| | PBKDF2 passphrase path | Raw Fernet key |
|--|----------------------|----------------|
| Entropy | Passphrase-quality (human-chosen) | 256 bits CSPRNG — always |
| Salt | Fixed `b"bambu-mcp"` → same password = same key on all installs | N/A |
| Rainbow table risk | Feasible for this specific software | None |
| Attack surface | Two code paths | One |

**Decision: drop `BAMBU_MCP_SECRETS_PASSWORD` entirely.** Nobody was relying on it (defaulted to `"changeit"`). The PBKDF2 layer is removed. Single key type everywhere.

### Final priority order (new `_get_fernet()` function)

```
1. BAMBU_MCP_FERNET_KEY env var → raw Fernet key (container/CI injection)
2. OS keychain (keyring) → retrieve or generate Fernet key (session-sealed by OS)
3. File fallback ~/.bambu-mcp/master.key (mode 0o400) → headless / no keychain backend
```

PBKDF2 is retained only as a migration detector: try new Fernet key first; if `InvalidToken`, try PBKDF2("changeit") once → if that decrypts → re-encrypt with new key and discard.

### Files to change
| File | Change |
|------|--------|
| `pyproject.toml` | Add `"keyring"` to `dependencies` |
| `secrets_store.py` | Remove `_get_password()` + `_DEFAULT_PASSWORD`. Replace `_make_fernet()` with `_get_fernet()`. Add keyring get/set logic, file fallback, migration from "changeit". Remove `password=` parameter from all public functions (no longer needed). |



---

## schwarztim/bambu-mcp — Deep Comparative Analysis + Issue Actions

### Problem
The README for schwarztim/bambu-mcp understates what Tim actually built. Source tree reveals:
`makerworld.ts`, `slicer.ts`, `vision-provider.ts`, `print-monitor.ts`, `resources.ts`, `write-protection.ts`
— capabilities not mentioned in the README at all. Need a full source-level read before any issue work.

### Approach
**Phase 1** (parallel): Deep source read of all non-trivial `.ts` files via fleet agents.  
**Phase 2**: Synthesize full feature parity report (compare to synman/bambu-mcp tool-by-tool).  
**Phase 3**: Act on findings — post comments on #6/#4/#16/#7, create new issues for capability gaps.

### Phase 1 — Source files to read in parallel

| File | What to extract |
|------|----------------|
| `src/makerworld.ts` + `src/tools/makerworld-tools.ts` | MakerWorld API: search/download models, auth, tool list |
| `src/tools/slicer.ts` | Slicer integration: which engine (Bambu/Cura), slice params, output format |
| `src/vision-provider.ts` | Vision: what camera analysis capability exists, model used, what it detects |
| `src/print-monitor.ts` + `src/tools/monitor.ts` | Monitoring: what metrics, poll interval, alert hooks |
| `src/resources.ts` | MCP resources exposed: topic names, data shapes |
| `src/safety.ts` + `src/write-protection.ts` | Safety model: blocked commands, rate limits, write guards |
| `src/secrets.ts` + `src/tool-context.ts` | Auth: X.509 flow, browser login token, how credentials are stored/retrieved |
| `src/tools/cloud-api.ts` | Cloud API tools: exact endpoints, auth header, what data is returned |
| `src/tools/camera.ts` + `src/tools/status.ts` | Camera & status: what is actually implemented vs README |
| `api/openapi.yaml` | REST API: all routes, parameters, response shapes |
| `docs/cloud-api-reference.md` + `docs/mqtt-protocol.md` | Tim's protocol knowledge: anything not in synman's knowledge modules? |

### Phase 2 — Parity Report Output
- Full tool-by-tool table: Tim's 25+ tools vs synman's 60+ tools
- Architecture comparison (TypeScript/Node vs Python/FastMCP)
- Capability gaps: what Tim has that synman doesn't (with implementation notes)
- Capability leads: what synman has that Tim doesn't
- Save report to `/tmp/tim-parity-report.html` and open it

### Phase 3 — Issue Actions

**Comments to post on existing issues:**
| Issue | Content |
|-------|---------|
| #6 (Klipper/OctoPrint) | `schwarztim/OctoPrint-BambuBoard` (Python, updated Feb 2026) + `claw3d` + MakerWorld integration findings |
| #4 (Ghost OS, bambu-rules) | Tim's X.509 signing approach = no-Developer-Mode MQTT; complements Ghost OS recipes |
| #16 (push alerts) | Tim has zero alert infrastructure — confirm differentiation. If `print-monitor.ts` reveals hooks, note them |
| #7 (open_charts) | Tim has no chart/monitoring-history equivalent — confirm uncontested |

**New issues to create (based on confirmed gaps from Phase 2):**
| Candidate | Repo | Condition |
|-----------|------|-----------|
| Cloud API integration (list printers, user profile, cloud status) | bambu-mcp | If Tim's impl is clean enough to adapt |
| X.509 cert signing — no-Developer-Mode operation | bambu-mcp | Almost certainly worth an issue |
| MakerWorld integration (search models, download, queue for print) | bambu-mcp | Only if Tim's impl reveals a usable pattern |
| Slicer integration (slice before print, profile management) | bambu-mcp | Only if Tim's slicer.ts shows real capability |
| Vision-based print analysis notes | bambu-mcp | Compare to synman's analyze_active_job |

---

## 🟢 Print COMPLETE — 2 independent tasks ready, fully parallelizable

| Task | Agent | Files |
|------|-------|-------|
| Phase 5 post-print analysis | Background A | `/tmp/phase5_postprint.py` → GitHub issue #10 |
| Issue #29 fix | Background B | `~/bambu-mcp/camera/mjpeg_server.py` → commit + push |
| mcp-reload | Main (after B) | Wait for agent B to confirm pushed, then reload |

### Agent A — Phase 5
- Fix monitor log path in script: `~/thermal_captures/plate9_monitor.log` doesn't exist; actual path is `~/thermal_captures/H2D_H2S_main_riser_2025-9-19_20260313_143122/monitor.log`
- Run: `cd ~/bambu-mcp && .venv/bin/python /tmp/phase5_postprint.py`
- Posts heatmap summary table to issue #10

### Agent B — Issue #29
1. Line ~190: `onclick="hpAnomalyToggle(this)"` → `onclick="hpWideToggle(this)"`
2. Remove lines ~178–179 (`Layer` and `Progress` metric rows) + JS update calls at ~580–581
3. Line ~478: rename + replace body with viewport-clamping version:
   ```javascript
   function hpWideToggle(hdr){
     var panel=document.getElementById('health-panel');
     var anomaly=document.getElementById('hp-sec-anomaly');
     var chev=hdr.querySelector('.hdr-chev');
     if(panel.classList.contains('hp-wide')){
       panel.classList.remove('hp-wide');chev.classList.remove('open');
       anomaly.style.maxHeight='';
     } else {
       panel.classList.add('hp-wide');chev.classList.add('open');
       requestAnimationFrame(function(){
         var hdrBottom=hdr.getBoundingClientRect().bottom;
         var available=window.innerHeight-hdrBottom-14;
         anomaly.style.maxHeight=Math.max(80,available)+'px';
       });
     }
   }
   ```
4. Commit: `stream: rename hpAnomalyToggle→hpWideToggle, clamp viewport, drop redundant metrics`
5. Push

### After both agents complete
- `mcp-reload` (main turn)


| Item | Status |
|------|--------|
| Phase 3 thermal monitor | 🔄 Cool-down mode (PID 95035), 112 captures total |
| Phase 4 auto-pause | ✅ Done — captured layer10, 25pct, 50pct + baselines (8 files in /tmp) |
| Phase 5 post-print analysis | 🔴 **Ready to run** — `cd ~/bambu-mcp && .venv/bin/python /tmp/phase5_postprint.py` |
| Watchdog | ⚠️ False-alarm restarted phase4 after clean exit (bad stdin bug); phase4 restart failed benignly |
| bambu-mcp stream changes | Staged, not committed (Score section removed, issue #29 fix pending) |

## Issue #29 — Failure Drivers viewport clamp + rename hpAnomalyToggle

### Naming verdict
`hpAnomalyToggle` is the wrong name:
- It toggles `hp-wide` on `#health-panel` — panel **width** expansion, not anomaly state
- "Anomaly" already means AI detection system in this codebase (spaghetti, air printing)
- `#hp-sec-anomaly` itself has a misleading ID — it contains the *radar chart*, not anomaly detection images
- Triggered from "Failure Drivers" header but the effect is panel-level
- **Rename: `hpAnomalyToggle` → `hpWideToggle`**

### Change set (both in `camera/mjpeg_server.py`)
1. Line ~190: `onclick="hpAnomalyToggle(this)"` → `onclick="hpWideToggle(this)"`
2. Line ~478: `function hpAnomalyToggle(hdr)` → `function hpWideToggle(hdr)` + add height clamping:
   ```javascript
   function hpWideToggle(hdr) {
     var panel   = document.getElementById('health-panel');
     var anomaly = document.getElementById('hp-sec-anomaly');
     var chev    = hdr.querySelector('.hdr-chev');
     if (panel.classList.contains('hp-wide')) {
       panel.classList.remove('hp-wide');
       chev.classList.remove('open');
       anomaly.style.maxHeight = '';
     } else {
       panel.classList.add('hp-wide');
       chev.classList.add('open');
       requestAnimationFrame(function() {
         var hdrBottom = hdr.getBoundingClientRect().bottom;
         var available = window.innerHeight - hdrBottom - 14;
         anomaly.style.maxHeight = Math.max(80, available) + 'px';
       });
     }
   }
   ```
3. Commit: `stream: rename hpAnomalyToggle→hpWideToggle, clamp Failure Drivers to viewport`
4. Push + mcp-reload

## Status
All calibration work is **done** (TC settle constants baked, nozzle compare fixed, coord transform fixed, idle timeout measured). The only remaining todos are POC thermal monitoring, all blocked on starting the plate 9 print.

**⚠️ Bed has been at 90°C for ~9 hours** (target still set). Chamber is at 56°C. Nozzles at 63/61°C (targets off).

### POC Phase Status
| Phase | Status | Blocker |
|-------|--------|---------|
| Phase 2: Baseline capture | ✅ done | — |
| Phase 3: thermal_snapshot_monitor (PID 95035) | 🔄 running, waiting for print | Start print |
| Phase 4: auto-pause at layer 10/25%/50% (PID 97199) | 🔄 running, waiting for print | Start print |
| Phase 5: post-print analysis + GitHub comment | ⏳ pending | Print completion |

## What's Next

### Option A: Start the plate 9 print now (recommended)
- Bed is already at temp — good adhesion start
- Both monitors activate automatically
- Phase 5 runs manually after print completes
- After POC: turn off bed, post results to issue #10

### Option B: Turn off bed, defer print
- Kill both monitors (PIDs 95035, 97199)
- `set_bed_temp(0)`
- Restart everything when ready to print

---

# Original Parallel Execution Plan (reference)

## Execution Strategy
**Minimize compaction**: push all pure file-editing work to background agents. Keep main thread for printer-interactive tasks only.  
**Maximum parallelism**: background agents for file edits run while printer-interactive tasks are being set up.

---

## Wave A — Now (printer IDLE, no print needed)

### A1: `coord-transform-update` → background general-purpose agent
All context known; zero printer interaction. Launch immediately.

**What the agent must do:**
1. Read `~/bambu-mcp/camera/coord_transform.py`
2. Fix module docstring line 11: `ORIGIN = shell NL = (1480, 1256)` → `ORIGIN = shell NL = (406, 517)`
3. Fix module docstring line 12: `N=? points` → `N=5 inliers`
4. Fix PLATE_BOUNDARY inline comments (currently show stale old calibration values):
   - `# (695,  616)  far-left`   → `# (1830, 2226)  far-left (off-screen — extrapolated)`
   - `# (860,  205)  far-right`  → `# (1561, -2178) far-right (off-screen — extrapolated)`
   - `# (1187, 299)  near-right` → `# (505,  522)   near-right`
   - `# (1480, 1256) near-left`  → `# (406,  517)   near-left ← ORIGIN`
5. Commit: `coord_transform: fix stale PLATE_BOUNDARY comments and ORIGIN docstring`
6. Push to origin

**Context to inline in agent prompt:**
- Current SHELL dict: `FL=(1830,2226), NL=(406,517), NR=(505,522), FR=(1561,-2178)`
- n_inliers=5, reproj=8.62px, calibration date 2026-03-13
- FL/FR are off-screen extrapolations (world Y=315 beyond camera view) — document this in comments
- BPM Write Scope Lock does NOT apply to bambu-mcp; full git lifecycle authorized

### A2: `nl-hotspot-offset-recalib` → main thread, after A1 (or in parallel if A1 backgrounded)
Requires printer interaction. Method:
1. `set_nozzle_temp(150)` on T0, wait for nozzle at temp
2. Move to known world coordinate (e.g. bed center X=175, Y=155)
3. Capture snapshot via `get_snapshot(resolution="native")`
4. Detect T0 hotspot centroid in image
5. Compute offset = centroid_px - project(world_xy through H)
6. Repeat for T1
7. Update `NL_FROM_HOTSPOT_OFFSET` in `h2d_heatmap.py`

---

## Wave B — Requires active plate 9 print

### B1: `poc-phase2-baseline` + `poc-phase3-monitor` → start simultaneously at print launch
- Background agent immediately runs `h2d_heatmap.py` (baseline capture)
- Main thread starts `thermal_snapshot_monitor.py` as detached background process

### B2: `poc-phase4-pauses` → during print (layer 10, 25%, 50%)
- Interactive: pause via MCP, wait for toolhead park, background agent captures + analyzes

### B3: `poc-phase5-postprint` → after print completes
- Background agent: analyze all metrics, generate summary, post to issue #10

---

## Parallelism Map

```
t=0:  [background] coord-transform-update agent LAUNCHED
      [main]       nl-hotspot-offset-recalib setup/execution begins
t=?:  [main]       coord-transform agent returns → verify + mark done
t=?+: Print plate 9 starts:
        [background] phase2-baseline agent
        [main]       phase3-monitor process (detached nohup)
        [main]       phase4-pauses (interactive, during print)
t=end: [background] phase5-postprint agent
```

---

# Re-run Idle Nozzle Timeout Calibration — 3 Clean Trials (COMPLETE)

## Status

Prior run (2026-03-13) had Trial 1 contaminated (timer was ~175s elapsed from killed prior run).
Only Trial 2 (300.41s) was a clean cold-start measurement. Constants have been baked at 300s
but based on a single clean data point. This run produces 3 fully clean trials.

All fixes are committed (`54c18ef`):
- `get_nozzle_target()` correct field: `extruders[N].temp_target` + fallback
- `MAX_WAIT` = 3600s
- `_get_nozzle_targets()` in corner_calibration.py fixed same way
- Constants already baked at 300s `[VERIFIED: empirical 2026-03-13]`

## Run configuration

```
cd ~/bambu-mcp && python3 calibration/calibrate_idle_nozzle_timeout.py --trials 3
```

- `--trials 3`: 3 drops, each ~300s + 2-3 min heat-up = **~25 min total**
- Each trial starts with `set_nozzle_temp(150)` → confirm in telemetry → watch for reset
- After each reset: script re-asserts 150°C → starts next trial
- All 3 measurements will be clean cold-start (T0 heats from near-38°C each time)
- User must type `y` at the auth prompt

## Prerequisites

- Printer must be `gcode_state=IDLE` (no print running)
- T0 should be cool (< 50°C) before starting — if warm from prior bake, wait a few min
- bambu-mcp server running on localhost

## After the run

### 1. Read results from `/tmp/idle_nozzle_timeout_calibration.json`

Expected: 3 samples all near 300s. Mean should be 295-305s.

### 2. Decide whether to update the baked constant

| Mean result | Action |
|-------------|--------|
| 295–305s | Keep 300 — within measurement noise |
| <295s | Update to `floor(mean) - 5` conservative value |
| >305s | Update to 300 (already conservative) |

### 3. Update constants + knowledge if constant changes

Files: `corner_calibration.py`, `nozzle_compare.py`, `behavioral_rules_print_state.py`
Update: value + `[VERIFIED: empirical 2026-03-13, 3 trials, mean=XXX.Xs]`

### 4. Commit + push

If constant unchanged: commit script-only (docstring update with 3-trial confirmation).
If constant changed: commit all 4 files.

## What stays blocked

- `coord-transform-update` — 9-point DLT calibration (needs active print)
- `nl-hotspot-offset-recalib` — nozzle hotspot offset (needs active print)
- `poc-phase*` — baseline + thermal monitor (needs active print)
