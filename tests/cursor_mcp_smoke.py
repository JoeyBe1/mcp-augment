#!/usr/bin/env python3
"""
Smoke test for mcp-augment: same MCAugmentMCP code path as mcp-augment-http.py.
Run: PROJECT_DIR=<repo> python3 tests/cursor_mcp_smoke.py
Used when Cursor has not yet loaded mcp-augment as an MCP server.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
os.environ.setdefault("PROJECT_DIR", str(REPO))
sys.path.insert(0, str(REPO / "project-tools" / "mcp-hooks-server"))

from importlib.machinery import SourceFileLoader  # noqa: E402

_mod = SourceFileLoader(
    "mcpaug",
    str(REPO / "project-tools" / "mcp-hooks-server" / "mcp-augment.py"),
).load_module()
hooks = _mod.MCAugmentMCP()


def main() -> int:
    results: dict = {}

    r = hooks._safe_read({"file_path": str(REPO / "pyproject.toml")})
    results["1_safe_read"] = {
        "ok": not r.get("blocked") and "content" in r,
        "bytes": len(r.get("content", "")),
    }

    r = hooks._safe_write({"file_path": str(REPO / ".env"), "content": "SECRET=1"})
    results["2_safe_write_env_blocked"] = r.get("blocked") is True

    p = REPO / "_cursor_mcp_smoke_test.txt"
    r = hooks._safe_write({"file_path": str(p), "content": "smoke"})
    results["3_safe_write_temp"] = not r.get("blocked") and p.exists()

    r = hooks._safe_bash({"command": "echo ok", "timeout": 5})
    results["4_safe_bash_echo"] = not r.get("blocked") and "ok" in (r.get("stdout") or "")

    destructive = "rm -rf /"
    r = hooks._safe_bash({"command": destructive, "timeout": 5})
    results["5_safe_bash_destructive_blocked"] = r.get("blocked") is True

    r = hooks._safe_delete({"file_path": str(REPO / ".env")})
    results["6_safe_delete_env_blocked"] = r.get("blocked") is True

    from hook_validator import validate_hook  # noqa: E402

    vp = REPO / ".kilo/hooks/block-sensitive-files.sh"
    v = json.loads(validate_hook(str(vp)))
    # Production hooks often FAIL the "silent" heuristic (grep for echo); tool must return valid JSON.
    results["7_validate_hook_returns_json"] = v.get("verdict") in ("PASS", "FAIL") and "checks" in v

    gc = hooks._handle_get_hooks_config()
    results["8_get_hooks_config"] = "config" in gc

    ml = hooks._manage_hook({"action": "list"})
    results["9_manage_hook_list"] = "hooks" in ml and isinstance(ml["hooks"], dict)

    if p.exists():
        p.unlink()

    ok_flags = [
        results["1_safe_read"]["ok"],
        results["2_safe_write_env_blocked"],
        results["3_safe_write_temp"],
        results["4_safe_bash_echo"],
        results["5_safe_bash_destructive_blocked"],
        results["6_safe_delete_env_blocked"],
        results["7_validate_hook_returns_json"],
        results["8_get_hooks_config"],
        bool(results["9_manage_hook_list"]),
    ]
    print(json.dumps({"results": results, "all_passed": all(ok_flags)}, indent=2))
    return 0 if all(ok_flags) else 1


if __name__ == "__main__":
    raise SystemExit(main())
