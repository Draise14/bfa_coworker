# SPDX-FileCopyrightText: 2026 Blender Authors
# SPDX-License-Identifier: GPL-3.0-or-later

"""
#487: tool results reach the model right.

1. Pictures: an image block survives ``_call_mcp_tool_sync``; the request
   after a screenshot carries the picture for a model that takes pictures
   (Local: llama-server's ``/props`` says so; Remote: the preference) and a
   one-line note for one that doesn't (no image block, no error); a render
   tool's file is shown the same way; PNG and JPEG keep their type; pictures
   are size-capped like attachments; only the newest few of a turn are sent;
   the stored history never holds the picture, also after compaction.
2. One envelope: an asked step that runs at once, an asked step that is
   itself deferred, a declined one and a step that isn't asked all answer
   with exactly one ``{"status", "result"}`` envelope.

The turn tests reuse the fake llama-server + MCP harness from
``test_turn_loop_integration`` (real HTTP, real turn loop).
"""

import base64
import importlib
import json
import os
import socket
import sys
import tempfile
import types
import unittest
from pathlib import Path

from tests.test_turn_loop_integration import (
    _EXECUTE_CODE_TOOL,
    _TurnLoopTestBase,
    _start_fake_server,
)

_ADDON = Path(__file__).resolve().parents[1] / "addon" / "bfa_coworker"
_PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x01" * 300).decode()
_JPEG = base64.b64encode(b"\xff\xd8\xff\xe0" + b"\x02" * 300).decode()


def _tool(name):
    return {"name": name, "description": name, "inputSchema": {"type": "object", "properties": {}}}


def _calls(*names, content="Looking."):
    """One assistant message calling each tool in *names* (in one batch)."""
    return {"content": content, "finish_reason": "tool_calls", "tool_calls": [
        {"id": "call_{:d}".format(i), "type": "function", "index": i,
         "function": {"name": n, "arguments": json.dumps(
             {"output_path": "r.png"} if n.startswith("render") else {"size_limit_in_bytes": i})}}
        for i, n in enumerate(names)]}


def _image_urls(message):
    content = message.get("content")
    return [b["image_url"]["url"] for b in content if isinstance(b, dict) and isinstance(b.get("image_url"), dict)] \
        if isinstance(content, list) else []


def _all_urls(request):
    return [u for m in request.get("messages") or [] for u in _image_urls(m)]


class _PictureTurns(_TurnLoopTestBase):

    def _mk(self, script, tools, vision=None):
        self.server.shutdown()
        self.server.server_close()
        self.server = _start_fake_server(script, mcp_tools=[_EXECUTE_CODE_TOOL] + [_tool(t) for t in tools])
        self.server.vision = vision
        self.port = self.server.server_address[1]
        cfg = self.lm.LLMConfig()
        cfg.mode, cfg.local_port, cfg.local_ctx_size, cfg.local_max_tokens = "local", self.port, 8192, 1024
        cfg.thinking_budget_tokens = 0
        self.lm.set_config(cfg)
        self.lm._runtime_vision_cache = None

    def _shots(self, *blocks, tool="get_screenshot_of_window_as_image"):
        self.server.named_tool_replies[tool] = [{"__content__": list(b)} for b in blocks]

    def _turn(self, message="render a preview and tell me what you see", remote=False):
        self.state.conversation_history = self.state.conversation_history or [
            {"role": "system", "content": "You are a helpful agent."}]
        url = "http://127.0.0.1:{:d}/v1/chat/completions".format(self.port) if remote else None
        self._pin_fake_bpy()
        try:
            history = self.ac.run_conversation_turn(message, on_text=lambda _t: None, chat_mode="AGENT",
                                                    llm_url=url, model="fake-model", mcp_port=self.port)
        finally:
            self._unpin_fake_bpy()
        self.assertEqual(self.state.error, "")
        with self.server.lock:
            reqs = [r for r in self.server.requests if r not in self.server.memory_writer_calls]
        return history, reqs


