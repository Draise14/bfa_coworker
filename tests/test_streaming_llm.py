"""
Tests for the streaming LLM request layer (issue #69).

The Remote API chat mode was a black box: a non-streaming request returned
nothing until the full generation completed (up to 600 s), Stop could not
cancel it, and token usage was never captured.  The streaming layer fixes
all three and these tests pin the behaviour:

1. ``_parse_sse_chunk`` must accumulate content, reasoning (under either
   provider field name), tool-call argument deltas, finish_reason, and the
   final ``include_usage`` chunk's usage object.

2. ``_assemble_stream_result`` must rebuild the standard
   ``choices[0].message`` shape the turn loop already consumes, so the
   hardened history-repair/trim pipeline is unaffected.

3. ``_openai_chat_completions_stream`` must abort the SSE read when
   ``_stop_event`` is set mid-stream, returning the partial content as a
   normal response (marked partial by the caller).

4. When the stream fails before the first token (endpoint does not
   support streaming, 200 body is a JSON error, connection refused), the
   helper must return ``None`` so the caller falls back to the
   non-streaming request; a mid-stream drop must return the partial
   instead.

5. ``AgentState.record_usage`` must accumulate per-call usage into both
   turn and session totals and ignore malformed usage objects.

6. The turn loop must route every LLM call through the stream wrapper so
   streamed responses still flow through the hardened truncation path
   (``_repair_tool_call_pairs`` / ``_fit_history_to_budget``) on
   tool-call iterations.

Loaded from source (the module imports bpy, which is not available in
the unit-test environment).

Run with::

    python -m unittest tests.test_streaming_llm -v
"""

__all__ = ()

import io
import json
import os
import time
import types
import typing
import unittest
import urllib.error
import urllib.request  # noqa: F401  (the extracted helper uses urllib.request.urlopen)
from pathlib import Path
from unittest import mock

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_AC_PATH = os.path.join(_REPO, "addon", "bfa_coworker", "agent_controller.py")
_LT_PATH = os.path.join(_REPO, "addon", "bfa_coworker", "llm_transport.py")


def _load_source():
    """Return agent_controller + llm_transport sources, concatenated.

    Since the transport split, the streaming helpers live in
    llm_transport.py; searching the concatenation keeps the loader
    agnostic about which module a helper landed in.
    """
    parts = []
    for path in (_AC_PATH, _LT_PATH):
        with open(path, "r", encoding="utf-8") as fh:
            parts.append(fh.read())
    return "\n".join(parts)


def _extract_func(source: str, name: str, extra: dict | None = None):
    """Extract one top-level function from the concatenated source."""
    marker = "\ndef {:s}(".format(name)
    start = source.find(marker)
    if start < 0:
        raise ImportError("{:s} not found in source".format(name))
    start += 1

    end = len(source)
    search_from = start + 100
    for m in ["\ndef _", "\ndef ", "\nclass ", "\n# ---"]:
        idx = source.find(m, search_from)
        if 0 <= idx < end:
            end = idx

    func_source = source[start:end]
    mod = types.ModuleType("_ac_stream_extract")
    mod.__dict__["json"] = json
    mod.__dict__["Any"] = object
    # Annotations are evaluated at exec time and the signature subscripts
    # Callable, so it must be the real (subscriptable) typing alias.
    mod.__dict__["Callable"] = typing.Callable
    mod.__dict__["__file__"] = _AC_PATH
    if extra:
        mod.__dict__.update(extra)
    exec(compile(func_source, _AC_PATH, "exec"), mod.__dict__)
    return mod.__dict__[name]


_SOURCE = _load_source()

_strip_think_tags = _extract_func(_SOURCE, "_strip_think_tags")
_extract_reasoning_delta = _extract_func(
    _SOURCE, "_extract_reasoning_delta",
    {"_h": lambda name: _strip_think_tags if name == "strip_think_tags" else None},
)
_parse_sse_chunk = _extract_func(
    _SOURCE, "_parse_sse_chunk",
    {
        "_extract_reasoning_delta": _extract_reasoning_delta,
        # The chunk parser routes <think> stripping through the injected
        # helper namespace (llm_transport._h).
        "_h": lambda name: _strip_think_tags if name == "strip_think_tags" else None,
    },
)
_assemble_stream_result = _extract_func(_SOURCE, "_assemble_stream_result")
_stop_event = __import__("threading").Event()


