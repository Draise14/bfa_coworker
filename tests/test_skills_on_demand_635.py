# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""#635 / #311: skills and tools on demand, so the first Local request fits 16K.  bpy-free.

* ``blmcp/skill_index.py``: the index (name + one line, from the file's front matter, a small
  table, or its first heading), ``load_skill_body``, no path escapes, front matter stripped;
* the server's ``load_skill`` tool and its instructions (External Agent / External Harness);
* the add-on's skills loader: only the version-drift files stay in the prompt;
* the real turn loop against the fake server: the index is in the static system message,
  ``load_skill`` is always offered, a loaded skill (read whole) does not change the system
  message or the tool list, and the first request of a new Local chat fits the 16K preset
  (the number ``llm_manager._FIRST_REQUEST_TOKENS`` is checked against the real prompt and
  the real tool list, so it cannot drift low).

Run with::

    python -m unittest tests.test_skills_on_demand_635 -v
"""

__all__ = ()

import asyncio
import importlib
import importlib.util
import json
import os
import pkgutil
import sys
import tempfile
import types
import unittest
from pathlib import Path

_TESTS = os.path.dirname(os.path.abspath(__file__))
_REPO = Path(_TESTS).parent
_MCP = str(_REPO / "mcp")
if _TESTS not in sys.path:
    sys.path.insert(0, _TESTS)
if _MCP not in sys.path:
    sys.path.insert(0, _MCP)

from test_turn_loop_integration import (  # noqa: E402
    _TurnLoopTestBase, _EXECUTE_CODE_TOOL, _start_fake_server,
)
from test_static_prefix_546 import _canon, _rendered, _first_difference  # noqa: E402

from blmcp import skill_index, tool_domains  # noqa: E402


def _load_skills_module():
    spec = importlib.util.spec_from_file_location(
        "bfa_skills_635", _REPO / "addon" / "bfa_coworker" / "skills" / "__init__.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_SKILLS = _load_skills_module()

_ALL_SKILLS = {"best_practices", "mcp_tools", "naming", "asset_browser", "animation", "collections",
               "materials", "mesh_editing", "modifiers", "operators", "rendering", "transformations"}


class TestSkillIndex(unittest.TestCase):

    def test_every_skill_file_is_in_the_index_with_a_short_line(self):
        listed = dict(skill_index.list_skills())
        self.assertEqual(set(listed), _ALL_SKILLS)
        for name, line in listed.items():
            self.assertTrue(line, name)
            self.assertLessEqual(len(line), 140, name)
            self.assertNotIn("\n", line)

    def test_the_version_drift_files_are_not_skills_on_demand(self):
        self.assertFalse([n for n, _ in skill_index.list_skills() if n.startswith("blender_")])
        self.assertIsNone(skill_index.load_skill_body("blender_53"))

    def test_the_rendered_index_is_small_and_names_the_tool(self):
        text = skill_index.get_skill_index()
        self.assertTrue(text.startswith("## Skills on demand"))
        self.assertIn("load_skill(name)", text)
        for name in _ALL_SKILLS:
            self.assertIn("- {:s}: ".format(name), text)
        self.assertLess(len(text), 2400)          # ~0.4K tokens; the files are ~35K characters
        self.assertEqual(text.count("\n## "), 0)    # one heading: its own section of the prompt

    def test_a_body_loads_by_name_with_a_few_spellings(self):
        body = skill_index.load_skill_body("best_practices")
        self.assertIn("Look Up APIs Before Using Them", body)
        self.assertEqual(skill_index.load_skill_body("Best_Practices.md"), body)
        self.assertEqual(skill_index.load_skill_body("  best_practices "), body)

    def test_unknown_names_and_paths_load_nothing(self):
        for bad in ("", "nope", "../../README", "..\\..\\README", "best_practices/../naming", "/etc/passwd",
                    "blender_52", "tool_domains.py"):
            self.assertIsNone(skill_index.load_skill_body(bad), bad)

    def test_a_skill_file_nobody_described_still_shows_up_by_its_first_heading(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "zz_new.md").write_text("# A brand new topic\n\nbody\n", encoding="utf-8")
            Path(tmp, "bad-name.md").write_text("# no\n", encoding="utf-8")          # not a skill name
            orig = skill_index._skill_dirs
            skill_index._skill_dirs = lambda: [Path(tmp)]
            try:
                self.assertEqual(skill_index.list_skills(), [("zz_new", "A brand new topic")])
                self.assertIsNone(skill_index.load_skill_body("bad-name"))
            finally:
                skill_index._skill_dirs = orig

    def test_front_matter_is_how_320_plugs_in_the_description_and_is_not_part_of_the_body(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "naming.md").write_text(
                "---\nname: naming\ndescription: \"Front matter wins over the table\"\n---\n# Naming\n\nbody\n",
                encoding="utf-8")
            orig = skill_index._skill_dirs
            skill_index._skill_dirs = lambda: [Path(tmp)]
            try:
                self.assertEqual(skill_index.list_skills(), [("naming", "Front matter wins over the table")])
                self.assertEqual(skill_index.load_skill_body("naming"), "# Naming\n\nbody")
            finally:
                skill_index._skill_dirs = orig

    def test_domain_skills_name_real_skills_and_real_domains(self):
        for domain, names in skill_index.DOMAIN_SKILLS.items():
            self.assertIn(domain, tool_domains.DOMAINS)
            for name in names:
                self.assertIn(name, _ALL_SKILLS)
        # One map in two shapes: the add-on's file list is the same.
        derived = {d: [n + ".md" for n in names] for d, names in skill_index.DOMAIN_SKILLS.items()}
        self.assertEqual(_SKILLS._DOMAIN_SKILL_MAP, derived)


class TestAddonSkillsLoader(unittest.TestCase):

    def setUp(self):
        _SKILLS.clear_cache()
        self.addCleanup(_SKILLS.clear_cache)

    def test_on_demand_keeps_only_the_version_drift_files(self):
        text = _SKILLS.get_always_loaded_skills(bpy_version=(5, 3, 0), on_demand=True)
        self.assertNotIn("Look Up APIs Before Using Them", text)       # best_practices
        self.assertNotIn("# MCP Tool Usage Guide", text)
        self.assertNotIn("# Naming Conventions", text)
        self.assertIn(Path(_SKILLS._get_skills_dir(), "blender_53.md").read_text(encoding="utf-8").strip()[:60], text)

    def test_the_old_block_is_still_there_without_the_flag(self):
        text = _SKILLS.get_always_loaded_skills(bpy_version=(5, 3, 0))
        self.assertIn("Look Up APIs Before Using Them", text)
        # And the two builds do not share a cache entry.
        lean = _SKILLS.get_always_loaded_skills(bpy_version=(5, 3, 0), on_demand=True)
        self.assertLess(len(lean), len(text) // 2)
        self.assertEqual(_SKILLS.get_always_loaded_skills(bpy_version=(5, 3, 0)), text)

    def test_the_addon_and_the_server_share_one_index_and_loader(self):
        self.assertEqual(_SKILLS.get_skill_index(), skill_index.get_skill_index())
        self.assertEqual(_SKILLS.load_skill_body("materials"), skill_index.load_skill_body("materials"))
        self.assertIsNone(_SKILLS.load_skill_body("nope"))


def _server():
    from mcp.server.fastmcp import FastMCP
    mcp = FastMCP("t", instructions="x")
    import blmcp.tools as tp
    for _i, name, _p in pkgutil.iter_modules(tp.__path__):
        if name.endswith("_toolcode") or name.startswith("_template_"):
            continue
        mod = importlib.import_module("blmcp.tools." + name)
        if hasattr(mod, "register"):
            mod.register(mcp)
    return mcp


_REAL_SERVER = None


def _real_mcp_tools():
    """The server's real tool list as ``tools/list`` gives it."""
    global _REAL_SERVER
    if _REAL_SERVER is None:
        _REAL_SERVER = _server()
    tools = asyncio.run(_REAL_SERVER.list_tools())
    return [{"name": t.name, "description": t.description or "", "inputSchema": t.inputSchema} for t in tools]


