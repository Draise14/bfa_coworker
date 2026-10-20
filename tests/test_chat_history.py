# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for the chat list (chat_history.py, task "Chat 7", #156).

1. A saved chat gets an entry (title = your first message); an empty one doesn't.
2. Pinned first, then the newest; search filters titles; rename sticks;
   delete removes the files.
3. The old per-.blend chats move in once, with their memory and External
   Agent sessions; backups and side files are left alone.
4. The .blend remembers its last chat.

Run with::

    python -m unittest tests.test_chat_history -v
"""

__all__ = ()

import importlib
import json
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path

_ADDON = Path(__file__).resolve().parents[1] / "addon" / "bfa_coworker"


def _load():
    name = "bfacw_chat_history_pkg"
    if name not in sys.modules:
        pkg = types.ModuleType(name)
        pkg.__path__ = [str(_ADDON)]
        sys.modules[name] = pkg
    return importlib.import_module(name + ".chat_history")


ch = _load()


def _chat(text):
    return [{"role": "system", "content": "sys"}, {"role": "user", "content": text, "turn_start": True},
            {"role": "assistant", "content": "Done."}]


class TestIndex(unittest.TestCase):

    def setUp(self):
        self.base = Path(tempfile.mkdtemp())
        self.index = ch.ChatIndex(self.base)
        self.index.ensure()

    def test_entries_titles_and_order(self):
        self.index.touch("a", _chat("Make a campfire [attached: ref.png]"), blend="camp", engine="Claude Code")
        time.sleep(0.01)
        self.index.touch("b", _chat("Build a lunar lander " * 10), blend="", engine="Local")
        self.index.touch("empty", [{"role": "system", "content": "sys"}])
        entries = self.index.entries()
        self.assertEqual([e["id"] for e in entries], ["b", "a"])           # newest first, empty not listed
        self.assertEqual(entries[1]["title"], "Make a campfire")
        self.assertLessEqual(len(entries[0]["title"]), 60)
        self.index.set_pinned("a", True)
        self.assertEqual([e["id"] for e in self.index.entries()], ["a", "b"])
        self.assertEqual([e["id"] for e in self.index.entries("lander")], ["b"])
        self.assertTrue(self.index.rename("b", "Lander"))
        self.index.touch("b", _chat("something else"))                    # a rename sticks
        self.assertEqual(self.index.get("b")["title"], "Lander")
        self.assertEqual(self.index.last_for_blend(""), "")                # no file written for "b" here

    def test_delete_removes_files_and_last_for_blend(self):
        self.index.chat_path("a").write_text(json.dumps(_chat("hi")), encoding="utf-8")
        self.index.memory_path("a").write_text("{}", encoding="utf-8")
        self.index.touch("a", _chat("hi"), blend="camp")
        self.assertEqual(self.index.last_for_blend("camp"), "a")
        self.index.delete("a")
        self.assertIsNone(self.index.get("a"))
        self.assertFalse(self.index.chat_path("a").exists())
        self.assertFalse(self.index.memory_path("a").exists())
        self.assertEqual(self.index.last_for_blend("camp"), "")


class TestMigration(unittest.TestCase):

    def test_old_per_blend_chats_move_in_once(self):
        base = Path(tempfile.mkdtemp())
        (base / "default.json").write_text(json.dumps(_chat("first chat")), encoding="utf-8")
        (base / "campfire.json").write_text(json.dumps(_chat("campfire chat")), encoding="utf-8")
        (base / "default_2026-10-19_22-10-15.json").write_text(json.dumps(_chat("backup")), encoding="utf-8")
        (base / "default_session_memory.json").write_text('{"memory": 1}', encoding="utf-8")
        (base / "agent_sessions.json").write_text(json.dumps({"campfire.json": {"agent": "claude_code",
                                                                                  "session": "s1"}}),
                                                  encoding="utf-8")
        index = ch.ChatIndex(base)
        index.ensure()
        titles = sorted(e["title"] for e in index.entries())
        self.assertEqual(titles, ["campfire chat", "first chat"])           # no backup, no side files
        camp = index.last_for_blend("campfire")
        self.assertEqual(json.loads(index.chat_path(camp).read_text(encoding="utf-8"))[1]["content"],
                         "campfire chat")
        default = index.last_for_blend("")
        self.assertEqual(index.memory_path(default).read_text(encoding="utf-8"), '{"memory": 1}')
        sessions = json.loads((base / "agent_sessions.json").read_text(encoding="utf-8"))
        self.assertEqual(sessions[index.chat_path(camp).name]["session"], "s1")
        self.assertTrue((base / "campfire.json").exists())                  # the original stays as a backup
        index.ensure()                                                      # once only
        self.assertEqual(len(index.entries()), 2)


class TestTime(unittest.TestCase):

    def test_relative_time(self):
        now = 1_000_000.0
        self.assertEqual(ch.relative_time(now - 5, now), "just now")
        self.assertEqual(ch.relative_time(now - 300, now), "5 min ago")
        self.assertEqual(ch.relative_time(now - 7200, now), "2 h ago")
        self.assertEqual(ch.relative_time(now - 100000, now), "yesterday")


if __name__ == "__main__":
    unittest.main()
