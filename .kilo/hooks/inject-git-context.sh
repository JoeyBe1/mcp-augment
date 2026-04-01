#!/bin/bash
# inject-git-context.sh
# SessionStart hook: Output git status as context when session begins
# Run with async: true — don't block startup
#
# Usage in config.yaml:
#   SessionStart:
#     - hooks:
#         - type: command
#           command: ".kilo/hooks/inject-git-context.sh"
#           async: true

INPUT=$(cat)
CWD=$(echo "$INPUT" | jq -r '.cwd // empty')

if [ -n "$CWD" ]; then
  cd "$CWD" || exit 0
fi

echo "=== GIT CONTEXT ==="
echo "Branch: $(git branch --show-current 2>/dev/null || echo 'N/A')"
echo ""
echo "Uncommitted changes:"
git status --short 2>/dev/null || echo "No git repo"
echo ""
echo "Recent commits:"
git log --oneline -5 2>/dev/null || echo "N/A"

# SessionStart cannot block — always exit 0
exit 0
