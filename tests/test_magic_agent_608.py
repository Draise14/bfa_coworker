# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for #608: with Magic Wording on, Coworker's own agent is the Wizard on screen, with the
wizard icon; off gives today's labels exactly; nothing magic reaches the model.

Run with::

    python -m unittest tests.test_magic_agent_608 -v
"""

__all__ = ()

import importlib
import re
import sys
import types
import unittest
from pathlib import Path

_ADDON = Path(__file__).resolve().parents[1] / "addon" / "bfa_coworker"
_DATA = Path(__file__).resolve().parent / "data"
_PKG = "bfacw_magic608_pkg"


def _load(module):
    if _PKG not in sys.modules:
        pkg = types.ModuleType(_PKG)
        pkg.__path__ = [str(_ADDON)]
        sys.modules[_PKG] = pkg
    return importlib.import_module(_PKG + "." + module)


mw = _load("magic_wording")
shared = _load("shared")
cv = _load("ui_gpu.chat_view")
sf = _load("ui_gpu.surface")
gi = _load("ui_gpu.icons")
lay = _load("ui_gpu.layout")
st = _load("ui_gpu.style")
hp = _load("helpers")

_SRC = {name: (_ADDON / name).read_text(encoding="utf-8")
        for name in ("ui_chat.py", "chat_editor.py", "llm_transport.py", "engine_loop.py", "engine.py",
                     "engine_acp.py", "agent_controller.py", "tool_listing.py")
        if (_ADDON / name).exists()}

# Every changed label: (plain text today, text with Magic Wording on).
LABELS = (
    ("Start Coworker", "Start Wizard"),                                      # GPU tooltip "start"
    ("Stop Coworker", "Stop Wizard"),                                        # GPU tooltip "stop_agent"
    ("Restart Coworker", "Restart Wizard"),                                  # sidebar button
    ("Stop Coworker to switch modes", "Stop Wizard to switch modes"),        # sidebar hint
    ("Ask Coworker...", "Ask Wizard..."),                                    # input placeholder
    ("Start Coworker to chat (Start, above)", "Start Wizard to chat (Start, above)"),
    ("Start the Coworker agent: MCP bridge, MCP server, and LLM backend",
     "Start the Wizard: MCP bridge, MCP server, and LLM backend"),           # operator tooltips
    ("Stop the Coworker agent and all subprocesses", "Stop the Wizard and all subprocesses"),
    ("Stop all components and restart the Coworker agent", "Stop all components and restart the Wizard"),
    ("Let the agent run this tool this one time", "Let the Wizard run this tool this one time"),
    ("Let the agent run this tool without asking again, until you stop the agent",
     "Let the Wizard run this tool without asking again, until you stop the Wizard"),
    ("Don't run it: the agent is told no and carries on without it",
     "Don't run it: the Wizard is told no and carries on without it"),
    ("Coworker", "Wizard"),                                                  # the reply's speaker
)


class _Case(unittest.TestCase):
    def setUp(self):
        mw.set_enabled_for_tests(True)
        self.addCleanup(mw.set_enabled_for_tests, None)

    def draw(self, hist):
        canvas = lay.Canvas(st.Style(), lay.fixed_measure(0.55), lay.InteractionState())
        view = cv.ChatView()
        view.draw(canvas, lay.Rect(0, 0, 900, 5000), hist, live=None, compact=False)
        return [(cmd.text if cmd.kind == "text" else "icon:" + str(getattr(cmd, "text", "")))
                for cmd in canvas.commands], canvas


class TestAgentLabel(_Case):

    def test_each_label_on_and_off(self):
        for plain, magic in LABELS:
            mw.set_enabled_for_tests(True)
            self.assertEqual(mw.agent_label(plain), magic)
            mw.set_enabled_for_tests(False)
            self.assertEqual(mw.agent_label(plain), plain, "off gives today's label exactly")

    def test_one_fixed_word(self):
        self.assertEqual(mw.agent_word(), "Wizard")
        self.assertEqual({mw.agent_label("Start Coworker") for _ in range(50)}, {"Start Wizard"})
        mw.set_enabled_for_tests(False)
        self.assertEqual(mw.agent_word(), "Coworker")

    def test_names_a_user_searches_for_stay_plain(self):
        for text in ("External Agent", "External Harness", "Local", "Remote", "Agent Setting",
                     "Check Agent", "Open Agent Folder", "The agent stopped with an error: x"):
            self.assertNotIn("Wizard", mw.agent_label(text.replace("The agent", "An agent")), text)

    def test_the_gpu_tooltips(self):
        for widget, plain, magic in (
                ("start", "Start Coworker", "Start Wizard"),
                ("stop_agent", "Stop Coworker", "Stop Wizard"),
                ("jobs", None, None)):
            mw.set_enabled_for_tests(False)
            off = sf.tip_for(widget)
            if plain:
                self.assertEqual(off, plain)
            mw.set_enabled_for_tests(True)
            on = sf.tip_for(widget)
            if magic:
                self.assertEqual(on, magic)
            else:
                self.assertIn("Wizard", on)
                self.assertNotIn("Coworker", on)
        # names stay: the editor, the settings, the mode
        for widget in ("prefs", "open_editor", "mode:AGENT", "plan_card:start", "mode_menu"):
            mw.set_enabled_for_tests(True)
            self.assertEqual(sf.tip_for(widget), sf.TIPS[widget])

    def test_the_reply_is_signed_by_the_wizard(self):
        hist = [{"role": "user", "content": "go", "turn_start": True},
                {"role": "assistant", "content": "Done."}]
        on, _ = self.draw(hist)
        self.assertIn("Wizard", on)
        self.assertNotIn("Coworker", on)
        mw.set_enabled_for_tests(False)
        off, _ = self.draw(hist)
        self.assertIn("Coworker", off)
        self.assertNotIn("Wizard", off)

    def test_copy_whole_chat_and_copy_message_stay_plain(self):
        hist = [{"role": "user", "content": "go", "turn_start": True},
                {"role": "assistant", "content": "Done."}]
        mw.set_enabled_for_tests(True)
        whole = cv.transcript(hist)                   # the real Copy Whole Chat text
        self.assertIn("Coworker:"+chr(10)+"Done.", whole)
        self.assertNotIn("Wizard", whole)
        shown, canvas = self.draw(hist)
        self.assertIn("Wizard", shown)                # drawn as the Wizard ...
        copies = []
        for widget in {w for _r, w in canvas.hit_areas if w.startswith("copymsg:")}:     # ... Copy Message
            clicked = lay.Canvas(st.Style(), lay.fixed_measure(0.55), lay.InteractionState(clicked=widget))
            copies += [text for kind, text in cv.ChatView().draw(
                clicked, lay.Rect(0, 0, 900, 5000), hist, live=None, compact=False) if kind == "copy"]
        self.assertEqual(copies, ["Done."])
        self.assertNotIn("Wizard", "".join(copies))

    def test_the_start_button_tooltip_says_what_the_button_says(self):
        cases = (("Start", False, "Start Wizard", "Start Coworker"), ("Start", True, "Stop Wizard", "Stop Coworker"),
                 ("Connect", False, "Connect to the agent app", "Connect to the agent app"),
                 ("Start Bridge", False, "Start Bridge", "Start Bridge"),
                 ("Start Bridge", True, "Stop Bridge", "Stop Bridge"))
        for label, running, magic, plain in cases:
            mw.set_enabled_for_tests(True)
            self.assertEqual(sf.start_tip(label, running), magic)
            mw.set_enabled_for_tests(False)
            self.assertEqual(sf.start_tip(label, running), plain)
        self.assertEqual(sf.ChatSurface().top_start_label, "Start")


class TestWizardIcon(_Case):

    def test_gpu_icon_on_and_off(self):
        self.assertEqual(mw.agent_icon("GHOST_ENABLED"), "WIZARD")
        mw.set_enabled_for_tests(False)
        self.assertEqual(mw.agent_icon("GHOST_ENABLED"), "GHOST_ENABLED")
        self.assertEqual(mw.agent_icon("WIZARD"), "WIZARD")

    def test_the_chat_draws_the_wizard_png_in_every_build(self):
        self.assertIn("WIZARD", gi.IMAGE_ICONS)
        path = gi.image_path("WIZARD", 24)
        self.assertTrue(path and Path(path).is_file(), path)
        self.assertTrue(Path(path).name.startswith("wizard_"))
        view = cv.ChatView(agent_icon="GHOST_ENABLED")        # what Blender's host passes
        hist = [{"role": "user", "content": "go", "turn_start": True}, {"role": "assistant", "content": "ok"}]
        canvas = lay.Canvas(st.Style(), lay.fixed_measure(0.55), lay.InteractionState())
        view.draw(canvas, lay.Rect(0, 0, 900, 5000), hist, live=None, compact=False)
        flat = repr(canvas.commands)
        self.assertIn("WIZARD", flat)
        self.assertNotIn("GHOST_ENABLED", flat)
        mw.set_enabled_for_tests(False)
        canvas = lay.Canvas(st.Style(), lay.fixed_measure(0.55), lay.InteractionState())
        view.draw(canvas, lay.Rect(0, 0, 900, 5000), hist, live=None, compact=False)
        self.assertIn("GHOST_ENABLED", repr(canvas.commands))

    def _fake_bpy(self, icons, bfa):
        enum = {name: None for name in icons}
        prop = types.SimpleNamespace(parameters={"icon": types.SimpleNamespace(enum_items=enum)})
        layout = types.SimpleNamespace(bl_rna=types.SimpleNamespace(functions={"prop": prop}))
        app = types.SimpleNamespace(**({"bfa_version_string": "5.2"} if bfa else {}))
        previews = types.ModuleType("bpy.utils.previews")
        loaded = {}

        class Set(dict):
            def load(self, name, path, kind):
                loaded[name] = path
                self[name] = types.SimpleNamespace(icon_id=77)
        previews.new = Set
        previews.remove = lambda _s: None
        utils = types.ModuleType("bpy.utils")
        utils.previews = previews
        bpy = types.ModuleType("bpy")
        bpy.types, bpy.app, bpy.utils = types.SimpleNamespace(UILayout=layout), app, utils
        return bpy, utils, previews, loaded

    def _with(self, icons, bfa):
        bpy, utils, previews, loaded = self._fake_bpy(icons, bfa)
        saved = {k: sys.modules.get(k) for k in ("bpy", "bpy.utils", "bpy.utils.previews")}
        sys.modules.update({"bpy": bpy, "bpy.utils": utils, "bpy.utils.previews": previews})
        shared._ICONS = None
        mw.free_agent_icon()

        def restore():
            for key, value in saved.items():
                if value is None:
                    sys.modules.pop(key, None)
                else:
                    sys.modules[key] = value
            shared._ICONS = None
            mw.free_agent_icon()
        self.addCleanup(restore)
        return loaded

    def test_layout_icon_in_blender_is_the_shipped_png(self):
        blender = {l.strip() for l in (_DATA / "blender_icons.txt").read_text(encoding="utf-8").splitlines()
                   if l.strip() and not l.startswith("#")}
        self.assertNotIn("WIZARD", blender, "Blender has no wizard icon: the PNG is the fallback")
        self.assertIn("GHOST_ENABLED", blender)
        loaded = self._with(blender, bfa=False)
        self.assertEqual(shared.icon("WIZARD"), "GHOST_ENABLED", "the name resolves on Blender")
        args = mw.agent_icon_args("GHOST_ENABLED")
        self.assertEqual(args, {"icon_value": 77})
        self.assertTrue(Path(loaded["WIZARD"]).is_file(), loaded)
        mw.set_enabled_for_tests(False)
        self.assertEqual(mw.agent_icon_args("GHOST_ENABLED"), {"icon": "GHOST_ENABLED"}, "off: as today")

    def test_layout_icon_in_bforartists_is_its_own_wizard(self):
        bfa_only = {l.strip() for l in (_DATA / "bforartists_only_icons.txt").read_text(encoding="utf-8").splitlines()
                    if l.strip() and not l.startswith("#")}
        self.assertIn("WIZARD", bfa_only)
        self._with(bfa_only | {"GHOST_ENABLED"}, bfa=True)
        self.assertEqual(mw.agent_icon_args("WIZARD"), {"icon": "WIZARD"})

    def test_no_previews_falls_back_to_the_plain_icon(self):
        self._with({"GHOST_ENABLED"}, bfa=False)
        sys.modules["bpy.utils.previews"].new = lambda: (_ for _ in ()).throw(RuntimeError("background"))
        self.assertEqual(mw.agent_icon_args("GHOST_ENABLED"), {"icon": "GHOST_ENABLED"})


class TestWizardPngRobust(_Case):
    """The shipped PNG is missing, or loading it raises (Blender, Magic Wording on)."""

    _with = TestWizardIcon._with
    _fake_bpy = TestWizardIcon._fake_bpy

    def test_a_missing_png_falls_back_to_the_plain_icon(self):
        blender = {"GHOST_ENABLED", "ERROR"}
        self._with(blender, bfa=False)
        made = []
        sys.modules["bpy.utils.previews"].new = lambda: made.append(1)
        real = mw.os.path.isfile if hasattr(mw, "os") else None
        import os
        orig = os.path.isfile
        os.path.isfile = lambda p: False if str(p).endswith("wizard_32.png") else orig(p)
        self.addCleanup(setattr, os.path, "isfile", orig)
        self.assertEqual(mw.agent_icon_args("GHOST_ENABLED"), {"icon": "GHOST_ENABLED"})
        self.assertEqual(made, [], "no collection made when there is no picture")
        del real

    def test_a_failing_load_removes_the_new_collection(self):
        self._with({"GHOST_ENABLED"}, bfa=False)
        previews = sys.modules["bpy.utils.previews"]
        removed, made = [], []

        class Set(dict):
            def load(self, *a):
                raise RuntimeError("bad picture")
        previews.new = lambda: made.append(Set()) or made[-1]
        previews.remove = removed.append
        for _ in range(3):                                    # three redraws
            self.assertEqual(mw.agent_icon_args("GHOST_ENABLED"), {"icon": "GHOST_ENABLED"})
        self.assertEqual(len(made), 3)
        self.assertEqual(removed, made, "every collection made is removed again")


class TestWiring(_Case):
    """Where the labels and icons are asked for (the files need Blender, so by source)."""

    def test_the_labels_go_through_the_one_function(self):
        ui, ce = _SRC["ui_chat.py"], _SRC["chat_editor.py"]
        for needle in ('_magic.agent_label("Restart Coworker")', '_magic.agent_label("Stop Coworker to switch modes")',
                       "return _magic.agent_label(cls.bl_description)", "_magic.agent_label({",
                       "**_magic.agent_icon_args(_AGENT_ICON)", "_magic.free_agent_icon()"):
            self.assertIn(needle, ui)
        self.assertEqual(ui.count("return _magic.agent_label(cls.bl_description)"), 3, "start, stop, restart")
        self.assertIn("magic_wording.agent_label(", ce)
        self.assertIn("magic_wording.agent_icon(item[3]) if item[0] == \"AGENT\"", ce)

    def test_operator_names_and_labels_stay_plain(self):
        ui = _SRC["ui_chat.py"]
        for ident, label in (("bfacw.agent_start", "Start Coworker"), ("bfacw.agent_stop", "Stop Coworker"),
                             ("bfacw.agent_restart", "Restart Coworker")):
            self.assertIn('bl_idname = "%s"\n    bl_label = "%s"' % (ident, label), ui)

    def test_the_preference_redraw_reaches_every_surface(self):
        ui = _SRC["ui_chat.py"]
        body = ui[ui.index("def _redraw_all_chats"):ui.index("def _ui_state")]
        for needle in ("_tag_chat_regions_redraw()", "_ce._tag_chat_regions()", "area.tag_redraw()"):
            self.assertIn(needle, body)       # sidebar, editor header and regions, every area incl. status bar
        prefs = (_ADDON / "preferences.py").read_text(encoding="utf-8")
        self.assertIn("update=_update_magic_wording", prefs)


class TestNothingMagicForTheModel(_Case):

    def test_the_wording_never_reaches_the_model(self):
        for name in ("llm_transport.py", "engine_loop.py", "engine.py", "engine_acp.py", "agent_controller.py"):
            source = _SRC.get(name)
            if source is None:
                continue
            for token in ("magic_wording.agent_", "_magic.agent_", "magic_wording import"):
                self.assertNotIn(token, source, "%s must not use the agent wording" % name)
        message = hp.framing("Build the scene", "Tidy the nodes.", "NODE_EDITOR")
        results = hp.results_text([hp.Helper(id="a", parent="p", title="A", status=hp.DONE, answer="ok")])
        for text in (message, results):
            self.assertNotIn(mw.AGENT_WORD, text)
        self.assertEqual(len(re.findall(r"Wizard", message)), 0)

    def test_what_the_labels_change_is_not_in_the_prompts(self):
        skills = "\n".join(p.read_text(encoding="utf-8") for p in (_ADDON / "skills").rglob("*.md"))
        self.assertNotIn("Start Wizard", skills)
        self.assertNotIn("Stop Wizard", skills)


if __name__ == "__main__":
    unittest.main()
