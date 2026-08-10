# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
MCP tool that reads the operation history JSONL file and returns the last N operations.

The operation history is written by ``agent_controller._log_operation()`` after
each tool execution.  This tool lets the LLM check what it already did, avoiding
redundant or repeated operations.
"""

__all__ = (
    "register",
)

import json
from pathlib import Path

from mcp.server.fastmcp import FastMCP  # pylint: disable=import-error,no-name-in-module
from mcp.types import ToolAnnotations  # pylint: disable=import-error,no-name-in-module


def _history_path() -> Path:
    """Return the path to the operation history JSONL file."""
    return Path.home() / ".cache" / "bfa_coworker" / "operations.jsonl"


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        annotations=ToolAnnotations(  # type: ignore[attr-defined]
            title="Get Operation History",
            readOnlyHint=True,
        )
    )
    def get_operation_history(
        count: int = 10,
    ) -> str:
        """
        Read the last *count* tool operations from the history log.

        Use this to check what operations have already been performed in the
        current session, avoiding redundant or repeated tool calls.

        Args:
            count: Number of recent operations to return (max 50).

        Returns:
            A formatted text summary of the last N operations, or a message
            indicating no history is available.
        """
        count = max(1, min(count, 50))
        log_path = _history_path()

        if not log_path.exists():
            return "No operation history found. Operations are logged after each tool execution."

        try:
            with open(str(log_path), "r", encoding="utf-8") as fh:
                lines = fh.readlines()
        except OSError as ex:
            return "Error reading operation history: {:s}".format(str(ex))

        if not lines:
            return "Operation history is empty."

        # Take the last N lines.
        recent = lines[-count:]

        entries = []
        for line in recent:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                ts = entry.get("timestamp", 0)
                tool = entry.get("tool", "?")
                params = entry.get("params", {})
                result_preview = entry.get("result", "")[:100]
                entries.append(
                    "[{:.1f}] Tool: {:s}\n  Params: {:s}\n  Result: {:s}".format(
                        ts, tool, json.dumps(params)[:200], result_preview
                    )
                )
            except (json.JSONDecodeError, KeyError):
                continue

        if not entries:
            return "No parseable entries in operation history."

        return "Recent operations ({:d} shown):\n\n{:s}".format(
            len(entries), "\n\n".join(entries)
        )
