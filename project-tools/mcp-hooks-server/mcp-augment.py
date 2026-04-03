#!/usr/bin/env python3
"""
MCP Hooks Server for Kilo Code Mode Enforcement
================================================

MCP server that adds pre-operation validation, mode checking, batch
validation, file monitoring, and hook simulation for Kilo Code.
"""

import copy
import json
import sys
import os
import re
import subprocess
import tempfile
import time
import threading
from pathlib import Path
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional
from dataclasses import dataclass, asdict
from enum import Enum

# Configuration
PROJECT_DIR = os.environ.get("PROJECT_DIR", str(Path(__file__).resolve().parents[2]))
LOG_FILE = f"{PROJECT_DIR}/.claude/logs/mcp-augment.log"
LOG_FILE_KILO = f"{PROJECT_DIR}/.kilo/logs/mcp-augment.log"
STATE_FILE = f"{PROJECT_DIR}/.claude/logs/mcp-augment-state.json"
STATE_FILE_KILO = f"{PROJECT_DIR}/.kilo/logs/mcp-augment-state.json"
HOOKS_CONFIG_FILE = f"{PROJECT_DIR}/.kilo/hooks/config.yaml"

# User review-resume: max wait in TextEdit (seconds). 0 means wait indefinitely.
_DEFAULT_REVIEW_TIMEOUT_S = 0


class ActionStatus(Enum):
    ALLOWED = "allowed"
    WARNING = "warning"
    BLOCKED = "blocked"


@dataclass
class ValidationResult:
    status: ActionStatus
    message: str
    suggested_mode: Optional[str] = None
    action: str = ""
    path: str = ""
    current_mode: str = ""
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()

    def to_dict(self) -> Dict:
        return {**asdict(self), "status": self.status.value}


