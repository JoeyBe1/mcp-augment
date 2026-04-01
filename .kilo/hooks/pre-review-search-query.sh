#!/bin/bash
# PreToolUse (Bash): review-resume demo — propose corrected command via reviewInput (TextEdit).
# Skips unless command contains REVIEW_DEMO and demo_search_backend.py with stale year.
INPUT=$(cat)
python3 -c '
import json
import sys

raw = sys.stdin.read()
try:
    inp = json.loads(raw)
except json.JSONDecodeError:
    sys.exit(0)
cmd = (inp.get("tool_input") or {}).get("command") or ""
if "REVIEW_DEMO" not in cmd:
    sys.exit(0)
if "demo_search_backend.py" not in cmd or "2025" not in cmd:
    sys.exit(0)
new_cmd = cmd.replace("2025", "2026")
print(
    json.dumps(
        {
            "reviewInput": {"command": new_cmd},
            "reviewTitle": "Demo: search command review",
            "reviewInstructions": (
                "Edit OUTPUT.proposed_tool_input if needed, save in TextEdit, then close."
            ),
        }
    )
)
' <<< "$INPUT"
exit 0
