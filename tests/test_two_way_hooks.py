"""Tests for two-way hook interception (modifiedInput + modifiedOutput).

Exercises run_command_hook parsing, execute_hook_chain propagation,
and safe_* tool integration with PreToolUse input modification
and synchronous PostToolUse output modification.
"""

import json
import os
import stat
import sys
import textwrap
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Import the server class under test
sys.path.insert(0, str(Path(__file__).parent.parent / "project-tools" / "mcp-hooks-server"))
from importlib.machinery import SourceFileLoader

_mod = SourceFileLoader(
    "mcp_augment",
    str(Path(__file__).parent.parent / "project-tools" / "mcp-hooks-server" / "mcp-augment.py"),
).load_module()
MCAugmentMCP = _mod.MCAugmentMCP


def _make_hook_script(tmp_path: Path, name: str, body: str) -> str:
    """Create an executable bash hook script that reads stdin and prints body."""
    p = tmp_path / name
    p.write_text(textwrap.dedent(f"""\
        #!/bin/bash
        TOOL_INPUT=$(cat)
        {body}
    """))
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return str(p)


@pytest.fixture
def hooks_server(tmp_path):
    """MCAugmentMCP with isolated project dir, no real config loaded."""
    server = MCAugmentMCP.__new__(MCAugmentMCP)
    server.project_dir = str(tmp_path)
    server.rules_file = str(tmp_path / "rules.yaml")
    server.log_file = str(tmp_path / "log.txt")
    server.state_file = str(tmp_path / "state.json")
    server.file_monitors = {}
    server.review_interactive_fn = None
    server._cached_config = {"hooks": {}, "mode_enforcement": {}, "settings": {}}
    server._config_loaded = True
    os.makedirs(tmp_path / "logs", exist_ok=True)
    return server


def _inject_hooks(server, event_name: str, matcher: str, hook_defs: list):
    """Inject hook definitions into the cached config for a given event."""
    hooks_section = server._cached_config.setdefault("hooks", {})
    entries = hooks_section.setdefault(event_name, [])
    entries.append({"matcher": matcher, "hooks": hook_defs})


# ──────────────────────────────────────────────────────────────────
# 1. run_command_hook: modifiedInput parsing
# ──────────────────────────────────────────────────────────────────

class TestRunCommandHookModifiedInput:

    def test_exit_0_with_modified_input(self, hooks_server, tmp_path):
        """Hook returns JSON with modifiedInput at exit 0 — parsed correctly."""
        script = _make_hook_script(tmp_path, "mi.sh",
            'echo \'{"modifiedInput": {"file_path": "/tmp/redirected"}}\'')
        hook = {"type": "command", "command": script, "timeout": 5}
        result = hooks_server.run_command_hook(hook, {"tool_input": {}})
        assert result["blocked"] is False
        assert result["modifiedInput"] == {"file_path": "/tmp/redirected"}

    def test_exit_0_with_modified_output(self, hooks_server, tmp_path):
        """Hook returns JSON with modifiedOutput at exit 0 — parsed correctly."""
        script = _make_hook_script(tmp_path, "mo.sh",
            'echo \'{"modifiedOutput": {"content": "[REDACTED]"}}\'')
        hook = {"type": "command", "command": script, "timeout": 5}
        result = hooks_server.run_command_hook(hook, {"tool_input": {}})
        assert result["blocked"] is False
        assert result["modifiedOutput"] == {"content": "[REDACTED]"}

    def test_exit_0_empty_stdout_no_modification(self, hooks_server, tmp_path):
        """Hook returns exit 0 with no stdout — no modification fields (regression)."""
        script = _make_hook_script(tmp_path, "noop.sh", "exit 0")
        hook = {"type": "command", "command": script, "timeout": 5}
        result = hooks_server.run_command_hook(hook, {"tool_input": {}})
        assert result == {"blocked": False}

    def test_exit_2_still_blocks(self, hooks_server, tmp_path):
        """Hook returns exit 2 — BLOCKED, no modification (regression)."""
        script = _make_hook_script(tmp_path, "block.sh", "exit 2")
        hook = {"type": "command", "command": script, "timeout": 5}
        result = hooks_server.run_command_hook(hook, {"tool_input": {}})
        assert result["blocked"] is True

    def test_non_dict_modified_input_ignored(self, hooks_server, tmp_path):
        """modifiedInput must be a dict — strings are ignored."""
        script = _make_hook_script(tmp_path, "bad.sh",
            'echo \'{"modifiedInput": "not-a-dict"}\'')
        hook = {"type": "command", "command": script, "timeout": 5}
        result = hooks_server.run_command_hook(hook, {"tool_input": {}})
        assert result == {"blocked": False}


# ──────────────────────────────────────────────────────────────────
# 2. execute_hook_chain: modifiedInput propagation
# ──────────────────────────────────────────────────────────────────

class TestChainPropagation:

    def test_modified_input_propagated_in_chain(self, hooks_server, tmp_path):
        """First hook modifies input, second hook sees the modified input."""
        script1 = _make_hook_script(tmp_path, "h1.sh",
            'echo \'{"modifiedInput": {"file_path": "/tmp/chain_test"}}\'')
        script2 = _make_hook_script(tmp_path, "h2.sh", textwrap.dedent("""\
            FP=$(echo "$TOOL_INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('tool_input',{}).get('file_path','MISSING'))")
            echo "{\\"modifiedInput\\": {\\"file_path\\": \\"${FP}_v2\\"}}"
        """))

        _inject_hooks(hooks_server, "PreToolUse", "Read", [
            {"type": "command", "command": script1, "timeout": 5},
            {"type": "command", "command": script2, "timeout": 5},
        ])

        event_data = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Read",
            "tool_input": {"file_path": "/original"},
            "session_id": "test",
            "cwd": str(tmp_path),
        }
        result = hooks_server.execute_hook_chain("PreToolUse", "Read", event_data, can_block=True)
        assert result["blocked"] is False
        assert result["modifiedInput"]["file_path"] == "/tmp/chain_test_v2"


