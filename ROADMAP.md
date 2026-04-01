# Roadmap

## v1 (current)

- MCP server under `project-tools/mcp-hooks-server/` with HTTP entry (`mcp-augment-http.py`) and hook-driven `safe_*` tools (`safe_write`, `safe_edit`, `safe_bash`, `safe_read`, `safe_delete`).
- Default hook chain and config under `.kilo/hooks/`.
- Automated tests under `tests/` for hooks, protocol, and smoke scenarios.

## Near term

- CI on `prod/` layout (venv, `pip install`, `pytest`, optional HTTP smoke).
- README and packaging polish as usage grows.

## Future (non-binding)

- Optional Docker-based quick start (not required for v1).
- Additional clients and hook examples maintained as separate small recipes.
