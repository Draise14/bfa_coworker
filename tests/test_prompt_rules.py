# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Regression guard: the co-work / selection guidance must stay in the prompts.

The scene-safety work relies on the model being told that (a) its selection is
not reliable because the user edits the same scene, and (b) the objects it
touches are temporarily locked in the UI.  If a prompt edit drops these rules
the preflight/error hints become the only defence, so pin them here.

Run with::

    python -m unittest tests.test_prompt_rules -v
"""

__all__ = ()

import os
import unittest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PROMPTS = os.path.join(_REPO, "mcp", "blmcp", "data", "prompts.yml")
_PROMPTS_COMPACT = os.path.join(_REPO, "mcp", "blmcp", "data", "prompts_compact.yml")
_BEST_PRACTICES = os.path.join(
    _REPO, "addon", "bfa_coworker", "skills", "best_practices.md")


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


class TestSelectionIsNotYours(unittest.TestCase):
    def test_full_prompt_has_rule(self):
        text = _read(_PROMPTS)
        self.assertIn("selection is not yours", text.lower())
        self.assertIn("view_layer.objects.active", text)

    def test_compact_prompt_has_rule(self):
        text = _read(_PROMPTS_COMPACT)
        self.assertIn("SELECTION IS NOT YOURS", text)
        self.assertIn("view_layer.objects.active", text)

    def test_best_practices_has_rule(self):
        text = _read(_BEST_PRACTICES)
        self.assertIn("selection is not yours", text.lower())

    def test_prompts_mention_co_work_lock(self):
        for path in (_PROMPTS, _PROMPTS_COMPACT):
            self.assertIn("un-selectable", _read(path).lower(), path)


if __name__ == "__main__":
    unittest.main()
