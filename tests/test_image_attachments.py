# SPDX-FileCopyrightText: 2026 Blender Authors
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Focused tests for issue #88 / Tier 3k: image attachments reaching the model.

Covers the send-path wiring in ``agent_controller``:

* ``run_conversation_turn(attachments=...)`` injects one ``image_url``
  block per attachment into the CURRENT turn's user message (the one
  flagged ``turn_start``) in EVERY request of the turn, on the request
  copy only;
* the stored history keeps plain text -- an ``[attached: ...]`` marker,
  never a ``data:`` URI;
* the turn-start reset of ``_pending_image`` stays independent of
  ``user_attachments``;
* each injected image is counted at the fixed ``_SCREENSHOT_TOKENS``
  cost before budgeting;
* ``MessageQueue.enqueue`` / ``enqueue_message`` snapshot attachments
  at enqueue time.

Plus the user-facing half (operators, FileHandler, panel drawing):
registration, ``poll_drop`` areas, extension list, the main-thread
capture helper (including Send Once), and both panels drawing the
attachment row -- all runnable without Blender.

The turn tests reuse the fake llama-server + MCP bridge harness from
``test_turn_loop_integration`` (real HTTP, real turn loop).
"""

import json
import os
import types
import unittest

from tests.test_turn_loop_integration import (
    _EXECUTE_CODE_TOOL,
    _TurnLoopTestBase,
    _start_fake_server,
    _tool_call_msg,
)

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_AC_PATH = os.path.join(_REPO, "addon", "bfa_coworker", "agent_controller.py")

_URIS = ["data:image/png;base64,QUJD", "data:image/jpeg;base64,REVG"]
_NAMES = ["a.png", "b.jpg"]
_MARKER = "[attached: a.png, b.jpg]"


def _image_urls(message):
    """All ``image_url`` URLs carried by a message (empty for plain text)."""
    content = message.get("content")
    if not isinstance(content, list):
        return []
    return [
        block["image_url"]["url"]
        for block in content
        if isinstance(block, dict) and isinstance(block.get("image_url"), dict)
    ]


def _request_image_messages(request):
    """Messages of a captured LLM request that carry image blocks."""
    return [m for m in (request.get("messages") or []) if _image_urls(m)]


class TestImageAttachmentsInTurnLoop(_TurnLoopTestBase):
    """Drive the real turn loop with attachments through the fake servers."""

    def _mk_server(self, script):
        """Restart the fake server with a script + the MCP tool bridge."""
        self.server.shutdown()
        self.server.server_close()
        self.server = _start_fake_server(script, mcp_tools=[_EXECUTE_CODE_TOOL])
        self.port = self.server.server_address[1]
        cfg = self.lm.LLMConfig()
        cfg.mode = "local"
        cfg.local_port = self.port
        cfg.local_ctx_size = 8192
        cfg.local_max_tokens = 1024
        cfg.thinking_budget_tokens = 0
        self.lm.set_config(cfg)

    def _main_requests(self):
        with self.server.lock:
            return [r for r in self.server.requests
                    if r not in self.server.memory_writer_calls]

    def _run(self, message, chat_mode="AGENT", **kwargs):
        self._pin_fake_bpy()
        try:
            return self.ac.run_conversation_turn(
                message, on_text=lambda _t: None, chat_mode=chat_mode,
                llm_url=None, model="fake-model",
                mcp_port=self.port, **kwargs)
        finally:
            self._unpin_fake_bpy()

    def test_attachments_injected_on_every_request_and_history_stays_plain(self):
        """Two tool iterations + final reply: all three requests carry the
        images; the stored history carries only the marker."""
        self._mk_server([
            _tool_call_msg("call_1", "print('one')"),
            _tool_call_msg("call_2", "print('two')"),
            {"content": "all done"},
        ])
        self.state.conversation_history = [
            {"role": "system", "content": "You are a helpful agent."}]
        history = self._run(
            "look at this", attachments=list(_URIS),
            attachment_names=list(_NAMES))
        self.assertEqual(self.state.error, "")

        reqs = self._main_requests()
        self.assertEqual(len(reqs), 3)
        for req in reqs:
            carriers = _request_image_messages(req)
            self.assertEqual(
                len(carriers), 1,
                "exactly one message per request may carry images")
            msg = carriers[0]
            self.assertEqual(msg.get("role"), "user")
            self.assertEqual(_image_urls(msg), _URIS)
            # Text follows the blocks: the user prompt plus the marker.
            text_blocks = [b for b in msg["content"]
                           if isinstance(b, dict) and "text" in b]
            self.assertEqual(len(text_blocks), 1)
            self.assertEqual(text_blocks[0]["text"],
                             "look at this\n{:s}".format(_MARKER))

        # Stored history: plain text only -- no base64, marker present.
        dumped = json.dumps(history)
        self.assertNotIn("data:image", dumped)
        user_msgs = [m for m in history if m.get("role") == "user"]
        self.assertEqual(len(user_msgs), 1)
        self.assertIsInstance(user_msgs[0]["content"], str)
        self.assertIn(_MARKER, user_msgs[0]["content"])

        # The turn did not consume the attachments: every request re-reads
        # them from the state (they are cleared only by the next turn).
        self.assertEqual(self.state.user_attachments, _URIS)
        self.assertEqual(self.state.user_attachment_names, _NAMES)

    def test_next_turn_without_attachments_carries_no_images(self):
        """Attachments are per-turn: a later send re-installs them (here
        with none), so turn 1's images never leak into turn 2 -- and a
        later attach (turn 3) targets turn 3's message, not turn 1's
        older ``turn_start`` entry still sitting in history."""
        self._mk_server(["reply one", "reply two", "reply three"])
        self.state.conversation_history = [
            {"role": "system", "content": "You are a helpful agent."}]
        self._run("first", chat_mode="ASK",
                  attachments=list(_URIS), attachment_names=list(_NAMES))
        self.assertEqual(self.state.error, "")
        history = self._run("second", chat_mode="ASK")
        self.assertEqual(self.state.error, "")
        self._run("third", chat_mode="ASK",
                  attachments=list(_URIS), attachment_names=list(_NAMES))
        self.assertEqual(self.state.error, "")

        reqs = self._main_requests()
        self.assertEqual(len(reqs), 3)
        first = _request_image_messages(reqs[0])
        self.assertEqual(len(first), 1)
        self.assertEqual(_image_urls(first[0]), _URIS)
        self.assertEqual(_request_image_messages(reqs[1]), [])
        third = _request_image_messages(reqs[2])
        self.assertEqual(len(third), 1)
        self.assertEqual(_image_urls(third[0]), _URIS)
        text_blocks = [b for b in third[0]["content"]
                       if isinstance(b, dict) and "text" in b]
        self.assertEqual(text_blocks[0]["text"],
                         "third\n{:s}".format(_MARKER),
                         "must target the CURRENT turn's user message")

        self.assertEqual(self.state.user_attachments, _URIS)
        user_msgs = [m for m in history if m.get("role") == "user"]
        self.assertEqual(len(user_msgs), 3)
        self.assertIn(_MARKER, user_msgs[0]["content"])
        self.assertNotIn("[attached", user_msgs[1]["content"])
        self.assertNotIn("data:image", json.dumps(history))

    def test_pending_screenshot_reset_keeps_turn_attachments(self):
        """Turn start clears the stale MCP screenshot but keeps this turn's
        attachments -- the two states are independent."""
        self._mk_server(["solo"])
        self.state.conversation_history = [
            {"role": "system", "content": "You are a helpful agent."}]
        self.state._pending_image = "data:image/png;base64,U1RBTEU"
        self._run("bring image", chat_mode="ASK",
                  attachments=[_URIS[0]], attachment_names=[_NAMES[0]])
        self.assertEqual(self.state.error, "")

        reqs = self._main_requests()
        urls = [u for m in reqs[0]["messages"] for u in _image_urls(m)]
        self.assertEqual(urls, [_URIS[0]],
                         "attachment kept; stale pending screenshot dropped")
        self.assertIsNone(self.state._pending_image)
        self.assertEqual(self.state.user_attachments, [_URIS[0]])

    def test_each_image_costs_a_fixed_1500_tokens(self):
        """The injected blocks count at the fixed vision cost, not their
        base64 length, so budgeting sees them before the request goes out."""
        base = {"role": "user", "content": "hello"}
        with_images = {
            "role": "user",
            "content": (
                [{"type": "image_url", "image_url": {"url": u}} for u in _URIS]
                + [{"type": "text", "text": "hello"}]
            ),
        }
        self.assertEqual(self.ac._SCREENSHOT_TOKENS, 1500)
        delta = (self.ac._estimate_messages_tokens([with_images])
                 - self.ac._estimate_messages_tokens([base]))
        self.assertEqual(delta, 1500 * len(_URIS))

    def test_injection_runs_before_budgeting_and_targets_turn_start(self):
        """Source-level guard for the two easy-to-break ordering rules."""
        with open(_AC_PATH, "r", encoding="utf-8") as fh:
            src = fh.read()
        start = src.index("def _build_send_messages(")
        end = src.index("\n    iterations = 0", start)
        body = src[start:end]
        inject = body.index('_img_msg["content"] = _img_blocks')
        budget = body.index("_fit_history_to_budget(msgs, prompt_budget)")
        self.assertLess(inject, budget,
                        "attachments must be injected before budgeting")
        self.assertIn('msgs[_ti].get("turn_start")', body,
                      "injection must target the turn_start user message")

    def test_queue_item_snapshots_attachments_at_enqueue_time(self):
        """The queue captures a copy: later mutation cannot change a
        message already waiting, and plain sends default to None."""
        q = self.ac.MessageQueue()
        uris = [_URIS[0]]
        names = [_NAMES[0]]
        pos = q.enqueue("queued", attachments=uris, attachment_names=names)
        self.assertEqual(pos, 1)
        uris.append("data:image/png;base64,QQ==")
        names.append("later.png")
        item = q.dequeue()
        self.assertEqual(item["attachments"], [_URIS[0]])
        self.assertEqual(item["attachment_names"], [_NAMES[0]])

        q.enqueue("no images")
        item2 = q.dequeue()
        self.assertIsNone(item2["attachments"])
        self.assertIsNone(item2["attachment_names"])

    def test_enqueue_message_forwards_attachments_through_singleton(self):
        self.ac.enqueue_message(
            "via singleton", attachments=list(_URIS),
            attachment_names=list(_NAMES))
        item = self.ac.dequeue_message()
        self.assertEqual(item["message"], "via singleton")
        self.assertEqual(item["attachments"], _URIS)
        self.assertEqual(item["attachment_names"], _NAMES)


# ---------------------------------------------------------------------------
# Register / draw / send-path wiring -- runs without Blender.

_UI_PATH = os.path.join(_REPO, "addon", "bfa_coworker", "ui_chat.py")


def _ui_source() -> str:
    with open(_UI_PATH, "r", encoding="utf-8") as fh:
        return fh.read()


def _extract_ui_func(name, extra=None):
    """Exec one ui_chat module-level function with stubbed globals."""
    src = _ui_source()
    marker = "\ndef {:s}(".format(name)
    start = src.find(marker)
    if start < 0:
        raise AssertionError("{:s} not found in ui_chat.py".format(name))
    end = len(src)
    for pattern in ("\ndef _", "\ndef ", "\nclass ", "\n# ---"):
        idx = src.find(pattern, start + 100)
        if 0 <= idx < end:
            end = idx
    ns = {}
    if extra:
        ns.update(extra)
    exec(compile(src[start:end], _UI_PATH, "exec"), ns)  # noqa: S102
    return ns[name]


class _FakeLayout:
    """Records UI calls so draw helpers run without Blender."""

    def __init__(self, log):
        self._log = log

    def row(self, *args, **kwargs):
        self._log.append(("row", args, kwargs))
        return _FakeLayout(self._log)

    def box(self, *args, **kwargs):
        self._log.append(("box", args, kwargs))
        return _FakeLayout(self._log)

    def label(self, *args, **kwargs):
        self._log.append(("label", args, kwargs))

    def prop(self, *args, **kwargs):
        self._log.append(("prop", args, kwargs))

    def operator(self, *args, **kwargs):
        self._log.append(("operator", args, kwargs))

    def template_ID(self, *args, **kwargs):
        self._log.append(("template_ID", args, kwargs))

    def template_icon(self, *args, **kwargs):
        self._log.append(("template_icon", args, kwargs))


class TestChatAttachmentUI(unittest.TestCase):
    """Registration, drawing, and send-path wiring (no Blender needed)."""

    _NEW_CLASSES = (
        "BFACW_OT_chat_image_attach",
        "BFACW_OT_chat_capture_render",
        "BFACW_OT_chat_capture_screen",
        "BFACW_OT_chat_image_drop",
        "BFACW_FH_chat_drop",
    )

    def test_all_new_classes_are_registered_and_exported(self):
        src = _ui_source()
        reg_start = src.index("_classes = (")
        reg_end = src.index(")", reg_start)
        block = src[reg_start:reg_end]
        all_start = src.index("__all__ = (")
        all_end = src.index(")", all_start)
        exported = src[all_start:all_end]
        for name in self._NEW_CLASSES:
            self.assertIn(name + ",", block,
                          "{:s} missing from _classes".format(name))
            self.assertIn('"{:s}"'.format(name), exported,
                          "{:s} missing from __all__".format(name))

    def test_filehandler_matches_drop_operator_and_supported_extensions(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "chat_attachments_under_test",
            os.path.join(_REPO, "addon", "bfa_coworker",
                         "chat_attachments.py"))
        ca = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ca)
        src = _ui_source()
        self.assertIn('bl_import_operator = "bfacw.chat_image_drop"', src)
        expected = ";".join(ca.SUPPORTED_EXTENSIONS)
        self.assertIn('bl_file_extensions = "{:s}"'.format(expected), src)
        self.assertIn('context.area.type in {"VIEW_3D", "TEXT_EDITOR"}', src,
                      "poll_drop must accept VIEW_3D and TEXT_EDITOR")

    def test_capture_encodes_on_main_thread_and_send_once(self):
        fake = types.SimpleNamespace(
            image_to_data_uri=lambda img: "data:image/png;base64,QQ==",
            attachment_name=lambda img: "a.png",
        )
        capture = _extract_ui_func(
            "_capture_chat_attachment", {"chat_attachments": fake})
        props = types.SimpleNamespace(chat_image=None,
                                      chat_image_send_once=False)
        self.assertEqual(capture(props), (None, None))

        props.chat_image = object()
        attachments, names = capture(props)
        self.assertEqual(attachments, ["data:image/png;base64,QQ=="])
        self.assertEqual(names, ["a.png"])
        self.assertIsNotNone(props.chat_image, "sticky socket must survive")

        props.chat_image_send_once = True
        capture(props)
        self.assertIsNone(props.chat_image, "send-once must consume it")

        props.chat_image = object()
        fake.image_to_data_uri = lambda img: None
        self.assertEqual(capture(props), (None, None))
        self.assertIsNotNone(
            props.chat_image,
            "a failed encode must not consume the socket")

    def test_draw_attachment_row_layout(self):
        preview_calls = []
        draw = _extract_ui_func(
            "_draw_attachment_row",
            {"_draw_attachment_preview":
                lambda layout, img: preview_calls.append(img)})
        log = []
        props = types.SimpleNamespace(chat_image=object(),
                                      chat_image_send_once=True)
        draw(_FakeLayout(log), props)

        tpl = [c for c in log if c[0] == "template_ID"]
        self.assertEqual(len(tpl), 1, "the socket must be drawn once")
        self.assertEqual(tpl[0][1], (props, "chat_image"))
        self.assertEqual(tpl[0][2],
                         {"open": "image.open", "new": "image.new"})
        ops = [c[1][0] for c in log if c[0] == "operator"]
        for op_id in ("bfacw.chat_image_attach",
                      "bfacw.chat_capture_render",
                      "bfacw.chat_capture_screen"):
            self.assertIn(op_id, ops)
        toggles = [c for c in log if c[0] == "prop"
                   and c[1][1] == "chat_image_send_once"]
        self.assertEqual(len(toggles), 1, "send-once toggle must be drawn")
        # The row hands the socketed datablock to the preview helper.
        self.assertEqual(preview_calls, [props.chat_image],
                         "the attached image must be previewed")

    def test_attachment_preview_draws_thumbnail_and_degrades_gracefully(self):
        draw = _extract_ui_func(
            "_draw_attachment_preview",
            {"_ATTACHMENT_PREVIEW_SCALE": 8.0})

        # Nothing attached -> nothing drawn.
        log = []
        draw(_FakeLayout(log), None)
        self.assertEqual(log, [])

        class _NoPreview:
            """Datablock whose preview cannot be generated."""

            def preview_ensure(self):
                raise RuntimeError("no preview")

        log = []
        draw(_FakeLayout(log), _NoPreview())
        self.assertEqual(log, [], "a failed preview must not raise or draw")

        class _EmptyPreview:
            icon_id = 0

        class _NoIcon:
            def preview_ensure(self):
                return _EmptyPreview()

        log = []
        draw(_FakeLayout(log), _NoIcon())
        self.assertEqual(log, [], "icon_id 0 means no preview to show")

        class _Preview:
            icon_id = 4242

        class _WithPreview:
            def preview_ensure(self):
                return _Preview()

        log = []
        draw(_FakeLayout(log), _WithPreview())
        icons = [c for c in log if c[0] == "template_icon"]
        self.assertEqual(len(icons), 1, "the thumbnail must be drawn once")
        self.assertEqual(icons[0][2]["icon_value"], 4242)
        self.assertEqual(icons[0][2]["scale"], 8.0)

    def test_ensure_attachment_preview_is_best_effort(self):
        ensure = _extract_ui_func("_ensure_attachment_preview")
        ensure(None)  # no image -> no-op

        class _Broken:
            def preview_ensure(self):
                raise RuntimeError("no preview data")

        ensure(_Broken())  # must swallow: attaching must never raise

        class _Good:
            def __init__(self):
                self.calls = 0

            def preview_ensure(self):
                self.calls += 1

        img = _Good()
        ensure(img)
        self.assertEqual(img.calls, 1, "the preview must be requested once")

    def test_attach_to_socket_sets_image_and_warms_preview(self):
        warmed = []
        reported = []
        img = object()
        props = types.SimpleNamespace(chat_image=None)
        attach = _extract_ui_func(
            "_attach_to_socket",
            {"chat_attachments": types.SimpleNamespace(
                attachment_name=lambda i: "shot.png"),
             "_ensure_attachment_preview": lambda i: warmed.append(i)})
        context = types.SimpleNamespace(
            window_manager=types.SimpleNamespace(bfacw_chat_props=props))
        op = types.SimpleNamespace(
            report=lambda level, message: reported.append(message))

        attach(context, op, img)

        self.assertIs(props.chat_image, img, "the socket must hold the image")
        self.assertEqual(warmed, [img],
                         "the thumbnail preview must be warmed on attach")
        self.assertTrue(reported and "shot.png" in reported[0],
                        "the attach must be reported to the user")

    def test_attachment_row_sits_above_the_chat_input(self):
        src = _ui_source()
        panels = (
            ("class BFACW_PT_chat_panel(", "\nclass BFACW_PT_chat_session("),
            ("class BFACW_PT_chat_text_editor(", "\n# ---"),
        )
        for start_marker, end_marker in panels:
            start = src.index(start_marker)
            end = src.find(end_marker, start + 1)
            body = src[start:] if end < 0 else src[start:end]
            row_at = body.index("_draw_attachment_row(layout, props)")
            input_at = body.index('layout.textbox(props, "chat_input")')
            self.assertLess(
                row_at, input_at,
                f"{start_marker} must draw the image row ABOVE the chat input")

    def test_send_paths_pass_encoded_attachments(self):
        src = _ui_source()
        # Both send operators capture on the main thread...
        self.assertEqual(
            src.count("= _capture_chat_attachment(props)"), 2,
            "chat_send and chat_queue_send must both capture the socket")
        # ...and hand the payload to the queue (x2) and the direct turn.
        self.assertEqual(src.count("attachments=attachments,"), 3)
        self.assertEqual(src.count("attachment_names=attachment_names,"), 3)
        # The queued consumer forwards the item's payload to the turn.
        self.assertEqual(src.count('attachments=item.get("attachments")'), 1)
        self.assertEqual(
            src.count('attachment_names=item.get("attachment_names")'), 1)

    def test_panels_draw_the_attachment_row(self):
        src = _ui_source()
        # Definition + the two panel call sites.
        self.assertEqual(
            src.count("_draw_attachment_row(layout, props)"), 3,
            "both chat panels must draw the attachment row")
