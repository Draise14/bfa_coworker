"""#631: the Local context presets start at 16K (16K, 32K, 64K, 128K, Custom).

* the preset lists (llm_manager, preferences, the budget sizes) have no 4K / 8K;
* the suggestion is never below 16K;
* a saved 4096 / 8192 preset or a custom size under 16384 becomes 16384 (the backup on a
  restore, and the live preferences at start), with one console line, no error;
* the kept presets keep their stored identifiers and their enum numbers.

bpy-free: stand-in objects, the source read as text (ast).
"""
import ast
import contextlib
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import types
import unittest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ADDON = os.path.join(_REPO, "addon", "bfa_coworker")


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(_ADDON, filename))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _tree(filename):
    with open(os.path.join(_ADDON, filename), encoding="utf-8") as fh:
        return ast.parse(fh.read())


def _assigned(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == name for t in node.targets):
            return ast.literal_eval(node.value)
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", None) == name:
            return ast.literal_eval(node.value)
    raise AssertionError(name)


def _property_call(name):
    for node in ast.walk(_tree("preferences.py")):
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", None) == name:
            return node.annotation      # ``name: IntProperty(...)``: the call is the annotation
    raise AssertionError(name)


def _kw(call, key):
    return next(k.value for k in call.keywords if k.arg == key)


class TestPresetLists(unittest.TestCase):

    def test_llm_manager_sizes_start_at_16k(self):
        self.assertEqual(_assigned(_tree("llm_manager.py"), "ctx_preset_sizes"), (16384, 32768, 65536, 131072))

    def test_budget_sizes_start_at_16k(self):
        self.assertEqual(_assigned(_tree("agent_controller.py"), "_CTX_SIZES")[0], 16384)

    def test_budget_fallback_is_16k(self):
        self.assertEqual(_assigned(_tree("agent_controller.py"), "_DEFAULT_LOCAL_CTX_FALLBACK"), 16384)

    def test_the_preference_presets(self):
        items = ast.literal_eval(_kw(_property_call("local_ctx_preset"), "items"))
        self.assertEqual([i[0] for i in items], ["16384", "32768", "65536", "131072", "custom"])
        self.assertEqual([i[1] for i in items], ["16K", "32K", "64K", "128K", "Custom"])
        # Blender stores an enum as its number: the kept items keep the one they had with 4K / 8K
        # in the list (2..6), so a saved 16K / 32K / 64K / 128K / Custom still reads as itself.
        self.assertEqual([i[3] for i in items], [2, 3, 4, 5, 6])

    def test_default_is_still_32k(self):
        self.assertEqual(ast.literal_eval(_kw(_property_call("local_ctx_preset"), "default")), "32768")

    def test_the_custom_slider_starts_at_16384(self):
        self.assertEqual(ast.literal_eval(_kw(_property_call("local_ctx_size"), "min")), 16384)

    def test_the_label(self):
        llm = _load("llm_manager_631", "llm_manager.py")
        self.assertEqual([llm.ctx_preset_label(s) for s in llm.ctx_preset_sizes], ["16K", "32K", "64K", "128K"])


class TestSuggestionFloor(unittest.TestCase):

    def setUp(self):
        self.llm = _load("llm_manager_631b", "llm_manager.py")

    def test_never_below_16k_on_a_tiny_machine(self):
        profile = self.llm.model_memory_profile(None)
        for free in (0, 500, 3000, 6000, 12000, None):
            for cap in (4096, 8192, 16384, 262144):
                with self.subTest(free=free, cap=cap):
                    plan = self.llm.plan_server_memory(
                        4.0 * 1024 ** 3, 0.0, profile, free, ctx_cap=cap)
                    self.assertGreaterEqual(plan.ctx, 16384)

    def test_a_suggestion_is_one_of_the_presets(self):
        profile = self.llm.model_memory_profile(None)
        plan = self.llm.plan_server_memory(4.0 * 1024 ** 3, 0.0, profile, 6000)
        self.assertIn(plan.ctx, self.llm.ctx_preset_sizes)

    def test_min_agent_ctx(self):
        self.assertGreaterEqual(self.llm.min_agent_ctx(), 16384)


