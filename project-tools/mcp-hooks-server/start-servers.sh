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

PIDFILE="/tmp/mcp-augment.pid"

# Kill only our own previously-started instance
if [ -f "$PIDFILE" ]; then
    OLD_PID=$(cat "$PIDFILE")
    kill "$OLD_PID" 2>/dev/null
    rm -f "$PIDFILE"
    sleep 1
fi

# Find a free port starting at 8200
PORT=8200
while lsof -i ":$PORT" | grep -q LISTEN; do
    PORT=$((PORT + 1))
done

# Start hooks server on the free port
nohup "$PYTHON" "$SCRIPT_DIR/mcp-augment-http.py" "$PORT" > "/tmp/mcp-augment-${PORT}.log" 2>&1 &
SERVER_PID=$!
echo "$SERVER_PID" > "$PIDFILE"
echo "$PORT" > /tmp/mcp-augment.port
echo "mcp-augment started on port $PORT (PID: $SERVER_PID)"
echo "MCP URL: http://localhost:${PORT}/mcp"

sleep 2

# Verify
if lsof -i ":$PORT" | grep -q LISTEN; then
    echo "mcp-augment HTTP server running on port $PORT."
else
    echo "ERROR: Server failed to start. Check /tmp/mcp-augment-${PORT}.log"
    exit 1
fi

# Keep script alive so launchd doesn't restart while servers are running
wait
