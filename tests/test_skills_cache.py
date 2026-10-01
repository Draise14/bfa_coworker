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


if __name__ == "__main__":
    unittest.main()