class TestLoadSkillTool(unittest.TestCase):

    def test_the_server_lists_load_skill_and_it_is_always_offered(self):
        names = {t["name"] for t in _real_mcp_tools()}
        self.assertIn("load_skill", names)
        self.assertIn("load_skill", tool_domains.SURFACE)              # never hidden by tools on demand

    def test_it_returns_the_whole_skill_and_lists_names_for_an_unknown_one(self):
        mcp = _server()
        content, _meta = asyncio.run(mcp.call_tool("load_skill", {"name": "naming"}))
        self.assertEqual(content[0].text, skill_index.load_skill_body("naming"))
        content, _meta = asyncio.run(mcp.call_tool("load_skill", {"name": "nope"}))
        self.assertIn("Unknown skill 'nope'", content[0].text)
        for name in _ALL_SKILLS:
            self.assertIn(name, content[0].text)
        content, _meta = asyncio.run(mcp.call_tool("load_skill", {}))
        self.assertTrue(content[0].text.startswith("Skills: "))

    def test_the_largest_skill_is_not_shortened_by_the_servers_result_limit(self):
        from blmcp.tools_helpers import bounded_output
        biggest = max(len(skill_index.load_skill_body(n) or "") for n in _ALL_SKILLS)
        self.assertGreater(biggest, bounded_output.LIMIT)
        self.assertGreaterEqual(bounded_output._LIMITS["load_skill"], biggest)

    def test_the_servers_instructions_end_with_the_index(self):
        import blmcp
        text = blmcp.instructions_with_skill_index("Base text.\n")
        self.assertTrue(text.startswith("Base text.\n\n## Skills on demand"))
        self.assertTrue(text.endswith(skill_index.get_skill_index()))

    def test_load_tools_points_at_a_domains_skills(self):
        self.assertEqual(skill_index.DOMAIN_SKILLS["material"], ("materials",))


