#!/bin/bash
# block-sensitive-files.sh
# PreToolUse hook: Block editing .env, credentials, secrets files
# PORTABLE: Works with both Claude Code AND mcp-augment (same stdin format)
#
# Usage in config.yaml:
#   PreToolUse:
#     - matcher: "Edit|Write|MultiEdit"
#       hooks:
#         - type: command
#           command: ".kilo/hooks/block-sensitive-files.sh"

INPUT=$(cat)
FILE=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')

# No file path = not a file edit, allow
if [ -z "$FILE" ]; then
  exit 0
fi

# Protected patterns
PROTECTED_PATTERNS=(
  "\.env$"
  "\.env\."
  "credentials"
  "secrets"
  "\.pem$"
  "\.key$"
  "id_rsa"
  "\.git/config"
  "password"
)

for pattern in "${PROTECTED_PATTERNS[@]}"; do
  if echo "$FILE" | grep -qiE "$pattern"; then
    jq -n \
      --arg file "$FILE" \
      '{
        hookSpecificOutput: {
          hookEventName: "PreToolUse",
          permissionDecision: "deny",
          permissionDecisionReason: ("Protected file: " + $file + ". Edit manually if needed.")
        }
      }'
    exit 2
  fi
done

exit 0
