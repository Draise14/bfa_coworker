# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for #536: strips are removed with the Sequencer's prefetch stopped first.

1. ``wand/prefetch.py`` (bpy-free): prefetch is stopped (``Strip.invalidate_cache``) once,
   before the first strip is removed; nothing to remove or no collection (no sequence
   editor) does nothing; a strip that can't be asked is still removed; an error in the
   removal reaches the caller and the editor's Prefetch Frames setting is never written.
2. Every strip removal in the add-on goes through it: no ``strips.remove`` /
   ``top.remove`` call outside the helper.

Run with::

    python -m unittest tests.test_strip_prefetch_536 -v
"""

__all__ = ()

import re
import unittest
from pathlib import Path

import test_wand_remove_silences as W

P = W._import("wand.prefetch")
_ADDON = Path(__file__).resolve().parents[1] / "addon" / "bfa_coworker"


class _Editor:
    """A sequence editor stand-in that records every write to ``use_prefetch``."""

    def __init__(self, on=True):
        self.__dict__["_on"] = on
        self.__dict__["writes"] = []

    @property
    def use_prefetch(self):
        return self._on

    @use_prefetch.setter
    def use_prefetch(self, value):
        self.writes.append(value)
        self.__dict__["_on"] = value


class _Strip:
    def __init__(self, name, log, fail=None):
        self.name, self.log, self.fail = name, log, fail

    def invalidate_cache(self, kind):
        if self.fail:
            raise self.fail
        self.log.append(("stop", self.name, kind))


class _Strips:
    """``ed.strips`` stand-in: remove() records, or raises for one name."""

    def __init__(self, log, editor=None, raise_on=None):
        self.log, self.editor, self.raise_on = log, editor, raise_on

    def remove(self, strip):
        if strip.name == self.raise_on:
            raise RuntimeError("Strip not found")
        self.log.append(("remove", strip.name))


class TestRemoveStrips(unittest.TestCase):

    def test_prefetch_stopped_once_before_the_first_removal(self):
        log = []
        doomed = [_Strip("cap_0001", log), _Strip("cap_0002", log), _Strip("cap_0003", log)]
        n = P.remove_strips(_Strips(log), doomed)
        self.assertEqual(n, 3)
        self.assertEqual(log, [("stop", "cap_0001", "RAW"), ("remove", "cap_0001"),
                               ("remove", "cap_0002"), ("remove", "cap_0003")])

    def test_a_generator_of_strips(self):
        log = []
        n = P.remove_strips(_Strips(log), (_Strip(n, log) for n in ("a", "b")))
        self.assertEqual(n, 2)
        self.assertEqual(log[0], ("stop", "a", "RAW"))

    def test_nothing_to_remove_does_nothing(self):
        log = []
        self.assertEqual(P.remove_strips(_Strips(log), []), 0)
        self.assertEqual(log, [])

    def test_no_sequence_editor_does_nothing(self):
        log = []
        self.assertEqual(P.remove_strips(None, [_Strip("a", log)]), 0)
        self.assertEqual(log, [])

    def test_a_strip_that_cant_be_asked_is_still_removed(self):
        for err in (ReferenceError("removed"), RuntimeError("no"), AttributeError("old"), TypeError("enum")):
            log = []
            doomed = [_Strip("a", log, fail=err), _Strip("b", log)]
            self.assertEqual(P.remove_strips(_Strips(log), doomed), 2, err)
            self.assertEqual(log, [("remove", "a"), ("remove", "b")])

    def test_no_invalidate_cache_at_all(self):
        log = []

        class Bare:
            name = "bare"
        self.assertFalse(P.stop_prefetch(Bare()))
        self.assertEqual(P.remove_strips(_Strips(log), [Bare()]), 1)
        self.assertEqual(log, [("remove", "bare")])

    def test_error_in_the_removal_reaches_the_caller_setting_untouched(self):
        for on in (True, False):
            log = []
            ed = _Editor(on)
            doomed = [_Strip("a", log), _Strip("b", log), _Strip("c", log)]
            with self.assertRaises(RuntimeError):
                P.remove_strips(_Strips(log, ed, raise_on="b"), doomed)
            self.assertEqual(log, [("stop", "a", "RAW"), ("remove", "a")])
            self.assertIs(ed.use_prefetch, on)
            self.assertEqual(ed.writes, [])

    def test_stop_prefetch_answer(self):
        log = []
        self.assertTrue(P.stop_prefetch(_Strip("a", log)))
        self.assertFalse(P.stop_prefetch(_Strip("a", log, fail=ReferenceError("gone"))))

    def test_module_needs_no_bpy(self):
        src = (_ADDON / "wand" / "prefetch.py").read_text(encoding="utf-8")
        self.assertNotRegex(src, r"(?m)^\s*import bpy|^\s*from bpy")
        self.assertNotIn("use_prefetch =", src)


class TestEveryRemovalUsesIt(unittest.TestCase):
    """No strip collection's ``remove`` is called outside the helper."""

    _CALL = re.compile(r"\b(?:top|strips|sequences|seqs)\.remove\(")

    def test_no_bare_strip_removal(self):
        found = []
        for path in sorted(_ADDON.rglob("*.py")):
            if path.name == "prefetch.py" and path.parent.name == "wand":
                continue
            for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                code = line.split("#", 1)[0]
                if self._CALL.search(code) and "``" not in code:
                    found.append("{:s}:{:d}: {:s}".format(path.relative_to(_ADDON).as_posix(), i, line.strip()))
        self.assertEqual(found, [])

    def test_the_known_sites_call_it(self):
        caps = (_ADDON / "captions_ops.py").read_text(encoding="utf-8")
        self.assertEqual(caps.count("remove_strips(top, "), 3)        # re-run, relayout, word strips
        seq = (_ADDON / "wand" / "sequencer.py").read_text(encoding="utf-8")
        body = seq[seq.index("def _cut_member("):]
        body = body[:body.index("\ndef ")]
        self.assertIn("remove_strips(strips, [middle])", body)         # Remove Silences
        # Strip.split() can free strips as well: prefetch is stopped before the first split.
        self.assertLess(body.index("stop_prefetch(strip)"), body.index("_split("))


if __name__ == "__main__":
    unittest.main()
