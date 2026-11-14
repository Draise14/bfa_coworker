# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
The running turn is written by a worker thread (our loop's streaming, run_conversation_turn)
while the main thread draws the chat (#387).  The chat view measures the running turn and
then draws it, and both read the live state: when the worker replaced the text in between
(a turn ending, a new request of the turn starting over with a short text), the turn was
drawn with other text than it was measured with -- a tall empty slot at the bottom of the
chat, the messages missing for that frame (reproduced live on Blender 5.2.4, with the draw
trace showing ``live=2437/2881/66->0/2881/67``).  The view now reads the live state once a
frame (:func:`chat_view.live_snapshot`).

Run with::

    python -m unittest tests.test_gpu_live_race -v
"""

__all__ = ()

import importlib
import sys
import types
import unittest
from pathlib import Path

_ADDON = Path(__file__).resolve().parents[1] / "addon" / "bfa_coworker"


def _load():
    name = "bfacw_live_race_pkg"
    if name not in sys.modules:
        pkg = types.ModuleType(name)
        pkg.__path__ = [str(_ADDON)]
        sys.modules[name] = pkg
    return [importlib.import_module(name + ".ui_gpu." + m) for m in ("chat_view", "layout", "style")]


cv, lay, st = _load()

STREAM = " ".join("word{:d}".format(i) for i in range(400))


class _Worker:
    """The live state as a worker changes it: every read of the streaming text after the
    first sees the turn's text replaced (*then*); the reads are counted."""

    is_thinking = True
    turn_phase = "thinking"
    thinking_dots = 1
    thinking_start_time = 0.0
    reasoning_text = ""

    def __init__(self, first, then):
        self.first, self.then, self.reads = first, then, 0

    @property
    def streaming_text(self):
        self.reads += 1
        return self.first if self.reads == 1 else self.then


HIST = [{"role": "user", "content": "Earlier", "turn_start": True},
        {"role": "assistant", "content": "Done before."},
        {"role": "user", "content": "Build a village", "turn_start": True}]


def _draw(view, live, rect=lay.Rect(0, 0, 300, 400)):
    canvas = lay.Canvas(st.Style(), lay.fixed_measure(0.55), lay.InteractionState())
    view.draw(canvas, rect, HIST, live=live)
    return canvas


def _texts(canvas):
    return [cmd for cmd in canvas.commands if cmd.kind == "text" and cmd.rect is not None]


class TestLiveReadOnce(unittest.TestCase):

    def test_live_text_read_once_a_frame(self):
        live = _Worker(STREAM, STREAM + " more")
        _draw(cv.ChatView(), live)
        self.assertEqual(live.reads, 1)          # was 2: once to measure, once to draw

    def test_text_cleared_mid_frame_still_draws_what_was_measured(self):
        canvas = _draw(cv.ChatView(), _Worker(STREAM, ""))
        words = [cmd for cmd in _texts(canvas) if cmd.text.startswith("word")]
        self.assertTrue(words)                   # was empty: a tall blank slot, no messages
        # ...and the newest words sit at the bottom of the chat, not far above an empty gap.
        self.assertLess(min(cmd.rect.y for cmd in words), 60)

    def test_text_grown_mid_frame_stays_inside_its_slot(self):
        view = cv.ChatView()
        canvas = _draw(view, _Worker(STREAM[:200], STREAM))
        words = [cmd for cmd in _texts(canvas) if cmd.text.startswith("word")]
        self.assertTrue(words)
        self.assertGreaterEqual(min(cmd.rect.y for cmd in words), 0.0)   # not drawn under the chat

    def test_snapshot(self):
        live = _Worker("hello", "")
        snap = cv.live_snapshot(live)
        self.assertEqual((snap.streaming_text, snap.streaming_text, snap.is_thinking), ("hello", "hello", True))
        self.assertIs(cv.live_snapshot(snap), snap)
        self.assertIsNone(cv.live_snapshot(None))
        bare = cv.live_snapshot(object())        # missing fields: the defaults
        self.assertEqual((bare.is_thinking, bare.streaming_text, bare.thinking_dots), (False, "", 0))


if __name__ == "__main__":
    unittest.main()
