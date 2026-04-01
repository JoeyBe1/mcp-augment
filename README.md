# mcp-augment

**Add safety hooks to AI coding tools that have none.**

---

## What's in this repository

| Path | Role |
|------|------|
| `project-tools/mcp-hooks-server/` | MCP server and hook engine — **this is what you run and ship.** |
| `.kilo/hooks/` | Default hook scripts and `config.yaml` (portable; same shape as Claude Code hooks). |
| `tests/` | Automated checks for hooks and MCP behavior. |
| `analysis/` | **Local development notes only.** Not required to install, run, or publish; omitted from the PyPI package via `pyproject.toml`. Safe to keep in your private clone; it is **not** part of the public release story. |
| Legacy autoresearch MCP (`server.py` / `server-http.py`) | **Not published** on GitHub in this repo’s release policy—keep those files only on your machine if you still use them. `mcp-augment` on port **8200** is sufficient for hooks, modes, and `get_current_mode`. |

---

## The Problem

Your AI coding assistant can delete files, expose secrets, and run destructive commands with no guardrails. Claude Code has hooks to prevent this. Cursor, Windsurf, Kilo Code, Aider, and Cline do not.

## The Solution

This MCP server exposes **enforced proxy tools** (`safe_write`, `safe_edit`, `safe_bash`, `safe_read`, `safe_delete`) that validate every operation through a configurable hook chain before executing. Connect it to any MCP-compatible AI coding tool and it adds a safer tool layer the client did not natively have.

This is not just "safer tools." It is a portable **capability injection + tool-call correction + enforcement layer**:

- add safer replacement tools to clients that do not have them
- fix bad inputs before a tool runs
- clean or transform bad outputs before they reach the model
- keep behavior more deterministic and predictable than prompting alone
- let the user step in with hooks, config, and manual oversight when needed

---

## Demo

```
$ kilo  # launch Kilo Code CLI with mcp-augment connected

Agent> safe_write .env "API_KEY=sk-..."
=> BLOCKED: Protected file (.env matches sensitive file pattern)

Agent> safe_write src/app.py "print('hello')"
=> ALLOWED: wrote src/app.py (16 bytes)

Agent> safe_delete .env
=> BLOCKED: Protected file

Agent> safe_bash "rm -rf /"
=> BLOCKED: Destructive command detected
```

---

## Quick Start

### 1. Clone and install

```bash
git clone https://github.com/JoeyBe1/mcp-augment.git
cd mcp-augment
python3 -m venv .venv && source .venv/bin/activate
pip install mcp
```

### 2. Start the server

```bash
# Stdio mode (for MCP clients that support it):
python3 project-tools/mcp-hooks-server/mcp-augment.py

# HTTP mode (for Kilo Code CLI and other HTTP-based clients):
./project-tools/mcp-hooks-server/start-servers.sh
# Hooks MCP on port 8200 only (public release)
```

### 3. Connect your AI coding tool

**Kilo Code CLI** — `~/.config/kilo/opencode.json`

> Note: Kilo CLI reads from this global path. Project-level `.kilo/kilo.json` is ignored.

```json
{
  "mcp": {
    "mcp-augment": {
      "type": "remote",
      "url": "http://localhost:8200/mcp",
      "enabled": true
    }
  }
}
```

**Claude Code** — `.claude/settings.json`

```json
{
  "mcpServers": {
    "mcp-augment": {
      "command": "python3",
      "args": ["-u", "project-tools/mcp-hooks-server/mcp-augment.py"]
    }
  }
}
```

**Cursor** — `~/.cursor/mcp.json` (verified on macOS, 2026-04-01)

```json
{
  "mcpServers": {
    "mcp-augment": {
      "command": "python3",
      "args": [
        "-u",
        "${workspaceFolder}/project-tools/mcp-hooks-server/mcp-augment-http.py",
        "--stdio"
      ],
      "env": {
        "PROJECT_DIR": "${workspaceFolder}"
      }
    }
  }
}
```

> Cursor note: the setup that was verified live uses the global Cursor config at
> `~/.cursor/mcp.json` and launches `mcp-augment-http.py --stdio`. In this
> environment, project-level `.cursor/mcp.json` did not instantiate reliably.

**Windsurf** — `.codeium/windsurf/mcp_config.json`

```json
{
  "mcpServers": {
    "mcp-augment": {
      "command": "python3",
      "args": ["-u", "./project-tools/mcp-hooks-server/mcp-augment.py"]
    }
  }
}
```

