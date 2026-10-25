# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for getting llama-server on Linux and macOS, and for custom paths (#382).

1. Asset names: every computer asks for an asset llama.cpp really publishes
   (the Linux CPU build has no "cpu" in its name; arm64 on aarch64).
2. Unpacking: the binary's whole folder comes out, flattened, with the real
   versioned libraries and the symlinks to them; links that leave the folder
   and files from other folders stay behind.
3. Where a download is looked for: one folder per variant on Linux / macOS,
   the old file names on Windows.
4. Custom path: the file, its folder, ``build/bin``, ``~``; a clear reason
   when it's missing, has no llama-server, or isn't executable.

Run with::

    python -m unittest tests.test_llama_server_linux -v
"""

__all__ = ()

import io
import os
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.test_llm_manager import load_llm_manager_module

llm = load_llm_manager_module()

# Release assets of llama.cpp b10154 (the pinned _LLAMA_SERVER_VERSION), from the GitHub API.
_B10154_ASSETS = {
    "llama-b10154-bin-macos-arm64.tar.gz",
    "llama-b10154-bin-macos-x64.tar.gz",
    "llama-b10154-bin-ubuntu-arm64.tar.gz",
    "llama-b10154-bin-ubuntu-vulkan-arm64.tar.gz",
    "llama-b10154-bin-ubuntu-vulkan-x64.tar.gz",
    "llama-b10154-bin-ubuntu-x64.tar.gz",
    "llama-b10154-bin-win-cpu-x64.zip",
    "llama-b10154-bin-win-cuda-12.4-x64.zip",
    "llama-b10154-bin-win-vulkan-x64.zip",
}


def _asset(system, machine, backend):
    parts = llm.llama_asset_parts(system, machine, backend)
    return llm.llama_asset_url("b10154", *parts).rsplit("/", 1)[1]


class TestAssetNames(unittest.TestCase):

    def test_linux_cpu_has_no_variant(self):
        self.assertEqual(_asset("linux", "x86_64", "cpu"), "llama-b10154-bin-ubuntu-x64.tar.gz")

    def test_linux_vulkan_and_arm(self):
        self.assertEqual(_asset("linux", "x86_64", "vulkan"), "llama-b10154-bin-ubuntu-vulkan-x64.tar.gz")
        self.assertEqual(_asset("linux", "aarch64", "cpu"), "llama-b10154-bin-ubuntu-arm64.tar.gz")

    def test_linux_cuda_falls_back_to_cpu(self):
        # llama.cpp publishes no CUDA build for Linux.
        self.assertEqual(_asset("linux", "x86_64", "cuda"), "llama-b10154-bin-ubuntu-x64.tar.gz")

    def test_every_computer_asks_for_a_real_asset(self):
        for system, machine in (("linux", "x86_64"), ("linux", "aarch64"), ("darwin", "arm64"),
                                ("darwin", "x86_64"), ("win32", "AMD64")):
            for backend in ("cpu", "cuda", "vulkan"):
                with self.subTest(system=system, machine=machine, backend=backend):
                    self.assertIn(_asset(system, machine, backend), _B10154_ASSETS)

    def test_url(self):
        self.assertEqual(llm.llama_asset_url("b1", "ubuntu", "", "x64", ".tar.gz"),
                         "https://github.com/ggml-org/llama.cpp/releases/download/b1/llama-b1-bin-ubuntu-x64.tar.gz")


def _tar(entries):
    """entries: (name, bytes) for files, (name, "->target") for symlinks, (name, None) for dirs."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in entries:
            info = tarfile.TarInfo(name)
            if data is None:
                info.type = tarfile.DIRTYPE
                tf.addfile(info)
            elif isinstance(data, str):
                info.type, info.linkname = tarfile.SYMTYPE, data[2:]
                tf.addfile(info)
            else:
                info.size, info.mode = len(data), 0o755
                tf.addfile(info, io.BytesIO(data))
    buf.seek(0)
    return tarfile.open(fileobj=buf, mode="r:gz")


