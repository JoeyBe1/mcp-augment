#!/bin/bash
# auto-approve-safe.sh
# PermissionRequest hook: Auto-approve safe read-only commands
#
# Usage in config.yaml:
#   PermissionRequest:
#     - matcher: "Bash"
#       hooks:
#         - type: command
#           command: ".kilo/hooks/auto-approve-safe.sh"

INPUT=$(cat)
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // empty')

if [ -z "$COMMAND" ]; then
  exit 0
fi

# Safe patterns — auto-approve without asking
SAFE_PATTERNS=(
  "^ls"
  "^pwd"
  "^cat "
  "^head "
  "^tail "
  "^echo "
  "^git status"
  "^git log"
  "^git diff"
  "^git branch"
  "^jj status"
  "^jj log"
  "^jj diff"
  "^npm test"
  "^npm run test"
  "^npm run lint"
  "^npm run build"
  "^pytest"
  "^python.*test"
  "^which "
  "^type "
  "^find .* -name"
  "^rg "
  "^grep "
)

for pattern in "${SAFE_PATTERNS[@]}"; do
  if echo "$COMMAND" | grep -qE "$pattern"; then
    # Auto-approve — exit 0 silently
    exit 0
  fi
done

# Not in safe list — let normal permission dialog show
exit 0