class TestMigrationOfASavedBackup(unittest.TestCase):

    def setUp(self):
        self.st = _load("settings_transfer_631", "settings_transfer.py")

    def _migrate(self, data):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            out = self.st.migrate_ctx_data(data)
        return out, [ln for ln in buf.getvalue().splitlines() if ln.strip()]

    def test_each_old_preset_becomes_16k_with_one_line(self):
        for old in ("4096", "8192"):
            with self.subTest(preset=old):
                out, lines = self._migrate({"local_ctx_preset": old, "local_ctx_size": int(old)})
                self.assertEqual((out["local_ctx_preset"], out["local_ctx_size"]), ("16384", 16384))
                self.assertEqual(len(lines), 1)
                self.assertIn("16K", lines[0])

    def test_an_old_preset_with_a_big_size_is_still_moved(self):
        out, lines = self._migrate({"local_ctx_preset": "8192", "local_ctx_size": 40000})
        self.assertEqual((out["local_ctx_preset"], out["local_ctx_size"]), ("16384", 16384))
        self.assertEqual(len(lines), 1)

    def test_a_custom_size_under_16384(self):
        for size in (4096, 9000, 16383):
            with self.subTest(size=size):
                out, lines = self._migrate({"local_ctx_preset": "custom", "local_ctx_size": size})
                self.assertEqual((out["local_ctx_preset"], out["local_ctx_size"]), ("16384", 16384))
                self.assertEqual(len(lines), 1)

    def test_good_values_are_left_alone_and_silent(self):
        for preset, size in (("16384", 16384), ("32768", 32768), ("custom", 16384), ("custom", 50000),
                             ("131072", 131072)):
            with self.subTest(preset=preset, size=size):
                data = {"local_ctx_preset": preset, "local_ctx_size": size, "x": 1}
                out, lines = self._migrate(data)
                self.assertIs(out, data)
                self.assertEqual(lines, [])

    def test_a_backup_without_the_keys_is_untouched(self):
        data = {"remote_model": "m"}
        out, lines = self._migrate(data)
        self.assertEqual((out, lines), (data, []))

    def test_other_settings_survive(self):
        out, _ = self._migrate({"local_ctx_preset": "8192", "local_ctx_size": 8192, "remote_model": "m"})
        self.assertEqual(out["remote_model"], "m")


class _Prop:
    def __init__(self, ident, ptype, default, items=()):
        self.identifier, self.type, self.default = ident, ptype, default
        self.is_readonly = self.is_array = False
        self.subtype = "NONE"
        self.enum_items = [types.SimpleNamespace(identifier=i) for i in items]


class _Prefs:
    """Preferences stand-in: the preset's update callback fills the size, like the real one;
    a stored value that is no item reads as '' (blank drop-down)."""

    ITEMS = ("16384", "32768", "65536", "131072", "custom")

    def __init__(self, preset, size, restored=False):
        props = [_Prop("settings_restored", "BOOLEAN", False),
                 _Prop("local_ctx_size", "INT", 32768),
                 _Prop("local_ctx_preset", "ENUM", "32768", self.ITEMS)]
        self.__dict__.update(bl_rna=types.SimpleNamespace(properties=props),
                             _v={"local_ctx_preset": preset, "local_ctx_size": size,
                                 "settings_restored": restored})

    def __getattr__(self, name):
        try:
            value = self.__dict__["_v"][name]
        except KeyError:
            raise AttributeError(name) from None
        if name == "local_ctx_preset" and value not in self.ITEMS:
            return ""
        return value

    def __setattr__(self, key, value):
        if key == "local_ctx_preset":
            if value not in self.ITEMS:
                raise TypeError("enum {!r} not found".format(value))
            if value != "custom":
                self._v["local_ctx_size"] = int(value)
        if key == "local_ctx_size":
            value = max(value, 16384)          # the slider's minimum
        self._v[key] = value


