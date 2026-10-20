# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for short tool results (blmcp/tools_helpers/bounded_output.py, task
"Engine 9").

1. A short result passes unchanged; a long one becomes a ~2 KB preview (head
   and tail) with an ``output_id``; structured results keep their status.
2. read_tool_output pages through the saved text, to the end.
3. Through a real FastMCP server: a tool returning a 50 KB node dump answers
   with the preview, and read_tool_output is listed.

Needs the MCP server's packages; run with the server's Python::

    mcp/.venv/Scripts/python -m unittest tests.test_bounded_output -v
"""

__all__ = ()

import asyncio
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

_MCP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mcp")
if _MCP_DIR not in sys.path:
    sys.path.insert(0, _MCP_DIR)

from blmcp.tools_helpers import bounded_output as bo  # noqa: E402  pylint: disable=wrong-import-position


def _node_dump(size=50_000):
    nodes = [{"name": "Node.{:03d}".format(i), "type": "ShaderNodeMath", "inputs": [0.5, 0.25]}
             for i in range(size // 70)]
    return {"status": "ok", "tree": "Material", "nodes": nodes}


class TestShorten(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"LOCALAPPDATA": self.tmp})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_short_results_pass_unchanged(self):
        self.assertEqual(bo.shorten("ok"), "ok")
        small = {"status": "ok", "value": 1}
        self.assertIs(bo.shorten(small), small)
        image = object()
        self.assertIs(bo.shorten(image), image)

    def test_long_dict_becomes_a_preview_with_a_handle(self):
        dump = _node_dump()
        short = bo.shorten(dump, "get_active_node_tree")
        self.assertEqual(list(short)[:2], ["output_id", "total_chars"])   # the handle first
        self.assertEqual(short["status"], "ok")
        self.assertLessEqual(len(json.dumps(short)), 2600)
        self.assertGreater(short["total_chars"], 49_000)
        self.assertIn("read_tool_output", short["note"])
        # The rest is there, in pages, to the end.
        text, offset = "", 0
        while offset is not None:
            page = bo.read(short["output_id"], offset)
            self.assertLessEqual(len(page["text"]), bo.PAGE_MAX)
            text, offset = text + page["text"], page["next_offset"]
        self.assertEqual(json.loads(text), dump)

    def test_long_text_and_bad_ids(self):
        short = bo.shorten("x" * 20_000, "execute_blender_code")
        self.assertTrue(short.startswith("[execute_blender_code result: 20000 characters."))
        self.assertIn("characters left out", short)
        self.assertEqual(bo.read("../../etc/passwd")["status"], "error")
        self.assertEqual(bo.read("0123456789ab")["status"], "error")

    def test_cannot_save_still_answers(self):
        with mock.patch.object(bo, "_save", return_value=""):
            short = bo.shorten("y" * 20_000)
        self.assertIn("couldn't be saved", short)


class TestThroughTheServer(unittest.TestCase):

    def test_a_50kb_tool_result_arrives_short(self):
        from mcp.server.fastmcp import FastMCP  # pylint: disable=import-error,no-name-in-module
        from blmcp.tools import read_tool_output

        mcp = FastMCP("t")
        register = mcp.tool
        mcp.tool = lambda *a, **k: (lambda fn: register(*a, **k)(bo.bounded(fn)))

        @mcp.tool()
        def dump_tree() -> dict[str, object]:
            """A big node tree."""
            return _node_dump()
        read_tool_output.register(mcp)

        async def run():
            names = [t.name for t in await mcp.list_tools()]
            result = await mcp.call_tool("dump_tree", {})
            return names, result
        with mock.patch.dict(os.environ, {"LOCALAPPDATA": tempfile.mkdtemp()}):
            names, result = asyncio.run(run())
        self.assertIn("read_tool_output", names)
        blocks = result[0] if isinstance(result, tuple) else result
        text = "".join(getattr(b, "text", "") for b in blocks)
        self.assertLessEqual(len(text), 2600)
        self.assertIn("output_id", text)


if __name__ == "__main__":
    unittest.main()
