# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for tools on demand (blmcp/tool_domains.py + load_tools, task "Engine 7"),
through a real stdio server.

1. Off (the default): every tool is listed, as before.
2. On (``BFACW_TOOLS_ON_DEMAND=1``): the surface tools only, much smaller;
   the server says its list can change.
3. ``load_tools(["material"])`` sends ``tools/list_changed`` and the material
   tools are listed after it; hidden tools were callable all along.

Needs the MCP server's packages; run with the server's Python::

    mcp/.venv/Scripts/python -m unittest tests.test_tools_on_demand -v
"""

__all__ = ()

import asyncio
import json
import os
import sys
import unittest

from mcp import ClientSession, StdioServerParameters  # pylint: disable=import-error
from mcp.client.stdio import stdio_client  # pylint: disable=import-error

_MCP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mcp")


async def _session(on_demand, body):
    env = {**os.environ, "PYTHONPATH": _MCP_DIR}
    env.pop("BFACW_TOOLS_ON_DEMAND", None)
    if on_demand:
        env["BFACW_TOOLS_ON_DEMAND"] = "1"
    notes = []

    async def on_message(message):
        notes.append(getattr(getattr(message, "root", message), "method", ""))
    params = StdioServerParameters(command=sys.executable, args=["-m", "blmcp", "--transport", "stdio"], env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write, message_handler=on_message) as session:
            init = await session.initialize()
            return await body(session, init, notes)


def _size(tools):
    return sum(len(json.dumps(t.model_dump(exclude_none=True))) for t in tools)


class TestToolsOnDemand(unittest.TestCase):

    def test_off_lists_everything(self):
        async def body(session, init, _notes):
            return [t.name for t in (await session.list_tools()).tools], init.capabilities.tools.listChanged
        names, list_changed = asyncio.run(_session(False, body))
        self.assertIn("setup_pbr_material", names)
        self.assertIn("wire_node_group", names)
        self.assertFalse(list_changed)

    def test_on_lists_the_surface_then_loads_a_domain(self):
        async def body(session, init, notes):
            first = (await session.list_tools()).tools
            result = await session.call_tool("load_tools", {"domains": ["material", "nope"]})
            await asyncio.sleep(0.3)
            after = (await session.list_tools()).tools
            return first, after, result, notes, init.capabilities.tools.listChanged
        first, after, result, notes, list_changed = asyncio.run(_session(True, body))
        names = {t.name for t in first}
        self.assertTrue(list_changed)
        self.assertIn("execute_blender_code", names)
        self.assertIn("load_tools", names)
        self.assertNotIn("setup_pbr_material", names)
        self.assertNotIn("wire_node_group", names)
        self.assertLess(_size(first), 40_000)              # was ~58K characters with every tool
        self.assertIn("notifications/tools/list_changed", notes)
        after_names = {t.name for t in after}
        self.assertIn("setup_pbr_material", after_names)
        self.assertNotIn("wire_node_group", after_names)   # geometry_nodes wasn't asked for
        payload = json.loads(result.content[0].text)
        self.assertEqual(payload["loaded"], ["material"])
        self.assertEqual(payload["unknown"], ["nope"])
        self.assertIn("setup_pbr_material", [t["name"] for t in payload["tools"]])


if __name__ == "__main__":
    unittest.main()
