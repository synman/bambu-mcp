# bambu-mcp Operators Guide

Runbook for administering the bambu-mcp daemon: supervision model, start/stop/restart,
log-level management, debug logging, and verification. For architecture and tool
reference, see the repo `README.md` and `CLAUDE.md`.

**Scope:** the streamable-http daemon on a workstation where the repo is registered in
isaac's `.workspace-paths` (which brings launchd supervision with it). All commands are
macOS (`launchctl`); on Linux the generated unit is systemd-user and the same concepts
apply.

## Supervision model — read this before restarting anything

Two managers can launch this daemon, and **launchd wins**:

| Manager | Launch path | Env source |
|---|---|---|
| **launchd** (authoritative) | `~/Library/LaunchAgents/com.isaac.app.forge-bambu-mcp.plist`, `KeepAlive` on non-successful exit, `ThrottleInterval` 5s | The plist's `EnvironmentVariables` dict |
| `bambu-mcp-daemon.sh` (legacy/manual) | `nohup .venv/bin/python3 server.py --transport streamable-http` | The invoking shell |

The plist is **generated** — do not treat it as source. isaac's
`services/gen-daemon-service.py` renders it from this repo's `.claude/daemon.json`
descriptor, and **every `install-hooks.sh` run regenerates it**, discarding hand edits.

**The trap:** `./bambu-mcp-daemon.sh restart` appears to work but silently discards your
environment. Its `stop` kills the process; launchd's KeepAlive immediately relaunches it
with the *plist's* env; the script's own `nohup` child then loses the race for port 25099,
crashes (`OSError: designated port 25099 is unavailable`), and dies — while the script
reports "started (PID N)" by reading the PID file the *launchd* child wrote. Symptom:
repeated port-25099 `OSError` tracebacks in the log around a restart, and env-dependent
behavior (log level) unchanged. (Empirical 2026-09-14.)

## Procedures

### Status

```bash
./bambu-mcp-daemon.sh status                                    # PID, MCP port, TCP probe
launchctl print gui/$(id -u)/com.isaac.app.forge-bambu-mcp | head   # launchd view: state, pid
```

Success signal: `state = running` and `TCP: OK (port 25099 responding)`. The two PIDs must
match — a mismatch means an orphan from the port race is lingering.

### Plain restart (no env change)

```bash
launchctl kickstart -k gui/$(id -u)/com.isaac.app.forge-bambu-mcp
```

Printer MQTT sessions restart automatically; streamable-http MCP clients reconnect
transparently to port 25099 (verified 2026-09-14 — no `mcp-reload` needed for CC
sessions on the http binding; a stdio-bound or stuck client still needs `~/bin/mcp-reload`).

Only restart while printers are idle unless the disruption is acceptable: an active
print is not interrupted (the printer runs autonomously), but monitoring, camera
streams, and telemetry capture drop until sessions re-establish.

### Temporary env change (e.g. enable debug logging)

Edit the plist's `EnvironmentVariables` dict (plain XML — any editor; validate with
`plutil -lint <plist>` if unsure), then reload the job. **A hand-edited plist requires
bootout + bootstrap** — `kickstart -k` restarts the job from the definition launchd
already holds in memory and does NOT re-read the file:

```bash
launchctl bootout gui/$(id -u)/com.isaac.app.forge-bambu-mcp
sleep 5
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.isaac.app.forge-bambu-mcp.plist
```

- `bootstrap` can fail with `Bootstrap failed: 5: Input/output error` immediately after
  `bootout` (teardown race). Wait a few seconds and retry — it then loads cleanly.
- This is a **live-only** change: the next `install-hooks.sh` regenerates the plist from
  `.claude/daemon.json` and reverts it. That auto-revert is a feature for debug toggles.

### Persistent env change

Edit `.claude/daemon.json` (`env` dict) in this repo, then regenerate and reload:

```bash
~/ai/isaac/services/gen-daemon-service.py --repo ~/ai/forge/bambu/bambu-mcp --key forge-bambu-mcp
launchctl kickstart -k gui/$(id -u)/com.isaac.app.forge-bambu-mcp
```

`daemon.json` is the tracked source of the deployed plist — a plist-only edit and a
`daemon.json`-only edit are each half a change (see `coding-principles.md` — the
deployed/tracked pair rule).

### After a code change

The full sequence (force-reinstall BPM, clear log, restart, reconnect) lives in the
bambu project `CLAUDE.md` — MCP Server Restart. Substitute the launchd restart above
for the `daemon.sh restart` step.

## Logging