class TestCallKeepsImageBlocks(_PictureTurns):

    def test_image_block_survives_call_mcp_tool_sync(self):
        self._mk(["ok"], ["get_screenshot_of_window_as_image"])
        self._shots([{"type": "image", "data": _JPEG, "mimeType": "image/jpeg"}],
                    [{"type": "image", "data": _PNG, "mimeType": "image/png"}])
        found = []
        text = self.ac._call_mcp_tool_sync("get_screenshot_of_window_as_image", {}, self.port, images=found)
        self.assertEqual((text, found), ("", [("image/jpeg", _JPEG)]))      # JPEG keeps its type
        # Without the list (other callers) a picture-only result says what it made.
        self.assertEqual(self.ac._call_mcp_tool_sync("get_screenshot_of_window_as_image", {}, self.port),
                         "The tool returned 1 picture(s) (image/png).")

    def test_text_and_images_are_both_kept(self):
        text, images = self.ac._parse_sse_tool_response("data: " + json.dumps({"jsonrpc": "2.0", "result": {
            "content": [{"type": "text", "text": "Rendered frame 12."},
                        {"type": "image", "data": _PNG, "mimeType": "image/png"}]}}))
        self.assertEqual((text, images), ("Rendered frame 12.", [("image/png", _PNG)]))


