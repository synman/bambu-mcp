# bambu-mcp Operators Guide

Runbook for administering the bambu-mcp daemon: supervision model, start/stop/restart,
log-level management, debug logging, and verification. For architecture and tool
reference, see the repo `README.md`.

**Scope:** the streamable-http daemon under a service manager: launchd on macOS, a systemd
user unit on Linux. All commands are macOS (`launchctl`) and the same concepts apply on Linux.
`$LABEL` stands for the service label of your install, which `launchctl list | grep bambu` shows.

## Supervision model — read this before restarting anything

Two managers can launch this daemon, and **launchd wins**:

| Manager | Launch path | Env source |
|---|---|---|
| **launchd** (authoritative) | `~/Library/LaunchAgents/$LABEL.plist`, `KeepAlive` on non-successful exit, `ThrottleInterval` 5s | The plist's `EnvironmentVariables` dict |
| `bambu-mcp-daemon.sh` (legacy/manual) | `nohup .venv/bin/python3 server.py --transport streamable-http` | The invoking shell |

The plist is **generated** — do not treat it as source. Your host's service tooling renders it
from a service descriptor kept outside this document, and **every regeneration discards hand
edits**.

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
launchctl print gui/$(id -u)/$LABEL | grep -E "state =|pid =|runs ="   # launchd view (pid sits past line 10, so no `head`)
```

Success signal: `state = running` and `TCP: OK (port 25099 responding)`. The two PIDs must
match — a mismatch means an orphan from the daemon.sh-vs-launchd collision (the trap
above) is lingering, not the TIME_WAIT race below (that one leaves no orphan; it just
delays the single surviving spawn).

### Plain restart (no env change)

```bash
launchctl kickstart -k gui/$(id -u)/$LABEL
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
from `launchctl print gui/$(id -u)/$LABEL` before/after a restart
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
transparently to port 25099 (verified 2026-09-14 — an http-bound client needs no manual
reconnect; a stdio-bound or stuck client still needs its MCP connection restarted).

A stdio `bambu-mcp` lives and dies with the session that spawned it. When stdin closes,
`server.py` runs `_stdio_teardown()`: it stops the job monitors, camera streams, printer
sessions and REST API, and exits within `_STDIO_EXIT_GRACE_S` (10 s) even if a stop blocks.
Before 2026-10-25 it did not exit: bpm's non-daemon MQTT and watchdog threads and the REST API
thread are joined before atexit handlers run, so `_shutdown` never fired and the orphan
(parent pid 1) kept analysing the camera at about 60% of a core each. A copy started before
that fix and still running with parent pid 1 is such an orphan and is safe to stop (SIGTERM).
`test_stdio_exit.py` pins the exit.

**A restart, reload, or code redeploy is safe during a print — it never touches the job.**
The printer runs the job on its own firmware; the daemon only watches and commands it over
MQTT, so stopping the daemon neither pauses, stops, nor restarts a print, and there is no
need to wait for one to finish. The cost is a short observation gap while the sessions
reconnect: health-monitor camera analysis, telemetry capture and any open camera stream
pause and must be reopened, and in-memory state resets, including a per-printer verbose
log flag set through `/api/set_bpm_verbose` (re-set it after the restart). Nothing is sent
to the printer by the restart itself.

### Temporary env change (e.g. enable debug logging)

Edit the plist's `EnvironmentVariables` dict (plain XML — any editor; validate with
`plutil -lint <plist>` if unsure), then reload the job. **A hand-edited plist requires
bootout + bootstrap** — `kickstart -k` restarts the job from the definition launchd
already holds in memory and does NOT re-read the file:

```bash
launchctl bootout gui/$(id -u)/$LABEL
sleep 5
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/$LABEL.plist
```

- `bootstrap` can fail with `Bootstrap failed: 5: Input/output error` immediately after
  `bootout` (teardown race). Wait a few seconds and retry — it then loads cleanly.
