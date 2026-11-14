# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for helper chats (sub-agents, #479): ``helpers.py`` (bpy-free registry and
texts) and ``helper_chats.py`` (the add-on glue) over the real thread registry
and LoopEngine, with stand-ins for the controller and the chat.

1. The registry: a parent's open batch, "all finished?", results handed over
   once, depth 1, the cap per asking turn, Stop, closing tabs.
2. Texts: the helper's first message, the results message (trimmed answers with a
   note, failures and stops said plainly), the waiting line, editor names.
3. The flow on our loop: two helpers delegated during the asking turn run one
   after another after it, and one "[Helpers]" message starts the asking chat's
   next turn; the parent still running when the results come (they wait in its
   queue); Stop in the parent stops queued and running helpers, and nothing is
   sent back; a helper can't delegate; External Harness is refused.
4. The External Agent's turn-end hook names the turn's thread and its error.
5. The GPU chat's Helpers card: a row per helper, a click shows its tab.

Run with::

    python -m unittest tests.test_helper_chats -v
"""

__all__ = ()

import importlib
import sys
import threading
import time
import types
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_ADDON = _REPO / "addon" / "bfa_coworker"
_PKG = "bfacw_helpers_pkg"


def _package():
    if _PKG not in sys.modules:
        pkg = types.ModuleType(_PKG)
        pkg.__path__ = [str(_ADDON)]
        sys.modules[_PKG] = pkg
    return sys.modules[_PKG]


_package()
hp = importlib.import_module(_PKG + ".helpers")
th = importlib.import_module(_PKG + ".threads")
eng = importlib.import_module(_PKG + ".engine")
el = importlib.import_module(_PKG + ".engine_loop")


def _message_queue_class():
    """The controller's MessageQueue, from its source (agent_controller needs bpy)."""
    src = (_ADDON / "agent_controller.py").read_text(encoding="utf-8")
    start = src.index("@dataclass\nclass MessageQueue:")
    end = src.index("\n\n\n", src.index("class MessageQueue:"))
    ns: dict = {}
    exec("from __future__ import annotations\nimport threading, time\n"
         "from dataclasses import dataclass, field\nfrom typing import Any\n" + src[start:end], ns)
    return ns["MessageQueue"]


MessageQueue = _message_queue_class()


def _state():
    return types.SimpleNamespace(conversation_history=[], turn_active=False, error="", ui_status="",
                                 streaming_text="", reasoning_text="", is_thinking=False, status_text="",
                                 thinking_start_time=0.0, turn_phase="", turn_usage={}, session_usage={},
                                 turn_token=0, prefetched_system_prompt="", mcp_port_actual=0)


