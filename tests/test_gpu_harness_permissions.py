# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Permissions in External Harness mode (#385).

The other modes carry the Permissions setting (Strict / Permissive / Auto) on the input
box, by Send.  External Harness has no input box (its card replaces the chat), so the
setting was only in Preferences.  Now:

1. The harness card has a "Permissions  [icon Permissive]" row: the setting at a glance;
   its button sends the input box's action (``approval_menu``: the same dropdown) and its
   tooltip names the setting.  A narrow card (a sidebar) has the word on its own line above
   a button as wide as Copy Log / Configure, apart from them.
2. The sidebar's top bar has it as a square icon button (only when the host asks: harness).
3. The editor's header has the same dropdown where the chat mode sits in the other modes;
   the other modes' header is unchanged.
4. ``chat_editor`` fills all three from the setting (``_approval_shown``): the harness
   card and the harness top bar carry it, the other modes' top bar does not.
5. The dropdown says what the setting means for a client's calls in External Harness.

Bpy-free: the surface with a fixed-width stand-in font, chat_editor's functions from its
source with stand-ins.

Run with::

    python -m unittest tests.test_gpu_harness_permissions -v
"""

__all__ = ()

import importlib
import sys
import types
import unittest
from pathlib import Path

_ADDON = Path(__file__).resolve().parents[1] / "addon" / "bfa_coworker"
_PKG = "bfacw_harness_perm_pkg"


def _load_ui():
    if _PKG not in sys.modules:
        pkg = types.ModuleType(_PKG)
        pkg.__path__ = [str(_ADDON)]
        sys.modules[_PKG] = pkg
    return [importlib.import_module(_PKG + "." + m) for m in ("ui_gpu.surface", "ui_gpu.layout", "ui_gpu.style",
                                                             "shared")]


sf, lay, st, shared = _load_ui()
_BUTTONS = (("copy_log", "Copy Log", "COPYDOWN"), ("configure", "Configure", "PREFERENCES"))


def _notice(approval=("Permissive", "UNLOCKED")):
    return sf.Notice("External Harness", "WORLD", ("The chat runs in your client.",), _BUTTONS,
                     rows_title="What your client did in Blender", approval=approval)


def _draw(surface, width=500, **kw):
    canvas = lay.Canvas(st.Style(), lay.fixed_measure(0.55), surface.interaction())
    actions = surface.draw(canvas, lay.Rect(0, 0, width, 600), [], **kw)
    return canvas, actions


def _rect(canvas, widget):
    return next(r for r, w in canvas.hit_areas if w == widget)


def _click(surface, canvas, widget, width=500, **kw):
    rect = _rect(canvas, widget)
    x, y = rect.x + rect.w / 2, rect.y + rect.h / 2
    surface.on_press(x, y)
    surface.on_release(x, y)
    return _draw(surface, width, **kw)[1]


def _overlap(a, b):
    return a.x < b.right and b.x < a.right and a.y < b.top and b.y < a.top


class TestHarnessCard(unittest.TestCase):

    def test_card_shows_the_setting_and_opens_the_dropdown(self):
        surface = sf.ChatSurface()
        canvas, _ = _draw(surface, notice=_notice())
        ids = [w for _r, w in canvas.hit_areas]
        self.assertIn("notice:approval_menu", ids)
        self.assertNotIn("input", ids)                       # still no input box
        texts = [c.text for c in canvas.commands if c.kind == "text"]
        self.assertIn("Permissions", texts)
        self.assertIn("Permissive", texts)                   # the current one, at a glance
        icons = [c.text for c in canvas.commands if c.kind == "icon"]
        self.assertIn("UNLOCKED", icons)
        self.assertEqual(_click(surface, canvas, "notice:approval_menu", notice=_notice()), [("approval_menu", "")])

    def test_inside_the_card_apart_from_its_buttons(self):
        for width in (500, 260, 180):
            with self.subTest(width=width):
                surface = sf.ChatSurface(compact=width < 400)
                canvas, _ = _draw(surface, width, notice=_notice())
                perm = _rect(canvas, "notice:approval_menu")
                for widget in ("notice:copy_log", "notice:configure"):
                    self.assertFalse(_overlap(perm, _rect(canvas, widget)), (width, widget))
                    self.assertGreater(perm.y, _rect(canvas, widget).y)      # above Copy Log / Configure
                self.assertLessEqual(perm.right, width)
                self.assertIn("Permissive", [c.text for c in canvas.commands if c.kind == "text"])

    def test_narrow_card_puts_the_word_above_the_button(self):
        # A sidebar: no room for the word beside the button; it gets its own line above it.
        wide = _draw(sf.ChatSurface(compact=True), 260, notice=_notice())[0]
        canvas, _ = _draw(sf.ChatSurface(compact=True), 180, notice=_notice())
        word = next(c for c in canvas.commands if c.kind == "text" and c.text == "Permissions")
        self.assertIn("Permissive", [c.text for c in canvas.commands if c.kind == "text"])
        perm = _rect(canvas, "notice:approval_menu")
        self.assertAlmostEqual(perm.w, _rect(canvas, "notice:copy_log").w, delta=1.0)   # full width, like them
        self.assertGreater(word.rect.y, perm.top - 2)       # its baseline above the button
        wide_word = next(c for c in wide.commands if c.kind == "text" and c.text == "Permissions")
        wide_perm = _rect(wide, "notice:approval_menu")
        self.assertLess(wide_word.rect.x, wide_perm.x)       # wide: beside it, on its left
        self.assertLess(wide_word.rect.y, wide_perm.top)

    def test_very_narrow_card_still_shows_the_button(self):
        canvas, _ = _draw(sf.ChatSurface(compact=True), 120, notice=_notice())
        perm = _rect(canvas, "notice:approval_menu")
        self.assertLessEqual(perm.right, 120)
        self.assertTrue(any(c.text.startswith("Permi") for c in canvas.commands if c.kind == "text"))

    def test_every_setting_shows_its_own_name_and_icon(self):
        for ident, name, _desc, icon, _n in shared.APPROVAL_MODE_ITEMS:
            with self.subTest(ident=ident):
                canvas, _ = _draw(sf.ChatSurface(), notice=_notice((name, icon)))
                self.assertIn(name, [c.text for c in canvas.commands if c.kind == "text"])
                self.assertIn(icon, [c.text for c in canvas.commands if c.kind == "icon"])

    def test_no_row_without_a_setting(self):
        canvas, _ = _draw(sf.ChatSurface(), notice=_notice(("", "")))
        self.assertNotIn("notice:approval_menu", [w for _r, w in canvas.hit_areas])
        self.assertNotIn("Permissions", [c.text for c in canvas.commands if c.kind == "text"])

    def test_the_card_grows_by_the_row(self):
        # The card hangs from the top of the chat: one row taller, its buttons sit one row lower.
        with_row = _draw(sf.ChatSurface(), notice=_notice())[0]
        without = _draw(sf.ChatSurface(), notice=_notice(("", "")))[0]
        u = st.Style().unit
        lower = _rect(without, "notice:copy_log").y - _rect(with_row, "notice:copy_log").y
        self.assertAlmostEqual(lower, u * 1.25, delta=1.0)
        self.assertLess(_rect(with_row, "notice:approval_menu").top, 600)

    def test_its_tooltip_names_the_setting(self):
        surface = sf.ChatSurface()
        surface.box.approval = ("Auto", "AUTO")              # chat_editor sets it in every mode
        self.assertTrue(sf.tip_for("notice:approval_menu"))
        now = [100.0]
        surface.clock = lambda: now[0]
        canvas, _ = _draw(surface, notice=_notice(("Auto", "AUTO")))
        rect = _rect(canvas, "notice:approval_menu")
        surface.on_move(rect.x + 3, rect.y + 3)
        _draw(surface, notice=_notice(("Auto", "AUTO")))       # the mouse starts resting here
        now[0] += 1.0
        canvas, _ = _draw(surface, notice=_notice(("Auto", "AUTO")))
        texts = [c.text for c in canvas.commands if c.kind == "text"]
        self.assertTrue(any("Permissions: Auto" in t for t in texts), texts)


class TestTopBar(unittest.TestCase):

    def test_harness_top_bar_has_the_square_button(self):
        surface = sf.ChatSurface(compact=True)
        top = sf.TopBar(mode_label="External MCP", status="Bridge Running", running=True, start_label="Start Bridge",
                        approval=("Strict", "LOCKED"))
        canvas, _ = _draw(surface, 300, top=top, notice=_notice(("Strict", "LOCKED")))
        bar = [r for r, w in canvas.hit_areas if w == "approval_menu"]
        self.assertEqual(len(bar), 1)                        # the card's row has its own id: one click, one dropdown
        self.assertIn("notice:approval_menu", [w for _r, w in canvas.hit_areas])
        self.assertAlmostEqual(bar[0].y, _rect(canvas, "open_editor").y)   # on the top bar's row
        self.assertAlmostEqual(bar[0].w, bar[0].h, delta=1.0)  # square (#238)
        self.assertLess(bar[0].right, _rect(canvas, "open_editor").x + 1)
        self.assertGreater(bar[0].x, _rect(canvas, "stop_agent").right - 1)
        x, y = bar[0].x + bar[0].w / 2, bar[0].y + bar[0].h / 2
        surface.on_press(x, y)
        surface.on_release(x, y)
        self.assertEqual(_draw(surface, 300, top=top, notice=_notice(("Strict", "LOCKED")))[1],
                         [("approval_menu", "")])

    def test_other_modes_top_bar_has_none(self):
        surface = sf.ChatSurface(compact=True)
        surface.box.approval = ("Strict", "LOCKED")          # by Send, as in every other mode
        canvas, _ = _draw(surface, 300, top=sf.TopBar(status="Ready"))
        rects = [r for r, w in canvas.hit_areas if w == "approval_menu"]
        self.assertEqual(len(rects), 1)
        self.assertLessEqual(rects[0].top, surface.box.rect.top)        # the input box's own


# -- chat_editor: the header and what fills the card ------------------------------------

def _editor_slice(start: str, end: str) -> str:
    src = (_ADDON / "chat_editor.py").read_text(encoding="utf-8")
    first = src.index(start)
    return src[first:src.index(end, first)]


class _Prefs(types.SimpleNamespace):
    pass


def _editor_ns():
    """chat_editor's _approval_shown, _mode_icon, _status and _harness_notice, with stand-ins."""
    activity = types.SimpleNamespace(entries=lambda n: [])
    sys.modules[_PKG + ".activity"] = activity
    bridge = types.SimpleNamespace(is_running=lambda: True, get_actual_port=lambda: 9876)
    ui_chat = types.SimpleNamespace(_ui_state=lambda _ctx: None)
    sys.modules[_PKG + ".ui_chat"] = ui_chat
    import time
    ns = {"Any": object, "__name__": _PKG + ".chat_editor", "__package__": _PKG, "time": time,
          "mcp_to_blender_server": bridge, "HARNESS_PRESET_ITEMS": shared.HARNESS_PRESET_ITEMS,
          "CHAT_MODE_ITEMS": shared.CHAT_MODE_ITEMS, "Notice": sf.Notice, "TopBar": sf.TopBar,
          "agent_controller": types.SimpleNamespace(_agent_state=types.SimpleNamespace(mcp_server_running=False)),
          "_props": lambda: types.SimpleNamespace(chat_mode="AGENT", chat_status="Ready"),
          "_helpers_waiting": lambda _ctx, _state: ""}
    code = (_editor_slice("def _is_harness", "# -- The editor type")
            + _editor_slice("def _status(", "def _helpers_waiting(")
            + _editor_slice("def _approval_shown(", "def _history("))
    exec(compile("from __future__ import annotations\n" + code, "chat_editor.py", "exec"), ns)  # noqa: S102
    return ns


