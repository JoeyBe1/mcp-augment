#!/usr/bin/env python3
"""
HTTP + stdio transport for mcp-augment (same FastMCP tools).

HTTP (Kilo / curl / tests on port 8200):
  python3 mcp-augment-http.py
  python3 mcp-augment-http.py 8300

Stdio (Cursor — spawn as subprocess; no separate HTTP server):
  python3 mcp-augment-http.py --stdio

Config in .kilo/kilo.json:
  "mcp-augment": { "type": "remote", "url": "http://localhost:8200/mcp" }

Config in .cursor/mcp.json:
  command + args including this script and --stdio (see repo .cursor/mcp.json).
"""

import sys
import os
import json
from pathlib import Path

# Ensure project dir is set
PROJECT_DIR = os.environ.get("PROJECT_DIR", str(Path(os.path.abspath(__file__)).parents[2]))
os.environ["PROJECT_DIR"] = PROJECT_DIR

# Add parent to path so we can import the server
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcp.server.fastmcp import FastMCP

from hook_validator import validate_hook as _validate_hook_impl

# Import our existing server logic
from importlib.machinery import SourceFileLoader
_mod = SourceFileLoader("mcp_augment", os.path.join(os.path.dirname(__file__), "mcp-augment.py")).load_module()
MCAugmentMCP = _mod.MCAugmentMCP

# Create the hooks server instance (reuses ALL existing logic)
hooks = MCAugmentMCP()

# HTTP listen port (ignore --stdio in argv so Cursor spawn does not break import)
_argv_rest = [a for a in sys.argv[1:] if a != "--stdio"]
PORT = int(_argv_rest[0]) if _argv_rest else 8200
mcp = FastMCP("mcp-augment", host="127.0.0.1", port=PORT)


@mcp.tool()
def hook_event(event_name: str, tool_name: str, tool_input: dict, tool_output: dict = None) -> str:
    """Fire any Claude Code hook event (PreToolUse, PostToolUse, etc.). Returns ALLOWED or BLOCKED with reason."""
    args = {
        "event_name": event_name,
        "tool_name": tool_name,
        "tool_input": tool_input,
    }
    if tool_output:
        args["tool_output"] = tool_output
    result = hooks._handle_hook_event(args)
    return json.dumps(result, indent=2)


@mcp.tool()
def pre_validate(action: str, path: str) -> str:
    """Pre-operation validation (simulates PreToolUse hook)."""
    result = hooks.pre_validate(action, path)
    return json.dumps(result.to_dict(), indent=2)


@mcp.tool()
def batch_validate(operations: list) -> str:
    """Validate multiple operations at once."""
    results = hooks.batch_validate(operations)
    return json.dumps({"results": [r.to_dict() for r in results]}, indent=2)


@mcp.tool()
def get_hooks_config() -> str:
    """Return the current hooks configuration from the project hooks config file."""
    result = hooks._handle_get_hooks_config()
    return json.dumps(result, indent=2)


@mcp.tool()
def start_file_monitor(file_path: str) -> str:
    """Start monitoring file for changes (mtime, size, process)."""
    result = hooks.start_file_monitor(file_path)
    return json.dumps(result, indent=2)


@mcp.tool()
def check_file_changed(file_path: str) -> str:
    """Check if monitored file has changed."""
    result = hooks.check_file_changed(file_path)
    return json.dumps(result, indent=2)


@mcp.tool()
def notify_user(title: str, message: str, file_path: str = None) -> str:
    """Show notification or open file in editor."""
    success = hooks.notify_user(title, message, file_path)
    return json.dumps({"notified": success})


@mcp.tool()
def safe_write(file_path: str, content: str) -> str:
    """MANDATORY file write tool. ALL file writes MUST go through this tool. Validates path against security hooks (blocks .env, credentials, secrets), then writes if allowed. Returns {blocked: true, reason: ...} or {blocked: false, wrote: path, bytes: N}. ALWAYS use this instead of native Write/write_to_file."""
    result = hooks._safe_write({"file_path": file_path, "content": content})
    return json.dumps(result, indent=2)