**Cline** — VS Code settings → Cline MCP Servers

```json
{
  "mcp-augment": {
    "command": "python3",
    "args": ["-u", "./project-tools/mcp-hooks-server/mcp-augment.py"],
    "disabled": false
  }
}
```

**Aider** — HTTP mode (Aider supports MCP via HTTP):

```bash
./project-tools/mcp-hooks-server/start-servers.sh
# Then in aider: /mcp add http://localhost:8200/mcp
```

> All harnesses share the same hook scripts (`.kilo/hooks/*.sh`). The scripts are
> portable — they use the same stdin JSON format as Claude Code's native hook system.
> If a hook works in Claude Code, it works here.

---

## The Pattern: Injection + Interception

mcp-augment does two things that existing MCP solutions do not:

**1. Capability Injection** — Creates enforced tool versions that don't exist in the host tool.
`safe_write`, `safe_edit`, `safe_bash`, `safe_read`, `safe_delete` replace the native equivalents.
Validation and execution are atomic in a single MCP call. The agent cannot validate then bypass.

**2. Tool Call Interception** — Operates at the semantic layer, before and after execution.
This pattern originated from fixing bad tool calls from weaker models: a model leaves a trailing
comma in JSON, you rewrite the input before it hits the tool. A web search uses last year's date,
you correct the query before it executes. Then, if the output is noisy, poisoned, malformed, or
just inconvenient, you can transform it before it gets back to the model. The pre-validate and
post-execute hooks run at this layer, giving you full control over what actually reaches the tool
and what comes back.

This is different from MCP gateways (Latch, Bifrost, mcproxy) which intercept at the transport
layer between client and existing servers. And different from Claude Code's native hooks which
only work inside Claude Code. mcp-augment operates at the tool execution layer and works in any
MCP-compatible host, with any model.

```
AI Coding Tool (Kilo, Cursor, Windsurf, Cline, Aider, Claude Code...)
    │
    │ MCP protocol (stdio or HTTP)
    │
    ▼
┌──────────────────────────────────────────────────┐
│                 mcp-augment                      │
│                                                  │
│  [PreToolUse hooks run here — can mutate input]  │
│                                                  │
│  safe_write  ──► hook chain ──► write file       │
│  safe_edit   ──► hook chain ──► edit file        │
│  safe_bash   ──► hook chain ──► execute command  │
│  safe_read   ──► hook chain ──► read file        │
│  safe_delete ──► hook chain ──► delete file      │
│                                                  │
│  [PostToolUse hooks run here — can act on output]│
│                                                  │
│  Atomic: validate THEN execute in one call       │
│  Hook chain: config.yaml ──► *.sh scripts        │
└──────────────────────────────────────────────────┘
    │
    ▼
Shell hooks (.kilo/hooks/*.sh) — portable, same format as Claude Code native hooks
  block-sensitive-files.sh  ← blocks .env, credentials, secrets
  validate-bash-command.sh  ← blocks destructive commands, sudo, force-push
  mode-enforcement.sh       ← research/optimize/benchmark/eval modes
  auto-approve-safe.sh      ← auto-approve git status, ls, cat
  auto-format.sh            ← post-edit formatting (async)
  inject-git-context.sh     ← session start context injection
```

---

## 17 Available Tools

| Tool                   | Type              | Purpose                                                                              |
| ---------------------- | ----------------- | ------------------------------------------------------------------------------------ |
| `safe_write`           | Proxy (mandatory) | Validate then write file                                                             |
| `safe_edit`            | Proxy (mandatory) | Validate then edit file                                                              |
| `safe_bash`            | Proxy (mandatory) | Validate then execute command                                                        |
| `safe_read`            | Proxy (mandatory) | Validate then read file                                                              |
| `safe_delete`          | Proxy (mandatory) | Validate then delete file                                                            |
| `hook_event`           | Core              | Fire hook chain for any event                                                        |
| `pre_validate`         | Core              | Pre-operation validation                                                             |
| `batch_validate`       | Core              | Validate multiple operations                                                         |
| `get_hooks_config`     | Config            | View current hook configuration                                                      |
| `get_current_mode`     | Mode              | Get active mode (research/optimize/etc.)                                             |
| `list_available_modes` | Mode              | List all available modes                                                             |
| `start_file_monitor`   | Monitor           | Watch file for changes                                                               |
| `check_file_changed`   | Monitor           | Check if monitored file changed                                                      |
| `notify_user`          | Utility           | Show macOS notification                                                              |
| `open_in_editor`       | Utility           | Open file in TextEdit/vim                                                            |
| `manage_hook`          | Config            | Add, remove, or list hooks at runtime                                                |
| `validate_hook`        | Validation        | Check a hook script for compliance (exists, executable, bash syntax, stdin, silence) |

