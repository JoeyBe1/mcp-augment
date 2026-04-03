#!/bin/bash
# validate-bash-command.sh
# PreToolUse hook: Block dangerous bash commands
# Blocks: rm -rf, DROP TABLE, git reset --hard, git push --force, curl piped to bash
#
# Usage in config.yaml:
#   PreToolUse:
#     - matcher: "Bash"
#       hooks:
#         - type: command
#           command: ".kilo/hooks/validate-bash-command.sh"

INPUT=$(cat)
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // empty')

if [ -z "$COMMAND" ]; then
  exit 0
fi

block() {
  local reason="$1"
  jq -n --arg reason "$reason" '{
    hookSpecificOutput: {
      hookEventName: "PreToolUse",
      permissionDecision: "deny",
      permissionDecisionReason: $reason
    }
  }'
  exit 2
}

# Destructive file operations
if echo "$COMMAND" | grep -qE 'rm\s+(-[a-zA-Z]*r[a-zA-Z]*f|--force.*-r|-rf)'; then
  block "Blocked: rm -rf is destructive. Use trash or move to .archive/ instead."
fi

# Database destruction
if echo "$COMMAND" | grep -qiE 'DROP\s+(TABLE|DATABASE|INDEX)'; then
  block "Blocked: DROP statements are destructive. Use a migration instead."
fi

# Git destructive operations
if echo "$COMMAND" | grep -qE 'git\s+reset\s+--hard'; then
  block "Blocked: git reset --hard destroys uncommitted work. Use git stash or jj."
fi

if echo "$COMMAND" | grep -qE 'git\s+push\s+(-f|--force)'; then
  block "Blocked: force push can destroy remote history. Use --force-with-lease instead."
fi

if echo "$COMMAND" | grep -qE 'git\s+clean\s+-[a-zA-Z]*f'; then
  block "Blocked: git clean -f permanently deletes untracked files."
fi

# Pipe from network to shell (supply chain risk)
if echo "$COMMAND" | grep -qE 'curl.*\|\s*(ba)?sh'; then
  block "Blocked: piping curl to shell is a supply chain risk. Download first, review, then execute."
fi

if echo "$COMMAND" | grep -qE 'wget.*\|\s*(ba)?sh'; then
  block "Blocked: piping wget to shell is a supply chain risk. Download first, review, then execute."
fi

# chmod 777 (world-writable)
if echo "$COMMAND" | grep -qE 'chmod\s+777'; then
    block "Blocked: chmod 777 makes files world-writable. Use chmod 755 for dirs, 644 for files."
fi

# sudo (elevated privileges)
if echo "$COMMAND" | grep -qE '^sudo\s'; then
    block "Blocked: sudo commands require manual review. Run without sudo or verify safety first."
fi

exit 0
