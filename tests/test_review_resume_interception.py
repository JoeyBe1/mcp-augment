"""Tests for review-resume (reviewInput / reviewOutput) with injectable review_interactive_fn."""

from __future__ import annotations

import json
import os
import stat
import sys
import textwrap
from pathlib import Path
from unittest.mock import Mock

import pytest

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "project-tools" / "mcp-hooks-server"))
from importlib.machinery import SourceFileLoader  # noqa: E402

_mod = SourceFileLoader(
    "kilo_hooks_rr",
    str(_ROOT / "project-tools" / "mcp-hooks-server" / "mcp-augment.py"),
).load_module()
KiloHooksMCP = _mod.KiloHooksMCP


def _make_hook_script(tmp_path: Path, name: str, body: str) -> str:
    p = tmp_path / name
    p.write_text(
        textwrap.dedent(
            f"""\
        #!/bin/bash
        TOOL_INPUT=$(cat)
        {body}
    """
        )
    )
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return str(p)


def _inject_hooks(server, event_name: str, matcher: str, hook_defs: list) -> None:
    hooks_section = server._cached_config.setdefault("hooks", {})
    entries = hooks_section.setdefault(event_name, [])
    entries.append({"matcher": matcher, "hooks": hook_defs})


@pytest.fixture
def hooks_server(tmp_path):
    server = KiloHooksMCP.__new__(KiloHooksMCP)
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


class TestRunCommandHookReviewFields:
    def test_parses_review_input_and_metadata(self, hooks_server, tmp_path):
        payload = {
            "reviewInput": {"command": "echo ok"},
            "reviewTitle": "T",
            "reviewInstructions": "I",
        }
        script = _make_hook_script(tmp_path, "r.sh", f"echo '{json.dumps(payload)}'")
        hook = {"type": "command", "command": script, "timeout": 5}
        result = hooks_server.run_command_hook(hook, {"tool_input": {}})
        assert result["blocked"] is False
        assert result["reviewInput"] == {"command": "echo ok"}
        assert result["reviewTitle"] == "T"
        assert result["reviewInstructions"] == "I"


class TestReviewWaitBehavior:
    def test_review_timeout_default_is_wait_forever(self, monkeypatch, hooks_server):
        monkeypatch.delenv("MCP_AUGMENT_REVIEW_TIMEOUT", raising=False)
        assert hooks_server._review_timeout_seconds() == 0

    def test_review_waits_for_close_after_save(
        self, monkeypatch, hooks_server, tmp_path
    ):
        review_file = tmp_path / "review.mcp-augment-review.json"
        initial = json.dumps({"OUTPUT": {"proposed_tool_output": {"stdout": "before"}}})
        edited = json.dumps({"OUTPUT": {"proposed_tool_output": {"stdout": "after"}}})

        class FakeTempFile:
            def __init__(self, path: Path):
                self.name = str(path)
                self._path = path

            def write(self, text: str) -> int:
                self._path.write_text(text, encoding="utf-8")
                return len(text)

            def flush(self) -> None:
                return None

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb) -> None:
                return None

        monkeypatch.setattr(
            _mod.tempfile,
            "NamedTemporaryFile",
            lambda **kwargs: FakeTempFile(review_file),
        )

        state = {"sleep_calls": 0}

        def fake_sleep(_seconds: float) -> None:
            state["sleep_calls"] += 1
            if state["sleep_calls"] == 2:
                review_file.write_text(edited, encoding="utf-8")

        times = iter([0.0, 0.5, 1.0, 1.5, 2.0, 2.5])

        monkeypatch.setattr(_mod.time, "sleep", fake_sleep)
        monkeypatch.setattr(_mod.time, "time", lambda: next(times))

        def fake_run(command, **kwargs):
            if command[:3] == ["open", "-a", "TextEdit"]:
                return Mock(returncode=0, stdout="", stderr="")
            raise AssertionError(f"Unexpected subprocess.run call: {command}")

        monkeypatch.setattr(_mod.subprocess, "run", fake_run)
        monkeypatch.setattr(_mod.os, "unlink", lambda _p: None)
        monkeypatch.setattr(
            hooks_server,
            "_is_review_file_open",
            lambda _filepath: state["sleep_calls"] < 3,
        )
        result = hooks_server._review_textedit_wait_for_edit(initial, timeout=0)

        assert json.loads(result) == json.loads(edited)
        assert state["sleep_calls"] >= 2


