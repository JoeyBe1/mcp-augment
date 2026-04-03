#!/bin/bash
# PreToolUse (Bash): demo only — rewrite stale year in demo_search_backend.py command.
# JSON to stdout only via python3 print when modifying (passes validate_hook silent check).
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
if "demo_search_backend.py" in cmd and "2025" in cmd:
    new_cmd = cmd.replace("2025", "2026")
    print(json.dumps({"modifiedInput": {"command": new_cmd}}))
' <<< "$INPUT"
exit 0
