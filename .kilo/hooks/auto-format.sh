#!/bin/bash
# auto-format.sh
# PostToolUse hook: Auto-format file after write/edit
# Run with async: true — don't block the agent
#
# Usage in config.yaml:
#   PostToolUse:
#     - matcher: "Write|Edit|MultiEdit"
#       hooks:
#         - type: command
#           command: ".kilo/hooks/auto-format.sh"
#           async: true

INPUT=$(cat)
FILE=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')
CWD=$(echo "$INPUT" | jq -r '.cwd // empty')

if [ -z "$FILE" ] || [ ! -f "$FILE" ]; then
  exit 0
fi

# Change to project dir if available
if [ -n "$CWD" ]; then
  cd "$CWD" || true
fi

EXT="${FILE##*.}"

case "$EXT" in
  js|jsx|ts|tsx|json|css|md|yaml|yml)
    if command -v npx &>/dev/null; then
      npx prettier --write "$FILE" 2>/dev/null || true
    fi
    ;;
  py)
    if command -v black &>/dev/null; then
      black --quiet "$FILE" 2>/dev/null || true
    elif command -v autopep8 &>/dev/null; then
      autopep8 --in-place "$FILE" 2>/dev/null || true
    fi
    ;;
  go)
    if command -v gofmt &>/dev/null; then
      gofmt -w "$FILE" 2>/dev/null || true
    fi
    ;;
  rs)
    if command -v rustfmt &>/dev/null; then
      rustfmt "$FILE" 2>/dev/null || true
    fi
    ;;
esac

# PostToolUse cannot block — always exit 0
exit 0
