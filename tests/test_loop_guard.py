# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for the loop guard (loop_guard.py, Runtime 1, #310).

1. The signature: same tool + same input (any key order) match; anything
   else doesn't.
2. The counter: the 3rd identical call asks, the 4th is refused; reads get
   more room and start again after any other call; status polls never
   count; a new turn (or a Harness pause) starts every count again.
3. What happens: Auto mode declines at once, "Allow this turn" lets the 3rd
   through, the local loop's wait ends on an answer or on Stop.
4. The bridge: ask on the 3rd (the card, a deferred answer), refuse the 4th,
   a recorded ACP tool call replayed until it is stopped.
5. #499: External Harness refuses the 3rd at once (nobody is at the card); a tool
   whose answer depends on state outside its input (transcribe_strip: speech
   settings, installed files) counts a call after that state changed as new.

Run with::

    python -m unittest tests.test_loop_guard -v
"""

__all__ = ()

import importlib
import json
import sys
import threading
import types
import unittest
from pathlib import Path
from unittest import mock

_ADDON = Path(__file__).resolve().parents[1] / "addon" / "bfa_coworker"
_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "acp"


def _load():
    name = "bfacw_loop_guard_pkg"
    if name not in sys.modules:
        pkg = types.ModuleType(name)
        pkg.__path__ = [str(_ADDON)]
        sys.modules[name] = pkg
    return importlib.import_module(name + ".loop_guard"), importlib.import_module(name + ".approvals")


lg, ap = _load()

_CODE = {"code": "bpy.data.objects['Cube'].location.z += 1"}


class TestSignature(unittest.TestCase):

    def test_same_call_same_signature(self):
        self.assertEqual(lg.signature("t", {"a": 1, "b": [1, 2]}), lg.signature("t", {"b": [1, 2], "a": 1}))

    def test_other_tool_or_input_differs(self):
        base = lg.signature("execute_blender_code", _CODE)
        self.assertNotEqual(base, lg.signature("execute_blender_code", {"code": "x = 1"}))
        self.assertNotEqual(base, lg.signature("assign_material_to_objects", _CODE))

    def test_odd_input_still_hashes(self):
        self.assertEqual(len(lg.signature("t", {"x": object()})), 40)


class TestCounter(unittest.TestCase):

    def test_ask_on_third_refuse_on_fourth(self):
        g = lg.LoopGuard()
        got = [g.check("execute_blender_code", _CODE) for _ in range(5)]
        self.assertEqual([v for v, _n in got], [lg.RUN, lg.RUN, lg.ASK, lg.REFUSE, lg.REFUSE])
        self.assertEqual([n for _v, n in got], [1, 2, 3, 4, 5])

    def test_different_calls_count_apart(self):
        g = lg.LoopGuard()
        for i in range(10):
            self.assertEqual(g.check("execute_blender_code", {"code": "x = {:d}".format(i)})[0], lg.RUN)

    def test_reads_get_more_room(self):
        g = lg.LoopGuard()
        got = [g.check("get_scene_digest", {})[0] for _ in range(6)]
        self.assertEqual(got, [lg.RUN] * 4 + [lg.ASK, lg.REFUSE])

    def test_read_after_each_step_is_never_flagged(self):
        # The quick challenges' pattern: build a little, read the digest, repeat.
        g = lg.LoopGuard()
        for i in range(10):
            self.assertEqual(g.check("execute_blender_code", {"code": "step({:d})".format(i)})[0], lg.RUN)
            self.assertEqual(g.check("get_scene_digest", {})[0], lg.RUN)
            self.assertEqual(g.check("get_objects_summary", {})[0], lg.RUN)

    def test_a_read_in_between_does_not_reset_a_write(self):
        g = lg.LoopGuard()
        g.check("execute_blender_code", _CODE)
        g.check("get_scene_digest", {})
        g.check("execute_blender_code", _CODE)
        g.check("get_scene_digest", {})
        self.assertEqual(g.check("execute_blender_code", _CODE)[0], lg.ASK)

    def test_status_polls_never_count(self):
        g = lg.LoopGuard()
        for _ in range(20):
            self.assertEqual(g.check("get_job_status", {"job_id": "j1"}), (lg.RUN, 0))

    def test_new_turn_starts_again(self):
        g = lg.LoopGuard()
        g.check("t", 1)
        g.check("t", 1)
        g.new_turn()
        self.assertEqual(g.check("t", 1), (lg.RUN, 1))

    def test_keys_count_apart_and_reset_alone(self):
        g = lg.LoopGuard()
        for _ in range(2):
            g.check("t", 1, key="a")
            g.check("t", 1, key="b")
        g.new_turn("a")
        self.assertEqual(g.check("t", 1, key="a")[0], lg.RUN)
        self.assertEqual(g.check("t", 1, key="b")[0], lg.ASK)

    def test_a_pause_is_a_new_turn_when_asked_for(self):
        now = [0.0]
        g = lg.LoopGuard(idle_reset=60.0, clock=lambda: now[0])
        g.check("t", 1)
        g.check("t", 1)
        now[0] = 61.0
        self.assertEqual(g.check("t", 1), (lg.RUN, 1))
        now[0] = 62.0
        g.check("t", 1)
        self.assertEqual(g.check("t", 1)[0], lg.ASK)

    def test_threads_can_check_at_once(self):
        g = lg.LoopGuard(ask_at=10_000, read_ask_at=10_000)
        workers = [threading.Thread(target=lambda: [g.check("t", 1) for _ in range(200)]) for _ in range(4)]
        for w in workers:
            w.start()
        for w in workers:
            w.join()
        self.assertEqual(g.check("t", 1)[1], 801)


class TestDecide(unittest.TestCase):

    def test_run_and_refuse(self):
        a = ap.Approvals()
        self.assertEqual(lg.decide(lg.RUN, "t", 1, a, False), (lg.RUN, None))
        action, error = lg.decide(lg.REFUSE, "t", 4, a, False)
        self.assertEqual(action, lg.REFUSE)
        self.assertEqual(error["status"], "error")
        self.assertIn("4th", error["message"])

    def test_auto_declines_at_once(self):
        a = ap.Approvals()
        action, error = lg.decide(lg.ASK, "t", 3, a, True)
        self.assertEqual(action, lg.REFUSE)
        self.assertIn("Auto mode", error["message"])
        self.assertIsNone(a.current(), "nothing waits for an answer")

    def test_allow_this_turn_lets_the_third_through(self):
        a = ap.Approvals()
        self.assertEqual(lg.decide(lg.ASK, "t", 3, a, False), (lg.ASK, None))
        a.ask(ap.ApprovalRequest(tool="x"), lambda _c: None)
        a.answer("always")
        self.assertEqual(lg.decide(lg.ASK, "t", 3, a, False), (lg.RUN, None))
        self.assertEqual(lg.decide(lg.REFUSE, "t", 4, a, False)[0], lg.REFUSE, "the 4th is still refused")

    def test_the_card_reads_plainly(self):
        req = lg.repeat_request("execute_blender_code", _CODE, 3, "Coworker")
        self.assertEqual(req.title, "Coworker wants to run execute_blender_code a 3rd time with the same input")
        self.assertIn("same thing", req.note)
        self.assertIn("location.z", req.detail)


class TestWaitAnswer(unittest.TestCase):

    def _answer_soon(self, approvals, choice):
        def run():
            while approvals.current() is None:
                threading.Event().wait(0.01)
            approvals.answer(choice)
        t = threading.Thread(target=run)
        t.start()
        return t

    def test_allow_once(self):
        a = ap.Approvals()
        t = self._answer_soon(a, "once")
        self.assertEqual(lg.wait_answer(a, lg.repeat_request("t", 1, 3)), "once")
        t.join()

    def test_deny(self):
        a = ap.Approvals()
        t = self._answer_soon(a, "deny")
        self.assertEqual(lg.wait_answer(a, lg.repeat_request("t", 1, 3)), "deny")
        t.join()

    def test_stop_cancels_and_takes_the_card_down(self):
        a = ap.Approvals()
        stop = threading.Event()
        stop.set()
        self.assertEqual(lg.wait_answer(a, lg.repeat_request("t", 1, 3), stop, poll=0.01), "cancel")
        self.assertIsNone(a.current())

    def test_cancel_all_ends_the_wait(self):
        a = ap.Approvals()
        threading.Timer(0.05, a.cancel_all).start()
        self.assertEqual(lg.wait_answer(a, lg.repeat_request("t", 1, 3)), "cancel")


class TestBridge(unittest.TestCase):
    """What an External Agent / Harness call gets back from the bridge."""

    def setUp(self):
        self.guard = lg.LoopGuard()
        self.approvals = ap.Approvals()
        self.ran = []

    def _call(self, tool="execute_blender_code", args=None, auto=False, deferred=False):
        args = _CODE if args is None else args

        def proceed():
            self.ran.append(tool)
            if deferred:        # a render's tool-code checker: its bare result
                return {"stdout": "rendering"}, lambda: {"status": "ok", "filepath": "r.png"}
            return {"status": "ok", "result": len(self.ran)}, None
        wrapped = lg.bridge_wrap(self.guard, self.approvals, auto, tool, args, "s1", proceed)
        return proceed() if wrapped is None else wrapped

    def test_first_two_run_third_asks(self):
        self.assertEqual(self._call()[0]["status"], "ok")
        self.assertEqual(self._call()[0]["status"], "ok")
        response, check = self._call()
        self.assertEqual(response, {})
        self.assertIsNotNone(check)
        self.assertIsNone(check(), "waits for the answer")
        self.assertEqual(len(self.ran), 2, "not run yet")
        self.assertIn("3rd time", self.approvals.current().title)
        self.approvals.answer("once")
        self.assertEqual(check(), {"status": "ok", "result": 3})
        response, check = self._call()
        self.assertIsNone(check)
        self.assertIn("Refused", response["message"])
        self.assertEqual(len(self.ran), 3, "the 4th never ran")

    def test_deny_tells_the_model(self):
        self._call()
        self._call()
        _response, check = self._call()
        self.approvals.answer("deny")
        self.assertIn("stopped this repeat", check()["message"])
        self.assertEqual(len(self.ran), 2)

    def test_allowed_call_that_is_itself_deferred(self):
        self._call(deferred=True)
        self._call(deferred=True)
        _response, check = self._call(deferred=True)
        self.approvals.answer("once")
        self.assertIsNone(check())
        # #487: one envelope around the render's result (the bridge sends a marked check's answer as it is).
        self.assertEqual(check(), {"status": "ok", "result": {"status": "ok", "filepath": "r.png"}, "stdout": "rendering"})
        self.assertTrue(getattr(check, "answers_in_full", False))

    def test_repeat_card_around_an_approval_card_is_one_envelope(self):
        # #487: the repeat card's call asks first too (risky_steps): its answer passes through as it is.
        full = {"status": "ok", "result": {"moved": 1}}

        def asked():
            return full
        asked.answers_in_full = True

        def proceed():
            self.ran.append("x")
            return {}, asked
        for _ in range(2):
            lg.bridge_wrap(self.guard, self.approvals, False, "execute_blender_code", _CODE, "s1", proceed)
        _response, check = lg.bridge_wrap(self.guard, self.approvals, False, "execute_blender_code", _CODE, "s1",
                                          proceed)
        self.approvals.answer("once")
        self.assertIsNone(check())
        self.assertEqual(check(), full)
        for _ in range(2):                                       # another session, its third call: Deny
            lg.bridge_wrap(self.guard, self.approvals, False, "execute_blender_code", _CODE, "s2", proceed)
        _r, check = lg.bridge_wrap(self.guard, self.approvals, False, "execute_blender_code", _CODE, "s2", proceed)
        self.approvals.answer("deny")
        self.assertEqual(check()["status"], "error")             # declined: an error at the top, not "ok"

    def test_auto_mode_never_waits(self):
        self._call(auto=True)
        self._call(auto=True)
        response, check = self._call(auto=True)
        self.assertIsNone(check)
        self.assertIn("Auto mode", response["message"])
        self.assertIsNone(self.approvals.current())

    def test_replayed_acp_tool_call(self):
        # A real External Agent session (OpenCode, recorded): its Blender-tool call,
        # repeated with the same input, is what the bridge sees again and again.
        started, calls = {}, []
        for line in (_FIXTURES / "opencode_mcp_tool_call.jsonl").read_text(encoding="utf-8").splitlines():
            update = (json.loads(line)["msg"].get("params") or {}).get("update") or {}
            if update.get("sessionUpdate") != "tool_call_update":
                continue
            if update.get("status") == "in_progress":
                started[update["toolCallId"]] = (update["title"].split("_", 1)[1], update["rawInput"])
            elif update.get("status") == "completed":
                calls.append(started[update["toolCallId"]])
        self.assertEqual(calls, [("search_api_docs", {"query": "primitive_cube_add"})])
        tool, args = calls[0]
        got = []
        for _ in range(6):
            response, check = self._call(tool, args)
            got.append("ask" if check else response["status"])
            if check:
                self.approvals.answer("once")
                self.assertEqual(check()["status"], "ok")
        # A read: more room before it asks; refused once it was let through.
        self.assertEqual(got, ["ok", "ok", "ok", "ok", "ask", "error"])

    def test_a_new_turn_forgets(self):
        self._call()
        self._call()
        self.guard.new_turn()
        self.assertEqual(self._call()[0]["status"], "ok")


class TestNobodyAtTheCard(unittest.TestCase):
    """External Harness (#499): the chat is the client's, nobody answers a card in Blender."""

    def setUp(self):
        self.guard = lg.LoopGuard()
        self.approvals = ap.Approvals()
        self.ran = []

    def _call(self, can_ask=False):
        def proceed():
            self.ran.append(1)
            return {"status": "ok"}, None
        wrapped = lg.bridge_wrap(self.guard, self.approvals, False, "execute_blender_code", _CODE, "s1", proceed,
                                 can_ask=can_ask)
        return proceed() if wrapped is None else wrapped

    def test_the_third_is_refused_at_once_without_a_card(self):
        self._call()
        self._call()
        response, check = self._call()
        self.assertIsNone(check, "nothing waits")
        self.assertIsNone(self.approvals.current(), "no card")
        self.assertEqual(response["status"], "error")
        self.assertIn("3rd call to execute_blender_code", response["message"])
        self.assertIn("ask the user in your chat", response["message"])
        self.assertEqual(len(self.ran), 2, "the 3rd never ran")
        self.assertIn("Refused", self._call()[0]["message"], "the 4th as before")

    def test_others_still_ask(self):
        self._call(can_ask=True)
        self._call(can_ask=True)
        _response, check = self._call(can_ask=True)
        self.assertIsNotNone(check)
        self.assertIsNotNone(self.approvals.current())

    def test_decide(self):
        a = ap.Approvals()
        action, error = lg.decide(lg.ASK, "t", 3, a, False, can_ask=False)
        self.assertEqual(action, lg.REFUSE)
        self.assertIn("nobody in Blender", error["message"])
        self.assertEqual(lg.decide(lg.RUN, "t", 2, a, False, can_ask=False), (lg.RUN, None))
        self.assertIn("Auto mode", lg.decide(lg.ASK, "t", 3, a, True, can_ask=False)[1]["message"])

    def test_the_bridge_passes_it_for_the_harness_only(self):
        src = (_ADDON / "mcp_to_blender_server.py").read_text(encoding="utf-8")
        call = src[src.index("loop_guard.bridge_wrap("):]
        call = call[:call.index("if _repeat is not None")]
        self.assertIn('can_ask=_engine != "EXTERNAL_HARNESS"', call)


class TestStateOutsideTheInput(unittest.TestCase):
    """#499: a tool whose answer depends on settings / installed files counts with them."""

    def setUp(self):
        self.state = ["not set up"]
        patcher = mock.patch.dict(lg._CONTEXT_MODULES, {"t": "x"})
        patcher.start()
        self.addCleanup(patcher.stop)
        real = lg._context
        patcher = mock.patch.object(lg, "_context", lambda tool: self.state[0] if tool == "t" else real(tool))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_call_after_the_state_changed_is_a_new_call(self):
        g = lg.LoopGuard()
        self.assertEqual(g.check("t", A1), (lg.RUN, 1))      # "Speech isn't set up yet"
        self.state[0] = "set up, CUDA, Auto"
        self.assertEqual(g.check("t", A1), (lg.RUN, 1))      # transcribed
        self.state[0] = "set up, CUDA, small"
        self.assertEqual(g.check("t", A1), (lg.RUN, 1))      # another model: transcribed again
        # Nothing changes now: a real loop, caught as before.
        self.assertEqual([g.check("t", A1)[0] for _ in range(3)], [lg.RUN, lg.ASK, lg.REFUSE])

    def test_other_tools_are_counted_as_before(self):
        g = lg.LoopGuard()
        got = []
        for i in range(4):
            self.state[0] = "state {:d}".format(i)          # only "t" reads it
            got.append(g.check("execute_blender_code", _CODE)[0])
        self.assertEqual(got, [lg.RUN, lg.RUN, lg.ASK, lg.REFUSE])

    def test_only_transcribe_strip_has_one(self):
        self.assertEqual(set(lg._CONTEXT_MODULES) - {"t"}, {"transcribe_strip"})
        self.assertEqual(lg._context("execute_blender_code"), "")


class TestContextProviders(unittest.TestCase):

    def test_a_broken_provider_counts_as_before(self):
        with mock.patch.dict(lg._CONTEXT_MODULES, {"t": "no_such_module"}):
            self.assertEqual(lg._context("t"), "")
            g = lg.LoopGuard()
            self.assertEqual([g.check("t", 1)[0] for _ in range(3)], [lg.RUN, lg.RUN, lg.ASK])

    def test_transcribe_strip_reads_the_speech_state(self):
        # The real provider (speech.repeat_context), bpy-free: set-up and a model change move it.
        import tempfile
        from pathlib import Path as _P
        speech = importlib.import_module("bfacw_loop_guard_pkg.speech")
        wm = importlib.import_module("bfacw_loop_guard_pkg.whisper_manager")
        prefs = types.SimpleNamespace(speech_device="CPU", speech_model="AUTO", speech_language="en",
                                      speech_use_remote=False, speech_remote_model="whisper-1", remote_api_url="",
                                      remote_api_key="", port_offset=0, whisper_port=0)
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(speech, "_prefs", lambda: prefs):
            wm.set_data_dir(_P(tmp))
            try:
                g = lg.LoopGuard()
                code = {"code": "transcribe_strip('talk_sound', language='en')"}
                self.assertEqual(g.check("transcribe_strip", code), (lg.RUN, 1))      # not set up
                exe = wm.build_dir("cpu") / "Release" / "whisper-server.exe"
                exe.parent.mkdir(parents=True)
                exe.write_bytes(b"MZ")
                with open(wm.model_file("tiny"), "wb") as fh:
                    fh.truncate(wm.get_preset("tiny").size)
                self.assertEqual(g.check("transcribe_strip", code), (lg.RUN, 1))      # set up
                self.assertEqual(g.check("transcribe_strip", code), (lg.RUN, 2))      # the same again
                prefs.speech_model = "tiny"
                self.assertEqual(g.check("transcribe_strip", code), (lg.RUN, 1))      # another model
                self.assertEqual([g.check("transcribe_strip", code)[0] for _ in range(3)],
                                 [lg.RUN, lg.ASK, lg.REFUSE])                          # then a loop
            finally:
                wm.set_data_dir(None)


A1 = {"code": "transcribe_strip('talk_sound', language='en')"}


if __name__ == "__main__":
    unittest.main()
