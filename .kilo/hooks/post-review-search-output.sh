#!/bin/bash
# PostToolUse (Bash): review-resume demo — propose shaped stdout via reviewOutput.
# Skips unless Bash command contained REVIEW_DEMO and stdout looks like demo search.
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
out = (inp.get("tool_output") or {}).get("stdout") or ""
if "REVIEW_DEMO" not in cmd:
    sys.exit(0)
if "[DEMO_SEARCH]" not in out:
    sys.exit(0)
lines = [ln for ln in out.splitlines() if not ln.startswith("INTERNAL_DEBUG:")]
merged = "[POST-HOOK FILTERED]\n" + "\n".join(lines)
print(
    json.dumps(
        {
            "reviewOutput": {"stdout": merged},
            "reviewTitle": "Demo: search output review",
            "reviewInstructions": (
                "Edit OUTPUT.proposed_tool_output if needed, save in TextEdit, then close."
            ),
        }
    )
)
' <<< "$INPUT"
exit 0
