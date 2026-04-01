"""
Standalone hook validation logic — no MCP/FastMCP dependency.
Imported by mcp-augment-http.py (as tool) and by tests directly.
"""

import os
import re
import json
import subprocess


def validate_hook(hook_script_path: str) -> str:
    """Validate a hook script for Claude Code / mcp-augment compliance.
    Checks: file exists + executable, bash syntax (bash -n), reads stdin (required for Claude Code hooks),
    and silence (no echo/printf to stdout which breaks JSON validation).
    Returns per-check results and overall verdict."""
    checks = {}
    path = os.path.expandvars(os.path.expanduser(hook_script_path))

    # 1. Exists and executable
    exists = os.path.isfile(path)
    executable = os.access(path, os.X_OK) if exists else False
    checks["exists"] = {"pass": exists, "detail": path if exists else f"Not found: {path}"}
    checks["executable"] = {"pass": executable, "detail": "chmod +x required" if exists and not executable else "ok"}

    if not exists:
        return json.dumps({"verdict": "FAIL", "checks": checks}, indent=2)

    try:
        content = open(path).read()
    except Exception as e:
        return json.dumps({"verdict": "FAIL", "checks": {"read_error": str(e)}}, indent=2)

    # 2. Bash syntax check
    try:
        result = subprocess.run(["bash", "-n", path], capture_output=True, text=True, timeout=5)
        syntax_ok = result.returncode == 0
        checks["bash_syntax"] = {"pass": syntax_ok, "detail": result.stderr.strip() or "ok"}
    except Exception as e:
        checks["bash_syntax"] = {"pass": False, "detail": str(e)}

    # 3. Reads stdin (Claude Code hook requirement: hooks must read full stdin JSON)
    reads_stdin = bool(re.search(r'\bcat\b|\bread\b|TOOL_INPUT=|stdin', content))
    checks["reads_stdin"] = {
        "pass": reads_stdin,
        "detail": "ok" if reads_stdin else "Hook must consume stdin (e.g. TOOL_INPUT=$(cat)) to prevent pipe blocking"
    }

    # 4. Silent (no bare echo/printf to stdout — causes JSON validation errors)
    # Allow echo to stderr (>&2) or to files (>> / >)
    loud_lines = [
        ln.strip() for ln in content.splitlines()
        if re.search(r'\b(echo|printf)\b', ln)
        and not re.search(r'>&2|>>\s*\S|>\s*\S', ln)
        and not ln.strip().startswith('#')
    ]
    checks["silent"] = {
        "pass": len(loud_lines) == 0,
        "detail": "ok" if not loud_lines else f"Stdout output on lines: {loud_lines[:3]}"
    }

    verdict = "PASS" if all(c["pass"] for c in checks.values()) else "FAIL"
    return json.dumps({"verdict": verdict, "checks": checks}, indent=2)