# ──────────────────────────────────────────────────────────────────
# 3. execute_hook_chain: synchronous PostToolUse with modifiedOutput
# ──────────────────────────────────────────────────────────────────

class TestSynchronousPostToolUse:

    def test_synchronous_captures_modified_output(self, hooks_server, tmp_path):
        """synchronous=True captures modifiedOutput from PostToolUse hooks."""
        script = _make_hook_script(tmp_path, "post.sh",
            'echo \'{"modifiedOutput": {"content": "[FILTERED]"}}\'')

        _inject_hooks(hooks_server, "PostToolUse", "Read", [
            {"type": "command", "command": script, "timeout": 5},
        ])

        event_data = {
            "hook_event_name": "PostToolUse",
            "tool_name": "Read",
            "tool_input": {"file_path": "/test"},
            "tool_output": {"content": "original"},
            "session_id": "test",
            "cwd": str(tmp_path),
        }
        result = hooks_server.execute_hook_chain(
            "PostToolUse", "Read", event_data, can_block=False, synchronous=True
        )
        assert result["modifiedOutput"] == {"content": "[FILTERED]"}

    def test_async_default_does_not_capture(self, hooks_server, tmp_path):
        """Default async PostToolUse does NOT return modifiedOutput (fire-and-forget)."""
        script = _make_hook_script(tmp_path, "post2.sh",
            'echo \'{"modifiedOutput": {"content": "should_not_appear"}}\'')

        _inject_hooks(hooks_server, "PostToolUse", "Read", [
            {"type": "command", "command": script, "timeout": 5},
        ])

        event_data = {
            "hook_event_name": "PostToolUse",
            "tool_name": "Read",
            "tool_input": {},
            "session_id": "test",
            "cwd": str(tmp_path),
        }
        result = hooks_server.execute_hook_chain(
            "PostToolUse", "Read", event_data, can_block=False, synchronous=False
        )
        assert "modifiedOutput" not in result


# ──────────────────────────────────────────────────────────────────
# 4. safe_read: end-to-end two-way integration
# ──────────────────────────────────────────────────────────────────

class TestSafeReadTwoWay:

    def test_modified_input_redirects_file(self, hooks_server, tmp_path):
        """PreToolUse hook redirects file_path; safe_read reads the new path."""
        real_file = tmp_path / "real.txt"
        real_file.write_text("real content")
        decoy = tmp_path / "decoy.txt"
        decoy.write_text("decoy content")

        redirect_json = json.dumps({"modifiedInput": {"file_path": str(real_file)}})
        script = _make_hook_script(tmp_path, "redir.sh",
            f"echo '{redirect_json}'")

        _inject_hooks(hooks_server, "PreToolUse", "Read", [
            {"type": "command", "command": script, "timeout": 5},
        ])

        result = hooks_server._safe_read({"file_path": str(decoy)})
        assert result["blocked"] is False
        assert result["content"] == "real content"
        assert result["file_path"] == str(real_file)

    def test_modified_output_transforms_content(self, hooks_server, tmp_path):
        """PostToolUse hook transforms output content; safe_read returns modified."""
        src = tmp_path / "secret.txt"
        src.write_text("super secret data")

        script = _make_hook_script(tmp_path, "redact.sh",
            'echo \'{"modifiedOutput": {"content": "[REDACTED]"}}\'')

        _inject_hooks(hooks_server, "PostToolUse", "Read", [
            {"type": "command", "command": script, "timeout": 5},
        ])

        result = hooks_server._safe_read({"file_path": str(src)})
        assert result["blocked"] is False
        assert result["content"] == "[REDACTED]"

    def test_no_hooks_unchanged(self, hooks_server, tmp_path):
        """No hooks configured — safe_read returns raw content (regression)."""
        src = tmp_path / "plain.txt"
        src.write_text("hello world")

        result = hooks_server._safe_read({"file_path": str(src)})
        assert result["blocked"] is False
        assert result["content"] == "hello world"


# ──────────────────────────────────────────────────────────────────
# 5. safe_write: modifiedInput changes file_path
# ──────────────────────────────────────────────────────────────────

class TestSafeWriteTwoWay:

    def test_modified_input_redirects_write(self, hooks_server, tmp_path):
        """PreToolUse hook redirects write path."""
        target = tmp_path / "redirected.txt"
        redirect_json = json.dumps({"modifiedInput": {"file_path": str(target)}})
        script = _make_hook_script(tmp_path, "redir_w.sh",
            f"echo '{redirect_json}'")

        _inject_hooks(hooks_server, "PreToolUse", "Write", [
            {"type": "command", "command": script, "timeout": 5},
        ])

        original_path = str(tmp_path / "original.txt")
        result = hooks_server._safe_write({"file_path": original_path, "content": "data"})
        assert result["blocked"] is False
        assert result["wrote"] == str(target)
        assert target.read_text() == "data"
        assert not Path(original_path).exists()