class TestUnpack(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dest = Path(self._tmp.name) / "llama-cpu"

    def tearDown(self):
        self._tmp.cleanup()

    def _archive(self):
        return _tar([
            ("llama-b10154", None),
            ("llama-b10154/llama-server", b"launcher"),
            ("llama-b10154/libllama-server-impl.so", b"server"),
            ("llama-b10154/libllama.so.0.0.10154", b"llama"),
            ("llama-b10154/libllama.so.0", "->libllama.so.0.0.10154"),
            ("llama-b10154/libllama.so", "->libllama.so.0"),
            ("llama-b10154/LICENSE", b"mit"),
            ("llama-b10154/escape", "->../../outside"),
            ("other/libstray.so", b"stray"),
        ])

    def test_whole_folder_flattened(self):
        binary = llm.extract_llama_folder(self._archive(), "llama-server", self.dest)
        self.assertEqual(binary, self.dest / "llama-server")
        self.assertEqual(binary.read_bytes(), b"launcher")
        self.assertEqual((self.dest / "libllama-server-impl.so").read_bytes(), b"server")
        self.assertEqual((self.dest / "libllama.so.0.0.10154").read_bytes(), b"llama")     # the real file
        self.assertEqual((self.dest / "libllama.so.0").read_bytes(), b"llama")             # link (or copy)
        self.assertEqual((self.dest / "libllama.so").read_bytes(), b"llama")
        self.assertTrue((self.dest / "LICENSE").is_file())

    def test_nothing_from_outside_the_folder(self):
        llm.extract_llama_folder(self._archive(), "llama-server", self.dest)
        self.assertFalse(os.path.lexists(self.dest / "escape"))
        self.assertFalse((self.dest / "libstray.so").exists())
        self.assertFalse((self.dest / "llama-b10154").exists())

    def test_replaces_an_old_unpack(self):
        self.dest.mkdir(parents=True)
        (self.dest / "libold.so").write_bytes(b"old")
        llm.extract_llama_folder(self._archive(), "llama-server", self.dest)
        self.assertFalse((self.dest / "libold.so").exists())

    def test_no_binary(self):
        self.assertIsNone(llm.extract_llama_folder(_tar([("x/readme", b"")]), "llama-server", self.dest))
        self.assertFalse(self.dest.exists())


class TestBundledCandidates(unittest.TestCase):

    def test_linux_folder_per_variant(self):
        base = Path("/c")
        with mock.patch.object(llm.sys, "platform", "linux"):
            self.assertEqual(llm._bundled_llama_candidates(base, "vulkan"),
                             [base / "llama-vulkan" / "llama-server", base / "llama-cpu" / "llama-server"])
            self.assertEqual(llm._bundled_llama_candidates(base, "cuda"), [base / "llama-cpu" / "llama-server"])

    def test_windows_names_unchanged(self):
        base = Path("C:/c")
        with mock.patch.object(llm.sys, "platform", "win32"):
            self.assertEqual(llm._bundled_llama_candidates(base, "cuda"),
                             [base / "llama-server-cuda.exe", base / "llama-server.exe"])


class TestCustomPath(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.name = "llama-server.exe" if sys.platform == "win32" else "llama-server"

    def tearDown(self):
        self._tmp.cleanup()

    def _binary(self, folder):
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / self.name
        path.write_bytes(b"")
        path.chmod(0o755)
        return str(path)

    def test_the_file_itself(self):
        path = self._binary(self.root)
        self.assertEqual(llm.resolve_llama_path(path), (path, ""))
        self.assertEqual(llm.resolve_llama_path("  " + path + "  "), (path, ""))

    def test_its_folder_or_build_bin(self):
        path = self._binary(self.root)
        self.assertEqual(llm.resolve_llama_path(str(self.root))[0], path)
        checkout = self.root / "llama.cpp"
        built = self._binary(checkout / "build" / "bin")
        self.assertEqual(os.path.normpath(llm.resolve_llama_path(str(checkout))[0]), os.path.normpath(built))

    def test_home_is_expanded(self):
        path = self._binary(self.root / "tools")
        with mock.patch.dict(os.environ, {"HOME": str(self.root), "USERPROFILE": str(self.root)}):
            self.assertEqual(os.path.normpath(llm.resolve_llama_path("~/tools")[0]), os.path.normpath(path))

    def test_says_why(self):
        self.assertEqual(llm.resolve_llama_path(""), (None, "no path set"))
        missing = str(self.root / "nope" / self.name)
        self.assertEqual(llm.resolve_llama_path(missing), (None, "file not found: " + missing))
        binary, problem = llm.resolve_llama_path(str(self.root))
        self.assertIsNone(binary)
        self.assertTrue(problem.startswith("no llama-server"))

    @unittest.skipIf(sys.platform == "win32", "no executable bit on Windows")
    def test_not_executable(self):
        path = self._binary(self.root)
        os.chmod(path, 0o644)
        binary, problem = llm.resolve_llama_path(path)
        self.assertIsNone(binary)
        self.assertIn("chmod +x", problem)

    def test_find_uses_it_and_keeps_the_reason(self):
        cfg = llm.get_config()
        saved = (cfg.llama_source, cfg.llama_path)
        try:
            cfg.llama_source, cfg.llama_path = "custom", str(self.root)
            llm.set_config(cfg)
            llm.invalidate_llama_server_cache()
            self.assertIsNone(llm.find_llama_server())
            self.assertTrue(llm.llama_custom_problem().startswith("no llama-server"))
            path = self._binary(self.root)
            llm.invalidate_llama_server_cache()
            with mock.patch.object(llm, "_check_llama_version", lambda _p: None):
                self.assertEqual(llm.find_llama_server(), path)
            self.assertEqual(llm.llama_custom_problem(), "")
        finally:
            cfg.llama_source, cfg.llama_path = saved
            llm.set_config(cfg)
            llm.invalidate_llama_server_cache()


if __name__ == "__main__":
    unittest.main()