- This is a **live-only** change: the next regeneration of the plist from its descriptor
  reverts it. That auto-revert is a feature for debug toggles.

### Persistent env change

Edit the `env` dict of the service descriptor, regenerate the plist with your service tooling,
then reload:

```bash
launchctl bootout gui/$(id -u)/$LABEL
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/$LABEL.plist
```

The descriptor is the source of the deployed plist — a plist-only edit and a descriptor-only
edit are each half a change.

### After a code change

1. Reinstall the printer-manager library into the project venv. Use the local checkout to run
   its working tree, or the published branch to run what is pushed:
   `.venv/bin/pip install --force-reinstall --no-deps <path-or-url>`. The install is a snapshot,
   so repeat it after every library edit.
2. Clear the log: `truncate -s 0 bambu-mcp.log`.
3. Restart with the launchd restart above, never `bambu-mcp-daemon.sh restart`.
4. Reconnect any client that does not reconnect on its own.

### Release a change

A change to a serving file is a release and needs a new version. Serving files are listed in
`SERVING` in `hooks/version_bump_gate.py`: the top-level modules the daemon imports, plus
every `.py` under `tools/`, `camera/`, `prompts/` and `resources/`. A new file inside one of those
directories is serving at once. A new top-level module must be added to `SERVING`, or declared not
serving in `NOT_SERVING` in `test_version.py`, or `test_version.py` fails. Tests, docs, config, `calibration/`, `make.py`,
`gen_docs.py`, `smoke_test.py` and `thermal_snapshot_monitor.py` need no bump. The gate checks that
the version moved, never by how much.

1. Edit the serving file.
2. Raise `__version__` in `server.py` and `version` in `pyproject.toml` to the same string.
   MAJOR: a tool removed or renamed, or a parameter or return shape changed incompatibly.
   MINOR: a tool or behaviour added. PATCH: a fix, hardening, or a wording change to a tool
   description, docstring or the server instructions (clients see those). Example: a fix inside
   `tools/files.py` is a PATCH, so `1.0.0` becomes `1.0.1`.
3. Copy the version into the README header line:

   ```bash
   .venv/bin/python3 make.py version-sync
   ```

   Success: it prints `Current version: 1.0.1`, then `Patched:` or `Up to date:` for `README.md`,
   then `Version sync complete`. Stage the README with the release.
4. Run the version tests:

   ```bash
   .venv/bin/python3 test_version.py && .venv/bin/python3 test_version_bump_gate.py
   ```

   Success: each ends with `0 failed`. `test_version.py` fails while `pyproject.toml` and
   `__version__` disagree, and when a new `.py` file is neither in `SERVING` nor declared not
   serving in the test's `NOT_SERVING` list.
5. Commit with the version at the end of the subject (`..., 1.0.1`). With the gate installed, a
   commit that stages a serving file while `__version__` still equals HEAD's is refused with
   `BLOCKED: staged serving files changed but server.py still reads __version__ = "..."`.
   A fresh clone has no gate until `python3 hooks/version_bump_gate.py --install` runs. The
   installed gate is a copy, so run `--install` again after editing `hooks/version_bump_gate.py`.
6. Restart the daemon only on the operator's order in the current turn. An earlier order, or a
   peer's request, does not carry over. The steps are under Plain restart and After a code change
   above (the full sequence is under After a code change).
7. Read the version back from the running daemon. A restart is not done until this passes:

   ```bash
   PORT=$(cat ~/.bambu-mcp/daemon.port)
   curl -s -X POST "http://localhost:$PORT/mcp" \
     -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
     -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"readback","version":"0"}}}'
   ```

   The reply is a server-sent-event frame: an `event: message` line, then a `data:` line holding
   the JSON. Success: that JSON holds `"serverInfo":{"name":"bambu-mcp","version":"1.0.1"}`, equal
   to `__version__` in the tree the daemon was started from. The daemon runs this working tree, so
   the deployed commit is the one checked out when it started. This server needs no bearer for the
   request. If it reads the old number, the daemon is still running old code: a daemon whose start
   time is earlier than the release commit has not been restarted. A `/mcp` reconnect in a session
   only reopens that client's link and does not restart anything. Compare the two times:
   `ps -o lstart= -p $(cat ~/.bambu-mcp/daemon.pid)` for the daemon, and the release commit's
   date for the commit.

