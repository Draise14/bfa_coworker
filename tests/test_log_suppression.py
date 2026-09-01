"""
Tests for console suppression in log.py (`_TeeStream` / `_should_pass_through`).

The addon redirects sys.stdout/stderr through a tee that both writes to the
rotating log file and forwards to Blender's console.  With console
suppression ON (debug mode OFF) the addon's routine ``[Coworker]`` edits
should never reach the console — *including* their trailing newline, which
``print()`` emits as a separate ``write()`` call (that bare newline has no
prefix and used to leak through as a blank console line).

Run with::

    python -m unittest tests.test_log_suppression -v
"""

__all__ = ()

import io
import os
import sys
import types
import unittest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_log():
    """Load addon/bfa_coworker/log.py standalone (stdlib-only)."""
    src_path = os.path.join(_REPO, "addon", "bfa_coworker", "log.py")
    with open(src_path, "r", encoding="utf-8") as fh:
        source = fh.read()
    mod = types.ModuleType("log_under_test")
    # Patch out the log-file writer: tests must not touch the real log path.
    mod.write = lambda *a, **k: None  # type: ignore[attr-defined]
    exec(compile(source, src_path, "exec"), mod.__dict__)
    # Re-bind the tee's file-write call to the no-op (module attribute lookup
    # happens at call time, so assigning after exec works for the tee too).
    return mod


_log = _load_log()


class _Sink(io.TextIOBase):
    """Captures whatever the tee forwards."""

    def __init__(self):
        self.data = ""
        self.flushes = 0

    def write(self, s: str) -> int:
        self.data += s
        return len(s)

    def flush(self) -> None:
        self.flushes += 1


class TestShouldPassThrough(unittest.TestCase):
    """Classifier: what should reach the console when suppression is ON."""

    def setUp(self):
        _log.set_suppress_console(True)

    def test_routine_addon_line_suppressed(self):
        self.assertFalse(_log._should_pass_through("[🛠️Coworker] health ping"))
        self.assertFalse(_log._should_pass_through("[Coworker] startup trace"))

    def test_warning_passes(self):
        self.assertTrue(_log._should_pass_through("[⚠️Coworker] outdated build"))

    def test_error_markers_pass(self):
        for line in (
            "[Coworker] all attempts FAILED — stopping",
            "[🛠️Coworker] ERROR in loader",
            "[Coworker] TRACEBACK follows",
        ):
            self.assertTrue(_log._should_pass_through(line), line)

    def test_non_addon_output_passes(self):
        for line in (
            "blend | Saving user preferences",
            "✅ Hotkey Tools: Registered",
            "00:24.469  operator         | Preferences saved",
        ):
            self.assertTrue(_log._should_pass_through(line), line)

    def test_debug_mode_shows_everything(self):
        _log.set_suppress_console(False)
        self.assertTrue(_log._should_pass_through("[🛠️Coworker] anything"))


class TestTeeStream(unittest.TestCase):
    """End-to-end tee behavior with print()-style chunked writes."""

    def setUp(self):
        _log.set_suppress_console(True)
        self.sink = _Sink()
        self.tee = _log._TeeStream(self.sink)

    def emit(self, text):
        # Mirror CPython print(): payload and newline are separate writes.
        self.tee.write(text)
        self.tee.write("\n")

    def test_suppressed_lines_leave_no_blank_lines(self):
        self.emit("[Coworker] health check ping")
        self.emit("[🛠️Coworker] startup trace: 42")
        self.assertEqual(self.sink.data, "")
        # No blanks folded back into the console stream.
        self.assertNotIn("\n", self.sink.data)

    def test_warnings_and_errors_pass_with_newline(self):
        self.emit("[⚠️Coworker] outdated build")
        self.emit("[Coworker] load FAILED — missing dll")
        self.assertIn("outdated build", self.sink.data)
        self.assertIn("FAILED", self.sink.data)
        # Each kept a single trailing newline (no doubled blank lines).
        self.assertNotIn("\n\n", self.sink.data)

    def test_mixed_stream_preserves_only_real_blank_lines(self):
        self.emit("[Coworker] hidden line")
        self.emit("[⚠️Coworker] warning line")
        self.emit("blend | user message")
        self.tee.write("\n")  # Blender's own deliberate blank line
        console = self.sink.data
        self.assertNotIn("hidden line", console)
        self.assertIn("warning line", console)
        self.assertIn("blend | user message", console)
        self.assertEqual(console.count("\n\n"), 1, repr(console))

    def test_multiline_suppressed_chunk(self):
        # A single chunk may contain newlines; the whole payload is dropped
        # (with its trailing newline) when it carries the addon prefix.
        self.tee.write("[Coworker] line1\nline2\n")
        self.assertEqual(self.sink.data, "")

    def test_non_prefixed_multiline_passes(self):
        self.tee.write("traceback frame 1\ntraceback frame 2\n")
        self.assertIn("frame 1", self.sink.data)
        self.assertIn("frame 2", self.sink.data)

    def test_flush_passthrough(self):
        self.tee.flush()
        self.assertEqual(self.sink.flushes, 1)


if __name__ == "__main__":
    unittest.main()
