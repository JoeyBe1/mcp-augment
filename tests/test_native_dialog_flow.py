"""Tests for the native macOS AppleScript dialog flow in mcp-augment."""

import json
import os
import sys
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "project-tools" / "mcp-hooks-server"))
from importlib.machinery import SourceFileLoader

_mod = SourceFileLoader(
    "kilo_hooks_native",
    str(_ROOT / "project-tools" / "mcp-hooks-server" / "mcp-augment.py"),
).load_module()
KiloHooksMCP = _mod.KiloHooksMCP


@pytest.fixture
def hooks_server(tmp_path):
    server = KiloHooksMCP.__new__(KiloHooksMCP)
    server.project_dir = str(tmp_path)
    server.log_file = str(tmp_path / "log.txt")
    server.review_interactive_fn = None
    server._cached_config = {"settings": {}}
    server._config_loaded = True
    os.makedirs(tmp_path / "logs", exist_ok=True)
    return server


def test_native_accept_bypasses_textedit(hooks_server):
    """If user clicks 'Accept', we return the initial JSON and skip TextEdit."""
    initial = json.dumps({"METADATA": {"title": "T"}})
    
    with patch("subprocess.run") as mock_run, \
         patch.object(hooks_server, "_review_textedit_wait_for_edit") as mock_wait:
        
        # Simulate osascript returning "button returned:Accept"
        mock_run.return_value = Mock(returncode=0, stdout="button returned:Accept")
        
        res = hooks_server._run_review_envelope(
            phase="tool_input",
            original={},
            proposed={"cmd": "ok"},
            instructions="instr",
            title="Title"
        )
        
        # Check that osascript was called
        assert mock_run.called
        # command was ["osascript", "-e", script]
        assert "display dialog" in mock_run.call_args[0][0][2]
        
        # Check that TextEdit fallback was NOT called
        assert not mock_wait.called
        
        # Check that we got the proposed data back (Accept = use proposal)
        assert res == {"cmd": "ok"}


def test_native_decline_blocks_action(hooks_server):
    """If user clicks 'Decline', we return an empty dict (effectively blocking)."""
    with patch("subprocess.run") as mock_run, \
         patch.object(hooks_server, "_review_textedit_wait_for_edit") as mock_wait:
        
        mock_run.return_value = Mock(returncode=0, stdout="button returned:Decline")
        
        res = hooks_server._run_review_envelope(
            phase="tool_input",
            original={},
            proposed={"cmd": "danger"},
            instructions="instr",
            title="Title"
        )
        
        assert mock_run.called
        assert not mock_wait.called
        
        # Decline returns empty dict which blocks the tool call
        assert res == {}


def test_native_v10_looping_flow(hooks_server):
    """V10: Looping field picker. Pick -> Edit -> Back to Picker -> DONE."""
    proposed = {"command": "echo 'old text'"}
    new_text = "looping new text"
    
    with patch("subprocess.run") as mock_run, \
         patch.object(hooks_server, "_review_textedit_wait_for_edit") as mock_wait:
        
        # 1. Main Dialog -> Edit
        # 2. Picker -> 1. EDIT MESSAGE: ... (no confirm step — goes straight to edit box)
        # 3. Edit Box -> looping new text (returns back to Picker)
        # 4. Picker -> ✅ DONE (Finish Editing)

        mock_run.side_effect = [
            Mock(returncode=0, stdout="button returned:Edit"),
            Mock(returncode=0, stdout="1. EDIT MESSAGE: old text..."),
            Mock(returncode=0, stdout=new_text),
            Mock(returncode=0, stdout="✅ DONE (Finish Editing)")
        ]
        
        res = hooks_server._run_review_envelope(
            phase="tool_input",
            original={},
            proposed=proposed,
            instructions="instr"
        )
        
        # Verify result is applied and loop exited correctly
        assert res["command"] == f"echo '{new_text}'"
        assert not mock_wait.called


def test_native_error_falls_back_to_textedit(hooks_server):
    """If osascript fails (non-zero or error), it should transparently fall back."""
    proposed = {"cmd": "ok"}
    envelope = json.dumps({"OUTPUT": {"proposed_tool_input": proposed}})
    
    with patch("subprocess.run") as mock_run, \
         patch.object(hooks_server, "_review_textedit_wait_for_edit") as mock_wait:
        
        # osascript fails (e.g. timeout or not macOS)
        mock_run.return_value = Mock(returncode=1, stderr="error")
        mock_wait.return_value = envelope
        
        res = hooks_server._run_review_envelope(
            phase="tool_input",
            original={},
            proposed=proposed,
            instructions="instr",
            title="Title"
        )
        
        assert mock_run.called
        assert mock_wait.called
        assert res == proposed
