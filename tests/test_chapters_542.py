# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for #542 Make Chapters (made-up transcripts, a scripted model, no Blender).

1. ``chapters.py``: the checks on the model's answer (numbers exist, go up, stay in the part it was shown), the
   settling (first chapter at 0:00, a minimum length, the last one, a wanted number), the ``0:00 Title`` text, the
   markers' names and the plan that touches only ours, the store on the scene.
2. The whole run with a scripted model on windows of a long talk: one request a window, a part that cannot be read is
   skipped and counted, a wrong number is dropped, nothing is invented, the model never sees a time to give.
3. Chapters_ops on a stand-in ``bpy``: markers made and replaced with the artist's own left alone, the text data-block,
   one report line, the search on a worker thread and its stop, the External Agent message.
4. The wand is in the Video panel, the Strip menu and the Transcript panel.

Run with::

    python -m unittest discover -s tests -p "test_chapters_542.py" -v
"""

__all__ = ()

import importlib
import sys
import threading
import types
import unittest
from pathlib import Path
from unittest import mock

_ADDON = Path(__file__).resolve().parents[1] / "addon" / "bfa_coworker"
_PKG = "bfacw_chapters_pkg"
FPS = 25.0


def _import(name: str):
    if _PKG not in sys.modules:
        pkg = types.ModuleType(_PKG)
        pkg.__path__ = [str(_ADDON)]
        sys.modules[_PKG] = pkg
    return importlib.import_module(_PKG + "." + name)


CH = _import("chapters")
AJ = _import("ask_json")
SR = _import("scene_reader")


def sentences(count: int, seconds: float = 12.0, frame_start: int = 1):
    """*count* sentences of *seconds* each, one after the other, the way the scene reader makes them."""
    out, t = [], 0.0
    for n in range(1, count + 1):
        start = frame_start + int(round(t * FPS))
        end = frame_start + int(round((t + seconds) * FPS))
        out.append({"n": n, "text": "Sentence {:d} says something.".format(n), "start": start, "end": end, "t": round(t, 2),
                    "seconds": seconds, "words": 25, "wps": 2.0, "pause_before": 0.0, "question": False, "number": False,
                    "fillers": 0, "speaker": "", "speaker_change": False})
        t += seconds
    return out


def by_n(rows):
    return {s["n"]: s for s in rows}


class Answer:
    def __init__(self, value):
        self.value, self.attempts = value, 1


# -- 1. the checks -------------------------------------------------------------------------------------

class TestCheck(unittest.TestCase):

    def setUp(self):
        self.rows = sentences(60)               # 12 minutes
        self.n = by_n(self.rows)

    def check(self, raw, window=None, stats=None):
        return CH.check(raw, self.n, 1, 1, 1 + 720 * 25, FPS, 10.0, window, stats)

    def test_a_good_answer_becomes_frames(self):
        got = self.check([{"sentence": 1, "title": "Start"}, {"sentence": 11, "title": "Setup"}])
        self.assertEqual([(c["sentence"], c["frame"]) for c in got], [(1, 1), (11, self.n[11]["start"])])

    def test_numbers_that_do_not_exist_are_dropped_and_counted(self):
        stats = {}
        got = self.check([{"sentence": 5, "title": "A"}, {"sentence": 999, "title": "B"}], stats=stats)
        self.assertEqual([c["sentence"] for c in got], [5])
        self.assertEqual(stats, {"no_such_sentence": 1})

    def test_numbers_must_go_up(self):
        stats = {}
        got = self.check([{"sentence": 20, "title": "A"}, {"sentence": 10, "title": "B"}, {"sentence": 20, "title": "C"},
                          {"sentence": 30, "title": "D"}], stats=stats)
        self.assertEqual([c["sentence"] for c in got], [20, 30])
        self.assertEqual(stats, {"not_ascending": 2})

    def test_a_number_outside_the_part_the_model_was_shown_is_invented(self):
        stats = {}
        got = self.check([{"sentence": 5, "title": "A"}, {"sentence": 40, "title": "B"}], window=(1, 30), stats=stats)
        self.assertEqual([c["sentence"] for c in got], [5])
        self.assertEqual(stats, {"outside_window": 1})

    def test_garbage_items(self):
        stats = {}
        got = self.check([{"title": "no number"}, "text", {"sentence": "x", "title": "y"}, None], stats=stats)
        self.assertEqual(got, [])
        self.assertEqual(stats, {"not_a_chapter": 4})
        self.assertEqual(self.check("not a list"), [])

    def test_titles_are_cleaned(self):
        got = self.check([{"sentence": 2, "title": '  "1. Getting   started."  '}, {"sentence": 3, "title": "   "},
                          {"sentence": 4, "title": "x" * 200}, {"sentence": 5, "title": "Chapter 4: Done"}])
        self.assertEqual(got[0]["title"], "Getting started")
        self.assertEqual(got[1]["title"], "Chapter 2")
        self.assertLessEqual(len(got[2]["title"]), CH.TITLE_CHARS + 3)
        self.assertEqual(got[3]["title"], "Done")


class TestSettle(unittest.TestCase):

    def setUp(self):
        self.rows = sentences(60)
        self.n = by_n(self.rows)
        self.end = 1 + 720 * 25

    def settle(self, numbers, count=0, min_seconds=10.0, stats=None):
        chapters = [{"sentence": k, "title": "T{:d}".format(k), "frame": self.n[k]["start"]} for k in numbers]
        return CH.settle(chapters, self.n, 1, 1, self.end, FPS, min_seconds, count, stats)

    def test_the_first_chapter_is_the_start(self):
        got = self.settle([1, 11, 31])
        self.assertEqual(got[0]["frame"], 1)
        self.assertEqual(CH.youtube_text(got, 1, FPS).splitlines()[0], "0:00 T1")

    def test_a_late_first_chapter_gets_an_introduction_in_front(self):
        stats = {}
        got = self.settle([11, 31], stats=stats)
        self.assertEqual([c["title"] for c in got], ["Introduction", "T11", "T31"])
        self.assertEqual(got[0]["frame"], 1)
        self.assertEqual(stats["intro_added"], 1)

    def test_a_first_chapter_in_the_first_seconds_is_moved_to_the_start(self):
        rows = sentences(60, seconds=4.0)
        n = by_n(rows)
        chapters = [{"sentence": 2, "title": "Hello", "frame": n[2]["start"]}, {"sentence": 12, "title": "Next", "frame": n[12]["start"]}]
        stats = {}
        got = CH.settle(chapters, n, 1, 1, 1 + 240 * 25, FPS, 10.0, 0, stats)
        self.assertEqual([c["title"] for c in got], ["Hello", "Next"])
        self.assertEqual(got[0]["frame"], 1)
        self.assertEqual(stats["first_moved"], 1)

    def test_a_chapter_closer_than_the_minimum_is_dropped(self):
        stats = {}
        got = self.settle([1, 2, 3, 12], min_seconds=20.0, stats=stats)
        self.assertEqual([c["sentence"] for c in got], [1, 3, 12])      # 12 s apart is under 20 s; 24 s is not
        self.assertEqual(stats["too_close"], 1)
        for a, b in zip(got, got[1:]):
            self.assertGreaterEqual(b["frame"] - a["frame"], 20 * FPS)

    def test_a_short_last_chapter_is_dropped_so_the_one_before_runs_to_the_end(self):
        stats = {}
        got = self.settle([1, 20, 60], stats=stats)         # sentence 60 starts 12 s before the end: fine at 10 s
        self.assertEqual([c["sentence"] for c in got], [1, 20, 60])
        got = self.settle([1, 20, 60], min_seconds=15.0, stats=stats)
        self.assertEqual([c["sentence"] for c in got], [1, 20])
        self.assertEqual(stats["tail_dropped"], 1)

    def test_a_repeat_from_two_windows_is_one_chapter(self):
        got = self.settle([1, 20, 20, 30])
        self.assertEqual([c["sentence"] for c in got], [1, 20, 30])

    def test_a_wanted_number_drops_the_closest_ones_down_to_one_and_a_half_times(self):
        stats = {}
        got = self.settle([1, 3, 6, 10, 20, 30, 31, 45, 50, 55], count=4, stats=stats)      # at most 6
        self.assertEqual(len(got), 6)
        self.assertEqual(got[0]["sentence"], 1)
        self.assertEqual(stats["capped"], 4)
        gaps = [b["frame"] - a["frame"] for a, b in zip(got, got[1:])]
        self.assertGreaterEqual(min(gaps), 2 * 12 * FPS)

    def test_nothing_is_dropped_without_a_wanted_number(self):
        self.assertEqual(len(self.settle([1, 3, 6, 10, 20, 30, 31, 45, 50, 55])), 10)

    def test_nothing_at_all_gives_the_introduction_only(self):
        got = CH.settle([], self.n, 1, 1, self.end, FPS)
        self.assertEqual([c["title"] for c in got], ["Introduction"])


class TestTexts(unittest.TestCase):

    def test_the_list_in_the_form_video_sites_accept(self):
        chapters = [{"sentence": 1, "title": "Intro", "frame": 1}, {"sentence": 9, "title": "Setting up", "frame": 1 + 83 * 25},
                    {"sentence": 20, "title": "The result", "frame": 1 + 725 * 25}]
        text = CH.youtube_text(chapters, 1, FPS)
        self.assertEqual(text, "0:00 Intro\n1:23 Setting up\n12:05 The result")
        self.assertTrue(text.splitlines()[0].startswith("0:00 "))

    def test_an_hour_long_edit_uses_hours_on_every_line(self):
        chapters = [{"sentence": 1, "title": "A", "frame": 1}, {"sentence": 2, "title": "B", "frame": 1 + 3725 * 25}]
        self.assertEqual(CH.youtube_text(chapters, 1, FPS), "0:00:00 A\n1:02:05 B")

    def test_seconds_are_rounded_down_and_start_at_the_scene_start(self):
        self.assertEqual(CH.format_time(59.99), "0:59")
        self.assertEqual(CH.youtube_text([{"sentence": 1, "title": "A", "frame": 101}], 101, 25.0), "0:00 A")

    def test_marker_names_have_the_prefix_and_fit(self):
        self.assertEqual(CH.marker_name(1, "Intro"), "CH1: Intro")
        self.assertLessEqual(len(CH.marker_name(12, "x" * 200)), 63)
        self.assertTrue(CH.marker_name(3, "t").startswith(CH.MARKER_PREFIX))


class TestMarkerPlan(unittest.TestCase):

    chapters = [{"sentence": 1, "title": "Intro", "frame": 1}, {"sentence": 5, "title": "Setup", "frame": 500}]

    def test_first_time_adds_all_and_removes_nothing(self):
        remove, add, names = CH.marker_plan(CH.empty_store(), [("My marker", 40), ("CH1: Mine", 99)], self.chapters)
        self.assertEqual(remove, [])
        self.assertEqual(add, [("CH1: Intro", 1), ("CH2: Setup", 500)])
        self.assertEqual(names, ["CH1: Intro", "CH2: Setup"])

    def test_again_replaces_only_what_the_store_made(self):
        store = dict(CH.empty_store(), markers=["CH1: Old", "CH2: Older"])
        existing = [("CH1: Old", 1), ("My marker", 40), ("CH2: Older", 300), ("CH1: Mine", 99)]
        remove, add, _names = CH.marker_plan(store, existing, self.chapters)
        self.assertEqual(remove, [("CH1: Old", 1), ("CH2: Older", 300)])
        self.assertEqual(len(add), 2)

    def test_the_store_round_trips_and_bad_text_gives_an_empty_one(self):
        store = dict(CH.empty_store(), chapters=[{"sentence": 1, "title": "A", "frame": 1}], markers=["CH1: A"], text="Chapters")
        self.assertEqual(CH.loads(CH.dumps(store)), store)
        for bad in ("", "{", "[]", '{"v": 99}', None, 5):
            self.assertEqual(CH.loads(bad), CH.empty_store())
        self.assertEqual(CH.loads('{"v":1,"chapters":[{"x":1},5],"markers":[1,"CH1: a"]}')["markers"], ["CH1: a"])


# -- 2. the whole run --------------------------------------------------------------------------------------

class Scripted:
    """A model that answers each request from a function of the sentence numbers it was shown."""

    def __init__(self, answer):
        self.answer, self.requests = answer, []

    def __call__(self, messages, schema, **kw):
        user = messages[-1]["content"]
        numbers = [int(line[1:line.index(" ")]) for line in user.splitlines() if line.startswith("S") and " " in line]
        self.requests.append({"system": messages[0]["content"], "user": user, "numbers": numbers, "schema": schema, "kw": kw})
        out = self.answer(numbers, len(self.requests))
        if isinstance(out, Exception):
            raise out
        return Answer({"chapters": out})


class TestFind(unittest.TestCase):

    def test_one_request_for_a_talk_that_fits(self):
        rows = sentences(60)
        model = Scripted(lambda nums, k: [{"sentence": 1, "title": "Hello"}, {"sentence": 20, "title": "The tool"},
                                          {"sentence": 40, "title": "Results"}])
        out = CH.find_chapters(rows, FPS, 1, 1 + 720 * 25, model)
        self.assertEqual(len(model.requests), 1)
        self.assertEqual([c["title"] for c in out["chapters"]], ["Hello", "The tool", "Results"])
        self.assertEqual(out["report"]["requests"], 1)
        self.assertEqual(out["report"]["messages"], [])

    def test_the_model_sees_numbers_and_is_asked_for_numbers_not_times(self):
        model = Scripted(lambda nums, k: [{"sentence": 1, "title": "A"}])
        CH.find_chapters(sentences(40), FPS, 1, 1 + 480 * 25, model, count=5, min_seconds=10)
        req = model.requests[0]
        self.assertIn("S1 [", req["user"])
        self.assertIn("never give times", req["system"].lower())
        self.assertIn("about 5 chapters", req["system"])
        self.assertIn("at least 10 seconds", req["system"])
        self.assertEqual(req["schema"]["properties"]["chapters"]["items"]["required"], ["sentence", "title"])
        self.assertEqual(req["kw"]["name"], "chapters")

    def test_auto_does_not_name_a_number(self):
        model = Scripted(lambda nums, k: [{"sentence": 1, "title": "A"}])
        CH.find_chapters(sentences(40), FPS, 1, 1 + 480 * 25, model)
        self.assertNotIn("about", model.requests[0]["system"].split("Use only")[1].split("A chapter is")[0])

    def test_a_long_talk_is_asked_in_windows_and_the_chapters_join_up(self):
        rows = sentences(400, seconds=14.0)        # 93 min, 10 000 words
        model = Scripted(lambda nums, k: [{"sentence": nums[0], "title": "Part {:d}".format(k)},
                                          {"sentence": nums[len(nums) // 2], "title": "Middle {:d}".format(k)}])
        out = CH.find_chapters(rows, FPS, 1, 1 + int(400 * 14 * FPS), model, context=8000)
        self.assertGreater(len(model.requests), 2)
        for req in model.requests:
            self.assertTrue(set(req["numbers"]) <= {s["n"] for s in rows})
        got = out["chapters"]
        self.assertEqual(got[0]["frame"], 1)
        self.assertEqual([c["sentence"] for c in got], sorted({c["sentence"] for c in got}))
        for a, b in zip(got, got[1:]):
            self.assertGreaterEqual(b["frame"] - a["frame"], 10 * FPS)
        self.assertEqual(out["report"]["parts"], len(model.requests))

    def test_a_part_the_model_cannot_answer_is_skipped_and_said(self):
        rows = sentences(400, seconds=14.0)
        model = Scripted(lambda nums, k: AJ.AskFailed(["bad"], "x", 2) if k == 2 else
                         [{"sentence": nums[0], "title": "T{:d}".format(k)}])
        out = CH.find_chapters(rows, FPS, 1, 1 + int(400 * 14 * FPS), model, context=8000)
        self.assertEqual(out["report"]["parts_failed"], 1)
        self.assertTrue(any("could not be read" in m for m in out["report"]["messages"]))
        self.assertTrue(out["chapters"])

    def test_wrong_numbers_are_dropped_and_counted_and_never_invented(self):
        model = Scripted(lambda nums, k: [{"sentence": 1, "title": "A"}, {"sentence": 500, "title": "Ghost"},
                                          {"sentence": 30, "title": "B"}, {"sentence": 25, "title": "Back"}])
        out = CH.find_chapters(sentences(60), FPS, 1, 1 + 720 * 25, model)
        self.assertEqual([c["title"] for c in out["chapters"]], ["A", "B"])
        self.assertEqual(out["report"]["dropped"], {"outside_window": 1, "not_ascending": 1})

    def test_no_speech_no_engine_call(self):
        model = Scripted(lambda nums, k: [])
        out = CH.find_chapters([], FPS, 1, 100, model)
        self.assertEqual(out["chapters"], [])
        self.assertEqual(model.requests, [])
        self.assertIn("No speech", out["report"]["messages"][0])

    def test_an_empty_answer_says_there_was_no_change_of_topic(self):
        out = CH.find_chapters(sentences(40), FPS, 1, 1 + 480 * 25, Scripted(lambda nums, k: []))
        self.assertEqual(out["chapters"], [])
        self.assertTrue(any("no change of topic" in m for m in out["report"]["messages"]))

    def test_few_chapters_are_flagged_for_video_sites(self):
        out = CH.find_chapters(sentences(40), FPS, 1, 1 + 480 * 25, Scripted(lambda nums, k: [{"sentence": 1, "title": "A"}, {"sentence": 20, "title": "B"}]))
        self.assertTrue(any("at least 3" in m for m in out["report"]["messages"]))

    def test_unavailable_goes_up(self):
        model = Scripted(lambda nums, k: AJ.Unavailable("Switch Coworker to Local LLM or Remote API", "external"))
        with self.assertRaises(AJ.Unavailable):
            CH.find_chapters(sentences(40), FPS, 1, 1 + 480 * 25, model)

    def test_stop_between_requests(self):
        rows = sentences(400, seconds=14.0)
        model = Scripted(lambda nums, k: [{"sentence": nums[0], "title": "T"}])
        calls = []
        out = CH.find_chapters(rows, FPS, 1, 1 + int(400 * 14 * FPS), model, context=8000,
                               should_stop=lambda: len(model.requests) >= 1 or bool(calls.append(1)))
        self.assertEqual(len(model.requests), 1)
        self.assertTrue(out["report"]["stopped"])
        self.assertTrue(any("Stopped" in m for m in out["report"]["messages"]))

    def test_a_thousand_sentences_check_quickly(self):
        import time
        rows = sentences(1000, seconds=9.0)
        raw = [{"sentence": k, "title": "T{:d}".format(k)} for k in range(1, 1000, 3)]
        t0 = time.monotonic()
        found = CH.check(raw, by_n(rows), 1, 1, 1 + 9000 * 25, FPS)
        CH.settle(found, by_n(rows), 1, 1, 1 + 9000 * 25, FPS, 10.0, 40)
        self.assertLess(time.monotonic() - t0, 1.0)


# -- 3. the Blender side on a stand-in ------------------------------------------------------------------------

class _Markers(list):
    def new(self, name, frame):
        m = types.SimpleNamespace(name=name, frame=frame)
        self.append(m)
        return m

    def remove(self, marker):
        super().remove(marker)


class _Text:
    def __init__(self, name):
        self.name, self.body, self.props = name, "", {}

    def clear(self):
        self.body = ""

    def write(self, text):
        self.body += text

    def get(self, key, default=None):
        return self.props.get(key, default)

    def __setitem__(self, key, value):
        self.props[key] = value


class _Texts(dict):
    def new(self, name):
        unique, k = name, 0
        while unique in self:
            k += 1
            unique = "{:s}.{:03d}".format(name, k)
        self[unique] = _Text(unique)
        return self[unique]


class _Scene(dict):
    def __init__(self):
        super().__init__()
        self.name, self.frame_start, self.frame_end = "Edit", 1, 18000
        self.timeline_markers = _Markers()
        self.render = types.SimpleNamespace(fps=25, fps_base=1.0)
        self.sequence_editor = types.SimpleNamespace(meta_stack=[])


def _load_ops():
    bpy = types.ModuleType("bpy")
    props = types.ModuleType("bpy.props")
    btypes = types.ModuleType("bpy.types")
    for n in ("BoolProperty", "FloatProperty", "IntProperty", "EnumProperty", "StringProperty"):
        setattr(props, n, lambda *a, **k: None)
    btypes.Operator = btypes.Panel = btypes.Menu = type("Base", (), {})
    bpy.props, bpy.types, bpy.app = props, btypes, mock.MagicMock()
    bpy.app.handlers.load_pre = []
    bpy.app.handlers.persistent = lambda fn: fn
    bpy.app.timers.is_registered = lambda fn: False
    bpy.data = types.SimpleNamespace(texts=_Texts(), scenes={})
    bpy.context = types.SimpleNamespace(window_manager=types.SimpleNamespace(windows=[], clipboard="", popup_menu=mock.MagicMock()),
                                        scene=None)
    bpy.utils = types.SimpleNamespace(register_class=lambda c: None, unregister_class=lambda c: None)
    bpy.ops = types.SimpleNamespace(bfacw=types.SimpleNamespace(make_chapters_watch=mock.MagicMock(return_value={'RUNNING_MODAL'})))
    with mock.patch.dict(sys.modules, {"bpy": bpy, "bpy.props": props, "bpy.types": btypes}):
        for name in ("wand.sequencer", "chapters_ops"):
            sys.modules.pop(_PKG + "." + name, None)
        return bpy, _import("chapters_ops")


class TestOps(unittest.TestCase):

    def setUp(self):
        self.bpy, self.ops = _load_ops()
        self.scene = _Scene()
        self.bpy.data.scenes["Edit"] = self.scene
        self.chapters = [{"sentence": 1, "title": "Intro", "frame": 1}, {"sentence": 5, "title": "Setup", "frame": 1 + 83 * 25},
                         {"sentence": 9, "title": "Result", "frame": 1 + 725 * 25}]
        self.ops._reset_job()

    def test_markers_and_the_text_are_made(self):
        info = self.ops.apply(self.scene, self.chapters, 25.0)
        self.assertEqual([(m.name, m.frame) for m in self.scene.timeline_markers],
                         [("CH1: Intro", 1), ("CH2: Setup", 2076), ("CH3: Result", 18126)])
        self.assertEqual(self.bpy.data.texts["Chapters"].body, "0:00 Intro\n1:23 Setup\n12:05 Result\n")
        self.assertEqual(info["markers"], 3)
        self.assertTrue(self.bpy.data.texts["Chapters"].get(self.ops.TEXT_TAG))

    def test_again_replaces_ours_and_leaves_the_artists_markers_and_text(self):
        self.scene.timeline_markers.new("My marker", 700)
        self.scene.timeline_markers.new("CH1: Mine", 701)          # the artist's, looks like ours
        self.ops.apply(self.scene, self.chapters, 25.0)
        self.ops.apply(self.scene, self.chapters[:2], 25.0)
        names = sorted(m.name for m in self.scene.timeline_markers)
        self.assertEqual(names, ["CH1: Intro", "CH1: Mine", "CH2: Setup", "My marker"])
        self.assertEqual(list(self.bpy.data.texts), ["Chapters"])
        self.assertEqual(self.bpy.data.texts["Chapters"].body, "0:00 Intro\n1:23 Setup\n")

    def test_a_text_of_the_artists_with_the_same_name_is_not_overwritten(self):
        mine = self.bpy.data.texts.new("Chapters")
        mine.write("my own notes")
        self.ops.apply(self.scene, self.chapters, 25.0)
        self.assertEqual(mine.body, "my own notes")
        self.assertIn("Chapters.001", self.bpy.data.texts)

    def test_remove_takes_only_ours(self):
        self.scene.timeline_markers.new("My marker", 700)
        self.ops.apply(self.scene, self.chapters, 25.0)
        self.assertEqual(self.ops.remove_ours(self.scene), 3)
        self.assertEqual([m.name for m in self.scene.timeline_markers], ["My marker"])
        self.assertEqual(self.ops.remove_ours(self.scene), 0)
        self.assertEqual(self.ops.load(self.scene)["markers"], [])

    def test_the_report_line(self):
        info = self.ops.apply(self.scene, self.chapters, 25.0)
        line = self.ops._report_text(self.chapters, info, 25.0, 1, True)
        self.assertEqual(line, "Made 3 chapters (the last at 12:05): 3 markers on the timeline and the list in the text "
                               "“Chapters”, copied to the clipboard")

    def _start(self, answer, sentences_=None):
        rows = sentences_ if sentences_ is not None else sentences(60)
        reader = mock.Mock(return_value={"sentences": rows, "report": {"messages": ["nothing"]}, "windows": []})
        patches = [mock.patch.object(self.ops.scene_reader_ops, "read", reader),
                   mock.patch.object(self.ops.scene_reader_ops, "context_tokens", lambda: None)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        model = Scripted(answer)
        return model, self.ops.start(self.scene, 0, 10.0, asker=model)

    def test_the_search_runs_on_a_worker_and_the_timer_writes_the_result(self):
        started = threading.Event()
        release = threading.Event()

        def answer(nums, k):
            started.set()
            release.wait(5)
            return [{"sentence": 1, "title": "Hello"}, {"sentence": 20, "title": "Tool"}, {"sentence": 40, "title": "End"}]
        model, why = self._start(answer)
        self.assertEqual(why, "")
        self.assertTrue(self.ops._JOB["running"])
        self.assertTrue(started.wait(5))
        self.assertIn("already", self.ops.start(self.scene, 0, 10.0, asker=model))
        self.assertEqual(self.scene.timeline_markers, [])       # nothing written from the worker
        release.set()
        self.ops._JOB["thread"].join(5)
        with mock.patch.object(self.ops._wand, "_push_undo") as undo:
            self.assertIsNone(self.ops._job_tick())
        self.assertEqual([m.name for m in self.scene.timeline_markers], ["CH1: Hello", "CH2: Tool", "CH3: End"])
        self.assertEqual(undo.call_count, 1)                    # one undo step
        self.assertEqual(self.bpy.context.window_manager.clipboard.splitlines()[0], "0:00 Hello")
        self.assertFalse(self.ops._JOB["running"])
        self.assertTrue(self.ops._JOB["message"].startswith("Made 3 chapters"))

    def test_the_modal_watcher_writes_when_the_worker_is_done_and_reports_one_line(self):
        model, _why = self._start(lambda nums, k: [{"sentence": 1, "title": "Hello"}, {"sentence": 20, "title": "Tool"},
                                                   {"sentence": 40, "title": "End"}])
        self.ops._JOB["thread"].join(5)
        watch = self.ops.BFACW_OT_make_chapters_watch()
        reports = []
        watch.report = lambda level, text: reports.append((level, text))
        timer = object()
        wm = types.SimpleNamespace(event_timer_add=mock.Mock(return_value=timer), event_timer_remove=mock.Mock(),
                                   modal_handler_add=mock.Mock())
        context = types.SimpleNamespace(window_manager=wm, window=object())
        self.assertEqual(watch.invoke(context, None), {'RUNNING_MODAL'})
        self.assertEqual(watch.modal(context, types.SimpleNamespace(type="MOUSEMOVE")), {'PASS_THROUGH'})
        with mock.patch.object(self.ops._wand, "_push_undo") as undo:
            self.assertEqual(watch.modal(context, types.SimpleNamespace(type="TIMER")), {'FINISHED'})
        self.assertEqual(undo.call_count, 1)
        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0][0], {'INFO'})
        self.assertTrue(reports[0][1].startswith("Made 3 chapters"))
        wm.event_timer_remove.assert_called_once_with(timer)
        self.assertEqual([m.name for m in self.scene.timeline_markers], ["CH1: Hello", "CH2: Tool", "CH3: End"])

    def test_escape_stops_the_watcher_and_nothing_is_written(self):
        release = threading.Event()
        self._start(lambda nums, k: release.wait(5) and [{"sentence": 1, "title": "Hello"}])
        watch = self.ops.BFACW_OT_make_chapters_watch()
        reports = []
        watch.report = lambda level, text: reports.append((level, text))
        wm = types.SimpleNamespace(event_timer_add=mock.Mock(return_value=1), event_timer_remove=mock.Mock(), modal_handler_add=mock.Mock())
        context = types.SimpleNamespace(window_manager=wm, window=object())
        watch.invoke(context, None)
        self.assertEqual(watch.modal(context, types.SimpleNamespace(type="ESC")), {'RUNNING_MODAL'})
        self.assertTrue(self.ops._JOB["stop"].is_set())
        release.set()
        self.ops._JOB["thread"].join(5)
        with mock.patch.object(self.ops._wand, "_push_undo") as undo:
            self.assertEqual(watch.modal(context, types.SimpleNamespace(type="TIMER")), {'FINISHED'})
        undo.assert_not_called()
        self.assertEqual(self.scene.timeline_markers, [])
        self.assertIn("Stopped", reports[0][1])

    def test_execute_starts_the_watcher_and_falls_back_to_a_timer(self):
        rows = sentences(60)
        reader = mock.Mock(return_value={"sentences": rows, "report": {"messages": []}, "windows": []})
        op = self.ops.BFACW_OT_make_chapters()
        op.count_auto, op.count, op.min_seconds = True, 6, 10.0
        op.report = lambda *a: None
        context = types.SimpleNamespace(sequencer_scene=self.scene)
        with mock.patch.object(self.ops.scene_reader_ops, "read", reader),                 mock.patch.object(self.ops.scene_reader_ops, "context_tokens", lambda: None),                 mock.patch.object(AJ, "resolve_asker", return_value=Scripted(lambda n, k: [])):
            self.assertEqual(op.execute(context), {'FINISHED'})
        self.bpy.ops.bfacw.make_chapters_watch.assert_called_once_with('INVOKE_DEFAULT')
        self.ops._JOB["thread"].join(5)
        self.ops._reset_job()
        self.bpy.ops.bfacw.make_chapters_watch.side_effect = RuntimeError("no window")
        with mock.patch.object(self.ops.scene_reader_ops, "read", reader),                 mock.patch.object(self.ops.scene_reader_ops, "context_tokens", lambda: None),                 mock.patch.object(AJ, "resolve_asker", return_value=Scripted(lambda n, k: [])):
            self.assertEqual(op.execute(context), {'FINISHED'})
        self.bpy.app.timers.register.assert_called_once()
        self.ops._JOB["thread"].join(5)

    def test_stop_ends_the_search_and_writes_nothing(self):
        release = threading.Event()
        model, _why = self._start(lambda nums, k: release.wait(5) and [])
        op = self.ops.BFACW_OT_make_chapters_stop()
        op.execute(None)
        self.assertTrue(self.ops._JOB["stop"].is_set())
        release.set()
        self.ops._JOB["thread"].join(5)
        with mock.patch.object(self.ops._wand, "_push_undo") as undo:
            self.ops._job_tick()
        self.assertEqual(self.scene.timeline_markers, [])
        undo.assert_not_called()

    def test_no_speech_says_what_to_do_first(self):
        _model, why = self._start(lambda nums, k: [], sentences_=[])
        self.assertIn("Make a transcript first", why)
        self.assertFalse(self.ops._JOB["running"])

    def test_inside_a_meta_strip_it_refuses(self):
        self.scene.sequence_editor.meta_stack = [object()]
        self.assertIn("meta strip", self.ops.start(self.scene, 0, 10.0, asker=lambda *a, **k: None))

    def test_an_external_agent_is_told_to_switch(self):
        rows = sentences(60)
        reader = mock.Mock(return_value={"sentences": rows, "report": {"messages": []}, "windows": []})
        with mock.patch.object(self.ops.scene_reader_ops, "read", reader), \
                mock.patch.object(self.ops.scene_reader_ops, "context_tokens", lambda: None), \
                mock.patch.object(AJ, "resolve_asker", side_effect=AJ.Unavailable(
                    "Make Chapters asks the model directly; an External Agent or Harness runs its own. "
                    "Switch Coworker to Local LLM or Remote API to use it.", "external")):
            why = self.ops.start(self.scene, 0, 10.0)
        self.assertIn("Switch Coworker to Local LLM or Remote API", why)
        self.assertFalse(self.ops._JOB["running"])

    def test_a_loaded_file_forgets_the_search(self):
        release = threading.Event()
        self._start(lambda nums, k: release.wait(5) and [])
        self.ops._on_load()
        self.assertFalse(self.ops._JOB["running"])
        release.set()


# -- 4. where it is -------------------------------------------------------------------------------------------

class TestPlacement(unittest.TestCase):

    def test_the_video_panel_and_the_strip_menu_list_it_after_the_captions(self):
        text = (_ADDON / "wand" / "sequencer.py").read_text(encoding="utf-8")
        a = text.index('("bfacw.auto_captions"')
        b = text.index('("bfacw.make_chapters", "BFACW_OT_make_chapters", "Make Chapters", "chapters", "MARKER_HLT")')
        c = text.index('("bfacw.make_vertical"')
        self.assertTrue(a < b < c)

    def test_the_transcript_panel_has_it_between_captions_and_the_removals(self):
        text = (_ADDON / "transcript_edit_ops.py").read_text(encoding="utf-8")
        a = text.index('"Make Captions...", "captions"')
        b = text.index('"bfacw.make_chapters", "Make Chapters...", "chapters", "MARKER_HLT"')
        c = text.index('"Remove Silences", "remove_silences"')
        self.assertTrue(a < b < c)

    def test_registered_with_the_add_on(self):
        text = (_ADDON / "__init__.py").read_text(encoding="utf-8")
        self.assertIn("chapters_ops.register()", text)
        self.assertIn("chapters_ops.unregister()", text)

    def test_the_operators_are_wands_with_the_agent_line(self):
        spec = importlib.import_module(_PKG + ".wand.spec")
        for idname in ("bfacw.make_chapters", "bfacw.make_chapters_stop", "bfacw.chapters_remove", "bfacw.reframe_zoom_to"):
            self.assertIn(idname, spec.WAND_OPERATORS)
        self.assertIn("needs a model", _ADDON.joinpath("chapters_ops.py").read_text(encoding="utf-8").replace("need a model", "needs a model"))


if __name__ == "__main__":
    unittest.main()
