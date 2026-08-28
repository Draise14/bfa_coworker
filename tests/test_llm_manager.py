# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Unit tests for local LLM manager helper functions."""

import importlib.util
import io
import struct
import tempfile
import unittest
import zipfile
from pathlib import Path


def load_llm_manager_module() -> object:
    repo_root = Path(__file__).resolve().parents[1]
    module_path = repo_root / "addon" / "bfa_coworker" / "llm_manager.py"
    spec = importlib.util.spec_from_file_location("llm_manager", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# Helpers for building synthetic GGUF files
# ---------------------------------------------------------------------------

# GGUF value type IDs (from llama.cpp gguf.h).
_GGUF_TYPE_UINT8 = 0
_GGUF_TYPE_INT8 = 1
_GGUF_TYPE_UINT16 = 2
_GGUF_TYPE_INT16 = 3
_GGUF_TYPE_UINT32 = 4
_GGUF_TYPE_UINT64 = 5
_GGUF_TYPE_FLOAT32 = 6
_GGUF_TYPE_FLOAT64 = 7
_GGUF_TYPE_BOOL = 8
_GGUF_TYPE_STRING = 9
_GGUF_TYPE_ARRAY = 10


def _gguf_write_kv_uint32(key: str, value: int) -> bytes:
    """Write a single GGUF key-value pair with a UINT32 value."""
    key_bytes = key.encode("utf-8")
    buf = struct.pack("<Q", len(key_bytes))  # key length (uint64)
    buf += key_bytes
    buf += struct.pack("<I", _GGUF_TYPE_UINT32)  # value type
    buf += struct.pack("<I", value)  # uint32 value
    return buf


def _gguf_write_kv_uint64(key: str, value: int) -> bytes:
    """Write a single GGUF key-value pair with a UINT64 value."""
    key_bytes = key.encode("utf-8")
    buf = struct.pack("<Q", len(key_bytes))
    buf += key_bytes
    buf += struct.pack("<I", _GGUF_TYPE_UINT64)
    buf += struct.pack("<Q", value)
    return buf


def _gguf_write_kv_string(key: str, value: str) -> bytes:
    """Write a single GGUF key-value pair with a STRING value."""
    key_bytes = key.encode("utf-8")
    val_bytes = value.encode("utf-8")
    buf = struct.pack("<Q", len(key_bytes))
    buf += key_bytes
    buf += struct.pack("<I", _GGUF_TYPE_STRING)
    buf += struct.pack("<Q", len(val_bytes))
    buf += val_bytes
    return buf


def _build_gguf_header(n_tensors: int, n_kv: int, kv_data: bytes) -> bytes:
    """Build a minimal GGUF v3 header with the given metadata."""
    header = b"GGUF"
    header += struct.pack("<I", 3)  # version 3
    header += struct.pack("<I", n_tensors)
    header += struct.pack("<I", n_kv)
    header += kv_data
    return header


# ---------------------------------------------------------------------------
# Tests for _gguf_layer_count
# ---------------------------------------------------------------------------

class TestGGUFLayerCount(unittest.TestCase):
    """Tests for _gguf_layer_count — reading block_count from GGUF headers."""

    def setUp(self) -> None:
        self.llm_manager = load_llm_manager_module()
        self._tmpdir = tempfile.TemporaryDirectory()
        self._tmp = Path(self._tmpdir.name)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _write_gguf(self, name: str, data: bytes) -> Path:
        path = self._tmp / name
        path.write_bytes(data)
        return path

    def test_valid_uint32_block_count(self) -> None:
        """A GGUF v3 file with UINT32 block_count=33 returns 33."""
        kv = _gguf_write_kv_uint32("llama.block_count", 33)
        header = _build_gguf_header(n_tensors=100, n_kv=1, kv_data=kv)
        path = self._write_gguf("model.gguf", header)
        self.assertEqual(self.llm_manager._gguf_layer_count(path), 33)

    def test_valid_uint64_block_count(self) -> None:
        """A GGUF v3 file with UINT64 block_count=80 returns 80."""
        kv = _gguf_write_kv_uint64("qwen3.block_count", 80)
        header = _build_gguf_header(n_tensors=200, n_kv=1, kv_data=kv)
        path = self._write_gguf("model.gguf", header)
        self.assertEqual(self.llm_manager._gguf_layer_count(path), 80)

    def test_block_count_among_multiple_kv_entries(self) -> None:
        """block_count is found when it's not the first metadata entry."""
        kv = b""
        kv += _gguf_write_kv_string("general.architecture", "qwen3")
        kv += _gguf_write_kv_string("general.name", "test-model")
        kv += _gguf_write_kv_uint32("qwen3.embedding_length", 3584)
        kv += _gguf_write_kv_uint32("qwen3.block_count", 48)
        kv += _gguf_write_kv_uint32("qwen3.attention.head_count", 32)
        header = _build_gguf_header(n_tensors=150, n_kv=5, kv_data=kv)
        path = self._write_gguf("model.gguf", header)
        self.assertEqual(self.llm_manager._gguf_layer_count(path), 48)

    def test_zero_block_count_returns_none(self) -> None:
        """block_count=0 should return None (can't use 0 for per-layer calc)."""
        kv = _gguf_write_kv_uint32("llama.block_count", 0)
        header = _build_gguf_header(n_tensors=10, n_kv=1, kv_data=kv)
        path = self._write_gguf("model.gguf", header)
        self.assertIsNone(self.llm_manager._gguf_layer_count(path))

    def test_gguf_v1_returns_none(self) -> None:
        """GGUF v1 files have no standard metadata layout — return None."""
        header = b"GGUF"
        header += struct.pack("<I", 1)  # version 1
        header += struct.pack("<I", 10)
        header += struct.pack("<I", 0)
        path = self._write_gguf("model.gguf", header)
        self.assertIsNone(self.llm_manager._gguf_layer_count(path))

    def test_non_gguf_file_returns_none(self) -> None:
        """A file that doesn't start with GGUF magic returns None."""
        path = self._write_gguf("not.gguf", b"PK\x03\x04" + b"\x00" * 100)
        self.assertIsNone(self.llm_manager._gguf_layer_count(path))

    def test_no_block_count_key_returns_none(self) -> None:
        """A valid GGUF with no block_count entry returns None."""
        kv = _gguf_write_kv_string("general.architecture", "llama")
        header = _build_gguf_header(n_tensors=10, n_kv=1, kv_data=kv)
        path = self._write_gguf("model.gguf", header)
        self.assertIsNone(self.llm_manager._gguf_layer_count(path))

    def test_missing_file_returns_none(self) -> None:
        """A non-existent file returns None (OSError caught)."""
        path = self._tmp / "does_not_exist.gguf"
        self.assertIsNone(self.llm_manager._gguf_layer_count(path))

    def test_empty_file_returns_none(self) -> None:
        """An empty file returns None."""
        path = self._write_gguf("empty.gguf", b"")
        self.assertIsNone(self.llm_manager._gguf_layer_count(path))

    def test_truncated_file_returns_none(self) -> None:
        """A file truncated mid-header returns None (struct.error caught)."""
        path = self._write_gguf("truncated.gguf", b"GGUF" + struct.pack("<I", 3))
        self.assertIsNone(self.llm_manager._gguf_layer_count(path))


# ---------------------------------------------------------------------------
# Tests for DLL companion extraction logic
# ---------------------------------------------------------------------------

class TestDllCompanionExtraction(unittest.TestCase):
    """Tests for the zip extraction logic in download_llama_server.

    We replicate the extraction logic (filter binary + companion DLL/SO files)
    in a controlled in-memory zip to verify the right files are extracted.
    """

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._tmp = Path(self._tmpdir.name)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _extract_companion_files(
        self, zip_data: bytes, dest_dir: Path, binary_name: str = "llama-server.exe"
    ) -> list[str]:
        """Replicate the download_llama_server extraction logic.

        Extracts the binary + all .dll/.so/.dylib companions from the zip.
        Returns the list of extracted file names (relative to dest_dir).
        """

        def _is_companion(name: str) -> bool:
            low = name.lower()
            if low.endswith(".dll") or low.endswith(".dylib"):
                return True
            if low.endswith(".so"):
                return True
            # Versioned .so: libcudart.so.12, libcublas.so.12.4, etc.
            if ".so." in low and low.split(".so.")[-1].isdigit():
                return True
            return False

        extracted: list[str] = []

        with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
            binary_members = [
                m for m in zf.namelist()
                if m.endswith(binary_name) or m.endswith("/" + binary_name)
            ]
            if not binary_members:
                return extracted

            members_to_extract = list(binary_members)
            for m in zf.namelist():
                if m in members_to_extract:
                    continue
                if _is_companion(m):
                    members_to_extract.append(m)

            for m in members_to_extract:
                zf.extract(m, str(dest_dir))

        # Collect what was extracted.
        for entry in sorted(dest_dir.rglob("*")):
            if entry.is_file():
                extracted.append(str(entry.relative_to(dest_dir)))
        return extracted

    def _make_zip(self, files: dict[str, bytes]) -> bytes:
        """Create an in-memory zip from a dict of {path: content}."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for name, content in files.items():
                zf.writestr(name, content)
        return buf.getvalue()

    def test_cuda_binary_with_dlls(self) -> None:
        """A CUDA zip with exe + DLLs extracts all of them."""
        zip_data = self._make_zip({
            "llama-server.exe": b"MZ fake exe",
            "cudart64_12.dll": b"cuda runtime",
            "cublas64_12.dll": b"cublas",
            "cublasLt64_12.dll": b"cublasLt",
        })
        dest = self._tmp / "extract1"
        dest.mkdir()
        result = self._extract_companion_files(zip_data, dest)

        self.assertIn("llama-server.exe", result)
        self.assertIn("cudart64_12.dll", result)
        self.assertIn("cublas64_12.dll", result)
        self.assertIn("cublasLt64_12.dll", result)
        self.assertEqual(len(result), 4)

    def test_binary_only_zip(self) -> None:
        """A CPU zip with only the exe extracts just the binary."""
        zip_data = self._make_zip({
            "llama-server.exe": b"MZ fake exe",
        })
        dest = self._tmp / "extract2"
        dest.mkdir()
        result = self._extract_companion_files(zip_data, dest)

        self.assertEqual(result, ["llama-server.exe"])

    def test_non_companion_files_ignored(self) -> None:
        """README, LICENSE, .txt files are NOT extracted as companions."""
        zip_data = self._make_zip({
            "llama-server.exe": b"MZ fake exe",
            "cudart64_12.dll": b"cuda runtime",
            "README.md": b"# llama.cpp",
            "LICENSE": b"MIT",
            "changelog.txt": b"v1.0",
        })
        dest = self._tmp / "extract3"
        dest.mkdir()
        result = self._extract_companion_files(zip_data, dest)

        self.assertIn("llama-server.exe", result)
        self.assertIn("cudart64_12.dll", result)
        self.assertNotIn("README.md", result)
        self.assertNotIn("LICENSE", result)
        self.assertNotIn("changelog.txt", result)
        self.assertEqual(len(result), 2)

    def test_subdirectory_prefix(self) -> None:
        """DLLs in a subdirectory (bin/) are extracted into dest_dir."""
        zip_data = self._make_zip({
            "bin/llama-server.exe": b"MZ fake exe",
            "bin/cudart64_12.dll": b"cuda runtime",
        })
        dest = self._tmp / "extract4"
        dest.mkdir()
        result = self._extract_companion_files(zip_data, dest)

        # Files should be extracted preserving the subdir structure.
        self.assertTrue(any("llama-server.exe" in r for r in result))
        self.assertTrue(any("cudart64_12.dll" in r for r in result))

    def test_vulkan_zip_with_companion(self) -> None:
        """A Vulkan zip may contain vulkan-1.dll alongside the binary."""
        zip_data = self._make_zip({
            "llama-server.exe": b"MZ fake exe",
            "vulkan-1.dll": b"vulkan runtime",
        })
        dest = self._tmp / "extract5"
        dest.mkdir()
        result = self._extract_companion_files(zip_data, dest)

        self.assertIn("llama-server.exe", result)
        self.assertIn("vulkan-1.dll", result)
        self.assertEqual(len(result), 2)

    def test_case_insensitive_dll_match(self) -> None:
        """DLL extensions should be matched case-insensitively."""
        zip_data = self._make_zip({
            "llama-server.exe": b"MZ fake exe",
            "CUDART64_12.DLL": b"cuda runtime",
            "MyLib.So": b"shared lib",
            "Plugin.DyLib": b"plugin",
        })
        dest = self._tmp / "extract6"
        dest.mkdir()
        result = self._extract_companion_files(zip_data, dest)

        self.assertEqual(len(result), 4)

    def test_empty_zip(self) -> None:
        """An empty zip extracts nothing and returns empty list."""
        zip_data = self._make_zip({})
        dest = self._tmp / "extract7"
        dest.mkdir()
        result = self._extract_companion_files(zip_data, dest)

        self.assertEqual(result, [])

    def test_binary_not_found_returns_empty(self) -> None:
        """A zip without the expected binary name returns empty list."""
        zip_data = self._make_zip({
            "some-other.exe": b"not llama",
            "cudart64_12.dll": b"cuda runtime",
        })
        dest = self._tmp / "extract8"
        dest.mkdir()
        result = self._extract_companion_files(zip_data, dest)

        self.assertEqual(result, [])

    def test_linux_binary_name(self) -> None:
        """On Linux, the binary is 'llama-server' (no .exe extension)."""
        zip_data = self._make_zip({
            "llama-server": b"ELF fake",
            "libggml.so": b"ggml lib",
            "libcublas.so.12": b"cublas",
        })
        dest = self._tmp / "extract9"
        dest.mkdir()
        result = self._extract_companion_files(
            zip_data, dest, binary_name="llama-server"
        )

        self.assertIn("llama-server", result)
        self.assertIn("libggml.so", result)
        self.assertIn("libcublas.so.12", result)

    def test_dll_files_are_actually_present_on_disk(self) -> None:
        """Verify extracted DLLs are real files with correct content."""
        zip_data = self._make_zip({
            "llama-server.exe": b"\x4d\x5a" + b"\x00" * 100,
            "cudart64_12.dll": b"CUDA_RUNTIME_V12",
        })
        dest = self._tmp / "extract10"
        dest.mkdir()
        self._extract_companion_files(zip_data, dest)

        cuda_dll = dest / "cudart64_12.dll"
        self.assertTrue(cuda_dll.is_file())
        self.assertEqual(cuda_dll.read_bytes(), b"CUDA_RUNTIME_V12")


class TestGGUFLayerCountIntegration(unittest.TestCase):
    """Integration test: _gguf_layer_count with a realistic GGUF-like header."""

    def setUp(self) -> None:
        self.llm_manager = load_llm_manager_module()
        self._tmpdir = tempfile.TemporaryDirectory()
        self._tmp = Path(self._tmpdir.name)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_realistic_qwen3_header(self) -> None:
        """Simulate a Qwen3.5-9B header with 48 transformer blocks."""
        kv = b""
        kv += _gguf_write_kv_string("general.architecture", "qwen3")
        kv += _gguf_write_kv_string("general.name", "Qwen3.5-9B")
        kv += _gguf_write_kv_uint32("qwen3.embedding_length", 3584)
        kv += _gguf_write_kv_uint32("qwen3.block_count", 48)
        kv += _gguf_write_kv_uint32("qwen3.attention.head_count", 32)
        kv += _gguf_write_kv_uint32("qwen3.attention.head_count_kv", 8)
        kv += _gguf_write_kv_uint32("qwen3.feed_forward_length", 19200)
        kv += _gguf_write_kv_uint32("qwen3.context_length", 131072)
        header = _build_gguf_header(n_tensors=300, n_kv=8, kv_data=kv)

        path = self._tmp / "qwen3.5-9b.gguf"
        path.write_bytes(header)
        result = self.llm_manager._gguf_layer_count(path)
        self.assertEqual(result, 48)

    def test_realistic_gemma3_header(self) -> None:
        """Simulate a Gemma-4-26B header with 46 transformer blocks."""
        kv = b""
        kv += _gguf_write_kv_string("general.architecture", "gemma3")
        kv += _gguf_write_kv_string("general.name", "gemma-4-26B")
        kv += _gguf_write_kv_uint32("gemma3.block_count", 46)
        kv += _gguf_write_kv_uint32("gemma3.embedding_length", 4608)
        header = _build_gguf_header(n_tensors=400, n_kv=4, kv_data=kv)

        path = self._tmp / "gemma-4-26b.gguf"
        path.write_bytes(header)
        result = self.llm_manager._gguf_layer_count(path)
        self.assertEqual(result, 46)


if __name__ == "__main__":
    unittest.main()
