# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
The Classic chat is gone (#430): every sidebar shows the GPU chat.

1. ``docker_area`` / ``_compact_sidebar``: the Coworker tab of every editor with a sidebar
   (the 3D View and the Text Editor too, which offered the Classic chat) shows the compact
   GPU chat, whatever the preferences say -- a saved ``chat_sidebar_style = CLASSIC`` from
   1.1.43 or older is not read.  The Coworker editor itself and editors without a sidebar
   tab are not sidebar chats.
2. The Classic panels, the Sidebar Chat preference and its Max Visible Turns are gone; the
   compact chat panel is registered for every sidebar editor.
3. A settings backup (#86, #400) saved by an older version with ``chat_sidebar_style``
   restores without it, and without an error.

Runs the code sliced from ui_chat.py with stand-ins (no Blender needed)::

    python -m unittest tests.test_sidebar_chat_gpu_only -v
"""

__all__ = ()

import importlib.util
import sys
import types
import unittest
from pathlib import Path

_ADDON = Path(__file__).resolve().parents[1] / "addon" / "bfa_coworker"
_PKG = "bfacw_gpu_only_pkg"
_UI = (_ADDON / "ui_chat.py").read_text(encoding="utf-8")
_PREFS = (_ADDON / "preferences.py").read_text(encoding="utf-8")
_EDITOR = (_ADDON / "chat_editor.py").read_text(encoding="utf-8")


class _Space:
    pass


def _sidebar_funcs(coworker_spaces=()):
    """SIDEBAR_SPACES, docker_area and _compact_sidebar from ui_chat.py, with stand-ins."""
    src = _UI[_UI.index("\nSIDEBAR_SPACES = ("):_UI.index("\nclass BFACW_PT_chat_compact(")]
    pkg = types.ModuleType(_PKG)
    pkg.__path__ = []
    editor = types.ModuleType(_PKG + ".chat_editor")
    editor.is_coworker_space = lambda space: space in coworker_spaces
    pkg.chat_editor = editor
    sys.modules[_PKG] = pkg
    sys.modules[_PKG + ".chat_editor"] = editor
    ns = {"__name__": _PKG + ".ui_chat", "__package__": _PKG,
          "bpy": types.SimpleNamespace(types=types.SimpleNamespace(Context=object))}
    exec(compile(src, str(_ADDON / "ui_chat.py"), "exec"), ns)  # noqa: S102
    return ns


def _area(kind, space=None):
    return types.SimpleNamespace(type=kind, spaces=types.SimpleNamespace(active=space or _Space()))


class _NoAddons:
    """``context.preferences`` without our add-on's entry: the chat must not need it."""

    @property
    def addons(self):
        raise KeyError("bfa_coworker")


class TestEverySidebarIsTheGpuChat(unittest.TestCase):

    def test_every_sidebar_editor_shows_the_gpu_chat_whatever_the_saved_style(self):
        ns = _sidebar_funcs()
        kinds = ns["SIDEBAR_TYPES"]
        self.assertIn("VIEW_3D", kinds)
        self.assertIn("TEXT_EDITOR", kinds)     # the two that offered the Classic chat
        self.assertNotIn("_CLASSIC_TYPES", ns)
        for style in ("CLASSIC", "COMPACT", None):
            prefs = types.SimpleNamespace() if style is None else types.SimpleNamespace(chat_sidebar_style=style)
            for kind in kinds:
                with self.subTest(kind=kind, style=style):
                    self.assertTrue(ns["docker_area"](_area(kind), prefs))
                    self.assertTrue(ns["docker_area"](_area(kind)))

    def test_compact_sidebar_does_not_read_the_preferences(self):
        ns = _sidebar_funcs()
        for kind in ns["SIDEBAR_TYPES"]:
            context = types.SimpleNamespace(area=_area(kind), preferences=_NoAddons())
            self.assertTrue(ns["_compact_sidebar"](context), kind)

    def test_the_coworker_editor_and_other_editors_are_no_sidebar_chat(self):
        coworker = _Space()
        ns = _sidebar_funcs(coworker_spaces=(coworker,))
        self.assertFalse(ns["docker_area"](_area("NODE_EDITOR", coworker)))     # its chat fills the editor
        self.assertTrue(ns["docker_area"](_area("NODE_EDITOR")))                # a Shader Editor's sidebar
        self.assertFalse(ns["docker_area"](_area("PROPERTIES")))
        self.assertFalse(ns["docker_area"](None))
        self.assertFalse(ns["_compact_sidebar"](types.SimpleNamespace(area=None)))
        self.assertFalse(ns["_compact_sidebar"](types.SimpleNamespace()))       # no area at all


class TestTheClassicChatIsGone(unittest.TestCase):

    def test_no_classic_panels_operators_or_props(self):
        for gone in ("class BFACW_PT_chat_panel(", "class BFACW_PT_chat_text_editor(",
                     "class BFACW_OT_chat_queue_send(", "class BFACW_OT_copy_message(",
                     "class BFACW_OT_copy_code_block(", "def _draw_attachment_row(",
                     "def draw_approval_box(", "def _render_markdown(", "chat_newest_first",
                     "chat_image_expanded", "chat_max_visible_turns", "_CLASSIC_TYPES"):
            self.assertNotIn(gone, _UI, gone)

    def test_no_sidebar_chat_preference(self):
        for gone in ("chat_sidebar_style", "Classic Panels", "chat_max_visible_turns"):
            self.assertNotIn(gone, _PREFS, gone)

    def test_the_compact_chat_is_registered_for_every_sidebar_editor(self):
        compact = _UI[_UI.index("class BFACW_PT_chat_compact("):]
        compact = compact[:compact.index("\n\n\n")]
        self.assertIn("bl_space_type = 'VIEW_3D'", compact)
        self.assertIn("return not bpy.app.background and _compact_sidebar(context)", compact)
        copies = _EDITOR[_EDITOR.index("def _sidebar_copies("):_EDITOR.index("# -- Taking over the node editor")]
        self.assertIn("ui_chat.BFACW_PT_chat_compact", copies)
        self.assertIn("for space, _cls_name, short, _icon in ui_chat.SIDEBAR_SPACES:", copies)

    def test_the_controls_panel_keeps_project_rules(self):
        """The Classic Text Editor panel's Edit Rules: in the sidebar's Coworker panel now."""
        controls = _UI[_UI.index("class BFACW_PT_chat_controls("):_UI.index("\nclass BFACW_PT_chat_session(")]
        self.assertIn('"bfacw.edit_rules"', controls)


def _settings_transfer():
    spec = importlib.util.spec_from_file_location("bfacw_gpu_only_settings", _ADDON / "settings_transfer.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Prop:
    def __init__(self, identifier, kind, items=()):
        self.identifier, self.type = identifier, kind
        self.is_readonly, self.is_array, self.array_length = False, False, 0
        self.enum_items = [types.SimpleNamespace(identifier=i) for i in items]


class _Prefs:
    """Today's preferences: no chat_sidebar_style any more."""

    def __init__(self):
        self.operating_mode = "LOCAL_LLM"
        self.chat_input_lines_sidebar = 6
        self.bl_rna = types.SimpleNamespace(properties=[
            _Prop("operating_mode", "ENUM", ("LOCAL_LLM", "REMOTE_API")),
            _Prop("chat_input_lines_sidebar", "INT"),
        ])


class TestASavedClassicStyleIsHarmless(unittest.TestCase):

    def test_a_backup_with_the_classic_style_restores_without_it(self):
        st = _settings_transfer()
        prefs = _Prefs()
        saved = {"chat_sidebar_style": "CLASSIC", "chat_max_visible_turns": 12,
                 "operating_mode": "REMOTE_API", "chat_input_lines_sidebar": 9}
        applied = st.apply(prefs, saved)
        self.assertEqual(sorted(applied), ["chat_input_lines_sidebar", "operating_mode"])
        self.assertFalse(hasattr(prefs, "chat_sidebar_style"))
        self.assertEqual((prefs.operating_mode, prefs.chat_input_lines_sidebar), ("REMOTE_API", 9))
        self.assertNotIn("chat_sidebar_style", st.dump(prefs))


if __name__ == "__main__":
    unittest.main()
