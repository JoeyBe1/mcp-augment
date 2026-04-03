#!/bin/bash
# Start mcp-augment HTTP MCP (port 8200) for Kilo Code and other HTTP clients.
# Run this before launching Kilo, or use the launchd plist for auto-start.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Use project venv python (has mcp package) — launchd doesn't inherit user PATH
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
PYTHON="$PROJECT_DIR/.venv/bin/python3"
if [ ! -x "$PYTHON" ]; then
    PYTHON="python3"  # fallback to PATH
fi

PIDFILE="/tmp/mcp-augment-8200.pid"

# Kill only our own previously-started instance (not other processes on 8200)
if [ -f "$PIDFILE" ]; then
    OLD_PID=$(cat "$PIDFILE")
    kill "$OLD_PID" 2>/dev/null
    rm -f "$PIDFILE"
    sleep 1
fi

# If port 8200 is still occupied by something else, abort — don't clobber it
if lsof -i :8200 | grep -q LISTEN; then
    echo "ERROR: Port 8200 in use by another process (not ours). Not starting." >&2
    exit 1
fi

# Start hooks server (port 8200)
nohup "$PYTHON" "$SCRIPT_DIR/mcp-augment-http.py" > /tmp/mcp-augment-8200.log 2>&1 &
SERVER_PID=$!
echo "$SERVER_PID" > "$PIDFILE"
echo "mcp-augment started on port 8200 (PID: $SERVER_PID)"

sleep 2

# Verify
if lsof -i :8200 | grep -q LISTEN; then
    echo "mcp-augment HTTP server running."
else
    echo "ERROR: Server failed to start. Check /tmp/mcp-augment-8200.log"
    exit 1
fi

# Keep script alive so launchd doesn't restart while servers are running
wait
