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

This is a **live LISTEN collision** — the script's child and launchd's child are both
genuinely bound (or trying to bind) 25099 at the same time. It is a different failure
than the TIME_WAIT race fixed below under Plain restart, and `SO_REUSEADDR` neither
fixes nor can fix it: a socket option that lets `bind()` succeed over a stale TIME_WAIT
entry still refuses to bind over another process's *active* LISTEN socket on the same
address — which is why the probe binds both the wildcard and `127.0.0.1` shapes (see
Plain restart).
Never use `bambu-mcp-daemon.sh restart`; use `kickstart -k` below instead.

## Procedures

### Status

```bash
./bambu-mcp-daemon.sh status                                    # PID, MCP port, TCP probe
launchctl print gui/$(id -u)/com.isaac.app.forge-bambu-mcp | command grep -E "state =|pid =|runs ="   # launchd view (pid sits past line 10, so no `head`)
```

Success signal: `state = running` and `TCP: OK (port 25099 responding)`. The two PIDs must
match — a mismatch means an orphan from the daemon.sh-vs-launchd collision (the trap
above) is lingering, not the TIME_WAIT race below (that one leaves no orphan; it just
delays the single surviving spawn).

### Plain restart (no env change)

```bash
launchctl kickstart -k gui/$(id -u)/com.isaac.app.forge-bambu-mcp
```

**Port race — fixed and empirically confirmed in this repo's own log.** A `kickstart -k`
restart used to reliably throw several `OSError: designated port 25099 is unavailable`
tracebacks and take ~25–30s to settle: uvicorn's graceful shutdown actively closes client
connections, which leaves the local port in TIME_WAIT; a bare `socket.bind()` probe fails
against that TIME_WAIT entry even though the old process (and its LISTEN socket) is
already gone, so `resolve_port()` raised, the new child crashed, and launchd's
`ThrottleInterval` (5s) retried until one attempt landed outside the TIME_WAIT window.
This was NOT a case of the previous daemon still being alive/mid-shutdown: every one of
these clusters logs `daemon_port: ensure_singleton: cleaning up stale PID/port files from
dead daemon` 5–6s after the SIGTERM and *before* the first `OSError`, which only fires
when the PID file's process is already confirmed dead — `bambu-mcp.log:1911-1969`
(`09:54:15`, 6 failed spawns), `:2106-2164` (`10:07:21`, 6), `:2200-2249` (`10:25:42`, 5),
`:2278-2327` (`10:34:01`, 5, the last failing restart).

`daemon_port.py`'s `_is_port_available()` now sets `SO_REUSEADDR` on the probe socket
before binding, which lets `bind()` succeed over a TIME_WAIT entry. With that option a
bind is refused only by a LISTEN on the *same* address — a wildcard probe sails past a
`127.0.0.1`-specific listener and vice versa (measured 2026-09-15 on a scratch port) —
so the probe binds both shapes (`""` and `DAEMON_HOST`, the address uvicorn actually
binds) and reports available only when both succeed; a foreign LISTEN of either shape
is still caught, TIME_WAIT is not. `daemon_port.py`'s on-disk mtime (`10:37:38`) sits 10s before the very next
restart, and every restart since is clean, first-spawn, zero-`OSError`:
`bambu-mcp.log:2353-2357` (`10:37:48`), `:2367-2371` (`10:38:06`), `:2381-2385`
(`10:46:51`) — three consecutive restarts, all landing on the first spawn, all still
logging the same "cleaning up stale … dead daemon" line 5–6s after SIGTERM (so the old
process dying fast is unchanged; only the TIME_WAIT bind failure is gone). Read `runs =`
from `launchctl print gui/$(id -u)/com.isaac.app.forge-bambu-mcp` before/after a restart
to confirm the spawn counter advances by exactly 1 on your own node — this behavior can
differ by macOS network-stack tuning.

**If you still see repeated `OSError` tracebacks on a `kickstart -k` restart**, check two
things, in order: (1) the previous process may genuinely still be inside `_shutdown()`
(MQTT `session_manager.stop_all()`, camera `mjpeg_server.stop_all()`, `job_monitor.stop_all()`
can each take time) and still holds a live LISTEN socket — SO_REUSEADDR cannot and does
not fix that case; wait longer before retrying, or check the log for whether
`Received SIGTERM` precedes `Finished server process` by longer than usual. (2) a stray
`bambu-mcp-daemon.sh`-launched child left running (see the trap above) — confirmed via
`lsof -nP -iTCP:25099 -sTCP:LISTEN` (verified 2026-09-15: correctly reports the current
listener's PID and matches the daemon's own last "Started server process" line).

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

