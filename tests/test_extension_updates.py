# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Issue #86: updates through Blender's extension system.

* ``extension_repo.py`` -- the ``index.json`` listing and the installer build.
* ``settings_transfer.py`` -- preferences survive the move into the update
  repository.
* ``installer/bfa_coworker_installer`` -- first-run setup, migration from a
  copy installed from a file, rollback, uninstall -- against a fake ``bpy``
  that performs real file operations.
* End to end with **Blender's own extension client** (``blender_ext.py``,
  pure Python): sync the listing over HTTP, install, upgrade.  Set
  ``BLENDER_EXT_CLI`` to ``<blender>/<version>/scripts/addons_core/bl_pkg/cli/blender_ext.py``;
  skipped when it is not available.

Run with::

    python -m unittest tests.test_extension_updates -v
"""

__all__ = ()

import functools
import http.server
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import types
import unittest
import zipfile

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)
import extension_repo  # noqa: E402  pylint: disable=wrong-import-position

_MANIFEST = """schema_version = "1.0.0"
id = "{id}"
version = "{version}"
name = "Coworker"
tagline = "test build"
maintainer = "Bforartists"
type = "add-on"
website = "https://github.com/Draise14/bfa_coworker"
blender_version_min = "5.1.0"
license = ["SPDX:GPL-3.0-or-later"]
tags = ["Development"]

