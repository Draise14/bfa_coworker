# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
#431: a .blend is a session that holds its chats.

1. History lists the files, newest first, each with its chats; chats that never
   belonged to a saved file are the last group.  Search matches titles and files.
2. Each area's tab is saved with the file: the same place in the same workspace
   first, then the same editor kind when the layout changed.
3. External Agent: a tab's chat continues its own agent session (it used to start
   a fresh one, so the agent had none of the chat), and files each new session
   under its chat.  Another file's chat (``switch_session``) runs in the default
   thread, and the old file's tabs are let go.

Run with::

    python -m unittest tests.test_sessions_by_file -v
"""

__all__ = ()

import importlib
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path

_ADDON = Path(__file__).resolve().parents[1] / "addon" / "bfa_coworker"
_PKG = "bfacw_sessions431_pkg"


def _import(name):
    if _PKG not in sys.modules:
        pkg = types.ModuleType(_PKG)
        pkg.__path__ = [str(_ADDON)]
        sys.modules[_PKG] = pkg
    return importlib.import_module(_PKG + "." + name)


ch = _import("chat_history")
ea = _import("engine_acp")
eng = _import("engine")

_MCP = {"command": "python", "args": ["-m", "blmcp"], "env": {}}


def _chat(text):
    return [{"role": "user", "content": text, "turn_start": True}, {"role": "assistant", "content": "Done."}]


class TestHistoryByFile(unittest.TestCase):

    def setUp(self):
        self.index = ch.ChatIndex(Path(tempfile.mkdtemp()))
        self.index.ensure()

    def touch(self, chat_id, text, blend="", path="", editor=""):
        self.index.touch(chat_id, _chat(text), blend=blend, blend_path=path, editor=editor)
        time.sleep(0.01)

    def test_files_newest_first_with_their_chats(self):
        self.touch("a1", "Dune: camera shots", "45-Dune", "D:/x/45-Dune.blend")
        self.touch("b1", "Cookie: lights", "Cookie-43", "D:/x/Cookie-43.blend")
        self.touch("a2", "Dune: shader", "45-Dune", "D:/x/45-Dune.blend", editor="NODE_EDITOR")
        files = self.index.files()
        self.assertEqual([f["blend"] for f in files], ["45-Dune", "Cookie-43"])
        self.assertEqual([c["id"] for c in files[0]["chats"]], ["a2", "a1"])
        self.assertEqual(files[0]["blend_path"], "D:/x/45-Dune.blend")
        self.assertEqual(files[0]["chats"][0]["editor"], "NODE_EDITOR")

    def test_chats_without_a_file_come_last(self):
        self.touch("u1", "an unsaved try")
        self.touch("a1", "Dune", "45-Dune", "D:/x/45-Dune.blend")
        self.touch("u2", "another unsaved try")
        files = self.index.files()
        self.assertEqual([f["blend"] for f in files], ["45-Dune", ""])
        self.assertEqual([c["id"] for c in files[-1]["chats"]], ["u2", "u1"])

    def test_pinned_chats_first_in_their_file(self):
        self.touch("a1", "first", "45-Dune")
        self.touch("a2", "second", "45-Dune")
        self.index.set_pinned("a1", True)
        self.assertEqual([c["id"] for c in self.index.files()[0]["chats"]], ["a1", "a2"])

    def test_search_matches_titles_and_file_names(self):
        self.touch("a1", "camera shots", "45-Dune")
        self.touch("b1", "lights", "Cookie-43")
        self.assertEqual([f["blend"] for f in self.index.files("dune")], ["45-Dune"])
        self.assertEqual([f["blend"] for f in self.index.files("lights")], ["Cookie-43"])
        self.assertEqual(self.index.files("nothing like it"), [])

    def test_area_tabs_round_trip_and_delete(self):
        tabs = [{"key": ch.area_key("Layout", 2, "VIEW_3D"), "editor": "VIEW_3D", "chat": "a1"},
                {"key": ch.area_key("Shading", 4, "ShaderNodeTree"), "editor": "ShaderNodeTree", "chat": "a2"}]
        self.index.set_area_tabs_for_blend("45-Dune", tabs)
        self.assertEqual(self.index.area_tabs_for_blend("45-Dune"), tabs)
        self.assertEqual(self.index.area_tabs_for_blend("Cookie-43"), [])
        self.index.delete("a2")
        self.assertEqual([t["chat"] for t in self.index.area_tabs_for_blend("45-Dune")], ["a1"])


class TestAreaMatching(unittest.TestCase):

    def test_the_same_area_first(self):
        saved = [{"key": "Layout|2|VIEW_3D", "chat": "a"}, {"key": "Layout|5|VIEW_3D", "chat": "b"}]
        self.assertEqual(ch.match_area_tabs(saved, ["Layout|5|VIEW_3D", "Layout|2|VIEW_3D"]),
                         {"Layout|5|VIEW_3D": "b", "Layout|2|VIEW_3D": "a"})

    def test_the_layout_changed_the_editor_kind_finds_it(self):
        saved = [{"key": "Shading|4|ShaderNodeTree", "editor": "ShaderNodeTree", "chat": "s"},
                 {"key": "Layout|2|VIEW_3D", "editor": "VIEW_3D", "chat": "v"}]
        # An area was split: the indices moved.
        got = ch.match_area_tabs(saved, ["Layout|3|VIEW_3D", "Shading|6|ShaderNodeTree", "Layout|1|OUTLINER"])
        self.assertEqual(got, {"Layout|3|VIEW_3D": "v", "Shading|6|ShaderNodeTree": "s"})

    def test_same_workspace_before_any_workspace(self):
        saved = [{"key": "Layout|2|VIEW_3D", "chat": "layout"}, {"key": "Modeling|1|VIEW_3D", "chat": "model"}]
        got = ch.match_area_tabs(saved, ["Modeling|0|VIEW_3D", "Sculpt|0|VIEW_3D"])
        self.assertEqual(got, {"Modeling|0|VIEW_3D": "model", "Sculpt|0|VIEW_3D": "layout"})

    def test_each_saved_tab_once(self):
        saved = [{"key": "Layout|2|VIEW_3D", "chat": "a"}]
        self.assertEqual(ch.match_area_tabs(saved, ["Layout|0|VIEW_3D", "Layout|1|VIEW_3D"]),
                         {"Layout|0|VIEW_3D": "a"})


class _Client:
    """Just enough of AcpClient for sessions and a turn."""

    def __init__(self, *_a, **_k):
        self.calls = []

    def start(self):
        pass

    def initialize(self):
        return types.SimpleNamespace(name="stub", version="", image=False, load_session=True)

    def new_session(self, cwd, servers):
        self.calls.append("new")
        return {"sessionId": "s{:d}".format(len(self.calls))}

    def load_session(self, session_id, cwd, servers):
        self.calls.append("load " + session_id)
        return {}

    def prompt(self, session_id, blocks, timeout=None):
        self.calls.append("prompt " + session_id)
        return {"stopReason": "end_turn"}

    def cancel(self, _session_id):
        pass

    def close(self):
        pass


def _state():
    return types.SimpleNamespace(conversation_history=[], streaming_text="", reasoning_text="",
                                 is_thinking=False, turn_active=False, error="", tool_activity=[])


class TestTabSessions(unittest.TestCase):

    def setUp(self):
        self.client = _Client()
        self.engine = ea.AcpEngine(_state(), client_factory=lambda *a, **k: self.client)
        self.engine.state_for = lambda _tag: _state()
        self.saved = {"t7": "sess-of-its-chat"}
        self.filed = []
        self.engine.resume_for = lambda tag: self.saved.get(tag, "")
        self.engine.on_thread_session = lambda sid, tag: self.filed.append((tag, sid))
        self.engine.connect(["stub"], _MCP, tempfile.mkdtemp(), wait=True)
        self.assertTrue(self.engine.connected, self.engine.error)
        self.addCleanup(self.engine.disconnect)

    def turn(self, thread):
        self.engine.submit(eng.Prompt(text="hi", thread=thread))
        deadline = time.monotonic() + 10
        while self.engine.is_busy() and time.monotonic() < deadline:
            time.sleep(0.02)

    def test_a_tab_continues_its_chats_session(self):
        self.turn("t7")
        self.assertIn("load sess-of-its-chat", self.client.calls)
        self.assertEqual(self.client.calls[-1], "prompt sess-of-its-chat")
        self.assertEqual(self.filed, [("t7", "sess-of-its-chat")])

    def test_a_new_tab_starts_fresh_and_is_filed(self):
        self.turn("t8")
        self.assertNotIn("load", " ".join(self.client.calls))
        self.assertEqual(self.filed, [("t8", "s2")])        # s1 is the default thread's

    def test_another_file_runs_in_the_default_thread(self):
        self.turn("t7")
        self.engine.drop_threads()
        self.assertEqual(self.engine.thread, "")
        self.assertNotIn("t7", self.engine._threads)
        self.engine.switch_session("sess-of-the-new-files-chat", wait=True)
        self.assertEqual((self.engine.thread, self.engine.session_id), ("", "sess-of-the-new-files-chat"))


if __name__ == "__main__":
    unittest.main()
