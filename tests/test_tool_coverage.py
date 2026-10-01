# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tool-coverage guard (issue #74, D17).

Every real MCP tool must be reachable from at least one of:

* ``_SURFACE_TOOLS`` (always offered), or
* a ``_TOOL_DOMAINS`` entry (offered when that domain is loaded).

A tool that appears in neither can never be selected by the model -- it is
invisible even in local mode, and would stay invisible under remote tool
filtering too.  This test parses the tool names out of ``agent_controller.py``
and the real ``@mcp.tool`` definitions under ``mcp/blmcp/tools/`` and fails if
any tool is orphaned.

Run with::

    python -m unittest tests.test_tool_coverage -v
"""

__all__ = ()

import re
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_AC = _REPO / "addon" / "bfa_coworker" / "agent_controller.py"
_TOOLS_DIR = _REPO / "mcp" / "blmcp" / "tools"


def _region(text: str, start_marker: str, end_marker: str) -> str:
    start = text.find(start_marker)
    if start < 0:
        return ""
    end = text.find(end_marker, start)
    return text[start:end if end >= 0 else len(text)]


def _quoted_names(block: str) -> set[str]:
    return set(re.findall(r'"([a-z][a-z0-9_]+)"', block))


def _mcp_tool_names() -> set[str]:
    """Return every tool name defined via ``@mcp.tool`` in the tools dir."""
    names: set[str] = set()
    for path in sorted(_TOOLS_DIR.glob("*.py")):
        if path.name.startswith("_") or path.name.endswith("_toolcode.py"):
            continue
        text = path.read_text(encoding="utf-8")
        # After each @mcp.tool( ... ) decorator, the next ``def name(`` is the
        # tool.  Toolcode modules are skipped above (their defs are helpers,
        # and they are dispatched through their parent tool).
        for m in re.finditer(r"@mcp\.tool\(", text):
            tail = text[m.end():]
            dm = re.search(r"\bdef\s+([a-z][a-z0-9_]+)\s*\(", tail)
            if dm:
                name = dm.group(1)
                # ``*_for_cli`` variants operate on a .blend path in
                # background Blender and are deliberately CLI-only — they are
                # not part of the agent's tool surface.
                if name.endswith("_for_cli"):
                    continue
                names.add(name)
    return names


class TestToolCoverage(unittest.TestCase):

    def test_every_mcp_tool_is_reachable(self):
        text = _AC.read_text(encoding="utf-8")
        surface = _quoted_names(_region(text, "_SURFACE_TOOLS = frozenset({", "})"))
        domains_block = _region(text, "_TOOL_DOMAINS: dict[str, frozenset[str]] = {", "_DOMAIN_KEYWORDS")
        domain_names = _quoted_names(domains_block)
        reachable = surface | domain_names

        mcp_tools = _mcp_tool_names()
        self.assertTrue(mcp_tools, "no @mcp.tool names parsed — path/config drift")

        orphaned = sorted(mcp_tools - reachable)
        self.assertEqual(
            orphaned, [],
            "these MCP tools are in no surface/domain set and can never be "
            "selected: {:s}".format(", ".join(orphaned)),
        )


if __name__ == "__main__":
    unittest.main()
