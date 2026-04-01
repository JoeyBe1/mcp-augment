"""Live-config integration: demo_search_backend + Bash hooks (modifiedInput / modifiedOutput)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def hooks_server():
    """Fresh KiloHooksMCP with PROJECT_DIR = repo root (loads .kilo/hooks/config.yaml from disk)."""
    os.environ["PROJECT_DIR"] = str(_ROOT)
    sys.path.insert(0, str(_ROOT / "project-tools" / "mcp-hooks-server"))
    from importlib.machinery import SourceFileLoader

    mod = SourceFileLoader(
        "kilo_hooks_demo",
        str(_ROOT / "project-tools" / "mcp-hooks-server" / "mcp-augment.py"),
    ).load_module()
    return mod.KiloHooksMCP()


def test_demo_search_two_way_interception(hooks_server):
    """PreToolUse rewrites 2025->2026; PostToolUse strips INTERNAL_DEBUG and adds marker."""
    backend = _ROOT / "project-tools" / "mcp-hooks-server" / "demo_search_backend.py"
    cmd = f'python3 "{backend}" --query "mcp augment release date 2025"'
    result = hooks_server._safe_bash({"command": cmd, "timeout": 30})
    assert result.get("blocked") is False, result
    out = result.get("stdout", "")
    assert "2026" in out, "pre-hook should rewrite year before execution"
    assert "[POST-HOOK FILTERED]" in out
    assert "INTERNAL_DEBUG" not in out
