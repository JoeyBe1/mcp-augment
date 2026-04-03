#!/bin/bash
# PostToolUse (Bash): demo HITL — macOS notification when demo search backend ran.
INPUT=$(cat)
OUT=$(jq -r '.tool_output.stdout // ""' <<< "$INPUT")
if [[ "$OUT" == *"[DEMO_SEARCH]"* ]]; then
  osascript -e 'display notification "Demo search output was post-processed." with title "mcp-augment"' >/dev/null 2>&1 || true
fi
exit 0