class _LoopBase(_TurnLoopTestBase):
    """The real turn loop with a built system prompt (the index in it) and the real tool list."""

    def setUp(self):
        super().setUp()
        # A bpy of our own: a Blender 5.3 with no preferences and an empty scripts folder.
        self._tmp = tempfile.mkdtemp()
        fake = types.ModuleType("bpy")
        fake.app = types.SimpleNamespace(
            version=(5, 3, 0),
            timers=types.SimpleNamespace(register=lambda cb, first_interval=0.0: None, unregister=lambda cb: None))
        fake.utils = types.SimpleNamespace(user_resource=lambda kind: self._tmp)
        fake.data = types.SimpleNamespace(filepath="")
        fake.context = types.SimpleNamespace(preferences=types.SimpleNamespace(addons={}))
        self._bpy = fake
        self.ac._detect_domain_from_scene = lambda: set()

    def tearDown(self):
        super().tearDown()
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _pin_fake_bpy(self):
        self._saved_bpy = sys.modules.get("bpy")
        sys.modules["bpy"] = self._bpy

    def _serve(self, script, ctx=32768, mode="local", mcp=None):
        self.server.shutdown()
        self.server.server_close()
        self.server = _start_fake_server(script, mcp_tools=list(mcp or _real_mcp_tools()))
        self.server.n_ctx = ctx
        self.port = self.server.server_address[1]
        cfg = self.lm.LLMConfig()
        cfg.mode = mode
        cfg.local_port = self.port
        cfg.local_ctx_size = ctx
        cfg.local_max_tokens = 2048
        cfg.thinking_budget_tokens = 0
        if mode == "remote":
            cfg.remote_api_url = "http://127.0.0.1:{:d}/v1".format(self.port)
            cfg.remote_model = "remote-model"
        self.lm.set_config(cfg)
        self.lm._runtime_ctx_cache = None
        self.ac._system_prompt_cache.clear()
        _SKILLS.clear_cache()
        sk = sys.modules.get("bfa_coworker.skills")
        if sk is not None:
            sk.clear_cache()

    def _turn(self, text, mode="AGENT"):
        self._pin_fake_bpy()
        try:
            self.state.prefetched_system_prompt = self.ac._get_system_prompt_with_rules()
            self.state.conversation_history = []
            return self.ac.run_conversation_turn(
                text, on_text=lambda s: None, chat_mode=mode, llm_url=None, model="fake-model",
                mcp_port=self.port, thread=None)
        finally:
            self._unpin_fake_bpy()

    def _sent(self):
        with self.server.lock:
            return [r for r in self.server.requests if r not in self.server.memory_writer_calls]

    @staticmethod
    def _call(call_id, tool, **args):
        return {"content": "Working on it.", "tool_calls": [{
            "id": call_id, "type": "function", "function": {"name": tool, "arguments": json.dumps(args)}}]}

    def _tokens(self, req):
        return self.ac._estimate_messages_tokens(req["messages"]) + self.ac._estimate_tools_tokens(req.get("tools"))


