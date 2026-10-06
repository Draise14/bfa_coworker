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
import re
import sys
import tempfile
import types
import unittest
from unittest import mock

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


def _ui_constants(*names):
    """Read numeric module constants straight out of ui_chat.py."""
    src = _ui_source()
    out = {}
    for name in names:
        match = re.search(r"^{:s} = ([0-9.]+)$".format(re.escape(name)), src, re.M)
        if match is None:
            raise AssertionError("{:s} not found in ui_chat.py".format(name))
        out[name] = float(match.group(1))
    return out


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
        # The drop operator accepts the whole file list Blender hands it.
        self.assertIn("directory: bpy.props.StringProperty", src)
        self.assertIn("files: bpy.props.CollectionProperty", src)

    def test_drop_handler_is_scoped_to_the_chat_panels(self):
        src = _ui_source()
        fh_start = src.index("class BFACW_FH_chat_drop(")
        fh_end = src.index("\nclass ", fh_start + 1)
        body = src[fh_start:fh_end]
        self.assertIn('if area.type == "TEXT_EDITOR":', body,
                      "the Text Editor panel must accept the drop")
        self.assertIn("return _is_coworker_panel_region(context)", body,
                      "the 3D Viewport drop must be scoped to our panel")
        self.assertIn('bl_label = "Attach Image to Coworker Chat"', body)

    def test_is_coworker_panel_region_scopes_the_drop(self):
        is_region = _extract_ui_func(
            "_is_coworker_panel_region", {"_CHAT_PANEL_CATEGORY": "Coworker"})

        def _ctx(area_type, region_type, category):
            area = None if area_type is None else types.SimpleNamespace(
                type=area_type)
            region = None if region_type is None else types.SimpleNamespace(
                type=region_type, active_panel_category=category)
            return types.SimpleNamespace(area=area, region=region)

        self.assertTrue(is_region(_ctx("VIEW_3D", "UI", "Coworker")))
        self.assertFalse(is_region(_ctx("VIEW_3D", "UI", "Item")),
                         "another sidebar tab must keep Blender's behaviour")
        self.assertFalse(is_region(_ctx("VIEW_3D", "WINDOW", "Coworker")),
                         "the viewport itself must not be claimed")
        self.assertFalse(is_region(_ctx("VIEW_3D", "UI", "")))
        self.assertFalse(is_region(_ctx("IMAGE_EDITOR", "UI", "Coworker")))
        self.assertFalse(is_region(_ctx(None, None, "")))

    def test_the_addon_never_patches_blenders_own_drop_handlers(self):
        # We used to wrap poll_drop on Blender's own image FileHandlers
        # (VIEW3D_FH_empty_image / VIEW3D_FH_camera_background_image) so
        # they would yield inside our panel.  That replaced a method on
        # classes Blender registers from a *startup* module, and on
        # Bforartists 5.3 a drag segfaulted inside bpy_class_call
        # (EXCEPTION_ACCESS_VIOLATION via file_handler_poll_drop).  The
        # add-on must not touch those classes at all.
        src = _ui_source()
        for banned in ("_suppress_builtin_viewport_drops",
                       "_restore_builtin_viewport_drops",
                       "_builtin_drop_patches",
                       "bl_operators"):
            self.assertNotIn(banned, src,
                             "the add-on must not patch Blender's handlers")

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
                lambda layout, img, context: preview_calls.append((img, context))})
        log = []
        props = types.SimpleNamespace(chat_image=object(),
                                      chat_image_send_once=True)
        context = object()
        draw(_FakeLayout(log), props, context)

        tpl = [c for c in log if c[0] == "template_ID"]
        self.assertEqual(len(tpl), 1, "the socket must be drawn once")
        self.assertEqual(tpl[0][1], (props, "chat_image"))
        self.assertEqual(tpl[0][2],
                         {"open": "image.open", "new": "image.new"})
        ops = [c[1][0] for c in log if c[0] == "operator"]
        # The redundant File button is gone; the socket's own "open"
        # (image.open) is the file-browser path now.
        self.assertNotIn("bfacw.chat_image_attach", ops)
        for op_id in ("bfacw.chat_capture_render",
                      "bfacw.chat_capture_screen"):
            self.assertIn(op_id, ops)
        toggles = [c for c in log if c[0] == "prop"
                   and c[1][1] == "chat_image_send_once"]
        self.assertEqual(len(toggles), 1, "send-once toggle must be drawn")
        # The row hands the socketed datablock (and the context) to the
        # preview helper so it can size itself to the panel width.
        self.assertEqual(preview_calls, [(props.chat_image, context)],
                         "the attached image must be previewed")

    def test_the_image_row_collapses_from_its_header(self):
        calls = []
        draw = _extract_ui_func(
            "_draw_attachment_row",
            {"_draw_attachment_preview":
                lambda layout, img, context: calls.append(img)})

        # Collapsed: the header toggle is drawn with the closed sub-panel
        # arrow, the attached name stays readable, and nothing else is
        # drawn -- so the panel gives the chat its room back.
        img = types.SimpleNamespace(name="shot.png")
        props = types.SimpleNamespace(chat_image=img,
                                      chat_image_send_once=True,
                                      chat_image_expanded=False)
        log = []
        draw(_FakeLayout(log), props, None)
        toggles = [c for c in log if c[0] == "prop"]
        self.assertEqual(len(toggles), 1,
                         "collapsed draws only the collapse toggle")
        self.assertEqual(toggles[0][1][1], "chat_image_expanded")
        self.assertEqual(toggles[0][2]["icon"], 'TRIA_RIGHT',
                         "collapsed uses the closed sub-panel arrow")
        self.assertIn("shot.png", [c[2].get("text") for c in log if c[0] == "label"],
                      "what is attached stays visible while collapsed")
        self.assertEqual([c for c in log if c[0] == "template_ID"], [])
        self.assertEqual([c for c in log if c[0] == "operator"], [])
        self.assertEqual(calls, [], "no thumbnail while collapsed")

        # Expanded: the open arrow and the whole row come back.
        props.chat_image_expanded = True
        log = []
        draw(_FakeLayout(log), props, None)
        opened = [c for c in log if c[0] == "prop"
                  and c[1][1] == "chat_image_expanded"]
        self.assertEqual(opened[0][2]["icon"], 'DOWNARROW_HLT',
                         "expanded uses the open sub-panel arrow")
        self.assertEqual(len([c for c in log if c[0] == "template_ID"]), 1)
        self.assertEqual(len([c for c in log if c[0] == "operator"]), 2)
        self.assertEqual(calls, [img])

        # Nothing attached: the header shows no name to fall back to.
        props.chat_image = None
        log = []
        draw(_FakeLayout(log), props, None)
        self.assertEqual([c for c in log if c[0] == "label"], [])

        # A stub without the property (older callers, tests) stays expanded.
        plain = types.SimpleNamespace(chat_image=None,
                                      chat_image_send_once=False)
        log = []
        draw(_FakeLayout(log), plain, None)
        self.assertEqual(len([c for c in log if c[0] == "template_ID"]), 1,
                         "missing property must not collapse the row")

    def test_attachment_preview_draws_thumbnail_and_degrades_gracefully(self):
        calls = []
        icons = {"value": (4242, 176, 44)}
        draw = _extract_ui_func(
            "_draw_attachment_preview",
            {"_attachment_preview_icon":
                lambda img, context: (calls.append((img, context)),
                                      icons["value"])[1],
             "_UI_UNIT_BASE_PX": 20.0})

        def _ctx(ui_scale=1.0):
            return types.SimpleNamespace(
                preferences=types.SimpleNamespace(
                    system=types.SimpleNamespace(ui_scale=ui_scale)))

        context = _ctx()

        # Nothing attached -> nothing drawn.
        log = []
        draw(_FakeLayout(log), None, context)
        self.assertEqual(log, [])
        self.assertEqual(calls, [], "no image means no preview work")

        img = types.SimpleNamespace()
        for icon_value, width, height, why in (
                (0, 176, 44, "no icon means no preview to show"),
                (4242, 0, 0, "no size means no preview to show")):
            icons["value"] = (icon_value, width, height)
            log = []
            draw(_FakeLayout(log), img, context)
            self.assertEqual(log, [], why)

        # The happy path: one icon button, sized by scale so it spans
        # the panel width measured by _attachment_preview_px.
        icons["value"] = (4242, 176, 44)
        log = []
        draw(_FakeLayout(log), img, context)
        drawn = [c for c in log if c[0] == "template_icon"]
        self.assertEqual(len(drawn), 1, "the thumbnail must be drawn once")
        self.assertEqual(drawn[0][2]["icon_value"], 4242)
        self.assertAlmostEqual(drawn[0][2]["scale"], 8.8, delta=0.01)
        self.assertEqual(len(calls), 3,
                         "the preview must be sized from the context")
        self.assertTrue(all(call[0] is img and call[1] is context
                            for call in calls))

        # ui_scale is honoured, and a context without preferences still
        # draws (never raising: this runs from a panel draw).
        log = []
        draw(_FakeLayout(log), img, _ctx(2.0))
        self.assertAlmostEqual(
            next(c for c in log if c[0] == "template_icon")[2]["scale"], 4.4,
            delta=0.01)
        log = []
        draw(_FakeLayout(log), img, None)
        self.assertEqual(len([c for c in log if c[0] == "template_icon"]), 1)

    def test_attachment_preview_px_is_a_panel_wide_square(self):
        px = _extract_ui_func("_attachment_preview_px", _ui_constants(
            "_ATTACHMENT_PREVIEW_MARGIN_PX", "_ATTACHMENT_PREVIEW_MIN_PX",
            "_ATTACHMENT_PREVIEW_MAX_PX", "_ATTACHMENT_PREVIEW_QUANTUM_PX"))

        def _ctx(width):
            return types.SimpleNamespace(
                region=types.SimpleNamespace(width=width))

        def _img(width, height):
            return types.SimpleNamespace(size=(width, height))

        # A SQUARE buffer, whatever the image's shape: Blender draws a
        # preview icon into a square rect (widget_draw_preview_icon ->
        # icon_draw_size), so a wide image could not span the panel
        # without empty bands unless those bands are composed in
        # (measured as a 176 x 176 buffer drawn as a 162 x 162 square in
        # a real session).
        self.assertEqual(px(_img(400, 100), _ctx(220)), (176, 176))
        self.assertEqual(px(_img(100, 400), _ctx(220)), (176, 176))
        self.assertEqual(px(_img(100, 100), _ctx(220)), (176, 176))

        # A wider panel grows the thumbnail, quantised to 16px so that
        # dragging the sidebar does not resample on every pixel of width.
        self.assertEqual(px(_img(400, 100), _ctx(400)), (352, 352))
        self.assertEqual(px(_img(400, 100), _ctx(305)), (272, 272))

        # Clamped to the documented range.
        self.assertEqual(px(_img(400, 100), _ctx(100000)), (512, 512))
        self.assertEqual(px(_img(400, 100), _ctx(0)), (64, 64))

        # Degenerate input never divides by zero or returns nonsense.
        self.assertEqual(px(_img(0, 0), _ctx(220)), (0, 0))
        self.assertEqual(px(None, _ctx(220)), (0, 0))
        self.assertEqual(px(_img(400, 100), None), (64, 64))

    def test_attachment_fit_and_cover_sizes_keep_the_aspect(self):
        fit = _extract_ui_func("_attachment_fit_size")
        cover = _extract_ui_func("_attachment_cover_size")

        # Landscape: the fit version is short (the sharp band) while the
        # cover version overflows sideways (the muted backdrop).
        self.assertEqual(fit(400, 100, 176), (176, 44))
        self.assertEqual(cover(400, 100, 176), (704, 176))
        # Portrait is the mirror image of that.
        self.assertEqual(fit(100, 400, 176), (44, 176))
        self.assertEqual(cover(100, 400, 176), (176, 704))
        # Square images fit and cover exactly.
        self.assertEqual(fit(100, 100, 176), (176, 176))
        self.assertEqual(cover(100, 100, 176), (176, 176))
        # Extreme shapes never collapse to zero pixels.
        self.assertEqual(fit(4000, 1, 176), (176, 1))
        self.assertEqual(cover(4000, 1, 176), (704000, 176))

    def test_attachment_source_floats_refuses_huge_images(self):
        source = _extract_ui_func(
            "_attachment_source_floats",
            {"_ATTACHMENT_THUMBNAIL_MAX_SOURCE_PX": 1000000,
             "array": __import__("array")})

        class _Img:
            def __init__(self, size, pixels=None):
                self.size = size
                self.pixels = pixels

        class _Pixels:
            def __init__(self, count):
                self.count = count
                self.read = None

            def foreach_get(self, buf):
                self.read = len(buf)

        # Over the cap: refused without reading a single pixel.
        pixels = _Pixels(4 * 2000 * 2000)
        self.assertIsNone(source(_Img((2000, 2000), pixels)))
        self.assertIsNone(pixels.read, "a huge image must not be read")

        # Within the cap the pixels come back as a byte-packed float array.
        pixels = _Pixels(16)
        buf, width, height = source(_Img((2, 2), pixels))
        self.assertEqual((width, height), (2, 2))
        self.assertEqual(len(buf), 16)
        self.assertEqual(pixels.read, 16)

        # No size, no pixels, or a failing read: None, never an exception.
        self.assertIsNone(source(_Img((0, 0))))
        self.assertIsNone(source(types.SimpleNamespace()))

        class _Broken:
            size = (2, 2)
            pixels = types.SimpleNamespace(
                foreach_get=lambda buf: (_ for _ in ()).throw(OSError()))

        self.assertIsNone(source(_Broken()))

    def test_attachment_thumbnail_pixels_composes_fit_over_cover(self):
        # An 8x8 buffer from a 4:1 source: fit (8, 2) centred on cover
        # (32, 8), so two rows are the sharp band and the rest is the
        # muted backdrop.
        side = 8
        cover = [float(i) for i in range(32 * 8 * 4)]
        front = [100.0 + i for i in range(8 * 2 * 4)]
        thumb = _extract_ui_func("_attachment_thumbnail_pixels", {
            "_attachment_fit_size": lambda w, h, s: (8, 2),
            "_attachment_cover_size": lambda w, h, s: (32, 8),
            "_attachment_scale_pixels":
                lambda img, w, h, **kw: cover if w == 32 else front,
            "_ATTACHMENT_THUMBNAIL_DIM": 0.4,
        })

        class _Img:
            size = (400, 100)

        out = thumb(_Img(), side)
        self.assertIsNotNone(out)
        self.assertEqual(len(out), side * side * 4)
        # Rows 3 and 4 are the sharp band, at x-offset 0 (full width).
        self.assertEqual(out[3 * 32:4 * 32], front[0:32])
        self.assertEqual(out[4 * 32:5 * 32], front[32:64])
        # A row outside the band is the backdrop, cropped to the middle
        # 8 columns of the 32-wide cover.
        expected = cover[12 * 4:12 * 4 + 32]
        self.assertEqual(out[0:32], expected)

        # A portrait source has the band in every row, inset sideways.
        portrait = _extract_ui_func("_attachment_thumbnail_pixels", {
            "_attachment_fit_size": lambda w, h, s: (2, 8),
            "_attachment_cover_size": lambda w, h, s: (8, 32),
            "_attachment_scale_pixels":
                lambda img, w, h, **kw: cover if w == 8 else front,
            "_ATTACHMENT_THUMBNAIL_DIM": 0.4,
        })
        out = portrait(_Img(), side)
        self.assertEqual(len(out), side * side * 4)
        self.assertEqual(out[3 * 4:5 * 4], front[0:8],
                         "the sharp band is inset and centred")

        # An image with no usable size composes nothing.
        self.assertIsNone(thumb(types.SimpleNamespace(size=(0, 0)), side))

    def test_attachment_preview_icon_composes_into_a_private_preview(self):
        class _Sink(list):
            """Stands in for the dynamic pixels array."""

        class _Preview:
            def __init__(self):
                self.image_size = (0, 0)
                self.icon_size = (0, 0)
                self.image_pixels_float = _Sink()
                self.icon_pixels_float = _Sink()
                self.icon_id = 777

        class _Collection:
            def __init__(self):
                self.created = []

            def new(self, name):
                preview = _Preview()
                self.created.append(preview)
                return preview

        class _Image:
            name = "a.png"
            size = (400, 100)

        state = {"pixels": [0.25] * (176 * 176 * 4),
                 "collection": _Collection()}
        icon = _extract_ui_func("_attachment_preview_icon", {
            "_attachment_preview_px": lambda img, context: (176, 176),
            "_attachment_thumbnail_pixels": lambda img, side: state["pixels"],
            "_attachment_datablock_icon": lambda img: 999,
            "_thumbnail_collection": lambda: state["collection"],
            "_ATTACHMENT_THUMB": {"collection": None, "preview": None,
                                  "key": None, "icon": 0},
        })

        img = _Image()
        self.assertEqual(icon(img, None), (777, 176, 176))
        preview = state["collection"].created[0]
        # Both buffers get the composite: template_icon asks for
        # ICON_SIZE_ICON and only falls back to the big preview buffer
        # when the small one is missing, so filling both keeps it sharp.
        self.assertEqual(preview.image_size, (176, 176))
        self.assertEqual(preview.icon_size, (176, 176))
        self.assertEqual(len(preview.image_pixels_float), 176 * 176 * 4)
        self.assertEqual(preview.icon_pixels_float,
                         preview.image_pixels_float)

        # Cached for the same image and size: one preview, no recompose.
        self.assertEqual(icon(img, None), (777, 176, 176))
        self.assertEqual(len(state["collection"].created), 1)

        # A datablock whose pixels cannot be read falls back to its own
        # (small but always valid) icon rather than drawing nothing.
        state["pixels"] = None
        other = _Image()
        other.name = "b.png"
        self.assertEqual(icon(other, None), (999, 176, 176))

    def test_the_preview_cache_is_released_on_unregister(self):
        src = _ui_source()
        unregister = src[src.index("def unregister("):]
        self.assertIn("free_attachment_thumbnail()", unregister,
                      "the cached thumbnail must be released on unregister")

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
            row_at = body.index("_draw_attachment_row(layout, props, context)")
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
            src.count("_draw_attachment_row(layout, props, context)"), 3,
            "both chat panels must draw the attachment row")

    def test_send_paths_warn_when_the_attachment_cannot_be_encoded(self):
        src = _ui_source()
        self.assertEqual(
            src.count("_warn_failed_attachment(self, props)"), 2,
            "both send operators must report a failed attachment encode")
        self.assertEqual(
            src.count("if attachments is None:"), 2,
            "the warning must be gated on the capture returning nothing")

    def test_warn_failed_attachment_only_fires_for_a_set_socket(self):
        reported = []
        warn = _extract_ui_func("_warn_failed_attachment")
        op = types.SimpleNamespace(
            report=lambda level, message: reported.append((level, message)))

        warn(op, types.SimpleNamespace(chat_image=None))
        self.assertEqual(reported, [],
                         "an empty socket must stay silent (plain-text send)")

        warn(op, types.SimpleNamespace(chat_image=object()))
        self.assertEqual(len(reported), 1)
        level, message = reported[0]
        self.assertEqual(level, {"WARNING"})
        self.assertIn("Could not prepare the attached image", message)


