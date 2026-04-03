#!/bin/bash
# Stop mcp-augment HTTP server (port 8200)
lsof -ti :8200 | xargs kill 2>/dev/null && echo "Stopped mcp-augment (8200)" || echo "No server on 8200"
