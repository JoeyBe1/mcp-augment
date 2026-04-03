"""Tests for validate_hook — imports directly from hook_validator.py (same code the MCP tool delegates to)."""

import os
import sys
import stat
import json
import builtins
import subprocess
import pytest
from pathlib import Path
from unittest.mock import patch

# Point at the module the server imports — no mocking needed, stdlib only
sys.path.insert(0, str(Path(__file__).parent.parent / "project-tools" / "mcp-hooks-server"))
from hook_validator import validate_hook


def _make_script(tmp_path, content: str, executable: bool = True) -> str:
    p = tmp_path / "hook.sh"
    p.write_text(content)
    if executable:
        p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return str(p)


class TestValidateHook:

    def test_missing_file_fails(self):
        result = json.loads(validate_hook("/nonexistent/path/hook.sh"))
        assert result["verdict"] == "FAIL"
        assert result["checks"]["exists"]["pass"] is False

    def test_not_executable_fails(self, tmp_path):
        p = _make_script(tmp_path, "#!/bin/bash\nTOOL_INPUT=$(cat)\n", executable=False)
        result = json.loads(validate_hook(p))
        assert result["verdict"] == "FAIL"
        assert result["checks"]["executable"]["pass"] is False

    def test_syntax_error_fails(self, tmp_path):
        p = _make_script(tmp_path, "#!/bin/bash\nTOOL_INPUT=$(cat)\nif [ {\n")
        result = json.loads(validate_hook(p))
        assert result["verdict"] == "FAIL"
        assert result["checks"]["bash_syntax"]["pass"] is False

    def test_no_stdin_read_fails(self, tmp_path):
        p = _make_script(tmp_path, "#!/bin/bash\nexit 0\n")
        result = json.loads(validate_hook(p))
        assert result["verdict"] == "FAIL"
        assert result["checks"]["reads_stdin"]["pass"] is False

    def test_stdout_echo_fails(self, tmp_path):
        p = _make_script(tmp_path, "#!/bin/bash\nTOOL_INPUT=$(cat)\necho 'hello'\n")
        result = json.loads(validate_hook(p))
        assert result["verdict"] == "FAIL"
        assert result["checks"]["silent"]["pass"] is False

    def test_stderr_echo_is_ok(self, tmp_path):
        p = _make_script(tmp_path, "#!/bin/bash\nTOOL_INPUT=$(cat)\necho 'log' >&2\n")
        result = json.loads(validate_hook(p))
        assert result["checks"]["silent"]["pass"] is True

    def test_compliant_hook_passes(self, tmp_path):
        content = "#!/bin/bash\nTOOL_INPUT=$(cat)\n# do nothing\nexit 0\n"
        p = _make_script(tmp_path, content)
        result = json.loads(validate_hook(p))
        assert result["verdict"] == "PASS"
        assert all(c["pass"] for c in result["checks"].values())

    def test_returns_valid_json(self, tmp_path):
        p = _make_script(tmp_path, "#!/bin/bash\nTOOL_INPUT=$(cat)\n")
        raw = validate_hook(p)
        parsed = json.loads(raw)
        assert "verdict" in parsed
        assert "checks" in parsed

    def test_read_error_fails(self, tmp_path):
        p = _make_script(tmp_path, "#!/bin/bash\nTOOL_INPUT=$(cat)\n")
        with patch.object(builtins, "open", side_effect=OSError("read denied")):
            result = json.loads(validate_hook(p))
        assert result["verdict"] == "FAIL"
        assert result["checks"]["read_error"] == "read denied"

    def test_bash_syntax_exception_fails(self, tmp_path):
        p = _make_script(tmp_path, "#!/bin/bash\nTOOL_INPUT=$(cat)\n")
        with patch.object(subprocess, "run", side_effect=RuntimeError("bash missing")):
            result = json.loads(validate_hook(p))
        assert result["verdict"] == "FAIL"
        assert result["checks"]["bash_syntax"]["pass"] is False
        assert result["checks"]["bash_syntax"]["detail"] == "bash missing"