class MCAugmentMCP:
    """MCP server with hook simulation for mode enforcement"""

    def __init__(self):
        self.project_dir = PROJECT_DIR
        self.log_file = LOG_FILE
        self.state_file = STATE_FILE
        self.file_monitors = {}  # path -> {mtime, size, process}

        # Optional test hook: if set, called with review envelope JSON string; return final JSON string.
        self.review_interactive_fn: Optional[Callable[[str], str]] = None

        # Ensure directories exist
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        os.makedirs(os.path.dirname(LOG_FILE_KILO), exist_ok=True)

        # Config loaded lazily on first use
        self._cached_config = None
        self._config_loaded = False

    def log(self, message: str):
        """Log to file for debugging — writes to both .claude and .kilo"""
        entry = f"[{datetime.now().isoformat()}] {message}\n"
        for path in (LOG_FILE, LOG_FILE_KILO):
            with open(path, "a") as f:
                f.write(entry)

    def send_response(self, response: Dict[str, Any]):
        """Send JSON-RPC response to stdout"""
        response_str = json.dumps(response)
        sys.stdout.write(f"Content-Length: {len(response_str)}\r\n\r\n{response_str}")
        sys.stdout.flush()

    def _find_yq(self) -> str:
        """Find yq binary, checking common paths if not in PATH."""
        for yq_path in ["yq", "/opt/homebrew/bin/yq", "/usr/local/bin/yq"]:
            try:
                subprocess.run([yq_path, "--version"], capture_output=True, timeout=2)
                return yq_path
            except (FileNotFoundError, subprocess.TimeoutExpired):
                continue
        return "yq"  # fallback, let it fail with a clear error

    def load_hooks_config(self) -> Dict:
        """
        Load .kilo/hooks/config.yaml using yq.
        Caches result after first load. Returns full config dict or empty fallback.
        """
        if self._config_loaded:
            return self._cached_config

        config_file = HOOKS_CONFIG_FILE
        default = {"hooks": {}, "settings": {}}

        if not os.path.exists(config_file):
            self.log(f"No hooks config at {config_file}, using defaults")
            self._cached_config = default
            self._config_loaded = True
            return default

        try:
            yq = self._find_yq()
            result = subprocess.run(
                [yq, "-o", "json", config_file],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                config = json.loads(result.stdout)
                self.log(
                    f"Loaded hooks config: {len((config.get('hooks') or {}))} event types"
                )
                self._cached_config = config
                self._config_loaded = True
                return config
            else:
                self.log(f"yq error on hooks config: {result.stderr}")
                self._cached_config = default
                self._config_loaded = True
                return default
        except Exception as e:
            self.log(f"Failed to load hooks config: {e}")
            self._cached_config = default
            self._config_loaded = True
            return default

    def _get_hooks_for_event_from_config(
        self, config: Dict, event_name: str, tool_name: str
    ) -> List[Dict]:
        """
        Find all hook handler definitions matching this event + tool_name
        from a pre-loaded config dict.

        Walks config.hooks[event_name] entries, checks each matcher
        against tool_name using pipe-separated regex.

        Returns flat list of hook dicts:
        [{"type": "command", "command": "...", "timeout": 30}, ...]
        """
        event_entries = (config.get("hooks") or {}).get(event_name, [])

        matched_hooks = []
        for entry in event_entries:
            matcher = entry.get("matcher", "")

            # Empty matcher = match all tools
            if not matcher or re.match(f"^({matcher})$", tool_name):
                hooks_list = entry.get("hooks", [])
                matched_hooks.extend(hooks_list)

        self.log(
            f"get_hooks_for_event({event_name}, {tool_name}): {len(matched_hooks)} hooks matched"
        )
        return matched_hooks

    def get_hooks_for_event(self, event_name: str, tool_name: str) -> List[Dict]:
        """
        Find all hook handler definitions matching this event + tool_name.
        Loads config and delegates to _get_hooks_for_event_from_config.
        """
        config = self.load_hooks_config()
        return self._get_hooks_for_event_from_config(config, event_name, tool_name)

    def run_command_hook(self, hook: Dict, event_data: Dict) -> Dict:
        """
        Execute a type:command handler.

        Sends event_data as JSON on stdin.
        Reads exit code + stdout for decision.

        Exit code semantics (matches Claude Code exactly):
        0 = allow
        2 = block (parse stdout for reason)
        1, 3+ = non-fatal warning
        """
        command = hook.get("command", "")
        timeout = hook.get("timeout", 30)

        if not command:
            return {"blocked": False}

        try:
            event_json = json.dumps(event_data)

            result = subprocess.run(
                command,
                shell=True,
                input=event_json,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=self.project_dir,
            )

            self.log(
                f"Command hook '{command}': exit={result.returncode}, stdout={result.stdout[:200]}"
            )

            if result.returncode == 2:
                # BLOCKED — try to parse reason from stdout
                reason = f"Hook blocked: {command}"
                try:
                    output = json.loads(result.stdout)
                    hook_output = output.get("hookSpecificOutput", {})
                    if hook_output.get("permissionDecision") == "deny":
                        reason = hook_output.get("permissionDecisionReason", reason)
                except (json.JSONDecodeError, AttributeError):
                    # Fallback: use stderr or stdout as reason text
                    reason = result.stderr.strip() or result.stdout.strip() or reason

                return {"blocked": True, "reason": reason}

            elif result.returncode == 0:
                # ALLOWED — optional JSON on stdout for two-way + review-resume hooks
                response: Dict = {"blocked": False}
                raw = (result.stdout or "").strip()
                if raw:
                    try:
                        output = json.loads(raw)
                        if isinstance(output, dict):
                            self._merge_hook_response_json(output, response)
                    except (json.JSONDecodeError, TypeError, ValueError):
                        pass
                return response

            else:
                # Non-fatal warning (exit 1, 3, etc.)
                warning = result.stderr.strip() or result.stdout.strip()
                self.log(f"Command hook warning (exit {result.returncode}): {warning}")
                return {"blocked": False, "warning": warning}

        except subprocess.TimeoutExpired:
            self.log(f"Command hook timed out after {timeout}s: {command}")
            return {"blocked": False, "warning": f"Hook timed out: {command}"}

        except Exception as e:
            self.log(f"Command hook error: {e}")
            return {"blocked": False, "warning": f"Hook error: {e}"}

    def run_http_hook(self, hook: Dict, event_data: Dict) -> Dict:
        """
        Execute a type:http handler. POST event_data to hook["url"], read decision from response.
        Falls back to ALLOW on any network/parse error (fail-open for http).
        """
        url = hook.get("url", "")
        headers = hook.get("headers", {})
        timeout = hook.get("timeout", 10)

        if not url:
            return {"blocked": False}

        try:
            import urllib.request
            import urllib.error

            req_data = json.dumps(event_data).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=req_data,
                headers={"Content-Type": "application/json", **headers},
                method="POST",
            )

            with urllib.request.urlopen(req, timeout=timeout) as http_resp:
                body = json.loads(http_resp.read().decode("utf-8"))
                decision = body.get("decision", "allow")
                if decision == "deny":
                    return {
                        "blocked": True,
                        "reason": body.get("reason", f"HTTP hook denied: {url}"),
                    }
                out: Dict = {"blocked": False}
                if isinstance(body, dict):
                    self._merge_hook_response_json(body, out)
                return out

        except Exception as e:
            self.log(f"HTTP hook error ({url}): {e}")
            return {"blocked": False, "warning": f"HTTP hook unreachable: {e}"}

    def execute_handler(self, hook: Dict, event_data: Dict) -> Dict:
        """
        Route to the correct handler by type.

        Returns:
        {"blocked": bool, "reason": str} or {"blocked": False}
        """
        handler_type = hook.get("type", "")
        if handler_type == "command":
            return self.run_command_hook(hook, event_data)
        elif handler_type == "http":
            return self.run_http_hook(hook, event_data)
        elif handler_type == "prompt":
            self.log("prompt handler not yet implemented")
            return {"blocked": False, "warning": "prompt handler not implemented"}
        elif handler_type == "agent":
            self.log("agent handler not yet implemented")
            return {"blocked": False, "warning": "agent handler not implemented"}
        else:
            self.log(f"Unknown handler type: {handler_type}")
            return {"blocked": False}

    def _run_async_hook(self, hook: Dict, event_data: Dict):
        """Fire-and-forget hook execution in a thread."""
        try:
            result = self.execute_handler(hook, event_data)
            self.log(
                f"Async hook completed: {hook.get('command', 'unknown')} -> {json.dumps(result)}"
            )
        except Exception as e:
            self.log(f"Async hook error: {e}")

    def execute_hook_chain(
        self,
        event_name: str,
        tool_name: str,
        event_data: Dict,
        can_block: bool = True,
        synchronous: bool = False,
    ) -> Dict:
        """
        Run all matching hooks for this event.
        For blocking events (PreToolUse, PermissionRequest):
        - Check mode rules first
        - Then run config.yaml handlers
        - First BLOCKED result wins (fail_fast)
        - modifiedInput from hooks is shallow-merged into event_data["tool_input"]
        For non-blocking events (PostToolUse):
        - Default: all handlers run async (synchronous=False)
        - synchronous=True (used by safe_* tools): run sequentially, merge modifiedOutput dicts
        """
        # Load config once for both hook matching and settings
        config = self.load_hooks_config()

        # ── Config-driven handlers ──
        hooks = self._get_hooks_for_event_from_config(config, event_name, tool_name)

        if not can_block:
            if synchronous:
                # Synchronous PostToolUse (safe_*): capture modifiedOutput / reviewOutput
                warnings: List[str] = []
                merged_output: Dict[str, Any] = {}
                for hook in hooks:
                    result = self.execute_handler(hook, event_data)
                    if result.get("warning"):
                        warnings.append(result["warning"])
                    snap_out = copy.deepcopy(event_data.get("tool_output") or {})
                    ro = result.get("reviewOutput")
                    if isinstance(ro, dict):
                        instr = result.get(
                            "reviewInstructions",
                            "Edit OUTPUT.proposed_tool_output in this file, then save.",
                        )
                        title = result.get("reviewTitle", "PostToolUse review")
                        edited = self._run_review_envelope(
                            "tool_output", snap_out, ro, instr, title
                        )
                        merged_output.update(edited)
                        event_data["tool_output"] = {
                            **(event_data.get("tool_output") or {}),
                            **edited,
                        }
                    else:
                        mo = result.get("modifiedOutput")
                        if isinstance(mo, dict):
                            merged_output.update(mo)
                            event_data["tool_output"] = {
                                **(event_data.get("tool_output") or {}),
                                **mo,
                            }
                response: Dict[str, Any] = {
                    "blocked": False,
                    "handlers_dispatched": len(hooks),
                }
                if merged_output:
                    response["modifiedOutput"] = merged_output
                if warnings:
                    response["warnings"] = warnings
                return response

            # Non-blocking async (e.g. hook_event PostToolUse)
            dispatched = 0
            for hook in hooks:
                t = threading.Thread(
                    target=self._run_async_hook, args=(hook, event_data), daemon=True
                )
                t.start()
                dispatched += 1
            return {"blocked": False, "handlers_dispatched": dispatched}

        # Blocking: run sequentially, stop on first block
        warnings = []
        modified_input_applied = False

        for hook in hooks:
            if hook.get("async", False):
                # Even in blocking event, async hooks fire-and-forget
                t = threading.Thread(
                    target=self._run_async_hook, args=(hook, event_data), daemon=True
                )
                t.start()
                continue

            snap_in = copy.deepcopy(event_data.get("tool_input") or {})
            result = self.execute_handler(hook, event_data)
            if result.get("blocked"):
                return result
            if result.get("warning"):
                warnings.append(result["warning"])
            ri = result.get("reviewInput")
            if isinstance(ri, dict):
                instr = result.get(
                    "reviewInstructions",
                    "Edit OUTPUT.proposed_tool_input in this file, then save.",
                )
                title = result.get("reviewTitle", "PreToolUse review")
                edited = self._run_review_envelope(
                    "tool_input", snap_in, ri, instr, title
                )
                event_data["tool_input"] = {
                    **(event_data.get("tool_input") or {}),
                    **edited,
                }
                modified_input_applied = True
            else:
                mi = result.get("modifiedInput")
                if isinstance(mi, dict):
                    event_data["tool_input"] = {
                        **(event_data.get("tool_input") or {}),
                        **mi,
                    }
                    modified_input_applied = True

        response = {"blocked": False}
        if modified_input_applied:
            response["modifiedInput"] = dict(event_data.get("tool_input") or {})
        if warnings:
            response["warnings"] = warnings
        return response

    def _handle_hook_event(self, arguments: Dict) -> Dict:
        """Handler for the hook_event tool call."""
        event_name = arguments.get("event_name", "")
        tool_name = arguments.get("tool_name", "")
        tool_input = arguments.get("tool_input", {})
        tool_output = arguments.get("tool_output", {})

        # Determine if this event type can block
        BLOCKING_EVENTS = {
            "PreToolUse",
            "PermissionRequest",
            "UserPromptSubmit",
            "Stop",
            "SubagentStop",
            "TeammateIdle",
            "TaskCompleted",
            "ConfigChange",
            "WorktreeCreate",
            "Elicitation",
            "ElicitationResult",
        }
        can_block = event_name in BLOCKING_EVENTS

        event_data = {
            "hook_event_name": event_name,
            "tool_name": tool_name,
            "tool_input": tool_input,
            "session_id": arguments.get("session_id", "kilo-session"),
            "cwd": self.project_dir,
        }
        if tool_output:
            event_data["tool_output"] = tool_output

        result = self.execute_hook_chain(
            event_name, tool_name, event_data, can_block=can_block
        )

        # Enrich result with metadata
        result["event"] = event_name
        result["tool_name"] = tool_name
        result["timestamp"] = datetime.now().isoformat()
        return result

    def _handle_get_hooks_config(self) -> Dict:
        """Handler for the get_hooks_config tool call."""
        config = self.load_hooks_config()
        return {
            "config": config,
            "config_file": HOOKS_CONFIG_FILE,
            "file_exists": os.path.exists(HOOKS_CONFIG_FILE),
        }

    def pre_validate(
        self, action: str, path: str, tool_name: str = "Edit"
    ) -> ValidationResult:
        """
        PRE-OPERATION VALIDATION
        Simulates Claude's PreToolUse by validating BEFORE the action
        Uses execute_hook_chain for config-driven hooks.
        Returns result that calling code should check before proceeding
        """
        event_data = {
            "hook_event_name": "PreToolUse",
            "tool_name": tool_name,
            "tool_input": {"file_path": path, "action": action},
            "session_id": "kilo-session",
            "cwd": PROJECT_DIR,
        }
        result = self.execute_hook_chain("PreToolUse", tool_name, event_data)
        if result.get("blocked"):
            validation_result = ValidationResult(
                status=ActionStatus.BLOCKED,
                message=result["reason"],
                action=action,
                path=path,
            )
        else:
            validation_result = ValidationResult(
                status=ActionStatus.ALLOWED,
                message="Action permitted",
                action=action,
                path=path,
            )

        # Log the validation
        self.log(f"PRE-VALIDATE: {action} {path} -> {validation_result.status.value}")

        # Save state for potential rollback
        self.save_state(action, path, validation_result)

        return validation_result

    def batch_validate(
        self, operations: List[Dict[str, str]]
    ) -> List[ValidationResult]:
        """
        BATCH VALIDATION
        Validate multiple operations at once using full hook chain.
        Returns list of ValidationResult
        """
        results = []

        for op in operations:
            action = op.get("action", "")
            path = op.get("path", "")
            result = self.pre_validate(action, path)
            results.append(result)

        return results

    def save_state(self, action: str, path: str, result: ValidationResult):
        """Save validation state for potential rollback"""
        try:
            state = {
                "action": action,
                "path": path,
                "result": result.to_dict(),
                "timestamp": datetime.now().isoformat(),
            }
            with open(STATE_FILE, "w") as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            self.log(f"Failed to save state: {e}")

    def start_file_monitor(self, file_path: str) -> Dict:
        """
        START FILE MONITORING
        Tracks mtime, size, and owning process
        Returns monitor info for later comparison
        """
        try:
            stat = os.stat(file_path)
            monitor_info = {
                "path": file_path,
                "mtime": stat.st_mtime,
                "size": stat.st_size,
                "timestamp": datetime.now().isoformat(),
                "processes": self._get_file_processes(file_path),
            }
            self.file_monitors[file_path] = monitor_info
            return monitor_info
        except FileNotFoundError:
            return {"path": file_path, "error": "File not found"}

    def check_file_changed(self, file_path: str) -> Dict:
        """Check if file has changed since monitoring started"""
        if file_path not in self.file_monitors:
            return {"error": "Monitor not started for this file"}

        original = self.file_monitors[file_path]
        try:
            stat = os.stat(file_path)
            current = {
                "mtime": stat.st_mtime,
                "size": stat.st_size,
                "processes": self._get_file_processes(file_path),
            }

            changed = {
                "mtime": current["mtime"] != original["mtime"],
                "size": current["size"] != original["size"],
                "processes": set(current["processes"]) != set(original["processes"]),
            }

            return {
                "path": file_path,
                "changed": any(changed.values()),
                "details": changed,
                "current": current,
                "original": original,
            }
        except FileNotFoundError:
            return {"path": file_path, "changed": True, "error": "File deleted"}

    def _get_file_processes(self, file_path: str) -> List[int]:
        """Get PIDs of processes using this file"""
        pids = []
        try:
            import psutil

            for proc in psutil.process_iter(["pid", "open_files"]):
                try:
                    if proc.info["open_files"]:
                        for file in proc.info["open_files"]:
                            if file.path == file_path:
                                pids.append(proc.info["pid"])
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
        except Exception:
            pass
        return pids

    def notify_user(
        self, title: str, message: str, file_path: Optional[str] = None
    ) -> bool:
        """
        NOTIFY USER
        Try macOS notification, fallback to opening file in editor
        """
        try:
            # Try macOS osascript notification
            subprocess.run(
                [
                    "osascript",
                    "-e",
                    f'display notification "{message}" with title "{title}"',
                ],
                check=True,
                timeout=5,
            )
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            # Fallback: open file in editor
            if file_path:
                return self.open_in_editor(file_path) is not None
            return False

    def open_in_editor(self, file_path: str) -> Optional[Dict]:
        """Open file in TextEdit (macOS) or vim fallback"""
        try:
            stat = os.stat(file_path)
            original_mtime = stat.st_mtime
            original_size = stat.st_size

            # Try TextEdit first (macOS)
            try:
                subprocess.run(
                    ["open", "-a", "TextEdit", file_path], check=True, timeout=10
                )
                editor = "TextEdit"
            except (subprocess.CalledProcessError, FileNotFoundError):
                # Fallback to vim
                subprocess.run(["vim", file_path], check=True, timeout=10)
                editor = "vim"

            return {
                "status": "opened",
                "path": file_path,
                "editor": editor,
                "original_mtime": original_mtime,
                "original_size": original_size,
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}

    def _review_timeout_seconds(self) -> int:
        """Max seconds to wait for TextEdit review (env MCP_AUGMENT_REVIEW_TIMEOUT, 0 = wait)."""
        try:
            return int(
                os.environ.get(
                    "MCP_AUGMENT_REVIEW_TIMEOUT", str(_DEFAULT_REVIEW_TIMEOUT_S)
                )
            )
        except ValueError:
            return _DEFAULT_REVIEW_TIMEOUT_S

    def _is_review_file_open(self, filepath: str) -> bool:
        """Best-effort check: is this specific review file still open in TextEdit?"""
        try:
            basename = os.path.basename(filepath)
            script = """
on run argv
    set targetPath to item 1 of argv
    tell application "TextEdit"
        if not running then return "false"
        repeat with docRef in documents
            try
                if ((path of docRef) as text) is equal to ((POSIX file targetPath) as text) then
                    return "true"
                end if
            end try
        end repeat
    end tell
    return "false"
end run
"""
            result = subprocess.run(
                ["osascript", "-e", script, filepath],
                capture_output=True,
                text=True,
                timeout=5,
            )
            is_open = result.returncode == 0 and result.stdout.strip().lower() == "true"
            names_script = """
tell application "TextEdit"
    if not running then return "NOT_RUNNING"
    set docLines to ""
    repeat with docRef in documents
        try
            set docLines to docLines & ((name of docRef) as text) & linefeed
        on error errMsg
            set docLines to docLines & ("ERR:" & errMsg) & linefeed
        end try
    end repeat
    return docLines
end tell
"""
            names_result = subprocess.run(
                ["osascript", "-e", names_script],
                capture_output=True,
                text=True,
                timeout=5,
            )
            doc_names = [
                line.strip()
                for line in names_result.stdout.splitlines()
                if line.strip()
            ]
            basename_match = basename in doc_names
            return is_open or basename_match
        except (subprocess.SubprocessError, FileNotFoundError, OSError):
            try:
                return (
                    subprocess.run(
                        ["pgrep", "-f", "TextEdit"],
                        capture_output=True,
                        text=True,
                        timeout=5,
                    ).returncode
                    == 0
                )
            except (subprocess.SubprocessError, FileNotFoundError, OSError):
                return False

    @staticmethod
    def _read_review_file(filepath: str) -> str:
        """Read review file contents."""
        with open(filepath, "r", encoding="utf-8") as f:
            return f.read().strip()

    def _review_textedit_wait_for_edit(self, initial_text: str, timeout: int) -> str:
        """
        Open JSON in TextEdit, wait for the user, and only continue once they close the file.
        If the file was edited and closed with valid JSON, return it; otherwise use initial_text.
        """
        filepath: Optional[str] = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w+",
                delete=False,
                suffix=".mcp-augment-review.json",
                encoding="utf-8",
            ) as tmp:
                tmp.write(initial_text)
                tmp.flush()
                filepath = tmp.name

            try:
                json.loads(initial_text)
            except json.JSONDecodeError as e:
                self.log(f"review: invalid initial envelope JSON: {e}")
                return initial_text

            try:
                subprocess.run(
                    ["open", "-a", "TextEdit", filepath],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
            except (
                subprocess.CalledProcessError,
                subprocess.TimeoutExpired,
                FileNotFoundError,
            ) as e:
                self.log(f"review: TextEdit open failed: {e}")
                return initial_text

            last_mtime = os.path.getmtime(filepath)
            last_size = os.path.getsize(filepath)
            start = time.time()
            interval = 0.5
            saw_change = False
            loop_count = 0

            while True:
                time.sleep(interval)
                try:
                    loop_count += 1
                    cur_mtime = os.path.getmtime(filepath)
                    cur_size = os.path.getsize(filepath)
                    changed = cur_mtime != last_mtime or cur_size != last_size
                    if changed:
                        last_mtime = cur_mtime
                        last_size = cur_size
                        saw_change = True

                    if timeout > 0 and time.time() - start > timeout:
                        self.log(f"review: timeout {timeout}s reached")
                        break

                    file_open = self._is_review_file_open(filepath)
                    if not file_open and time.time() - start > 1:
                        break
                except OSError:
                    return initial_text

            if not saw_change:
                return initial_text

            edited = self._read_review_file(filepath)
            try:
                json.loads(edited)
                return edited
            except json.JSONDecodeError as e:
                self.log(f"review: edited JSON invalid: {e}")
                return initial_text
        finally:
            if filepath and os.path.exists(filepath):
                try:
                    os.unlink(filepath)
                except OSError:
                    pass

    @staticmethod
    def _extract_review_payload(
        data: Dict[str, Any], proposed_key: str
    ) -> Optional[Dict[str, Any]]:
        """Read the edited review payload from the review artifact."""
        output = data.get("OUTPUT")
        if isinstance(output, dict):
            proposed_value = output.get(proposed_key)
            if isinstance(proposed_value, dict):
                return proposed_value

        edit_here = data.get("EDIT_HERE")
        if isinstance(edit_here, dict):
            return edit_here

        return None

    def _show_native_applescript_dialog(
        self, title: str, instructions: str, proposed: Dict[str, Any]
    ) -> Optional[str]:
        """Shows the initial Accept/Decline/Edit dialog with current JSON."""
        try:
            preview = json.dumps(proposed, indent=2)
            preview_esc = preview.replace('"', '\\"').replace('\n', '\r')
            title_esc = title.replace('"', '\\"')
            instr_esc = instructions.replace('"', '\\"').replace('\n', '\r')
            script = f'display dialog "Review Required: {title_esc}" & return & return & "{instr_esc}" & return & return & "CURRENT PAYLOAD:" & return & "{preview_esc}" buttons {{"Decline", "Edit", "Accept"}} default button "Accept" with title "mcp-augment • Safety Shield" with icon note'
            result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=86400)
            if result.returncode != 0:
                self.log(f"dialog: osascript failed (rc={result.returncode}): {result.stderr}")
                return None
            output = result.stdout.strip()
            if "button returned:Accept" in output: return "ACCEPT"
            if "button returned:Decline" in output: return "DECLINE"
            if "button returned:Edit" in output: return "EDIT"
            return None
        except Exception as e:
            self.log(f"dialog: exception: {e}")
            return None

    def _show_native_field_picker(self, proposed: Dict[str, Any]) -> Optional[str]:
        """Shows a list of human-friendly choices for editing."""
        try:
            choices = ["✅ DONE (Finish Editing)"]
            for k, v in proposed.items():
                if k == "command" and isinstance(v, str):
                    parts = v.split(" ", 1)
                    prefix = parts[0]
                    args = parts[1].strip("'\"") if len(parts) > 1 else ""
                    # High-priority human labels
                    choices.append(f"1. EDIT MESSAGE: {args[:30]}...")
                    choices.append(f"2. EDIT COMMAND: {prefix}")
                elif k == "path":
                    choices.append(f"3. EDIT PATH: {v}")
                elif isinstance(v, str):
                    choices.append(f"Edit {k}: {v[:30]}...")
            
            choices.append("Manual JSON Editor (Advanced)")
            
            escaped_choices = [c.replace('"', '\\"') for c in choices]
            items_str = '", "'.join(escaped_choices)
            script = f'choose from list {{"{items_str}"}} with title "mcp-augment • Select Action" with prompt "Select a field to EDIT, or choose DONE to finish." default items {{"{escaped_choices[0]}"}} OK button name "Select" cancel button name "Cancel"'
            result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=86400)
            if result.returncode != 0:
                self.log(f"picker: osascript failed (rc={result.returncode}): {result.stderr}")
                return None
            sel = result.stdout.strip()
            if sel == "false" or not sel:
                return None
            return sel
        except Exception as e:
            self.log(f"picker: exception: {e}")
            return None

    def _show_native_edit_box(self, key: str, val: str) -> Optional[str]:
        """Shows a text box for a single value and returns the new string."""
        try:
            val_esc = val.replace('"', '\\"')
            key_esc = key.replace('"', '\\"')
            script = f'set res to display dialog "Edit {key_esc}:" default answer "{val_esc}" buttons {{"Cancel", "Save"}} default button "Save" with title "mcp-augment • Smart Edit"\nreturn text returned of res'
            result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=86400)
            if result.returncode == 0:
                return result.stdout.strip()
            self.log(f"edit_box: osascript failed (rc={result.returncode}): {result.stderr}")
            return None
        except Exception:
            return None

    def _run_review_envelope(
        self,
        phase: str,
        original: Dict[str, Any],
        proposed: Dict[str, Any],
        instructions: str,
        title: str = "Review Required",
    ) -> Dict[str, Any]:
        """Blueprint Review Flow (V14): Orchestrator -> Selection List -> Cancel/Edit/Done."""
        if phase not in ("tool_input", "tool_output"): return proposed
        proposed_key = "proposed_tool_input" if phase == "tool_input" else "proposed_tool_output"
        original_key = "original_tool_input" if phase == "tool_input" else "original_tool_output"
        
        envelope = {
            "INPUT": {original_key: original},
            "OUTPUT": {
                proposed_key: proposed,
                "instructions": f"EDIT ONLY OUTPUT.{proposed_key} below. Save and close to apply.",
            },
            "METADATA": {
                "title": title, 
                "phase": phase, 
                "details": instructions,
                "edit_field": f"OUTPUT.{proposed_key}"
            },
        }
        initial_envelope = json.dumps(envelope, indent=2, ensure_ascii=False)
        timeout = self._review_timeout_seconds()

        # Test hook check (crucial for automated headless tests)
        if self.review_interactive_fn:
            self.log("review: diverting to review_interactive_fn (headless test mode)")
            final_s = self.review_interactive_fn(initial_envelope)
            return self._process_textedit_result(final_s, proposed_key, proposed)

        # 1. FIRST WINDOW: Accept / Edit / Decline
        local_proposed = proposed.copy()
        while True:
            action = self._show_native_applescript_dialog(title, instructions, local_proposed)
            
            if action == "ACCEPT":
                return local_proposed
            elif action == "DECLINE":
                self.log("review: BLOCKED by user via native dialog")
                return {}
            elif action == "EDIT":
                # 2. SECOND WINDOW: Selection List
                while True:
                    sel = self._show_native_field_picker(local_proposed)
                    if not sel: 
                        # User hit Cancel in list -> Back to FIRST WINDOW
                        break
                    
                    if "DONE" in sel:
                        return local_proposed

                    # Go straight to edit box — no confirmation step
                    try:
                        if "Manual JSON" in sel:
                            final_s = self._review_textedit_wait_for_edit(initial_envelope, timeout)
                            return self._process_textedit_result(final_s, proposed_key, local_proposed)

                        if "MESSAGE" in sel:
                            cmd_val = local_proposed.get("command", "")
                            parts = cmd_val.split(" ", 1)
                            prefix = parts[0]
                            args = parts[1].strip("'\"") if len(parts) > 1 else ""
                            new_args = self._show_native_edit_box("Message Text", args)
                            if new_args is not None:
                                local_proposed["command"] = f"{prefix} '{new_args}'"
                        elif "COMMAND" in sel:
                            cmd_val = local_proposed.get("command", "")
                            parts = cmd_val.split(" ", 1)
                            prefix = parts[0]
                            args = parts[1] if len(parts) > 1 else ""
                            new_prefix = self._show_native_edit_box("Command Prefix", prefix)
                            if new_prefix is not None:
                                local_proposed["command"] = f"{new_prefix} {args}".strip()
                        else:
                            key_part = sel.split(":")[0]
                            key = key_part.split(" ")[-1].lower().rstrip(":")
                            if key in local_proposed:
                                new_val = self._show_native_edit_box(key.capitalize(), local_proposed[key])
                                if new_val is not None:
                                    local_proposed[key] = new_val
                            
                            # After edit box, loop back to SELECTION LIST
                            continue
                    except Exception as e:
                        # Fallback to TextEdit on any osascript failure
                        self.log(f"review: osascript failure ({e}), falling back to TextEdit.")
                        final_s = self._review_textedit_wait_for_edit(initial_envelope, timeout)
                        return self._process_textedit_result(final_s, proposed_key, local_proposed)
            else:
                # unknown button or failure -> TextEdit
                final_s = self._review_textedit_wait_for_edit(initial_envelope, timeout)
                return self._process_textedit_result(final_s, proposed_key, local_proposed)

    def _process_textedit_result(self, raw_s: str, key: str, fallback: Dict[str, Any]) -> Dict[str, Any]:
        """Processes raw TextEdit JSON and extracts the payload."""
        try:
            json_s = raw_s
            if "```json" in json_s: json_s = json_s.split("```json")[1].split("```")[0]
            data = json.loads(json_s)
            out = self._extract_review_payload(data, key)
            return out if isinstance(out, dict) else fallback
        except Exception: 
            return fallback

    @staticmethod
    def _merge_hook_response_json(
        output: Dict[str, Any], response: Dict[str, Any]
    ) -> None:
        """Copy optional two-way and review fields from parsed hook JSON into response dict."""
        if isinstance(output.get("modifiedInput"), dict):
            response["modifiedInput"] = output["modifiedInput"]
        if isinstance(output.get("modifiedOutput"), dict):
            response["modifiedOutput"] = output["modifiedOutput"]
        if isinstance(output.get("reviewInput"), dict):
            response["reviewInput"] = output["reviewInput"]
        if isinstance(output.get("reviewOutput"), dict):
            response["reviewOutput"] = output["reviewOutput"]
        if isinstance(output.get("reviewInstructions"), str):
            response["reviewInstructions"] = output["reviewInstructions"]
        if isinstance(output.get("reviewTitle"), str):
            response["reviewTitle"] = output["reviewTitle"]

    # MCP Protocol Methods

    def handle_initialize(self, id: int) -> Dict:
        """Handle initialize request"""
        return {
            "jsonrpc": "2.0",
            "id": id,
            "result": {
                "protocolVersion": "2024-11-05",
                "serverInfo": {
                    "name": "mcp-augment",
                    "version": "1.0.0",
                    "description": "MCP hook simulation for mode enforcement",
                },
                "capabilities": {"tools": {}},
            },
        }

    # ─── Proxy tools: validate via hooks THEN execute ───────────────

    def _validate_before_action(self, tool_name: str, tool_input: Dict) -> Dict:
        """Run PreToolUse hook chain. Returns validation result dict with 'blocked' key."""
        return self._handle_hook_event(
            {
                "event_name": "PreToolUse",
                "tool_name": tool_name,
                "tool_input": tool_input,
            }
        )

    def _post_tool_hook(
        self, tool_name: str, tool_input: Dict, tool_output: Dict
    ) -> Dict:
        """Run PostToolUse hook chain synchronously. Returns modifiedOutput dict (empty if none)."""
        result = self.execute_hook_chain(
            "PostToolUse",
            tool_name,
            {
                "hook_event_name": "PostToolUse",
                "tool_name": tool_name,
                "tool_input": tool_input,
                "tool_output": tool_output,
                "session_id": "kilo-session",
                "cwd": self.project_dir,
            },
            can_block=False,
            synchronous=True,
        )
        return result.get("modifiedOutput", {})

    def _safe_write(self, arguments: Dict) -> Dict:
        """Validate then write file. Returns blocked reason or write confirmation."""
        file_path = arguments.get("file_path", "")
        content = arguments.get("content", "")

        check = self._validate_before_action("Write", {"file_path": file_path})
        if check.get("blocked"):
            self.log(
                f"safe_write BLOCKED: {file_path} — {check.get('reason', 'no reason')}"
            )
            return {
                "blocked": True,
                "reason": check.get("reason", "Blocked by hook"),
                "file_path": file_path,
            }

        mi = check.get("modifiedInput")
        if isinstance(mi, dict):
            file_path = mi.get("file_path", file_path)
            content = mi.get("content", content)

        try:
            os.makedirs(os.path.dirname(os.path.abspath(file_path)), exist_ok=True)
            Path(file_path).write_text(content)
            self.log(f"safe_write OK: {file_path} ({len(content)} bytes)")
            result = {"blocked": False, "wrote": file_path, "bytes": len(content)}
        except Exception as e:
            self.log(f"safe_write ERROR: {file_path} — {e}")
            result = {"blocked": False, "error": str(e), "file_path": file_path}

        mo = self._post_tool_hook(
            "Write", {"file_path": file_path, "content": content}, result
        )
        if mo:
            result.update(mo)
        return result

    def _safe_edit(self, arguments: Dict) -> Dict:
        """Validate then edit file (find/replace). Returns blocked reason or edit confirmation."""
        file_path = arguments.get("file_path", "")
        old_string = arguments.get("old_string", "")
        new_string = arguments.get("new_string", "")

        check = self._validate_before_action("Edit", {"file_path": file_path})
        if check.get("blocked"):
            self.log(
                f"safe_edit BLOCKED: {file_path} — {check.get('reason', 'no reason')}"
            )
            return {
                "blocked": True,
                "reason": check.get("reason", "Blocked by hook"),
                "file_path": file_path,
            }

        mi = check.get("modifiedInput")
        if isinstance(mi, dict):
            file_path = mi.get("file_path", file_path)
            old_string = mi.get("old_string", old_string)
            new_string = mi.get("new_string", new_string)

        try:
            p = Path(file_path)
            if not p.exists():
                return {"blocked": False, "error": f"File not found: {file_path}"}
            text = p.read_text()
            count = text.count(old_string)
            if count == 0:
                return {
                    "blocked": False,
                    "error": "old_string not found in file",
                    "file_path": file_path,
                }
            if count > 1:
                return {
                    "blocked": False,
                    "error": f"old_string found {count} times — must be unique",
                    "file_path": file_path,
                }
            text = text.replace(old_string, new_string, 1)
            p.write_text(text)
            self.log(f"safe_edit OK: {file_path}")
            result = {"blocked": False, "edited": file_path, "replacements": 1}
        except Exception as e:
            self.log(f"safe_edit ERROR: {file_path} — {e}")
            result = {"blocked": False, "error": str(e), "file_path": file_path}

        mo = self._post_tool_hook("Edit", {"file_path": file_path}, result)
        if mo:
            result.update(mo)
        return result

    def _safe_bash(self, arguments: Dict) -> Dict:
        """Validate then execute bash command. Returns blocked reason or command output."""
        command = arguments.get("command", "")
        timeout = arguments.get("timeout", 30)

        check = self._validate_before_action("Bash", {"command": command})
        if check.get("blocked"):
            self.log(
                f"safe_bash BLOCKED: {command[:80]} — {check.get('reason', 'no reason')}"
            )
            return {
                "blocked": True,
                "reason": check.get("reason", "Blocked by hook"),
                "command": command,
            }

        mi = check.get("modifiedInput")
        if isinstance(mi, dict):
            command = mi.get("command", command)
            timeout = mi.get("timeout", timeout)

        try:
            proc = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=self.project_dir,
            )
            self.log(f"safe_bash OK: {command[:80]} (exit {proc.returncode})")
            result = {
                "blocked": False,
                "exit_code": proc.returncode,
                "stdout": (
                    proc.stdout[-4000:] if len(proc.stdout) > 4000 else proc.stdout
                ),
                "stderr": (
                    proc.stderr[-2000:] if len(proc.stderr) > 2000 else proc.stderr
                ),
            }
        except subprocess.TimeoutExpired:
            result = {
                "blocked": False,
                "error": f"Command timed out after {timeout}s",
                "command": command,
            }
        except Exception as e:
            self.log(f"safe_bash ERROR: {e}")
            result = {"blocked": False, "error": str(e), "command": command}

        mo = self._post_tool_hook("Bash", {"command": command}, result)
        if mo:
            result.update(mo)
        return result

    def _safe_read(self, arguments: Dict) -> Dict:
        """Validate then read file. Returns blocked reason or file content."""
        file_path = arguments.get("file_path", "")

        check = self._validate_before_action("Read", {"file_path": file_path})
        if check.get("blocked"):
            self.log(
                f"safe_read BLOCKED: {file_path} — {check.get('reason', 'no reason')}"
            )
            return {
                "blocked": True,
                "reason": check.get("reason", "Blocked by hook"),
                "file_path": file_path,
            }

        mi = check.get("modifiedInput")
        if isinstance(mi, dict):
            file_path = mi.get("file_path", file_path)

        try:
            content = Path(file_path).read_text()
            self.log(f"safe_read OK: {file_path} ({len(content)} bytes)")
            result = {"blocked": False, "file_path": file_path, "content": content}
        except Exception as e:
            self.log(f"safe_read ERROR: {file_path} — {e}")
            result = {"blocked": False, "error": str(e), "file_path": file_path}

        mo = self._post_tool_hook("Read", {"file_path": file_path}, result)
        if mo:
            result.update(mo)
        return result

    def _safe_delete(self, arguments: Dict) -> Dict:
        """Validate then delete file. Returns blocked reason or delete confirmation."""
        file_path = arguments.get("file_path", "")

        check = self._validate_before_action("delete_file", {"file_path": file_path})
        if check.get("blocked"):
            self.log(
                f"safe_delete BLOCKED: {file_path} — {check.get('reason', 'no reason')}"
            )
            return {
                "blocked": True,
                "reason": check.get("reason", "Blocked by hook"),
                "file_path": file_path,
            }

        mi = check.get("modifiedInput")
        if isinstance(mi, dict):
            file_path = mi.get("file_path", file_path)

        try:
            Path(file_path).unlink()
            self.log(f"safe_delete OK: {file_path}")
            result = {"blocked": False, "deleted": file_path}
        except Exception as e:
            self.log(f"safe_delete ERROR: {file_path} — {e}")
            result = {"blocked": False, "error": str(e), "file_path": file_path}

        mo = self._post_tool_hook("delete_file", {"file_path": file_path}, result)
        if mo:
            result.update(mo)
        return result

    def _manage_hook(self, arguments: Dict) -> Dict:
        """Add, remove, or list hooks in config.yaml via yq."""
        action = arguments.get("action", "")

        if action == "list":
            config = self.load_hooks_config()
            hooks = config.get("hooks", {})
            summary = {}
            for event, entries in hooks.items():
                summary[event] = []
                for entry in entries:
                    matcher = entry.get("matcher", "*")
                    for h in entry.get("hooks", []):
                        summary[event].append(
                            {
                                "matcher": matcher,
                                "type": h.get("type", "command"),
                                "command": h.get("command", h.get("url", "")),
                                "timeout": h.get("timeout", 30),
                            }
                        )
            return {"hooks": summary}

        # Required for add/remove
        event_name = arguments.get("event_name", "")
        command = arguments.get("command", "")

        if not event_name or not command:
            return {"error": "event_name and command are required for add/remove"}

        config_file = HOOKS_CONFIG_FILE
        yq = self._find_yq()

        if action == "add":
            matcher = arguments.get("matcher", ".*")
            hook_type = arguments.get("hook_type", "command")
            timeout = arguments.get("timeout", 10)

            hook_obj = {"type": hook_type, "command": command, "timeout": timeout}
            new_entry = {"matcher": matcher, "hooks": [hook_obj]}
            entry_json = json.dumps(new_entry)

            expr = f".hooks.{event_name} += [{entry_json}]"
            result = subprocess.run(
                [yq, "-i", expr, config_file],
                capture_output=True,
                text=True,
                timeout=5,
                cwd=self.project_dir,
            )
            if result.returncode != 0:
                return {"error": f"yq failed: {result.stderr}"}

            self._config_loaded = False
            self._cached_config = None
            self.log(f"manage_hook ADD: {event_name} matcher={matcher} cmd={command}")
            return {
                "added": True,
                "event": event_name,
                "matcher": matcher,
                "command": command,
            }

        elif action == "remove":
            expr = (
                f'del(.hooks.{event_name}[] | select(.hooks[].command == "{command}"))'
            )
            result = subprocess.run(
                [yq, "-i", expr, config_file],
                capture_output=True,
                text=True,
                timeout=5,
                cwd=self.project_dir,
            )
            if result.returncode != 0:
                return {"error": f"yq failed: {result.stderr}"}

            self._config_loaded = False
            self._cached_config = None
            self.log(f"manage_hook REMOVE: {event_name} cmd={command}")
            return {"removed": True, "event": event_name, "command": command}

        else:
            return {"error": f"Unknown action: {action}. Use add, remove, or list."}

    # ─── End proxy tools ──────────────────────────────────────────

    def handle_tools_list(self, id: int) -> Dict:
        """Handle tools/list request"""
        return {
            "jsonrpc": "2.0",
            "id": id,
            "result": {
                "tools": [
                    {
                        "name": "pre_validate",
                        "description": "MANDATORY pre-operation check. Call before any file write, edit, bash command, or delete to simulate the PreToolUse hook chain. Returns ALLOWED or BLOCKED with reason. Use this to validate a single operation before executing it.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "action": {
                                    "type": "string",
                                    "description": "Action to validate (edit, write, delete, etc.)",
                                },
                                "path": {
                                    "type": "string",
                                    "description": "File path to operate on",
                                },
                            },
                            "required": ["action", "path"],
                        },
                    },
                    {
                        "name": "batch_validate",
                        "description": "MANDATORY batch pre-operation check. Call to validate multiple file or command operations in a single request before executing them. Returns per-operation ALLOWED or BLOCKED results. More efficient than multiple pre_validate calls.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "operations": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "action": {"type": "string"},
                                            "path": {"type": "string"},
                                        },
                                    },
                                }
                            },
                            "required": ["operations"],
                        },
                    },
                    {
                        "name": "start_file_monitor",
                        "description": "Begin watching a file for changes by recording its current mtime, size, and optionally the writing process. Use before an operation that should modify a file, then call check_file_changed to confirm the change occurred.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "file_path": {
                                    "type": "string",
                                    "description": "Absolute path to monitor",
                                }
                            },
                            "required": ["file_path"],
                        },
                    },
                    {
                        "name": "check_file_changed",
                        "description": "Check whether a previously monitored file has changed since start_file_monitor was called. Returns changed/unchanged status with mtime and size delta. Use after an operation that was expected to modify a file.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"file_path": {"type": "string"}},
                            "required": ["file_path"],
                        },
                    },
                    {
                        "name": "notify_user",
                        "description": "Send a macOS notification or advisory message to the user. Use for non-blocking oversight alerts — when an operation completed and the user should be informed but no action is required. Does not block or pause execution.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string"},
                                "message": {"type": "string"},
                                "file_path": {"type": "string"},
                            },
                            "required": ["title", "message"],
                        },
                    },
                    {
                        "name": "open_in_editor",
                        "description": "Open a file in the user's editor (TextEdit on macOS, vim fallback). Use when the user needs to manually review or edit a file as part of a human-in-the-loop workflow. Execution continues after the file is opened.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "file_path": {
                                    "type": "string",
                                    "description": "Absolute path to file to open",
                                },
                            },
                            "required": ["file_path"],
                        },
                    },
                    {
                        "name": "hook_event",
                        "description": "Fire any Claude Code hook event (PreToolUse, PostToolUse, SessionStart, etc.). Returns ALLOWED or BLOCKED with reason.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "event_name": {
                                    "type": "string",
                                    "description": "Hook event: PreToolUse, PostToolUse, PermissionRequest, SessionStart, SessionEnd, Stop, UserPromptSubmit, etc.",
                                    "enum": [
                                        "PreToolUse",
                                        "PostToolUse",
                                        "PostToolUseFailure",
                                        "PermissionRequest",
                                        "SessionStart",
                                        "SessionEnd",
                                        "Stop",
                                        "StopFailure",
                                        "UserPromptSubmit",
                                        "SubagentStart",
                                        "SubagentStop",
                                        "PreCompact",
                                        "PostCompact",
                                        "ConfigChange",
                                        "Notification",
                                        "InstructionsLoaded",
                                        "Setup",
                                        "TeammateIdle",
                                        "TaskCompleted",
                                        "WorktreeCreate",
                                        "WorktreeRemove",
                                        "Elicitation",
                                        "ElicitationResult",
                                    ],
                                },
                                "tool_name": {
                                    "type": "string",
                                    "description": "Tool being invoked (Edit, Write, Bash, Read, etc.)",
                                },
                                "tool_input": {
                                    "type": "object",
                                    "description": "Tool input parameters (file_path, command, etc.)",
                                },
                                "tool_output": {
                                    "type": "object",
                                    "description": "Tool output (only for PostToolUse events)",
                                },
                            },
                            "required": ["event_name", "tool_name", "tool_input"],
                        },
                    },
                    {
                        "name": "get_hooks_config",
                        "description": "Return the current hooks configuration from the project's hooks config file. Shows all registered hook handlers, matchers, timeouts, and settings. Use to inspect or debug what hooks are active for this project.",
                        "inputSchema": {"type": "object", "properties": {}},
                    },
                    {
                        "name": "safe_write",
                        "description": "MANDATORY file write tool. ALL file writes MUST go through this tool. Validates path against security hooks (blocks .env, credentials, secrets), then writes if allowed. Returns {blocked: true, reason: ...} or {blocked: false, wrote: path, bytes: N}. ALWAYS use this instead of native Write/write_to_file.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "file_path": {
                                    "type": "string",
                                    "description": "Absolute path to write to",
                                },
                                "content": {
                                    "type": "string",
                                    "description": "File content to write",
                                },
                            },
                            "required": ["file_path", "content"],
                        },
                    },
                    {
                        "name": "safe_edit",
                        "description": "MANDATORY file edit tool. ALL file edits MUST go through this tool. Validates path against security hooks, then performs find-replace if allowed. Returns {blocked: true, reason: ...} or {blocked: false, edited: path}. ALWAYS use this instead of native Edit/apply_diff.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "file_path": {
                                    "type": "string",
                                    "description": "Absolute path to edit",
                                },
                                "old_string": {
                                    "type": "string",
                                    "description": "Exact string to find and replace",
                                },
                                "new_string": {
                                    "type": "string",
                                    "description": "Replacement string",
                                },
                            },
                            "required": ["file_path", "old_string", "new_string"],
                        },
                    },
                    {
                        "name": "safe_bash",
                        "description": "MANDATORY command execution tool. ALL bash/shell commands MUST go through this tool. Validates command against security hooks (blocks rm -rf, sudo, force-push), then executes if allowed. Returns {blocked: true, reason: ...} or {blocked: false, exit_code: N, stdout: ..., stderr: ...}. ALWAYS use this instead of native Bash/execute_command.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "command": {
                                    "type": "string",
                                    "description": "Bash command to execute",
                                },
                                "timeout": {
                                    "type": "integer",
                                    "description": "Timeout in seconds (default 30)",
                                },
                            },
                            "required": ["command"],
                        },
                    },
                    {
                        "name": "safe_read",
                        "description": "MANDATORY file read tool. ALL file reads MUST go through this tool. Validates path against security hooks, then reads content if allowed. Returns {blocked: true, reason: ...} or {blocked: false, content: ...}. ALWAYS use this instead of native Read/read_file.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "file_path": {
                                    "type": "string",
                                    "description": "Absolute path to read",
                                },
                            },
                            "required": ["file_path"],
                        },
                    },
                    {
                        "name": "safe_delete",
                        "description": "MANDATORY file deletion tool. ALL file deletions MUST go through safe_delete. NEVER use native delete_file directly. Validates path against security hooks (blocks .env, credentials, protected files) before deleting. Returns {blocked: true, reason} or {blocked: false, deleted: path}.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "file_path": {
                                    "type": "string",
                                    "description": "Path to file to delete",
                                },
                            },
                            "required": ["file_path"],
                        },
                    },
                    {
                        "name": "manage_hook",
                        "description": "Add, remove, or list hooks in the config. Actions: 'list' (show all registered hooks), 'add' (register a hook script for an event), 'remove' (unregister a hook by command path). Hooks added here fire for ALL connected AI tools. Server restart may be needed for changes to take effect.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "action": {
                                    "type": "string",
                                    "enum": ["add", "remove", "list"],
                                    "description": "add = register hook, remove = unregister, list = show all",
                                },
                                "event_name": {
                                    "type": "string",
                                    "enum": [
                                        "PreToolUse",
                                        "PostToolUse",
                                        "PermissionRequest",
                                        "SessionStart",
                                        "SessionEnd",
                                        "Stop",
                                        "UserPromptSubmit",
                                    ],
                                    "description": "Hook event (required for add/remove)",
                                },
                                "matcher": {
                                    "type": "string",
                                    "description": "Pipe-separated tool regex, e.g. 'Edit|Write'. Default '.*' (all tools). For add only.",
                                },
                                "command": {
                                    "type": "string",
                                    "description": "Path to hook script, e.g. '.kilo/hooks/my-hook.sh'. Required for add/remove.",
                                },
                                "hook_type": {
                                    "type": "string",
                                    "enum": ["command", "http"],
                                    "description": "Handler type (default: command). For add only.",
                                },
                                "timeout": {
                                    "type": "number",
                                    "description": "Timeout in seconds (default: 10). For add only.",
                                },
                            },
                            "required": ["action"],
                        },
                    },
                ]
            },
        }

    def handle_tool_call(self, id: int, tool_name: str, arguments: Dict) -> Dict:
        """Handle tool invocation"""
        self.log(f"Tool called: {tool_name} with args: {arguments}")

        result = {}

        if tool_name == "pre_validate":
            action = arguments.get("action", "")
            path = arguments.get("path", "")
            validation = self.pre_validate(action, path)
            result = validation.to_dict()

        elif tool_name == "batch_validate":
            operations = arguments.get("operations", [])
            validations = self.batch_validate(operations)
            result = {"results": [v.to_dict() for v in validations]}

        elif tool_name == "start_file_monitor":
            file_path = arguments.get("file_path", "")
            result = self.start_file_monitor(file_path)

        elif tool_name == "check_file_changed":
            file_path = arguments.get("file_path", "")
            result = self.check_file_changed(file_path)

        elif tool_name == "notify_user":
            title = arguments.get("title", "")
            message = arguments.get("message", "")
            file_path = arguments.get("file_path")
            success = self.notify_user(title, message, file_path)
            result = {"notified": success}

        elif tool_name == "hook_event":
            result = self._handle_hook_event(arguments)

        elif tool_name == "get_hooks_config":
            result = self._handle_get_hooks_config()

        elif tool_name == "safe_write":
            result = self._safe_write(arguments)

        elif tool_name == "safe_edit":
            result = self._safe_edit(arguments)

        elif tool_name == "safe_bash":
            result = self._safe_bash(arguments)

        elif tool_name == "safe_read":
            result = self._safe_read(arguments)

        elif tool_name == "safe_delete":
            result = self._safe_delete(arguments)

        elif tool_name == "open_in_editor":
            file_path = arguments.get("file_path", "")
            result = self.open_in_editor(file_path)

        elif tool_name == "manage_hook":
            result = self._manage_hook(arguments)

        else:
            result = {"error": f"Unknown tool: {tool_name}"}

        return {
            "jsonrpc": "2.0",
            "id": id,
            "result": {
                "content": [{"type": "text", "text": json.dumps(result, indent=2)}]
            },
        }

    def main_loop(self):
        """Main MCP server loop"""
        self.log("Kilo Hooks MCP Server starting")

        while True:
            try:
                # Read Content-Length header
                header_line = sys.stdin.readline()
                if not header_line:
                    break

                if header_line.startswith("Content-Length:"):
                    content_length = int(header_line.split(":")[1].strip())

                    # Read blank line
                    sys.stdin.readline()

                    # Read message body
                    body = sys.stdin.read(content_length)
                    request = json.loads(body)

                    self.log(f"Received: {request}")

                    method = request.get("method")
                    req_id = request.get("id")

                    if method == "initialize":
                        self.send_response(self.handle_initialize(req_id))
                    elif method == "tools/list":
                        self.send_response(self.handle_tools_list(req_id))
                    elif method == "tools/call":
                        params = request.get("params", {})
                        tool_name = params.get("name", "")
                        arguments = params.get("arguments", {})
                        self.send_response(
                            self.handle_tool_call(req_id, tool_name, arguments)
                        )
                    elif method and method.startswith("notifications/"):
                        self.log(f"Received notification: {method}")
                    else:
                        self.send_response(
                            {
                                "jsonrpc": "2.0",
                                "id": req_id,
                                "error": {
                                    "code": -32601,
                                    "message": f"Method not found: {method}",
                                },
                            }
                        )

            except Exception as e:
                self.log(f"Error: {e}")
                import traceback

                self.log(traceback.format_exc())


