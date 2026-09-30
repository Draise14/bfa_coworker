"""
Tests for graceful HTTP error handling in the LLM transport.

A local llama-server returns HTTP 400 when a request is larger than the
context window it was started with.  The transport used to read that error
body once into a local variable, never cache it, and then re-read an
already-exhausted stream in the final failure block -- so the user saw a
bare ``LLM request failed: HTTP Error 400: Bad Request`` with no reason and
the payload was retried identically five times.

These tests pin the fixed behaviour:

1. ``is_context_overflow`` recognises llama-server's context-window 400
   body and ``context_overflow_message`` names the real cause.

2. A context-window 400 fails fast (exactly one POST -- no pointless
   retries), sets ``error_kind == "context_overflow"`` so the turn loop can
   compact and retry, and surfaces the server body in ``error_full``.

3. Any other non-template 400 also fails fast and keeps the server body.

4. A successful response clears ``error_kind``.

``llm_transport`` has no bpy dependency, so it is loaded directly from
source with importlib rather than extracted function-by-function.

Run with::

    python -m unittest tests.test_llm_transport_errors -v
"""

__all__ = ()

import importlib.util
import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LT_PATH = os.path.join(_REPO, "addon", "bfa_coworker", "llm_transport.py")


def _load_transport_module():
    """Load llm_transport.py standalone (it imports no bpy)."""
    spec = importlib.util.spec_from_file_location("_bfacw_llm_transport", _LT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_lt = _load_transport_module()

# Context-window 400 body as llama-server actually returns it.
_OVERFLOW_BODY = json.dumps({
    "error": {
        "code": 400,
        "message": "the request exceeds the available context size, "
                   "try increasing it",
        "type": "exceed_context_size_error",
    }
})


class _FakeState:
    """Minimal stand-in for AgentState (only the fields the transport sets)."""

    def __init__(self) -> None:
        self.error = ""
        self.error_full = ""
        self.error_kind = ""
        self.warning = ""


class _ResponseHandler(BaseHTTPRequestHandler):
    """Serve a queue of ``(status, body)`` responses; record every request."""

    def do_POST(self) -> None:  # noqa: N802 (stdlib name)
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length:
            self.rfile.read(length)
        self.server.request_count += 1  # type: ignore[attr-defined]
        responses = self.server.responses  # type: ignore[attr-defined]
        status, body = responses.pop(0) if responses else (200, "{}")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body.encode())))
        self.end_headers()
        self.wfile.write(body.encode())

    def log_message(self, *args) -> None:  # silence
        pass


class _ServerCase(unittest.TestCase):
    def setUp(self) -> None:
        self.state = _FakeState()
        self.stop_event = threading.Event()
        _lt.bind(self.state, self.stop_event)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _ResponseHandler)
        self.server.responses = []  # type: ignore[attr-defined]
        self.server.request_count = 0  # type: ignore[attr-defined]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address
        self.url = "http://{:s}:{:d}/v1/chat/completions".format(host, port)

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def _queue(self, *responses) -> None:
        self.server.responses.extend(responses)  # type: ignore[attr-defined]

    def _post(self):
        return _lt.openai_chat_completions(
            self.url, [{"role": "user", "content": "hi"}], [],
            api_key=None, model=None, max_tokens=64,
        )


class TestOverflowClassification(unittest.TestCase):
    """Pure classification tests (no HTTP)."""

    def test_recognises_context_overflow_body(self):
        self.assertTrue(_lt.is_context_overflow(_OVERFLOW_BODY))
        self.assertTrue(_lt.is_context_overflow(
            "the request exceeds the available context size"))

    def test_ignores_template_and_other_bodies(self):
        self.assertFalse(_lt.is_context_overflow(""))
        self.assertFalse(_lt.is_context_overflow(
            "Unable to generate parser for this template"))
        self.assertFalse(_lt.is_context_overflow("Internal server error"))

    def test_overflow_message_names_the_cause(self):
        msg = _lt.context_overflow_message(_OVERFLOW_BODY)
        self.assertIn("context window", msg.lower())
        self.assertIn("exceed_context_size_error", msg)


class TestContextOverflow400(_ServerCase):
    """A context-window 400 must fail fast and be classified."""

    def test_fails_fast_without_retrying(self):
        self._queue((400, _OVERFLOW_BODY))
        result = self._post()
        self.assertIsNone(result)
        # Fail fast: exactly one POST, not the full retry budget.
        self.assertEqual(self.server.request_count, 1)  # type: ignore[attr-defined]
        self.assertEqual(self.state.error_kind, "context_overflow")

    def test_error_surfaces_the_real_server_reason(self):
        self._queue((400, _OVERFLOW_BODY))
        self._post()
        combined = (self.state.error + self.state.error_full).lower()
        self.assertIn("context", combined)
        # The bare urllib reason must not be the only thing shown.
        self.assertIn("exceed", combined)


class TestGeneric400(_ServerCase):
    """Any other 400 fails fast and keeps the server body."""

    def test_generic_400_fails_fast_with_body(self):
        body = json.dumps({"error": {"message": "messages must not be empty"}})
        self._queue((400, body))
        result = self._post()
        self.assertIsNone(result)
        self.assertEqual(self.server.request_count, 1)  # type: ignore[attr-defined]
        self.assertIn("messages must not be empty", self.state.error_full)


class TestSuccess(_ServerCase):
    """A 200 response clears the last error classification."""

    def test_success_clears_error_kind(self):
        self.state.error_kind = "context_overflow"  # stale from a prior call
        self._queue((200, json.dumps({
            "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
        })))
        result = self._post()
        self.assertIsNotNone(result)
        self.assertEqual(self.state.error_kind, "")


if __name__ == "__main__":
    unittest.main()
