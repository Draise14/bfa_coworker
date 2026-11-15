# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for #511: a tool's arguments and its answer read ``***`` for a key wherever the
chat shows them, as the Copy buttons and the approval card already did (#346, #500).

1. The Workshop, finished and while the turn runs: the argument line (keys nested in
   dicts and lists, the dicts' own keys), ``execute_blender_code``'s code frame, the
   tool's answer, an agent's tool title, a note; a key set after the first frame; the
   Copy buttons of the code and the answer.
2. The job row under the newest turn.
3. The approval card's title (an agent's tool title is its command line) and line.
4. The status line (``AgentState.ui_status``), as ``error`` already was.
5. The Helpers card and a helper's tab label.
6. What the tool and the model get is not changed: the history keeps the key.

Fake values only: shaped like the keys the redactor knows (``sk-...``, a bearer token)
or handed to it like a key you set.

Run with::

    python -m unittest tests.test_workshop_redaction_511 -v
"""

__all__ = ()

import importlib
import json
import sys
import types
import unittest
from pathlib import Path

_ADDON = Path(__file__).resolve().parents[1] / "addon" / "bfa_coworker"
_PKG = "bfacw_redact511_pkg"


def _load(module):
    if _PKG not in sys.modules:
        pkg = types.ModuleType(_PKG)
        pkg.__path__ = [str(_ADDON)]
        sys.modules[_PKG] = pkg
    return importlib.import_module(_PKG + "." + module)


log = _load("log")
cv = _load("ui_gpu.chat_view")
lay = _load("ui_gpu.layout")
st = _load("ui_gpu.style")
ap = _load("approvals")

FAKE_KEY = "sk-or-v1-" + "FAKEdummy0" * 4             # the sk-... pattern
FAKE_BEARER = "FAKEbearerDUMMY0123456789"              # only ever after "Bearer "
FAKE_SET = "my-fake-remote-key-0000"                   # a key you set (no pattern)
FAKE_LATER = "fake-key-set-later-0000"                 # set after the first frame
SECRETS = (FAKE_KEY, FAKE_BEARER, FAKE_SET, FAKE_LATER)

ARGS = {"name": "Cube", "headers": {"Authorization": "Bearer " + FAKE_BEARER},
        "token": FAKE_KEY, "items": [{"k": FAKE_SET}], FAKE_KEY: 1}
CODE = ("import requests\nrequests.get(url, headers={'Authorization': 'Bearer %s'})\nkey = '%s'\n"
        % (FAKE_BEARER, FAKE_KEY))


def _history(late=""):
    return [
        {"role": "user", "content": "go", "turn_start": True},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "function": {"name": "set_thing", "arguments": json.dumps(ARGS)}},
            {"id": "c2", "function": {"name": "execute_blender_code", "arguments": json.dumps({"code": CODE})}}]},
        {"role": "tool", "name": "set_thing", "tool_call_id": "c1",
         "content": json.dumps({"status": "ok", "result": {"echo": FAKE_KEY, "named": FAKE_SET + late}})},
        {"role": "tool", "name": "execute_blender_code", "tool_call_id": "c2",
         "content": json.dumps({"status": "ok", "result": "ok", "stdout": "Bearer " + FAKE_BEARER})},
        # The External Agent's row: its input in "args", its tool named by a title.
        {"role": "tool", "name": "curl -H 'Authorization: Bearer %s'" % FAKE_BEARER, "tool_call_id": "a1",
         "args": {"command": "curl -H 'Authorization: Bearer %s' x" % FAKE_BEARER, "env": [FAKE_SET]},
         "content": "Error: 401 for " + FAKE_KEY},
        {"role": "user", "content": "[System: the call failed with %s]" % FAKE_KEY, "system_note": "spiral"},
        {"role": "assistant", "content": "Done."},
    ]


class _Live:
    is_thinking = True
    turn_phase = "thinking"
    thinking_dots = 1
    streaming_text = ""


class _Case(unittest.TestCase):

    def setUp(self):
        log.set_secret("remote_api_key", FAKE_SET)
        self.addCleanup(log.set_secret, "remote_api_key", "")
        self.addCleanup(log.set_secret, "later", "")

    def assertNoSecret(self, text, where):
        for secret in SECRETS:
            self.assertNotIn(secret[:12], text, "{:s} shows {:s}".format(where, secret))

    def draw(self, view, hist, live=None, **state):
        canvas = lay.Canvas(st.Style(), lay.fixed_measure(0.55), lay.InteractionState(**state))
        actions = view.draw(canvas, lay.Rect(0, 0, 900, 5000), hist, live=live)
        return "\n".join(cmd.text for cmd in canvas.commands if cmd.kind == "text"), actions


class TestWorkshop(_Case):

    def _open(self):
        view = cv.ChatView()
        view.expanded.add("t0")                 # the Workshop open
        return view

    def test_the_finished_workshop_shows_stars(self):
        hist = _history()
        texts, _actions = self.draw(self._open(), hist)
        self.assertNoSecret(texts, "the Workshop")
        self.assertIn('headers={"Authorization": "Bearer ***"}, token="***", items=[{"k": "***"}]', texts)
        self.assertIn("key = '***'", texts)                          # the code frame
        self.assertIn('"echo": "***"', texts)                         # the tool's own answer first (#500)
        self.assertIn("stdout: Bearer ***", texts)
        self.assertIn("curl -H 'Authorization: Bearer ***'", texts)   # the agent's tool title
        self.assertIn("Error: 401 for ***", texts)
        self.assertIn("the call failed with ***", texts)              # a note
        # The tool and the model get the call as it was.
        self.assertIn(FAKE_KEY, hist[1]["tool_calls"][1]["function"]["arguments"])
        self.assertEqual(hist[4]["args"]["env"], [FAKE_SET])

    def test_the_running_workshop_shows_stars(self):
        hist = _history()[:-1]                  # no reply yet: the turn runs
        texts, _actions = self.draw(self._open(), hist, live=_Live())
        self.assertIn("execute_blender_code", texts)
        self.assertNoSecret(texts, "the running Workshop")

    def test_a_key_set_after_the_first_frame_is_hidden_too(self):
        view, hist = self._open(), _history(late=FAKE_LATER)
        texts, _actions = self.draw(view, hist)
        self.assertIn(FAKE_LATER, texts)        # not a key yet: shown as it is
        log.set_secret("later", FAKE_LATER)
        texts, _actions = self.draw(view, hist)
        self.assertNoSecret(texts, "the Workshop after a key was set")

    def test_the_copy_buttons_copy_stars(self):
        for widget in ("copycode:0:1", "copyres:0:1", "copyres:0:0"):
            _texts, actions = self.draw(self._open(), _history(), clicked=widget)
            copied = [value for kind, value in actions if kind == "copy"]
            self.assertTrue(copied, widget)
            self.assertNoSecret(copied[0], widget)


class TestJobRow(_Case):

    def test_the_job_row_shows_stars(self):
        job = {"kind": "RENDER", "text": "Rendering, 4 s", "label": "Render to //out/%s.png" % FAKE_KEY,
               "progress": 0.5}
        texts, _actions = self.draw(cv.ChatView(running_jobs=lambda: [job]), _history())
        self.assertIn("Rendering, 4 s", texts)
        self.assertNoSecret(texts, "the job row")


class TestApprovalCard(_Case):

    def test_an_agent_tool_title_and_its_input(self):
        card = ap.ApprovalRequest(tool="curl -H 'Authorization: Bearer %s'" % FAKE_BEARER, source="Claude Code",
                                  args={"command": "echo " + FAKE_KEY})
        self.assertEqual(card.title, "Claude Code wants to run curl -H 'Authorization: Bearer ***'")
        self.assertEqual(card.detail, "echo ***")

    def test_a_coworker_tool_card_is_worded_as_before(self):
        card = ap.ApprovalRequest(tool="set_location", source="Coworker", coworker_tool=True,
                                  args={"name": "Cube", "token": FAKE_SET})
        self.assertEqual(card.title, "Coworker wants to run set_location")
        self.assertEqual(card.detail, 'set_location(name="Cube", token="***")')
        risky = ap.ApprovalRequest(tool="risky:x", reason="overwrite " + FAKE_KEY, name="bake_textures")
        self.assertEqual(risky.title, "The agent wants to overwrite ***")


class TestStatusLine(_Case):

    def test_ui_status_is_redacted_wherever_it_is_set(self):
        ac = _load("agent_controller")
        state = ac.AgentState()
        state.ui_status = "Using curl -H 'Authorization: Bearer %s'..." % FAKE_BEARER
        self.assertEqual(state.ui_status, "Using curl -H 'Authorization: Bearer ***'...")
        state.ui_status = "Waiting for you: allow echo {:s}?".format(FAKE_SET)
        self.assertNoSecret(state.ui_status, "the status line")
        state.ui_status = "Idle"
        self.assertEqual(state.ui_status, "Idle")


class TestHelpers(_Case):

    def test_the_helpers_card_and_tab_label(self):
        ac = types.ModuleType(_PKG + ".agent_controller")
        ui = types.ModuleType(_PKG + ".ui_chat")
        ui._thread_status = lambda t: ("Thinking", "SORTTIME")
        bpy = types.ModuleType("bpy")
        stand_ins = {_PKG + ".agent_controller": ac, _PKG + ".ui_chat": ui, "bpy": bpy}
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
        hc = importlib.import_module(_PKG + ".helper_chats")
        hc.HELPERS.clear()
        self.addCleanup(hc.HELPERS.clear)
        ac.THREADS = types.SimpleNamespace(get=lambda _id: None)
        hc.HELPERS.add("p1", "h1", title="Check " + FAKE_KEY, task="Use " + FAKE_KEY)
        card = hc.card(types.SimpleNamespace(id="p1"))
        self.assertEqual(card.rows[0][1], "Check ***")
        self.assertEqual(hc.tab_label(types.SimpleNamespace(id="h1")), "Check ***")
        self.assertIn(FAKE_KEY, hc.HELPERS.get("h1").task, "the helper's task is sent as it is")


if __name__ == "__main__":
    unittest.main()
