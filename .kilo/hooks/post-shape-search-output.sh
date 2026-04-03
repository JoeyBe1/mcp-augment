#!/bin/bash
# PostToolUse (Bash): demo only — strip INTERNAL_DEBUG line, prepend visibility marker.
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
if "REVIEW_DEMO" in cmd:
    sys.exit(0)
out = (inp.get("tool_output") or {}).get("stdout") or ""
if "[DEMO_SEARCH]" not in out:
    sys.exit(0)
lines = [ln for ln in out.splitlines() if not ln.startswith("INTERNAL_DEBUG:")]
merged = "[POST-HOOK FILTERED]\n" + "\n".join(lines)
print(json.dumps({"modifiedOutput": {"stdout": merged}}))
' <<< "$INPUT"
exit 0
