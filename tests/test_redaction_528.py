# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for #528: what #511 (PR #525) left of the redaction.

1. A key glued to an underscore (``out_sk-...``, ``MY_hf_...``,
   ``HTTP_AUTHORIZATION: Basic ...``) is hidden: ``\\b`` does not match between two
   word characters, so the patterns missed it.  An ``sk-or-v1-`` / ``sk-ant-api03-``
   key glued to a letter is hidden too; ordinary words still are not.
2. ``operations.jsonl``: a key you set that holds ``"``, ``\\`` or non-ASCII
   characters is hidden.  The values are redacted before the line is written as
   JSON (where those characters are escaped and the key no longer matched), and the
   line stays valid JSON.
3. A key set while a chat is open: a finished turn's Workshop is measured again, so
   its height matches what is drawn (``***`` is shorter than the key).

Fake values only, made up for these tests.

Run with::

    python -m unittest tests.test_redaction_528 -v
"""

__all__ = ()

import importlib
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

_ADDON = Path(__file__).resolve().parents[1] / "addon" / "bfa_coworker"
_PKG = "bfacw_redact528_pkg"


def _load(module):
    if _PKG not in sys.modules:
        pkg = types.ModuleType(_PKG)
        pkg.__path__ = [str(_ADDON)]
        sys.modules[_PKG] = pkg
    return importlib.import_module(_PKG + "." + module)


log = _load("log")

FAKE_SK = "sk-or-v1-" + "0fa4e" * 8                       # shaped like an OpenRouter key
FAKE_PROJ = "sk-proj-FAKEdummyAbc123_def456-ghi789"        # shaped like an OpenAI project key
FAKE_ANT = "sk-ant-api03-FAKEdummy0123456789abcdefXYZ"     # shaped like an Anthropic key
FAKE_HF = "hf_FAKEdummyToken0123456789abcd"
FAKE_BASIC = "ZmFrZTpkdW1teS1wYXNzd29yZA=="                # base64 of made-up text
# A key you set that JSON escapes: a quote, a backslash, non-ASCII letters.
FAKE_ODD = 'fäke"k\\ey-ünïcode-0123'


class TestGluedKeys(unittest.TestCase):

    def assertHidden(self, text, secret):
        out = log.redact(text)
        self.assertNotIn(secret[:12], out, "{!r} -> {!r}".format(text, out))
        self.assertIn("***", out)
        return out

    def test_a_key_glued_to_an_underscore(self):
        self.assertEqual(log.redact("out_" + FAKE_SK), "out_***")
        self.assertEqual(log.redact("MY_KEY_" + FAKE_PROJ + " done"), "MY_KEY_*** done")
        self.assertEqual(log.redact("cache_" + FAKE_HF), "cache_***")
        self.assertEqual(log.redact("x_" + FAKE_ANT), "x_***")

    def test_an_authorization_header_glued_to_an_underscore(self):
        for text in ("HTTP_AUTHORIZATION: Basic " + FAKE_BASIC,
                     "proxy_authorization=Basic " + FAKE_BASIC,
                     "{'X_Authorization': 'token " + FAKE_BASIC + "'}"):
            with self.subTest(text=text):
                self.assertHidden(text, FAKE_BASIC)
        self.assertEqual(log.redact("auth_bearer " + FAKE_BASIC), "auth_bearer ***")

    def test_a_known_key_shape_glued_to_a_letter_or_digit(self):
        self.assertEqual(log.redact("key" + FAKE_SK), "key***")
        self.assertEqual(log.redact("2" + FAKE_ANT), "2***")

    def test_a_key_after_other_marks_still_hidden(self):
        for lead in ("", " ", "=", "'", '"', "-", "/", ":", "(", "\n"):
            with self.subTest(lead=lead):
                self.assertEqual(log.redact(lead + FAKE_SK), lead + "***")
                self.assertEqual(log.redact(lead + FAKE_HF), lead + "***")

    def test_ordinary_words_are_left_alone(self):
        for text in ("task-scheduler-for-the-renders", "risk-assessment-pipeline-step",
                     "desk-organizer-for-the-studio-room", "whisk-and-fold-the-batter-slowly",
                     "task-admin-dashboard-for-the-studio-team", "risk-or-reward-analysis-for-the-team",
                     "task-proj-management-overview-doc", "flask_hf_integration_module",
                     "prefs.hf_token is set", "use_bearer = True", "the bearer of bad news",
                     "Authorization failed (401)", "pre_authorization_check()", "mask_sk-short",
                     "überdesk-arrangement-for-a-quiet-room"):
            with self.subTest(text=text):
                self.assertEqual(log.redact(text), text)


class TestOperationsLog(unittest.TestCase):

    def setUp(self):
        self.ac = _load("agent_controller")
        self.home = tempfile.TemporaryDirectory()
        self.addCleanup(self.home.cleanup)
        patcher = mock.patch("pathlib.Path.home", return_value=Path(self.home.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        log.set_secret("remote_api_key", FAKE_ODD)
        self.addCleanup(log.set_secret, "remote_api_key", "")

    def _lines(self):
        path = Path(self.home.name) / ".cache" / "bfa_coworker" / "operations.jsonl"
        return path.read_text(encoding="utf-8").splitlines()

    def test_a_key_json_escapes_is_hidden(self):
        self.ac._log_operation("execute_blender_code",
                               {"code": "key = '{:s}'".format(FAKE_ODD),
                                "nested": [{"k": FAKE_ODD}, (FAKE_ODD, 3)], FAKE_ODD: True},
                               "answer: " + FAKE_ODD)
        (line,) = self._lines()
        self.assertNotIn(json.dumps(FAKE_ODD)[1:-1], line)     # not even escaped
        entry = json.loads(line)                                 # still valid JSON
        self.assertNotIn(FAKE_ODD[:8], json.dumps(entry, ensure_ascii=False))
        self.assertEqual(entry["params"]["code"], "key = '***'")
        self.assertEqual(entry["params"]["nested"], [{"k": "***"}, ["***", 3]])
        self.assertIn("***", entry["params"])                   # a dict key too
        self.assertEqual(entry["result"], "answer: ***")
        self.assertEqual(entry["tool"], "execute_blender_code")

    def test_the_tool_name_and_patterns(self):
        self.ac._log_operation("curl -H 'Authorization: Bearer " + FAKE_BASIC + "'",
                               {"auth": "Authorization: Bearer " + FAKE_BASIC + '"quoted"',
                                "k": "out_" + FAKE_SK}, "x" * 495 + FAKE_HF)
        (line,) = self._lines()
        entry = json.loads(line)
        for secret in (FAKE_BASIC, FAKE_SK, FAKE_HF):
            self.assertNotIn(secret[:10], line)
        self.assertEqual(entry["tool"], "curl -H 'Authorization: Bearer ***'")
        self.assertEqual(entry["params"]["auth"], 'Authorization: Bearer ***"quoted"')
        self.assertEqual(entry["params"]["k"], "out_***")

    def test_the_tool_gets_its_arguments_unchanged(self):
        params = {"code": FAKE_ODD, "items": [FAKE_ODD]}
        self.ac._log_operation("t", params, "")
        self.assertEqual(params, {"code": FAKE_ODD, "items": [FAKE_ODD]})
        self.assertEqual(json.loads(self._lines()[0])["params"], {"code": "***", "items": ["***"]})


class TestWorkshopHeight(unittest.TestCase):
    """A key set while a chat is open: the finished turn's height follows the ``***``."""

    LATE = "fake-late-key-" + "q7Zx" * 40               # long: ``***`` wraps to fewer lines

    def setUp(self):
        self.cv = _load("ui_gpu.chat_view")
        self.lay = _load("ui_gpu.layout")
        self.st = _load("ui_gpu.style")
        self.addCleanup(log.set_secret, "late", "")

    def _history(self):
        return [
            {"role": "user", "content": "go", "turn_start": True},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "c1", "function": {"name": "set_thing", "arguments": json.dumps({"v": self.LATE})}}]},
            {"role": "tool", "name": "set_thing", "tool_call_id": "c1",
             "content": json.dumps({"status": "ok", "result": "stored " + self.LATE})},
            {"role": "assistant", "content": "Done."},
            {"role": "user", "content": "next", "turn_start": True},
            {"role": "assistant", "content": "Ok."},
        ]

    def _draw(self, view, hist):
        canvas = self.lay.Canvas(self.st.Style(), self.lay.fixed_measure(0.55), self.lay.InteractionState())
        view.draw(canvas, self.lay.Rect(0, 0, 420, 5000), hist)
        return view.content_height

    def _open(self):
        view = self.cv.ChatView()
        view.expanded.add("t0")                 # the first (finished, not newest) turn's Workshop open
        return view

    def test_the_height_follows_a_key_set_later(self):
        hist = self._history()
        view = self._open()
        before = self._draw(view, hist)
        log.set_secret("late", self.LATE)
        after = self._draw(view, hist)
        fresh = self._draw(self._open(), hist)
        self.assertLess(fresh, before, "the test needs a key long enough to wrap")
        self.assertEqual(after, fresh, "the open chat measures the turn with the key as ***")
        log.set_secret("late", "")
        self.assertEqual(self._draw(view, hist), before, "and again when the key is cleared")


if __name__ == "__main__":
    unittest.main()