def _wait_for(predicate, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(0.01)
    return False


# -- 1. the registry --------------------------------------------------------------------
class TestRegistry(unittest.TestCase):

    def setUp(self):
        self.reg = hp.HelperRegistry()

    def test_batch_finishes_once(self):
        self.reg.add("p", "h1", title="Node tidy", turn_key="1")
        self.reg.add("p", "h2", title="Strips", turn_key="1")
        self.assertEqual([h.id for h in self.reg.waiting("p")], ["h1", "h2"])
        self.assertEqual(self.reg.ended("h1", "end_turn", "Tidied 12 nodes."), "", "h2 still works")
        self.assertEqual(self.reg.take_results("p"), [], "nothing while one runs")
        self.assertEqual(self.reg.ended("h2", "end_turn", "Translated 4 strips."), "p")
        batch = self.reg.take_results("p")
        self.assertEqual([(h.id, h.status, h.answer) for h in batch],
                         [("h1", "done", "Tidied 12 nodes."), ("h2", "done", "Translated 4 strips.")])
        self.assertEqual(self.reg.take_results("p"), [], "handed over once")
        self.assertEqual(self.reg.waiting("p"), [])
        self.assertEqual(self.reg.ended("h1", "end_turn", "again"), "", "later turns of a helper don't count")

    def test_stop_reasons(self):
        self.reg.add("p", "a")
        self.reg.add("p", "b")
        self.reg.add("p", "c")
        self.reg.ended("a", "cancelled", "half")
        self.reg.ended("b", "error", "", "HTTP 500")
        self.assertEqual(self.reg.ended("c", "end_turn", ""), "p")
        a, b, c = self.reg.take_results("p")
        self.assertEqual((a.status, b.status, b.error, c.status), ("stopped", "failed", "HTTP 500", "done"))

    def test_depth_one(self):
        self.reg.add("p", "h1")
        self.assertIn("can't start helpers", self.reg.refusal("h1", "1"))
        self.assertEqual(self.reg.refusal("p", "1"), "")
        self.assertTrue(self.reg.is_helper("h1"))
        self.assertFalse(self.reg.is_helper("p"))
        self.assertEqual(self.reg.parent_of("h1"), "p")

    def test_cap_per_asking_turn(self):
        for n in range(3):
            self.assertEqual(self.reg.refusal("p", "t1", cap=3), "")
            self.reg.add("p", "h{:d}".format(n), turn_key="t1")
        self.assertIn("already started 3 helpers", self.reg.refusal("p", "t1", cap=3))
        self.assertEqual(self.reg.refusal("p", "t2", cap=3), "", "a new turn may start helpers again")
        self.reg.add("p", "h9", turn_key="t2")
        self.assertEqual(self.reg.refusal("p", "t2", cap=1)[:30], "This chat already started 1 he")

    def test_stop_parent(self):
        self.reg.add("p", "h1")
        self.reg.add("p", "h2")
        self.reg.ended("h1", "end_turn", "ok")
        stopped = self.reg.stop_parent("p")
        self.assertEqual([h.id for h in stopped], ["h2"], "only the unfinished ones need stopping")
        self.assertEqual(self.reg.get("h2").status, "stopped")
        self.assertEqual(self.reg.ended("h2", "cancelled"), "", "no results for a stopped parent")
        self.assertEqual(self.reg.take_results("p"), [])

    def test_closing_tabs(self):
        self.reg.add("p", "h1")
        self.reg.add("p", "h2")
        self.reg.ended("h1", "end_turn", "ok")
        self.assertEqual(self.reg.closed("h2"), "p", "a closed helper counts as stopped")
        self.assertEqual([h.status for h in self.reg.take_results("p")], ["done", "stopped"])
        self.reg.add("q", "h3")
        self.assertEqual(self.reg.closed("q"), "", "the parent closed")
        self.assertEqual(self.reg.batch("q"), [], "its helpers are plain chats now")
        self.assertEqual(self.reg.ended("h3", "end_turn", "ok"), "", "and report to no one")


# -- 2. texts -------------------------------------------------------------------------------
class TestTexts(unittest.TestCase):

    def test_parse_editor(self):
        for value, kind in (("", ""), ("All editors", ""), (None, ""), ("NODE_EDITOR", "NODE_EDITOR"),
                            ("node editor", "NODE_EDITOR"), ("Video Sequencer", "SEQUENCE_EDITOR"),
                            ("sequencer", "SEQUENCE_EDITOR"), ("3D Viewport", "VIEW_3D"), ("view_3d", "VIEW_3D"),
                            ("Text Editor", "TEXT_EDITOR"), ("shader editor", "NODE_EDITOR")):
            self.assertEqual(hp.parse_editor(value), kind, value)
        self.assertIsNone(hp.parse_editor("PROPERTIES"))
        self.assertIsNone(hp.parse_editor("kitchen"))

    def test_short_title(self):
        self.assertEqual(hp.short_title("Node tidy"), "Node tidy")
        title = hp.short_title("Tidy every node in the rock material and rename the frames")
        self.assertLessEqual(len(title), hp.TITLE_CHARS)
        self.assertTrue(title.endswith("…"))
        self.assertEqual(hp.short_title("  "), "Helper")

    def test_framing(self):
        text = hp.framing("Scene build", "Tidy the node tree.", "NODE_EDITOR")
        self.assertTrue(text.startswith('[Helper for "Scene build"] Tidy the node tree.'))
        self.assertIn("in the Node Editor's context", text)
        self.assertIn("end with a short, concrete result", text)
        self.assertNotIn("context", hp.framing("Chat", "Do it.", ""))

    def test_results_text(self):
        long_answer = "word " * 600
        batch = [hp.Helper("h1", "p", title="Node tidy", editor="NODE_EDITOR", status="done", answer="Tidied 12 nodes."),
                 hp.Helper("h2", "p", title="Strips", editor="SEQUENCE_EDITOR", status="done", answer=long_answer),
                 hp.Helper("h3", "p", title="Lights", status="failed", error="HTTP 500"),
                 hp.Helper("h4", "p", title="Render", status="stopped")]
        text = hp.results_text(batch, chars=200)
        self.assertTrue(text.startswith("[Helpers] Your 4 helpers finished (2 done)."))
        self.assertIn("1. Node tidy (Node Editor) — done\nTidied 12 nodes.", text)
        self.assertIn('(Shortened to 200 characters: the whole answer is in its tab, "Strips".)', text)
        self.assertNotIn(long_answer.strip(), text)
        self.assertIn("3. Lights (All editors) — failed: HTTP 500.", text)
        self.assertIn("4. Render (All editors) — stopped: it was stopped before it finished.", text)
        self.assertTrue(text.endswith("don't redo what the helpers did."))

    def test_waiting_and_summary(self):
        self.assertEqual(hp.waiting_text(1), "Waiting for 1 helper")
        self.assertEqual(hp.waiting_text(2), "Waiting for 2 helpers")
        batch = [hp.Helper("h1", "p", title="Node tidy", status="running"), hp.Helper("h2", "p", title="Strip translate")]
        self.assertEqual(hp.summary(batch), "2 helpers: Node tidy — running; Strip translate — queued")

    def test_last_answer(self):
        history = [{"role": "user", "content": "old"}, {"role": "assistant", "content": "old answer"},
                   {"role": "user", "content": "[Helper for \"x\"] task"}, {"role": "tool", "content": "ok"},
                   {"role": "assistant", "content": "Step one."},
                   {"role": "user", "content": "[System: note]", "system_note": "x"},
                   {"role": "assistant", "content": "All done: made Cube.001."}]
        self.assertEqual(hp.last_answer(history), "All done: made Cube.001.")
        self.assertEqual(hp.last_answer(history[:3]), "", "no answer yet in this turn")
        self.assertTrue(hp.asked(history, "[Helper for \"x\"] task"))
        self.assertFalse(hp.asked(history[:2], "[Helper for \"x\"] task"))

    def test_results_dont_become_the_goal(self):
        gp = importlib.import_module(_PKG + ".goal_plan")
        goal = gp.GoalPlan() if hasattr(gp, "GoalPlan") else None
        if goal is None:
            self.skipTest("goal_plan has no GoalPlan")
        goal.set_request("Build a campfire scene")
        goal.set_request("[Helpers] Your 2 helpers finished (2 done).")
        self.assertEqual(goal.turn_goal, "Build a campfire scene")


# -- 3. the flow on our loop -------------------------------------------------------------
class _FakeController:
    """What agent_controller.ThreadController gives a LoopEngine (as in test_threads): fake
    turns that write the thread's history, wait for their release, honour Stop."""

    def __init__(self, reg, thread, release, log):
        self.reg, self.thread, self.release, self.log = reg, thread, release, log

    @property
    def _agent_state(self):
        return self.thread.state

    @property
    def _stop_event(self):
        return self.thread.stop

    def request_stop(self):
        self.thread.stop.set()

    def enqueue_message(self, *args, **kwargs):
        return self.thread.queue.enqueue(*args, **kwargs)

    def dequeue_message(self):
        return self.thread.queue.dequeue()

    def turn_blocked(self):
        return self.reg.blocked(self.thread)

    def next_turn(self, engine=None):
        waiting = self.reg.next_waiting()
        if waiting is None:
            return None
        return engine if waiting is self.thread else waiting.engine

    def run_conversation_turn(self, user_message="", **_kw):
        state = self.thread.state
        if not self.reg.try_begin(self.thread):
            return state.conversation_history
        state.turn_active = True
        self.thread.stop.clear()
        self.log.append((self.thread.id, user_message[:12]))
        try:
            state.conversation_history.append({"role": "user", "content": user_message})
            event = self.release.setdefault(self.thread.id, threading.Event())
            while not event.wait(0.01):
                if self.thread.stop.is_set():
                    state.conversation_history.append({"role": "assistant", "content": "stopped"})
                    return state.conversation_history
            event.clear()                       # the next turn waits for its own release
            state.conversation_history.append({"role": "assistant", "content": "done: " + user_message[:40]})
            return state.conversation_history
        finally:
            state.turn_active = False
            self.reg.end(self.thread)


class _Flow(unittest.TestCase):
    """helper_chats over a real ThreadRegistry and LoopEngines, with stand-in modules."""

    def setUp(self):
        self.reg = th.ThreadRegistry(make_state=_state, make_queue=MessageQueue, make_store=dict,
                                     default_objects=True)
        self.release: dict = {}
        self.log: list = []
        self.prefs = types.SimpleNamespace(operating_mode="LOCAL_LLM", helpers_per_turn=4)
        self.timers: list = []
        reg = self.reg

        def engine_for(thread, _mode=None):
            if thread.engine is None:
                engine = el.LoopEngine(_FakeController(reg, thread, self.release, self.log))
                engine.on_turn_finished = lambda t=thread: self.hc.loop_turn_finished(t)
                thread.engine = engine
            return thread.engine

        ac = types.ModuleType(_PKG + ".agent_controller")
        ac.THREADS = reg
        ac._agent_state = reg.default.state
        ac.thread_state = lambda t=None: (t or reg.default).state
        ac.thread_queue = lambda t=None: (t or reg.default).queue
        ac.thread_stop = lambda t=None: (t or reg.default).stop
        ac.turn_blocked = lambda t=None: reg.blocked(t or reg.default)
        ac.prefetch_main_thread_context = lambda t=None: None
        ui = types.ModuleType(_PKG + ".ui_chat")
        ui._acp = None
        ui._is_external_agent = lambda context=None: False
        ui._thread_engine = engine_for
        ui._editor_thread = lambda editor, chat_id="": reg.create(editor=editor, title=th.short_name(editor))
        ui._tab_label = lambda t: "All editors" if t.is_default else (t.title or "Chat")
        ui._save_tabs = lambda: None
        ui._redraw_all_chats = lambda: None
        ui._sync_prefs_to_config = lambda prefs: None
        ui._turn_label = lambda context=None, thread=None: "Coworker's turn"
        ui._approvals_new_turn = lambda thread=None: None
        ui._thread_status = lambda t: ("Thinking", "SORTTIME") if t.state.turn_active else ("Idle", "DOT")
        lm = types.ModuleType(_PKG + ".llm_manager")
        lm.get_config = lambda: types.SimpleNamespace(mode="local", remote_api_url="", remote_api_key="",
                                                      remote_model="")
        cw = types.ModuleType(_PKG + ".coworker_api")
        cw.turn_started = lambda label: None
        cw.cancel_questions = lambda tag=None: None
        rs = types.ModuleType(_PKG + ".risky_steps")
        rs.APPROVALS = types.SimpleNamespace(cancel_all=lambda tag=None: None)
        shared = types.ModuleType(_PKG + ".shared")
        shared.effective_ports = lambda prefs: (9876, 9191, 8080)
        prefs_holder = self.prefs
        bpy = types.ModuleType("bpy")
        bpy.app = types.SimpleNamespace(timers=types.SimpleNamespace(
            register=lambda fn, first_interval=0.0: (self.timers.append(fn), fn())))
        bpy.context = types.SimpleNamespace(preferences=types.SimpleNamespace(
            addons={_PKG: types.SimpleNamespace(preferences=prefs_holder)}))
        stand_ins = {_PKG + ".agent_controller": ac, _PKG + ".ui_chat": ui, _PKG + ".llm_manager": lm,
                     _PKG + ".coworker_api": cw, _PKG + ".risky_steps": rs, _PKG + ".shared": shared, "bpy": bpy}
        saved = {name: sys.modules.get(name) for name in list(stand_ins) + [_PKG + ".helper_chats"]}

        def restore():
            for name, module in saved.items():
                if module is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = module
        self.addCleanup(restore)
        sys.modules.update(stand_ins)
        sys.modules.pop(_PKG + ".helper_chats", None)
        self.hc = importlib.import_module(_PKG + ".helper_chats")
        self.hc.HELPERS.clear()
        self.addCleanup(self.hc.HELPERS.clear)
        self.addCleanup(lambda: [e.set() for e in self.release.values()])
        self.parent = reg.create(title="Scene build")
        engine_for(self.parent)

    def go(self, thread):
        self.release.setdefault(thread.id, threading.Event()).set()

    def start_parent(self, text="Build the scene"):
        self.parent.engine.submit(eng.Prompt(text=text), el.LoopConfig())
        self.assertTrue(_wait_for(lambda: self.parent.state.turn_active))

    def delegate(self, task, editor="", title="", caller=None):
        return self.hc.delegate(th.tag(caller or self.parent), {"task": task, "editor": editor, "title": title})

    def helper(self, result):
        return self.reg.get(result["helper_id"])


class TestFlow(_Flow):

    def test_two_helpers_run_after_the_asking_turn_one_after_another(self):
        self.start_parent()
        one = self.delegate("Tidy the rock material's nodes.", "NODE_EDITOR", "Node tidy")
        two = self.delegate("Translate the title strips to German.", "sequencer", "Strip translate")
        self.assertEqual((one["status"], two["status"]), ("ok", "ok"))
        self.assertIn("is queued", one["message"])
        self.assertIn("one after another", one["message"], "Local says so")
        h1, h2 = self.helper(one), self.helper(two)
        self.assertEqual((h1.editor, h2.editor), ("NODE_EDITOR", "SEQUENCE_EDITOR"))
        self.assertEqual((h1.queue.pending_count, h2.queue.pending_count), (1, 1), "queued, not started")
        self.assertIn(h1, self.reg.live(), "a tab of its own")
        self.assertEqual(self.hc.waiting(self.parent), 2)
        self.assertEqual(self.hc.tab_label(h1), "Node tidy")
        self.assertEqual(self.hc.tab_tip(h1), 'Helper for "Build the scene": ', "the asking chat's first message")
        self.go(self.parent)                                   # the asking turn ends: the helpers go
        self.assertTrue(_wait_for(lambda: h1.state.turn_active))
        self.assertFalse(h2.state.turn_active, "chained: one at a time")
        self.go(h1)
        self.assertTrue(_wait_for(lambda: h2.state.turn_active))
        self.assertEqual(self.hc.waiting(self.parent), 1)
        self.go(h2)
        self.assertTrue(_wait_for(lambda: len(self.parent.state.conversation_history) >= 3),
                        "the results start the asking chat's next turn")
        self.assertEqual([who for who, _t in self.log], [self.parent.id, h1.id, h2.id, self.parent.id])
        results = self.parent.state.conversation_history[2]["content"]
        self.assertTrue(results.startswith("[Helpers] Your 2 helpers finished (2 done)."))
        self.assertIn("1. Node tidy (Node Editor) — done\ndone: [Helper for \"Build the scene\"]", results)
        self.assertIn("2. Strip translate (Video Sequencer) — done", results)
        self.assertEqual(self.hc.waiting(self.parent), 0)
        self.go(self.parent)
        self.assertTrue(_wait_for(lambda: len(self.parent.state.conversation_history) == 4))
        self.assertTrue(h1.state.conversation_history[0]["content"].startswith('[Helper for "Build the scene"]'))

    def test_parent_still_running_when_results_come(self):
        # With Threads 3 a helper may finish while the asking turn still runs: the results wait.
        self.start_parent()
        one = self.delegate("Tidy the nodes.", "NODE_EDITOR")
        h1 = self.helper(one)
        h1.queue.clear()                                       # as if it ran side by side ...
        h1.state.conversation_history += [{"role": "user", "content": self.hc.HELPERS.get(h1.id).message},
                                          {"role": "assistant", "content": "Tidied 9 nodes."}]
        h1.engine = el.LoopEngine(_FakeController(self.reg, h1, self.release, self.log))
        h1.engine.last_stop_reason = "end_turn"
        self.hc.loop_turn_finished(h1)                         # ... and ended now
        self.assertTrue(self.parent.state.turn_active, "the asking turn isn't disturbed")
        self.assertEqual(self.parent.queue.pending_count, 1, "the results wait in its queue")
        self.assertTrue(self.parent.queue.peek()["message"].startswith("[Helpers] Your 1 helper finished"))
        self.assertEqual(self.parent.queue.peek()["mcp_port"], 9191,
                         "sent with the settings the task was sent with (a helper's engine has none of its own)")
        self.go(self.parent)
        self.assertTrue(_wait_for(lambda: len(self.parent.state.conversation_history) >= 3))
        self.assertIn("Tidied 9 nodes.", self.parent.state.conversation_history[2]["content"])

    def test_stop_in_the_parent_stops_queued_helpers(self):
        self.start_parent()
        h1 = self.helper(self.delegate("Tidy the nodes.", "NODE_EDITOR"))
        h2 = self.helper(self.delegate("Translate strips.", "SEQUENCE_EDITOR"))
        self.parent.engine.cancel()
        self.assertEqual(self.hc.stop_helpers(self.parent), 2)
        self.assertEqual((h1.queue.pending_count, h2.queue.pending_count), (0, 0), "their tasks are gone")
        self.assertTrue(_wait_for(lambda: not self.parent.state.turn_active))
        time.sleep(0.1)
        self.assertFalse(h1.state.turn_active or h2.state.turn_active)
        self.assertEqual(self.parent.queue.pending_count, 0, "no results message for a stopped parent")
        self.assertEqual([who for who, _t in self.log], [self.parent.id])
        self.assertEqual(self.hc.waiting(self.parent), 0)

    def test_stop_in_the_parent_stops_a_running_helper(self):
        self.start_parent()
        h1 = self.helper(self.delegate("Tidy the nodes.", "NODE_EDITOR"))
        h2 = self.helper(self.delegate("Translate strips.", "SEQUENCE_EDITOR"))
        self.go(self.parent)
        self.assertTrue(_wait_for(lambda: h1.state.turn_active))
        self.assertEqual(self.hc.waiting(self.parent), 2, "the parent is idle and waits")
        self.hc.stop_helpers(self.parent)
        self.assertTrue(_wait_for(lambda: not h1.state.turn_active))
        self.assertEqual(h1.state.conversation_history[-1]["content"], "stopped")
        time.sleep(0.1)
        self.assertFalse(h2.state.turn_active, "the queued one never starts")
        self.assertEqual(len(self.parent.state.conversation_history), 2, "nothing comes back")

    def test_a_stopped_helper_is_reported_as_stopped(self):
        self.start_parent()
        h1 = self.helper(self.delegate("Tidy the nodes.", "NODE_EDITOR", "Node tidy"))
        self.go(self.parent)
        self.assertTrue(_wait_for(lambda: h1.state.turn_active))
        h1.engine.cancel()                                     # Stop in the helper's own tab
        self.assertTrue(_wait_for(lambda: len(self.parent.state.conversation_history) >= 3))
        self.assertIn("Node tidy (Node Editor) — stopped", self.parent.state.conversation_history[2]["content"])

    def test_a_helper_cant_delegate(self):
        self.start_parent()
        h1 = self.helper(self.delegate("Tidy the nodes.", "NODE_EDITOR"))
        again = self.delegate("More helpers!", caller=h1)
        self.assertEqual(again["status"], "error")
        self.assertIn("can't start helpers", again["message"])
        self.assertEqual(len(self.reg.live()), 3, "no tab opened")

    def test_cap_per_turn_and_bad_params(self):
        self.prefs.helpers_per_turn = 2
        self.start_parent()
        self.assertEqual(self.delegate("one")["status"], "ok")
        self.assertEqual(self.delegate("two")["status"], "ok")
        third = self.delegate("three")
        self.assertEqual(third["status"], "error")
        self.assertIn("Helpers per Turn", third["message"])
        self.assertIn("Give the helper a task", self.delegate("  ")["message"])
        self.assertIn("Unknown editor", self.delegate("x", editor="kitchen")["message"])
        self.parent.state.turn_token = 1                       # its next turn
        self.assertEqual(self.delegate("four")["status"], "ok")

    def test_external_harness_is_refused(self):
        self.prefs.operating_mode = "EXTERNAL_HARNESS"
        result = self.delegate("Tidy the nodes.")
        self.assertEqual(result["status"], "error")
        self.assertIn("sub-agents", result["message"])
        self.assertEqual(len(self.reg.live()), 2)

    def test_closing_a_queued_helper_tab(self):
        self.start_parent()
        h1 = self.helper(self.delegate("Tidy the nodes.", "NODE_EDITOR", "Node tidy"))
        self.hc.tab_closed(h1)
        self.reg.remove(h1.id)
        self.assertEqual(h1.queue.pending_count, 0)
        self.assertEqual(self.parent.queue.pending_count, 1, "the results (one stopped) wait for the parent")
        self.go(self.parent)
        self.assertTrue(_wait_for(lambda: len(self.parent.state.conversation_history) >= 3))
        self.assertIn("Node tidy (Node Editor) — stopped", self.parent.state.conversation_history[2]["content"])

    def test_external_agent_turn_end_reports(self):
        # The External Agent's hook runs on the main thread: the results are sent (started here).
        h1 = self.reg.create(editor="NODE_EDITOR", title="Node tidy")
        self.hc.HELPERS.add(self.parent.id, h1.id, title="Node tidy", editor="NODE_EDITOR", task="Tidy.")
        self.hc.acp_turn_ended(th.tag(h1), "error", "the agent stopped")
        self.assertTrue(_wait_for(lambda: len(self.parent.state.conversation_history) >= 1))
        self.assertIn("Node tidy (Node Editor) — failed: the agent stopped.",
                      self.parent.state.conversation_history[0]["content"])
        self.hc.acp_turn_ended(th.tag(self.parent), "end_turn", "")     # not a helper: nothing happens
        self.go(self.parent)

    def test_card(self):
        self.start_parent()
        h1 = self.helper(self.delegate("Tidy the nodes.", "NODE_EDITOR", "Node tidy"))
        self.delegate("Translate strips.", "SEQUENCE_EDITOR", "Strip translate")
        card = self.hc.card(self.parent)
        self.assertEqual(card.title, "2 helpers — 2 working")
        self.assertEqual([r[1:] for r in card.rows], [("Node tidy", "queued", "TIME"),
                                                      ("Strip translate", "queued", "TIME")])
        self.go(self.parent)
        self.assertTrue(_wait_for(lambda: h1.state.turn_active))
        self.assertEqual(self.hc.card(self.parent).rows[0][2:], ("running", "SORTTIME"))
        self.assertIsNone(self.hc.card(h1), "a helper has no helpers")


# -- 4. the External Agent's hook --------------------------------------------------------------
class TestAcpTurnEndHook(unittest.TestCase):

    def test_hook_names_the_thread_and_error(self):
        ea = importlib.import_module(_PKG + ".engine_acp")
        engine = ea.AcpEngine(_state())
        heard = []
        engine.on_turn_end = lambda thread, reason, error: heard.append((thread, reason, error))
        engine._turn_threads["t_1"] = "t7"
        engine._apply(eng.AgentEvent("error", "t_1", {"message": "boom"}))
        engine._apply(eng.AgentEvent("turn_end", "t_1", {"stop_reason": "error"}))
        engine._apply(eng.AgentEvent("turn_end", "t_2", {"stop_reason": "end_turn"}))
        self.assertEqual(heard, [("t7", "error", "boom"), ("", "end_turn", "")])


# -- 5. the GPU chat's Helpers card ----------------------------------------------------------
class TestHelpersCard(unittest.TestCase):

    def test_rows_and_click(self):
        sf = importlib.import_module(_PKG + ".ui_gpu.surface")
        lay = importlib.import_module(_PKG + ".ui_gpu.layout")
        st = importlib.import_module(_PKG + ".ui_gpu.style")
        card = sf.Helpers(title="2 helpers — 2 working",
                          rows=(("t5", "Node tidy", "running", "SORTTIME"), ("t6", "Strips", "queued", "TIME")))
        surface = sf.ChatSurface()

        def draw():
            canvas = lay.Canvas(st.Style(), lay.fixed_measure(0.55), surface.interaction())
            return canvas, surface.draw(canvas, lay.Rect(0, 0, 500, 600), [], helpers=card)
        canvas, _ = draw()
        ids = [w for _r, w in canvas.hit_areas]
        self.assertIn("helper:t5", ids)
        self.assertIn("helper:t6", ids)
        texts = " ".join(c.text for c in canvas.commands if getattr(c, "text", ""))
        self.assertIn("Node tidy  —  running", texts)
        rect = next(r for r, w in surface._hits if w == "helper:t6")
        self.assertGreaterEqual(rect.y, surface.box.rect.top, "above the input")
        x, y = rect.x + rect.w / 2, rect.y + rect.h / 2
        surface.on_press(x, y)
        surface.on_release(x, y)
        self.assertIn(("show_helper", "t6"), draw()[1])
        self.assertEqual(sf.tip_for("helper:t6")[:26], "Show this helper chat's ta")


if __name__ == "__main__":
    unittest.main()