All logging lands in `bambu-mcp.log` at the repo root (the root-logger file handler and
the plist's `StandardOutPath`/`StandardErrorPath` all point there).

### Knobs (read once at process start — `server.py:51-69`)

| Env var | Default | Effect |
|---|---|---|
| `BAMBU_MCP_LOG_LEVEL` | `WARNING` | Root logger + file handler level (`DEBUG`/`INFO`/`WARNING`) |
| `BAMBU_MCP_BPM_VERBOSE` | unset | When set (`1`), the `bpm` logger follows the root level; when unset, bpm is clamped to WARNING regardless of root level |

- Server-wide only — there is **no per-printer** log level.
- With both set, bpm logs every raw MQTT payload at DEBUG
  (`DEBUG    bpm: _on_message - bambu_msg: [{"print":{...}}]`) — the log grows fast;
  truncate when done.
- `server.py` also has a runtime `set_log_level()` + SIGUSR1 cycle
  (DEBUG→INFO→WARNING), but it does **not** lift the bpm clamp when
  `BAMBU_MCP_BPM_VERBOSE` is unset, and no MCP tool exposes it. Env + reload (above) is
  the reliable route.
- stderr is capped at WARNING regardless of level (stdio-pipe overflow guard).

### Debug-logging SOP

1. Add to the plist env: `BAMBU_MCP_LOG_LEVEL=DEBUG`, `BAMBU_MCP_BPM_VERBOSE=1`.
2. Reload the job (Temporary env change, above).
3. Verify within ~15s: `grep 'DEBUG    bpm:' bambu-mcp.log | tail` shows `_on_message`
   payload lines (printers push telemetry every few seconds). This assumes at least one
   printer session is connected — confirm with `get_configured_printers()` first; with no
   connected printer there is no telemetry to log. No bpm DEBUG lines *with* sessions
   active means the env did not reach the process — check for the trap above.
4. When done: revert the plist env (hand-edit it back, or run `install-hooks.sh` /
   `gen-daemon-service.py` to regenerate it from `daemon.json`), then reload via
   **bootout + bootstrap** — after ANY plist change, regen included, `kickstart -k`
   restarts from launchd's in-memory definition and does not re-read the file (the
   regenerator itself never restarts a running daemon by design). Then truncate the log
   (`truncate_log` MCP tool with `user_permission=True` — server-level, no printer
   argument, same as `dump_log` — or `truncate -s 0 bambu-mcp.log`).

### Reading the log

- MCP: `dump_log` (tail; server-level, no printer arg) — responses may arrive
  gzip+base64; fall back to `GET /api/dump_log?tail_lines=<n>` on the REST port.
- Shell: the file directly. Format: `%(asctime)s %(levelname)-8s %(name)s: message`
  (note: grep `'DEBUG    bpm:'` needs 4 spaces — levelname is padded to 8).

## Ports

Two separate port systems — do not conflate them:

- **MCP** (streamable-http, what CC sessions connect to): fixed at **25099**
  (designated; range 25000–25099, port file `~/.bambu-mcp/daemon.port`, PID file
  `~/.bambu-mcp/daemon.pid`).
- **REST API + MJPEG streams**: dynamic, from a shared `PortPool` starting **49152** —
  there is no fixed REST port; never hardcode one. Discover via `get_server_info()`
  (MCP) or probe `GET http://localhost:49152/api/server_info` upward.

## Failure branches

| Symptom | Cause | Action |
|---|---|---|
| Repeated `OSError: designated port 25099 is unavailable` tracebacks | Port race after a kill — old socket lingering while launchd (ThrottleInterval 5s) retries | Wait; launchd wins within ~30s. Do not start `daemon.sh` in parallel. |
| `Bootstrap failed: 5: Input/output error` | bootout/bootstrap teardown race | `sleep 5`, retry bootstrap |
| Env-dependent behavior unchanged after "restart" | The trap: launchd relaunched with plist env | Use the plist + bootout/bootstrap route |
| Daemon down, launchd job not loaded | bootout without bootstrap | `launchctl bootstrap gui/$(id -u) <plist>` |
| MCP tools failing after restart | Client stuck on dead connection | `~/bin/mcp-reload`, or `/mcp` reconnect in the CC session |
| Sessions show `connected: false` | Printer/MQTT side, not the daemon | Check printer power/LAN, then `get_session_status` |

## Update triggers (keep this guide current)

- `bambu-mcp-daemon.sh`, `daemon_port.py`, or the logging block in `server.py` changes →
  re-verify the procedures and knobs here.
- `.claude/daemon.json` schema or isaac's `gen-daemon-service.py` changes → re-verify
  the supervision model and regen commands.
- Port pool or transport changes → update Ports.
- Canonical KB pointer: `bambu-mcp-daemon-launchd-supervision-admin-guide` (node-kb-mcp)
  points here; keep it aligned if this file moves.
