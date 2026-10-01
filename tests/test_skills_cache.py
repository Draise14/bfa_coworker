# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Skills-cache regression tests (issue #74, D1).

The skills cache MUST be keyed on the Blender version.  ``Preferences`` draws
``list_loaded_skills()`` (no version) before the first turn; without a key that
version-less build poisoned every later versioned call, silently dropping all
``blender_*.md`` version-drift skills for the whole session.

Run with::

    python -m unittest tests.test_skills_cache -v
"""

__all__ = ()

import importlib.util
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_SKILLS_PATH = _REPO / "addon" / "bfa_coworker" / "skills" / "__init__.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("bfa_skills", _SKILLS_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


_skills = _load_module()


class TestSkillsCacheKey(unittest.TestCase):

    def setUp(self):
        _skills.clear_cache()

    def test_versionless_first_call_does_not_poison_versioned(self):
        # Preferences draws this first, with no version.
        _skills.list_loaded_skills()
        # A later versioned call must still load the drift skills.
        listed = _skills.list_loaded_skills(bpy_version=(5, 3, 0))
        self.assertIn("blender_53.md", listed)
        self.assertIn("blender_52.md", listed)

    def test_versioned_call_loads_version_files(self):
        text = _skills.get_always_loaded_skills(bpy_version=(5, 3, 0))
        listed = _skills.list_loaded_skills()
        self.assertTrue(text)
        self.assertIn("blender_53.md", listed)
        # Base reference files are always present.
        self.assertIn("best_practices.md", listed)

    def test_clear_cache_resets_key(self):
        _skills.get_always_loaded_skills(bpy_version=(5, 3, 0))
        _skills.clear_cache()
        # After a clear, a version-less call rebuilds without version files.
        listed = _skills.list_loaded_skills()
        self.assertNotIn("blender_53.md", listed)

    def test_small_budget_keeps_version_files_whole(self):
        # On a small window the version-drift files (which prevent hard API
        # crashes) must win over the large reference docs, and no file may be
        # truncated to fit.  The current version's file must survive.
        _skills.clear_cache()
        text = _skills.get_always_loaded_skills(
            bpy_version=(5, 3, 0), max_tokens=3000)
        listed = _skills.list_loaded_skills()
        self.assertIn("blender_53.md", listed)
        # Whatever was included fits the budget (whole files only).
        self.assertLessEqual(int(len(text) / 3.5), 3000)
        # best_practices.md is the largest file and cannot fit this budget.
        self.assertNotIn("best_practices.md", listed)

    def test_large_budget_includes_all(self):
        _skills.clear_cache()
        _skills.get_always_loaded_skills(bpy_version=(5, 3, 0), max_tokens=100000)
        listed = _skills.list_loaded_skills()
        for name in ("blender_53.md", "best_practices.md", "mcp_tools.md", "naming.md"):
            self.assertIn(name, listed)

    def test_budget_is_a_cache_key(self):
        # Different budgets must build different blocks (no stale reuse).
        _skills.clear_cache()
        small = _skills.get_always_loaded_skills(bpy_version=(5, 3, 0), max_tokens=3000)
        big = _skills.get_always_loaded_skills(bpy_version=(5, 3, 0), max_tokens=100000)
        self.assertLess(len(small), len(big))


if __name__ == "__main__":
    unittest.main()
