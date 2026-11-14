# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
The chat tab row (#427 follow-up): the tabs on the left, then a separator, then close,
+ and the tab menu in their own fixed-width group on the right, the same three buttons
whatever tabs are open.  The + is a chat for All editors; in an editor (not the Coworker
editor) a fourth button, with the editor's icon, is a chat using its context (#466).

Runs ``ui_chat.draw_chat_tabs`` on a fake layout (no Blender needed).
"""

__all__ = ()

import os
import sys
import types
import unittest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_UI_PATH = os.path.join(_REPO, "addon", "bfa_coworker", "ui_chat.py")
_PKG = "_tabrow_pkg"


def _extract(*names, extra=None):
    """The named top-level functions/assignments of ui_chat.py, run in one module."""
    with open(_UI_PATH, "r", encoding="utf-8") as fh:
        src = fh.read()
    mod = types.ModuleType(_PKG + ".ui_chat")
    mod.__dict__.update({"__package__": _PKG, "__file__": _UI_PATH, **(extra or {})})
    for name in names:
        for marker in ("\ndef {:s}(".format(name), "\n{:s} = ".format(name)):
            start = src.find(marker)
            if start >= 0:
                break
        else:
            raise ImportError(name)
        start += 1
        end = min(i for i in (src.find("\ndef ", start + 1), src.find("\nclass ", start + 1),
                              src.find("\n_", start + 1), len(src)) if i >= 0)
        exec(compile(src[start:end], _UI_PATH, "exec"), mod.__dict__)  # noqa: S102
    return mod


class _Layout:
    """Records every row, separator, operator and menu, with the row's settings."""

    def __init__(self, log, depth=0):
        self.log, self.depth = log, depth
        self.enabled, self.alert, self.ui_units_x = True, False, 0.0

    def row(self, align=False):
        child = _Layout(self.log, self.depth + 1)
        self.log.append(("row", self.depth, child))
        return child

    def separator(self):
        self.log.append(("separator", self.depth, self))

    def operator(self, idname, text="", icon="NONE", depress=False):
        props = types.SimpleNamespace(thread_id="", scope="ALL")
        self.log.append(("op", self.depth, self, idname, text, icon, depress, props))
        return props

    def menu(self, idname, text="", icon="NONE"):
        self.log.append(("menu", self.depth, self, idname, icon))


def _thread(tid, default=False):
    return types.SimpleNamespace(id=tid, is_default=default, editor="")


class TestTabRow(unittest.TestCase):

    def setUp(self):
        self.refused = set()
        threads = types.SimpleNamespace(close_refused=lambda _reg, t, _busy: "busy" if t.id in self.refused else "")
        pkg = types.ModuleType(_PKG)
        pkg.threads = threads
        sys.modules[_PKG] = pkg
        sys.modules[_PKG + ".threads"] = threads
        self.addCleanup(sys.modules.pop, _PKG, None)
        self.addCleanup(sys.modules.pop, _PKG + ".threads", None)
        self.a, self.b, self.c = _thread("a", True), _thread("b"), _thread("c")
        self.strip = (self.b, [self.a, self.b, self.c], ["All editors", "Node", "Seq"], ["icon", "full", "hidden"])
        ac = types.SimpleNamespace(THREADS=object(),
                                   thread_state=lambda _t: types.SimpleNamespace(turn_active=False))
        self.kind = ""          # the strip's editor kind ("" = the Coworker editor)
        self.ui = _extract("_TAB_TOOLS_W", "_tab_tools_w", "new_tab_tip", "draw_chat_tabs", extra={
            "agent_controller": ac,
            "tab_strip": lambda _ctx, _units: self.strip,
            "_tab_icon": lambda _t: ("NODETREE", False),
            "_tab_kind": lambda _ctx: self.kind,
            "_editor_icon": lambda kind: {"VIEW_3D": "VIEW3D"}.get(kind, "WINDOW"),
        })

    def draw(self):
        log = []
        self.ui.draw_chat_tabs(_Layout(log), None)
        return log

    def tools(self):
        return [e for e in self.draw() if (e[0] == "op" and e[3] != "bfacw.tab_show") or e[0] == "menu"]

    def test_tabs_left_then_separator_then_tools_right(self):
        log = self.draw()
        kinds = [e[0] for e in log]
        sep = kinds.index("separator")
        tabs = [e for e in log if e[0] == "op" and e[3] == "bfacw.tab_show"]
        tools = [e for e in log if (e[0] == "op" and e[3] != "bfacw.tab_show") or e[0] == "menu"]
        self.assertEqual([t[7].thread_id for t in tabs], ["a", "b"], "the hidden tab isn't drawn")
        self.assertTrue(all(log.index(t) < sep for t in tabs), "tabs come before the separator")
        self.assertEqual([t[3] for t in tools], ["bfacw.tab_close", "bfacw.tab_new", "BFACW_MT_chat_tabs"])
        self.assertTrue(all(log.index(t) > sep for t in tools), "tools come after the separator")
        self.assertNotIn("bfacw.tab_close", [t[3] for t in tabs])

    def test_tools_group_has_a_fixed_width(self):
        log = self.draw()
        new = next(e for e in log if e[0] == "op" and e[3] == "bfacw.tab_new")
        self.assertEqual(new[2].ui_units_x, self.ui._TAB_TOOLS_W)

    def test_close_acts_on_the_shown_tab(self):
        close = next(e for e in self.draw() if e[0] == "op" and e[3] == "bfacw.tab_close")
        self.assertEqual(close[7].thread_id, "b")
        self.assertTrue(close[2].enabled)

    def test_close_is_greyed_out_not_hidden_when_refused(self):
        self.refused.add("b")
        close = next(e for e in self.draw() if e[0] == "op" and e[3] == "bfacw.tab_close")
        self.assertFalse(close[2].enabled)

    def test_menu_shows_even_when_every_tab_fits(self):
        self.strip = (self.a, [self.a], ["All editors"], ["full"])
        self.assertIn("BFACW_MT_chat_tabs", [e[3] for e in self.draw() if e[0] == "menu"])

    def test_new_tab_tooltip_names_the_editors_context(self):
        self.assertEqual(self.ui.new_tab_tip("Shader Editor"),
                         "New chat in a new tab, using this Shader Editor's context")
        self.assertEqual(self.ui.new_tab_tip(""), "New chat in a new tab, for All editors")

    def test_plus_is_for_all_editors(self):
        plus = [t for t in self.tools() if t[0] == "op" and t[3] == "bfacw.tab_new"]
        self.assertEqual(len(plus), 1, "the Coworker editor: no editor's own button")
        self.assertEqual((plus[0][5], plus[0][7].scope), ("ADD", "ALL"))
        self.assertEqual(plus[0][2].ui_units_x, self.ui._TAB_TOOLS_W)

    def test_an_editor_strip_has_a_button_with_its_icon(self):
        self.kind = "VIEW_3D"
        tools = self.tools()
        self.assertEqual([t[3] for t in tools],
                         ["bfacw.tab_close", "bfacw.tab_new", "bfacw.tab_new", "BFACW_MT_chat_tabs"])
        here = tools[2]
        self.assertEqual((here[5], here[7].scope), ("VIEW3D", "EDITOR"))
        self.assertEqual(tools[1][7].scope, "ALL", "the + stays All editors")
        self.assertEqual(here[2].ui_units_x, self.ui._TAB_TOOLS_W + 1.0, "one unit wider for it")


if __name__ == "__main__":
    unittest.main()