class TestEditorFills(unittest.TestCase):

    def tearDown(self):
        for name in (".activity", ".ui_chat"):
            sys.modules.pop(_PKG + name, None)

    def test_harness_card_and_top_bar_carry_the_setting(self):
        ns = _editor_ns()
        for ident, name, _desc, icon, _n in shared.APPROVAL_MODE_ITEMS:
            prefs = _Prefs(operating_mode="EXTERNAL_HARNESS", approval_mode=ident, harness_preset="CLAUDE_DESKTOP")
            ns["_prefs"] = lambda _ctx=None, _p=prefs: _p
            self.assertEqual(ns["_approval_shown"](prefs), (name, icon))
            self.assertEqual(ns["_harness_notice"](prefs).approval, (name, icon))
            self.assertEqual(ns["_status"](None).approval, (name, icon))

    def test_other_modes_top_bar_leaves_it_to_the_input_box(self):
        ns = _editor_ns()
        prefs = _Prefs(operating_mode="REMOTE_API", approval_mode="RISKY")
        ns["_prefs"] = lambda _ctx=None: prefs
        state = types.SimpleNamespace(is_thinking=False, error="")
        sys.modules[_PKG + ".ui_chat"]._ui_state = lambda _ctx: state
        ns["agent_controller"]._agent_state = state
        state.mcp_server_running = True
        self.assertEqual(ns["_status"](None).approval, ("", ""))

    def test_no_prefs_reads_strict(self):
        ns = _editor_ns()
        self.assertEqual(ns["_approval_shown"](None), ("Strict", "LOCKED"))


