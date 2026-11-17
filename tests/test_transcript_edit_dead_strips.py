# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for #593 (a strip taken before another strip is added or removed is dead afterwards (reading
it crashes Blender).  Stand-in strips here go invalid on *any* add or remove, and reading an
invalid one raises, so a loop that holds a strip across such a change fails in the test.

* ``_update_captions`` (Edit by Transcript, Apply) after a Word-by-word caption's text changed.
* ``captions_ops.text_changed`` / ``set_caption_text``: the line is looked up again after its word
  strips were removed.

Run with::

    python -m unittest tests.test_transcript_edit_dead_strips -v
"""

__all__ = ()

import importlib
import sys
import types
import unittest
from unittest import mock

import test_captions as C
import test_transcript_edit_review as R


class DeadStripError(ReferenceError):
    pass


class _Strip:
    """A handle: valid until the collection changes (then every handle taken before is dead)."""

    _OWN = ("_rec", "_coll", "_gen")

    def __init__(self, coll, rec):
        object.__setattr__(self, "_coll", coll)
        object.__setattr__(self, "_rec", rec)
        object.__setattr__(self, "_gen", coll.gen)

    def _check(self):
        if self._gen != self._coll.gen:
            raise DeadStripError("StructRNA of type Strip has been removed")

    def __getattr__(self, name):
        self._check()
        if name in self._rec:
            return self._rec[name]
        raise AttributeError(name)

    def __setattr__(self, name, value):
        self._check()
        self._rec[name] = value

    def get(self, key, default=None):
        self._check()
        return self._rec.get("props", {}).get(key, default)


class _Strips:
    def __init__(self):
        self.gen = 0
        self.records = []

    def add(self, name, **kw):
        rec = dict(name=name, type="TEXT", text="", left_handle=1, right_handle=60, props={})
        rec.update(kw)
        self.records.append(rec)
        self.gen += 1
        return _Strip(self, rec)

    def remove(self, strip):
        strip._check()
        self.records.remove(strip._rec)
        self.gen += 1

    def __iter__(self):
        return iter([_Strip(self, r) for r in self.records])

    def get(self, name, default=None):
        return next((_Strip(self, r) for r in self.records if r["name"] == name), default)


KEY = "media:c:/talk.mp4"


def _line(strips, name, left, right, text, style="WORD"):
    return strips.add(name, left_handle=left, right_handle=right, text=text,
                      props={"bfacw_caption": {"role": "line", "source": KEY, "style": style}})


def _word(strips, line, i):
    return strips.add("{:s}_w{:02d}".format(line, i), left_handle=1, right_handle=5,
                      props={"bfacw_caption": {"role": "word", "source": KEY, "line": line}})


class TestUpdateCaptionsHoldsNoDeadStrip(R._Rig):

    def _rig(self, lines=4, words=3):
        strips = _Strips()
        for n in range(lines):
            name = "cap_{:04d}".format(n + 1)
            _line(strips, name, 1 + n * 20, 21 + n * 20, "A B C D")
            for i in range(words):
                _word(strips, name, i + 1)
        self.strips = strips
        self.scene.sequence_editor = types.SimpleNamespace(strips=strips)
        calls = []

        def tag_of(strip):
            return strip.get("bfacw_caption")

        def set_caption_text(scene, strip, text):
            calls.append(strip.name)
            name = strip.name
            strip.text = text
            for rec in [r for r in strips.records if (r["props"].get("bfacw_caption") or {}).get("line") == name]:
                strips.remove(_Strip(strips, rec))      # what text_changed does: the word strips go ...
            for i in range(3):
                _word(strips, name, i + 1)              # ... and new ones come

        fake = types.ModuleType(self.ops.__package__ + ".captions_ops")
        fake.tag_of, fake.set_caption_text = tag_of, set_caption_text
        fake.cap = types.SimpleNamespace(TAG="bfacw_caption")
        sys.modules[fake.__name__] = fake
        self.addCleanup(sys.modules.pop, fake.__name__, None)
        resigned = []
        seqmod = types.ModuleType(self.ops.__package__ + ".wand.sequencer")
        seqmod._unedited = lambda strip: strip.type == "TEXT"
        seqmod._resign = lambda strip: resigned.append(strip.name)
        self.ops._seq = lambda: seqmod
        self.ops.te = types.SimpleNamespace(caption_text=lambda occ, words, retext, left, right: "A C D")
        self.calls, self.resigned = calls, resigned

    def test_every_changed_caption_is_rewritten_and_none_touches_a_dead_strip(self):
        self._rig()
        updated, kept = self.ops._update_captions(self.scene, {"source": "c:/talk.mp4"}, [], [], {}, set(),
                                                  [(1, 200)])
        self.assertEqual((updated, kept), (4, 0))
        self.assertEqual(self.calls, ["cap_0001", "cap_0002", "cap_0003", "cap_0004"])
        self.assertEqual(self.resigned, self.calls)
        self.assertEqual(sum(1 for r in self.strips.records if r["name"].endswith("_w03")), 4)

    def test_a_caption_that_went_away_meanwhile_is_skipped(self):
        self._rig(lines=2)
        fake = sys.modules[self.ops.__package__ + ".captions_ops"]
        inner = fake.set_caption_text

        def set_and_drop_the_next(scene, strip, text):
            inner(scene, strip, text)
            other = self.strips.get("cap_0002")
            if other is not None:
                self.strips.remove(other)

        fake.set_caption_text = set_and_drop_the_next
        updated, _kept = self.ops._update_captions(self.scene, {"source": "c:/talk.mp4"}, [], [], {}, set(),
                                                   [(1, 200)])
        self.assertEqual(updated, 1)

    def test_a_hand_edited_caption_is_kept_and_counted(self):
        self._rig(lines=2)
        seqmod = types.SimpleNamespace(_unedited=lambda strip: strip.name == "cap_0001", _resign=lambda s: None)
        self.ops._seq = lambda: seqmod
        updated, kept = self.ops._update_captions(self.scene, {"source": "c:/talk.mp4"}, [], [], {}, set(),
                                                  [(1, 200)])
        self.assertEqual((updated, kept), (1, 1))


class _Dead:
    """What a strip handle becomes when a strip is added or removed behind it."""

    def __getattribute__(self, name):
        raise DeadStripError("StructRNA of type Strip has been removed")

    def __setattr__(self, name, value):
        raise DeadStripError("StructRNA of type Strip has been removed")


def _regenerate(strips):
    """Every handle taken before is dead; the collection holds fresh ones for the same strips."""
    for i, old in enumerate(list(strips)):
        fresh = object.__new__(type(old))
        fresh.__dict__ = old.__dict__
        old.__class__ = _Dead
        strips[i] = fresh


class TestCaptionsOpsHoldNoDeadStrip(C._OpsCase):

    def _strict(self):
        """Adding a strip, or removing some (one call of ``remove_strips`` is one change), kills
        every handle taken before; the collection hands out fresh ones."""
        strips = self.scene.sequence_editor.strips
        add, remove = strips.new_effect, strips.remove

        def new_effect(*a, **k):
            _regenerate(strips)
            return add(*a, **k)

        def remove_strips(coll, doomed):
            doomed = list(doomed)
            for strip in doomed:
                remove(strip)
            _regenerate(coll)
            return len(doomed)

        strips.new_effect = new_effect
        prefetch = importlib.import_module(C._PKG + ".wand.prefetch")
        patch = mock.patch.object(prefetch, "remove_strips", remove_strips)
        patch.start()
        self.addCleanup(patch.stop)

    def test_a_translated_word_by_word_line_is_laid_out_without_a_dead_strip(self):
        with self._measured():
            clip = C._clip(self.scene)
            self.ops.add_captions(self.scene, [clip], "WORD_HIGHLIGHT", None, self.transcript)
            line = self.lines()[0]
            line.font = types.SimpleNamespace(filepath="Inter.woff2")
            name = line.name
            self._strict()
            self.ops.set_caption_text(self.scene, line, "Hola y bienvenidos")
        mine = [w for w in self.words() if w.get("bfacw_caption")["line"] == name]
        self.assertEqual([w.text for w in mine], ["Hola", "y", "bienvenidos"])

    def test_translating_every_word_by_word_caption_touches_no_dead_strip(self):
        with self._measured():
            clip = C._clip(self.scene)
            self.ops.add_captions(self.scene, [clip], "WORD_HIGHLIGHT", None, self.transcript)
            for line in self.lines():
                line.font = types.SimpleNamespace(filepath="Inter.woff2")
            lines = self.lines()
            path, _current, _what = self.ops.translate_request(self.scene, lines)
            reply = "\n".join("[{:d}] ES {:d}".format(i + 1, i + 1) for i in range(len(lines)))
            self._strict()
            written, missing = self.ops.apply_translation(path, reply)
        self.assertEqual((written, missing), (len(lines), 0))
        for line in self.lines():
            mine = [w.text for w in self.words() if w.get("bfacw_caption")["line"] == line.name]
            self.assertEqual(mine, line.text.split())


class TestPlayFromLineAndMakeCaptions(R._Rig):
    """#595: Play from Line starts playback (a second press stops it); Make Captions opens the dialog."""

    def setUp(self):
        super().setUp()
        self.log = []
        sequencer = types.SimpleNamespace(type='SEQUENCE_EDITOR', width=900, height=500,
                                          regions=[types.SimpleNamespace(type='HEADER'),
                                                   types.SimpleNamespace(type='WINDOW')])
        self.screen = types.SimpleNamespace(is_animation_playing=False, areas=[
            types.SimpleNamespace(type='TEXT_EDITOR', width=500, height=500, regions=[]), sequencer])
        self.window = types.SimpleNamespace(screen=self.screen)
        self.sequencer = sequencer

        def play():
            self.log.append(("play", self.overridden))
            self.screen.is_animation_playing = not self.screen.is_animation_playing
            return {'FINISHED'}

        self.overridden = None
        self.ctx = types.SimpleNamespace(
            window=self.window, screen=self.screen,
            temp_override=self._override)
        self.ops.bpy.ops = types.SimpleNamespace(
            screen=types.SimpleNamespace(animation_play=play),
            bfacw=types.SimpleNamespace(auto_captions=lambda *a, **k: self.log.append(("captions", a, k, self.overridden))
                                        or {'RUNNING_MODAL'}))
        self.ops.seek_line = lambda text, line: self.log.append(("seek", line)) or True
        self.ops.state_of = lambda text: {"scene": "Scene", "source": "c:/talk.mp4", "transcript": "t.json"}

    def _override(self, **kw):
        outer = self

        class _Ctx:
            def __enter__(self_):
                outer.overridden = kw.get("area")

            def __exit__(self_, *a):
                outer.overridden = None
                return False

        return _Ctx()

    def _seek(self, **kw):
        op = self.ops.BFACW_OT_transcript_seek()
        op.line, op.play, op.text_name = kw.get("line", 1), kw.get("play", True), ""
        op.text = lambda ctx: self.text
        op.report = lambda *a: self.log.append(("report", a))
        return op

    def test_play_from_line_moves_the_playhead_and_starts_playback_through_the_sequencer(self):
        self.assertEqual(self._seek().execute(self.ctx), {'FINISHED'})
        self.assertEqual(self.log, [("seek", 1), ("play", self.sequencer)])
        self.assertTrue(self.screen.is_animation_playing)

    def test_a_second_press_stops_playback_and_does_not_seek(self):
        self._seek().execute(self.ctx)
        self.log.clear()
        self.assertEqual(self._seek(line=2).execute(self.ctx), {'FINISHED'})
        self.assertEqual(self.log, [("play", self.sequencer)])
        self.assertFalse(self.screen.is_animation_playing)

    def test_a_flagged_line_only_moves_the_playhead(self):
        self._seek(play=False).execute(self.ctx)
        self.assertEqual(self.log, [("seek", 1)])

    def test_a_line_without_audio_plays_nothing(self):
        self.ops.seek_line = lambda text, line: False
        self.assertEqual(self._seek().execute(self.ctx), {'CANCELLED'})
        self.assertFalse(self.screen.is_animation_playing)

    def _captions_op(self):
        scene = types.SimpleNamespace(sequence_editor=types.SimpleNamespace(strips=[]))
        strips = [types.SimpleNamespace(name="talk", select=False), types.SimpleNamespace(name="other", select=True)]
        scene.sequence_editor.strips = strips
        self.ops.bpy.data.scenes = {"Scene": scene}
        self.ops.source_strips = lambda sc, source: [strips[0]]
        op = self.ops.BFACW_OT_transcript_make_captions()
        op.text_name = ""
        op.text = lambda ctx: self.text
        op.report = lambda *a: self.log.append(("report", a))
        return op, strips

    def test_make_captions_opens_the_auto_captions_dialog_over_the_sequencer(self):
        op, strips = self._captions_op()
        self.assertEqual(op.invoke(self.ctx, None), {'FINISHED'})
        self.assertEqual([(s.name, s.select) for s in strips], [("talk", True), ("other", False)])
        (kind, args, kwargs, area), = self.log
        self.assertEqual((kind, args, kwargs["transcript_path"], area), ("captions", ('INVOKE_DEFAULT',), "t.json",
                                                                          self.sequencer))

    def test_make_captions_without_a_sequencer_says_so(self):
        op, _strips = self._captions_op()
        self.screen.areas = [a for a in self.screen.areas if a.type != 'SEQUENCE_EDITOR']
        self.assertEqual(op.invoke(self.ctx, None), {'CANCELLED'})
        self.assertEqual(self.log[0][0], "report")


if __name__ == "__main__":
    unittest.main()