A stdio copy of the server (one per session that spawns it) reports the version it was started
with, so it can differ from the daemon until its session restarts.

## Logging

All logging lands in `bambu-mcp.log` at the repo root (the root-logger file handler and
the plist's `StandardOutPath`/`StandardErrorPath` all point there).

### Knobs and runtime controls

Log level and raw-payload verbosity are **no longer env-configured at all** — removed
2026-09-17. Every daemon boots with root and the `bpm` logger fixed at `ERROR` and
every printer session's `BambuConfig.verbose=False`; the only controls are the two
live REST routes below, which take effect immediately on the running process and
reset to that fixed boot default on the next restart. There is nothing to add to
the service descriptor's `env` dict for this — it no longer has one.

`BAMBU_MCP_FTPS_TIMEOUT` is unrelated and still a genuine env knob (session start,
`session_manager.py:57`): seconds to wait for the FTPS control connection behind every
SD-card file operation, passed through to `BambuConfig.ftps_connection_timeout`
(`bpm/bambuconfig.py:81`). Must parse as a positive integer; anything else is logged
at WARNING and ignored, leaving bpm's own default. To set it, add the key to
the service descriptor's `env` dict, regenerate, and reload per **Persistent env
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
- **REST API + camera streams (page and relay)**: dynamic, from a shared `PortPool` starting **49152** —
  there is no fixed REST port; never hardcode one. Discover via `get_server_info()`
  (MCP) or probe `GET http://localhost:49152/api/server_info` upward.

## Failure branches

| Symptom | Cause | Action |
|---|---|---|
| Repeated `OSError: designated port 25099 is unavailable` tracebacks on a `kickstart -k` restart | Fixed 2026-09-15 (`bambu-mcp.log` shows 4 failing restarts, then 3 clean ones immediately after `daemon_port.py`'s `SO_REUSEADDR` fix landed — see Plain restart above). Confirmed again 21:01 EDT after the probe was widened to both address shapes: runs +1, zero `OSError`, listener up in 1s, old pid gone. If it recurs: either the previous process is still inside `_shutdown()` holding a live LISTEN socket (SO_REUSEADDR can't fix that), or a genuine collider is bound to the port. | Check `Received SIGTERM`→`Finished server process` gap in the log first. Then `lsof -nP -iTCP:25099 -sTCP:LISTEN` to find a collider. Do not run `bambu-mcp-daemon.sh` in parallel — see the trap above. |
| `Bootstrap failed: 5: Input/output error` | bootout/bootstrap teardown race | `sleep 5`, retry bootstrap |
| Env-dependent behavior unchanged after "restart" | The trap: launchd relaunched with plist env | Use the plist + bootout/bootstrap route |
| Daemon down, launchd job not loaded | bootout without bootstrap | `launchctl bootstrap gui/$(id -u) <plist>` |
| MCP tools failing after restart | Client stuck on dead connection | Restart the client's MCP connection |
| Sessions show `connected: false` | Printer/MQTT side, not the daemon | Check printer power/LAN, then `get_session_status` |

## Update triggers (keep this guide current)

- `bambu-mcp-daemon.sh`, `daemon_port.py`, or the logging block in `server.py` changes →
  re-verify the procedures and knobs here.
- The service descriptor schema or the host's service tooling changes → re-verify
  the supervision model and regen commands.
- Port pool or transport changes → update Ports.
- A module added to or removed from the daemon's import tree, or a change to
  `hooks/version_bump_gate.py` or the `__version__` line in `server.py` → re-verify Release a change. `.venv/bin/python3 test_version.py`
  fails when `SERVING` and the tree disagree.