class TestReviewEnvelopeShape:
    def test_review_artifact_uses_prompt_ia_style_sections(self, hooks_server):
        captured = {}

        def editor(initial: str) -> str:
            captured["initial"] = json.loads(initial)
            return initial

        hooks_server.review_interactive_fn = editor
        result = hooks_server._run_review_envelope(
            phase="tool_input",
            original={"command": "ORIGINAL"},
            proposed={"command": "PROPOSED"},
            instructions="Review the command carefully.",
            title="PreToolUse review",
        )

        assert result == {"command": "PROPOSED"}
        assert (
            captured["initial"]["INPUT"]["original_tool_input"]["command"] == "ORIGINAL"
        )
        assert (
            captured["initial"]["OUTPUT"]["proposed_tool_input"]["command"]
            == "PROPOSED"
        )
        assert (
            "EDIT ONLY OUTPUT.proposed_tool_input"
            in captured["initial"]["OUTPUT"]["instructions"]
        )
        assert (
            captured["initial"]["METADATA"]["edit_field"]
            == "OUTPUT.proposed_tool_input"
        )


class TestPreReviewResumeChain:
    def test_review_input_merges_edited_tool_input(self, hooks_server, tmp_path):
        """Simulated user edits proposed command via review_interactive_fn."""

        def editor(initial: str) -> str:
            data = json.loads(initial)
            assert data["METADATA"]["edit_field"] == "OUTPUT.proposed_tool_input"
            data["OUTPUT"]["proposed_tool_input"]["command"] = "USER_EDITED_CMD"
            return json.dumps(data)

        ri = json.dumps(
            {
                "reviewInput": {"command": "PROPOSED"},
                "reviewTitle": "pre",
                "reviewInstructions": "edit",
            }
        )
        script = _make_hook_script(tmp_path, "pre.sh", f"echo '{ri}'")
        _inject_hooks(
            hooks_server,
            "PreToolUse",
            "Bash",
            [
                {"type": "command", "command": script, "timeout": 5},
            ],
        )

        hooks_server.review_interactive_fn = editor
        event_data = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "ORIGINAL"},
            "session_id": "test",
            "cwd": str(tmp_path),
        }
        result = hooks_server.execute_hook_chain(
            "PreToolUse", "Bash", event_data, can_block=True
        )
        assert result["blocked"] is False
        assert result["modifiedInput"]["command"] == "USER_EDITED_CMD"

    def test_legacy_output_path_still_parses_for_input(self, hooks_server, tmp_path):
        def editor(initial: str) -> str:
            data = json.loads(initial)
            data["OUTPUT"] = {"proposed_tool_input": {"command": "LEGACY_EDIT_CMD"}}
            return json.dumps(data)

        ri = json.dumps({"reviewInput": {"command": "PROPOSED"}})
        script = _make_hook_script(tmp_path, "pre-legacy.sh", f"echo '{ri}'")
        _inject_hooks(
            hooks_server,
            "PreToolUse",
            "Bash",
            [
                {"type": "command", "command": script, "timeout": 5},
            ],
        )

        hooks_server.review_interactive_fn = editor
        event_data = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "ORIGINAL"},
            "session_id": "test",
            "cwd": str(tmp_path),
        }
        result = hooks_server.execute_hook_chain(
            "PreToolUse", "Bash", event_data, can_block=True
        )
        assert result["modifiedInput"]["command"] == "LEGACY_EDIT_CMD"

    def test_invalid_editor_json_falls_back_to_proposed(self, hooks_server, tmp_path):
        ri = json.dumps({"reviewInput": {"command": "FALLBACK_CMD"}})
        script = _make_hook_script(tmp_path, "pre2.sh", f"echo '{ri}'")
        _inject_hooks(
            hooks_server,
            "PreToolUse",
            "Bash",
            [
                {"type": "command", "command": script, "timeout": 5},
            ],
        )
        hooks_server.review_interactive_fn = lambda _s: "not-json"

        event_data = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "ORIGINAL"},
            "session_id": "test",
            "cwd": str(tmp_path),
        }
        result = hooks_server.execute_hook_chain(
            "PreToolUse", "Bash", event_data, can_block=True
        )
        assert result["modifiedInput"]["command"] == "FALLBACK_CMD"


