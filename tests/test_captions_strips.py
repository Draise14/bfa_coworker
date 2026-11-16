# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Auto Captions on every selected strip (#538): a transcript mapped to captions per strip (the
pieces of a cut clip, a clip used twice, a slipped sound, another scene fps and start), no
caption across a strip's edge or over a neighbour's, which strips are skipped and why, and
hand-edited captions kept on a re-run.  No Blender: made-up words, ``captions_ops`` on the
stand-in ``bpy`` of ``test_captions``.

Run with::

    python -m unittest tests.test_captions_strips -v
"""

__all__ = ()

import json
import tempfile
import unittest
from pathlib import Path

from test_captions import _OpsCase, _clip, cap

FPS = 30.0


def _made_up() -> list[dict]:
    """Five made-up sentences, a word every 0.5 s: at 1, 5, 13, 19 and 25 s of the media."""
    out = []
    for start, text in ((1.0, "Alpha one opens the first part."), (5.0, "Bravo two keeps it going here."),
                        (13.0, "Charlie three starts the middle."), (19.0, "Delta four ends the middle bit."),
                        (25.0, "Echo five closes the last part.")):
        for i, w in enumerate(text.split()):
            out.append({"w": w, "start": start + 0.5 * i, "end": start + 0.5 * i + 0.45, "p": 0.95})
    return out


WORDS = _made_up()


def _piece(name: str, media_from: float, media_to: float, at: int, fps: float = FPS, offset: float = 0.0) -> dict:
    """A strip showing media seconds [media_from, media_to) from timeline frame *at*."""
    return {"name": name, "content_start": at - media_from * fps + offset * fps, "left": at,
            "right": at + int(round((media_to - media_from) * fps)), "offset": offset}


def _cut_in_three(at: int = 1, fps: float = FPS) -> list[dict]:
    """Media 0-10 s, 12-20 s and 24-30 s, the gaps closed."""
    out = []
    for i, (a, b) in enumerate(((0, 10), (12, 20), (24, 30))):
        out.append(_piece("P{:d}".format(i), a, b, at, fps))
        at += int(round((b - a) * fps))
    return out


def _heard(strips: list[dict], fps: float = FPS) -> list[tuple[str, float]]:
    """(word, timeline frame) of every word a strip shows -- worked out without the module."""
    out = []
    for s in strips:
        for w in WORDS:
            f = s["content_start"] + (w["start"] - s.get("offset", 0.0)) * fps
            if s["left"] <= round(f) < s["right"]:
                out.append((w["w"], f))
    return out


class TestStripsToCaptions(unittest.TestCase):

    def check_cover(self, got: dict, strips: list[dict], fps: float = FPS) -> None:
        caps = got["captions"]
        for word, f in _heard(strips, fps):
            over = [c for c in caps if c["frame_start"] <= round(f) < c["frame_end"]]
            self.assertTrue(over and any(word in c["text"].split() for c in over),
                            "{!r} at frame {:.1f} has no caption".format(word, f))
        for a, b in zip(caps, caps[1:]):
            self.assertLessEqual(a["frame_end"], b["frame_start"], "captions overlap")
        by_name = {s["name"]: s for s in strips}
        for c in caps:
            s = by_name[c["strip"]]
            self.assertGreaterEqual(c["frame_start"], s["left"])
            self.assertLessEqual(c["frame_end"], s["right"], "a caption crosses its strip's edge")

    def test_a_clip_cut_in_three_with_the_gaps_closed(self):
        strips = _cut_in_three()
        got = cap.captions_for_strips(WORDS, strips, FPS, 42, 2)
        self.assertEqual(got["captioned"], ["P0", "P1", "P2"])
        self.assertEqual(got["no_speech"], [])
        self.check_cover(got, strips)
        texts = [c["text"] for c in got["captions"]]
        self.assertEqual(texts, ["Alpha one opens the first part.", "Bravo two keeps it going here.",
                                 "Charlie three starts the middle.", "Delta four",     # "ends" is at 20 s: cut
                                 "Echo five closes the last part."])
        # P1 shows media 12-20 s from frame 301: "Charlie" (13 s) at 301 + 1 s.
        self.assertEqual(got["captions"][2]["frame_start"], 301 + 30)
        self.assertEqual(got["captions"][4]["frame_start"], 541 + 30)

    def test_the_same_clip_used_twice(self):
        strips = [_piece("First", 0, 30, 1), _piece("Again", 0, 30, 1001)]
        got = cap.captions_for_strips(WORDS, strips, FPS, 42, 2)
        self.assertEqual(got["captioned"], ["First", "Again"])
        self.assertEqual(len(got["captions"]), 10)
        first, again = got["captions"][:5], got["captions"][5:]
        self.assertEqual([c["frame_start"] + 1000 for c in first], [c["frame_start"] for c in again])
        self.check_cover(got, strips)

    def test_a_caption_never_crosses_a_cut(self):
        # A cut in the middle of "Bravo two keeps it going here." (media 6.0 s): both pieces
        # keep their own words; the caption of the first ends at its edge.
        strips = [_piece("A", 0, 6.0, 1), _piece("B", 6.0, 30, 181)]
        got = cap.captions_for_strips(WORDS, strips, FPS, 42, 2)
        a = [c for c in got["captions"] if c["strip"] == "A"]
        b = [c for c in got["captions"] if c["strip"] == "B"]
        self.assertEqual(a[-1]["text"], "Bravo two")
        self.assertLessEqual(a[-1]["frame_end"], 181)
        self.assertEqual(b[0]["text"], "keeps it going here.")
        self.check_cover(got, strips)

    def test_a_word_on_a_cut_goes_to_one_piece_only(self):
        strips = [_piece("A", 0, 5.0, 1), _piece("B", 5.0, 30, 151)]       # "Bravo" starts at 5.0 s
        got = cap.captions_for_strips(WORDS, strips, FPS, 42, 2)
        bravo = [c for c in got["captions"] if "Bravo" in c["text"]]
        self.assertEqual([c["strip"] for c in bravo], ["B"])

    def test_a_piece_with_no_speech(self):
        strips = [_piece("Talk", 0, 10, 1), _piece("Quiet", 9.5, 12.5, 301)]
        got = cap.captions_for_strips(WORDS, strips, FPS, 42, 2)
        self.assertEqual((got["captioned"], got["no_speech"]), (["Talk"], ["Quiet"]))

    def test_a_slipped_sound_strip(self):
        # sound_offset 2 s: media second t plays at content_start + (t - 2) * fps.
        s = {"name": "Slipped", "content_start": 1.0, "left": 1, "right": 601, "offset": 2.0}
        got = cap.captions_for_strips(WORDS, [s], FPS, 42, 2)
        self.assertEqual(got["captions"][0]["text"], "opens the first part.")     # 2.0 s at frame 1
        self.assertEqual(got["captions"][0]["frame_start"], 1)
        self.assertEqual(got["captions"][1]["text"], "Bravo two keeps it going here.")
        self.assertEqual(got["captions"][1]["frame_start"], 1 + 3 * 30)
        self.check_cover(got, [s])

    def test_another_scene_fps_and_start(self):
        # A 24 fps scene from frame 101 (30 fps footage plays at its own speed: seconds x 24).
        strips = [_piece("C0", 0, 10, 101, 24.0), _piece("C1", 13, 30, 341, 24.0)]
        got = cap.captions_for_strips(WORDS, strips, 24.0, 42, 2)
        self.assertEqual(got["captions"][0]["frame_start"], 101 + 24)
        self.assertEqual(got["captions"][2]["frame_start"], 341)            # "Charlie" at 13 s
        self.check_cover(got, strips, 24.0)

    def test_neighbours_never_overlap(self):
        caps = [{"frame_start": 10, "frame_end": 40, "strip": "a"}, {"frame_start": 30, "frame_end": 60, "strip": "b"},
                {"frame_start": 30, "frame_end": 35, "strip": "c"}, {"frame_start": 60, "frame_end": 61, "strip": "d"}]
        kept, dropped = cap.separate(caps)
        self.assertEqual([(c["strip"], c["frame_start"], c["frame_end"]) for c in kept],
                         [("a", 10, 30), ("c", 30, 35), ("b", 35, 60), ("d", 60, 61)])
        self.assertEqual(dropped, 0)
        kept, dropped = cap.separate([{"frame_start": 5, "frame_end": 9}, {"frame_start": 5, "frame_end": 9}])
        self.assertEqual((len(kept), dropped), (1, 1))

    def test_media_frame(self):
        self.assertEqual(cap.media_frame(2.0, 1.0, 0.0, 30.0), 61.0)
        self.assertEqual(cap.media_frame(2.0, 1.0, 1.5, 30.0), 16.0)


class TestPlanStrips(unittest.TestCase):

    def item(self, name, type_="SOUND", key="media:a", left=1, right=301, **kw):
        return dict({"name": name, "type": type_, "key": key, "left": left, "right": right}, **kw)

    def test_a_movie_is_heard_through_its_sound(self):
        items = [self.item("P0", "MOVIE"), self.item("P0_snd"), self.item("P1", "MOVIE", left=301, right=541),
                 self.item("P1_snd", left=301, right=541)]
        todo, skipped, partners = cap.plan_strips(items)
        self.assertEqual([t["name"] for t in todo], ["P0_snd", "P1_snd"])
        self.assertEqual(skipped, {})
        self.assertEqual(partners, {"P0": ["P0_snd"], "P1": ["P1_snd"]})

    def test_a_movie_on_its_own_is_captioned(self):
        todo, _skipped, partners = cap.plan_strips([self.item("Pic", "MOVIE"), self.item("Other", key="media:b")])
        self.assertEqual([t["name"] for t in todo], ["Other", "Pic"])
        self.assertEqual(partners, {})

    def test_muted_and_retimed_are_skipped(self):
        items = [self.item("Muted", muted=True), self.item("Fast", key="media:b", retimed=True),
                 self.item("Pic", "MOVIE")]
        todo, skipped, partners = cap.plan_strips(items)
        self.assertEqual(todo, [])
        self.assertEqual(skipped, {"Muted": cap.SKIP_MUTED, "Fast": cap.SKIP_RETIMED})
        self.assertEqual(partners, {"Pic": ["Muted"]})             # nothing of it is heard either
        self.assertEqual(cap.partner_outcome([skipped["Muted"]]), cap.SKIP_MUTED)
        self.assertEqual(cap.partner_outcome([cap.SKIP_NO_SPEECH, "captioned"]), "captioned")


class TestReplacePlan(unittest.TestCase):

    def line(self, name, left, right, key="media:a", edited=False):
        return {"name": name, "key": key, "left": left, "right": right, "edited": edited}

    def test_only_captions_over_the_strips_captioned_now(self):
        lines = [self.line("a", 10, 40), self.line("b", 310, 340), self.line("gone", 900, 930),
                 self.line("other", 10, 40, key="media:b")]
        targets = [("media:a", 301, 541)]
        present = [("media:a", 1, 301), ("media:a", 301, 541)]
        doomed, kept = cap.replace_plan(lines, targets, present)
        # "a" lies over an unselected piece of the same file; "gone" over no strip any more.
        self.assertEqual((doomed, kept), ({"b", "gone", "other"}, set()))

    def test_edited_captions_are_kept_unless_asked(self):
        lines = [self.line("a", 10, 40, edited=True), self.line("b", 50, 80)]
        targets = [("media:a", 1, 301)]
        self.assertEqual(cap.replace_plan(lines, targets, targets), ({"b"}, {"a"}))
        self.assertEqual(cap.replace_plan(lines, targets, targets, replace_edited=True), ({"a", "b"}, set()))

    def test_without_targets_every_line_goes(self):
        lines = [self.line("a", 10, 40), self.line("b", 5000, 5040)]
        self.assertEqual(cap.replace_plan(lines, None)[0], {"a", "b"})

    def test_signature(self):
        sig = cap.caption_signature("Hello there", 45, 3, (0.5, 0.09))
        self.assertEqual(sig, cap.caption_signature("Hello there", 45, 3, (0.5, 0.09)))
        for other in (("Hello where", 45, 3, (0.5, 0.09)), ("Hello there", 44, 3, (0.5, 0.09)),
                      ("Hello there", 45, 4, (0.5, 0.09)), ("Hello there", 45, 3, (0.5, 0.2))):
            self.assertNotEqual(sig, cap.caption_signature(*other), other)

    def test_report(self):
        text = cap.strips_report(["P0_snd", "P0"], [{"strip": "Music", "reason": cap.SKIP_NO_SPEECH},
                                                    {"strip": "Fast", "reason": cap.SKIP_RETIMED}], 1)
        self.assertTrue(text.startswith("for 2 strips; skipped 1, no speech in the part it shows: “Music”"))
        self.assertIn("skipped 1, retimed", text)
        self.assertIn("“Fast”", text)
        self.assertIn("kept 1 caption edited by hand", text)
        # #558: a clip whose transcript has no words at all says so, not "in the part it shows".
        silent = cap.strips_report([], [{"strip": "Music", "reason": cap.SKIP_SILENT}])
        self.assertEqual(silent, "for 0 strips; skipped 1, no speech in the clip: “Music”")
        many = [{"strip": "s{:d}".format(i), "reason": cap.SKIP_MUTED} for i in range(6)]
        self.assertIn("skipped 6, muted", cap.strips_report([], many))
        self.assertIn("and 2 more", cap.strips_report([], many))

    def test_pick_channel_with_each_captions_frames(self):
        occupied = {3: [(301, 541)]}                       # a caption kept over a piece not captioned now
        names = {3: cap.CHANNEL_NAME}
        self.assertEqual(cap.pick_channel(occupied, names, 2, (1, 721)), 4)
        self.assertEqual(cap.pick_channel(occupied, names, 2, [(1, 300), (541, 721)]), 3)


# -- captions_ops on the stand-in bpy -----------------------------------------------------

class TestEveryStrip(_OpsCase):

    def setUp(self):
        super().setUp()
        self.scene.render.fps = 30
        fh = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        json.dump({"schema": cap.SCHEMA, "language": "en", "words": WORDS}, fh)
        fh.close()
        self.made_up = fh.name
        self.addCleanup(lambda: Path(fh.name).unlink())

    def pair(self, name, media_from, media_to, at, path="//media/talk.mp4", **kw):
        """A movie and its sound showing media [media_from, media_to) from frame *at*."""
        right = at + int(round((media_to - media_from) * 30))
        cs = at - media_from * 30
        m = _clip(self.scene, name, path, channel=2, left=at, right=right, type_="MOVIE")
        s = _clip(self.scene, name + "_snd", path, channel=1, left=at, right=right)
        for x in (m, s):
            x.content_start = cs
            x.__dict__.update(kw)
        return m, s

    def media(self):
        return [s for s in self.scene.sequence_editor.strips if s.type in ("MOVIE", "SOUND")]

    def run_captions(self, strips=None, **kw):
        return self.ops.add_captions(self.scene, strips or self.media(), "CLEAN", "en", self.made_up, **kw)

    def spans(self):
        return [(s.left_handle, s.right_handle, s.text) for s in sorted(self.lines(), key=lambda s: s.left_handle)]

    def test_every_piece_of_a_cut_clip(self):
        at = 1
        for i, (a, b) in enumerate(((0, 10), (12, 20), (24, 30))):
            self.pair("P{:d}".format(i), a, b, at)
            at += (b - a) * 30
        r = self.run_captions()
        self.assertEqual(r["lines_created"], 5)
        self.assertEqual(sorted(r["strips_captioned"]), ["P0", "P0_snd", "P1", "P1_snd", "P2", "P2_snd"])
        self.assertEqual(r["strips_skipped"], [])
        self.assertEqual([x[0] for x in self.spans()], [31, 151, 331, 511, 571])
        self.assertIn("for 6 strips", self.ops._summary(r))

    def test_the_same_clip_twice_and_a_retimed_and_a_muted_one(self):
        self.pair("First", 0, 30, 1)
        self.pair("Again", 0, 30, 1001)
        self.pair("Fast", 0, 30, 2001, retiming_keys=[1, 2])
        _clip(self.scene, "Off", "//media/other.wav", channel=1, left=3001, right=3301).mute = True
        r = self.run_captions()
        self.assertEqual(r["lines_created"], 10)
        self.assertEqual(sorted(r["strips_captioned"]), ["Again", "Again_snd", "First", "First_snd"])
        self.assertEqual({s["strip"]: s["reason"] for s in r["strips_skipped"]},
                         {"Fast": cap.SKIP_RETIMED, "Fast_snd": cap.SKIP_RETIMED, "Off": cap.SKIP_MUTED})
        summary = self.ops._summary(r)
        self.assertIn("skipped 2, retimed", summary)
        self.assertIn("“Off”", summary)
        self.assertTrue(self.ops._warn(r))

    def test_nothing_to_caption_says_why(self):
        self.pair("Quiet", 9.5, 12.5, 1)
        with self.assertRaises(self.ops.CaptionsError) as ctx:
            self.run_captions()
        self.assertIn("no speech in the part it shows", str(ctx.exception))
        self.assertIn("“Quiet_snd”", str(ctx.exception))

    def test_a_clip_with_no_words_at_all_says_it_has_no_speech(self):
        # #558: a transcript with no words (music only): the clip has no speech.
        Path(self.made_up).write_text(json.dumps({"schema": cap.SCHEMA, "language": "en", "words": []}),
                                      encoding="utf-8")
        self.pair("Music", 0, 10, 1)
        with self.assertRaises(self.ops.CaptionsError) as ctx:
            self.run_captions()
        self.assertEqual(str(ctx.exception),
                         "There's no speech to caption (skipped 2, no speech in the clip: “Music”, “Music_snd”).")
        self.assertNotIn("the part it shows", str(ctx.exception))

    def test_a_slipped_sound_follows_its_offset(self):
        s = _clip(self.scene, "Slipped", "//media/talk.wav", left=1, right=601)
        s.sound_offset = 2.0
        self.run_captions([s])
        bravo = [x for x in self.spans() if x[2].startswith("Bravo")]
        self.assertEqual(bravo[0][0], 91)                       # 5 s at 1 + (5 - 2) x 30

    def test_captioning_one_piece_keeps_the_others(self):
        a, a_snd = self.pair("A", 0, 10, 1)
        b, b_snd = self.pair("B", 12, 20, 301)
        self.run_captions()
        before = self.spans()
        r = self.run_captions([b, b_snd])
        self.assertEqual(r["replaced"], 2)
        self.assertEqual(self.spans(), before)                  # A's captions are still there, B's made again

    def test_a_caption_edited_by_hand_is_kept(self):
        self.pair("P", 0, 30, 1)
        self.run_captions()
        lines = sorted(self.lines(), key=lambda s: s.left_handle)
        lines[1].text = "Bravo two, as I said it"                # reworded
        lines[3].location = (0.5, 0.4)                          # moved up
        r = self.run_captions()
        self.assertEqual(r["kept_edited"], 2)
        self.assertEqual(r["replaced"], 3)
        texts = [x[2] for x in self.spans()]
        self.assertIn("Bravo two, as I said it", texts)
        self.assertEqual(len(texts), 5)                          # no new caption over the kept ones
        self.assertIn("kept 2 captions edited by hand", self.ops._summary(r))
        r = self.run_captions(replace_edited=True)
        self.assertEqual((r["kept_edited"], r["replaced"]), (0, 5))
        self.assertNotIn("Bravo two, as I said it", [x[2] for x in self.spans()])

    def test_a_rippled_caption_is_not_an_edit(self):
        m, s = self.pair("P", 0, 30, 1)
        self.run_captions()
        for x in list(self.lines()) + [m, s]:                    # Remove Silences' ripple: all slide left
            x.left_handle -= 10
            x.right_handle -= 10
            x.content_start -= 10
        r = self.run_captions()
        self.assertEqual((r["kept_edited"], r["replaced"]), (0, 5))

    def test_the_tool_answer_names_what_it_skipped(self):
        self.pair("First", 0, 30, 1)
        self.pair("Fast", 0, 30, 1001, retiming_keys=[1, 2])
        r = self.ops.tool_add_captions(["First", "First_snd", "Fast", "Fast_snd"], "CLEAN", "en", self.made_up)
        self.assertEqual(r["status"], "ok")
        self.assertIn("for 2 strips", r["message"])
        self.assertIn("skipped 2, retimed", r["message"])
        self.assertEqual([s["reason"] for s in r["strips_skipped"]], [cap.SKIP_RETIMED] * 2)


if __name__ == "__main__":
    unittest.main()
