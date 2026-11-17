# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for the review of Edit by Transcript (#498, PR #587): the Blender side
(``transcript_edit_ops.py``) on a stand-in ``bpy`` with the timeline stubbed (the real timeline
is in ``test_transcript_edit_timeline`` and the live runs).

* Live waits while a render runs or a clip plays, holds back a cut whose words are on the
  clipboard (Ctrl+X, the paste comes next), waits longer for whole lines, never applies a cut of
  more than half the clip.
* Remove Filler Words refuses while the text has edits of its own.
* The tool: ``cut_lines`` needs the version (or the lines) it was read from, is refused when the
  text moved on, is refused with ``text``; a dry run creates and writes nothing; a large cut needs
  ``confirm_large_cut``.
* Apply: the dialog for a large cut, a locked strip refuses before any cut, an exception after a
  cut says so, the captions refit after a move when the add-on has the refit.
* Drawing the panel and the playing line reads caches: no work in proportion to the transcript.

Run with::

    python -m unittest tests.test_transcript_edit_review -v
"""

__all__ = ()

import contextlib
import sys
import types
import unittest

import test_transcript_edit as T
import test_transcript_edit_ops as FB

TE = T.TE
FPS = T.FPS


class _Layout:
    """Takes any call of a layout and gives back itself."""

    def __getattr__(self, name):
        return lambda *a, **k: self

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


class _Rig(unittest.TestCase):

    def setUp(self):
        self.bpy = FB._fake_bpy()
        self.clock = [100.0]
        self.bpy.context = types.SimpleNamespace(
            window_manager=types.SimpleNamespace(clipboard=""),
            temp_override=lambda **kw: contextlib.nullcontext())
        self.bpy.app.is_job_running = lambda kind: False
        self.ops = FB._load(self.bpy)
        self.ops._SEEN.clear()
        self.ops.time = types.SimpleNamespace(monotonic=lambda: self.clock[0])
        self.words = T.WORDS
        self.pieces = [T.piece()]
        self.scene = types.SimpleNamespace(name="Scene", frame_start=1, frame_current=1, sequence_editor=None,
                                           render=types.SimpleNamespace(fps=FPS, fps_base=1.0))
        self.bpy.data.scenes = {"Scene": self.scene}
        self.occ = TE.timeline_words(self.words, self.pieces, FPS, 1)
        self.view = TE.render(self.occ, self.words, None, FPS)
        self.ops.view_now = self._view_now
        got = self._view_now({})
        self.state = {"schema": 1, "source": "c:/talk.mp4", "path": "c:/talk.mp4", "scene": "Scene", "strip": "talk",
                      "retext": "{}", "live": False, "follow": True, "transcript": "t.json"}
        self.ops._state_from_view(self.state, got)
        self.body = self.view["text"]
        self.state["applied"] = self.ops._hash(self.body)
        self.text = FB._Text("talk transcript", self.body, dict(self.state))
        self.text.current_line_index = 0
        self.text.select_end_line_index = 0
        self.calls = []

    def _view_now(self, _state):
        return {"scene": self.scene, "words": self.words, "pieces": self.pieces, "strips": [], "occ": self.occ,
                "view": self.view, "fps": float(FPS)}

    def lines(self):
        return self.body.splitlines()

    def edited(self, lines):
        return "\n".join(lines) + "\n"

    def tool_stubs(self, block="the text", dry_texts=None):
        ops = self.ops
        ops._tool_scene = lambda: self.scene
        ops._tool_strip = lambda scene, name: (types.SimpleNamespace(name="talk"), "")
        ops.strip_path = lambda strip: "c:/talk.mp4"
        ops.find_transcript = lambda media: "t.json"
        ops.text_for = lambda scene, source: self.text if block == "the text" else None

        def opened(scene, strip, path=""):
            self.calls.append("open_transcript")
            return self.text, ""
        ops.open_transcript = opened
        seq = types.SimpleNamespace(
            _push_undo=lambda what: self.calls.append("undo"),
            blocked_in=lambda scene, lo, hi: [],
            apply_cuts=lambda *a, **k: self.calls.append("apply_cuts") or {
                "status": "ok", "seconds_removed": 3.4, "cut_ranges": [], "new_names": {}, "end_frame": 1,
                "captions_trimmed": []})
        ops._seq = lambda: seq
        ops._update_captions = lambda *a, **k: (0, 0)
        ops._record = lambda *a, **k: None
        ops._store = lambda *a, **k: self.calls.append("store")
        return seq


# -- the tool ----------------------------------------------------------------------------------------

class TestTool(_Rig):

    def setUp(self):
        super().setUp()
        self.tool_stubs()

    def applied(self):
        got = []
        real = self.ops.apply_text

        def spy(text, body=None, **kw):
            got.append((body, kw))
            return {"status": "ok", "message": "ok", "text": "t"}
        self.ops.apply_text = spy
        self.addCleanup(setattr, self.ops, "apply_text", real)
        return got

    def test_8_cut_lines_need_the_version_they_were_read_from(self):
        got = self.applied()
        out = self.ops.tool_apply_transcript_edit(cut_lines=[4])
        self.assertEqual(out["status"], "error")
        self.assertIn("version", out["message"])
        self.assertEqual(got, [])
        out = self.ops.tool_apply_transcript_edit(cut_lines=[4], version=self.ops._hash(self.body))
        self.assertEqual(out["status"], "ok")
        self.assertNotIn("The price", got[0][0])
        self.assertIn("We talk", got[0][0])
        self.assertEqual(len(got), 1)

    def test_8_a_stale_version_is_refused_and_the_current_text_comes_back(self):
        got = self.applied()
        out = self.ops.tool_apply_transcript_edit(cut_lines=[4], version="0123456789abcdef")
        self.assertEqual(out["status"], "refused")
        self.assertEqual(out["text_now"], self.body)
        self.assertEqual(out["version"], self.ops._hash(self.body))
        self.assertEqual(got, [])

    def test_8_lines_with_the_text_that_was_read(self):
        got = self.applied()
        price = "[0:10.0] The price of pears went up again."
        self.assertIn(price, self.lines())
        out = self.ops.tool_apply_transcript_edit(lines=[{"line": 4, "text": "The price of pears went up again."}])
        self.assertEqual(out["status"], "ok")
        self.assertNotIn("The price", got[0][0])
        out = self.ops.tool_apply_transcript_edit(lines=[{"line": 4, "text": "Another sentence."}])
        self.assertEqual(out["status"], "refused")
        self.assertEqual(out["text_now"], self.body)
        self.assertEqual(len(got), 1)
        out = self.ops.tool_apply_transcript_edit(cut_lines=[4, 6], lines=[{"line": 4, "text": "The price of pears "
                                                                            "went up again."}])
        self.assertEqual(out["status"], "error")             # line 6 was not read
        self.assertEqual(len(got), 1)

    def test_8_cut_lines_and_text_together_are_refused(self):
        got = self.applied()
        out = self.ops.tool_apply_transcript_edit(text=self.body, cut_lines=[4], version=self.ops._hash(self.body))
        self.assertEqual(out["status"], "error")
        self.assertIn("not both", out["message"])
        self.assertEqual(got, [])
        out = self.ops.tool_apply_transcript_edit(text=self.body, lines=[{"line": 4, "text": "x"}])
        self.assertEqual(out["status"], "error")

    def test_8_a_line_that_is_not_there(self):
        self.applied()
        out = self.ops.tool_apply_transcript_edit(cut_lines=[40], version=self.ops._hash(self.body))
        self.assertEqual(out["status"], "error")
        self.assertIn("No line 40", out["message"])

    def test_a_dry_run_creates_nothing_and_writes_nothing(self):
        self.tool_stubs(block=None)
        self.bpy.data.texts = []
        out = self.ops.tool_apply_transcript_edit(cut_lines=[4], version=self.ops._hash(self.body), dry_run=True)
        self.assertEqual(out["status"], "ok", out)
        self.assertEqual(out["cut_words"], 7)
        self.assertAlmostEqual(out["seconds"], 3.0, delta=0.5)
        self.assertIn("Would cut 7 words", out["message"])
        self.assertEqual(self.calls, [])                      # no open_transcript, no store, no undo step
        self.assertEqual(self.bpy.data.texts, [])
        self.assertEqual(self.scene.__dict__.get("frame_current"), 1)

    def test_a_dry_run_on_an_open_text_writes_no_property(self):
        class Guard(FB._Text):
            armed = False

            def __setitem__(self, key, value):
                if self.armed:
                    raise AssertionError("a dry run wrote " + key)
                super().__setitem__(key, value)
        guard = Guard(self.text.name, self.body, dict(self.state))
        guard.armed = True
        self.text = guard
        out = self.ops.tool_apply_transcript_edit(text=self.body.replace("little ", ""), dry_run=True)
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["cut_words"], 1)
        self.assertEqual(self.calls, [])
        self.assertEqual(guard.as_string(), self.body)

    def test_9_a_large_cut_needs_the_confirm_argument(self):
        out = self.ops.tool_apply_transcript_edit(text="")
        self.assertEqual(out["status"], "refused")
        self.assertTrue(out["needs_confirm"])
        self.assertIn("confirm_large_cut", out["message"])
        self.assertEqual(out["percent"], 100)
        self.assertNotIn("apply_cuts", self.calls)
        self.assertNotIn("undo", self.calls)
        out = self.ops.tool_apply_transcript_edit(text=self.edited(self.lines()[:3]), dry_run=True)   # a dry run says it too
        self.assertEqual(out["status"], "ok")
        self.assertTrue(out["needs_confirm"])
        self.assertIn("asks first", out["message"])
        got = self.applied()
        self.ops.tool_apply_transcript_edit(text="", confirm_large_cut=True)
        self.assertTrue(got[0][1]["confirm"])
        self.ops.tool_apply_transcript_edit(text="x")
        self.assertFalse(got[1][1]["confirm"])

    def test_9_a_small_cut_goes_without_it(self):
        out = self.ops.tool_apply_transcript_edit(text=self.edited(self.lines()[:3] + self.lines()[4:]))
        self.assertEqual(out["status"], "ok", out)
        self.assertIn("apply_cuts", self.calls)

    def test_get_transcript_text_has_a_version(self):
        self.bpy.data.texts = []
        out = self.ops.tool_get_transcript_text()
        self.assertEqual(out["status"], "ok", out)
        self.assertEqual(out["version"], self.ops._hash(self.body))


# -- Apply -------------------------------------------------------------------------------------------

class TestApply(_Rig):

    def setUp(self):
        super().setUp()
        self.seq = self.tool_stubs()

    def moved_text(self):
        lines = self.lines()
        n = next(k for k, ln in enumerate(lines) if "The price" in ln)
        moved = lines.pop(n)
        lines.insert(0, moved)
        return self.edited(lines).replace(" little", "")

    def test_a_locked_strip_in_the_move_refuses_before_any_cut(self):
        self.seq.blocked_in = lambda scene, lo, hi: [("Music", "is locked")]
        out = self.ops.apply_text(self.text, self.moved_text())
        self.assertEqual(out["status"], "refused")
        self.assertEqual(out["refused"][0]["strip"], "Music")
        self.assertNotIn("apply_cuts", self.calls)             # nothing was cut: a refusal never comes after the cuts

    def test_the_whole_split_range_is_asked_not_only_the_words(self):
        asked = []
        self.seq.blocked_in = lambda scene, lo, hi: asked.append((lo, hi)) or []
        self.ops._moves = lambda *a, **k: {"status": "ok", "moves": 0, "spans": [], "new_names": {}}
        self.ops.apply_text(self.text, self.moved_text())
        (lo, hi), = asked
        first = self.occ[indices_of(self.occ, T.indices(2)[0])]
        self.assertLessEqual(lo, first["frame"] - 2)           # to the middle of the pause before the moved words
        pause_start = int(round(1 + self.words[T.indices(1)[-1]]["end"] * FPS))
        self.assertLessEqual(lo, pause_start + (first["frame"] - pause_start) // 2 + 1)
        self.assertGreaterEqual(hi, 1 + int(self.words[T.indices(2)[-1]]["end"] * FPS))

    def test_an_exception_after_a_cut_says_so_and_stays_one_undo_step(self):
        def boom(*a, **k):
            raise RuntimeError("disk gone")
        self.ops._moves = boom
        out = self.ops.apply_text(self.text, self.moved_text())
        self.assertEqual(out["status"], "error")
        self.assertTrue(out["changed"])                        # the operator finishes: its undo step takes it all back
        self.assertIn("Ctrl+Z", out["message"])
        self.assertIn("apply_cuts", self.calls)

    def test_an_exception_before_any_change_changes_nothing(self):
        def boom(*a, **k):
            raise RuntimeError("no words")
        self.ops._plan = boom
        out = self.ops.apply_text(self.text, self.body)
        self.assertEqual((out["status"], out["changed"]), ("error", False))
        self.assertIn("nothing was changed", out["message"])

    def test_captions_refit_after_a_move_when_the_addon_has_it(self):
        asked = []
        pkg = sys.modules[self.ops.__package__]
        fake = types.ModuleType(self.ops.__package__ + ".captions_ops")
        fake.refit_captions = lambda scene, names: asked.append(list(names)) or len(names)
        sys.modules[fake.__name__] = fake
        self.addCleanup(sys.modules.pop, fake.__name__, None)
        strips = {"cap_0003": types.SimpleNamespace(type="TEXT"), "cap_0003.001": types.SimpleNamespace(type="TEXT"),
                  "talk": types.SimpleNamespace(type="SOUND")}
        scene = types.SimpleNamespace(sequence_editor=types.SimpleNamespace(strips_all=types.SimpleNamespace(
            get=strips.get)))
        self.ops._refit_captions(scene, {"cap_0003": ["cap_0003", "cap_0003.001"], "talk": ["talk", "talk.001"]})
        self.assertEqual(asked, [["cap_0003", "cap_0003.001"]])
        del sys.modules[fake.__name__]
        if hasattr(pkg, "captions_ops"):
            delattr(pkg, "captions_ops")
        self.ops._refit_captions(scene, {"cap_0003": ["cap_0003"]})        # the refit isn't there: nothing, no error

    def test_the_apply_button_asks_before_a_large_cut(self):
        op = self.ops.BFACW_OT_transcript_apply()
        asked = []
        wm = types.SimpleNamespace(invoke_confirm=lambda *a, **k: asked.append(k) or {'RUNNING_MODAL'})
        context = types.SimpleNamespace(window_manager=wm)
        op.text = lambda ctx: self.text
        op.confirmed = False
        self.text.body = ""
        self.assertEqual(op.invoke(context, None), {'RUNNING_MODAL'})
        self.assertIn("100%", asked[0]["message"])
        self.assertIn("s (", asked[0]["message"])
        self.assertTrue(op.confirmed)
        reports = []
        op.report = lambda kind, msg: reports.append((kind, msg))
        self.assertEqual(op.execute(context), {'FINISHED'})        # confirmed: it goes ahead
        self.assertIn("apply_cuts", self.calls)
        self.assertFalse(op.confirmed)
        # a small cut is not asked about
        self.calls.clear()
        self.text.body = self.edited(self.lines()[:3] + self.lines()[4:])
        asked.clear()
        op.invoke(context, None)
        self.assertEqual(asked, [])
        self.assertIn("apply_cuts", self.calls)


def indices_of(occ, word):
    return next(k for k, o in enumerate(occ) if o["i"] == word)


# -- Live --------------------------------------------------------------------------------------------

class TestLive(_Rig):

    def setUp(self):
        super().setUp()
        self.window = types.SimpleNamespace(screen=types.SimpleNamespace(is_animation_playing=False))
        self.area = types.SimpleNamespace(tag_redraw=lambda: None)
        self.text.body = self.edited(self.lines()[:3] + self.lines()[4:])             # "The price" deleted
        self.state["live"] = True
        self.text["bfacw_transcript"] = dict(self.state, live=True)
        self.ops._transcript_areas = lambda: [(self.window, self.area, self.text)]
        self.applied = []
        self.ops.apply_text = lambda text, **kw: self.applied.append(kw) or {
            "status": kw.get("status", "ok"), "message": "done", "changed": True}
        self.ops._push_undo = lambda window, name: self.calls.append("undo")
        self.edit = TE.compute_edit(self.view["tokens"], self.view["order"], self.text.body, self.words, None,
                                    base_lines=self.view["lines"])
        self.ops._pending_edit = lambda text, state: self.edit
        self.tick()                                                                    # the timer sees the text change

    def tick(self, after=0.0):
        self.clock[0] += after
        return self.ops._tick_inner()

    def test_a_word_edit_goes_after_the_short_pause(self):
        self.edit = dict(self.edit, lines_cut=0)
        self.tick(1.0)
        self.assertEqual(self.applied, [])
        self.tick(0.6)
        self.assertEqual(len(self.applied), 1)
        self.assertTrue(self.applied[0]["keep_text"])

    def test_6_whole_lines_wait_longer(self):
        self.assertEqual(self.edit["lines_cut"], 1)
        self.tick(2.0)
        self.tick(1.9)
        self.assertEqual(self.applied, [])
        self.tick(0.3)                                                                 # 4.2 s
        self.assertEqual(len(self.applied), 1)

    def test_6_a_cut_whose_words_are_on_the_clipboard_is_held_back(self):
        self.bpy.context.window_manager.clipboard = "[0:10.0] The price of pears went up again.\n"
        self.tick(5.0)
        self.tick(5.0)
        self.assertEqual(self.applied, [])                                             # the paste comes next
        self.assertIn("clipboard", self.ops._SEEN[self.text.name]["live_note"])
        self.bpy.context.window_manager.clipboard = "something else"
        self.tick(1.0)
        self.assertEqual(len(self.applied), 1)                                         # no longer on it: it goes
        self.assertIsNone(self.ops._SEEN[self.text.name]["live_note"])

    def test_6_cut_then_paste_is_a_move_not_a_cut(self):
        lines = self.lines()
        moved = lines.pop(3)
        lines.insert(0, moved)
        self.text.body = self.edited(lines)
        self.edit = TE.compute_edit(self.view["tokens"], self.view["order"], self.text.body, self.words, None,
                                    base_lines=self.view["lines"])
        self.assertEqual((self.edit["cut"], self.edit["moved"]), ([], True))
        self.bpy.context.window_manager.clipboard = moved
        self.tick()                                                                    # the timer sees the paste
        self.tick(2.0)
        self.assertEqual(len(self.applied), 1)

    def test_6_a_render_running_or_a_clip_playing_holds_it_back(self):
        self.bpy.app.is_job_running = lambda kind: kind == "RENDER"
        self.tick(10.0)
        self.assertEqual(self.applied, [])
        self.assertIn("render", self.ops._SEEN[self.text.name]["live_note"])
        self.bpy.app.is_job_running = lambda kind: False
        self.window.screen.is_animation_playing = True
        self.tick(1.0)
        self.assertEqual(self.applied, [])
        self.window.screen.is_animation_playing = False
        self.tick(1.0)
        self.assertEqual(len(self.applied), 1)

    def test_9_live_never_applies_a_large_cut(self):
        self.ops.apply_text = lambda text, **kw: self.applied.append(kw) or {
            "status": "confirm", "message": "too much", "percent": 80}
        self.tick(5.0)
        self.assertEqual(len(self.applied), 1)
        self.assertFalse(self.applied[0].get("confirm", False))                        # it never asks for the cut
        self.assertNotIn("undo", self.calls)
        self.assertIn("80%", self.ops._SEEN[self.text.name]["live_note"])
        self.tick(5.0)
        self.assertEqual(len(self.applied), 1)                                         # and doesn't try again until the text changes


# -- Remove Filler Words ----------------------------------------------------------------------------

class TestFillers(_Rig):

    def make(self):
        op = self.ops.BFACW_OT_transcript_remove_fillers()
        op.text = lambda ctx: self.text
        self.reports = []
        op.report = lambda kind, msg: self.reports.append((kind, msg))
        op.phrases = False
        return op

    def test_7_pending_edits_refuse_it(self):
        op = self.make()
        self.text.body = self.body.replace("little ", "")                   # an edit of the artist's own, not applied
        applied = []
        self.ops.apply_text = lambda *a, **k: applied.append(1) or {"status": "ok", "message": "x"}
        wm = types.SimpleNamespace(invoke_props_dialog=lambda *a, **k: self.fail("no dialog"))
        self.assertEqual(op.invoke(types.SimpleNamespace(window_manager=wm), None), {'CANCELLED'})
        self.assertEqual(op.execute(types.SimpleNamespace(window_manager=wm)), {'CANCELLED'})
        self.assertEqual(applied, [])
        self.assertIn("Apply or undo your text edits first", self.reports[0][1])
        self.assertEqual(self.reports[0][0], {'ERROR'})

    def test_7_with_no_pending_edit_it_lists_and_applies_only_the_fillers(self):
        op = self.make()
        bodies = []
        self.ops.apply_text = lambda text, body=None, **k: bodies.append(body) or {"status": "ok", "message": "x"}
        self.assertEqual(op.execute(types.SimpleNamespace()), {'FINISHED'})
        self.assertNotIn("Um,", bodies[0])
        self.assertIn("The price of pears", bodies[0])
        edit = TE.compute_edit(self.view["tokens"], self.view["order"], bodies[0], self.words, None,
                               base_lines=self.view["lines"])
        self.assertEqual(edit["cut"], T.indices(3)[:1])


# -- drawing reads caches ----------------------------------------------------------------------------

class TestDrawing(_Rig):

    def setUp(self):
        super().setUp()
        self.reads = []
        text = self.text
        real = text.as_string
        text.as_string = lambda: self.reads.append(1) or real()
        self.text.body = self.body.replace("little ", "")
        self.ops._context_text = lambda context: self.text
        self.ops._wand = lambda *a, **k: None
        self.counts = {"compute": 0, "json": 0}
        real_compute = TE.compute_edit
        self.ops.te = types.SimpleNamespace(**{k: getattr(TE, k) for k in dir(TE) if not k.startswith("__")})
        self.ops.te.compute_edit = lambda *a, **k: self.counts.__setitem__("compute", self.counts["compute"] + 1) or \
            real_compute(*a, **k)
        real_j = self.ops._j
        self.ops._j = lambda *a: self.counts.__setitem__("json", self.counts["json"] + 1) or real_j(*a)

    def draw(self):
        panel = self.ops.BFACW_PT_transcript()
        panel.layout = _Layout()
        panel.draw(types.SimpleNamespace())

    def test_the_panel_draws_from_the_cache(self):
        state = self.ops.state_of(self.text)
        self.draw()                                           # the first draw reads the text once for its hash
        self.assertEqual(self.counts["compute"], 0)           # and computes no edit: the timer does
        self.assertEqual(len(self.reads), 1)
        self.ops._pending_edit(self.text, state)              # what the timer does after the typing stops
        before = (self.counts["compute"], self.counts["json"], len(self.reads))
        for _ in range(50):
            self.draw()
        self.assertEqual((self.counts["compute"], self.counts["json"], len(self.reads)), before)

    def test_the_playing_line_reads_nothing_again(self):
        self.text.body = self.body
        self.text["bfacw_transcript"] = dict(self.state)
        state = self.ops.state_of(self.text)
        self.scene.frame_current = 1 + int(6.5 * FPS)
        first = self.ops.playing_line(self.text, state)
        self.assertEqual(first, 2)
        before = (self.counts["json"], len(self.reads))
        for frame in range(1, 600, 7):
            self.scene.frame_current = frame
            self.ops.playing_line(self.text, state)
        self.assertEqual((self.counts["json"], len(self.reads)), before)
        self.scene.frame_current = 1 + int(10.5 * FPS)
        self.assertEqual(self.ops.playing_line(self.text, state), 3)


if __name__ == "__main__":
    unittest.main()