class TestHeader(unittest.TestCase):

    def setUp(self):
        import test_editor_header as teh        # its recording layout and loader
        self.teh = teh

    def tearDown(self):
        sys.modules.pop(self.teh._PKG, None)
        sys.modules.pop(self.teh._PKG + ".ui_chat", None)

    def header(self, width=1600, **kw):
        ns, _ = self.teh._load(self.teh._bar(**kw))
        log = []
        ns["_draw_header"](types.SimpleNamespace(layout=self.teh._Layout(log)), self.teh._context(width))
        return log

    def test_harness_header_has_the_dropdown_before_the_mode(self):
        log = self.header(chat_modes=(), mode_label="External MCP", start_label="Start Bridge",
                          status="Bridge Running", approval=("Permissive", "UNLOCKED"))
        pops = [e for e in log if e[0] == "popover"]
        self.assertEqual(pops[0], ("popover", "BFACW_PT_approval_popover", "Permissive", "UNLOCKED"))
        self.assertEqual(pops[1][1], "BFACW_PT_mode_popover")
        self.assertLess(log.index(("tabs", next(e[1] for e in log if e[0] == "tabs"))), log.index(pops[0]))

    def test_other_modes_header_unchanged(self):
        log = self.header()                                   # no approval on the bar: the input box has it
        self.assertNotIn("BFACW_PT_approval_popover", [e[1] for e in log if e[0] == "popover"])

    def test_narrow_harness_header_keeps_the_tab_strip(self):
        teh = self.teh
        ns, _ = teh._load(teh._bar(chat_modes=(), approval=("Permissive", "UNLOCKED")))
        log = []
        ns["_draw_header"](types.SimpleNamespace(layout=teh._Layout(log)), teh._context(700))
        tabs = next(e for e in log if e[0] == "tabs")
        self.assertGreaterEqual(tabs[1], ns["_TABS_MIN"])
        self.assertIn("BFACW_PT_approval_popover", [e[1] for e in log if e[0] == "popover"])


class TestDropdownText(unittest.TestCase):

    def test_the_dropdown_says_what_it_means_for_a_client(self):
        src = (_ADDON / "ui_chat.py").read_text(encoding="utf-8")
        body = src[src.index("class BFACW_PT_approval_popover"):src.index("def _picker_for")]
        self.assertIn('operating_mode == "EXTERNAL_HARNESS"', body)
        self.assertIn("Auto never asks", body)


if __name__ == "__main__":
    unittest.main()