def _quiet_print(*args, **kwargs):
    """Swallow the helper's console logging.

    The helper logs emoji-prefixed lines that crash on a cp1252 console
    (Windows CI) and are irrelevant to the assertions here.
    """

_openai_chat_completions_stream = _extract_func(
    _SOURCE,
    "openai_chat_completions_stream",
    {
        "json": json,
        "urllib": urllib,
        "urllib_request_urlopen": None,
        "_DEFAULT_MAX_TOKENS": 1024,
        "_DEFAULT_TEMPERATURE_CODE": 0.2,
        "_DEFAULT_TEMPERATURE_PROSE": 0.7,
        "_CHAT_SAMPLING": {},
        "_STREAM_TIMEOUT": 600.0,
        "time": time,
        "_parse_sse_chunk": _parse_sse_chunk,
        "_assemble_stream_result": _assemble_stream_result,
        "_stop_requested": _stop_event.is_set,
        "_agent_state": types.SimpleNamespace(warning=""),
        "print": _quiet_print,
    },
)


def _sse(chunks: list[dict]) -> bytes:
    """Encode OpenAI-style chunks into an SSE response body."""
    lines = []
    for chunk in chunks:
        lines.append("data: " + json.dumps(chunk))
    lines.append("data: [DONE]")
    return ("\n\n".join(lines) + "\n\n").encode("utf-8")


class _FakeResponse:
    """Minimal stand-in for the object returned by urlopen()."""

    def __init__(self, body: bytes, status: int = 200):
        self._stream = io.BytesIO(body)
        self.status = status

    def __iter__(self):
        return iter(self._stream)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _urlopen_returning(body: bytes, status: int = 200):
    return lambda req, timeout=None: _FakeResponse(body, status)


class TestParseSSEChunk(unittest.TestCase):
    """_parse_sse_chunk accumulates deltas into the accumulator dict."""

    def _acc(self):
        return {
            "content": "",
            "reasoning": "",
            "tool_calls": [],
            "finish_reason": "",
            "usage": None,
        }

    def test_content_delta_appended(self):
        acc = self._acc()
        _parse_sse_chunk(
            {"choices": [{"delta": {"content": "Hello"}}]}, acc,
        )
        _parse_sse_chunk(
            {"choices": [{"delta": {"content": " world"}}]}, acc,
        )
        self.assertEqual(acc["content"], "Hello world")

    def test_reasoning_content_field(self):
        acc = self._acc()
        _parse_sse_chunk(
            {"choices": [{"delta": {"reasoning_content": "thinking"}}]}, acc,
        )
        self.assertEqual(acc["reasoning"], "thinking")

    def test_reasoning_field_openrouter(self):
        acc = self._acc()
        _parse_sse_chunk(
            {"choices": [{"delta": {"reasoning": "pondering"}}]}, acc,
        )
        self.assertEqual(acc["reasoning"], "pondering")

    def test_think_tags_stripped_from_reasoning(self):
        acc = self._acc()
        _parse_sse_chunk(
            {"choices": [{"delta": {"reasoning_content": "<think>deep</think>"}}]}, acc,
        )
        self.assertEqual(acc["reasoning"], "deep")

    def test_tool_call_deltas_merged(self):
        acc = self._acc()
        tool_call_delta_1 = {
            "index": 0, "id": "call_1",
            "function": {"name": "execute_blender_code", "arguments": ""},
        }
        tool_call_delta_2 = {
            "index": 0,
            "function": {"arguments": '{"code": "print(1'},
        }
        tool_call_delta_3 = {
            "index": 0,
            "function": {"arguments": ')"}'},
        }
        _parse_sse_chunk(
            {"choices": [{"delta": {"tool_calls": [tool_call_delta_1]}}]}, acc,
        )
        _parse_sse_chunk(
            {"choices": [{"delta": {"tool_calls": [tool_call_delta_2]}}]}, acc,
        )
        _parse_sse_chunk(
            {"choices": [{"delta": {"tool_calls": [tool_call_delta_3]}}]}, acc,
        )
        self.assertEqual(len(acc["tool_calls"]), 1)
        self.assertEqual(acc["tool_calls"][0]["id"], "call_1")
        self.assertEqual(acc["tool_calls"][0]["function"]["name"], "execute_blender_code")
        self.assertEqual(acc["tool_calls"][0]["function"]["arguments"], '{"code": "print(1)"}')

    def test_finish_reason_captured(self):
        acc = self._acc()
        _parse_sse_chunk(
            {"choices": [{"delta": {}, "finish_reason": "stop"}]}, acc,
        )
        self.assertEqual(acc["finish_reason"], "stop")

    def test_usage_chunk_captured(self):
        acc = self._acc()
        _parse_sse_chunk(
            {"choices": [], "usage": {
                "prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15,
            }}, acc,
        )
        self.assertEqual(acc["usage"]["total_tokens"], 15)

    def test_malformed_chunk_ignored(self):
        acc = self._acc()
        # Missing/None deltas must not raise.
        _parse_sse_chunk({"choices": [{}]}, acc)
        _parse_sse_chunk({}, acc)
        self.assertEqual(acc["content"], "")