---

## How It Differs

| Capability                           | MCP Gateways (Latch, Bifrost) | Claude Code Hooks (Captain Hook) | mcp-augment                      |
| ------------------------------------ | ----------------------------- | -------------------------------- | -------------------------------- |
| Works without native client hooks    | No                            | No                               | **Yes**                          |
| Atomic validate-then-execute         | No (two steps)                | No (two steps)                   | **Yes**                          |
| Portable across AI clients           | Partially                     | No (Claude only)                 | **Yes**                          |
| Pre-tool input interception/mutation | No                            | No                               | **Yes**                          |
| Post-tool output hooks               | Transport only                | Yes (Claude only)                | **Yes, any client**              |
| Mode enforcement                     | No                            | No                               | **Yes**                          |
| Hook script validation built-in      | No                            | No                               | **Yes**                          |
| Configurable handler types           | No                            | Shell only                       | **command, http, prompt, agent** |

What this means in plain terms:

- `mcp-augment` is not a transport gateway. It creates new enforced tools like `safe_write` and `safe_bash`.
- `mcp-augment` is not the same thing as Claude Code hooks. Claude has native hooks; `mcp-augment` brings a hook-enforced tool layer to clients that do not.
- `mcp-augment` is still soft enforcement at the host level. The model has to use `safe_*` tools instead of bypassing them with native tools. The strong tool descriptions help in practice, but this is not kernel-level sandboxing.
- `mcp-augment` is a good demo of how to make MCP systems easier to trust: when the model botches the call before or after execution, the system can correct it, and the user can still step in with hooks and oversight.

Today, the clearest product statement is:

`mcp-augment` is a portable MCP-delivered pre/post tool-call correction and enforcement layer for AI clients without native hooks.

## Correction Modes Today

- **Auto-correction (shipped)**: `PreToolUse` hooks can emit `modifiedInput`, the tool runs with the corrected arguments, then synchronous `PostToolUse` hooks can emit `modifiedOutput` before the result reaches the model. This is the default `demo_search_backend` demo when no user edit is needed.
- **Advisory oversight (shipped)**: `notify_user` and `open_in_editor` can alert the user or hand a file over for review. This is only a notification / handoff layer. It does not by itself merge the user's edits back into the same `safe_*` call.
- **Collaborative user review-resume (shipped, macOS TextEdit)**: this is the real same-call human-in-the-loop path. Hooks return `reviewInput` (pre) or `reviewOutput` (post) plus optional `reviewTitle` / `reviewInstructions`. The engine writes a JSON review file in the older `INPUT` / `OUTPUT` / `METADATA` layout, opens it in TextEdit, waits for the user to save and close it, then resumes the same tool call using the edited `OUTPUT.proposed_tool_input` or `OUTPUT.proposed_tool_output`. Invalid or abandoned edits still fall back to the hook-proposed dict. By default it waits indefinitely; set `MCP_AUGMENT_REVIEW_TIMEOUT` to a positive number if you want a forced timeout, or `MCP_AUGMENT_SKIP_REVIEW=1` to accept the proposal without opening an editor. This flow works live, but the current TextEdit UX is still rough and not yet polished for human editing. Tests use `KiloHooksMCP.review_interactive_fn` to inject edits without TextEdit.

In other words: there is no separate generic "manual mode" flag in the engine. The shipped collaborative/manual behavior is the `reviewInput` / `reviewOutput` review-resume path.

### Working two-way demo (auto-correction)

From the repository root (where `pyproject.toml` lives), run this through `safe_bash` in Cursor:

```bash
python3 project-tools/mcp-hooks-server/demo_search_backend.py --query "mcp augment release date 2025"
```

Expected live result after the `mcp-augment` MCP server has been restarted:

- pre-hook rewrites `2025` -> `2026`
- backend runs with corrected query
- post-hook removes `INTERNAL_DEBUG`
- post-hook prepends `[POST-HOOK FILTERED]`
- optional notification hook alerts the user that output was post-processed

### User-review-resume demo (same call, TextEdit)

Use the same backend but include the substring `REVIEW_DEMO` in the **shell command** so the auto pre/post demo hooks skip and the review hooks run instead:

