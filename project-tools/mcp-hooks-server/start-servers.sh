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

# Kill any existing instance on 8200
lsof -ti :8200 | xargs kill 2>/dev/null
sleep 1

# Start hooks server (port 8200)
nohup "$PYTHON" "$SCRIPT_DIR/mcp-augment-http.py" > /tmp/mcp-augment-8200.log 2>&1 &
echo "mcp-augment started on port 8200 (PID: $!)"

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
