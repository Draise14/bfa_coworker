# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
The Coworker editor's header (#467): editor type, Chat, View | the chat tabs in the
middle | chat mode, mode, status, Start / Stop, the display dropdown, Preferences.
The thumbnail size is no longer three loose buttons: it is in the dropdown
(``BFACW_PT_editor_display``, like the 3D Viewport's Overlays), Small / Medium / Large
with the current one pressed.  In a narrow editor the chat mode goes to icons and the
status is cut, so the tab strip keeps its smallest room (its x, + and menu) and
Start / Stop stays.

Runs ``chat_editor._draw_header`` and the panel's ``draw`` on a recording layout
(no Blender needed).

Run with::

    python -m unittest tests.test_editor_header -v
"""

__all__ = ()

import sys
import types
import unittest
from pathlib import Path

_ADDON = Path(__file__).resolve().parents[1] / "addon" / "bfa_coworker"
_PKG = "bfacw_editor_header_pkg"
_MODES = (("AGENT", "Coworker", "GHOST_ENABLED"), ("ASK", "Ask", "QUESTION"), ("PLAN", "Plan", "LINENUMBERS_ON"))


class _Layout:
    """Records every call, flattened, in drawing order."""

    def __init__(self, log):
        self.log = log

    def _add(self, *entry):
        self.log.append(entry)

    def template_header(self):
        self._add("template_header")

    def row(self, align=False):
        return self

    def menu(self, idname, text="", icon="NONE"):
        self._add("menu", idname)

    def separator(self):
        self._add("separator")

    def separator_spacer(self):
        self._add("spacer")

    def prop(self, _data, name, expand=False, icon_only=False):
        self._add("prop", name, icon_only)

    def popover(self, panel, text="", icon="NONE"):
        self._add("popover", panel, text, icon)

    def label(self, text="", icon="NONE"):
        self._add("label", text, icon)

    def operator(self, idname, text="", icon="NONE", depress=False):
        props = types.SimpleNamespace(size="")
        self._add("op", idname, text, icon, depress, props)
        return props


def _load(status, thumb="M", tabs=("All editors",)):
    src = (_ADDON / "chat_editor.py").read_text(encoding="utf-8")
    start = src.index("# The header's widths")
    code = src[start:src.index("# -- Toolshelf and sidebar", start)]
    drawn = []
    ui_chat = types.SimpleNamespace(
        _tab_label=lambda t: t,
        draw_chat_tabs=lambda layout, _ctx, units=0.0: layout.log.append(("tabs", units)))
    pkg = types.ModuleType(_PKG)
    pkg.__path__ = []
    pkg.ui_chat = ui_chat
    sys.modules[_PKG] = pkg
    sys.modules[_PKG + ".ui_chat"] = ui_chat
    bpy = types.SimpleNamespace(types=types.SimpleNamespace(Panel=object))
    ns = {"bpy": bpy, "Any": object, "__name__": _PKG + ".chat_editor", "__package__": _PKG,
          "_status": lambda _ctx: status, "_props": lambda: object(),
          "safe_icon": lambda name: name, "_is_coworker": lambda _ctx: True,
          "_area_view_setting": lambda _area, name, default: thumb if name == "thumb_size" else default,
          "agent_controller": types.SimpleNamespace(THREADS=types.SimpleNamespace(live=lambda: list(tabs)))}
    exec(compile("from __future__ import annotations\n" + code, "chat_editor.py", "exec"), ns)  # noqa: S102
    return ns, drawn


def _context(width, scale=1.0):
    return types.SimpleNamespace(region=types.SimpleNamespace(width=width), area=object(),
                                 preferences=types.SimpleNamespace(system=types.SimpleNamespace(ui_scale=scale)))


def _bar(**kw):
    base = dict(mode_label="Remote API", mode_icon="WORLD", status="Ready", status_icon="CHECKMARK",
                running=True, start_label="Start", error=False, chat_modes=_MODES, chat_mode="AGENT")
    base.update(kw)
    return types.SimpleNamespace(**base)


class _Base(unittest.TestCase):

    def tearDown(self):
        sys.modules.pop(_PKG, None)
        sys.modules.pop(_PKG + ".ui_chat", None)

    def header(self, width=1600, **kw):
        tabs = kw.pop("tabs", ("All editors",))
        ns, _ = _load(_bar(**kw), tabs=tabs)
        log = []
        ns["_draw_header"](types.SimpleNamespace(layout=_Layout(log)), _context(width))
        return ns, log


class TestHeaderOrder(_Base):

    def test_menus_then_tabs_in_the_middle_then_the_right_group(self):
        _ns, log = self.header()
        names = [e[0] if e[0] not in ("menu", "op", "popover", "prop") else e[1] for e in log]
        self.assertEqual(names, [
            "template_header", "BFACW_MT_editor_chat", "BFACW_MT_editor_view",
            "spacer", "tabs", "spacer",
            "chat_mode", "separator", "BFACW_PT_mode_popover", "label", "bfacw.agent_stop",
            "separator", "BFACW_PT_editor_display", "bfacw.open_addon_prefs"])

    def test_no_loose_thumbnail_buttons_in_the_header(self):
        _ns, log = self.header()
        self.assertNotIn("bfacw.editor_thumbs", [e[1] for e in log if e[0] == "op"])

    def test_the_display_dropdown_is_an_icon_popover(self):
        _ns, log = self.header()
        pop = next(e for e in log if e[0] == "popover" and e[1] == "BFACW_PT_editor_display")
        self.assertEqual(pop[2:], ("", "OVERLAY"))

    def test_external_harness_has_no_chat_mode_and_says_stop_bridge(self):
        _ns, log = self.header(chat_modes=(), mode_label="External MCP", start_label="Start Bridge",
                               status="Bridge Running")
        self.assertNotIn("prop", [e[0] for e in log])
        stop = next(e for e in log if e[0] == "op" and e[1] == "bfacw.agent_stop")
        self.assertEqual(stop[2], "Stop Bridge")

    def test_offline_shows_start_and_an_error_its_copy_button(self):
        _ns, log = self.header(running=False, error=True, status="Error: boom")
        ops = [e[1] for e in log if e[0] == "op"]
        self.assertIn("bfacw.agent_start", ops)
        label = next(i for i, e in enumerate(log) if e[0] == "label")
        self.assertEqual(log[label + 1][1], "bfacw.copy_status_error", "the copy button follows the status")

    def test_wide_header_shows_everything_whole(self):
        status = "Thinking ... reading the scene and the selection (12s)"
        _ns, log = self.header(width=2400, status=status)
        self.assertEqual(next(e for e in log if e[0] == "label")[1], status)
        self.assertFalse(next(e for e in log if e[0] == "prop")[2], "chat mode with its words")
        _ns, log = self.header(width=2400, status="x" * 80)
        self.assertEqual(next(e for e in log if e[0] == "label")[1], "x" * 59 + "…", "60 at most")


class TestNarrowHeader(_Base):

    def test_narrow_header_keeps_the_tab_tools_and_start(self):
        status = "Thinking ... reading the scene and the selected objects (12s)"
        ns, log = self.header(width=700, status=status, tabs=("All editors", "Node", "Shader", "Seq"))
        tabs = next(e for e in log if e[0] == "tabs")
        self.assertGreaterEqual(tabs[1], ns["_TABS_MIN"], "room for the tab strip's x, + and menu")
        self.assertTrue(next(e for e in log if e[0] == "prop")[2], "the chat mode goes to icons")
        label = next(e for e in log if e[0] == "label")[1]
        self.assertLess(len(label), len(status), "the status is cut to fit")
        self.assertIn("bfacw.agent_stop", [e[1] for e in log if e[0] == "op"])

    def test_fit_never_overflows_while_it_can(self):
        ns, _ = _load(_bar())
        fit = ns["_header_fit"]
        fixed, modes_full, modes_icon = 21.0, 10.5, 4.7
        for units in range(35, 140, 5):
            for status_len in (0, 10, 40, 60, 90):
                tabs, icon_only, chars = fit(float(units), fixed, modes_full, modes_icon, status_len, 9.0)
                modes = modes_icon if icon_only else modes_full
                status_w = ns["_ICON_W"] + (ns["_CHAR_W"] * chars + ns["_PAD_W"] if chars else 0.0)
                self.assertGreaterEqual(tabs, ns["_TABS_MIN"])
                self.assertLessEqual(chars, min(status_len, ns["_STATUS_MAX"]))
                if units >= fixed + ns["_TABS_MIN"] + modes_icon + ns["_ICON_W"]:
                    self.assertLessEqual(fixed + modes + status_w + tabs, units + 1e-6, (units, status_len))

    def test_tabs_keep_their_share_before_the_status_is_whole(self):
        ns, _ = _load(_bar())
        tabs, icon_only, chars = ns["_header_fit"](60.0, 21.0, 10.5, 4.7, 60, 30.0)
        self.assertGreaterEqual(tabs, 0.4 * 60.0 - 1e-6, "two fifths for many tabs")
        self.assertTrue(icon_only)
        self.assertLess(chars, 60)

    def test_cut(self):
        ns, _ = _load(_bar())
        cut = ns["_cut"]
        self.assertEqual(cut("Ready", 10), "Ready")
        self.assertEqual(cut("Connecting to the agent", 8), "Connect…")
        self.assertEqual(cut("Connecting", 0), "")


class TestDisplayPanel(_Base):

    def draw(self, thumb):
        ns, _ = _load(_bar(), thumb=thumb)
        panel_cls = ns["BFACW_PT_editor_display"]
        self.assertEqual((panel_cls.bl_space_type, panel_cls.bl_region_type), ("NODE_EDITOR", "HEADER"))
        self.assertTrue(panel_cls.poll(_context(800)))
        panel = panel_cls()
        log = []
        panel.layout = _Layout(log)
        panel.draw(_context(800))
        return log

    def test_thumbnail_size_small_medium_large_current_pressed(self):
        log = self.draw("L")
        self.assertEqual(log[0], ("label", "Thumbnail Size", "NONE"))
        ops = [e for e in log if e[0] == "op"]
        self.assertEqual([e[1] for e in ops], ["bfacw.editor_thumbs"] * 3)
        self.assertEqual([e[2] for e in ops], ["Small", "Medium", "Large"])
        self.assertEqual([e[5].size for e in ops], ["S", "M", "L"])
        self.assertEqual([e[4] for e in ops], [False, False, True])

    def test_follows_the_area_setting(self):
        self.assertEqual([e[4] for e in self.draw("S") if e[0] == "op"], [True, False, False])


if __name__ == "__main__":
    unittest.main()