if __name__ == "__main__":
    import traceback as _tb

    _proj = os.environ.get("PROJECT_DIR", str(Path(__file__).resolve().parents[2]))
    _log_paths = [
        os.path.join(_proj, ".claude/logs/mcp-augment-startup.log"),
        os.path.join(_proj, ".kilo/logs/mcp-augment-startup.log"),
    ]
    for _lp in _log_paths:
        os.makedirs(os.path.dirname(_lp), exist_ok=True)

    def _startup_log(msg: str):
        for _lp in _log_paths:
            with open(_lp, "a") as _f:
                _f.write(msg)

    try:
        _startup_log(
            f"[{datetime.now().isoformat()}] Starting server\n"
            f"  Python: {sys.executable} {sys.version}\n"
            f"  CWD: {os.getcwd()}\n"
            f"  PATH: {os.environ.get('PATH', 'N/A')}\n"
            f"  PROJECT_DIR: {os.environ.get('PROJECT_DIR', 'N/A')}\n"
        )
        server = MCAugmentMCP()
        _startup_log(
            f"[{datetime.now().isoformat()}] Init complete, entering main loop\n"
        )
        server.main_loop()
    except Exception as _e:
        _startup_log(
            f"[{datetime.now().isoformat()}] CRASH: {_e}\n" + _tb.format_exc() + "\n"
        )
        sys.exit(1)