class TestAssembleStreamResult(unittest.TestCase):
    """_assemble_stream_result rebuilds the non-streaming response shape."""

    def test_plain_content(self):
        result = _assemble_stream_result({
            "content": "answer",
            "reasoning": "",
            "tool_calls": [],
            "finish_reason": "stop",
            "usage": None,
        })
        self.assertEqual(result["choices"][0]["message"]["content"], "answer")
        self.assertEqual(result["choices"][0]["finish_reason"], "stop")
        self.assertNotIn("usage", result)

    def test_reasoning_mapped_to_reasoning_content(self):
        result = _assemble_stream_result({
            "content": "answer",
            "reasoning": "chain of thought",
            "tool_calls": [],
            "finish_reason": "stop",
            "usage": None,
        })
        msg = result["choices"][0]["message"]
        self.assertEqual(msg.get("reasoning_content"), "chain of thought")

    def test_tool_calls_get_ids(self):
        result = _assemble_stream_result({
            "content": "",
            "reasoning": "",
            "tool_calls": [{
                "id": "", "type": "function",
                "function": {"name": "load_tools", "arguments": "{}"},
            }],
            "finish_reason": "tool_calls",
            "usage": None,
        })
        tcs = result["choices"][0]["message"]["tool_calls"]
        self.assertEqual(len(tcs), 1)
        self.assertTrue(tcs[0]["id"])
        self.assertEqual(result["choices"][0]["finish_reason"], "tool_calls")

    def test_usage_forwarded(self):
        usage = {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}
        result = _assemble_stream_result({
            "content": "a", "reasoning": "", "tool_calls": [],
            "finish_reason": "stop", "usage": usage,
        })
        self.assertEqual(result["usage"], usage)


