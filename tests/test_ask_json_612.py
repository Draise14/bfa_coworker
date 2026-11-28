# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for ``ask_json`` (#612, Highlights H2): schema-checked model answers, one retry.

Two kinds of stand-in, neither a real model:

* a scripted transport function (the answers are fixed texts), and
* a stand-in HTTP server that speaks the chat endpoint, driven through the REAL
  ``llm_transport.openai_chat_completions``, so ``extra_body`` is seen on the wire.

Cases: valid; invalid then valid (the retry names the error); invalid twice (reported,
exactly two requests); a server that refuses ``response_format`` (asked again without it,
remembered); a server that ignores it; an engine that gives no answer; External Agent /
Harness; the schema checker; finding JSON in prose; Follow Subject on the helper.

No Blender needed.  Run with::

    python -m unittest tests.test_ask_json_612 -v
"""

__all__ = ()

import importlib
import importlib.util
import json
import sys
import threading
import types
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_ADDON = _REPO / "addon" / "bfa_coworker"


def _pkg() -> str:
    name = "bfacw_askjson_pkg"
    if name not in sys.modules:
        pkg = types.ModuleType(name)
        pkg.__path__ = [str(_ADDON)]
        sys.modules[name] = pkg
    return name


aj = importlib.import_module(_pkg() + ".ask_json")
lt = importlib.import_module(_pkg() + ".llm_transport")
rf = importlib.import_module(_pkg() + ".reframe_follow")

SCHEMA = {
    "type": "object",
    "properties": {
        "candidates": {
            "type": "array", "maxItems": 3,
            "items": {"type": "object",
                      "properties": {"first": {"type": "integer", "minimum": 1},
                                     "last": {"type": "integer", "minimum": 1},
                                     "label": {"type": "string", "maxLength": 60}},
                      "required": ["first", "last"]},
        },
    },
    "required": ["candidates"],
}
GOOD = {"candidates": [{"first": 214, "last": 231, "label": "why we dropped the old rig"}]}
MESSAGES = [{"role": "user", "content": "Name ranges."}]


def _reply(text):
    return {"choices": [{"message": {"content": text}}]}


class _Script:
    """A transport that answers from a list of texts and records every request."""

    def __init__(self, *texts):
        self.texts = list(texts)
        self.calls = []

    def __call__(self, url, messages, tools, key, model, **kw):
        self.calls.append({"url": url, "messages": messages, "tools": tools, "key": key, "model": model, **kw})
        if not self.texts:
            return None
        text = self.texts.pop(0)
        return None if text is None else _reply(text)


def _ask(transport, url="http://example.test/v1/chat/completions", **kw):
    return aj.ask_json(MESSAGES, SCHEMA, target=(url, "", ""), transport=transport, **kw)


class _Fresh(unittest.TestCase):
    def setUp(self):
        aj.reset_schema_memory()


class TestAskJson(_Fresh):

    def test_valid_first_time(self):
        t = _Script(json.dumps(GOOD))
        r = _ask(t)
        self.assertEqual(r.value, GOOD)
        self.assertEqual((r.attempts, r.schema_sent), (1, True))
        self.assertEqual(len(t.calls), 1)
        call = t.calls[0]
        self.assertIsNone(call["tools"])
        self.assertEqual(call["chat_mode"], "ASK")
        rf_field = call["extra_body"]["response_format"]
        self.assertEqual(rf_field["type"], "json_schema")
        self.assertEqual(rf_field["json_schema"]["schema"], SCHEMA)
        self.assertIn("JSON only", call["messages"][-1]["content"])
        self.assertEqual(MESSAGES, [{"role": "user", "content": "Name ranges."}])   # caller's list untouched

    def test_invalid_then_valid_names_the_error(self):
        t = _Script('{"candidates": [{"first": "a", "last": 3}]}', json.dumps(GOOD))
        r = _ask(t)
        self.assertEqual((r.value, r.attempts), (GOOD, 2))
        self.assertEqual(len(t.calls), 2)
        retry = t.calls[1]["messages"]
        self.assertEqual(retry[-2]["role"], "assistant")
        self.assertIn("$.candidates[0].first", retry[-1]["content"])
        self.assertIn("expected integer", retry[-1]["content"])

    def test_invalid_twice_is_reported(self):
        t = _Script("not json at all", '{"candidates": 5}', json.dumps(GOOD))
        with self.assertRaises(aj.AskFailed) as cm:
            _ask(t)
        self.assertEqual(len(t.calls), 2)            # one retry only
        self.assertEqual(cm.exception.attempts, 2)
        self.assertIn("expected array", "; ".join(cm.exception.errors))
        self.assertEqual(cm.exception.text, '{"candidates": 5}')

    def test_retries_zero(self):
        t = _Script("nope", json.dumps(GOOD))
        with self.assertRaises(aj.AskFailed):
            _ask(t, retries=0)
        self.assertEqual(len(t.calls), 1)

    def test_json_in_prose_and_fences(self):
        t = _Script("Sure! Here you go:\n```json\n" + json.dumps(GOOD) + "\n```\nHope it helps.")
        self.assertEqual(_ask(t).value, GOOD)
        t = _Script("<think>{\"candidates\": 1}</think>" + json.dumps(GOOD))
        self.assertEqual(_ask(t).value, GOOD)

    def test_no_answer_is_unavailable(self):
        with self.assertRaises(aj.Unavailable):
            _ask(_Script())

    def test_server_that_refuses_the_schema_field(self):
        url = "http://refuses.test/v1/chat/completions"
        seen = []

        def transport(u, messages, tools, key, model, **kw):
            seen.append(kw.get("extra_body"))
            return None if kw.get("extra_body") else _reply(json.dumps(GOOD))
        r = aj.ask_json(MESSAGES, SCHEMA, target=(url, "", ""), transport=transport)
        self.assertEqual((r.value, r.schema_sent, r.attempts), (GOOD, False, 1))
        self.assertEqual(len(seen), 2)               # tried with, then without
        aj.ask_json(MESSAGES, SCHEMA, target=(url, "", ""), transport=transport)
        self.assertEqual(len(seen), 3)               # remembered: asked without it at once
        self.assertIsNone(seen[-1])

    def test_dead_server_is_not_remembered_as_refusing(self):
        url = "http://down.test/v1/chat/completions"
        with self.assertRaises(aj.Unavailable):
            aj.ask_json(MESSAGES, SCHEMA, target=(url, "", ""), transport=lambda *a, **k: None)
        t = _Script(json.dumps(GOOD))
        aj.ask_json(MESSAGES, SCHEMA, target=(url, "", ""), transport=t)
        self.assertIsNotNone(t.calls[0]["extra_body"])

    def test_send_schema_off(self):
        t = _Script(json.dumps(GOOD))
        r = _ask(t, send_schema=False)
        self.assertFalse(r.schema_sent)
        self.assertIsNone(t.calls[0]["extra_body"])

    def test_server_that_ignores_the_field_is_still_checked(self):
        t = _Script("Here: {\"candidates\": [{\"first\": 0, \"last\": 2}]}", json.dumps(GOOD))
        r = _ask(t)
        self.assertEqual(r.attempts, 2)              # first = 0 breaks minimum 1

    def test_thinking_off_for_local_only(self):
        t = _Script(json.dumps(GOOD))
        _ask(t, url="http://127.0.0.1:8081/v1/chat/completions")
        self.assertEqual(t.calls[0]["thinking_budget_tokens"], 0)
        t = _Script(json.dumps(GOOD))
        _ask(t, url="https://api.example/v1/chat/completions")
        self.assertIsNone(t.calls[0]["thinking_budget_tokens"])
        t = _Script(json.dumps(GOOD))
        _ask(t, url="http://127.0.0.1:8081/v1/chat/completions", thinking_off=False)
        self.assertIsNone(t.calls[0]["thinking_budget_tokens"])

    def test_image_message_gets_the_instruction_as_a_text_block(self):
        msgs = [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:x"}}]}]
        out = aj._with_instruction(msgs, SCHEMA)
        self.assertEqual(len(out[0]["content"]), 2)
        self.assertEqual(out[0]["content"][1]["type"], "text")
        self.assertEqual(len(msgs[0]["content"]), 1)


class TestEngines(unittest.TestCase):

    def _cfg(self, **kw):
        base = dict(mode="local", local_port=8081, remote_api_url="", remote_api_key="", remote_model="")
        base.update(kw)
        return types.SimpleNamespace(**base)

    def test_external_engines_say_so_in_a_way_the_caller_can_act_on(self):
        for mode in ("EXTERNAL_AGENT", "EXTERNAL_HARNESS"):
            with self.assertRaises(aj.Unavailable) as cm:
                aj.engine_target(mode, self._cfg(), True, purpose="Find Highlights")
            self.assertEqual(cm.exception.kind, "external")
            self.assertIn("Find Highlights", str(cm.exception))

    def test_local_and_remote_targets(self):
        self.assertEqual(aj.engine_target("LOCAL_LLM", self._cfg(), True),
                         ("http://127.0.0.1:8081/v1/chat/completions", "", ""))
        with self.assertRaises(aj.Unavailable) as cm:
            aj.engine_target("LOCAL_LLM", self._cfg(), False)
        self.assertEqual(cm.exception.kind, "no_engine")
        remote = self._cfg(mode="remote", remote_api_url="https://api.example/v1", remote_api_key="k",
                           remote_model="m")
        self.assertEqual(aj.engine_target("REMOTE_API", remote, False),
                         ("https://api.example/v1/chat/completions", "k", "m"))
        with self.assertRaises(aj.Unavailable):
            aj.engine_target("REMOTE_API", self._cfg(), False)

    def test_vision_is_only_asked_for_when_needed(self):
        no_eyes = lambda url: {"modalities": {"vision": False}}  # noqa: E731
        aj.engine_target("LOCAL_LLM", self._cfg(), True, fetch=no_eyes)
        with self.assertRaises(aj.Unavailable) as cm:
            aj.engine_target("LOCAL_LLM", self._cfg(), True, vision=True, fetch=no_eyes)
        self.assertEqual(cm.exception.kind, "no_vision")

    def test_stub_replaces_the_transport_but_not_the_checking(self):
        stub = _Script("bad", json.dumps(GOOD))
        old = aj.STUB
        aj.STUB = stub
        try:
            aj.reset_schema_memory()
            r = aj.resolve_asker("Find Highlights")(MESSAGES, SCHEMA)
        finally:
            aj.STUB = old
        self.assertEqual((r.value, r.attempts), (GOOD, 2))


class TestValidate(unittest.TestCase):

    def test_types(self):
        self.assertEqual(aj.validate(3, {"type": "integer"}), [])
        self.assertEqual(aj.validate(3.0, {"type": "integer"}), [])
        self.assertTrue(aj.validate(3.5, {"type": "integer"}))
        self.assertTrue(aj.validate(True, {"type": "integer"}))
        self.assertTrue(aj.validate(True, {"type": "number"}))
        self.assertEqual(aj.validate(None, {"type": ["number", "null"]}), [])
        self.assertTrue(aj.validate("x", {"type": ["number", "null"]}))
        self.assertTrue(aj.validate(float("nan"), {"type": "number"}))

    def test_object_rules(self):
        schema = {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"],
                  "additionalProperties": False}
        self.assertEqual(aj.validate({"a": "x"}, schema), [])
        self.assertIn('$: missing "a"', aj.validate({}, schema))
        self.assertIn('$: unexpected "b"', aj.validate({"a": "x", "b": 1}, schema))
        # Without additionalProperties: false, extra keys are ignored (the design: "anything else is ignored").
        self.assertEqual(aj.validate({"a": "x", "b": 1}, {**schema, "additionalProperties": True}), [])

    def test_bounds_and_enum(self):
        self.assertTrue(aj.validate(4, {"type": "integer", "maximum": 3}))
        self.assertTrue(aj.validate("abc", {"type": "string", "maxLength": 2}))
        self.assertTrue(aj.validate([1, 2, 3, 4], {"type": "array", "maxItems": 3}))
        self.assertTrue(aj.validate([], {"type": "array", "minItems": 1}))
        self.assertTrue(aj.validate("c", {"enum": ["a", "b"]}))
        self.assertEqual(aj.validate("a", {"enum": ["a", "b"]}), [])

    def test_extract_candidates(self):
        self.assertEqual(list(aj.extract_candidates('{"a": 1}')), [{"a": 1}])
        self.assertEqual(list(aj.extract_candidates("x {\"a\": 1} y [2]")), [{"a": 1}, [2]])
        self.assertEqual(list(aj.extract_candidates("")), [])
        self.assertEqual(list(aj.extract_candidates(None)), [])
        self.assertEqual(list(aj.extract_candidates("reasoning {\"z\": 0}</think>{\"a\": 1}")), [{"a": 1}])


# --- through the real transport, against a stand-in server ---------------------------------

class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0) or 0))
        body = json.loads(raw)
        self.server.bodies.append(body)
        status, payload = self.server.respond(body)
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


class TestOverTheWire(unittest.TestCase):
    """The real ``llm_transport.openai_chat_completions`` against a stand-in chat server."""

    def setUp(self):
        aj.reset_schema_memory()
        self.state = types.SimpleNamespace(error="", error_full="", error_kind="", warning="", is_thinking=False)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.server.bodies = []
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = "http://127.0.0.1:{:d}/v1/chat/completions".format(self.server.server_address[1])

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def _transport(self, *args, **kwargs):
        with lt.using(self.state, threading.Event()):
            return lt.openai_chat_completions(*args, **kwargs)

    def _ask(self, **kw):
        return aj.ask_json(MESSAGES, SCHEMA, target=(self.url, "", ""), transport=self._transport, **kw)

    def test_schema_travels_and_a_valid_answer_passes(self):
        self.server.respond = lambda body: (200, _reply(json.dumps(GOOD)))
        r = self._ask()
        self.assertEqual((r.value, r.attempts, r.schema_sent), (GOOD, 1, True))
        body = self.server.bodies[0]
        self.assertEqual(body["response_format"]["json_schema"]["schema"], SCHEMA)
        self.assertEqual(body["chat_template_kwargs"], {"enable_thinking": False})   # local: thinking off
        self.assertEqual(body["thinking_budget_tokens"], 1)
        self.assertNotIn("tools", body)

    def test_invalid_then_valid(self):
        answers = ["{\"candidates\": [{\"first\": 5}]}", json.dumps(GOOD)]
        self.server.respond = lambda body: (200, _reply(answers.pop(0)))
        r = self._ask()
        self.assertEqual((r.value, r.attempts), (GOOD, 2))
        self.assertEqual(len(self.server.bodies), 2)
        self.assertIn("response_format", self.server.bodies[1])      # kept on the retry

    def test_invalid_twice(self):
        self.server.respond = lambda body: (200, _reply("{}"))
        with self.assertRaises(aj.AskFailed) as cm:
            self._ask()
        self.assertEqual(len(self.server.bodies), 2)
        self.assertIn('missing "candidates"', "; ".join(cm.exception.errors))

    def test_server_without_schema_support(self):
        def respond(body):
            if "response_format" in body:
                return 400, {"error": {"code": 400, "message": "unsupported field response_format"}}
            return 200, _reply(json.dumps(GOOD))
        self.server.respond = respond
        r = self._ask()
        self.assertEqual((r.value, r.schema_sent), (GOOD, False))
        self.assertEqual(["response_format" in b for b in self.server.bodies], [True, False])
        self._ask()                                                    # remembered
        self.assertEqual(["response_format" in b for b in self.server.bodies], [True, False, False])


class TestFollowSubjectOnTheHelper(_Fresh):

    def _png(self):
        import tempfile
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = str(Path(tmp.name) / "f.png")
        Path(path).write_bytes(b"\x89PNG\r\n\x1a\nfake")
        return path

    def test_checked_answer(self):
        t = _Script('{"x": 0.25}')
        ask = rf.make_asker(("http://h/v1/chat/completions", "", ""), t)
        self.assertEqual(ask(self._png(), "shot", 1), 0.25)
        self.assertEqual(t.calls[0]["extra_body"]["response_format"]["json_schema"]["schema"], rf.X_SCHEMA)

    def test_null_means_no_subject(self):
        ask = rf.make_asker(("http://h/v1/chat/completions", "", ""), _Script('{"x": null}'))
        self.assertIsNone(ask(self._png(), "shot", 1))

    def test_wrong_then_right_costs_one_retry(self):
        t = _Script('{"x": 4}', '{"x": 0.4}')
        ask = rf.make_asker(("http://h/v1/chat/completions", "", ""), t)
        self.assertEqual(ask(self._png(), "shot", 1), 0.4)
        self.assertEqual(len(t.calls), 2)

    def test_hopeless_answer_falls_back_to_the_old_lenient_reading(self):
        ask = rf.make_asker(("http://h/v1/chat/completions", "", ""), _Script("about 60%", "about 60%"))
        self.assertEqual(ask(self._png(), "shot", 1), 0.6)
        ask = rf.make_asker(("http://h/v1/chat/completions", "", ""), _Script("no idea", "no idea"))
        self.assertIsNone(ask(self._png(), "shot", 1))

    def test_dead_engine_still_raises_unavailable(self):
        ask = rf.make_asker(("http://h/v1/chat/completions", "", ""), lambda *a, **k: None)
        with self.assertRaises(rf.Unavailable):
            ask(self._png(), "shot", 1)
        self.assertIs(rf.Unavailable, aj.Unavailable)


if __name__ == "__main__":
    unittest.main()