class TestMigrationOfLivePreferences(unittest.TestCase):

    def setUp(self):
        self.st = _load("settings_transfer_631b", "settings_transfer.py")

    def _run(self, prefs):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            changed = self.st.migrate_ctx_prefs(prefs)
        return changed, [ln for ln in buf.getvalue().splitlines() if ln.strip()]

    def test_a_blank_preset_from_a_saved_8k(self):
        prefs = _Prefs("8192", 8192)                       # reads as '' in Blender
        self.assertEqual(prefs.local_ctx_preset, "")
        changed, lines = self._run(prefs)
        self.assertTrue(changed)
        self.assertEqual((prefs.local_ctx_preset, prefs.local_ctx_size), ("16384", 16384))
        self.assertEqual(len(lines), 1)

    def test_a_blank_preset_with_a_valid_size_is_still_filled(self):
        prefs = _Prefs("4096", 32768)
        changed, _ = self._run(prefs)
        self.assertTrue(changed)
        self.assertEqual(prefs.local_ctx_preset, "16384")

    def test_a_small_custom_size(self):
        prefs = _Prefs("custom", 9000)
        prefs._v["local_ctx_size"] = 9000                  # a value stored under the old minimum
        changed, lines = self._run(prefs)
        self.assertTrue(changed)
        self.assertEqual((prefs.local_ctx_preset, prefs.local_ctx_size), ("16384", 16384))
        self.assertEqual(len(lines), 1)

    def test_good_settings_change_nothing_and_print_nothing(self):
        for preset, size in (("32768", 32768), ("16384", 16384), ("custom", 24576)):
            with self.subTest(preset=preset):
                prefs = _Prefs(preset, size)
                changed, lines = self._run(prefs)
                self.assertFalse(changed)
                self.assertEqual(lines, [])
                self.assertEqual((prefs.local_ctx_preset, prefs.local_ctx_size), (preset, size))

    def test_never_raises(self):
        class Boom:
            def __getattr__(self, name):
                raise RuntimeError("boom")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertFalse(self.st.migrate_ctx_prefs(Boom()))


class TestRestoreWithAnOldBackup(unittest.TestCase):

    def setUp(self):
        self.st = _load("settings_transfer_631c", "settings_transfer.py")
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)

    def _write(self, preset, size):
        payload = {"format": 2, "version": "1.1.43", "secrets": {},
                   "settings": {"local_ctx_preset": preset, "local_ctx_size": size}}
        with open(self.st.settings_path(self.dir), "w", encoding="utf-8") as fh:
            json.dump(payload, fh)

    def test_a_fresh_install_gets_16k_from_a_saved_8k(self):
        self._write("8192", 8192)
        prefs = _Prefs("32768", 32768)                     # defaults
        with contextlib.redirect_stdout(io.StringIO()) as out:
            applied = self.st.restore_once(prefs, self.dir)
        self.assertEqual((prefs.local_ctx_preset, prefs.local_ctx_size), ("16384", 16384))
        self.assertIn("local_ctx_preset", applied)
        self.assertEqual(sum("16K" in ln for ln in out.getvalue().splitlines()), 1)

    def test_a_saved_4k_and_a_small_custom_size(self):
        for preset, size in (("4096", 4096), ("custom", 8192)):
            with self.subTest(preset=preset):
                self._write(preset, size)
                prefs = _Prefs("32768", 32768)
                with contextlib.redirect_stdout(io.StringIO()):
                    self.st.restore_once(prefs, self.dir)
                self.assertEqual((prefs.local_ctx_preset, prefs.local_ctx_size), ("16384", 16384))

    def test_a_kept_preset_is_restored_as_it_was(self):
        self._write("65536", 65536)
        prefs = _Prefs("32768", 32768)
        with contextlib.redirect_stdout(io.StringIO()):
            self.st.restore_once(prefs, self.dir)
        self.assertEqual((prefs.local_ctx_preset, prefs.local_ctx_size), ("65536", 65536))

    def test_the_migrated_backup_is_not_taken_for_a_newer_change(self):
        # The live prefs were already moved to 16K: the old backup must not make the restore skip.
        self._write("8192", 8192)
        prefs = _Prefs("16384", 16384)
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.st.restore_once(prefs, self.dir)
        self.assertNotIn("skipped", out.getvalue())


class TestWiring(unittest.TestCase):

    def test_the_timer_migrates_after_the_restore_and_before_auto_start(self):
        fn = next(n for n in ast.walk(_tree("__init__.py"))
                  if isinstance(n, ast.FunctionDef) and n.name == "_after_register_timer")
        calls = [ast.unparse(n.value.func) for n in fn.body if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)]
        self.assertLess(calls.index("_restore_settings_backup"), calls.index("_migrate_ctx_preset"))
        self.assertLess(calls.index("_migrate_ctx_preset"), calls.index("_schedule_autostart"))

    def test_the_small_context_warning_starts_below_16k(self):
        with open(os.path.join(_ADDON, "llm_manager.py"), encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn("if ctx_size < 16384:", src)
        self.assertNotIn("ctx_size <= 8192", src)


if __name__ == "__main__":
    unittest.main()
