# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""The Local presets' memory texts and the GPU layer choice (#558).

1. ``_gguf_layer_count`` reads ``<arch>.block_count`` through ``gguf_info``
   (it read the GGUF v3 counts as 4 bytes and used wrong type ids, so it
   returned ``None`` for every real file).
2. ``plan_gpu_layers`` / ``autodetect_gpu_layers``: the partial offload uses
   the memory plan's numbers -- free VRAM minus the margin, the file's own
   layers, the header's KV -- and ``start_local_llama`` passes the plan's.
3. The preset texts are generated from header numbers in the preset table:
   the VRAM line, the card note, the download size, the tooltips in
   ``shared.MODEL_PRESET_ITEMS``; the two measured presets stay calibrated.
4. ``_misc/read_preset_headers.py`` reads the same keys as the add-on.

Run with::

    python -m unittest tests.test_preset_memory_558 -v
"""

__all__ = ()

import ast
import importlib.util
import os
import re
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from test_local_server_543 import HYBRID_META, TENSORS, _FakeProc, write_gguf

ROOT = Path(__file__).resolve().parents[1]
ADDON = ROOT / "addon" / "bfa_coworker"
# The two models measured for #543 (read only; the tests that need them skip without).
REAL_MODELS = Path("Z:/AI/LLM Models/Blender MCP")
REAL_27B = REAL_MODELS / "Qwen3.8-27B-UD-Q4_K_M.gguf"
REAL_9B = REAL_MODELS / "Qwen3.5-9B-DeepSeek-V4-Flash-Q4_K_M.gguf"
MIB = 1024 * 1024


def _load(name: str, path: Path) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _llm() -> types.ModuleType:
    return _load("llm_manager_558", ADDON / "llm_manager.py")


# The card notes as reviewed for #558 (from the headers read on 2026-11-16).
# A change here is a change of what users are told: check it against the PR.
EXPECTED_CARD_NOTES = {
    "qwen38_27b_q8": "32 GB+ card with 8-bit KV, 48 GB+ with 16-bit -- ~29.5-30.5 GB VRAM at 32K",
    "fable_fusion_27b_q6": "32 GB+ card -- ~24.9-25.8 GB VRAM at 32K",
    "nail_35b_q4": "32 GB+ card -- ~22.5-22.8 GB VRAM at 32K",
    "gpt_oss_20b_q4": "16 GB+ card -- at most ~11.8-12.5 GB VRAM at 32K",
    "qwen38_27b_q4": "20 GB+ card with 8-bit KV, 24 GB+ with 16-bit -- ~17.8-18.8 GB VRAM at 32K",
    "fable_fusion_27b_iq4": "24 GB+ card -- ~18.2-19.1 GB VRAM at 32K",
    "gemma4_e4b_q4": "12 GB+ card with 8-bit KV, 16 GB+ with 16-bit -- at most ~8.8-11.2 GB VRAM at 32K",
    "qwen35_9b_dsv4_q4": "10 GB+ card -- ~7.1-7.6 GB VRAM at 32K",
    "qwen35_9b_q8": "16 GB+ card -- ~10.7-11.2 GB VRAM at 32K",
}
# The Recommended Model list's ids, in the order saved preferences index them.
ENUM_ORDER = ["_custom", "gpt_oss_20b_q4", "qwen38_27b_q4", "fable_fusion_27b_iq4", "qwen38_27b_q8",
              "fable_fusion_27b_q6", "nail_35b_q4", "gemma4_e4b_q4", "qwen35_9b_dsv4_q4", "qwen35_9b_q8"]


# ---------------------------------------------------------------------------
# 1. Layer count

class TestLayerCount(unittest.TestCase):

    def setUp(self) -> None:
        self.llm = _llm()
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))

    def test_synthetic_v3_header(self) -> None:
        path = self.tmp / "hybrid.gguf"
        write_gguf(path, HYBRID_META, TENSORS)
        self.assertEqual(self.llm._gguf_layer_count(path), 9)        # MTP layer included
        self.assertEqual(self.llm._gguf_layer_count(str(path)), 9)

    def test_unreadable_files(self) -> None:
        self.assertIsNone(self.llm._gguf_layer_count(self.tmp / "missing.gguf"))
        junk = self.tmp / "junk.gguf"
        junk.write_bytes(b"NOPE" * 64)
        self.assertIsNone(self.llm._gguf_layer_count(junk))
        no_arch = self.tmp / "noarch.gguf"
        write_gguf(no_arch, [("qwen35.block_count", 4, 9)], TENSORS)
        self.assertIsNone(self.llm._gguf_layer_count(no_arch))

    @unittest.skipUnless(REAL_27B.is_file() and REAL_9B.is_file(), "the two measured models are not on this machine")
    def test_the_two_real_models(self) -> None:
        self.assertEqual(self.llm._gguf_layer_count(REAL_27B), 65)
        self.assertEqual(self.llm._gguf_layer_count(REAL_9B), 32)


# ---------------------------------------------------------------------------
# 2. GPU layers from the plan's numbers

class TestGpuLayers(unittest.TestCase):

    def setUp(self) -> None:
        self.llm = _llm()
        self.p27 = self.llm.preset_profile(self.llm.get_preset_by_id("qwen38_27b_q4"))
        self.model27, self.proj27 = 16464440224.0, 927607488.0

    def _layers(self, free: "int | None", ctx: int = 32768, kv_q8: bool = False, slots: int = 1) -> int:
        return self.llm.plan_gpu_layers(self.model27, self.proj27, self.p27, 65, free, ctx, kv_q8, slots)

    def test_every_layer_when_it_fits_or_free_is_unknown(self) -> None:
        self.assertEqual(self._layers(None), 99)
        need = self.llm.estimate_server_vram_mib(self.model27, self.proj27, self.p27, 32768, False)
        self.assertEqual(self._layers(need + self.llm._VRAM_MARGIN_MIB), 99)
        self.assertLess(self._layers(need + self.llm._VRAM_MARGIN_MIB - 1), 65)

    def test_partial_offload_fills_free_minus_the_margin(self) -> None:
        llm = self.llm
        for free in (11264, 15360, 17000, 19000):          # 12 and 16 GB cards less 1 GB, and tight 24 GB ones
            for kv_q8 in (False, True):
                ngl = self._layers(free, kv_q8=kv_q8)
                full = llm.estimate_server_vram_mib(self.model27, self.proj27, self.p27, 32768, kv_q8)
                fixed = (self.proj27 + llm._PROJECTOR_COMPUTE_MIB * MIB + 32768 * llm._KV_SCRATCH_BYTES_PER_TOKEN) / MIB
                per_layer = (full - fixed) / 65
                avail = free - llm._VRAM_MARGIN_MIB
                self.assertLessEqual(fixed + ngl * per_layer, avail, (free, kv_q8))
                self.assertGreater(fixed + (ngl + 1) * per_layer, avail, (free, kv_q8))
                self.assertLess(ngl, 65)
        self.assertGreater(self._layers(15360, kv_q8=True), self._layers(15360))   # 8-bit KV: more layers
        self.assertEqual(self._layers(1000), 0)

    def test_more_layers_than_the_old_33_layer_guess(self) -> None:
        # main: a 16 GB card, 32K -> 28 layers (of a guessed 33; the model has 65).
        self.assertGreater(self._layers(16384 - 1024), 28)

    def _model(self) -> Path:
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        path = tmp / "hybrid.gguf"
        write_gguf(path, HYBRID_META, TENSORS)
        return path

    def test_autodetect_uses_the_files_header(self) -> None:
        llm = self.llm
        path = self._model()
        info = llm.gguf_info(path)
        prof = llm.model_memory_profile(info)
        calls = []
        real = llm.plan_gpu_layers

        def spy(*args):
            calls.append(args)
            return real(*args)

        with mock.patch.object(llm, "_detect_gpu_backend", lambda: "cuda"), \
                mock.patch.object(llm, "plan_gpu_layers", spy):
            self.assertEqual(llm.autodetect_gpu_layers(path, 32768, free_mib=40000), 99)
            model_bytes, proj, profile, layers, free, ctx, kv_q8, slots = calls[-1]
            self.assertEqual((model_bytes, layers, free, ctx), (info["file_bytes"], 9, 40000, 32768))
            self.assertEqual(profile, prof)
            llm.autodetect_gpu_layers(path, 16384, free_mib=5000, projector_bytes=123.0, kv_q8=True, slots=3)
            self.assertEqual(calls[-1][1:], (123.0, prof, 9, 5000, 16384, True, 3))
        with mock.patch.object(llm, "_detect_gpu_backend", lambda: "cpu"):
            self.assertEqual(llm.autodetect_gpu_layers(path, 32768, free_mib=40000), 0)

    def test_autodetect_reads_free_then_total(self) -> None:
        llm = self.llm
        path = self._model()
        seen = []
        with mock.patch.object(llm, "_detect_gpu_backend", lambda: "cuda"), \
                mock.patch.object(llm, "plan_gpu_layers", lambda *a: seen.append(a[4]) or 7):
            with mock.patch.object(llm, "detect_free_vram_mib", lambda: 9000):
                llm.autodetect_gpu_layers(path, 32768)
            with mock.patch.object(llm, "detect_free_vram_mib", lambda: None), \
                    mock.patch.object(llm, "_hardware_info", lambda: (None, "GPU", 12288)):
                llm.autodetect_gpu_layers(path, 32768)
            with mock.patch.object(llm, "detect_free_vram_mib", lambda: None), \
                    mock.patch.object(llm, "_hardware_info", lambda: (None, None, None)):
                self.assertEqual(llm.autodetect_gpu_layers(path, 32768), 99)
        self.assertEqual(seen, [9000, 12288 - llm._DESKTOP_VRAM_MIB])

    def test_no_second_sizing_rule_left(self) -> None:
        src = (ADDON / "llm_manager.py").read_text(encoding="utf-8")
        self.assertNotIn("_KV_MB_PER_1K_CTX", src)
        self.assertNotIn("_RUNTIME_OVERHEAD_MB", src)


class TestStartPassesThePlan(unittest.TestCase):
    """``start_local_llama`` sizes the layers with the numbers of its own plan."""

    def setUp(self) -> None:
        self.llm = llm = _llm()
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.models = self.tmp / "models"
        self.models.mkdir()
        self.bundled = self.tmp / "bundled"
        self.bundled.mkdir()
        write_gguf(self.models / "tiny.gguf", HYBRID_META, TENSORS)
        self.free = 30000
        self.calls = []
        patches = [
            mock.patch.object(llm, "find_llama_server", lambda: str(self.bundled / "llama-server.exe")),
            mock.patch.object(llm, "_llama_server_version", lambda _e: "version: 10154 (test)"),
            mock.patch.object(llm, "_server_supported_flags", lambda _e: None),
            mock.patch.object(llm, "_get_bundled_llama_dir", lambda: self.bundled),
            mock.patch.object(llm, "_detect_gpu_backend", lambda: "cuda"),
            mock.patch.object(llm, "plan_gpu_layers", lambda *a: self.calls.append(a) or 5),
            mock.patch.object(llm, "_find_free_port", lambda p: p),
            mock.patch.object(llm, "_show_llama_console", lambda: False),
            mock.patch.object(llm, "detect_free_vram_mib", lambda: self.free),
            mock.patch.object(llm, "_resolve_mmproj_path", lambda _p: None),
            mock.patch.object(llm._process_guard, "bind_to_parent", lambda *a, **k: None),
            mock.patch.object(llm._process_guard, "forget", lambda *a, **k: None),
            mock.patch.object(llm._process_guard, "kill_own", lambda *a, **k: 0),
            mock.patch.object(llm._process_guard, "reap_orphans", lambda *a, **k: 0),
            mock.patch.object(llm.subprocess, "Popen", _FakeProc),
        ]
        if hasattr(os, "add_dll_directory"):
            patches.append(mock.patch.object(os, "add_dll_directory", lambda _d: None))
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        llm.set_config(llm.LLMConfig(mode="local", model_repo_id="", model_filename="tiny.gguf",
                                     downloaded_models_dir=str(self.models), local_ctx_size=32768,
                                     llama_backend="cuda", local_parallel=2))
        self.addCleanup(lambda: setattr(self.llm, "_llama_process", None))

    def test_layers_use_the_plans_free_memory_kv_and_slots(self) -> None:
        proc = self.llm.start_local_llama()
        self.assertIsNotNone(proc, self.llm.get_state().error)
        plan = self.llm.get_last_memory_plan()
        _model, _proj, _prof, layers, free, ctx, kv_q8, slots = self.calls[-1]
        self.assertEqual((layers, free, ctx, kv_q8, slots), (9, self.free, 32768, plan.kv_q8, 2))
        self.assertEqual(proc.args[proc.args.index("--n-gpu-layers") + 1], "5")


# ---------------------------------------------------------------------------
# 3. Preset texts

class TestPresetTexts(unittest.TestCase):

    def setUp(self) -> None:
        self.llm = _llm()

    def test_every_preset_has_header_numbers(self) -> None:
        for p in self.llm.PRESET_MODELS:
            self.assertGreater(p.file_bytes, 1 << 30, p.identifier)
            self.assertGreater(p.kv_bytes_per_token, 0, p.identifier)
            self.assertGreater(p.block_count, 0, p.identifier)
            self.assertEqual(bool(p.projector_bytes), bool(p.mmproj_filename), p.identifier)

    def test_memory_texts_cannot_be_written_by_hand(self) -> None:
        fields = set(self.llm.ModelPreset.__dataclass_fields__)
        for name in ("ram_gb", "disk_gb", "hardware_note", "vram_note"):
            self.assertNotIn(name, fields)
        for p in self.llm.PRESET_MODELS:
            for text in (p.description, p.why):
                self.assertIsNone(re.search(r"\d\s*GB", text), (p.identifier, text))

    def test_card_notes(self) -> None:
        self.assertEqual({p.identifier: p.hardware_note for p in self.llm.PRESET_MODELS}, EXPECTED_CARD_NOTES)

    def test_texts_follow_the_estimate(self) -> None:
        llm = self.llm
        reserve = llm._VRAM_MARGIN_MIB + llm._DESKTOP_VRAM_MIB
        for p in llm.PRESET_MODELS:
            vram, card = llm.preset_memory_note(p)
            self.assertEqual((p.vram_note, p.hardware_note), (vram, card))
            q8, f16 = llm.preset_vram_mib(p, True), llm.preset_vram_mib(p, False)
            self.assertLess(q8, f16)
            profile = llm.preset_profile(p)
            self.assertEqual(f16, llm.estimate_server_vram_mib(p.file_bytes, p.projector_bytes, profile, 32768, False))
            # Rounded up to 0.1 GB: never below the estimate, at most 0.1 GB over.
            shown = [float(x) for x in re.findall(r"~(\d+\.\d)", vram)]
            self.assertEqual(len(shown), 2, vram)
            for gb, mib in zip(shown, (q8, f16)):
                self.assertGreaterEqual(gb * 1024, mib - 0.01, (p.identifier, gb, mib))
                self.assertLess(gb * 1024 - mib, 102.5, (p.identifier, gb, mib))
            self.assertIn("32K context", vram)
            self.assertEqual("vision projector included" in vram, bool(p.projector_bytes))
            self.assertEqual(vram.count("at most"), 1 if p.sliding_window else 0)
            # A named card holds the estimate plus the reserve; the size below it doesn't.
            cards = [int(c) for c in re.findall(r"(\d+) GB\+", card)]
            for gb, mib in zip(cards, (q8, f16) if len(cards) == 2 else (q8,)):
                self.assertLessEqual(mib + reserve, gb * 1024, (p.identifier, gb))
                smaller = [c for c in llm._PRESET_CARD_GB if c < gb]
                if smaller:
                    self.assertGreater(mib + reserve, smaller[-1] * 1024, (p.identifier, gb))
            if len(cards) == 1:
                self.assertLessEqual(f16 + reserve, cards[0] * 1024, p.identifier)

    def test_the_measured_presets_stay_calibrated(self) -> None:
        # #543, RTX 4090, one slot, with the projector, 32K: the estimate is at
        # or above the measurement and within 400 MiB of it.
        llm = self.llm
        measured = [("qwen38_27b_q4", False, 18896), ("qwen38_27b_q4", True, 18022),
                    ("qwen35_9b_dsv4_q4", False, 7530)]
        for ident, kv_q8, mib in measured:
            est = llm.preset_vram_mib(llm.get_preset_by_id(ident), kv_q8)
            self.assertGreaterEqual(est, mib, ident)
            self.assertLess(est - mib, 400, ident)

    def test_download_sizes(self) -> None:
        llm = self.llm
        for p in llm.PRESET_MODELS:
            gb = float(p.disk_gb.strip("~ GB"))
            total = (p.file_bytes + p.projector_bytes) / 1024 ** 3
            self.assertGreaterEqual(gb, total - 1e-9, p.identifier)
            self.assertLess(gb - total, 0.1, p.identifier)
        # The IQ4_XS is not the Q4_K_M's size (it is in fact a little larger).
        self.assertNotEqual(llm.get_preset_by_id("fable_fusion_27b_iq4").disk_gb,
                            llm.get_preset_by_id("qwen38_27b_q4").disk_gb)
        self.assertEqual(llm._preset_disk_hint("unsloth/Qwen3.8-27B-GGUF", "Qwen3.8-27B-UD-Q4_K_M.gguf"),
                         llm.get_preset_by_id("qwen38_27b_q4").disk_gb)

    def test_info_text(self) -> None:
        llm = self.llm
        p = llm.get_preset_by_id("qwen38_27b_q4")
        text = llm.preset_info_text(p)
        self.assertIn(p.vram_note, text)
        self.assertIn(p.hardware_note, text)
        self.assertIn(p.disk_gb, text)
        self.assertNotIn("RAM:", text)

    def test_recommendation_without_the_file_uses_the_preset(self) -> None:
        llm = self.llm
        p = llm.get_preset_by_id("qwen38_27b_q4")
        with mock.patch.object(llm, "resolve_gpu_backend", lambda b: "cuda"):
            plan = llm.recommend_server_plan(p.file_bytes / 1024 ** 3, "cuda", 64.0, 24.0, free_vram_mib=21612,
                                             projector_bytes=p.projector_bytes, profile=llm.preset_profile(p))
            self.assertEqual((plan.ctx, plan.kv_q8, plan.fits), (32768, False, True))
            self.assertEqual(plan.need_mib, llm.preset_vram_mib(p, False))
            # The fallback profile (what it used before) overstates the same model.
            fallback = llm.recommend_server_plan(p.file_bytes / 1024 ** 3, "cuda", 64.0, 24.0, free_vram_mib=40000,
                                                 projector_bytes=p.projector_bytes, ctx=32768)
            self.assertGreater(fallback.need_mib, plan.need_mib + 1500)

    @unittest.skipUnless(REAL_27B.is_file() and REAL_9B.is_file(), "the two measured models are not on this machine")
    def test_preset_numbers_are_the_real_headers(self) -> None:
        llm = self.llm
        for ident, path in (("qwen38_27b_q4", REAL_27B), ("qwen35_9b_dsv4_q4", REAL_9B)):
            p = llm.get_preset_by_id(ident)
            info = llm.gguf_info(path)
            prof = llm.model_memory_profile(info)
            self.assertEqual(p.file_bytes, info["file_bytes"])
            self.assertEqual(p.kv_bytes_per_token, prof["kv_bytes_per_token"])
            self.assertEqual(p.recurrent_bytes, prof["recurrent_bytes"])
            self.assertEqual(p.block_count, llm._gguf_layer_count(path))
            projector = REAL_MODELS / llm._local_mmproj_name(p)
            self.assertEqual(p.projector_bytes, projector.stat().st_size)


class TestSharedItems(unittest.TestCase):

    def setUp(self) -> None:
        self.llm = _llm()
        self.shared = _load("shared_558", ADDON / "shared.py")

    def test_shared_items_are_the_generated_ones(self) -> None:
        items = self.shared.MODEL_PRESET_ITEMS
        self.assertIsInstance(items, list)
        self.assertEqual(items, self.llm.preset_enum_items())
        self.assertEqual([i[0] for i in items], ENUM_ORDER)
        by_id = {p.identifier: p for p in self.llm.PRESET_MODELS}
        for ident, name, tip in items[1:]:
            p = by_id[ident]
            self.assertEqual(name, p.name)
            self.assertIn(p.hardware_note, tip)
            self.assertIn(p.disk_gb + " download", tip)
            self.assertNotRegex(tip, r"\bRAM\b")

    def test_items_are_a_constant_built_at_import(self) -> None:
        tree = ast.parse((ADDON / "shared.py").read_text(encoding="utf-8"))
        assign = next(n for n in tree.body if isinstance(n, ast.AnnAssign)
                      and getattr(n.target, "id", "") == "MODEL_PRESET_ITEMS")
        self.assertIsInstance(assign.value, ast.Call)        # built once, not a callback
        self.assertEqual(assign.value.func.attr, "preset_enum_items")

    def test_preferences_show_only_generated_texts(self) -> None:
        src = (ADDON / "preferences.py").read_text(encoding="utf-8")
        self.assertNotIn("preset.ram_gb", src)
        self.assertNotIn("disk_gb.split", src)
        self.assertIn("preset_info_text", src)
        self.assertNotRegex(src, r'"(Flagship|Mid-Range|Lightweight) \(')     # no VRAM in the group titles


# ---------------------------------------------------------------------------
# 4. The refresh script

class TestRefreshScript(unittest.TestCase):

    def test_reads_the_same_keys_as_the_addon(self) -> None:
        llm = _llm()
        script = _load("read_preset_headers_558", ROOT / "_misc" / "read_preset_headers.py")
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        path = tmp / "hybrid.gguf"
        write_gguf(path, HYBRID_META, TENSORS)
        meta, stop = script.read_model_keys(script._Source(path=str(path)), llm)
        self.assertEqual(stop, "tokenizer.ggml.tokens")
        full = llm.gguf_info(path)["meta"]
        self.assertEqual(meta, {k: v for k, v in full.items() if not k.startswith("tokenizer.")})
        self.assertEqual(llm.model_memory_profile({"meta": meta}), llm.model_memory_profile(llm.gguf_info(path)))


if __name__ == "__main__":
    unittest.main()
