# AGENTS.md — Guidelines for agents in this repository

This file is the **public** agent brief for the mcp-augment project. It omits private workflow rules that belong only in a full dev checkout.

## Project

- **Purpose**: Provide an MCP server and hook layer so AI coding tools can enforce safer file and shell operations (`safe_*` tools + Pre/Post hook chain).
- **Language**: Python 3.10+
- **Key paths**: `project-tools/mcp-hooks-server/`, `.kilo/hooks/`, `tests/`.

## Build / test

```bash
export PROJECT_DIR="$(pwd)"   # repository root; required for many tests and runtime
pip install -U pip mcp
pip install pytest hatchling    # hatchling if you use pip install -e . once wheel layout exists
pip install -e .               # optional; prod tree may use pip install mcp + pytest only
pytest tests/ -q --tb=short
```

For HTTP integration tests, start the stack (see root `README.md`), then run `python3 tests/mcp_protocol_test.py` and related smoke tests.

## Code style

- Prefer clear, small functions; type hints on public APIs.
- Follow existing patterns in `project-tools/mcp-hooks-server/`.
- Do not commit secrets, API keys, or machine-specific absolute paths in shipped files.

## Scope

- Keep changes focused on the user-facing MCP + hooks story.
- Propose larger refactors in an issue or PR description before broad rewrites.
