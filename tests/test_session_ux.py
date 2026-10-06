# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Human-readable chat & Session panel helpers (ui_chat, bpy-free parts)."""

import json
import os
import re
import unittest

_UI = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "addon", "bfa_coworker", "ui_chat.py")


def _load():
    src = open(_UI, encoding="utf-8").read()
    ns = {"re": re, "json": json, "_draw_multiline": lambda *a, **k: None}
    exec(src[src.index("def _is_system_note_msg"):src.index("# Friendly Workshop titles")], ns)
    exec(src[src.index("# Human-readable display helpers"):src.index("def _group_turns(")], ns)
    return ns, src


_NS, _SRC = _load()


class TestAttachmentMarker(unittest.TestCase):

    def test_marker_becomes_names_not_text(self):
        clean, names = _NS["_split_attachment_marker"](
            "make it like this\n[attached: FB_IMG_1.jpg, ref.png]")
        self.assertEqual(clean, "make it like this")
        self.assertEqual(names, ["FB_IMG_1.jpg", "ref.png"])
        self.assertEqual(_NS["_split_attachment_marker"]("plain"), ("plain", []))

    def test_chat_bubble_uses_icon_drawer(self):
        self.assertIn('_draw_user_text(turn_box, user_msg.get("content", ""))', _SRC)


class TestMemoryAndCheckpoints(unittest.TestCase):

    def test_memory_is_plain_language(self):
        lines = _NS["_humanize_memory"](
            "[Session memory]\nEarlier request: Build a lighthouse\nRecent context:\n"
            "- user: add a lamp [attached: a.png]\n- assistant: lamp added\n"
            "Last updated: turn 3")
        text = "\n".join(lines)
        self.assertNotIn("[Session memory]", text)
        self.assertNotIn("Last updated", text)
        self.assertNotIn("[attached", text)
        self.assertIn("Earlier you asked: Build a lighthouse", text)
        self.assertIn("You: add a lamp", text)
        self.assertIn("Coworker: lamp added", text)
        self.assertEqual(_NS["_humanize_memory"](""), [])

    def test_checkpoint_title_uses_the_request_words(self):
        cp = {"timestamp": "2026-10-06 10:42:11", "reason": "compaction", "message_count": 24,
              "history": [
                  {"role": "user", "content": "Looks like there are a lot of floating "
                                              "parts. Could you reposition them\n[attached: a.jpg]"},
                  {"role": "user", "content": "[System: keep going]", "system_note": "followup"},
              ]}
        title, detail = _NS["_checkpoint_title"](cp, 3)
        self.assertTrue(title.startswith("Checkpoint 3: Looks like there are"))
        self.assertTrue(title.endswith("…"))
        self.assertNotIn("System", title)
        self.assertIn("10:42", detail)
        self.assertIn("24 msgs", detail)
        self.assertIn("tokens", detail)
        self.assertIn("auto", detail)

    def test_session_is_split_into_subpanels(self):
        for pid in ("BFACW_PT_chat_session_context", "BFACW_PT_chat_session_memory",
                    "BFACW_PT_chat_session_goal"):
            self.assertIn('bl_idname = "{:s}"'.format(pid), _SRC)
        self.assertEqual(_SRC.count('bl_parent_id = "BFACW_PT_chat_session"'), 3)


if __name__ == "__main__":
    unittest.main()
