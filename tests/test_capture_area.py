# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for "Attach Screen" taking part of the screen (#200): the rectangle
from two corners (any order), and Shift keeping it square.

Run with::

    python -m unittest tests.test_capture_area -v
"""

__all__ = ()

import unittest
from pathlib import Path

_ADDON = Path(__file__).resolve().parents[1] / "addon" / "bfa_coworker"


def _shot_rect():
    src = (_ADDON / "ui_chat.py").read_text(encoding="utf-8")
    start = src.index("def _shot_rect(")
    end = src.index("\n\n\n", start)
    ns: dict = {}
    exec(compile(src[start:end], "ui_chat.py", "exec"), ns)      # noqa: S102
    return ns["_shot_rect"]


class TestShotRect(unittest.TestCase):

    def test_corners_in_any_order(self):
        rect = _shot_rect()
        self.assertEqual(rect((700, 600), (400, 300)), (400, 300, 700, 600))
        self.assertEqual(rect((400, 600), (700, 300)), (400, 300, 700, 600))

    def test_square_follows_the_longer_side_and_the_drag_direction(self):
        rect = _shot_rect()
        self.assertEqual(rect((100, 100), (300, 150), square=True), (100, 100, 300, 300))
        self.assertEqual(rect((300, 300), (250, 100), square=True), (100, 100, 300, 300))


if __name__ == "__main__":
    unittest.main()
