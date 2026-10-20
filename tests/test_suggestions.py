# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for suggestion buttons (#220): a reply's closing question or options.

Run with::

    python -m unittest tests.test_suggestions -v
"""

__all__ = ()

import importlib
import sys
import types
import unittest
from pathlib import Path

_ADDON = Path(__file__).resolve().parents[1] / "addon" / "bfa_coworker"


def _load():
    name = "bfacw_suggest_pkg"
    if name not in sys.modules:
        pkg = types.ModuleType(name)
        pkg.__path__ = [str(_ADDON)]
        sys.modules[name] = pkg
    return importlib.import_module(name + ".ui_gpu.suggestions")


sg = _load()


class TestSuggestions(unittest.TestCase):

    def test_a_closing_question_becomes_a_yes(self):
        self.assertEqual(sg.suggestions("Done: the table.\n\nShall I add four chairs around your table?"),
                         [("Yes, add four chairs around my table.",) * 2])
        self.assertEqual(sg.suggestions("Done. Would you like me to light the scene?"),
                         [("Yes, light the scene.",) * 2])
        self.assertEqual(sg.suggestions("Which style fits best for the roof?"), [("Yes, go ahead.",) * 2])

    def test_offered_options_become_one_button_each(self):
        reply = ("The walls are up. Next, I could:\n\n"
                 "1. Add **windows** to each wall\n2. Put a `gable` roof on it\n3. Paint the walls white.")
        self.assertEqual([t for _l, t in sg.suggestions(reply)],
                         ["Add windows to each wall", "Put a gable roof on it", "Paint the walls white"])

    def test_a_plain_list_or_statement_gives_nothing(self):
        self.assertEqual(sg.suggestions("I made these objects:\n- Cube\n- Sphere"), [])     # a report, not an offer
        self.assertEqual(len(sg.suggestions("Which one next?\n- Cube\n- Sphere")), 2)
        self.assertEqual(sg.suggestions("Steps taken today.\nAll done."), [])
        self.assertEqual(sg.suggestions("Created:\nCube and Sphere"), [])
        self.assertEqual(sg.suggestions(""), [])

    def test_at_most_five(self):
        reply = "Options:\n" + "\n".join("- option {:d}".format(i) for i in range(8))
        self.assertEqual(len(sg.suggestions(reply)), sg.MAX_BUTTONS)


if __name__ == "__main__":
    unittest.main()