class TestFirstRequest(_LoopBase):

    def test_local_16k_first_request_has_the_index_the_version_files_and_core_tools(self):
        self._serve([{"content": "Hello."}], ctx=16384)
        self._turn("Hi, what is in the scene?")
        req = self._sent()[0]
        system = req["messages"][0]["content"]
        self.assertIn("## Skills on demand", system)
        self.assertIn("- materials: ", system)
        self.assertIn(Path(_SKILLS._get_skills_dir(), "blender_53.md").read_text(encoding="utf-8").strip()[:80], system)
        for body_marker in ("Look Up APIs Before Using Them", "# MCP Tool Usage Guide", "# Naming Conventions"):
            self.assertNotIn(body_marker, system)
        names = [t["function"]["name"] for t in req["tools"]]
        self.assertIn("load_skill", names)
        self.assertIn("load_tools", names)
        self.assertNotIn("setup_pbr_material", names)           # a domain's tool: on demand

    def test_the_first_request_fits_the_16k_preset_and_the_number_in_llm_manager_covers_it(self):
        self._serve([{"content": "Hello."}], ctx=16384)
        self._turn("Hi, what is in the scene?")
        used = self._tokens(self._sent()[0])
        first = self.lm._FIRST_REQUEST_TOKENS
        self.assertLessEqual(used, first, "the add-on's own count of the first request is above "
                                          "_FIRST_REQUEST_TOKENS ({:d}): raise it".format(used))
        self.assertLessEqual(first + self.lm._REPLY_ROOM_TOKENS, 16384)
        self.assertEqual(self.lm.min_agent_ctx(), 16384)
        self.assertNotIn("first request", self.lm.validate_ctx_against_hardware(15.3, "cuda", 16384) or "")
        self.assertIsNotNone(self.lm.validate_ctx_against_hardware(15.3, "cuda", 8192))

    def test_a_request_with_domains_still_fits_16k_and_keeps_them(self):
        self._serve([{"content": "Done."}], ctx=16384)
        self.ac._detect_domain_from_scene = lambda: {"material"}
        self._turn("Make the cube a red glass material and add a sun light")
        req = self._sent()[0]
        names = {t["function"]["name"] for t in req["tools"]}
        self.assertTrue({"setup_pbr_material", "three_point_lighting_rig"} <= names, sorted(names))
        self.assertIn("## Skills on demand", req["messages"][0]["content"])
        # No domain skill text pasted into the request any more (the index names them).
        self.assertNotIn("## Domain Skills", json.dumps(req["messages"]))
        self.assertLessEqual(self._tokens(req) + 2048, 16384 + 512)

    def test_remote_gets_the_index_instead_of_the_whole_skill_block(self):
        self._serve([{"content": "Hello."}], ctx=0, mode="remote")
        self._turn("Hi, what is in the scene?")
        req = self._sent()[0]
        system = req["messages"][0]["content"]
        self.assertIn("## Skills on demand", system)
        self.assertNotIn("Look Up APIs Before Using Them", system)
        self.assertIn("load_skill", [t["function"]["name"] for t in req["tools"]])
        # Remote keeps the full tool descriptions; only Local gets the short ones.
        desc = {t["function"]["name"]: t["function"]["description"] for t in req["tools"]}
        self.assertIn("Returns:", desc["get_job_status"])

    def test_local_tool_descriptions_are_shorter_than_remotes(self):
        self._serve([{"content": "Hello."}], ctx=16384)
        self._turn("Hi")
        local = {t["function"]["name"]: t["function"]["description"] for t in self._sent()[0]["tools"]}
        self.assertNotIn("Returns:", local["get_job_status"])
        self.assertIn("Args:", local["get_job_status"])
        self.assertIn("job_id", local["get_job_status"])