@mcp.tool()
def safe_edit(file_path: str, old_string: str, new_string: str) -> str:
    """MANDATORY file edit tool. ALL file edits MUST go through this tool. Validates path against security hooks, then performs find-replace if allowed. Returns {blocked: true, reason: ...} or {blocked: false, edited: path}. ALWAYS use this instead of native Edit/apply_diff."""
    result = hooks._safe_edit({"file_path": file_path, "old_string": old_string, "new_string": new_string})
    return json.dumps(result, indent=2)


@mcp.tool()
def safe_bash(command: str, timeout: int = 30) -> str:
    """MANDATORY command execution tool. ALL bash/shell commands MUST go through this tool. Validates command against security hooks (blocks rm -rf, sudo, force-push), then executes if allowed. Returns {blocked: true, reason: ...} or {blocked: false, exit_code: N, stdout: ..., stderr: ...}. ALWAYS use this instead of native Bash/execute_command."""
    result = hooks._safe_bash({"command": command, "timeout": timeout})
    return json.dumps(result, indent=2)


@mcp.tool()
def safe_read(file_path: str) -> str:
    """MANDATORY file read tool. ALL file reads MUST go through this tool. Validates path against security hooks, then reads content if allowed. Returns {blocked: true, reason: ...} or {blocked: false, content: ...}. ALWAYS use this instead of native Read/read_file."""
    result = hooks._safe_read({"file_path": file_path})
    return json.dumps(result, indent=2)


@mcp.tool()
def safe_delete(file_path: str) -> str:
    """MANDATORY file delete tool. ALL file deletions MUST go through this tool. Validates path against security hooks (blocks .env, credentials, secrets), then deletes if allowed. Returns {blocked: true, reason: ...} or {blocked: false, deleted: path}. ALWAYS use this instead of native delete/rm."""
    result = hooks._safe_delete({"file_path": file_path})
    return json.dumps(result, indent=2)


@mcp.tool()
def open_in_editor(file_path: str) -> str:
    """Open a file in the system editor (TextEdit on macOS, vim fallback). Returns editor used and file metadata for change detection."""
    result = hooks.open_in_editor(file_path)
    return json.dumps(result, indent=2)


@mcp.tool()
def manage_hook(action: str, event_name: str = None, matcher: str = None,
                command: str = None, hook_type: str = None, timeout: int = None) -> str:
    """Add, remove, or list hooks in config.yaml. Actions: 'list' (show all registered hooks), 'add' (register a hook script for an event), 'remove' (unregister a hook by command path). Hooks added here fire for ALL connected AI tools. Note: HTTP server restart needed for config changes to take full effect."""
    args = {"action": action}
    if event_name is not None:
        args["event_name"] = event_name
    if matcher is not None:
        args["matcher"] = matcher
    if command is not None:
        args["command"] = command
    if hook_type is not None:
        args["hook_type"] = hook_type
    if timeout is not None:
        args["timeout"] = timeout
    result = hooks._manage_hook(args)
    return json.dumps(result, indent=2)


@mcp.tool()
def validate_hook(hook_script_path: str) -> str:
    """Validate a hook script for Claude Code / mcp-augment compliance.
    Checks: file exists + executable, bash syntax (bash -n), reads stdin (required for Claude Code hooks),
    and silence (no echo/printf to stdout which breaks JSON validation).
    Returns per-check results and overall verdict."""
    return _validate_hook_impl(hook_script_path)


if __name__ == "__main__":
    if "--stdio" in sys.argv:
        # Cursor / MCP hosts: line-delimited JSON-RPC on stdin/stdout (no prints to stdout)
        mcp.run(transport="stdio")
    else:
        print(f"Starting mcp-augment HTTP server on port {PORT}", file=sys.stderr)
        print(f"Configure Kilo with: \"url\": \"http://localhost:{PORT}/mcp\"", file=sys.stderr)
        mcp.run(transport="streamable-http")
