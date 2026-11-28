# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""The Local server's command line, sizing and preferences (#543).

1. Every preference that shapes the command line reaches the config: the
   fields start_local_llama (and what it calls) reads are the shared list or
   derived; every Start path copies through ``apply_server_prefs``; the KV
   quant, backend and verbose preferences sync when changed.
2. The tuning flags: one slot (``--kv-unified`` only with more), the smaller
   host prompt cache, the KV flags, ``--cache-reuse`` only without a projector.
3. The sizing rule: the model's KV and recurrent needs from its GGUF header,
   free VRAM, 8-bit KV when 16-bit would not fit, never below the first request.
4. The size check: the file against its own header, in bytes, no network.
5. A start through ``start_local_llama`` with stubs: the flags on the real
   command line, the previous log kept, no folder in the models directory, and
   Stop during the load ends without a startup error.

Run with::

    python -m unittest tests.test_local_server_543 -v
"""

__all__ = ()

import ast
import importlib.util
import os
import shutil
import struct
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
ADDON = ROOT / "addon" / "bfa_coworker"


def _load(name: str, filename: str) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(name, ADDON / filename)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _llm() -> types.ModuleType:
    return _load("llm_manager_543", "llm_manager.py")


def _tree(filename: str) -> ast.Module:
    return ast.parse((ADDON / filename).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# A synthetic GGUF writer (made-up model, the real layout)

def _gguf_str(s: str) -> bytes:
    b = s.encode("utf-8")
    return struct.pack("<Q", len(b)) + b


def _gguf_kv(key: str, vtype: int, value) -> bytes:
    out = _gguf_str(key) + struct.pack("<I", vtype)
    if vtype == 4:
        return out + struct.pack("<I", value)
    if vtype == 8:
        return out + _gguf_str(value)
    if vtype == 9:      # array of uint32
        return out + struct.pack("<IQ", 4, len(value)) + struct.pack("<{:d}I".format(len(value)), *value)
    raise ValueError(vtype)


def write_gguf(path: Path, meta: list, tensors: list, cut: int = 0) -> int:
    """*tensors*: (name, dims, ggml type).  Returns the byte where the data ends."""
    sizes = {0: (1, 4), 1: (1, 2), 12: (256, 144)}
    head = b"GGUF" + struct.pack("<IQQ", 3, len(tensors), len(meta))
    head += b"".join(_gguf_kv(*kv) for kv in meta)
    # A tokenizer string array, skipped by the reader.
    head += _gguf_str("tokenizer.ggml.tokens") + struct.pack("<I", 9) + struct.pack("<IQ", 8, 3)
    head = head[:4] + struct.pack("<IQQ", 3, len(tensors), len(meta) + 1) + head[24:]
    head += b"".join(_gguf_str(t) for t in ("a", "bb", "ccc"))
    offset = 0
    infos = b""
    for name, dims, ttype in tensors:
        n = 1
        for d in dims:
            n *= d
        nbytes = n // sizes[ttype][0] * sizes[ttype][1]
        infos += _gguf_str(name) + struct.pack("<I", len(dims)) + struct.pack(
            "<{:d}Q".format(len(dims)), *dims) + struct.pack("<IQ", ttype, offset)
        offset += (nbytes + 31) // 32 * 32
        last_end = offset - (nbytes + 31) // 32 * 32 + nbytes
    head += infos
    data_start = (len(head) + 31) // 32 * 32
    total = data_start + offset
    with open(path, "wb") as fh:
        fh.write(head + b"\0" * (data_start - len(head)))
        fh.write(b"\1" * (offset - cut))
    return data_start + last_end


HYBRID_META = [
    ("general.architecture", 8, "qwen35"),
    ("qwen35.block_count", 4, 9),              # 8 + one MTP layer
    ("qwen35.nextn_predict_layers", 4, 1),
    ("qwen35.embedding_length", 4, 256),
    ("qwen35.attention.head_count", 4, 4),
    ("qwen35.attention.head_count_kv", 4, 2),
    ("qwen35.attention.key_length", 4, 64),
    ("qwen35.attention.value_length", 4, 64),
    ("qwen35.full_attention_interval", 4, 4),
    ("qwen35.ssm.conv_kernel", 4, 4),
    ("qwen35.ssm.state_size", 4, 16),
    ("qwen35.ssm.group_count", 4, 2),
    ("qwen35.ssm.inner_size", 4, 128),
]
TENSORS = [("tok", (256, 512), 12), ("w", (256, 256), 1), ("norm", (256,), 0)]


# ---------------------------------------------------------------------------
# 1. Preferences -> config

class TestPreferencesReachTheConfig(unittest.TestCase):

    def setUp(self) -> None:
        self.llm = _llm()

    def _config_reads(self) -> set:
        """``_config`` fields read by start_local_llama and the functions it calls (2 deep)."""
        tree = _tree("llm_manager.py")
        funcs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}

        def reads(fn: ast.AST) -> set:
            out = set()
            for n in ast.walk(fn):
                if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id == "_config":
                    out.add(n.attr)
                if (isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "getattr"
                        and len(n.args) >= 2 and isinstance(n.args[0], ast.Name) and n.args[0].id == "_config"
                        and isinstance(n.args[1], ast.Constant)):
                    out.add(n.args[1].value)
            return out

        def callees(fn: ast.AST) -> set:
            return {n.func.id for n in ast.walk(fn)
                    if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in funcs}

        seen, frontier, out = set(), {"start_local_llama"}, set()
        for _ in range(3):
            nxt = set()
            for name in frontier - seen:
                seen.add(name)
                out |= reads(funcs[name])
                nxt |= callees(funcs[name])
            frontier = nxt
        return out

    def test_every_field_the_server_start_reads_is_synced(self) -> None:
        # A new preference that feeds the command line must join SERVER_PREF_FIELDS
        # (or be derived), or this fails: the KV quant preference was forgotten once.
        reads = self._config_reads()
        known = set(self.llm.SERVER_PREF_FIELDS) | set(self.llm.SERVER_DERIVED_FIELDS)
        self.assertIn("local_kv_cache_quant", reads)
        self.assertIn("llama_backend", reads)
        self.assertIn("local_server_verbose", reads)
        self.assertEqual(reads - known, set(), "start_local_llama reads config fields no Start path syncs")

    def test_every_synced_field_is_a_preference_and_a_config_field(self) -> None:
        config_fields = set(self.llm.LLMConfig.__dataclass_fields__)
        prefs_src = (ADDON / "preferences.py").read_text(encoding="utf-8")
        for name in self.llm.SERVER_PREF_FIELDS + self.llm.SERVER_DERIVED_FIELDS:
            self.assertIn(name, config_fields)
        for name in self.llm.SERVER_PREF_FIELDS:
            self.assertRegex(prefs_src, r"\n    {:s}: \w+Property\(".format(name), name)

    def test_apply_server_prefs_copies_every_field(self) -> None:
        prefs = types.SimpleNamespace(
            llama_source="CUSTOM", llama_path="//bin/llama-server.exe", llama_backend="vulkan",
            model_repo_id="x/y", model_filename="m.gguf", downloaded_models_dir="D:/models",
            local_ctx_size=40960, local_kv_cache_quant=True, local_server_verbose=True, hf_token="tok",
            chats_at_once=3, local_side_by_side=False)
        cfg = self.llm.apply_server_prefs(self.llm.LLMConfig(), prefs, lambda p: "ABS:" + p)
        self.assertEqual(cfg.llama_source, "custom")
        self.assertEqual(cfg.llama_path, "ABS://bin/llama-server.exe")
        for name in ("llama_backend", "model_repo_id", "model_filename", "downloaded_models_dir",
                     "local_ctx_size", "local_kv_cache_quant", "local_server_verbose", "hf_token"):
            self.assertEqual(getattr(cfg, name), getattr(prefs, name), name)
        self.assertEqual(cfg.local_parallel, 1)
        prefs.local_side_by_side = True
        self.assertEqual(self.llm.apply_server_prefs(self.llm.LLMConfig(), prefs).local_parallel, 3)
        prefs.chats_at_once = 8
        self.assertEqual(self.llm.apply_server_prefs(self.llm.LLMConfig(), prefs).local_parallel, 4)

    def test_older_preferences_class_does_not_break_the_sync(self) -> None:
        cfg = self.llm.apply_server_prefs(self.llm.LLMConfig(), types.SimpleNamespace(local_ctx_size=8192))
        self.assertEqual(cfg.local_ctx_size, 8192)
        self.assertEqual(cfg.local_parallel, 1)

    def test_slots_follow_the_thread_runner(self) -> None:
        threads = _load("threads_543", "threads.py")
        for cap in range(1, 9):
            for sbs in (False, True):
                self.assertEqual(self.llm.local_server_slots(cap, sbs), threads.slots_for("local", cap, sbs),
                                 (cap, sbs))

    def test_config_round_trip_keeps_every_field(self) -> None:
        cfg = self.llm.LLMConfig()
        for i, name in enumerate(cfg.__dataclass_fields__):
            value = getattr(cfg, name)
            setattr(cfg, name, (not value) if isinstance(value, bool) else
                    (value + i + 1) if isinstance(value, int) else "v{:d}".format(i))
        self.llm.set_config(cfg)
        self.assertEqual(self.llm.get_config(), cfg)

    def _calls_apply(self, filename: str, func: str) -> bool:
        for node in ast.walk(_tree(filename)):
            if isinstance(node, ast.FunctionDef) and node.name == func:
                return any(isinstance(n, ast.Attribute) and n.attr == "apply_server_prefs" for n in ast.walk(node))
        self.fail("{:s} not found in {:s}".format(func, filename))

    def test_every_start_path_syncs_through_apply_server_prefs(self) -> None:
        self.assertTrue(self._calls_apply("ui_chat.py", "_sync_prefs_to_config"))
        start_llm = next(n for n in ast.walk(_tree("operators_llm.py"))
                         if isinstance(n, ast.ClassDef) and n.name == "_BFACW_OT_start_llm")
        self.assertTrue(any(isinstance(n, ast.Attribute) and n.attr == "apply_server_prefs"
                            for n in ast.walk(start_llm)))
        self.assertIn("apply_server_prefs", (ADDON / "__init__.py").read_text(encoding="utf-8"))

    def test_kv_backend_verbose_preferences_sync_when_changed(self) -> None:
        tree = _tree("preferences.py")
        updates = {}
        for node in ast.walk(tree):
            # ``name: BoolProperty(..., update=cb)``: the call is the annotation.
            if (isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
                    and isinstance(node.annotation, ast.Call)):
                for kw in node.annotation.keywords:
                    if kw.arg == "update" and isinstance(kw.value, ast.Name):
                        updates[node.target.id] = kw.value.id
        for name in ("local_kv_cache_quant", "llama_backend", "local_server_verbose"):
            self.assertIn(name, updates, name)
        # The shared callback syncs through apply_server_prefs.
        cb = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_update_server_pref")
        self.assertTrue(any(isinstance(n, ast.Attribute) and n.attr == "apply_server_prefs" for n in ast.walk(cb)))
        self.assertEqual(updates["local_kv_cache_quant"], "_update_server_pref")
        self.assertEqual(updates["local_server_verbose"], "_update_server_pref")


# ---------------------------------------------------------------------------
# 2. Tuning flags

class TestServerTuningArgs(unittest.TestCase):

    def setUp(self) -> None:
        self.llm = _llm()

    def _flag(self, args: list, flag: str) -> "str | None":
        return args[args.index(flag) + 1] if flag in args else None

    def test_one_slot_and_a_small_host_cache_by_default(self) -> None:
        args = self.llm.build_server_tuning_args(gpu=True, kv_q8=False)
        self.assertEqual(self._flag(args, "--parallel"), "1")
        self.assertNotIn("--kv-unified", args)
        self.assertEqual(self._flag(args, "--cache-ram"), str(self.llm.LOCAL_CACHE_RAM_MIB))
        self.assertLess(self.llm.LOCAL_CACHE_RAM_MIB, 8192)     # the server's default

    def test_side_by_side_slots_share_one_context(self) -> None:
        args = self.llm.build_server_tuning_args(gpu=True, kv_q8=False, slots=3)
        self.assertEqual(self._flag(args, "--parallel"), "3")
        self.assertIn("--kv-unified", args)

    def test_kv_flags_when_ticked_on_gpu_only(self) -> None:
        args = self.llm.build_server_tuning_args(gpu=True, kv_q8=True)
        self.assertEqual(self._flag(args, "--cache-type-k"), "q8_0")
        self.assertEqual(self._flag(args, "--cache-type-v"), "q8_0")
        self.assertEqual(self._flag(args, "--batch-size"), "2048")
        cpu = self.llm.build_server_tuning_args(gpu=False, kv_q8=True)
        for flag in ("--cache-type-k", "--cache-type-v", "--batch-size", "--ubatch-size"):
            self.assertNotIn(flag, cpu)
        self.assertEqual(self._flag(cpu, "--parallel"), "1")

    def test_cache_reuse_only_without_a_projector(self) -> None:
        self.assertEqual(self._flag(self.llm.build_server_tuning_args(gpu=True, kv_q8=False), "--cache-reuse"), "256")
        self.assertNotIn("--cache-reuse",
                         self.llm.build_server_tuning_args(gpu=True, kv_q8=False, projector=True))

    def test_verbose_and_preset_flags(self) -> None:
        args = self.llm.build_server_tuning_args(gpu=True, kv_q8=False, verbose=True,
                                                 preset_extra=("--no-context-shift",))
        self.assertEqual(args[:2], ["--verbose", "--no-context-shift"])
        self.assertNotIn("--verbose", self.llm.build_server_tuning_args(gpu=True, kv_q8=False))
        self.assertNotIn("--reasoning-preserve", args)

    def test_long_flag_forms_survive_the_help_filter(self) -> None:
        # The --help filter only knows --long flags: a build without --cache-ram
        # drops it with its value, and keeps the rest.
        args = ["exe"] + self.llm.build_server_tuning_args(gpu=True, kv_q8=True, projector=True)
        self.assertTrue(all(a.startswith("--") or not a.startswith("-") for a in args[1:]))
        kept, dropped = self.llm._filter_flags_against_help(
            args, {"--parallel", "--cache-type-k", "--cache-type-v", "--batch-size", "--ubatch-size"})
        self.assertEqual(dropped, ["--cache-ram"])
        self.assertNotIn("--cache-ram", kept)
        self.assertEqual(kept[-2:], ["--parallel", "1"])       # its value went with it


# ---------------------------------------------------------------------------
# 3. Sizing rule

class TestSizingRule(unittest.TestCase):

    def setUp(self) -> None:
        self.llm = _llm()
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        # The measured 27B profile (#543): 16 attention layers x 4 KV heads x (256 + 256)
        # x 2 bytes = 64 KiB per token; 48 recurrent layers, 149.6 MiB per slot.
        self.p27 = {"kv_bytes_per_token": 65536.0, "recurrent_bytes": 149.625 * 2 ** 20, "from_header": 1.0}
        self.model27 = 16464440224.0
        self.proj27 = 927607488.0

    def test_profile_from_a_hybrid_header(self) -> None:
        path = self.tmp / "hybrid.gguf"
        write_gguf(path, HYBRID_META, TENSORS)
        prof = self.llm.model_memory_profile(self.llm.gguf_info(path))
        # 8 layers (MTP layer not counted), every 4th is attention: 2 layers x 2 heads x 128 x 2 bytes.
        self.assertEqual(prof["kv_bytes_per_token"], 2 * 2 * 128 * 2)
        # 6 recurrent layers: conv 3 x (128 + 2*2*16) x 4 + state 128 x 16 x 4.
        self.assertEqual(prof["recurrent_bytes"], 6 * ((3 * (128 + 64) + 128 * 16) * 4))

    def test_profile_with_per_layer_kv_heads(self) -> None:
        meta = [("general.architecture", 8, "llama"), ("llama.block_count", 4, 4),
                ("llama.embedding_length", 4, 512), ("llama.attention.head_count", 4, 8),
                ("llama.attention.head_count_kv", 9, [2, 0, 2, 0])]
        path = self.tmp / "perlayer.gguf"
        write_gguf(path, meta, TENSORS)
        prof = self.llm.model_memory_profile(self.llm.gguf_info(path))
        self.assertEqual(prof["kv_bytes_per_token"], 2 * 2 * (64 + 64) * 2)
        self.assertEqual(prof["recurrent_bytes"], 0.0)

    def test_fallback_without_a_header(self) -> None:
        prof = self.llm.model_memory_profile(None)
        self.assertEqual(prof["kv_bytes_per_token"], self.llm._FALLBACK_KV_BYTES_PER_TOKEN)
        self.assertEqual(prof["from_header"], 0.0)

    def test_estimate_matches_the_measurements_from_above(self) -> None:
        est = self.llm.estimate_server_vram_mib
        measured = {(32768, False, 1): 18896, (32768, True, 1): 18022, (16384, False, 1): 17856,
                    (65536, True, 1): 19270, (32768, False, 4): 19346}
        for (ctx, q8, slots), mib in measured.items():
            e = est(self.model27, self.proj27, self.p27, ctx, q8, slots)
            self.assertGreaterEqual(e, mib, (ctx, q8, slots))
            self.assertLess(e - mib, 400, (ctx, q8, slots))
        self.assertGreaterEqual(est(self.model27, 0, self.p27, 32768, False), 17760)
        self.assertGreater(est(self.model27, self.proj27, self.p27, 32768, False, 4),
                           est(self.model27, self.proj27, self.p27, 32768, False, 1) + 400)

    def test_floor_holds_the_first_request(self) -> None:
        # #635: skills and tools on demand: the first request fits the smallest preset.
        self.assertGreaterEqual(self.llm.min_agent_ctx(), self.llm._FIRST_REQUEST_TOKENS + self.llm._REPLY_ROOM_TOKENS)
        self.assertEqual(self.llm.min_agent_ctx(), 16384)

    def test_recommendation_prefers_16_bit_then_8_bit(self) -> None:
        plan = self.llm.plan_server_memory
        roomy = plan(self.model27, self.proj27, self.p27, 21612)        # ~2.5 GB used elsewhere
        self.assertEqual((roomy.ctx, roomy.kv_q8, roomy.fits), (32768, False, True))
        tight = plan(self.model27, self.proj27, self.p27, 20092)        # ~4 GB used: f16 32K spilled
        self.assertEqual((tight.ctx, tight.kv_q8, tight.fits), (16384, False, True))     # #635: 16K is a size now
        full = plan(self.model27, self.proj27, self.p27, 17566)         # ~6.5 GB used
        self.assertEqual((full.ctx, full.kv_q8, full.fits), (16384, True, False))
        big = plan(self.model27, self.proj27, self.p27, 40000)          # a 48 GB card
        self.assertEqual((big.ctx, big.kv_q8), (65536, False))          # capped at 64K
        unknown = plan(self.model27, self.proj27, self.p27, None)
        self.assertEqual((unknown.ctx, unknown.fits), (16384, None))
        capped = plan(self.model27, self.proj27, self.p27, 40000, ctx_cap=16384)
        self.assertEqual(capped.ctx, 16384)                             # the model's own window

    def test_forced_q8_skips_16_bit(self) -> None:
        p = self.llm.plan_server_memory(self.model27, self.proj27, self.p27, 21612, kv_q8_forced=True)
        self.assertTrue(p.kv_q8)

    def test_launch_keeps_the_context_and_picks_the_kv_type(self) -> None:
        plan = self.llm.plan_server_memory
        self.assertFalse(plan(self.model27, self.proj27, self.p27, 21612, ctx=32768).kv_q8)
        tight = plan(self.model27, self.proj27, self.p27, 20092, ctx=32768)
        self.assertEqual((tight.ctx, tight.kv_q8, tight.fits), (32768, True, True))
        # The measured spill: f16 32K, 4 slots, ~4 GB used elsewhere -> would not fit.
        spill = plan(self.model27, self.proj27, self.p27, 20092, ctx=32768, slots=4)
        self.assertFalse(spill.need_mib <= 20092 - 1024 and not spill.kv_q8)
        self.assertIsNone(plan(self.model27, self.proj27, self.p27, None, ctx=32768).fits)

    def test_validate_warns_below_the_first_request(self) -> None:
        w = self.llm.validate_ctx_against_hardware(15.3, "cuda", 8192)
        self.assertIsNotNone(w)
        self.assertIn("first request", w)
        self.assertIn("16K", w)                       # #635: the smallest size that holds it
        # 16K holds the first request now: whatever else it says, not "the first message fails".
        w16 = self.llm.validate_ctx_against_hardware(15.3, "cuda", 16384)
        self.assertNotIn("first request", w16 or "")

    def test_hint_uses_free_vram(self) -> None:
        with mock.patch.object(self.llm, "_detect_hardware_cached", lambda: (64.0, 24.0)), \
                mock.patch.object(self.llm, "resolve_gpu_backend", lambda b: "cuda"):
            text = self.llm.hardware_context_hint(15.3, "cuda", free_vram_mib=21612, projector_bytes=self.proj27)
            self.assertIn("16K", text)           # the fallback profile (no header): 32K f16 would not fit
            self.assertIn("11K tokens", text)    # #635: the first request, now with skills and tools on demand
            self.assertIn("21.1 GB free VRAM", text)
            low = self.llm.hardware_context_hint(15.3, "cuda", free_vram_mib=12000, projector_bytes=self.proj27)
            self.assertIn("only 11.7 GB", low)


# ---------------------------------------------------------------------------
# 4. Size check

class TestSizeCheck(unittest.TestCase):

    def setUp(self) -> None:
        self.llm = _llm()
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))

    def test_complete_file_passes_without_network(self) -> None:
        path = self.tmp / "Qwen3.8-27B-UD-Q4_K_M.gguf"         # a preset's name: no HF request either
        end = write_gguf(path, HYBRID_META, TENSORS)
        info = self.llm.gguf_info(path)
        self.assertEqual(info["data_end"], end)
        with mock.patch.object(self.llm.urllib.request, "urlopen", side_effect=AssertionError("network")):
            self.assertEqual(self.llm.check_model_file_integrity(path), "")

    def test_truncated_file_is_reported_in_bytes(self) -> None:
        path = self.tmp / "cut.gguf"
        end = write_gguf(path, HYBRID_META, TENSORS, cut=1000)
        with mock.patch.object(self.llm.urllib.request, "urlopen", side_effect=AssertionError("network")):
            warning = self.llm.check_model_file_integrity(path)
        size = path.stat().st_size
        self.assertIn("truncated", warning)
        self.assertIn("{:,d} bytes on disk".format(size), warning)
        self.assertIn("{:,d}".format(end), warning)

    def test_not_gguf_or_unknown_type_is_skipped(self) -> None:
        junk = self.tmp / "junk.gguf"
        junk.write_bytes(b"NOPE" * 10)
        self.assertEqual(self.llm.check_model_file_integrity(junk), "")
        odd = self.tmp / "odd.gguf"
        write_gguf(odd, HYBRID_META, TENSORS)
        data = bytearray(odd.read_bytes())
        # Re-type the last tensor ("norm") to an unknown ggml type: no verdict, no warning.
        idx = data.rfind(b"norm")
        struct.pack_into("<I", data, idx + 4 + 4 + 8, 999)
        odd.write_bytes(bytes(data[:-100]))
        self.assertEqual(self.llm.gguf_info(odd)["data_end"], 0)
        self.assertEqual(self.llm.check_model_file_integrity(odd), "")

    def test_no_hugging_face_head_request_left(self) -> None:
        src = (ADDON / "llm_manager.py").read_text(encoding="utf-8")
        start = src.index("def check_model_file_integrity")
        body = src[start:src.index("\ndef ", start + 10)]
        self.assertNotIn("huggingface", body.lower())
        self.assertNotIn("_hf_repo_file_size", src)


# ---------------------------------------------------------------------------
# 5. A start with stubs

class _FakeProc:
    pid = 4242

    def __init__(self, args, **_kw):
        self.args = list(args)
        self.returncode = None

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = 1

    def kill(self):
        self.returncode = 1

    def wait(self, timeout=None):
        return self.returncode


class TestStartLocalLlama(unittest.TestCase):

    def setUp(self) -> None:
        self.llm = _llm()
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.models = self.tmp / "models"
        self.models.mkdir()
        self.bundled = self.tmp / "bundled"
        self.bundled.mkdir()
        write_gguf(self.models / "tiny.gguf", HYBRID_META, TENSORS)
        self.projector = None
        self.free = 30000
        llm = self.llm
        patches = [
            mock.patch.object(llm, "find_llama_server", lambda: str(self.bundled / "llama-server.exe")),
            mock.patch.object(llm, "_llama_server_version", lambda _e: "version: 10154 (test)"),
            mock.patch.object(llm, "_server_supported_flags", lambda _e: None),
            mock.patch.object(llm, "_get_bundled_llama_dir", lambda: self.bundled),
            mock.patch.object(llm, "autodetect_gpu_layers", lambda *_a, **_k: 99),
            mock.patch.object(llm, "_find_free_port", lambda p: p),
            mock.patch.object(llm, "_show_llama_console", lambda: False),
            mock.patch.object(llm, "detect_free_vram_mib", lambda: self.free),
            mock.patch.object(llm, "_resolve_mmproj_path", lambda _p: self.projector),
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
        cfg = llm.LLMConfig(mode="local", model_repo_id="", model_filename="tiny.gguf",
                            downloaded_models_dir=str(self.models), local_ctx_size=32768, llama_backend="cuda")
        llm.set_config(cfg)

    def _start(self, **cfg_changes) -> list:
        cfg = self.llm.get_config()
        for k, v in cfg_changes.items():
            setattr(cfg, k, v)
        self.llm.set_config(cfg)
        proc = self.llm.start_local_llama()
        self.assertIsNotNone(proc, self.llm.get_state().error)
        self.addCleanup(lambda: setattr(self.llm, "_llama_process", None))
        return proc.args

    def _flag(self, args: list, flag: str) -> "str | None":
        return args[args.index(flag) + 1] if flag in args else None

    def test_ticked_kv_quant_reaches_the_command_line(self) -> None:
        args = self._start(local_kv_cache_quant=True)
        self.assertEqual(self._flag(args, "--cache-type-k"), "q8_0")
        self.assertEqual(self._flag(args, "--cache-type-v"), "q8_0")
        self.assertEqual(self._flag(args, "--parallel"), "1")
        self.assertEqual(self._flag(args, "--cache-ram"), str(self.llm.LOCAL_CACHE_RAM_MIB))
        self.assertEqual(self._flag(args, "--cache-reuse"), "256")     # no projector

    def test_f16_when_it_fits_q8_when_it_does_not(self) -> None:
        self.free = 30000
        self.assertNotIn("--cache-type-k", self._start())
        plan = self.llm.get_last_memory_plan()
        self.assertEqual((plan.kv_q8, plan.fits), (False, True))
        # Too little room for f16 at this context, enough for q8 (the tiny model's
        # KV is 512 B/token f16 -> 32K needs 16 MiB more with f16 than with q8).
        self.llm.stop_local_llama()
        self.free = plan.need_mib + self.llm._VRAM_MARGIN_MIB - 1
        args = self._start()
        self.assertEqual(self._flag(args, "--cache-type-k"), "q8_0")
        self.assertTrue(self.llm.get_last_memory_plan().kv_q8)
        self.assertTrue(self.llm.get_last_memory_plan().fits)

    def test_projector_drops_cache_reuse(self) -> None:
        proj = self.models / "mmproj-tiny.gguf"
        proj.write_bytes(b"GGUF" + b"\0" * 64)
        self.projector = proj
        args = self._start()
        self.assertNotIn("--cache-reuse", args)
        self.assertEqual(self._flag(args, "--mmproj"), str(proj))

    def test_side_by_side_slots(self) -> None:
        args = self._start(local_parallel=3)
        self.assertEqual(self._flag(args, "--parallel"), "3")
        self.assertIn("--kv-unified", args)

    def test_previous_log_kept_and_no_folder_in_models_dir(self) -> None:
        (self.bundled / "llama-server.log").write_text("crash of the last run", encoding="utf-8")
        self._start()
        self.assertEqual((self.bundled / "llama-server.prev.log").read_text(encoding="utf-8"),
                         "crash of the last run")
        self.assertEqual(sorted(p.name for p in self.models.iterdir()), ["tiny.gguf"])
        self.assertFalse((self.models / ".hf_cache").exists())

    def test_stop_during_load_is_not_a_startup_error(self) -> None:
        self._start()
        proc = self.llm.get_llama_process()
        self.llm.stop_local_llama()
        self.assertTrue(self.llm.stopped_by_user(proc))
        with mock.patch.object(self.llm, "health_check", lambda *a, **k: False):
            ready = self.llm.wait_until_ready(timeout=5.0, proc=proc)
        self.assertFalse(ready)
        self.assertEqual(self.llm.get_state().error, "")
        # A crash the user did not cause is still an error.
        other = _FakeProc(["x"])
        other.pid = 777
        other.returncode = 1
        with mock.patch.object(self.llm, "health_check", lambda *a, **k: False):
            self.assertFalse(self.llm.wait_until_ready(timeout=5.0, proc=other))
        self.assertIn("exited during startup", self.llm.get_state().error)

    def test_chat_start_shows_stopped(self) -> None:
        src = (ADDON / "ui_chat.py").read_text(encoding="utf-8")
        start = src.index("def _start_llm_backend")
        body = src[start:src.index("thread = threading.Thread(target=_start_llm_backend", start)]
        self.assertIn("stopped_by_user(proc)", body)
        self.assertIn('_set_chat_status("Stopped")', body)


if __name__ == "__main__":
    unittest.main()