[permissions]
network = "test"
"""


def _make_ext_zip(path: str, version: str, ext_id: str = "bfa_coworker", top_folder: bool = True) -> str:
    prefix = ext_id + "/" if top_folder else ""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(prefix + "blender_manifest.toml", _MANIFEST.format(id=ext_id, version=version))
        zf.writestr(prefix + "__init__.py", "def register(): pass\ndef unregister(): pass\n")
    return path


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _load(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Listing & installer build


class TestListing(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_entry_has_what_blender_needs(self):
        z = _make_ext_zip(os.path.join(self.tmp, "bfa_coworker-1.1.37.zip"), "1.1.37")
        e = extension_repo.index_entry(z, "https://example.com/bfa_coworker-1.1.37.zip")
        for key in ("id", "schema_version", "name", "version", "tagline", "maintainer", "type",
                    "blender_version_min", "license", "archive_url", "archive_size", "archive_hash"):
            self.assertIn(key, e)
        self.assertEqual(e["archive_size"], os.path.getsize(z))
        self.assertTrue(e["archive_hash"].startswith("sha256:"))
        self.assertEqual(e["permissions"], {"network": "test"})

    def test_flat_and_folder_zips_are_both_read(self):
        a = _make_ext_zip(os.path.join(self.tmp, "a.zip"), "1.0.0", top_folder=False)
        b = _make_ext_zip(os.path.join(self.tmp, "b.zip"), "2.0.0", top_folder=True)
        self.assertEqual(extension_repo.manifest_from_zip(a)["version"], "1.0.0")
        self.assertEqual(extension_repo.manifest_from_zip(b)["version"], "2.0.0")

    def test_upsert_keeps_one_version_per_id(self):
        idx = extension_repo.load_index(os.path.join(self.tmp, "missing.json"))
        self.assertEqual((idx["version"], idx["blocklist"], idx["data"]), ("v1", [], []))
        idx = extension_repo.upsert(idx, {"id": "bfa_coworker", "version": "1.1.37"})
        idx = extension_repo.upsert(idx, {"id": "other", "version": "1.0"})
        idx = extension_repo.upsert(idx, {"id": "bfa_coworker", "version": "1.1.38"})
        self.assertEqual([(e["id"], e["version"]) for e in idx["data"]],
                         [("bfa_coworker", "1.1.38"), ("other", "1.0")])

    def test_drag_link_carries_the_repository(self):
        link = extension_repo.drag_install_url(
            {"archive_url": "https://github.com/x/y/releases/download/v1/bfa_coworker-1.zip",
             "blender_version_min": "5.1.0"})
        self.assertIn("repository=https%3A%2F%2Fraw.githubusercontent.com", link)
        self.assertIn("blender_version_min=5.1.0", link)
        self.assertTrue(link.startswith("https://github.com/x/y/releases/download/v1/bfa_coworker-1.zip?"))

    def test_committed_listing_is_valid_and_points_at_releases(self):
        idx = extension_repo.load_index()
        self.assertEqual(idx["version"], "v1")
        for e in idx["data"]:
            self.assertTrue(e["archive_url"].startswith(
                "https://github.com/Draise14/bfa_coworker/releases/download/"), e["archive_url"])
            self.assertTrue(e["archive_hash"].startswith("sha256:"))

    def test_repo_url_is_the_same_everywhere(self):
        shared = _read(os.path.join(_REPO, "addon", "bfa_coworker", "shared.py"))
        inst = _read(os.path.join(_REPO, "installer", "bfa_coworker_installer", "__init__.py"))
        self.assertIn(extension_repo.REPO_INDEX_URL, shared)
        self.assertIn(extension_repo.REPO_INDEX_URL, inst)

    def test_installer_bundles_the_coworker_zip_with_matching_version(self):
        z = _make_ext_zip(os.path.join(self.tmp, "bfa_coworker-1.1.37.zip"), "1.1.37")
        out = extension_repo.build_installer(z, self.tmp)
        self.assertTrue(out.endswith("bfa_coworker_installer-1.1.37.zip"))
        with zipfile.ZipFile(out) as zf:
            names = zf.namelist()
            self.assertIn("bfa_coworker_installer/__init__.py", names)
            self.assertIn("bfa_coworker_installer/payload/bfa_coworker-1.1.37.zip", names)
            self.assertIn('version = "1.1.37"', zf.read("bfa_coworker_installer/blender_manifest.toml").decode())
            with open(z, "rb") as fh:
                self.assertEqual(zf.read("bfa_coworker_installer/payload/bfa_coworker-1.1.37.zip"), fh.read())
        self.assertEqual(extension_repo.manifest_from_zip(out)["id"], "bfa_coworker_installer")


# ---------------------------------------------------------------------------
# Settings carry-over


class _Prop:
    def __init__(self, ident, ptype, items=(), readonly=False):
        self.identifier, self.type, self.is_readonly = ident, ptype, readonly
        self.is_array = False
        self.enum_items = [types.SimpleNamespace(identifier=i, value=n) for n, i in enumerate(items)]


class _FakePrefs:
    """Mimics AddonPreferences: attribute reads, item writes bypass 'update'."""

    def __init__(self, props, values):
        self.bl_rna = types.SimpleNamespace(properties=props)
        self._props = {p.identifier: p for p in props}
        self._values = dict(values)
        self.update_calls = []

    def __getattr__(self, name):
        values = self.__dict__.get("_values", {})
        if name in values:
            return values[name]
        raise AttributeError(name)

    def __setitem__(self, key, value):
        prop = self._props[key]
        if prop.type == "ENUM":
            value = next(i.identifier for i in prop.enum_items if i.value == value)
        self._values[key] = value


def _coworker_prefs(**values):
    props = [
        _Prop("rna_type", "POINTER"),
        _Prop("settings_restored", "BOOLEAN"),
        _Prop("remote_api_key", "STRING"),
        _Prop("local_ctx_size", "INT"),
        _Prop("timer_interval_active", "FLOAT"),
        _Prop("agent_autostart", "BOOLEAN"),
        _Prop("operating_mode", "ENUM", items=("LOCAL_LLM", "REMOTE_API", "EXTERNAL_HARNESS")),
        _Prop("show_advanced", "BOOLEAN"),
        _Prop("model_preset_info", "STRING", readonly=True),
    ]
    base = dict(settings_restored=False, remote_api_key="", local_ctx_size=16384,
                timer_interval_active=0.1, agent_autostart=False, operating_mode="LOCAL_LLM",
                show_advanced=False, model_preset_info="")
    base.update(values)
    return _FakePrefs(props, base)


class TestSettingsTransfer(unittest.TestCase):

    def setUp(self):
        self.st = _load("settings_transfer_t", os.path.join(_REPO, "addon", "bfa_coworker", "settings_transfer.py"))
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_round_trip_into_a_fresh_install(self):
        old = _coworker_prefs(remote_api_key="sk-123", local_ctx_size=32768, operating_mode="REMOTE_API",
                              agent_autostart=True, timer_interval_active=0.25, settings_restored=True)
        self.assertTrue(self.st.save(old, self.dir, "1.1.36"))
        new = _coworker_prefs()
        applied = self.st.restore_once(new, self.dir)
        self.assertEqual(set(applied), {"remote_api_key", "local_ctx_size", "operating_mode",
                                        "agent_autostart", "timer_interval_active"})
        self.assertEqual((new.remote_api_key, new.local_ctx_size, new.operating_mode),
                         ("sk-123", 32768, "REMOTE_API"))
        self.assertTrue(new.settings_restored)

    def test_ui_toggles_readonly_and_bookkeeping_are_not_carried(self):
        data = self.st.dump(_coworker_prefs(show_advanced=True, model_preset_info="x"))
        for key in ("rna_type", "settings_restored", "show_advanced", "model_preset_info"):
            self.assertNotIn(key, data)

    def test_upgrade_in_place_is_never_overwritten(self):
        self.st.save(_coworker_prefs(local_ctx_size=4096), self.dir)
        current = _coworker_prefs(local_ctx_size=65536, settings_restored=True)
        self.assertEqual(self.st.restore_once(current, self.dir), [])
        self.assertEqual(current.local_ctx_size, 65536)

    def test_bad_values_and_removed_options_are_skipped(self):
        new = _coworker_prefs()
        applied = self.st.apply(new, {"operating_mode": "REMOVED_MODE", "local_ctx_size": "big",
                                      "agent_autostart": True, "unknown_key": 1})
        self.assertEqual(applied, ["agent_autostart"])
        self.assertEqual(new.operating_mode, "LOCAL_LLM")

    def test_no_backup_marks_the_install_restored(self):
        new = _coworker_prefs()
        self.assertEqual(self.st.restore_once(new, self.dir), [])
        self.assertTrue(new.settings_restored)

    def test_installer_writes_the_same_format_coworker_reads(self):
        # The installer has its own small copy of dump() for old Coworker builds.
        fake = _FakeBlender(self.dir)
        inst = fake.load_installer()
        fake.addons["bl_ext.user_default.bfa_coworker"] = types.SimpleNamespace(
            preferences=_coworker_prefs(remote_api_key="sk-old", settings_restored=True))
        self.assertTrue(inst.backup_settings("bl_ext.user_default.bfa_coworker"))
        new = _coworker_prefs()
        self.st.restore_once(new, os.path.join(fake.config_dir, "bfa_coworker"))
        self.assertEqual(new.remote_api_key, "sk-old")


# ---------------------------------------------------------------------------
# The installer, against a fake bpy that really moves files


class _Repo:
    def __init__(self, name, module, directory, remote_url=""):
        self.name, self.module, self.directory = name, module, directory
        self.remote_url, self.use_remote_url = remote_url, bool(remote_url)
        self.enabled, self.use_sync_on_startup = True, False


class _FakeBlender:
    """Just enough of bpy for the installer: repositories are real folders."""

    def __init__(self, root):
        self.root = root
        self.config_dir = os.path.join(root, "config")
        self.repos = [_Repo("User Default", "user_default", os.path.join(root, "user_default"))]
        os.makedirs(self.repos[0].directory, exist_ok=True)
        self.addons = {}
        self.calls = []
        self.online = True

    # -- operators ---------------------------------------------------------
    def repo_add(self, name, remote_url, type, use_sync_on_startup):  # noqa: A002
        module = name.replace(" ", "_")
        repo = _Repo(name, module, os.path.join(self.root, module), remote_url)
        repo.use_sync_on_startup = use_sync_on_startup
        os.makedirs(repo.directory, exist_ok=True)
        self.repos.append(repo)
        self.calls.append(("repo_add", name))
        return {'FINISHED'}

    def repo_remove(self, index, remove_files):
        repo = self.repos.pop(index)
        if remove_files:
            shutil.rmtree(repo.directory, ignore_errors=True)
        self.calls.append(("repo_remove", repo.module))
        return {'FINISHED'}

    def install_files(self, filepath, repo, enable_on_install):
        target = next(r for r in self.repos if r.module == repo)
        with zipfile.ZipFile(filepath) as zf:
            zf.extractall(target.directory)
        if enable_on_install:
            self.addons["bl_ext.{:s}.bfa_coworker".format(repo)] = types.SimpleNamespace(preferences=None)
        self.calls.append(("install", repo, os.path.basename(filepath)))
        return {'FINISHED'}

    def uninstall(self, repo_index, pkg_id):
        repo = self.repos[repo_index]
        shutil.rmtree(os.path.join(repo.directory, pkg_id), ignore_errors=True)
        self.addons.pop("bl_ext.{:s}.{:s}".format(repo.module, pkg_id), None)
        self.calls.append(("uninstall", repo.module, pkg_id))
        return {'FINISHED'}

    def sync(self, repo_index):
        self.calls.append(("sync", self.repos[repo_index].module))
        return {'FINISHED'}

    # -- module ------------------------------------------------------------
    def bpy_module(self):
        fake = self

        class _Base:
            pass
        bpy = types.ModuleType("bpy")
        bpy.context = types.SimpleNamespace(preferences=types.SimpleNamespace(
            extensions=types.SimpleNamespace(repos=self.repos), is_dirty=False, addons=self.addons))
        bpy.ops = types.SimpleNamespace(
            preferences=types.SimpleNamespace(extension_repo_add=self.repo_add,
                                              extension_repo_remove=self.repo_remove),
            extensions=types.SimpleNamespace(package_install_files=self.install_files,
                                             package_uninstall=self.uninstall,
                                             repo_sync=self.sync))
        bpy.app = types.SimpleNamespace(online_access=self.online, background=True,
                                        timers=types.SimpleNamespace(register=lambda *a, **k: None,
                                                                     is_registered=lambda f: False))
        bpy.utils = types.SimpleNamespace(user_resource=lambda kind: fake.config_dir,
                                          register_class=lambda c: None, unregister_class=lambda c: None)
        bpy.types = types.SimpleNamespace(AddonPreferences=_Base, Operator=_Base)
        props = types.ModuleType("bpy.props")
        props.BoolProperty = lambda **kw: kw.get("default")
        bpy.props = props
        return bpy, props

    def load_installer(self, payload_version="1.1.37"):
        bpy, props = self.bpy_module()
        sys.modules["bpy"], sys.modules["bpy.props"] = bpy, props
        src = os.path.join(_REPO, "installer", "bfa_coworker_installer")
        pkg_dir = os.path.join(self.root, "installer_pkg", "bfa_coworker_installer")
        shutil.copytree(src, pkg_dir, dirs_exist_ok=True)
        os.makedirs(os.path.join(pkg_dir, "payload"), exist_ok=True)
        _make_ext_zip(os.path.join(pkg_dir, "payload", "bfa_coworker-{:s}.zip".format(payload_version)),
                      payload_version)
        spec = importlib.util.spec_from_file_location(
            "bl_ext.user_default.bfa_coworker_installer", os.path.join(pkg_dir, "__init__.py"),
            submodule_search_locations=[pkg_dir])
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def install_from_file(self, version, repo_index=0, enabled=True):
        z = _make_ext_zip(os.path.join(self.root, "manual-{:s}.zip".format(version)), version)
        repo = self.repos[repo_index]
        with zipfile.ZipFile(z) as zf:
            zf.extractall(repo.directory)
        if enabled:
            self.addons["bl_ext.{:s}.bfa_coworker".format(repo.module)] = types.SimpleNamespace(
                preferences=_coworker_prefs(remote_api_key="sk-from-file", settings_restored=True))


class TestInstaller(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.fake = _FakeBlender(self.root)
        self._saved = {k: sys.modules.get(k) for k in ("bpy", "bpy.props")}

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
        shutil.rmtree(self.root, ignore_errors=True)

    def _versions(self, inst):
        return sorted((c["repo"].module, c["version"]) for c in inst.installed_copies())

    def test_fresh_install_adds_repo_and_installs_bundled_copy(self):
        inst = self.fake.load_installer()
        msgs = inst.setup()
        self.assertEqual(self._versions(inst), [("Bforartists_Coworker", "1.1.37")])
        _i, repo = inst.find_repo()
        self.assertTrue(repo.use_sync_on_startup)
        self.assertIn(("sync", "Bforartists_Coworker"), self.fake.calls)
        self.assertTrue(any("Installed Coworker 1.1.37" in m for m in msgs))

    def test_old_copy_from_a_file_is_replaced_and_its_settings_kept(self):
        self.fake.install_from_file("1.1.36")
        inst = self.fake.load_installer()
        inst.setup()
        self.assertEqual(self._versions(inst), [("Bforartists_Coworker", "1.1.37")])
        with open(os.path.join(self.fake.config_dir, "bfa_coworker", inst.SETTINGS_FILE)) as fh:
            self.assertEqual(json.load(fh)["settings"]["remote_api_key"], "sk-from-file")
        # Order matters: the old copy is gone before the new one is enabled.
        order = [c[0] for c in self.fake.calls if c[0] in ("uninstall", "install")]
        self.assertEqual(order, ["uninstall", "install"])

    def test_setup_is_idempotent_and_never_downgrades_an_updated_copy(self):
        inst = self.fake.load_installer()
        inst.setup()
        # Blender later updated it from the repository to 1.1.40.
        _i, repo = inst.find_repo()
        _make_ext_zip(os.path.join(self.root, "u.zip"), "1.1.40")
        with zipfile.ZipFile(os.path.join(self.root, "u.zip")) as zf:
            zf.extractall(repo.directory)
        n_installs = sum(1 for c in self.fake.calls if c[0] == "install")
        inst.setup()
        self.assertEqual(sum(1 for c in self.fake.calls if c[0] == "install"), n_installs)
        self.assertEqual(self._versions(inst), [("Bforartists_Coworker", "1.1.40")])
        self.assertEqual(sum(1 for c in self.fake.calls if c[0] == "repo_add"), 1)

    def test_reinstall_rolls_back_to_the_bundled_copy(self):
        inst = self.fake.load_installer()
        inst.setup()
        _i, repo = inst.find_repo()
        _make_ext_zip(os.path.join(self.root, "u.zip"), "1.1.40")
        with zipfile.ZipFile(os.path.join(self.root, "u.zip")) as zf:
            zf.extractall(repo.directory)
        inst.setup(reinstall=True)
        self.assertEqual(self._versions(inst), [("Bforartists_Coworker", "1.1.37")])

    def test_newer_development_copy_is_left_alone(self):
        self.fake.install_from_file("1.2.0")
        inst = self.fake.load_installer()
        inst.setup()
        self.assertEqual(self._versions(inst), [("user_default", "1.2.0")])
        self.assertIsNotNone(inst.find_repo()[1], "the repository is still added")

    def test_offline_setup_installs_without_syncing(self):
        self.fake.online = False
        inst = self.fake.load_installer()
        msgs = inst.setup()
        self.assertEqual(self._versions(inst), [("Bforartists_Coworker", "1.1.37")])
        self.assertFalse(any(c[0] == "sync" for c in self.fake.calls))
        self.assertTrue(any("Online access is off" in m for m in msgs))

    def test_uninstall_everything_removes_coworker_repo_and_installer(self):
        inst = self.fake.load_installer()
        inst.setup()
        # The installer itself lives in user_default.
        os.makedirs(os.path.join(self.fake.repos[0].directory, "bfa_coworker_installer"))
        inst._uninstall_everything()  # pylint: disable=protected-access
        self.assertEqual(inst.installed_copies(), [])
        self.assertEqual(inst.find_repo(), (-1, None))
        self.assertIn(("uninstall", "user_default", "bfa_coworker_installer"), self.fake.calls)
        kinds = [c[0] for c in self.fake.calls]
        self.assertLess(kinds.index("repo_remove"), len(kinds) - 1, "installer is removed last")


# ---------------------------------------------------------------------------
# End to end with Blender's own extension client


def _find_blender_ext_cli() -> str:
    path = os.environ.get("BLENDER_EXT_CLI", "")
    return path if path and os.path.isfile(path) else ""


@unittest.skipUnless(_find_blender_ext_cli(), "set BLENDER_EXT_CLI to Blender's bl_pkg/cli/blender_ext.py")
class TestWithBlenderExtensionClient(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.cli = _find_blender_ext_cli()
        cls.tmp = tempfile.mkdtemp()
        cls.srv = os.path.join(cls.tmp, "srv")
        os.makedirs(cls.srv)
        class _Quiet(http.server.SimpleHTTPRequestHandler):
            def log_message(self, *_args):  # pylint: disable=arguments-differ
                pass
        handler = functools.partial(_Quiet, directory=cls.srv)
        cls.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        cls.base = "http://127.0.0.1:{:d}".format(cls.httpd.server_address[1])
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _cli(self, *args):
        res = subprocess.run([sys.executable, self.cli, *args, "--output-type", "TEXT"],
                             capture_output=True, text=True, timeout=120)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        return res.stdout

    def _publish(self, version):
        name = "bfa_coworker-{:s}.zip".format(version)
        z = _make_ext_zip(os.path.join(self.srv, name), version)
        index_path = os.path.join(self.srv, "index.json")
        idx = extension_repo.upsert(extension_repo.load_index(index_path),
                                    extension_repo.index_entry(z, self.base + "/" + name))
        extension_repo.save_index(idx, index_path)

    def _installed_version(self, repo_dir):
        return extension_repo._parse_toml(_read(os.path.join(  # pylint: disable=protected-access
            repo_dir, "bfa_coworker", "blender_manifest.toml")))["version"]

    def test_sync_install_and_upgrade(self):
        repo_dir = os.path.join(self.tmp, "repo")
        os.makedirs(repo_dir)
        url = self.base + "/index.json"
        common = ["--remote-url", url, "--local-dir", repo_dir]
        self._publish("1.1.37")
        self._cli("sync", *common)
        self._cli("install", "bfa_coworker", *common, "--local-cache", "0",
                  "--blender-version", "5.1.0", "--python-version", "3.13.0")
        self.assertEqual(self._installed_version(repo_dir), "1.1.37")
        self._publish("1.1.38")
        self._cli("sync", *common)
        self.assertIn("1.1.38", self._cli("list", *common))
        self._cli("install", "bfa_coworker", *common, "--local-cache", "0",
                  "--blender-version", "5.1.0", "--python-version", "3.13.0")
        self.assertEqual(self._installed_version(repo_dir), "1.1.38")

    def test_blender_validates_the_installer_and_coworker_manifests(self):
        # Blender rejects e.g. permission texts over 64 characters at install time.
        z = _make_ext_zip(os.path.join(self.tmp, "bfa_coworker-1.1.37.zip"), "1.1.37")
        inst_zip = extension_repo.build_installer(z, self.tmp)
        self._cli("validate", inst_zip)
        self._cli("validate", os.path.join(_REPO, "addon", "bfa_coworker"))

    def test_installer_zip_and_its_payload_install(self):
        z = _make_ext_zip(os.path.join(self.tmp, "bfa_coworker-1.1.37.zip"), "1.1.37")
        inst_zip = extension_repo.build_installer(z, self.tmp)
        user_default = os.path.join(self.tmp, "user_default")
        os.makedirs(user_default)
        self._cli("install-files", inst_zip, "--local-dir", user_default,
                  "--blender-version", "5.1.0", "--python-version", "3.13.0")
        payload = os.path.join(user_default, "bfa_coworker_installer", "payload", "bfa_coworker-1.1.37.zip")
        self.assertTrue(os.path.isfile(payload))
        repo_dir = os.path.join(self.tmp, "remote_repo")
        os.makedirs(repo_dir)
        self._cli("install-files", payload, "--local-dir", repo_dir,
                  "--blender-version", "5.1.0", "--python-version", "3.13.0")
        self.assertEqual(self._installed_version(repo_dir), "1.1.37")


if __name__ == "__main__":
    unittest.main()
