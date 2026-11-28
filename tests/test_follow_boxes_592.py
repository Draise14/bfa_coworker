# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Follow Subject asks a vision model for face and person boxes (#592): the question, the strict answer, the
reading of a bad one.  The model is a scripted stand-in (a fake, not a real model): these check our side of
the exchange only; a real model's boxes are a by-hand check."""

import importlib
import sys
import tempfile
import types
import unittest
from pathlib import Path

_ADDON = Path(__file__).resolve().parents[1] / "addon" / "bfa_coworker"
_PKG = "bfa_coworker"


def _import(name):
    if _PKG not in sys.modules:
        pkg = types.ModuleType(_PKG)
        pkg.__path__ = [str(_ADDON)]
        sys.modules[_PKG] = pkg
    return importlib.import_module(_PKG + "." + name)


rf = _import("reframe_follow")
sf = _import("subject_find")
aj = _import("ask_json")


class _Script:
    def __init__(self, *texts):
        self.texts = list(texts)
        self.calls = []

    def __call__(self, url, messages, tools, key, model, **kw):
        self.calls.append({"messages": messages, **kw})
        if not self.texts:
            return None
        return {"choices": [{"message": {"content": self.texts.pop(0)}}]}


class TestParse(unittest.TestCase):

    def test_faces_and_people(self):
        faces, people = rf.parse_boxes('{"faces": [[0.7, 0.1, 0.8, 0.3]], "people": [[0.1, 0.1, 0.4, 0.9]]}')
        self.assertEqual(faces, [(0.7, 0.1, 0.8, 0.3)])
        self.assertEqual(people, [(0.1, 0.1, 0.4, 0.9)])

    def test_text_around_the_json_and_percent_boxes(self):
        faces, _ = rf.parse_boxes('Here you go: {"faces": [[70, 10, 80, 30]], "people": []} done')
        self.assertEqual([tuple(round(v, 3) for v in b) for b in faces], [(0.7, 0.1, 0.8, 0.3)])

    def test_swapped_corners_are_put_right(self):
        faces, _ = rf.parse_boxes({"faces": [[0.8, 0.3, 0.7, 0.1]], "people": []})
        self.assertEqual(faces, [(0.7, 0.1, 0.8, 0.3)])

    def test_nonsense_is_dropped(self):
        for bad in ('{"faces": [[0.1, 0.1]], "people": []}', '{"faces": [[0.5, 0.5, 0.5, 0.9]], "people": []}',
                    '{"faces": [[0, 0, 1, 1]], "people": []}', '{"faces": [["a", 0, 1, 1]], "people": []}',
                    "no faces here", "", None, "{}", '{"faces": 3}'):
            self.assertEqual(rf.parse_boxes(bad), ([], []), bad)

    def test_the_schema_is_strict(self):
        self.assertFalse(aj.validate({"faces": [], "people": []}, rf.BOXES_SCHEMA))
        self.assertTrue(aj.validate({"faces": [[0.1, 0.1, 0.2]], "people": []}, rf.BOXES_SCHEMA))
        self.assertTrue(aj.validate({"faces": [[0.1, 0.1, 0.2, 1.5]], "people": []}, rf.BOXES_SCHEMA))
        self.assertTrue(aj.validate({"faces": []}, rf.BOXES_SCHEMA))


class TestAsker(unittest.TestCase):

    def _png(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = str(Path(tmp.name) / "f.png")
        Path(path).write_bytes(b"\x89PNG\r\n\x1a\nfake")
        return path

    def _asker(self, *texts):
        t = _Script(*texts)
        return rf.make_box_asker(("http://h/v1/chat/completions", "", ""), t), t

    def test_one_frame_one_request_with_the_box_question(self):
        ask, t = self._asker('{"faces": [[0.7, 0.1, 0.8, 0.3]], "people": []}')
        self.assertEqual(ask(self._png(), "shot", 12), ([(0.7, 0.1, 0.8, 0.3)], []))
        self.assertEqual(len(t.calls), 1)
        content = t.calls[0]["messages"][0]["content"]
        self.assertEqual([c["type"] for c in content][:2], ["image_url", "text"])   # ask_json adds its own note after
        self.assertEqual(content[1]["text"], rf.BOXES_PROMPT)
        self.assertEqual(t.calls[0]["extra_body"]["response_format"]["json_schema"]["schema"], rf.BOXES_SCHEMA)

    def test_wrong_then_right_costs_one_retry(self):
        ask, t = self._asker('{"faces": [[7, 1, 8, 3]], "people": []}', '{"faces": [[0.7, 0.1, 0.8, 0.3]], "people": []}')
        self.assertEqual(ask(self._png(), "shot", 1)[0], [(0.7, 0.1, 0.8, 0.3)])
        self.assertEqual(len(t.calls), 2)

    def test_a_hopeless_answer_counts_as_nobody(self):
        ask, _t = self._asker("I see a cat", "I see a cat")
        self.assertEqual(ask(self._png(), "shot", 1), ([], []))

    def test_dead_engine_raises_unavailable(self):
        ask = rf.make_box_asker(("http://h/v1/chat/completions", "", ""), lambda *a, **k: None)
        with self.assertRaises(rf.Unavailable):
            ask(self._png(), "shot", 1)

    def test_the_box_stub_replaces_the_model(self):
        rf.BOX_STUB = lambda path, name, frame: ([(0.1, 0.1, 0.2, 0.3)], [])
        self.addCleanup(setattr, rf, "BOX_STUB", None)
        self.assertEqual(rf.resolve_box_asker()("x", "s", 1), ([(0.1, 0.1, 0.2, 0.3)], []))


class TestNotice(unittest.TestCase):

    def test_the_dialog_says_where_the_frames_go(self):
        self.assertIn("stay on this machine", rf.engine_notice("LOCAL_LLM"))
        self.assertIn("sent", rf.engine_notice("REMOTE_API"))
        self.assertIn("screen activity only", rf.engine_notice("EXTERNAL_AGENT"))


class TestKinds(unittest.TestCase):
    """Faces only / screen only keep one family of subjects (``analyse(kinds=...)``)."""

    def _samples(self):
        faces = (sf.Box(0.7, 0.2, 0.84, 0.5),)
        out = []
        for i in range(8):
            a = [[0.2] * 64 for _ in range(36)]
            b = [[0.2] * 64 for _ in range(36)]
            for y in range(10, 25):                 # the left side changes: screen activity
                for x in range(6, 30):
                    b[y][x] = 0.9
            out.append(sf.Sample(i * 72, a, b, faces))
        return out

    def test_faces_only_ignores_what_moves(self):
        out = sf.analyse(self._samples(), 24.0, "auto", ("face", "person"))
        self.assertEqual({t.kind for t in out.primary}, {"face"})
        self.assertEqual(out.layout, "single")

    def test_screen_only_ignores_faces(self):
        out = sf.analyse(self._samples(), 24.0, "auto", ("pip", "screen", "pointer"))
        self.assertEqual({t.kind for t in out.primary}, {"screen"})

    def test_everything_gives_face_on_top_and_the_screen_second(self):
        out = sf.analyse(self._samples(), 24.0, "auto", None)
        self.assertEqual((out.primary[0].kind, out.secondary[0].kind, out.layout), ("face", "screen", "split"))


if __name__ == "__main__":
    unittest.main()
