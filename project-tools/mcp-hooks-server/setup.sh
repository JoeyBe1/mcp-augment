#!/bin/bash
# setup.sh — detect running mcp-augment server port and write correct MCP client config.
# Run once after install, and any time the server port changes.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
PORT_FILE="/tmp/mcp-augment.port"
CONFIG_OUT="$PROJECT_DIR/mcp_config.json"

# Start server if not already running
if [ ! -f "$PORT_FILE" ] || ! kill -0 "$(cat /tmp/mcp-augment.pid 2>/dev/null)" 2>/dev/null; then
    echo "Server not running — starting..."
    bash "$SCRIPT_DIR/start-servers.sh" &
    sleep 3
fi

# Read port
PORT=$(cat "$PORT_FILE" 2>/dev/null)
if [ -z "$PORT" ]; then
    echo "ERROR: Could not detect server port." >&2
    exit 1
fi

# Health check
URL="http://localhost:${PORT}/mcp"
if ! curl -s --max-time 3 "$URL" >/dev/null 2>&1; then
    echo "ERROR: Server not responding at $URL" >&2
    exit 1
fi

# Write client config
cat > "$CONFIG_OUT" <<EOF
{
  "mcpServers": {
    "mcp-augment": {
      "type": "remote",
      "url": "http://localhost:${PORT}/mcp"
    }
  }
}
EOF

echo "MCP client config written: $CONFIG_OUT"
echo "URL: $URL"
