# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""#546: the system message and the tool list stay byte-identical, so Local keeps
its prompt cache.  bpy-free.

llama-server keeps a checkpoint where the system turn ends (the system text and
the tools; the 9B's template puts the text first, the 27B's the tools first) and
one where the last user message starts.  So every request of a session must
start with the same system message and the same tools, and what changes travels
after them.  The real ``run_conversation_turn`` against the fake server of
``test_turn_loop_integration``:

* two steps, a plan update, a checkpoint, another chat and a helper chat: the
  system message and the tool block are the same bytes in every request, and the
  first byte where two requests differ lies after both (in either template order);
* the goal & plan travel at the end, the session memory right after the system
  message, never in it; a helper is offered delegate_task and refused when it calls it;
* a request that must leave tools out to fit keeps that choice for the turn, and
  what it leaves out first is the end of the tool list;
* Remote: the same layout, one system message, no Local fields.

Run with::

    python -m unittest tests.test_static_prefix_546 -v
"""

__all__ = ()

import importlib
import json
import os
import sys
import unittest

_TESTS = os.path.dirname(os.path.abspath(__file__))
if _TESTS not in sys.path:
    sys.path.insert(0, _TESTS)

from test_turn_loop_integration import (  # noqa: E402
    _TurnLoopTestBase, _EXECUTE_CODE_TOOL, _start_fake_server, _tool_call_msg,
)


def _tool(name, size=300):
    return {"name": name, "description": name + " " + "d" * size,
            "inputSchema": {"type": "object", "properties": {}}}


def _plan_call(call_id, **args):
    return {"content": "", "tool_calls": [{
        "id": call_id, "type": "function",
        "function": {"name": "update_plan", "arguments": json.dumps(args)}}]}


def _canon(obj):
    return json.dumps(obj, sort_keys=True, ensure_ascii=False)


def _rendered(req, tools_first):
    """A request as a chat template lays it out: the system turn (system text and
    tools, in the template's order), then every other message."""
    system = _canon(req["messages"][0])
    tools = _canon(req.get("tools") or [])
    head = tools + system if tools_first else system + tools
    return head, head + "".join(_canon(m) for m in req["messages"][1:])


def _first_difference(a, b):
    n = min(len(a), len(b))
    for i in range(n):
        if a[i] != b[i]:
            return i
    return n


class _Base(_TurnLoopTestBase):

    _MCP = [_EXECUTE_CODE_TOOL, _tool("delegate_task"), _tool("setup_pbr_material"),
            _tool("assign_material_to_objects"), _tool("set_collection_color_tag")]

    def setUp(self):
        super().setUp()
        # The registry agent_controller asks (``from .helpers import HELPERS``).
        self.helpers = importlib.import_module("bfa_coworker.helpers")
        # Every chat sees the same scene: the same domains (the default scene's).
        self.ac._detect_domain_from_scene = lambda: {"material"}

    def _serve(self, script, ctx=32768, mode="local", mcp=None):
        self.server.shutdown()
        self.server.server_close()
        self.server = _start_fake_server(script, mcp_tools=list(mcp or self._MCP))
        self.server.n_ctx = ctx
        self.port = self.server.server_address[1]
        cfg = self.lm.LLMConfig()
        cfg.mode = mode
        cfg.local_port = self.port
        cfg.local_ctx_size = ctx
        cfg.local_max_tokens = 1024
        cfg.thinking_budget_tokens = 0
        if mode == "remote":
            cfg.remote_api_url = "http://127.0.0.1:{:d}/v1".format(self.port)
            cfg.remote_model = "remote-model"
        self.lm.set_config(cfg)
        self.lm._runtime_ctx_cache = None

    def _turn(self, text, thread=None, mode="AGENT"):
        self._pin_fake_bpy()
        try:
            return self.ac.run_conversation_turn(
                text, on_text=lambda s: None, chat_mode=mode, llm_url=None, model="fake-model",
                mcp_port=self.port, thread=thread)
        finally:
            self._unpin_fake_bpy()

    def _sent(self):
        with self.server.lock:
            return [r for r in self.server.requests if r not in self.server.memory_writer_calls]

    def _new_chat(self, editor=""):
        t = self.ac.THREADS.create(editor=editor)
        self.addCleanup(self.ac.THREADS.remove, t.id)
        self.ac.thread_state(t).conversation_history = [{"role": "system", "content": "You are a helpful agent."}]
        return t

    def assertSamePrefix(self, requests, what):
        self.assertGreaterEqual(len(requests), 2)
        sys0 = _canon(requests[0]["messages"][0])
        tools0 = _canon(requests[0].get("tools") or [])
        for k, req in enumerate(requests):
            self.assertEqual(_canon(req["messages"][0]), sys0, "{:s}: system message of request {:d}".format(what, k))
            self.assertEqual(_canon(req.get("tools") or []), tools0, "{:s}: tools of request {:d}".format(what, k))
            self.assertEqual([m["role"] for m in req["messages"]].count("system"), 1, "one system message")
        for tools_first in (False, True):
            for a, b in zip(requests, requests[1:]):
                head, ra = _rendered(a, tools_first)
                _head, rb = _rendered(b, tools_first)
                self.assertGreaterEqual(_first_difference(ra, rb), len(head),
                                        "{:s}: two requests differ inside the system turn".format(what))


class TestOnePrefixForTheSession(_Base):

    def test_steps_plan_checkpoint_other_chat_and_helper(self):
        self._serve([
            _plan_call("p1", steps=["look", "add the cube", "colour it"]),        # A, turn 1: a plan update
            _tool_call_msg("c1", "bpy.ops.mesh.primitive_cube_add()"),           # a step
            _plan_call("p2", done=[1, 2]),                                        # the plan moves on
            {"content": "Cube added."},
            {"content": "Coloured."},                                              # A, turn 2 (after a checkpoint)
            {"content": "Sphere added."},                                          # chat B
            {"content": "Helper done."},                                           # the helper chat
        ])
        default = self.ac.THREADS.default
        self.state.conversation_history = [{"role": "system", "content": "You are a helpful agent."}]
        self._turn("add a cube and colour it")
        n_a1 = len(self._sent())
        self.assertEqual(n_a1, 4)
        # A checkpoint: older turns retired into a memory note (the real compaction).
        for i in range(8):
            self.state.conversation_history += [{"role": "user", "content": "older request {:d}".format(i)},
                                                {"role": "assistant", "content": "older reply {:d}".format(i)}]
        self.ac._force_compact_session(self.state.conversation_history, None, 20000)
        memory = self.sm.store.memory_block
        self.assertTrue(memory, "the checkpoint wrote a memory note")
        self._turn("now colour it red")
        # Another chat, then a helper of the first one.
        b = self._new_chat()
        self._turn("add a sphere", thread=b)
        h = self._new_chat()
        self.helpers.HELPERS.add(default.id, h.id, title="Helper", task="tidy up")
        self.addCleanup(self.helpers.HELPERS.clear)
        self.assertTrue(self.ac._is_helper_thread(h))
        self._turn("tidy up the collection", thread=h)

        sent = self._sent()
        self.assertEqual(len(sent), 7)
        self.assertSamePrefix(sent, "two steps, a plan update, a checkpoint, another chat, a helper")
        names = [t["function"]["name"] for t in sent[-1]["tools"]]
        self.assertIn("delegate_task", names, "a helper gets the same tools")
        self.assertIn("setup_pbr_material", names)
        # What moves travels after the tools.
        for req in sent:
            self.assertNotIn("[Goal & plan", req["messages"][0]["content"])
            self.assertNotIn("[Session memory]", req["messages"][0]["content"])
        plan_req = sent[2]                       # after update_plan: the plan is in the context at the end
        self.assertIn("[ ] 2. add the cube", str(plan_req["messages"][-1]["content"]))
        after_plan = sent[3]
        self.assertIn("[x] 2. add the cube", str(after_plan["messages"][-1]["content"]))
        after_checkpoint = sent[4]["messages"]
        self.assertIn(memory, str(after_checkpoint[1]["content"]), "the memory right after the system message")
        self.assertIn("Coworker's notes for this chat", str(after_checkpoint[1]["content"]))
        self.assertIn("now colour it red", str(after_checkpoint[-1]["content"]))
        self.assertIn("Coworker's context for this request", str(after_checkpoint[-1]["content"]))
        # The stored system message is the static one.
        self.assertEqual(self.state.conversation_history[0]["content"], sent[0]["messages"][0]["content"])

    def test_a_helper_calling_delegate_task_is_refused(self):
        h = self._new_chat()
        self.helpers.HELPERS.add(self.ac.THREADS.default.id, h.id, title="Helper", task="x")
        self.addCleanup(self.helpers.HELPERS.clear)
        why = self.helpers.HELPERS.refusal(h.id, "", 3)
        self.assertIn("can't start helpers of its own", why)

    def test_mode_addendum_and_old_texts_are_not_kept(self):
        self._serve([{"content": "a"}, {"content": "b"}, {"content": "c"}])
        legacy = ("You are a helpful agent.\n\n[Note: The Blender scene is currently empty — no objects "
                  "exist. You must create objects before using mode-dependent operators like "
                  "bpy.ops.object.mode_set().]" + self.ac._ASK_MODE_PROMPT_ADDENDUM)
        self.state.conversation_history = [{"role": "system", "content": legacy}]
        self._turn("what is a bevel?", mode="ASK")
        self._turn("add a cube")
        self._turn("add another cube")
        sent = self._sent()
        self.assertTrue(sent[0]["messages"][0]["content"].endswith(self.ac._ASK_MODE_PROMPT_ADDENDUM))
        for req in sent[1:]:
            self.assertEqual(req["messages"][0]["content"], "You are a helpful agent.",
                             "the Ask addendum and the old empty-scene note are gone from the system message")
        self.assertEqual(sent[1]["messages"][0], sent[2]["messages"][0])


class TestPureLayout(_Base):

    def test_static_system_text(self):
        ac = self.ac
        text, where = ac._static_system_text(
            "Base." + ac._PLAN_MODE_PROMPT_ADDENDUM + "\n\n# Where the user is\nIn the Node Editor.", "AGENT")
        self.assertEqual(text, "Base.")
        self.assertEqual(where, "In the Node Editor.")
        text, _ = ac._static_system_text("Base." + ac._ASK_MODE_PROMPT_ADDENDUM, "PLAN")
        self.assertEqual(text, "Base." + ac._PLAN_MODE_PROMPT_ADDENDUM)
        self.assertEqual(ac._static_system_text(text, "PLAN")[0], text, "never twice")

    def test_place_context_joins_a_neighbouring_user_message(self):
        ac = self.ac
        sys0 = {"role": "system", "content": "S"}
        out = ac._place_context([sys0, {"role": "user", "content": "do it"}], "N", "C")
        self.assertEqual([m["role"] for m in out], ["system", "user"])
        self.assertTrue(out[1]["content"].startswith(ac._CHAT_NOTES_HEAD + "\nN\n\ndo it"))
        self.assertTrue(out[1]["content"].endswith(ac._REQUEST_CONTEXT_HEAD + "\nC"))
        tool_tail = [sys0, {"role": "user", "content": "do it"},
                     {"role": "assistant", "content": "", "tool_calls": [{"id": "1"}]},
                     {"role": "tool", "tool_call_id": "1", "content": "ok"}]
        out = ac._place_context(tool_tail, "", "C")
        self.assertEqual([m["role"] for m in out], ["system", "user", "assistant", "tool", "user"])
        self.assertEqual(out[1]["content"], "do it", "nothing joins a message that is not a neighbour")
        pic = [sys0, {"role": "user", "content": [{"type": "text", "text": "this"}]}]
        out = ac._place_context(pic, "N", "")
        self.assertEqual([m["role"] for m in out], ["system", "user", "user"], "a picture message stays as it is")
        self.assertIs(out[2], pic[1])
        self.assertEqual(ac._place_context(pic, "", ""), pic)

    def test_a_left_out_domain_is_the_end_of_the_list(self):
        ac = self.ac
        all_tools = ac._mcp_tools_to_openai([_tool(n) for n in sorted(
            set(ac._SURFACE_TOOLS) | set().union(*ac._TOOL_DOMAINS.values()))])
        domains = {"assets", "lighting", "material", "modeling"}
        early, _late = ac._domain_drop_order(domains, set(), set(), set())
        full = [t["function"]["name"] for t in ac._build_tool_set(all_tools, domains, log=False)]
        fewer = [t["function"]["name"] for t in ac._build_tool_set(all_tools, domains - {early[0]}, log=False)]
        self.assertEqual(full[:len(fewer)], fewer, "leaving out {:s} changes only the end".format(early[0]))
        self.assertLess(len(fewer), len(full))
        # The same set in any order is the same list.
        self.assertEqual(full, [t["function"]["name"] for t in ac._build_tool_set(
            list(reversed(all_tools)), set(reversed(sorted(domains))), log=False)])


class TestFittedToolsStayForTheTurn(_Base):

    def test_left_out_tools_are_not_put_back_within_the_turn(self):
        big = [_EXECUTE_CODE_TOOL, _tool("search_assets", 2500), _tool("get_asset_libraries", 2500),
               _tool("list_asset_catalogs", 2500), _tool("three_point_lighting_rig", 1000)]
        self._serve([_tool_call_msg("c1", "print(1)"), _tool_call_msg("c2", "print(2)"), {"content": "done"}],
                    ctx=8192, mcp=big)
        self.ac._detect_domain_from_scene = lambda: {"assets", "lighting"}
        history = [{"role": "system", "content": "You are a helpful agent."}]
        for i in range(6):
            history += [{"role": "user", "content": "older request {:d} ".format(i) + "x" * 1500},
                        {"role": "assistant", "content": "older reply {:d} ".format(i) + "y" * 1500}]
        self.state.conversation_history = history
        # A checkpoint before step 2 retires the older turns: there is room for
        # every tool again.  Main put them back at once -- a different tool block
        # mid-turn, everything after it re-read; now they come back next turn.
        steps = {"n": 0}

        def checkpoint_before_step_2(hist, tools, budget, **_kw):
            steps["n"] += 1
            if steps["n"] == 2:
                self.ac._force_compact_session(hist, tools, budget)
            return 0
        real = self.ac._maybe_compact_session
        self.ac._maybe_compact_session = checkpoint_before_step_2
        self.addCleanup(setattr, self.ac, "_maybe_compact_session", real)
        self._turn("hello")
        sent = self._sent()
        self.assertEqual(len(sent), 3)
        self.assertTrue(self.sm.store.memory_block, "the checkpoint happened")
        self.assertLess(len(sent[1]["messages"]), len(sent[0]["messages"]), "and made the request smaller")
        first = [t["function"]["name"] for t in sent[0]["tools"]]
        self.assertNotIn("search_assets", first, "made to fit (#544)")
        for req in sent[1:]:
            self.assertEqual(_canon(req["tools"]), _canon(sent[0]["tools"]), "the same tools for the whole turn")
        # The next turn decides again: there is room, so they are back.
        self._serve([{"content": "ok"}], ctx=8192, mcp=big)
        self._turn("thanks")
        self.assertIn("search_assets", [t["function"]["name"] for t in self._sent()[-1]["tools"]])


class TestRemoteRequestShape(_Base):

    def test_remote_has_the_same_layout(self):
        self._serve([_plan_call("p1", steps=["a", "b"]), {"content": "one"}, {"content": "two"}], mode="remote")
        self.state.conversation_history = [{"role": "system", "content": "You are a helpful agent."}]
        self._turn("plan the scene")
        b = self._new_chat()
        self._turn("another chat", thread=b)
        sent = self._sent()
        self.assertEqual(len(sent), 3)
        self.assertSamePrefix(sent, "Remote")
        for req in sent:
            self.assertNotIn("thinking_budget_tokens", req, "no Local fields online")
            self.assertNotIn("chat_template_kwargs", req)
            self.assertEqual(req["messages"][0]["role"], "system")
            self.assertEqual(req["messages"][-1]["role"], "user", "the context ends the request")
        self.assertIn("[Goal & plan", str(sent[1]["messages"][-1]["content"]))
        names = [t["function"]["name"] for t in sent[0]["tools"]]
        core = [n for n in names if n in self.ac._SURFACE_TOOLS or n in ("load_tools", "update_plan")]
        self.assertEqual(names[:len(core)], sorted(core), "the always-offered tools first, by name")


if __name__ == "__main__":
    unittest.main()