### Knobs and runtime controls

Log level and raw-payload verbosity are **no longer env-configured at all** — removed
2026-09-17. Every daemon boots with root and the `bpm` logger fixed at `ERROR` and
every printer session's `BambuConfig.verbose=False`; the only controls are the two
live REST routes below, which take effect immediately on the running process and
reset to that fixed boot default on the next restart. There is nothing to add to
`.claude/daemon.json`'s `env` dict for this — it no longer has one.

`BAMBU_MCP_FTPS_TIMEOUT` is unrelated and still a genuine env knob (session start,
`session_manager.py:57`): seconds to wait for the FTPS control connection behind every
SD-card file operation, passed through to `BambuConfig.ftps_connection_timeout`
(`bpm/bambuconfig.py:81`). Must parse as a positive integer; anything else is logged
at WARNING and ignored, leaving bpm's own default. To set it, add the key to
`.claude/daemon.json`'s `env` dict, regenerate, and reload per **Persistent env
change** above.

- Server-wide only — there is **no per-printer** log level.
- `POST /api/set_log_level?level=<L>&bpm_level=<L>` (`api_server.py`, `server.py`'s
  `set_log_level(level_name, bpm_level_name=None)`) sets root and, when `bpm_level`
  is given, the `bpm` logger independently — omit it to leave `bpm` untouched.
- `POST /api/set_bpm_verbose?printer=<name>&verbose=true|false` sets
  `BambuConfig.verbose` directly on that printer's live session object — the ONLY
  thing that enables bpm's raw-payload line (`bambuprinter.py` `_on_message`:
  `if self.config.verbose: logger.debug("_on_message - bambu_msg…")`). Per-printer,
  live, no restart.
- Both routes are read fresh on every call — no caching, no session-start-only
  latch. Raising `bpm_level` alone yields bpm's method-level DEBUG lines but not the
  raw payload dump; that needs `verbose=true` too. With both on, bpm logs every raw
  MQTT payload at DEBUG (`DEBUG    bpm: _on_message - bambu_msg: [{"print":{...}}]`)
  — the log grows fast (181 MB in one evening on 2026-09-15); truncate when done.
- The file handler's level is recomputed on every `set_log_level` call as
  `min(root level, bpm's current effective level)`, so whichever logger currently
  wants more detail is admitted to the file.
- `SIGUSR1`/`SIGUSR2` (`server.py` `_cycle_log_level`/`_cycle_bpm_log_level`) still
  cycle root/bpm DEBUG→INFO→WARNING→DEBUG independently via signal
  (`kill -USR1|USR2 $(cat ~/.bambu-mcp/daemon.pid)`) — a fallback when REST is
  unreachable. No MCP tool wraps any of this — REST/signal only, by design.
- stderr is capped at WARNING regardless of level (stdio-pipe overflow guard).

### Debug-logging SOP

`curl -X POST "http://localhost:<api_port>/api/set_log_level?level=DEBUG&bpm_level=DEBUG"`
then `curl -X POST ".../api/set_bpm_verbose?printer=<name>&verbose=true"`. Confirm within
~15s: `grep 'DEBUG    bpm:' bambu-mcp.log | tail` shows `_on_message` payload lines
(needs at least one connected printer session — check `get_configured_printers()` first).
When done, flip `verbose=false` and set both levels back to `ERROR` (or just restart the
daemon — the boot default is fixed at `ERROR`/`verbose=False` either way), then truncate
the log (`truncate_log` MCP tool with `user_permission=True`, or `truncate -s 0 bambu-mcp.log`).

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
| Repeated `OSError: designated port 25099 is unavailable` tracebacks on a `kickstart -k` restart | Fixed 2026-09-15 (`bambu-mcp.log` shows 4 failing restarts, then 3 clean ones immediately after `daemon_port.py`'s `SO_REUSEADDR` fix landed — see Plain restart above). Confirmed again 21:01 EDT after the probe was widened to both address shapes: runs +1, zero `OSError`, listener up in 1s, old pid gone. If it recurs: either the previous process is still inside `_shutdown()` holding a live LISTEN socket (SO_REUSEADDR can't fix that), or a genuine collider is bound to the port. | Check `Received SIGTERM`→`Finished server process` gap in the log first. Then `lsof -nP -iTCP:25099 -sTCP:LISTEN` to find a collider. Do not run `bambu-mcp-daemon.sh` in parallel — see the trap above. |
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