class TestStreamRequest(unittest.TestCase):
    """_openai_chat_completions_stream end-to-end over a fake connection."""

    def setUp(self):
        _stop_event.clear()

    def test_stream_returns_assembled_response(self):
        body = _sse([
            {"choices": [{"delta": {"content": "Hi"}}]},
            {"choices": [{"delta": {"content": " there"}}]},
            {"choices": [{"delta": {}, "finish_reason": "stop"}]},
            {"choices": [], "usage": {"prompt_tokens": 7, "completion_tokens": 2, "total_tokens": 9}},
        ])
        statuses = []
        with mock.patch(
            "urllib.request.urlopen",
            _urlopen_returning(body),
        ):
            result = _openai_chat_completions_stream(
                "http://x/v1/chat/completions", [{"role": "user", "content": "hi"}],
                [], on_status=statuses.append,
            )
        self.assertEqual(result["choices"][0]["message"]["content"], "Hi there")
        self.assertEqual(result["usage"]["total_tokens"], 9)
        self.assertTrue(statuses)  # phase statuses were emitted

    def test_stream_callbacks_receive_text_and_reasoning(self):
        body = _sse([
            {"choices": [{"delta": {"reasoning_content": "hmm"}}]},
            {"choices": [{"delta": {"content": "ok"}}]},
        ])
        texts, reasonings = [], []
        with mock.patch("urllib.request.urlopen", _urlopen_returning(body)):
            _openai_chat_completions_stream(
                "http://x/v1/chat/completions", [{"role": "user", "content": "hi"}],
                [], on_stream_text=texts.append, on_stream_reasoning=reasonings.append,
            )
        self.assertEqual(reasonings[-1], "hmm")
        self.assertEqual(texts[-1], "ok")

    def test_elapsed_status_phases_and_format(self):
        """Per-phase elapsed status (issue #69): the reasoning phase shows
        a 'Reasoning... (Ns)' status, the content phase a
        'Generating... (Ns)' status, and the phases arrive in order.
        Never asserts exact seconds -- only the format/prefix and that the
        elapsed number is a non-negative integer string.
        """
        body = _sse([
            # Chunk 1: reasoning only -> Reasoning... phase.
            {"choices": [{"delta": {"reasoning_content": "thinking"}}]},
            # Chunk 2: content starts -> Generating... phase.
            {"choices": [{"delta": {"content": "answer"}}]},
            # Chunk 3: mixed (both present) -> neither phase re-emitted
            # (the generating status is already live via the text).
            {"choices": [{"delta": {
                "content": " more", "reasoning_content": " more",
            }}]},
        ])
        statuses = []
        with mock.patch("urllib.request.urlopen", _urlopen_returning(body)):
            _openai_chat_completions_stream(
                "http://x/v1/chat/completions", [{"role": "user", "content": "hi"}],
                [], on_status=statuses.append,
            )
        # Phase order: contact, first token, reasoning elapsed, generating elapsed.
        self.assertEqual(statuses[0], "Contacting API...")
        self.assertEqual(statuses[1], "Generating...")
        self.assertEqual(statuses[2], "Reasoning... (0s)")
        self.assertEqual(statuses[3], "Generating... (0s)")
        # No further phase statuses after the mixed chunk.
        self.assertEqual(len(statuses), 4)
        # Format: the elapsed part is always a non-negative integer.
        import re
        for s in statuses[2:]:
            m = re.match(r"^(Reasoning|Generating)\.\.\. \((\d+)s\)$", s)
            self.assertIsNotNone(m, "bad elapsed status format: {:s}".format(s))

    def test_elapsed_status_reflects_fake_clock(self):
        """The elapsed seconds come from time.monotonic at request start;
        with a fake clock the number must track the injected value."""
        body = _sse([
            {"choices": [{"delta": {"reasoning_content": "think"}}]},
        ])
        fake_clock = [1000.0]

        def _fake_monotonic():
            fake_clock[0] += 7.0  # each call advances 7 s
            return fake_clock[0]

        statuses = []
        with mock.patch("urllib.request.urlopen", _urlopen_returning(body)):
            with mock.patch("time.monotonic", _fake_monotonic):
                _openai_chat_completions_stream(
                    "http://x/v1/chat/completions",
                    [{"role": "user", "content": "hi"}],
                    [], on_status=statuses.append,
                )
        reasoning_statuses = [s for s in statuses if s.startswith("Reasoning...")]
        self.assertEqual(len(reasoning_statuses), 1)
        # _request_start is captured on one monotonic call, the elapsed
        # read on another: 7 s apart regardless of absolute values.
        self.assertEqual(reasoning_statuses[0], "Reasoning... (7s)")

    def test_no_reasoning_no_reasoning_status(self):
        """A stream with content but no reasoning never shows the
        Reasoning phase -- it goes straight to Generating."""
        body = _sse([
            {"choices": [{"delta": {"content": "answer"}}]},
        ])
        statuses = []
        with mock.patch("urllib.request.urlopen", _urlopen_returning(body)):
            _openai_chat_completions_stream(
                "http://x/v1/chat/completions", [{"role": "user", "content": "hi"}],
                [], on_status=statuses.append,
            )
        self.assertNotIn(
            True, [s.startswith("Reasoning...") for s in statuses],
        )
        self.assertTrue(any(s.startswith("Generating...") for s in statuses))

    def test_stop_mid_stream_returns_partial(self):
        # A response whose first line arrives normally, but where the stop
        # event is set before the next line is read: the reader must break
        # after the first chunk and return it as a partial response.
        class _StopAfterFirstChunk:
            status = 200

            def __init__(self):
                self._chunks = [
                    b'data: {"choices": [{"delta": {"content": "partial answer"}}]}\n\n',
                    b'data: {"choices": [{"delta": {"content": " never"}}]}\n\n',
                ]
                self._n = 0

            def __iter__(self):
                return self

            def __next__(self):
                self._n += 1
                if self._n == 2:
                    _stop_event.set()
                if self._n <= 2:
                    return self._chunks[self._n - 1]
                raise StopIteration

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        with mock.patch(
            "urllib.request.urlopen", lambda req, timeout=None: _StopAfterFirstChunk()
        ):
            result = _openai_chat_completions_stream(
                "http://x/v1/chat/completions", [{"role": "user", "content": "hi"}],
                [],
            )
        self.assertIsNotNone(result)
        self.assertEqual(result["choices"][0]["message"]["content"], "partial answer")
        self.assertNotIn("never", result["choices"][0]["message"]["content"])

    def test_failure_before_first_token_returns_none(self):
        def _boom(req, timeout=None):
            raise urllib.error.URLError("refused")

        with mock.patch("urllib.request.urlopen", _boom):
            result = _openai_chat_completions_stream(
                "http://x/v1/chat/completions", [{"role": "user", "content": "hi"}],
                [],
            )
        self.assertIsNone(result)

    def test_mid_stream_drop_returns_partial(self):
        class _DroppingResponse:
            status = 200

            def __init__(self):
                self._n = 0

            def __iter__(self):
                return self

            def __next__(self):
                self._n += 1
                if self._n == 1:
                    return b'data: {"choices": [{"delta": {"content": "so far"}}]}\n\n'
                raise OSError("connection reset")

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        with mock.patch("urllib.request.urlopen", lambda req, timeout=None: _DroppingResponse()):
            result = _openai_chat_completions_stream(
                "http://x/v1/chat/completions", [{"role": "user", "content": "hi"}],
                [],
            )
        self.assertEqual(result["choices"][0]["message"]["content"], "so far")

    def test_stream_error_inside_200_body_returns_none(self):
        body = _sse([{"error": {"message": "quota exceeded"}}])
        with mock.patch("urllib.request.urlopen", _urlopen_returning(body)):
            result = _openai_chat_completions_stream(
                "http://x/v1/chat/completions", [{"role": "user", "content": "hi"}],
                [],
            )
        self.assertIsNone(result)

    def test_request_body_uses_stream_true(self):
        captured = {}

        def _capture(req, timeout=None):
            captured["body"] = json.loads(req.data.decode("utf-8"))
            return _FakeResponse(_sse([{"choices": [{"delta": {"content": "x"}}]}]))

        with mock.patch("urllib.request.urlopen", _capture):
            _openai_chat_completions_stream(
                "http://x/v1/chat/completions", [{"role": "user", "content": "hi"}],
                [], model="gpt-test",
            )
        self.assertTrue(captured["body"]["stream"])
        self.assertEqual(captured["body"]["stream_options"], {"include_usage": True})
        self.assertEqual(captured["body"]["model"], "gpt-test")


