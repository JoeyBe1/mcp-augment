#!/usr/bin/env python3
"""Deterministic local search-style backend for mcp-augment two-way hook demo.

Not a real web search: prints fixed-shaped lines so PreToolUse/PostToolUse hooks
 can demonstrate modifiedInput (query year correction) and modifiedOutput (redaction).
"""

from __future__ import annotations

import argparse


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Demo search-style stdout for safe_bash hooks."
    )
    parser.add_argument(
        "--query",
        required=True,
        help="Search-style query string (demo only).",
    )
    args = parser.parse_args()
    print("[DEMO_SEARCH] query:", args.query)
    print("INTERNAL_DEBUG: raw_backend_trace_id=demo-001")
    print("1. mcp-augment two-way hooks (modifiedInput / modifiedOutput)")
    print("2. See project-tools/mcp-hooks-server/DEV-README.md")


if __name__ == "__main__":
    main()
