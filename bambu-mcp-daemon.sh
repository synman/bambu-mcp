#!/usr/bin/env bash
# bambu-mcp-daemon.sh — Manage the bambu-mcp streamable-http daemon.
#
# Usage:
#   ./bambu-mcp-daemon.sh start    Start the daemon (if not already running)
#   ./bambu-mcp-daemon.sh stop     Stop the running daemon
#   ./bambu-mcp-daemon.sh restart  Stop + start
#   ./bambu-mcp-daemon.sh status   Check daemon health
#
# Port range: 25000–25099 (IANA registered, above isaac-mcp 23975–24999)
# Port file:  ~/.bambu-mcp/daemon.port
# PID file:   ~/.bambu-mcp/daemon.pid
# Log file:   bambu-mcp.log (in repo root)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
PYTHON="$SCRIPT_DIR/.venv/bin/python3"
SERVER="$SCRIPT_DIR/server.py"
PORT_FILE="$HOME/.bambu-mcp/daemon.port"
PID_FILE="$HOME/.bambu-mcp/daemon.pid"
LOG_FILE="$SCRIPT_DIR/bambu-mcp.log"

_is_alive() {
    local pid="$1"
    kill -0 "$pid" 2>/dev/null
}

_read_pid() {
    [[ -f "$PID_FILE" ]] && cat "$PID_FILE" 2>/dev/null || echo ""
}

_read_port() {
    [[ -f "$PORT_FILE" ]] && cat "$PORT_FILE" 2>/dev/null || echo ""
}

_cleanup() {
    rm -f "$PORT_FILE" "$PID_FILE"
}

cmd_start() {
    local pid
    pid="$(_read_pid)"
    if [[ -n "$pid" ]] && _is_alive "$pid"; then
        local port
        port="$(_read_port)"
        echo "bambu-mcp daemon already running (PID $pid, port ${port:-unknown})"
        return 0
    fi

    # Clean up stale files
    [[ -n "$pid" ]] && echo "Cleaning up stale PID file (PID $pid is dead)" && _cleanup

    echo "Starting bambu-mcp daemon..."
    cd "$SCRIPT_DIR"
    nohup "$PYTHON" "$SERVER" --transport streamable-http >> "$LOG_FILE" 2>&1 &

    # Wait for port file (up to 30s)
    local elapsed=0
    while [[ ! -f "$PORT_FILE" ]] && (( elapsed < 30 )); do
        sleep 1
        elapsed=$(( elapsed + 1 ))
    done

    if [[ -f "$PORT_FILE" ]]; then
        local port
        port="$(_read_port)"
        pid="$(_read_pid)"
        echo "bambu-mcp daemon started (PID $pid, MCP port $port)"
    else
        echo "ERROR: daemon did not write port file within 30s. Check $LOG_FILE"
        return 1
    fi
}

cmd_stop() {
    local pid
    pid="$(_read_pid)"
    if [[ -z "$pid" ]]; then
        echo "No PID file found — daemon not running"
        _cleanup
        return 0
    fi

    if ! _is_alive "$pid"; then
        echo "PID $pid is dead — cleaning up stale files"
        _cleanup
        return 0
    fi

    echo "Stopping bambu-mcp daemon (PID $pid)..."
    kill "$pid" 2>/dev/null

    # Wait up to 10s for clean shutdown
    local elapsed=0
    while _is_alive "$pid" && (( elapsed < 10 )); do
        sleep 1
        elapsed=$(( elapsed + 1 ))
    done

    if _is_alive "$pid"; then
        echo "SIGTERM timeout — sending SIGKILL"
        kill -9 "$pid" 2>/dev/null
        sleep 1
    fi

    _cleanup
    echo "bambu-mcp daemon stopped"
}

cmd_restart() {
    cmd_stop
    cmd_start
}

cmd_status() {
    local pid port
    pid="$(_read_pid)"
    port="$(_read_port)"

    if [[ -z "$pid" ]]; then
        echo "bambu-mcp daemon: NOT RUNNING (no PID file)"
        return 1
    fi

    if ! _is_alive "$pid"; then
        echo "bambu-mcp daemon: DEAD (stale PID $pid)"
        return 1
    fi

    echo "bambu-mcp daemon: RUNNING"
    echo "  PID:      $pid"
    echo "  MCP port: ${port:-unknown}"

    # TCP probe
    if [[ -n "$port" ]]; then
        if nc -z localhost "$port" 2>/dev/null; then
            echo "  TCP:      OK (port $port responding)"
        else
            echo "  TCP:      FAIL (port $port not responding)"
        fi
    fi

    # mDNS check
    if command -v dns-sd &>/dev/null; then
        if timeout 2 dns-sd -L bambu-mcp _bambu-mcp._tcp local. 2>/dev/null | grep -q "port"; then
            echo "  mDNS:     registered"
        else
            echo "  mDNS:     not found (may take a moment)"
        fi
    fi
}

case "${1:-}" in
    start)   cmd_start ;;
    stop)    cmd_stop ;;
    restart) cmd_restart ;;
    status)  cmd_status ;;
    -h|--help)
        echo "Usage: $0 {start|stop|restart|status}"
        echo ""
        echo "Manage the bambu-mcp streamable-http daemon."
        echo "Port range: 25000–25099 (registered, above isaac-mcp 23975–24999)"
        ;;
    *)
        echo "Usage: $0 {start|stop|restart|status}" >&2
        exit 1
        ;;
esac
