# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for #537: Make Vertical's 9:16 copy survives save and reopen.

``ID.copy()`` hands back a copy with no user, and nothing counts as a scene's user
(not the window, not the workspace's Sequencer), so Blender drops a 0-user scene
on save.  The copy gets the extra user Blender gives the scenes it makes itself --
not a fake user, so deleting it by hand removes it and what only it used.

The stand-in scene below behaves like Blender's ID user counting for the parts the
code touches: ``copy()`` returns 0 users, ``use_extra_user`` adds / removes one user,
``use_fake_user`` likewise.  No Blender needed::

    python -m unittest tests.test_reframe_copy_saved -v
"""

__all__ = ()

import unittest
from types import SimpleNamespace

from test_sequencer_ui import _reframe


class _ID:
    """Blender's user counting, the bits ``_copy_scene`` touches."""

    def __init__(self, name: str, users: int = 1, extra: bool = True) -> None:
        self.name = name
        self.users = users
        self._extra = extra
        self._fake = False
        self.props: dict = {}

    def _flag(self, attr: str, value: bool) -> None:
        if bool(value) != getattr(self, attr):
            self.users += 1 if value else -1
            setattr(self, attr, bool(value))

    use_extra_user = property(lambda self: self._extra, lambda self, v: self._flag("_extra", v))
    use_fake_user = property(lambda self: self._fake, lambda self, v: self._flag("_fake", v))

    def __setitem__(self, key, value):
        self.props[key] = value

    def get(self, key, default=None):
        return self.props.get(key, default)


class _Action(_ID):
    def copy(self) -> "_Action":
        return _Action(self.name + ".001", users=0, extra=False)


class _Scene(_ID):
    def __init__(self, name: str, action: _Action | None = None) -> None:
        super().__init__(name)
        self.animation_data = SimpleNamespace(action=action) if action is not None else None

    def copy(self) -> "_Scene":
        # rna_ID_copy: a copy with no user; the action is shared until we copy it.
        twin = _Scene(self.name + ".001")
        twin.users, twin._extra = 0, False
        twin.animation_data = (SimpleNamespace(action=self.animation_data.action)
                               if self.animation_data is not None else None)
        return twin


def _saved(idb: _ID) -> bool:
    """What Blender writes: IDs with at least one user."""
    return idb.users > 0


class TestVerticalCopyIsKept(unittest.TestCase):

    def test_the_copy_has_a_real_user_not_a_fake_one(self):
        rf = _reframe()
        edit = _Scene("Edit", action=_Action("EditAction"))
        copy = rf._copy_scene(edit)
        self.assertTrue(_saved(copy), "a 0-user scene is dropped on save (#537)")
        self.assertEqual(copy.users, 1)
        self.assertTrue(copy.use_extra_user)
        self.assertFalse(copy.use_fake_user, "a fake user would keep a copy the user deleted")
        self.assertEqual(copy.name, "Edit Vertical")
        self.assertEqual(copy.get(rf.VERTICAL_TAG), "Edit")
        # its own action, still (keys on its modifiers stay out of the 16:9 edit)
        self.assertIsNot(copy.animation_data.action, edit.animation_data.action)
        # the 16:9 edit is untouched
        self.assertEqual((edit.users, edit.use_extra_user, edit.use_fake_user), (1, True, False))

    def test_a_copy_without_animation(self):
        copy = _reframe()._copy_scene(_Scene("Edit"))
        self.assertTrue(_saved(copy))
        self.assertIsNone(copy.animation_data)

    def test_no_fake_user_and_no_user_clear_in_make_vertical(self):
        rf = _reframe()
        import inspect
        src = inspect.getsource(rf._copy_scene) + inspect.getsource(rf.make_vertical)
        self.assertNotIn("use_fake_user", src)
        self.assertNotIn("user_clear", src)


if __name__ == "__main__":
    unittest.main()
