# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for Highlights H4 (#614): the picks on the scene, placing them again after a cut, Preview's range,
the markers, and the panel's operators.

1. ``highlight_picks.py`` (bpy-free): the store survives a round trip and any damage (garbage, a wrong
   version, a pick without words, too many picks, a huge text); picks from H3's result keep the words as
   their identity.
2. Placing again, on sentences made by the real scene reader from made-up edits: unchanged, moved, the
   part before it cut away, a cut inside it (changed), cut away whole (gone), a clip used twice, a
   second speaker taking turns.
3. The marker plan (only markers the store made are touched), the preview range, the engine line.
4. ``highlight_picks_ops.py`` with a stand-in ``bpy``: the scene store, ``check`` (placing again and saving),
   the markers switch, Dismiss, Preview refusing a stale pick and setting then restoring the range, Find's
   refusals; the operators are registered and taken out again; the tooltips and the panel rules hold
   (the AST tests of #607 and #610 also walk this panel).

Run with::

    python -m unittest tests.test_highlight_picks_614 -v
"""

__all__ = ()

import json
import sys
import threading
import types
import unittest

import test_scene_reader_611 as R
import test_wand_remove_silences as W

HP = W._import("highlight_picks")
FPS = R.FPS
KEY = "a" * 16
OTHER = "b" * 16

TALK = [(1.0, "Welcome to the show everybody."), (6.0, "Today we talk about the old rig."),
        (12.0, "We threw away six months of work."), (18.0, "That sounded wrong but it was right."),
        (25.0, "The new one is twice as fast."), (31.0, "Thanks for listening to this."),
        (40.0, "See you next week then.")]


def sentences_of(*sources):
    return R.SR.read_scene(list(sources), FPS)["sentences"]


def one_file(*pieces, said=TALK):
    return R.source(KEY, R.talk(said), *pieces)


def pick_of(sents, first, last, **extra):
    """H3's result for sentences ``first`` to ``last`` (numbers), as ``find_highlights`` returns it."""
    a, b = sents[first - 1], sents[last - 1]
    return dict({"first": first, "last": last, "start": a["start"], "end": b["end"], "seconds": b["seconds"],
                 "title": "The rig we threw away", "hook": "Six months, gone.", "reason": "A story with a payoff.",
                 "scores": {"hook": 3, "alone": 2}, "total": 71.5, "kind": "podcast",
                 "first_word": dict(a["first"]), "last_word": dict(b["last"])}, **extra)


def store_with(sents, *ranges):
    store = HP.empty()
    store["picks"] = HP.from_found([pick_of(sents, a, b) for a, b in ranges], sents)
    return store


class TestStore(unittest.TestCase):

    def setUp(self):
        self.sents = sentences_of(one_file(R.piece("A")))

    def test_round_trip(self):
        store = store_with(self.sents, (3, 4))
        store["markers"] = ["HL1: x", "HL1: end"]
        store["markers_on"] = True
        store["found"] = {"edit": "abc", "seconds": 60}
        back = HP.loads(HP.dumps(store))
        self.assertEqual(back, store)
        pick = back["picks"][0]
        self.assertEqual((pick["first_word"], pick["last_word"]), ({"media": KEY, "i": 12}, {"media": KEY, "i": 25}))
        self.assertEqual(pick["state"], "ok")
        self.assertEqual(pick["sig"], R.SR.version(self.sents))

    def test_what_the_scene_keeps_is_plain_json_text(self):
        text = HP.dumps(store_with(self.sents, (3, 4)))
        self.assertIsInstance(text, str)
        self.assertEqual(json.loads(text)["v"], 1)

    def test_damage_gives_an_empty_store(self):
        for bad in (None, "", "not json", "[]", '{"v": 2, "picks": []}', '{"picks": []}', 5, "x" * 70000):
            self.assertEqual(HP.loads(bad), HP.empty(), repr(bad)[:30])

    def test_a_pick_that_is_not_well_formed_is_left_out(self):
        good = store_with(self.sents, (3, 4))["picks"][0]
        text = json.dumps({"v": 1, "picks": [
            good, "x", {}, dict(good, id="n1", first_word=None), dict(good, id="n2", end=good["start"]),
            dict(good, id="n3", start="soon"), dict(good, id="n4", last_word={"media": "", "i": 3}),
            dict(good, id="n5", first_word={"media": KEY, "i": True}), dict(good, id="ok2", state="weird", title="  A  b ")]})
        out = HP.loads(text)["picks"]
        self.assertEqual([p["id"] for p in out], [good["id"], "ok2"])
        self.assertEqual(out[1]["state"], "ok")
        self.assertEqual(out[1]["title"], "A b")

    def test_a_pick_has_no_duplicate_id_and_there_are_at_most_thirty(self):
        good = store_with(self.sents, (3, 4))["picks"][0]
        out = HP.loads(json.dumps({"v": 1, "picks": [good, good]}))["picks"]
        self.assertEqual(len(out), 1)
        many = [dict(good, id="p{:d}".format(k)) for k in range(50)]
        self.assertEqual(len(HP.loads(json.dumps({"v": 1, "picks": many}))["picks"]), HP.MAX_PICKS)

    def test_long_texts_are_cut(self):
        good = store_with(self.sents, (3, 4))["picks"][0]
        out = HP.loads(json.dumps({"v": 1, "picks": [dict(good, title="t" * 500, hook="h" * 500, reason="r" * 900)]}))
        pick = out["picks"][0]
        self.assertEqual((len(pick["title"]), len(pick["hook"]), len(pick["reason"])), (60, 110, 220))

    def test_from_found_takes_the_frames_of_the_sentences_and_skips_unknown_numbers(self):
        got = HP.from_found([pick_of(self.sents, 2, 3), {"first": 99, "last": 100}], self.sents)
        self.assertEqual(len(got), 1)
        self.assertEqual((got[0]["start"], got[0]["end"]), (self.sents[1]["start"], self.sents[2]["end"]))
        self.assertEqual(len(got[0]["id"]), 6)


class TestPlacing(unittest.TestCase):

    def _again(self, store, *sources):
        sents = sentences_of(*sources)
        counts = HP.place_all(store, sents, FPS)
        return sents, counts

    def setUp(self):
        self.before = sentences_of(one_file(R.piece("A")))
        self.store = store_with(self.before, (3, 4))
        self.pick = self.store["picks"][0]
        self.start0 = self.pick["start"]

    def test_unchanged_stays_put_without_looking_again(self):
        sents, counts = self._again(self.store, one_file(R.piece("A")))
        self.assertEqual(counts, {"ok": 1, "changed": 0, "gone": 0, "moved": 0})
        self.assertEqual(self.pick["start"], self.start0)

    def test_a_moved_strip_gives_new_frames(self):
        # the whole clip plays 100 frames later
        sents, counts = self._again(self.store, one_file(R.piece("A", left=101, cs=101.0)))
        self.assertEqual((counts["ok"], counts["moved"]), (1, 1))
        self.assertEqual(self.pick["state"], "ok")
        self.assertEqual(self.pick["start"], self.start0 + 100)
        self.assertEqual(self.pick["sig"], R.SR.version(sents))

    def test_the_beginning_cut_away_moves_the_pick_earlier_and_the_sentence_numbers_change(self):
        # the first 11 s are gone: sentences 1 and 2 no longer play, the rest moves to the start
        sents, counts = self._again(self.store, one_file(R.piece("A", left=1, seconds=60, cs=1.0 - 11 * FPS)))
        self.assertEqual(sents[0]["text"], "We threw away six months of work.")
        self.assertEqual(counts["ok"], 1)
        self.assertEqual(self.pick["state"], "ok")
        self.assertLess(self.pick["start"], self.start0)
        self.assertEqual((self.pick["start"], self.pick["end"]), (sents[0]["start"], sents[1]["end"]))

    def test_a_cut_inside_the_pick_is_changed(self):
        # seconds 14 to 17 removed from the middle of sentence 3 / 4: the second part moves up
        words = R.talk(TALK)
        pieces = [R.piece("A1", left=1, seconds=14, cs=1.0),
                  R.piece("A2", left=1 + 14 * FPS, seconds=40, cs=1.0 + 20 * FPS)]
        sents, counts = self._again(self.store, R.source(KEY, words, *pieces))
        self.assertEqual((counts["changed"], counts["ok"]), (1, 0))
        self.assertEqual(self.pick["state"], "changed")

    def test_the_end_cut_away_is_changed_and_the_whole_pick_cut_away_is_gone(self):
        sents, counts = self._again(self.store, one_file(R.piece("A", seconds=20)))
        self.assertEqual(self.pick["state"], "changed")
        self.assertEqual(counts["changed"], 1)
        store = store_with(self.before, (6, 7))
        sents = sentences_of(one_file(R.piece("A", seconds=20)))
        self.assertEqual(HP.place_all(store, sents, FPS)["gone"], 1)
        self.assertEqual(store["picks"][0]["state"], "gone")
        self.assertEqual(HP.state_text("gone"), "No longer in the edit")

    def test_a_changed_pick_that_is_whole_again_comes_back(self):
        self._again(self.store, one_file(R.piece("A", seconds=20)))
        self.assertEqual(self.pick["state"], "changed")
        self._again(self.store, one_file(R.piece("A")))
        self.assertEqual(self.pick["state"], "ok")

    def test_a_clip_used_twice_takes_the_use_nearest_to_where_it_was(self):
        two = one_file(R.piece("one", left=1, seconds=60, cs=1.0),
                       R.piece("two", left=1 + 100 * FPS, seconds=60, cs=1.0 + 100 * FPS))
        sents = sentences_of(two)
        store = store_with(sents, (10, 11))                      # in the second use
        pick = store["picks"][0]
        near = pick["start"]
        self.assertGreater(near, 100 * FPS)
        # the second use moves by 50 frames, a new first use is added elsewhere: still the second one
        moved = one_file(R.piece("one", left=1, seconds=60, cs=1.0),
                         R.piece("two", left=1 + 150 * FPS, seconds=60, cs=1.0 + 150 * FPS))
        HP.place_all(store, sentences_of(moved), FPS)
        self.assertEqual(pick["state"], "ok")
        self.assertEqual(pick["start"], near + 50 * FPS)

    def test_a_second_speaker_taking_turns_does_not_break_a_pick(self):
        ana = R.source(KEY, R.talk(TALK), R.piece("Ana", speaker="Ana"))
        bo = R.source(OTHER, R.talk([(15.0, "Really, six months?")]), R.piece("Bo", channel=2, speaker="Bo"))
        sents = sentences_of(ana, bo)
        store = store_with(sents, (3, 5))
        self.assertEqual(sents[3]["speaker"], "Bo")
        counts = HP.place_all(store, sentences_of(ana, R.source(OTHER, R.talk([(15.0, "Really, six months?")]),
                                                                  R.piece("Bo", left=51, channel=2, speaker="Bo", cs=1.0))), FPS)
        self.assertEqual(store["picks"][0]["state"], "ok", counts)

    def test_a_word_missing_inside_a_sentence_is_changed(self):
        sents = sentences_of(one_file(R.piece("A")))
        thin = [dict(s) for s in sents]
        s3 = dict(thin[2], words=thin[2]["words"] - 2)           # two words of sentence 3 are gone
        thin[2] = s3
        state, _a, _b = HP.place(self.pick, thin)
        self.assertEqual(state, "changed")


class TestMarkersPreviewAndWords(unittest.TestCase):

    def setUp(self):
        self.sents = sentences_of(one_file(R.piece("A")))
        self.store = store_with(self.sents, (2, 3), (5, 6))

    def test_marker_names(self):
        a, b = HP.marker_names(1, self.store["picks"][0])
        self.assertEqual((a, b), ("HL1: The rig we threw away", "HL1: end"))

    def test_on_adds_a_pair_per_pick_and_off_takes_only_ours(self):
        mine = [("My own", 5), ("HL1: end", 99)]
        remove, names, add = HP.marker_plan(self.store, mine, True)
        self.assertEqual((remove, len(names), len(add)), ([], 4, 4))
        self.store["markers"] = names
        existing = [("My own", 5)] + [(n, f) for n, f in add]
        remove, names2, add2 = HP.marker_plan(self.store, existing, False)
        self.assertEqual(sorted(n for n, _f in remove), sorted(names))
        self.assertEqual((names2, add2), ([], []))

    def test_a_gone_pick_gets_no_marker(self):
        self.store["picks"][0]["state"] = "gone"
        _remove, names, add = HP.marker_plan(self.store, [], True)
        self.assertEqual((len(names), len(add)), (2, 2))
        self.assertTrue(names[0].startswith("HL1: "))

    def test_markers_are_replaced_not_doubled(self):
        _r, names, add = HP.marker_plan(self.store, [], True)
        self.store["markers"] = names
        remove, names_b, add_b = HP.marker_plan(self.store, add, True)
        self.assertEqual(sorted(n for n, _f in remove), sorted(names))
        self.assertEqual(names_b, names)

    def test_preview_range_is_the_pick_inside_the_scene(self):
        pick = {"start": 100, "end": 400}
        self.assertEqual(HP.preview_range(pick, 1, 1000), (100, 399))
        self.assertEqual(HP.preview_range(pick, 150, 300), (150, 300))
        self.assertEqual(HP.preview_range({"start": 5, "end": 6}, 1, 1000), (5, 5))

    def test_engine_line_says_where_the_transcript_goes(self):
        text, kind = HP.engine_line("local")
        self.assertEqual(kind, "local")
        self.assertIn("Nothing leaves", text)
        text, kind = HP.engine_line("remote", "api.example.com")
        self.assertEqual((kind, "api.example.com" in text), ("remote", True))
        self.assertIn("sent", text)
        text, kind = HP.engine_line("external")
        self.assertEqual(kind, "external")
        self.assertIn("agent", text)       # the search is handed to the agent (#616): the line says where the text goes
        self.assertNotIn("Nothing leaves", text)

    def test_the_privacy_line_follows_the_decision_the_search_acts_on(self):
        aj = W._import("ask_json")
        cfg = types.SimpleNamespace(mode="remote")
        for mode, config, kind in (("LOCAL_LLM", types.SimpleNamespace(mode="local"), "local"),
                                   ("REMOTE_API", types.SimpleNamespace(mode="local"), "remote"),
                                   ("LOCAL_LLM", cfg, "remote"), ("", types.SimpleNamespace(mode="local"), "local"),
                                   ("EXTERNAL_AGENT", cfg, "external"), ("EXTERNAL_HARNESS", cfg, "external")):
            self.assertEqual(aj.engine_kind(mode, config), kind, (mode, config))
            if kind == "external":
                with self.assertRaises(aj.Unavailable):
                    aj.engine_target(mode, config, True)
            else:
                aj.engine_target(mode, types.SimpleNamespace(mode=config.mode, remote_api_url="http://x", local_port=1),
                                 True)

    def test_clock_and_summary(self):
        self.assertEqual(HP.clock(30 * 42, 30), "0:42")
        self.assertEqual(HP.clock(30 * 3723, 30), "1:02:03")
        text = HP.summary(self.store, FPS, 1)
        self.assertIn("2 highlights found", text)
        self.assertIn("Highlights panel", text)
        self.assertEqual(HP.summary(HP.empty(), FPS), "No highlights found.")


# -- the operators with a stand-in bpy ----------------------------------------------------------------

class _Markers(list):
    def new(self, name, frame=0):
        m = types.SimpleNamespace(name=name, frame=frame)
        self.append(m)
        return m


class _Scene(dict):
    """A scene with the parts the panel's code touches."""

    def __init__(self, name="Scene"):
        super().__init__()
        self.name = name
        self.timeline_markers = _Markers()
        self.sequence_editor = types.SimpleNamespace(meta_stack=[], strips_all=[])
        self.frame_start, self.frame_end, self.frame_current = 1, 3000, 1
        self.use_preview_range, self.frame_preview_start, self.frame_preview_end = False, 1, 250
        self.render = types.SimpleNamespace(fps=FPS, fps_base=1.0)


def _fake_bpy():
    bpy = W._fake_bpy(False)
    bpy.props.EnumProperty = bpy.props.IntProperty = bpy.props.StringProperty = bpy.props.BoolProperty = lambda **kw: ("prop", kw)
    timers = {}
    bpy.app = types.SimpleNamespace(timers=types.SimpleNamespace(
        register=lambda fn, first_interval=0.0, persistent=False: timers.__setitem__(fn, (first_interval, persistent)),
        unregister=lambda fn: timers.pop(fn, None), is_registered=lambda fn: fn in timers), handlers=types.SimpleNamespace(load_pre=[], frame_change_post=[]))
    bpy.timers = timers
    bpy.data = types.SimpleNamespace(scenes={})
    bpy.context = types.SimpleNamespace(window_manager=None, preferences=types.SimpleNamespace(addons={}))
    return bpy


def _load_ops(bpy):
    names = ("bpy", "bpy.props", "bpy.types")
    saved = {n: sys.modules.get(n) for n in names}
    sys.modules.update({"bpy": bpy, "bpy.props": bpy.props, "bpy.types": bpy.types})
    try:
        W._package()
        for mod in ("highlight_picks_ops", "wand.sequencer", "wand.scene"):
            sys.modules.pop(W._PKG + "." + mod, None)
        return W._import("highlight_picks_ops")
    finally:
        for n, m in saved.items():
            if m is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = m


class _Op:
    def __init__(self, pick=""):
        self.pick = pick
        self.reports = []

    def report(self, level, text):
        self.reports.append((sorted(level)[0], text))


class TestOperators(unittest.TestCase):

    def setUp(self):
        self.bpy = _fake_bpy()
        self.ops = _load_ops(self.bpy)
        self.scene = _Scene()
        self.bpy.data.scenes["Scene"] = self.scene
        self.sents = sentences_of(one_file(R.piece("A")))
        self.reader_sents = self.sents
        self.reader = lambda scene, context=None: {"sentences": self.reader_sents, "report": {"messages": ["No speech."]},
                                                   "windows": []}
        self._patch(self.ops.scene_reader_ops, "read", self.reader)
        self._patch(self.ops._wand, "_push_undo", lambda name: None)
        self.ctx = types.SimpleNamespace(sequencer_scene=self.scene, scene=self.scene, workspace=None,
                                         space_data=None, region=None, window_manager=None)
        store = store_with(self.sents, (3, 4), (6, 7))
        store["found"]["edit"] = self.ops.edit_signature(self.scene)
        self.ops.save(self.scene, store)
        self.ids = [p["id"] for p in store["picks"]]

    def _patch(self, obj, name, value):
        old = getattr(obj, name)
        setattr(obj, name, value)
        self.addCleanup(setattr, obj, name, old)

    def test_the_picks_are_one_custom_property_of_the_scene(self):
        self.assertIsInstance(self.scene[HP.PROP], str)
        self.assertEqual([p["id"] for p in self.ops.load(self.scene)["picks"]], self.ids)
        self.assertEqual(self.ops.load(_Scene())["picks"], [])

    def test_check_places_them_again_and_saves(self):
        self.reader_sents = sentences_of(one_file(R.piece("A", left=101, cs=101.0)))
        counts, why = self.ops.check(self.scene)
        self.assertEqual((counts["moved"], why), (2, ""))
        self.assertEqual(self.ops.load(self.scene)["picks"][0]["start"], self.sents[2]["start"] + 100)

    def test_check_with_no_speech_keeps_the_picks(self):
        self.reader_sents = []
        counts, why = self.ops.check(self.scene)
        self.assertIsNone(counts)
        self.assertIn("No speech", why)
        self.assertEqual([p["state"] for p in self.ops.load(self.scene)["picks"]], ["ok", "ok"])

    def test_markers_on_then_off_in_one_click_each(self):
        op = self.ops.BFACW_OT_highlights_markers()
        op.report = lambda level, text: None
        self.scene.timeline_markers.new("Mine", frame=7)
        self.assertEqual(op.execute(self.ctx), {'FINISHED'})
        names = [m.name for m in self.scene.timeline_markers]
        self.assertEqual(len(names), 5)
        self.assertTrue(self.ops.load(self.scene)["markers_on"])
        op.execute(self.ctx)
        self.assertEqual([m.name for m in self.scene.timeline_markers], ["Mine"])
        self.assertFalse(self.ops.load(self.scene)["markers_on"])

    def test_markers_are_off_by_default(self):
        self.assertFalse(self.ops.load(self.scene)["markers_on"])
        self.assertEqual(len(self.scene.timeline_markers), 0)

    def test_dismiss_removes_the_pick_and_its_markers(self):
        mk = self.ops.BFACW_OT_highlights_markers()
        mk.report = lambda *a: None
        mk.execute(self.ctx)
        op = self.ops.BFACW_OT_highlights_dismiss()
        op.pick = self.ids[0]
        op.report = lambda *a: None
        self.assertEqual(op.execute(self.ctx), {'FINISHED'})
        self.assertEqual([p["id"] for p in self.ops.load(self.scene)["picks"]], [self.ids[1]])
        self.assertEqual(len(self.scene.timeline_markers), 2)
        op.pick = "nothing"
        self.assertEqual(op.execute(self.ctx), {'CANCELLED'})

    def test_preview_of_a_stale_pick_is_refused_with_the_reason(self):
        self.reader_sents = sentences_of(one_file(R.piece("A", seconds=20)))
        op = self.ops.BFACW_OT_highlights_preview()
        op.pick = self.ids[0]
        op.reports = []
        op.report = lambda level, text: op.reports.append((sorted(level)[0], text))
        self.assertEqual(op.execute(self.ctx), {'CANCELLED'})
        self.assertEqual(op.reports[0][0], "WARNING")
        self.assertIn("changed", op.reports[0][1])
        self.assertFalse(self.scene.use_preview_range)

    def test_preview_sets_the_range_plays_and_puts_the_range_back(self):
        played = []
        self.bpy.ops = types.SimpleNamespace(screen=types.SimpleNamespace(
            animation_play=lambda: played.append("play"), animation_cancel=lambda restore_frame=True: played.append("cancel")))
        win = types.SimpleNamespace(screen=types.SimpleNamespace(is_animation_playing=False, areas=[]))
        self.bpy.context = types.SimpleNamespace(window_manager=types.SimpleNamespace(windows=[win]),
                                                 temp_override=lambda **kw: _Ctx(),
                                                 preferences=types.SimpleNamespace(addons={}))
        self.ops.bpy.context = self.bpy.context
        op = self.ops.BFACW_OT_highlights_preview()
        op.pick = self.ids[0]
        op.report = lambda *a: None
        self.assertEqual(op.execute(self.ctx), {'FINISHED'})
        pick = self.ops.load(self.scene)["picks"][0]
        self.assertTrue(self.scene.use_preview_range)
        self.assertEqual((self.scene.frame_preview_start, self.scene.frame_preview_end), (pick["start"], pick["end"] - 1))
        self.assertEqual(self.scene.frame_current, pick["start"])
        self.assertEqual(played, ["play"])
        self.assertIn(self.ops._preview_tick, self.bpy.timers)
        # still playing before the end: the tick goes on and keeps the range
        win.screen.is_animation_playing = True
        self.scene.frame_current = pick["start"] + 10
        self.assertEqual(self.ops._preview_tick(), self.ops.TICK)
        self.assertTrue(self.scene.use_preview_range)
        # it reaches the end: playback is stopped and the range is back
        self.scene.frame_current = pick["end"] - 1
        self.assertIsNone(self.ops._preview_tick())
        self.assertEqual(played, ["play", "cancel"])
        self.assertEqual((self.scene.use_preview_range, self.scene.frame_preview_start, self.scene.frame_preview_end),
                         (False, 1, 250))

    def test_a_second_preview_keeps_the_first_original_range(self):
        self.bpy.ops = types.SimpleNamespace(screen=types.SimpleNamespace(
            animation_play=lambda: None, animation_cancel=lambda restore_frame=True: None))
        self.ops.bpy.context = types.SimpleNamespace(window_manager=types.SimpleNamespace(windows=[]),
                                                     temp_override=lambda **kw: _Ctx())
        for pick_id in self.ids:
            op = self.ops.BFACW_OT_highlights_preview()
            op.pick, op.report = pick_id, lambda *a: None
            op.execute(self.ctx)
        self.ops._restore_preview()
        self.assertEqual((self.scene.use_preview_range, self.scene.frame_preview_start, self.scene.frame_preview_end),
                         (False, 1, 250))

    def test_make_short_hands_the_placed_pick_to_the_builder(self):
        seen = []
        stub = types.ModuleType(W._PKG + ".highlight_short")
        stub.make_short = lambda op, scene, pick: seen.append((scene, pick["id"])) or {'FINISHED'}
        sys.modules[stub.__name__] = stub
        self.addCleanup(sys.modules.pop, stub.__name__, None)
        op = self.ops.BFACW_OT_highlights_make_short()
        op.pick = self.ids[0]
        op.report = lambda *a: None
        self.assertEqual(op.execute(self.ctx), {'FINISHED'})
        self.assertEqual(seen, [(self.scene, self.ids[0])])

    def test_find_without_the_search_or_without_speech_or_with_one_running_says_why(self):
        op = self.ops.BFACW_OT_highlights_find()
        op.seconds, op.count, op.kind = "60", 5, "auto"
        op.reports = []
        op.report = lambda level, text: op.reports.append((sorted(level)[0], text))
        self.ops._finder = lambda: None
        self.assertEqual(op.execute(self.ctx), {'CANCELLED'})
        self.assertEqual(op.reports[-1], ("ERROR", self.ops.NO_FINDER))
        self.ops._finder = lambda: object()
        self.reader_sents = []
        self.assertEqual(op.execute(self.ctx), {'CANCELLED'})
        self.assertIn("Make a transcript first", op.reports[-1][1])
        self.ops._JOB["running"] = True
        try:
            self.assertEqual(op.execute(self.ctx), {'CANCELLED'})
            self.assertIn("already running", op.reports[-1][1])
        finally:
            self.ops._JOB["running"] = False

    def test_find_runs_on_a_worker_and_the_timer_stores_the_picks(self):
        op = self.ops.BFACW_OT_highlights_find()
        op.seconds, op.count, op.kind = "30", 3, "tutorial"
        op.report = lambda *a: None
        asked = {}

        def find_highlights(sentences, fps, ask, **kw):
            asked.update(kw, n=len(sentences), fps=fps)
            kw["progress"]("Window 1 of 1")
            return {"picks": [pick_of(sentences, 2, 3, title="Found one")], "report": {"messages": ["Done."]}}

        aj = W._import("ask_json")
        self.ops._finder = lambda: types.SimpleNamespace(find_highlights=find_highlights)
        self._patch(self.ops.scene_reader_ops, "context_tokens", lambda: 16384)
        aj.STUB = lambda *a, **k: None          # an engine that is never asked here
        try:
            self.assertEqual(op.execute(self.ctx), {'FINISHED'})
            self.ops._JOB["thread"].join(5)
            self.assertIsNone(self.ops._job_tick())
        finally:
            aj.STUB = None
        self.assertEqual((asked["kind"], asked["max_seconds"], asked["count"], asked["context"]),
                         ("tutorial", 30.0, 3, 16384))
        picks = self.ops.load(self.scene)["picks"]
        self.assertEqual([p["title"] for p in picks], ["Found one"])
        self.assertEqual(self.ops.load(self.scene)["found"]["kind"], "tutorial")
        self.assertFalse(self.ops._JOB["running"])
        self.assertIn("1 highlight found", self.ops._JOB["message"])

    def _find_op(self):
        op = self.ops.BFACW_OT_highlights_find()
        op.seconds, op.count, op.kind = "60", 5, "auto"
        op.reports = []
        op.report = lambda level, text: op.reports.append((sorted(level)[0], text))
        return op

    def _start_find(self, finder):
        self.ops._finder = lambda: types.SimpleNamespace(find_highlights=finder)
        self._patch(self.ops.scene_reader_ops, "context_tokens", lambda: 16384)
        aj = W._import("ask_json")
        aj.STUB = lambda *a, **k: None
        self.addCleanup(setattr, aj, "STUB", None)
        self.addCleanup(self.ops._reset_job)
        op = self._find_op()
        self.assertEqual(op.execute(self.ctx), {'FINISHED'})
        return op

    def test_the_search_timer_is_persistent_so_a_file_load_cannot_drop_it(self):
        self._start_find(lambda sentences, fps, ask, **kw: {"picks": [], "report": {}})
        self.ops._JOB["thread"].join(5)
        self.assertEqual(self.bpy.timers[self.ops._job_tick][1], True)

    def test_find_works_again_after_the_timer_was_dropped_and_stop_takes_the_answer(self):
        self._start_find(lambda sentences, fps, ask, **kw: {"picks": [pick_of(sentences, 2, 3, title="Late")],
                                                              "report": {"messages": []}})
        self.ops._JOB["thread"].join(5)
        self.bpy.timers.clear()                          # Blender dropped the timer
        self.assertTrue(self.ops._JOB["running"])
        self.assertEqual(self.ops.BFACW_OT_highlights_stop().execute(None), {'FINISHED'})
        self.assertFalse(self.ops._JOB["running"])
        self.assertEqual([p["title"] for p in self.ops.load(self.scene)["picks"]], ["Late"])
        self.assertEqual(self._find_op().execute(self.ctx), {'FINISHED'})      # no "already running"

    def test_stop_with_a_dead_worker_and_no_answer_clears_running(self):
        self._start_find(lambda sentences, fps, ask, **kw: (_ for _ in ()).throw(SystemExit()))
        self.ops._JOB["thread"].join(5)
        self.bpy.timers.clear()
        self.ops.BFACW_OT_highlights_stop().execute(None)
        self.assertFalse(self.ops._JOB["running"])
        self.assertEqual(self.ops._JOB["level"], "error")

    def test_stop_while_a_request_is_out_says_it_waits(self):
        gate = threading.Event()
        self._start_find(lambda sentences, fps, ask, **kw: (gate.wait(5), {"picks": [], "report": {}})[1])
        self.ops.BFACW_OT_highlights_stop().execute(None)
        self.assertTrue(self.ops._JOB["running"])
        self.assertIn("current request", self.ops._JOB["progress"])
        gate.set()
        self.ops._JOB["thread"].join(5)
        self.assertIsNone(self.ops._job_tick())
        self.assertFalse(self.ops._JOB["running"])

    def test_unregister_and_a_file_load_forget_a_running_search(self):
        gate = threading.Event()
        self._start_find(lambda sentences, fps, ask, progress=None, **kw: (progress("old"), gate.wait(5),
                                                                          {"picks": [], "report": {}})[2])
        old = self.ops._JOB["stop"]
        self.ops._on_load()
        self.assertTrue(old.is_set())
        self.assertFalse(self.ops._JOB["running"])
        gate.set()
        self.assertIsNone(self.ops._job_tick())
        self.assertEqual(len(self.ops.load(self.scene)["picks"]), 2)      # the dropped job's answer was not applied
        self.assertEqual(self._find_op().execute(self.ctx), {'FINISHED'})
        self.ops.unregister()
        self.assertFalse(self.ops._JOB["running"])

    def test_the_picks_keep_the_signature_of_the_edit_that_was_read_not_the_one_at_the_end(self):
        def strip(left):
            return types.SimpleNamespace(name="s", type="SOUND", channel=1, left_handle=left, right_handle=left + 10,
                                         content_start=0.0, mute=False)
        self.scene.sequence_editor.strips_all = [strip(1)]
        first = self.ops.edit_signature(self.scene)

        def finder(sentences, fps, ask, **kw):
            self.scene.sequence_editor.strips_all = [strip(50)]        # the artist moves a strip meanwhile
            return {"picks": [pick_of(sentences, 2, 3)], "report": {}}
        self._start_find(finder)
        self.ops._JOB["thread"].join(5)
        self.ops._job_tick()
        self.assertEqual(self.ops.load(self.scene)["found"]["edit"], first)
        self.assertNotEqual(first, self.ops.edit_signature(self.scene))     # so the panel's "edit changed" line shows

    def test_a_file_load_puts_the_preview_range_back_and_forgets_the_preview(self):
        self.bpy.ops = types.SimpleNamespace(screen=types.SimpleNamespace(
            animation_play=lambda: None, animation_cancel=lambda restore_frame=True: None))
        self.ops.bpy.context = types.SimpleNamespace(window_manager=types.SimpleNamespace(windows=[]),
                                                     temp_override=lambda **kw: _Ctx())
        op = self.ops.BFACW_OT_highlights_preview()
        op.pick, op.report = self.ids[0], lambda *a: None
        op.execute(self.ctx)
        self.assertIsNotNone(self.ops._PREVIEW["orig"])
        self.ops._on_load()
        self.assertIsNone(self.ops._PREVIEW["orig"])
        self.assertNotIn(self.ops._preview_tick, self.bpy.timers)
        self.assertFalse(self.scene.use_preview_range)
        self.assertNotIn(self.ops._on_frame, self.bpy.app.handlers.frame_change_post)

    def test_preview_stops_watching_when_another_scene_is_the_one_playing(self):
        played = []
        self.bpy.ops = types.SimpleNamespace(screen=types.SimpleNamespace(
            animation_play=lambda: played.append("play"), animation_cancel=lambda restore_frame=True: played.append("cancel")))
        other = types.SimpleNamespace(name="Other")
        win = types.SimpleNamespace(scene=other, screen=types.SimpleNamespace(is_animation_playing=False, areas=[]))
        self.bpy.context = types.SimpleNamespace(window_manager=types.SimpleNamespace(windows=[win]),
                                                 temp_override=lambda **kw: _Ctx(), preferences=types.SimpleNamespace(addons={}))
        self.ops.bpy.context = self.bpy.context
        op = self.ops.BFACW_OT_highlights_preview()
        op.pick, op.report = self.ids[0], lambda *a: None
        op.execute(self.ctx)
        win.screen.is_animation_playing = True            # playing, but our scene's frame never moves
        for _ in range(self.ops.STILL_TICKS - 1):
            self.assertEqual(self.ops._preview_tick(), self.ops.TICK)
        self.assertIsNone(self.ops._preview_tick())
        self.assertIn("cancel", played)
        self.assertFalse(self.scene.use_preview_range)

    def test_check_with_a_reader_that_raises_leaves_the_picks_and_says_why(self):
        def broken(scene, context=None):
            raise RuntimeError("no reader")
        self._patch(self.ops.scene_reader_ops, "read", broken)
        before = self.scene[HP.PROP]
        counts, why = self.ops.check(self.scene)
        self.assertIsNone(counts)
        self.assertIn("no reader", why)
        self.assertEqual(self.scene[HP.PROP], before)

    def test_edit_signature_changes_when_a_strip_moves(self):
        def strip(left):
            return types.SimpleNamespace(name="s", type="SOUND", channel=1, left_handle=left, right_handle=left + 10,
                                         content_start=0.0, mute=False)
        self.scene.sequence_editor.strips_all = [strip(1)]
        a = self.ops.edit_signature(self.scene)
        self.scene.sequence_editor.strips_all = [strip(2)]
        self.assertNotEqual(a, self.ops.edit_signature(self.scene))
        self.assertEqual(self.ops.edit_signature(types.SimpleNamespace(sequence_editor=None)), "")

    def test_register_and_unregister_leave_nothing(self):
        self.ops.register()
        names = [c.__name__ for c in self.bpy.registered]
        self.assertEqual(names[-1], "BFACW_PT_highlights")
        self.assertEqual(len(names), 8)
        self.ops.unregister()
        self.assertEqual(self.bpy.registered, [])

    def test_every_operator_is_a_wand_with_the_tooltip_line(self):
        spec = W._import("wand.spec")
        for cls in self.ops._CLASSES[:-1]:
            self.assertIn(cls.bl_idname, spec.WAND_OPERATORS)
            self.assertTrue(cls.bl_description.splitlines()[-1].startswith("Works without the agent started")
                            or "Works without the agent started" in cls.bl_description, cls.bl_idname)
            self.assertTrue(cls.bl_label, cls.bl_idname)
        self.assertTrue(self.ops.BFACW_OT_highlights_find.bl_label.endswith("..."))      # its dialog always opens
        self.assertFalse(self.ops.BFACW_OT_highlights_preview.bl_label.endswith("..."))


class _Ctx:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


if __name__ == "__main__":
    unittest.main()
