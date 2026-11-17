# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
#579: a second All editors tab (made with +) must not take over the file's All editors
chat after reopening.  Only the default thread's chat becomes ``last_for_blend``; a
name given to the All editors chat survives; an index written by an older version
(``last_for_blend`` already pointing at a second tab's chat) shows no chat twice.

Run with::

    python -m unittest tests.test_all_editors_chat -v
"""

__all__ = ()

import importlib
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path

_ADDON = Path(__file__).resolve().parents[1] / "addon" / "bfa_coworker"
_PKG = "bfacw_all579_pkg"


def _import(name):
    if _PKG not in sys.modules:
        pkg = types.ModuleType(_PKG)
        pkg.__path__ = [str(_ADDON)]
        sys.modules[_PKG] = pkg
    return importlib.import_module(_PKG + "." + name)


ch = _import("chat_history")


def _chat(text):
    return [{"role": "user", "content": text, "turn_start": True}, {"role": "assistant", "content": "Done."}]


class TestAllEditorsChat(unittest.TestCase):

    def setUp(self):
        self.index = ch.ChatIndex(Path(tempfile.mkdtemp()))
        self.index.ensure()

    def _save(self, chat_id, text, default, editor="", **kw):
        """What the add-on does for a chat: its file, then its index entry."""
        self.index.folder.mkdir(parents=True, exist_ok=True)
        self.index.chat_path(chat_id).write_text(json.dumps(_chat(text)), encoding="utf-8")
        self.index.touch(chat_id, _chat(text), blend="camp", blend_path="D:/x/camp.blend",
                         editor=editor, default=default, **kw)

    def _reopen(self):
        """(All editors chat, tab chats) the way the add-on restores them."""
        all_editors = self.index.last_for_blend("camp")
        tabs = [t["chat"] for t in self.index.tabs_for_blend("camp") if t["chat"] != all_editors]
        return all_editors, tabs

    def test_second_all_editors_tab_keeps_its_own_chat(self):
        self._save("main", "the file's chat", default=True)
        self._save("second", "made with plus", default=False)         # saved last: used to win
        self.index.set_tabs_for_blend("camp", [{"chat": "second", "editor": ""}])
        all_editors, tabs = self._reopen()
        self.assertEqual(all_editors, "main")
        self.assertEqual(tabs, ["second"])
        self.assertNotIn(all_editors, tabs)

    def test_editor_tabs_still_remember_their_chat(self):
        self._save("main", "all", default=True)
        self._save("view", "3d", default=False, editor="VIEW_3D")
        self.assertEqual(self.index.last_for_blend("camp", "VIEW_3D"), "view")
        self.assertEqual(self.index.last_for_blend("camp"), "main")

    def test_name_of_the_all_editors_chat_survives(self):
        self._save("main", "the file's chat", default=True, user_title="Campfire")
        self._save("second", "made with plus", default=False)
        self.assertEqual(self.index.last_for_blend("camp"), "main")
        entry = self.index.get("main")
        self.assertEqual(entry["title"], "Campfire")
        self.assertTrue(entry["renamed"])
        self._save("main", "more words", default=True, user_title="Campfire")     # saved again
        self.assertEqual(self.index.get("main")["title"], "Campfire")

    def test_older_index_pointing_at_a_tab_shows_no_chat_twice(self):
        self._save("main", "the file's chat", default=True)
        self._save("second", "made with plus", default=False)
        self.index.set_tabs_for_blend("camp", [{"chat": "second", "editor": ""}])
        self.index.set_last_for_blend("camp", "second")                # as older versions wrote it
        all_editors, tabs = self._reopen()
        self.assertEqual(all_editors, "")                              # a fresh chat, not the tab's
        self.assertEqual(tabs, ["second"])

    def test_untold_caller_keeps_the_old_rule(self):
        self.index.touch("a", _chat("hi"), blend="camp")
        self.assertEqual(self.index.last_for_blend("camp"), "")        # no chat file yet
        self.index.chat_path("a").write_text("[]", encoding="utf-8")
        self.assertEqual(self.index.last_for_blend("camp"), "a")


if __name__ == "__main__":
    unittest.main()