# ---------------------------------------------------------------------------
# Encoder internals -- fake imbuf, no Blender.

_CA_PATH = os.path.join(_REPO, "addon", "bfa_coworker", "chat_attachments.py")


def _load_chat_attachments():
    """Import chat_attachments.py with stdlib-only globals (no Blender)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "chat_attachments_encoder_under_test", _CA_PATH)
    ca = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ca)
    return ca


class _FakeImBuf:
    """Minimal ImBuf: ``size`` + ``file_type``, copy/resize/free."""

    def __init__(self, size=(2048, 1024), file_type="BMP"):
        self.size = size
        self.file_type = file_type
        self.filepath = "src.bmp"

    def copy(self):
        return _FakeImBuf(self.size, self.file_type)

    def resize(self, size, method="FAST"):
        self.size = size

    def free(self):
        pass


class _FakeImbufModule:
    """Records the ``file_type`` of every write, like Blender's imbuf."""

    def __init__(self, raise_on_write=False):
        self.raise_on_write = raise_on_write
        self.writes = []  # (file_type, size)

    def load(self, filepath):
        return _FakeImBuf()

    def write(self, buf, *, filepath=None):
        self.writes.append((buf.file_type, tuple(buf.size)))
        if self.raise_on_write:
            raise OSError("Unable to write image file (No error)")
        # Bigger for bigger buffers so the divisor walk really shrinks.
        with open(filepath, "wb") as fh:
            fh.write(b"x" * (buf.size[0] * buf.size[1] // 1000))


class TestAttachmentEncoder(unittest.TestCase):
    """Regression tests for the BMP-written-as-PNG bug (#88 follow-up)."""

    def setUp(self):
        self.ca = _load_chat_attachments()
        self._prev_imbuf = sys.modules.get("imbuf")
        self.addCleanup(self._restore_imbuf)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.src = os.path.join(self._tmp.name, "in.bmp")
        with open(self.src, "wb") as fh:
            fh.write(b"BM-original-bytes")

    def _restore_imbuf(self):
        if self._prev_imbuf is None:
            sys.modules.pop("imbuf", None)
        else:
            sys.modules["imbuf"] = self._prev_imbuf

    def _install(self, fake):
        sys.modules["imbuf"] = fake
        return fake

    def test_downscale_pins_the_buffer_to_png(self):
        """imbuf.write uses the buffer's own format, so it must be PNG."""
        fake = self._install(_FakeImbufModule())
        data, mime = self.ca._downscale_to_limit(
            self._tmp.name, self.src, 1_000_000)
        self.assertEqual(fake.writes, [("PNG", (2048, 1024))])
        self.assertEqual(mime, "image/png")
        self.assertTrue(data.startswith(b"x"))

    def test_resized_copies_are_png_too(self):
        fake = self._install(_FakeImbufModule())
        _, mime = self.ca._downscale_to_limit(self._tmp.name, self.src, 1000)
        self.assertEqual(mime, "image/png")
        self.assertGreaterEqual(len(fake.writes), 2)
        for file_type, _size in fake.writes:
            self.assertEqual(file_type, "PNG")

    def test_downscale_falls_back_to_raw_when_every_write_fails(self):
        self._install(_FakeImbufModule(raise_on_write=True))
        data, mime = self.ca._downscale_to_limit(
            self._tmp.name, self.src, 1_000_000)
        self.assertEqual(data, b"BM-original-bytes")
        self.assertEqual(mime, "image/bmp")

    def test_encode_file_is_total_when_the_write_fails(self):
        self._install(_FakeImbufModule(raise_on_write=True))
        data, mime = self.ca._encode_file(self.src, 1_000_000)
        self.assertEqual(data, b"BM-original-bytes")
        self.assertEqual(mime, "image/bmp")

    def test_image_to_data_uri_survives_a_failing_encoder(self):
        img = types.SimpleNamespace(filepath=self.src, is_dirty=False)
        with mock.patch.object(self.ca, "_encode_file",
                               side_effect=OSError("disk full")):
            self.assertIsNone(self.ca.image_to_data_uri(img))

    def test_image_to_data_uri_encodes_normally(self):
        self._install(_FakeImbufModule())
        img = types.SimpleNamespace(filepath=self.src, is_dirty=False)
        uri = self.ca.image_to_data_uri(img, limit=1_000_000)
        self.assertTrue(uri.startswith("data:image/png;base64,"))
# ---------------------------------------------------------------------------
# Render-from-view -- non-destructive to the user's scene.


class _FakeSceneCollectionObjects:
    def __init__(self, log):
        self._log = log

    def link(self, obj):
        self._log.append(("link", obj))


class _FakeSceneCollection:
    def __init__(self, log):
        self.objects = _FakeSceneCollectionObjects(log)


class _FakeScene:
    def __init__(self, log, camera=None):
        self.camera = camera
        self.collection = _FakeSceneCollection(log)
        self.render = types.SimpleNamespace(
            resolution_x=1920, resolution_y=1080, resolution_percentage=100)
        self.cycles = types.SimpleNamespace(samples=128, time_limit=0.0)


class _FakeTempOverride:
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TestRenderFromView(unittest.TestCase):
    """capture_render_from_view renders the view through a temp camera."""

    def setUp(self):
        self.ca = _load_chat_attachments()
        self._prev_bpy = sys.modules.get("bpy")
        self.addCleanup(self._restore_bpy)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _restore_bpy(self):
        if self._prev_bpy is None:
            sys.modules.pop("bpy", None)
        else:
            sys.modules["bpy"] = self._prev_bpy

    def _install_bpy(self, area_type="VIEW_3D", previous_camera=None,
                     fail_render=False):
        log = []

        render_result = types.SimpleNamespace(size=(4, 4))

        def _save_render(path):
            with open(path, "wb") as fh:
                fh.write(b"png")

        render_result.save_render = _save_render

        class _CamData:
            lens = 0.0

        class _CamObject:
            matrix_world = None

        class _Cameras:
            def new(self, name):
                return _CamData()

            def remove(self, data):
                log.append(("cameras.remove", data))

        class _Objects:
            def new(self, name, data):
                return _CamObject()

            def remove(self, obj, do_unlink=False):
                log.append(("objects.remove", obj, do_unlink))

        class _OpsView3D:
            def camera_to_view(self):
                log.append(("camera_to_view",))

        class _OpsRender:
            def render(self, *args, **kwargs):
                log.append(("render", args, kwargs))
                log.append(("at_render",
                            scene.render.resolution_x,
                            scene.render.resolution_y,
                            scene.render.resolution_percentage,
                            scene.cycles.samples,
                            scene.cycles.time_limit))
                if fail_render:
                    raise RuntimeError("render failed")

        class _Ops:
            view3d = _OpsView3D()
            render = _OpsRender()

        region = types.SimpleNamespace(type="WINDOW")
        space = types.SimpleNamespace(
            lens=35.0,
            region_3d=types.SimpleNamespace(
                            view_matrix=types.SimpleNamespace(inverted=lambda: None)))
        area = types.SimpleNamespace(
            type=area_type, width=800, height=600,
            regions=[region], spaces=[space])
        scene = _FakeScene(log, camera=previous_camera)

        class _Images:
            def get(self, name):
                return render_result

        class _Data:
            cameras = _Cameras()
            objects = _Objects()
            images = _Images()

        fake = types.ModuleType("bpy")
        fake.context = types.SimpleNamespace(
            window=types.SimpleNamespace(
                screen=types.SimpleNamespace(areas=[area])),
            area=None, scene=scene,
            temp_override=lambda **kw: _FakeTempOverride(**kw))
        fake.data = _Data()
        fake.ops = _Ops()
        fake.app = types.SimpleNamespace(tempdir=self._tmp.name, background=True)
        fake.utils = types.SimpleNamespace(
            user_resource=lambda kind: self._tmp.name)
        sys.modules["bpy"] = fake
        return log, scene, render_result

    def test_renders_through_a_temp_camera_and_restores_the_scene(self):
        sentinel = object()
        log, scene, _ = self._install_bpy(previous_camera=sentinel)

        path = self.ca.capture_render_from_view()

        self.assertTrue(path and os.path.isfile(path))
        self.assertIn(("camera_to_view",), log)
        self.assertIn(("render", (), {}), log)
        self.assertIs(scene.camera, sentinel,
                      "the user's camera must be restored")
        self.assertEqual([c[0] for c in log].count("objects.remove"), 1,
                         "the temporary camera must be removed")
        self.assertEqual([c[0] for c in log].count("cameras.remove"), 1)

    def test_restores_the_scene_even_when_the_render_fails(self):
        sentinel = object()
        log, scene, _ = self._install_bpy(previous_camera=sentinel,
                                          fail_render=True)

        self.assertIsNone(self.ca.capture_render_from_view())
        self.assertIs(scene.camera, sentinel)
        self.assertEqual([c[0] for c in log].count("objects.remove"), 1)
        self.assertEqual([c[0] for c in log].count("cameras.remove"), 1)

    def test_no_viewport_means_no_render(self):
        log, scene, _ = self._install_bpy(area_type="TEXT_EDITOR")
        self.assertIsNone(self.ca.capture_render_from_view())
        self.assertEqual(log, [], "nothing may be touched without a viewport")
        self.assertIsNone(scene.camera)

    def test_renders_although_the_render_result_reports_zero_size(self):
        # Blender 5.2 reports ``Render Result.size`` as (0, 0) even after
        # a render that did produce pixels, so the save must not be
        # gated on the datablock's reported size.
        _log, _, render_result = self._install_bpy()
        render_result.size = (0, 0)

        path = self.ca.capture_render_from_view()

        self.assertTrue(path and os.path.isfile(path),
                        "a zero reported size must not block the save")
        self.assertGreater(os.path.getsize(path), 0)

    def test_nothing_rendered_yet_is_a_clean_none(self):
        # Saving a Render Result with no image data raises RuntimeError;
        # that must yield None rather than a bogus attachment.
        _log, _, render_result = self._install_bpy()

        def _raise(path):
            raise RuntimeError(
                "Error: Image 'Render Result' does not have any image data")

        render_result.save_render = _raise
        self.assertIsNone(self.ca.capture_render())

    def test_an_empty_write_is_rejected_and_cleaned_up(self):
        _log, _, render_result = self._install_bpy()

        def _write_nothing(path):
            with open(path, "wb"):
                pass

        render_result.save_render = _write_nothing
        self.assertIsNone(self.ca.capture_render())
        left = [name for name in os.listdir(self.ca.attachments_dir())
                if name.startswith("render")]
        self.assertEqual(left, [], "an empty write must not be kept")

    def test_capture_renders_cheaply_and_restores_the_render_settings(self):
        log, scene, _ = self._install_bpy()
        before = (scene.render.resolution_x, scene.render.resolution_y,
                  scene.render.resolution_percentage, scene.cycles.samples,
                  scene.cycles.time_limit)

        self.ca.capture_render_from_view()

        at_render = [c for c in log if c[0] == "at_render"]
        self.assertEqual(len(at_render), 1, "the view must be rendered once")
        _, rx, ry, _pct, samples, time_limit = at_render[0]
        self.assertLessEqual(max(rx, ry), self.ca._RENDER_MAX_EDGE,
                             "the captured image must be downscaled")
        self.assertLessEqual(samples, self.ca._RENDER_MAX_SAMPLES,
                             "the capture must not run the full sample count")
        self.assertEqual(time_limit, self.ca._RENDER_TIME_LIMIT_S,
                         "a Cycles time limit must backstop the capture")

        after = (scene.render.resolution_x, scene.render.resolution_y,
                 scene.render.resolution_percentage, scene.cycles.samples,
                 scene.cycles.time_limit)
        self.assertEqual(after, before,
                         "the user's own render settings must be restored")