class TestLoadingASkillKeepsThePrefix(_LoopBase):

    def test_a_skill_loaded_mid_chat_is_a_tool_result_read_whole_and_rewrites_nothing_before_it(self):
        body = skill_index.load_skill_body("best_practices")
        self.assertGreater(len(body), 10000)
        self._serve([self._call("s1", "load_skill", name="best_practices"),
                     {"content": "Read it; ready."}], ctx=32768)
        self.server.named_tool_replies["load_skill"] = [{"__content__": [{"type": "text", "text": body}]}]
        self._turn("Tidy the scene names")
        sent = self._sent()
        self.assertEqual(len(sent), 2)
        # The system message and the tool list: the same bytes in both requests.
        self.assertEqual(_canon(sent[0]["messages"][0]), _canon(sent[1]["messages"][0]))
        self.assertEqual(_canon(sent[0]["tools"]), _canon(sent[1]["tools"]))
        for tools_first in (False, True):
            head0, rendered0 = _rendered(sent[0], tools_first)
            _head1, rendered1 = _rendered(sent[1], tools_first)
            self.assertGreaterEqual(_first_difference(rendered0, rendered1), len(head0))
        # The skill is in the second request as the tool's answer, whole (not cut at 2000 characters).
        tool_msgs = [m for m in sent[1]["messages"] if m["role"] == "tool"]
        self.assertEqual(len(tool_msgs), 1)
        self.assertEqual(tool_msgs[0]["content"], body)
        self.assertNotIn("Look Up APIs Before Using Them", sent[1]["messages"][0]["content"])

    def test_load_tools_names_the_skills_of_the_domain_it_loads(self):
        self._serve([self._call("t1", "load_tools", domain="material"), {"content": "Loaded."}])
        self._turn("Make it shiny")
        second = self._sent()[1]
        answer = [m for m in second["messages"] if m["role"] == "tool"][0]["content"]
        self.assertIn("Loaded material tools.", answer)
        self.assertIn("materials", answer)
        self.assertIn("load_skill", answer)
        # A domain without a skill file says nothing about skills.
        self.assertEqual(self.ac._domain_skill_hint("lighting"), "")
        self.assertEqual(self.ac._domain_skill_hint("nope"), "")


class TestStripAndTrim(_LoopBase):

    def test_leaving_the_built_in_block_out_takes_the_whole_block_with_its_own_headings(self):
        text = ("You are connected to Blender 5.3.\n\n## Built-in Skills\nversion file\n\n## Blender 5.3 API Changes\n"
                "more\n\n## Skills on demand\nRead: x\n- a: b\n\n## Project Rules\nrules\n\n## Instructions\nbe brief")
        out = self.ac._strip_builtin_skills(text)
        self.assertNotIn("Built-in Skills", out)
        self.assertNotIn("API Changes", out)
        self.assertIn("## Skills on demand\nRead: x\n- a: b", out)       # the index stays
        self.assertNotIn("version file", out)
        self.assertIn("## Project Rules\nrules", out)
        self.assertIn("## Instructions\nbe brief", out)

    def test_a_skill_result_is_kept_whole_other_results_are_cut(self):
        big = "s" * 9000
        msgs = [{"role": "tool", "name": "load_skill", "tool_call_id": "a", "content": big},
                {"role": "tool", "name": "get_objects_summary", "tool_call_id": "b", "content": big}]
        out = self.ac._trim_history_tool_results(msgs)
        self.assertEqual(out[0]["content"], big)
        self.assertLess(len(out[1]["content"]), len(big))
        giant = "g" * 20000
        cut = self.ac._trim_history_tool_results([{"role": "tool", "name": "load_skill", "content": giant}])
        self.assertLess(len(cut[0]["content"]), len(giant))

    def test_compact_description_drops_returns_keeps_args_and_whole_paragraphs(self):
        doc = ("\n        Do the thing.\n\n        More about it, in a long second paragraph.\n\n"
               "        Args:\n            a: the a.\n            b: the b.\n\n"
               "        Returns:\n            ``status`` and lots more.\n")
        out = self.ac._compact_tool_description(doc)
        self.assertTrue(out.startswith("Do the thing."))
        self.assertIn("Args:\na: the a.\nb: the b.", out)
        self.assertNotIn("Returns", out)
        self.assertNotIn("lots more", out)
        # Over the limit: later prose goes, the first paragraph and Args stay, never a cut sentence.
        long_doc = "First.\n\n" + ("x" * 700) + "\n\nArgs:\n    q: question."
        short = self.ac._compact_tool_description(long_doc, limit=100)
        self.assertEqual(short, "First.\n\nArgs:\nq: question.")
        one = "y" * 3000
        self.assertEqual(self.ac._compact_tool_description(one), one)
        self.assertEqual(self.ac._compact_tool_description(""), "")
        self.assertEqual(self.ac._compact_tool_description(doc), self.ac._compact_tool_description(doc))

    def test_local_conversion_is_compact_remote_is_whole(self):
        tool = {"name": "t", "description": "Does a.\n\nReturns:\n    stuff.", "inputSchema": {"type": "object"}}
        whole = self.ac._mcp_tools_to_openai([tool])[0]["function"]["description"]
        compact = self.ac._mcp_tools_to_openai([tool], compact=True)[0]["function"]["description"]
        self.assertIn("Returns:", whole)
        self.assertEqual(compact, "Does a.")


if __name__ == "__main__":
    unittest.main()