```bash
python3 project-tools/mcp-hooks-server/demo_search_backend.py --query "mcp augment release date REVIEW_DEMO 2025"
```

Flow:

1. `pre-review-search-query.sh` emits `reviewInput` (proposed command with `2026`). TextEdit opens and waits for the user; save and close to continue.
2. `post-shape-search-output.sh` is skipped when `REVIEW_DEMO` is present.
3. `post-review-search-output.sh` emits `reviewOutput` for stdout shaping; another TextEdit pass waits for the user the same way.
4. Final stdout matches the auto demo when proposals are accepted.

Verified behavior: the two TextEdit review windows are sequential. The second review does not open until the first review file has been saved/closed and merged back into the same `safe_bash` call.

---

## Hook Configuration

Hooks are configured in `.kilo/hooks/config.yaml`:

```yaml
hooks:
  PreToolUse:
    - matcher: "Edit|Write|MultiEdit|delete_file"
      hooks:
        - type: command
          command: ".kilo/hooks/block-sensitive-files.sh"
          timeout: 10
        - type: command
          command: ".kilo/hooks/mode-enforcement.sh"
          timeout: 10

    - matcher: "Bash"
      hooks:
        - type: command
          command: ".kilo/hooks/validate-bash-command.sh"
          timeout: 10

  PostToolUse:
    - matcher: "Write|Edit|MultiEdit"
      hooks:
        - type: command
          command: ".kilo/hooks/auto-format.sh"
          async: true
```

### Writing Custom Hooks

Hooks are shell scripts that:

1. Read JSON from stdin (`tool_name`, `tool_input`, `cwd`)
2. Exit 0 to allow, exit 2 to block
3. Optionally output JSON with `permissionDecisionReason`, `modifiedInput`, `modifiedOutput`, or review-resume fields (`reviewInput`, `reviewOutput`, `reviewTitle`, `reviewInstructions`)

```bash
#!/bin/bash
INPUT=$(cat)
FILE=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')

if [[ "$FILE" == *.env* ]]; then
  echo '{"hookSpecificOutput":{"permissionDecision":"deny","permissionDecisionReason":"Protected file"}}'
  exit 2
fi
exit 0
```

These hooks are **portable** -- the same script works in Claude Code's native hook system AND through mcp-augment in any other tool.

### What you can extend today

- **Custom hooks**: yes — write shell scripts and register them with `manage_hook`
- **Custom workflows**: yes — use `hook_event`, `pre_validate`, `batch_validate`, monitors, and hook config to shape runtime behavior
- **Custom tool behavior**: yes — via pre/post interception around the existing `safe_*` tools
- **Custom `prompt` / `agent` hook handlers**: not yet — documented as planned, not shipped

---

## Auto-Start (macOS)

Install the launchd plist for automatic server startup:

```bash
cp project-tools/mcp-hooks-server/com.mcp-augment.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.mcp-augment.plist
```

Verify:

```bash
lsof -i :8200  # mcp-augment hooks server
```

---

## Testing

```bash
python3 tests/mcp_protocol_test.py
python3 tests/cursor_mcp_smoke.py
pytest tests/test_two_way_hooks.py tests/test_demo_search_interception.py tests/test_review_resume_interception.py -q
```

Expected today:

- `tests/mcp_protocol_test.py` passes against the live HTTP server on port `8200`
- `tests/cursor_mcp_smoke.py` returns `"all_passed": true`
- `pytest tests/test_two_way_hooks.py tests/test_demo_search_interception.py tests/test_review_resume_interception.py -q` passes

---

## Roadmap

**Near-term:**

- Rate limiting (RPM enforcement — token bucket in state file, configurable per project)
- macOS Seatbelt / sandbox-style integration for `safe_bash` (exploratory; not MVP)
- Provider/harness routing (dispatch different tools to different backends via config)
- tmux multi-agent support (macOS — spawn named sessions, send keys, capture output)
- `doctor` tool (config hygiene: hook compliance check, log path validation, tool swap e.g. grep→ripgrep)
- Cross-harness `manage_hook` — write to `settings.json` (Claude Code) in addition to `.kilo/hooks/config.yaml`

**Tool wrappers** (`project-tools/` — stubs exist, implementations pending):

- ripgrep, jq, ast-grep as first-class MCP tools

---

## Contributing

1. Fork the repository
2. Create a feature branch
3. Add tests for new functionality
4. Ensure `python3 -m pytest tests/ -q` passes
5. Submit a pull request

---

## License

MIT -- see [LICENSE](LICENSE)
