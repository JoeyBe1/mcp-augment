#!/bin/bash
# mode-enforcement.sh
# PreToolUse hook: Mode-aware file protection (upgraded from mode-guide.sh)
#
# DIFFERENCES from .claude/hooks/mode-guide.sh:
#   - Old: guidance only (exit 0 always, log to stderr)
#   - New: ENFORCEMENT (exit 2 to block, JSON output for reason)
#   - Old: only watches Bash commands
#   - New: watches Edit, Write, MultiEdit (file_path) + Bash (command)
#
# Usage in config.yaml:
#   PreToolUse:
#     - matcher: "Edit|Write|MultiEdit|Bash"
#       hooks:
#         - type: command
#           command: ".kilo/hooks/mode-enforcement.sh"

INPUT=$(cat)
TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty')
CWD=$(echo "$INPUT" | jq -r '.cwd // empty')

# Find project root
PROJECT_DIR="${CWD:-$(pwd)}"
MODE_FILE="$PROJECT_DIR/.claude/session-env/autoresearch_mode"

MODE=$(cat "$MODE_FILE" 2>/dev/null || echo "research")

# Extract the path being operated on
if [[ "$TOOL_NAME" == "Bash" ]]; then
  CMD=$(echo "$INPUT" | jq -r '.tool_input.command // empty')
  # Extract file-like paths from bash command
  PATH_TO_CHECK="$CMD"
else
  # Edit, Write, MultiEdit
  PATH_TO_CHECK=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')
fi

if [ -z "$PATH_TO_CHECK" ]; then
  exit 0
fi

block() {
  local reason="$1"
  local suggestion="${2:-}"
  local msg="$reason"
  if [ -n "$suggestion" ]; then
    msg="$msg Suggestion: $suggestion"
  fi
  jq -n --arg reason "$msg" '{
    hookSpecificOutput: {
      hookEventName: "PreToolUse",
      permissionDecision: "deny",
      permissionDecisionReason: $reason
    }
  }'
  exit 2
}

case "$MODE" in
  "optimize")
    # eval/ is ALWAYS immutable in optimize mode
    if echo "$PATH_TO_CHECK" | grep -qE "(^|/)eval/"; then
      block "CRITICAL: eval/ is ALWAYS immutable in optimize mode. The evaluation harness must remain stable." \
            "Move to research mode if you need to modify eval/."
    fi
    # target/current/ is locked in optimize mode (use candidates/ instead)
    if echo "$PATH_TO_CHECK" | grep -qE "(^|/)target/current/"; then
      block "Optimize mode: target/current/ is locked. Use candidates/ to create variants." \
            "Create target/candidates/my-variant/ instead."
    fi
    ;;

  "benchmark")
    # target/current/ is read-only during benchmarking
    if echo "$PATH_TO_CHECK" | grep -qE "(^|/)target/current/"; then
      block "Benchmark mode: target/current/ is read-only during measurement." \
            "Switch to optimize mode to modify candidates."
    fi
    # target/*.py files are protected
    if echo "$PATH_TO_CHECK" | grep -qE "(^|/)target/[^/]+\.py$"; then
      block "Benchmark mode: Cannot modify target/*.py files during measurement." \
            "Switch to optimize mode first."
    fi
    ;;

  "eval")
    # eval mode: EVERYTHING is blocked
    block "CRITICAL: eval mode is fully immutable. The evaluation harness cannot be modified." \
          "Switch to research or optimize mode."
    ;;

  "research")
    # No restrictions in research mode
    exit 0
    ;;
esac

exit 0
