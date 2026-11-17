"""#575: your choices in Coworker's settings are kept when Blender quits, and the settings restore
never undoes a newer change.  Blender saves its preferences on quit only when they are marked
changed; a Python assignment to an add-on's setting does not mark them (a button does).

* ``shared.set_user_pref`` / ``shared.mark_preferences_changed``: the one helper.
* the call sites: every operator / menu that stores a user's choice from code goes through it;
  start-up and timer bookkeeping never mark the preferences changed.
* ``settings_transfer.restore_once``: after a session that ended without saving the preferences
  (a crash, a kill) the restore comes again, but restores nothing when a setting is newer than
  the backup.

bpy-free: stand-in objects and the source read as text (ast).
"""
import ast
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import types
import unittest
from unittest import mock

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ADDON = os.path.join(_REPO, "addon", "bfa_coworker")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Preferences:
    """``context.preferences``: only the flag Blender saves on quit by."""

    def __init__(self):
        self.is_dirty = False


class _Prefs:
    """An add-on's preferences: plain attributes; an enum refuses unknown values like Blender."""

    def __init__(self, **values):
        self.__dict__.update(values)


# ---------------------------------------------------------------------------
# The helper


class TestSetUserPref(unittest.TestCase):

    def setUp(self):
        self.shared = _load("shared_575", os.path.join(_ADDON, "shared.py"))

    def test_a_new_value_is_stored_and_marks_the_preferences_changed(self):
        prefs, preferences = _Prefs(local_ctx_size=8192), _Preferences()
        self.assertTrue(self.shared.set_user_pref(prefs, "local_ctx_size", 16384, preferences))
        self.assertEqual(prefs.local_ctx_size, 16384)
        self.assertTrue(preferences.is_dirty)

    def test_the_same_value_does_not_mark_them(self):
        # Marking without a change makes Blender write its whole copy on quit: with two windows
        # open, the one quitting last would cover the other's newer preferences for nothing.
        prefs, preferences = _Prefs(translate_language="en"), _Preferences()
        self.assertFalse(self.shared.set_user_pref(prefs, "translate_language", "en", preferences))
        self.assertFalse(preferences.is_dirty)

    def test_a_refused_value_raises_and_marks_nothing(self):
        class _Enum(_Prefs):
            def __setattr__(self, key, value):
                if value not in ("en", "de"):
                    raise TypeError("enum not found")
                super().__setattr__(key, value)
        prefs, preferences = _Enum(translate_language="en"), _Preferences()
        with self.assertRaises(TypeError):
            self.shared.set_user_pref(prefs, "translate_language", "xx", preferences)
        self.assertFalse(preferences.is_dirty)

    def test_without_preferences_it_uses_blenders(self):
        preferences = _Preferences()
        bpy = types.ModuleType("bpy")
        bpy.context = types.SimpleNamespace(preferences=preferences)
        with mock.patch.dict(sys.modules, {"bpy": bpy}):
            self.shared.set_user_pref(_Prefs(remote_model="a"), "remote_model", "b")
        self.assertTrue(preferences.is_dirty)

    def test_no_blender_is_harmless(self):
        with mock.patch.dict(sys.modules, {"bpy": None}):
            self.shared.mark_preferences_changed()          # ImportError swallowed
        self.shared.mark_preferences_changed(object())      # no is_dirty: swallowed


# ---------------------------------------------------------------------------
# The call sites (source read as text)


def _tree(name):
    with open(os.path.join(_ADDON, name), encoding="utf-8") as fh:
        return ast.parse(fh.read())


def _function(tree, name, cls=None):
    for node in ast.walk(tree):
        if cls is not None and isinstance(node, ast.ClassDef) and node.name == cls:
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == name:
                    return item
        if cls is None and isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError("{}.{} not found".format(cls or "", name))


def _calls(fn, name):
    return [n for n in ast.walk(fn) if isinstance(n, ast.Call)
            and (getattr(n.func, "id", None) == name or getattr(n.func, "attr", None) == name)]


def _pref_assignments(fn):
    """``prefs.x = ...`` / ``setattr(prefs, ...)`` / ``...preferences.x = ...`` in *fn*."""
    out = []
    for n in ast.walk(fn):
        targets = n.targets if isinstance(n, ast.Assign) else [n.target] if isinstance(n, ast.AugAssign) else []
        for t in targets:
            for e in (t.elts if isinstance(t, ast.Tuple) else [t]):
                if isinstance(e, ast.Attribute):
                    base = ast.unparse(e.value)
                    if base in ("prefs", "_prefs") or base.endswith("preferences"):
                        out.append(ast.unparse(e))
        if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "setattr" and \
                ast.unparse(n.args[0]) in ("prefs", "_prefs"):
            out.append(ast.unparse(n))
    return out