class TestAgentStateRecordUsage(unittest.TestCase):
    """AgentState.record_usage accumulates turn + session token totals."""

    def _extract_state(self):
        source = _SOURCE
        start = source.find("@dataclass\nclass AgentState:")
        end = source.find("\n_agent_state = AgentState()", start)
        mod = types.ModuleType("_ac_state_extract")
        import dataclasses
        mod.__dict__["field"] = dataclasses.field
        mod.__dict__["dataclass"] = dataclasses.dataclass
        mod.__dict__["Any"] = object
        exec(compile(source[start:end], _AC_PATH, "exec"), mod.__dict__)
        return mod.__dict__["AgentState"]

    def test_accumulates_turn_and_session(self):
        State = self._extract_state()
        state = State()
        state.record_usage({"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})
        state.record_usage({"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})
        self.assertEqual(state.turn_usage["total_tokens"], 30)
        self.assertEqual(state.session_usage["prompt_tokens"], 20)

    def test_ignores_malformed_usage(self):
        State = self._extract_state()
        state = State()
        state.record_usage(None)
        state.record_usage({"prompt_tokens": "many"})
        state.record_usage({"prompt_tokens": -3})
        self.assertEqual(state.turn_usage, {})
        self.assertEqual(state.session_usage, {})


class TestTurnLoopUsesStreamingWrapper(unittest.TestCase):
    """The turn loop must route every LLM call through the stream wrapper.

    This keeps the streamed responses flowing through the hardened
    truncation pipeline (repair -> strip -> trim -> budget) that lives in
    the same loop, per the tier3 plan's pre-flight requirement.
    """

    def _loop_body(self):
        source = _SOURCE
        start = source.find("def _run_conversation_turn_inner(")
        end = source.find("\ndef ping_agent(", start)
        return source[start:end]

    def test_all_call_sites_route_through_llm_request(self):
        loop_body = self._loop_body()
        # Each raw helper may be referenced only inside the _llm_request
        # wrapper (one call each); every request site goes through the
        # wrapper, which also records usage.  The request helpers now live
        # in llm_transport under public names.
        self.assertEqual(loop_body.count("openai_chat_completions_stream("), 1)
        self.assertEqual(loop_body.count("openai_chat_completions("), 1)
        self.assertGreaterEqual(loop_body.count("_llm_request("), 3)

    def test_run_conversation_turn_exposes_stream_callbacks(self):
        source = _SOURCE
        start = source.find("def run_conversation_turn(")
        end = source.find("\ndef _run_conversation_turn_inner(", start)
        signature = source[start:end]
        self.assertIn("on_stream_text", signature)
        self.assertIn("on_stream_reasoning", signature)

    def test_abort_keeps_partial_message(self):
        loop_body = self._loop_body()
        # Stop mid-turn must keep the partial streamed content (marked).
        self.assertIn('"partial": True', loop_body)

    def test_llm_request_writes_deltas_into_rendered_state(self):
        """Stream deltas must land in AgentState.streaming_text/
        reasoning_text -- the fields the Workshop renders as
        'Coworker (live)' -- while generation is still in progress.

        Regression guard for the rendering gap: _update_streaming in the
        UI only redraws, so if the wrapper passes the raw callbacks
        through, deltas never reach the rendered state and nothing
        appears until the full response arrives.
        """
        loop_body = self._loop_body()
        # The wrapper must write the delta into the rendered state, not
        # merely forward it to the UI callback.
        self.assertIn("_agent_state.streaming_text = text", loop_body)
        self.assertIn("_agent_state.reasoning_text = text", loop_body)
        # ...and still forward to the caller's callback.
        self.assertIn("if on_stream_text:", loop_body)
        self.assertIn("if on_stream_reasoning:", loop_body)

    def test_deltas_visible_before_completion(self):
        """End-to-end through the real ``_llm_request`` wrapper: while the
        SSE stream is mid-flight, the state the Workshop renders already
        holds the streamed text and reasoning.
        """
        import threading as _threading
        import textwrap as _textwrap

        # Build an SSE body whose final chunks arrive only after the test
        # releases a pause, so the rendered state can be sampled mid-stream
        # with the reasoning and first text chunk already dispatched.
        reasoning_chunk = b'data: {"choices": [{"delta": {"reasoning_content": "live chain of thought"}}]}\n\n'
        text_chunk = b'data: {"choices": [{"delta": {"content": "live partial text"}}]}\n\n'
        final_chunk = b'data: {"choices": [{"delta": {"content": " plus final"}, '
        final_chunk += b'"finish_reason": "stop"}], "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}}\n\n'
        done_chunk = b"data: [DONE]\n\n"

        state = types.SimpleNamespace(
            warning="",
            streaming_text="",
            reasoning_text="",
            is_thinking=True,
            turn_usage={},
            session_usage={},
            record_usage=lambda usage: None,
        )
        d = {
            "json": json,
            "urllib": urllib,
            "typing": typing,
            "Any": object,
            "Callable": typing.Callable,
            "_DEFAULT_MAX_TOKENS": 1024,
            "_DEFAULT_TEMPERATURE_CODE": 0.2,
            "_DEFAULT_TEMPERATURE_PROSE": 0.7,
            "_CHAT_SAMPLING": {},
            "_STREAM_TIMEOUT": 600.0,
            "time": time,
            "_parse_sse_chunk": _parse_sse_chunk,
            "_assemble_stream_result": _assemble_stream_result,
            "_stop_requested": lambda: False,
            "_agent_state": state,
            "print": _quiet_print,
            # Closure variables the wrapper reads from the turn-loop scope.
            "llm_url": "http://x/v1/chat/completions",
            "api_key": None,
            "model": "gpt-test",
            "max_tokens": 1024,
            "chat_mode": "AGENT",
            "on_status": None,
            "on_stream_text": None,
            "on_stream_reasoning": None,
        }
        start = _SOURCE.find("def openai_chat_completions_stream(")
        end = _SOURCE.find("\ndef _mcp_tools_to_openai(", start)
        exec(compile(_SOURCE[start:end], _AC_PATH, "exec"), d)
        # The wrapper is nested in the turn loop at indent 4; dedent it so
        # it can be exec'd standalone with the closure vars supplied above.
        w_start = _SOURCE.find("    def _llm_request(")
        w_end = _SOURCE.find("\n    # Determine LLM URL.", w_start)
        exec(
            compile(_textwrap.dedent(_SOURCE[w_start:w_end]), _AC_PATH, "exec"), d,
        )
        llm_request = d["_llm_request"]

        body_lines = [reasoning_chunk, text_chunk, b"PAUSE", final_chunk, done_chunk]
        pause = _threading.Event()

        class _MidStreamResponse:
            status = 200

            def __iter__(self):
                return self

            def __next__(self):
                if body_lines:
                    line = body_lines.pop(0)
                    if line == b"PAUSE":
                        # The reasoning and text chunks are already consumed
                        # and dispatched; block here so the test can sample
                        # the rendered state mid-stream.
                        pause.wait(timeout=5)
                        return b""
                    return line
                raise StopIteration

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        with mock.patch(
            "urllib.request.urlopen",
            lambda req, timeout=None: _MidStreamResponse(),
        ):
            result_holder = {}

            def _run():
                result_holder["result"] = llm_request(
                    [{"role": "user", "content": "hi"}], [],
                )

            worker = _threading.Thread(target=_run, daemon=True)
            worker.start()
            # The reader blocks after consuming the reasoning and text
            # chunks; the rendered state must already hold both.  Poll
            # instead of sleeping so the test is not timing-dependent.
            midstream = _threading.Event()
            deadline = 50  # ~5 s of 0.1 s polls
            for _ in range(deadline):
                if state.streaming_text == "live partial text":
                    midstream.set()
                    break
                _threading.Event().wait(0.1)
            self.assertTrue(
                midstream.is_set(),
                "streamed text never appeared in the rendered state mid-stream",
            )
            self.assertEqual(state.reasoning_text, "live chain of thought")
            # Release the reader; the stream completes normally.
            pause.set()
            worker.join(timeout=10)
            self.assertFalse(worker.is_alive())
            result = result_holder["result"]
        self.assertEqual(
            result["choices"][0]["message"]["content"],
            "live partial text plus final",
        )
        # The full text remains in the rendered state after completion
        # (post-response assignment keeps the final message intact).
        self.assertEqual(state.streaming_text, "live partial text plus final")


if __name__ == "__main__":
    unittest.main()