class TestPicturesReachTheModel(_PictureTurns):

    def test_vision_model_gets_the_picture_local(self):
        self._mk([_calls("get_screenshot_of_window_as_image"), {"content": "A grey window."}],
                 ["get_screenshot_of_window_as_image"], vision=True)
        self._shots([{"type": "image", "data": _PNG, "mimeType": "image/png"}])
        history, reqs = self._turn()
        self.assertEqual(len(reqs), 2)
        msgs = reqs[1]["messages"]
        roles = [m["role"] for m in msgs]
        tool_at = roles.index("tool")
        self.assertIn("in the next message", msgs[tool_at]["content"])
        note = msgs[tool_at + 1]                      # right after the tool results
        self.assertEqual(note["role"], "user")
        self.assertEqual(_image_urls(note), ["data:image/png;base64," + _PNG])
        self.assertIn("get_screenshot_of_window_as_image (call call_0)", note["content"][0]["text"])
        self.assertNotIn("tool_images", note)          # bookkeeping keys stay off the wire
        self.assertEqual(_all_urls(reqs[0]), [])
        # The stored history keeps text and ids only.
        dumped = json.dumps(history)
        self.assertNotIn(_PNG[:40], dumped)
        self.assertNotIn("data:image", dumped)
        stored = [m for m in history if m.get("tool_images")]
        self.assertEqual(len(stored), 1)
        self.assertIsInstance(stored[0]["content"], str)

    def test_text_only_model_gets_a_note_and_no_error_local(self):
        self._mk([_calls("get_screenshot_of_window_as_image"), {"content": "I can't see it."}],
                 ["get_screenshot_of_window_as_image"], vision=False)
        self._shots([{"type": "image", "data": _PNG, "mimeType": "image/png"}])
        history, reqs = self._turn()
        self.assertEqual(_all_urls(reqs[1]), [])
        tool_msg = next(m for m in reqs[1]["messages"] if m["role"] == "tool")
        self.assertIn("made a picture (PNG", tool_msg["content"])
        self.assertIn("this model doesn't take pictures", tool_msg["content"])
        saved = tool_msg["content"].split("saved at ", 1)[1].split(". It isn't", 1)[0]
        self.assertTrue(os.path.isfile(saved), saved)                   # where it is
        self.assertFalse(any(m.get("tool_images") for m in history))

    def test_remote_follows_the_preference(self):
        for flag in (True, False):
            with self.subTest(flag=flag):
                self._mk([_calls("get_screenshot_of_window_as_image"), {"content": "done"}],
                         ["get_screenshot_of_window_as_image"])
                self._shots([{"type": "image", "data": _JPEG, "mimeType": "image/jpeg"}])
                self.state.conversation_history = []
                self.state.prefetched_remote_images = flag
                _history, reqs = self._turn(remote=True)
                self.assertEqual(_all_urls(reqs[1]), ["data:image/jpeg;base64," + _JPEG] if flag else [])

    def test_render_tool_file_is_shown_or_named(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "r.png")
            with open(path, "wb") as fh:
                fh.write(base64.b64decode(_PNG))
            answer = {"status": "ok", "result": {"status": "ok", "filepath": path}}
            for vision in (True, False):
                with self.subTest(vision=vision):
                    self._mk([_calls("render_viewport_to_path"), {"content": "done"}],
                             ["render_viewport_to_path"], vision=vision)
                    self.server.named_tool_replies["render_viewport_to_path"] = [answer]
                    self.state.conversation_history = []
                    _history, reqs = self._turn()
                    tool_msg = next(m for m in reqs[1]["messages"] if m["role"] == "tool")
                    self.assertIn(path.replace("\\", "\\\\"), tool_msg["content"])   # the tool's own answer stays
                    if vision:
                        self.assertEqual(_all_urls(reqs[1]), ["data:image/png;base64," + _PNG])
                    else:
                        self.assertEqual(_all_urls(reqs[1]), [])
                        self.assertIn("the picture at " + path, tool_msg["content"])

    def test_only_the_newest_pictures_of_a_turn_are_sent(self):
        shots = [base64.b64encode(b"\x89PNG\r\n\x1a\n" + bytes([i]) * 50).decode() for i in range(5)]
        self._mk([_calls(*["get_screenshot_of_window_as_image"] * 5), {"content": "done"}],
                 ["get_screenshot_of_window_as_image"], vision=True)
        self._shots(*[[{"type": "image", "data": s, "mimeType": "image/png"}] for s in shots])
        _history, reqs = self._turn()
        keep = self.ac.tool_images.KEEP
        self.assertEqual(_all_urls(reqs[1]), ["data:image/png;base64," + s for s in shots[-keep:]])
        carriers = [m for m in reqs[1]["messages"] if _image_urls(m)]
        self.assertEqual(len(carriers), 1, "one note after the batch carries them")

    def test_next_turn_does_not_resend_last_turns_pictures(self):
        self._mk([_calls("get_screenshot_of_window_as_image"), {"content": "seen"}, {"content": "hello"}],
                 ["get_screenshot_of_window_as_image"], vision=True)
        self._shots([{"type": "image", "data": _PNG, "mimeType": "image/png"}])
        self._turn()
        _history, reqs = self._turn("thanks")
        self.assertEqual(_all_urls(reqs[-1]), [])

    def test_history_does_not_grow_by_the_picture_after_compaction(self):
        big = base64.b64encode(b"\x89PNG\r\n\x1a\n" + os.urandom(200_000)).decode()
        self._mk([_calls("get_screenshot_of_window_as_image"), {"content": "seen"}],
                 ["get_screenshot_of_window_as_image"], vision=True)
        self._shots([{"type": "image", "data": big, "mimeType": "image/png"}])
        history = [{"role": "system", "content": "You are a helpful agent."}]
        for i in range(30):
            history.append({"role": "user", "content": "old {:d} ".format(i) + "x" * 1400})
            history.append({"role": "assistant", "content": "old reply {:d} ".format(i) + "y" * 1400})
        self.state.conversation_history = history
        history, reqs = self._turn()
        self.assertTrue(self.sm.store.memory_block, "compaction fired")
        dumped = json.dumps(history)
        self.assertNotIn(big[:60], dumped)
        self.assertLess(len(dumped), 120_000, "the stored history holds no picture")
        # What was sent stays within the attachment cap's reach.
        sent = _all_urls(reqs[-1])
        limit = self.ac.chat_attachments.ATTACHMENT_LIMIT_BYTES
        for uri in sent:
            self.assertLessEqual(len(uri.split(",", 1)[1]) * 3 // 4, 2 * limit)


class TestSizeCap(unittest.TestCase):
    """Pictures are capped like attachments (chat_attachments' encoder)."""

    @classmethod
    def setUpClass(cls):
        name = "bfacw_toolimg_pkg"
        if name not in sys.modules:
            pkg = types.ModuleType(name)
            pkg.__path__ = [str(_ADDON)]
            sys.modules[name] = pkg
        cls.ti = importlib.import_module(name + ".tool_images")
        cls.ca = importlib.import_module(name + ".chat_attachments")

    def setUp(self):
        self._saved = sys.modules.get("imbuf")
        self.addCleanup(lambda: sys.modules.__setitem__("imbuf", self._saved) if self._saved
                        else sys.modules.pop("imbuf", None))

    def test_small_png_and_jpeg_pass_through(self):
        self.assertEqual(self.ti.data_uri_from_block("image/png", _PNG), "data:image/png;base64," + _PNG)
        self.assertEqual(self.ti.data_uri_from_block("image/jpeg", _JPEG), "data:image/jpeg;base64," + _JPEG)

    def test_an_oversized_picture_is_downscaled(self):
        writes = []

        class Buf:
            def __init__(self, size):
                self.size, self.file_type = size, "PNG"

            def copy(self):
                return Buf(self.size)

            def resize(self, size, method="BILINEAR"):
                self.size = size

            def free(self):
                pass

        def write(buf, filepath=None):
            writes.append(buf.size)
            with open(filepath, "wb") as fh:
                fh.write(b"\x89PNG" + b"x" * (buf.size[0] * buf.size[1] // 1000))

        sys.modules["imbuf"] = types.SimpleNamespace(load=lambda p: Buf((2000, 1000)), write=write)
        raw = b"\x89PNG\r\n\x1a\n" + b"\x03" * 2000
        uri = self.ti.data_uri_from_block("image/png", base64.b64encode(raw).decode(), limit=1000)
        self.assertTrue(uri.startswith("data:image/png;base64,"))
        self.assertLessEqual(len(base64.b64decode(uri.split(",", 1)[1])), 1000)
        self.assertGreater(len(writes), 1, "it was shrunk until it fit")

    def test_a_picture_that_cant_be_shrunk_is_refused(self):
        sys.modules["imbuf"] = types.SimpleNamespace()        # no imbuf API: the raw bytes come back
        raw = b"\x89PNG\r\n\x1a\n" + b"\x04" * 5000
        self.assertIsNone(self.ti.data_uri_from_block("image/png", base64.b64encode(raw).decode(), limit=1000))

    def test_broken_data_is_refused(self):
        self.assertIsNone(self.ti.data_uri_from_block("image/png", ""))

    def test_file_of_reads_only_render_tools_with_a_real_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "r.png")
            Path(path).write_bytes(b"\x89PNG")
            ok = json.dumps({"status": "ok", "result": {"status": "ok", "filepath": path}})
            self.assertEqual(self.ti.file_of("render_viewport_to_path", ok), path)
            self.assertEqual(self.ti.file_of("execute_blender_code", ok), "")
            self.assertEqual(self.ti.file_of("render_viewport_to_path", ok.replace("r.png", "gone.png")), "")
            self.assertEqual(self.ti.file_of("render_viewport_to_path", json.dumps(
                {"status": "ok", "result": {"status": "error", "message": "no camera"}})), "")


class TestLocalVisionProbe(unittest.TestCase):
    """llm_manager.get_runtime_vision: /props first, the launched projector otherwise."""

    def setUp(self):
        from tests.test_turn_loop_integration import _load_llm_manager
        self.lm = _load_llm_manager()
        self.server = _start_fake_server(["ok"])
        self.port = self.server.server_address[1]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def test_props_modalities_decide(self):
        self.server.vision = True
        self.assertTrue(self.lm.get_runtime_vision(self.port))
        self.lm._runtime_vision_cache = None
        self.server.vision = False
        self.assertFalse(self.lm.get_runtime_vision(self.port))

    def test_older_server_falls_back_to_the_launched_projector(self):
        self.server.vision = None                          # no modalities in /props
        self.lm._last_launched_model_path = Path("C:/models/m.gguf")
        self.lm._resolve_mmproj_path = lambda p: Path("C:/models/mmproj.gguf")
        self.assertTrue(self.lm.get_runtime_vision(self.port))
        self.lm._runtime_vision_cache = None
        self.lm._resolve_mmproj_path = lambda p: None
        self.assertFalse(self.lm.get_runtime_vision(self.port))

    def test_unreachable_is_no_and_not_cached(self):
        port = self.port
        self.server.shutdown()
        self.server.server_close()
        self.assertFalse(self.lm.get_runtime_vision(port))
        self.assertIsNone(self.lm._runtime_vision_cache)
        self.server = _start_fake_server(["ok"])


class TestOneEnvelope(unittest.TestCase):
    """Every answer the bridge sends has exactly one envelope (asked or not)."""

    @classmethod
    def setUpClass(cls):
        name = "bfacw_envelope_pkg"
        if name not in sys.modules:
            pkg = types.ModuleType(name)
            pkg.__path__ = [str(_ADDON)]
            sys.modules[name] = pkg
        cls.rs = importlib.import_module(name + ".risky_steps")
        cls.lg = importlib.import_module(name + ".loop_guard")
        cls.dt = importlib.import_module(name + ".deferred_tool")

    def setUp(self):
        self.rs.APPROVALS.end_session()
        self.rs.auto_declines[0] = False
        self.dt._deferred_clients.clear()

    class _R:
        def __init__(self, response, check_fn=None):
            self.response, self.check_fn = response, check_fn

    def _sent(self, response, check):
        """What the bridge sends: *response* at once, or the deferred answer of *check*."""
        if check is None:
            return response
        bridge, client = socket.socketpair()
        bridge.setblocking(False)
        client.settimeout(2.0)
        self.addCleanup(client.close)
        self.dt.add(bridge, check, True, str(response.get("stdout", "")), str(response.get("stderr", "")))
        buf = b""
        for _ in range(4):
            self.dt.poll()
        while not buf.endswith(b"\0"):
            buf += client.recv(65536)
        return json.loads(buf[:-1].decode("utf-8"))

    def _assert_one(self, answer):
        self.assertIn(answer.get("status"), ("ok", "error"))
        inner = answer.get("result")
        self.assertFalse(isinstance(inner, dict) and set(inner) <= {"status", "result", "message", "stdout",
                                                                   "stderr", "preflight_notes"}
                         and "result" in inner, "nested envelope: {!r}".format(answer))

    def _ask(self, execute, tool="execute_blender_code"):
        return self.rs.wrap("obj.location.x = 1", True, "", execute, tool=tool, kind=tool)

    def test_asked_then_synchronous(self):
        response, check = self._ask(lambda c, s: self._R({"status": "ok", "result": {"moved": 1}, "stdout": "hi"}))
        self.rs.APPROVALS.answer("once")
        answer = self._sent(response, check)
        self.assertEqual(answer, {"status": "ok", "result": {"moved": 1}, "stdout": "hi"})
        self._assert_one(answer)

    def test_asked_then_deferred(self):
        response, check = self._ask(
            lambda c, s: self._R({"stdout": "rendering"}, lambda: {"status": "ok", "filepath": "/t/r.png"}),
            tool="render_viewport_to_path")
        self.rs.APPROVALS.answer("once")
        answer = self._sent(response, check)
        self.assertEqual(answer, {"status": "ok", "result": {"status": "ok", "filepath": "/t/r.png"},
                                  "stdout": "rendering"})
        self._assert_one(answer)

    def test_asked_then_declined_is_an_error_at_the_top(self):
        response, check = self._ask(lambda c, s: self.fail("ran"))
        self.rs.APPROVALS.answer("deny")
        answer = self._sent(response, check)
        self.assertEqual(answer["status"], "error")
        self.assertIn("declined", answer["message"])
        self.assertNotIn("result", answer)

    def test_not_asked(self):
        self.rs.APPROVALS.answer("always")       # nothing open: harmless
        response = {"status": "ok", "result": {"moved": 1}}
        self._assert_one(self._sent(response, None))
        # A plain deferred tool-code checker (not asked) still gets its one envelope.
        answer = self._sent({}, lambda: {"status": "ok", "filepath": "/t/r.png"})
        self.assertEqual(answer, {"status": "ok", "result": {"status": "ok", "filepath": "/t/r.png"}})

    def test_repeat_card_around_an_asked_step(self):
        guard = self.lg.LoopGuard()
        approvals = self.rs.APPROVALS

        def proceed():
            return self._ask(lambda c, s: self._R({"status": "ok", "result": {"moved": 2}}))
        for _ in range(2):
            self.lg.bridge_wrap(guard, approvals, False, "execute_blender_code", {"code": "x"}, "s", proceed)
            approvals.answer("once")
        response, check = self.lg.bridge_wrap(guard, approvals, False, "execute_blender_code", {"code": "x"}, "s",
                                              proceed)
        approvals.answer("once")                 # the repeat card
        self.assertIsNone(check())               # now the approval card is up
        approvals.answer("once")
        answer = self._sent(response, check)
        self.assertEqual(answer, {"status": "ok", "result": {"moved": 2}})


if __name__ == "__main__":
    unittest.main()