# A user's action is behind each of these: what they store must be kept on quit.
_USER_CHOICES = (
    ("operators_agent.py", "BFACW_OT_load_provider", "execute"),
    ("operators_llm.py", "_BFACW_OT_select_preset", "execute"),
    ("operators_llm.py", "_BFACW_OT_set_ctx_preset", "execute"),
    ("operators_llm.py", "_BFACW_OT_select_existing_model", "execute"),
    ("operators_llm.py", "_BFACW_OT_download_custom_model", "execute"),
    ("ui_context_menus.py", "BFACW_OT_translate_swap", "execute"),
    ("ui_chat.py", None, "_remote_model_set"),
    ("chat_editor.py", None, "_remember_input_lines"),
)


class TestCallSites(unittest.TestCase):

    def test_every_user_choice_goes_through_the_helper(self):
        for path, cls, name in _USER_CHOICES:
            with self.subTest(where="{}:{}.{}".format(path, cls or "", name)):
                fn = _function(_tree(path), name, cls)
                self.assertTrue(_calls(fn, "set_user_pref"), "no set_user_pref")
                self.assertEqual(_pref_assignments(fn), [], "a plain assignment is not saved on quit")

    def test_saved_provider_profiles_are_kept(self):
        tree = _tree("operators_agent.py")
        for cls in ("BFACW_OT_save_provider", "BFACW_OT_delete_provider"):
            with self.subTest(cls=cls):
                fn = _function(tree, "execute", cls)
                self.assertTrue(_calls(fn, "_set_saved_providers"))
                self.assertTrue(_calls(fn, "mark_preferences_changed"))

    def test_bookkeeping_never_marks_the_preferences_changed(self):
        # Start-up, timers, status text and the Preferences tab: no change of yours.
        for path, cls, name in (("__init__.py", None, "_migrate_operating_mode"),
                                ("__init__.py", None, "_after_register_timer"),
                                ("__init__.py", None, "_schedule_autostart"),
                                ("operators_agent.py", "_BFACW_OT_refresh_remote_models", "execute"),
                                ("operators_agent.py", None, "_open_addon_prefs_filtered"),
                                ("preferences.py", "BFACW_OT_pref_tab_select", "execute")):
            with self.subTest(where="{}:{}.{}".format(path, cls or "", name)):
                fn = _function(_tree(path), name, cls)
                src = ast.unparse(fn)
                for word in ("set_user_pref", "mark_preferences_changed", "is_dirty"):
                    self.assertNotIn(word, src)

    def test_the_restore_marks_them_only_for_a_real_change(self):
        fn = _function(_tree("__init__.py"), "_restore_settings_backup")
        for n in ast.walk(fn):
            if isinstance(n, ast.Assign) and ast.unparse(n.targets[0]).endswith("is_dirty"):
                parent = next(p for p in ast.walk(fn) if isinstance(p, ast.If) and n in p.body)
                self.assertEqual(ast.unparse(parent.test), "changed")
                break
        else:
            self.fail("no is_dirty in _restore_settings_backup")

    def test_coworker_never_saves_or_reloads_the_preferences_itself(self):
        # Blender's Auto-Save Preferences decides (it can be off); two windows: last quit wins.
        for name in os.listdir(_ADDON):
            if name.endswith(".py") and name != "weak_sandbox.py":
                with open(os.path.join(_ADDON, name), encoding="utf-8") as fh:
                    src = fh.read()
                for call in ("wm.save_userpref(", "wm.read_userpref(", "wm.read_factory_userpref("):
                    self.assertNotIn(call, src, name)


# ---------------------------------------------------------------------------
# The settings restore (#86) after a session that did not save


class _Prop:
    def __init__(self, ident, ptype, default, items=()):
        self.identifier, self.type, self.default = ident, ptype, default
        self.is_readonly = self.is_array = False
        self.subtype = "PASSWORD" if ident.endswith("_key") else "NONE"
        self.enum_items = [types.SimpleNamespace(identifier=i) for i in items]