class TestPostReviewResumeChain:
    def test_review_output_merges_edited_stdout(self, hooks_server, tmp_path):
        def editor(initial: str) -> str:
            data = json.loads(initial)
            assert data["METADATA"]["edit_field"] == "OUTPUT.proposed_tool_output"
            data["OUTPUT"]["proposed_tool_output"]["stdout"] = "USER_STDOUT"
            return json.dumps(data)

        ro = json.dumps(
            {
                "reviewOutput": {"stdout": "PROPOSED_OUT"},
                "reviewTitle": "post",
                "reviewInstructions": "edit out",
            }
        )
        script = _make_hook_script(tmp_path, "post.sh", f"echo '{ro}'")
        _inject_hooks(
            hooks_server,
            "PostToolUse",
            "Bash",
            [
                {"type": "command", "command": script, "timeout": 5},
            ],
        )
        hooks_server.review_interactive_fn = editor
        event_data = {
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "c"},
            "tool_output": {"stdout": "RAW"},
            "session_id": "test",
            "cwd": str(tmp_path),
        }
        result = hooks_server.execute_hook_chain(
            "PostToolUse", "Bash", event_data, can_block=False, synchronous=True
        )
        assert result["modifiedOutput"]["stdout"] == "USER_STDOUT"

    def test_legacy_output_path_still_parses_for_output(self, hooks_server, tmp_path):
        def editor(initial: str) -> str:
            data = json.loads(initial)
            data["OUTPUT"] = {"proposed_tool_output": {"stdout": "LEGACY_STDOUT"}}
            return json.dumps(data)

        ro = json.dumps({"reviewOutput": {"stdout": "PROPOSED_OUT"}})
        script = _make_hook_script(tmp_path, "post-legacy.sh", f"echo '{ro}'")
        _inject_hooks(
            hooks_server,
            "PostToolUse",
            "Bash",
            [
                {"type": "command", "command": script, "timeout": 5},
            ],
        )
        hooks_server.review_interactive_fn = editor
        event_data = {
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "c"},
            "tool_output": {"stdout": "RAW"},
            "session_id": "test",
            "cwd": str(tmp_path),
        }
        result = hooks_server.execute_hook_chain(
            "PostToolUse", "Bash", event_data, can_block=False, synchronous=True
        )
        assert result["modifiedOutput"]["stdout"] == "LEGACY_STDOUT"


@pytest.fixture
def hooks_server_repo():
    os.environ["PROJECT_DIR"] = str(_ROOT)
    sys.path.insert(0, str(_ROOT / "project-tools" / "mcp-hooks-server"))
    mod = SourceFileLoader(
        "kilo_hooks_demo_rr",
        str(_ROOT / "project-tools" / "mcp-hooks-server" / "mcp-augment.py"),
    ).load_module()
    srv = mod.KiloHooksMCP()
    srv.review_interactive_fn = lambda s: s
    return srv


def test_demo_review_resume_e2e_accepts_proposal(hooks_server_repo):
    """Live config: REVIEW_DEMO path uses review hooks; inject accepts TextEdit payload."""
    backend = _ROOT / "project-tools" / "mcp-hooks-server" / "demo_search_backend.py"
    cmd = f'python3 "{backend}" --query "mcp augment release date REVIEW_DEMO 2025"'
    result = hooks_server_repo._safe_bash({"command": cmd, "timeout": 30})
    assert result.get("blocked") is False, result
    out = result.get("stdout", "")
    assert "2026" in out
    assert "[POST-HOOK FILTERED]" in out
    assert "INTERNAL_DEBUG" not in out


def test_review_input_takes_precedence_over_modified_input_same_hook(
    hooks_server, tmp_path
):
    """If both are present, reviewInput path runs (no double-merge of modifiedInput)."""
    both = json.dumps(
        {
            "modifiedInput": {"command": "AUTO"},
            "reviewInput": {"command": "FOR_REVIEW"},
        }
    )
    script = _make_hook_script(tmp_path, "both.sh", f"echo '{both}'")
    _inject_hooks(
        hooks_server,
        "PreToolUse",
        "Bash",
        [
            {"type": "command", "command": script, "timeout": 5},
        ],
    )
    hooks_server.review_interactive_fn = lambda s: s

    event_data = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "START"},
        "session_id": "test",
        "cwd": str(tmp_path),
    }
    result = hooks_server.execute_hook_chain(
        "PreToolUse", "Bash", event_data, can_block=True
    )
    assert result["modifiedInput"]["command"] == "FOR_REVIEW"