class _FakePrefs:
    def __init__(self, props, values):
        self.__dict__.update(bl_rna=types.SimpleNamespace(properties=props), _values=dict(values))

    def __getattr__(self, name):
        if name in self.__dict__.get("_values", {}):
            return self._values[name]
        raise AttributeError(name)

    def __setattr__(self, key, value):
        self._values[key] = value


_DEFAULTS = dict(settings_restored=False, remote_api_key="", auto_continue_rounds=3, translate_language="en",
                 timer_interval_active=0.1, remote_model="", whisper_model="")


def _prefs(**values):
    props = [_Prop("settings_restored", "BOOLEAN", False), _Prop("remote_api_key", "STRING", ""),
             _Prop("auto_continue_rounds", "INT", 3), _Prop("translate_language", "ENUM", "en", ("en", "de", "fr")),
             _Prop("timer_interval_active", "FLOAT", 0.1), _Prop("remote_model", "STRING", ""),
             # Items from a function: Blender gives no fixed default.
             _Prop("whisper_model", "ENUM", "")]
    v = dict(_DEFAULTS)
    v.update(values)
    return _FakePrefs(props, v)


class TestRestoreAfterACrash(unittest.TestCase):

    def setUp(self):
        self.st = _load("settings_transfer_575", os.path.join(_ADDON, "settings_transfer.py"))
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        # The backup a clean quit wrote: your settings then.
        self.st.save(_prefs(remote_api_key="sk-made-up-575", auto_continue_rounds=7, translate_language="de",
                            settings_restored=True), self.dir, "1.1.43")

    def test_a_fresh_install_restores(self):
        new = _prefs()
        self.assertIn("auto_continue_rounds", self.st.restore_once(new, self.dir))
        self.assertEqual((new.auto_continue_rounds, new.translate_language, new.remote_api_key),
                         (7, "de", "sk-made-up-575"))
        self.assertTrue(new.settings_restored)

    def test_after_a_crash_it_restores_again(self):
        # The restore was never saved (Blender crashed): the next start holds defaults again,
        # and restoring once more is what brings your settings back.
        self.st.restore_once(_prefs(), self.dir)
        again = _prefs()
        self.assertIn("auto_continue_rounds", self.st.restore_once(again, self.dir))
        self.assertEqual(again.auto_continue_rounds, 7)

    def test_a_newer_setting_is_never_undone(self):
        # Changed after the backup was written: nothing at all is restored over it.
        mine = _prefs(auto_continue_rounds=9)
        with mock.patch("builtins.print") as out:
            self.assertEqual(self.st.restore_once(mine, self.dir), [])
        self.assertEqual((mine.auto_continue_rounds, mine.translate_language, mine.remote_api_key), (9, "en", ""))
        self.assertTrue(mine.settings_restored)
        self.assertIn("auto_continue_rounds", out.call_args[0][0])

    def test_a_newer_key_is_named_never_shown(self):
        mine = _prefs(remote_api_key="sk-mine-newer-000")
        with mock.patch("builtins.print") as out:
            self.assertEqual(self.st.restore_once(mine, self.dir), [])
        self.assertEqual(mine.remote_api_key, "sk-mine-newer-000")
        printed = " ".join(str(c) for c in out.call_args_list)
        self.assertIn("remote_api_key", printed)
        self.assertNotIn("sk-mine", printed)
        self.assertNotIn("sk-made-up", printed)

    def test_values_equal_to_the_backup_or_their_default_are_not_newer(self):
        self.assertEqual(self.st.newer_than_backup(_prefs(auto_continue_rounds=7, timer_interval_active=0.1 + 1e-9),
                                                   self.st.load(self.dir)), [])

    def test_a_setting_without_a_fixed_default_is_left_out(self):
        data = dict(self.st.load(self.dir), whisper_model="base")
        self.assertEqual(self.st.newer_than_backup(_prefs(whisper_model="small"), data), [])

    def test_a_setting_the_backup_does_not_have_is_left_out(self):
        # An older copy's backup, from before this setting existed.
        data = {k: v for k, v in self.st.load(self.dir).items() if k != "remote_model"}
        self.assertEqual(self.st.newer_than_backup(_prefs(remote_model="mine"), data), [])

    def test_no_backup_still_marks_the_install_restored(self):
        empty = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, empty, True)
        new = _prefs(auto_continue_rounds=9)
        self.assertEqual(self.st.restore_once(new, empty), [])
        self.assertTrue(new.settings_restored)

    def test_backup_file_is_the_one_coworker_writes(self):
        with open(self.st.settings_path(self.dir), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["format"], 2)


if __name__ == "__main__":
    unittest.main()
