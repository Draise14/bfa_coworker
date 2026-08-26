# BFA Coworker — Tier 3f: Buddy Optimizations — Model UX, Defaults & Security

**Date**: 2026-08-26
**Status**: Planning — Not Started
**Depends on**: Existing `llm_manager.py`, `preferences.py`, `agent_controller.py`
**Reference Issue**: [#29 — Improve downloading UX from Hugging Face](https://github.com/Draise14/bfa_coworker/issues/29)
**Reference Implementation**: [Blender Buddy v9.13.1](https://github.com/CGMatter/blender_buddy) — `__init__.py` lines 1-7800

---

## Table of Contents

1. [Current State Analysis](#1-current-state-analysis)
2. [The Problems](#2-the-problems)
3. [Blender Buddy's Approach — What to Adopt](#3-blender-buddys-approach--what-to-adopt)
4. [Implementation Plan](#4-implementation-plan)
5. [Summary of Changes](#5-summary-of-changes)

---

## 1. Current State Analysis

### 1.1 The Model Selection UX (The Problem)

BFA Coworker's preferences currently show **9 curated presets across 3 categories**:

```
┌─ Pick a Model ─────────────────────────────────────────────┐
│ Flagship (24 GB+ VRAM)                                      │
│   Qwen3.8-27B (Q8_0)         │ RTX 3090/4090/5090          │
│                              └ Latest Qwen3.8 — best...    │
│   Fable Fusion 27B (Q6_K)    │ RTX 3090/4090/5090          │
│                              └ Top-ranked fine-tune...     │
│   Nail 35B A3B (UD-Q4_K_XL)  │ RTX 3090/4090/5090 (MoE)   │
│                              └ MoE efficiency — 3.4B...    │
│                                                             │
│ Mid-Range (16-20 GB VRAM)                                   │
│   GPT-OSS 20B (Q4_K_M)  ★    │ RTX 3090/4090 — 12 GB+     │
│                              └ OpenAI's open-weight...     │
│   Qwen3.8-27B (Q4_K_M)       │ RTX 3090/4090 — 16 GB+     │
│                              └ Latest Qwen3.8 at Q4...    │
│   Fable Fusion 27B (IQ4_XS)  │ RTX 3090/4090 — 16 GB+     │
│                              └ Fable Fusion at IQ4...      │
│                                                             │
│ Lightweight (≤ 8 GB VRAM)                                   │
│   Gemma 4 E4B (Q4_K_M)       │ Any GPU — 4 GB+            │
│                              └ Google's small agentic...   │
│   Qwen3.5-9B DeepSeek-V4     │ Any GPU — 4 GB+            │
│                              └ DeepSeek-V4 distilled...    │
│   Qwen3.5-9B (Q8_0)          │ Any GPU — 8 GB+            │
│                              └ Highest quality light...    │
│                                                             │
│ Custom Model: [dropdown ▼]                                  │
│                                                             │
│ Or use an existing model:                                   │
│ [Scan] [Open Folder]                                        │
│ Models dir: [/home/user/bfa_coworker_models]                │
│ Using: gemma-4-26B-A4B-it-UD-Q4_K_M.gguf  ✓                │
└─────────────────────────────────────────────────────────────┘
```

Then below that, a separate "Advanced" section with raw `model_repo_id`, `model_filename`, and `local_max_tokens` fields.

### 1.2 What's Confusing

| Problem | Why It's Bad |
|---|---|
| **9 presets is too many** | Users don't know which model to pick. They see "Flagship" and think they need it, even if their hardware can't run it. |
| **Categories based on VRAM, not use case** | "Flagship" vs "Mid-Range" vs "Lightweight" is a performance hierarchy, not a needs-based one. A user with 24 GB still needs to know *which* flagship is best for Blender. |
| **Custom model flow is buried** | The "Custom Model" dropdown is at the bottom, and the "existing model" file picker is even further down. Users who already downloaded a model from HuggingFace can't find how to use it. |
| **Preset vs custom conflict** | Picking a preset clears `existing_model_path`. Picking a custom model doesn't clear the preset. The two flows fight each other. |
| **No "Recommended" guidance** | The default is `gpt_oss_20b_q4` but there's no visual indicator that it's the recommended choice. Users with 24 GB cards see it in "Mid-Range" and think they should pick a flagship. |
| **Raw HF fields in "Advanced"** | `model_repo_id` and `model_filename` are exposed as raw text fields. Users who want to download a model not in the preset list have to know the exact HuggingFace repo ID and filename. |
| **No download safety** | No SHA-256 verification, no resume support, no disk space check. A corrupted 15 GB download fails silently with "missing tensor" errors. |
| **No GPU auto-detection** | Users manually set `--n-gpu-layers`. Wrong values cause OOM crashes. |

### 1.3 Existing Infrastructure We Can Leverage

| Component | Location | What It Does |
|---|---|---|
| `ModelPreset` dataclass | `llm_manager.py` | Metadata for curated model presets |
| `PRESET_MODELS` list | `llm_manager.py` | 9 presets across 3 categories |
| `download_model()` | `llm_manager.py` | Downloads GGUF from HuggingFace |
| `start_local_llama()` | `llm_manager.py` | Launches llama-server subprocess |
| `_detect_gpu_backend()` | `llm_manager.py` | Detects CUDA/Vulkan/CPU |
| `detect_system_ram_gb()` | `llm_manager.py` | Detects total system RAM |
| `detect_vram_gb()` | `llm_manager.py` | Detects GPU VRAM |
| `recommend_context_size()` | `llm_manager.py` | Hardware-aware context size recommendation |
| `_openai_chat_completions()` | `agent_controller.py` | Sends chat requests with sampling params |
| `_update_model_preset()` | `preferences.py` | Auto-fills fields when preset changes |
| Model preset UI | `preferences.py` | Draws the 3-category preset grid |

---

## 2. The Problems

### 2.1 Problem 1: Model Selection Is Overwhelming

**Root cause**: We have 9 presets because we're trying to cover every possible model architecture (Qwen3.8, Fable Fusion, Nail, GPT-OSS, Gemma 4, Qwen3.5-9B) at every quality tier. Blender Buddy has 3 presets because it picked ONE model architecture and offers it at 3 quantization levels.

**The fix**: Reduce to a **single recommended model family** with quantization tiers, plus a few "specialist" alternatives for specific needs. Users pick based on their RAM, not model specs.

### 2.2 Problem 2: Custom Model Download Is Confusing

**Root cause**: The "custom model" flow is split across two UI sections — the preset dropdown at the top and the "existing model" file picker at the bottom. Users who want to download a model from HuggingFace that isn't in the preset list have to:
1. Know the exact `repo_id` and `filename`
2. Type them into the Advanced fields
3. Click "Download Model"
4. Hope it works

Blender Buddy solves this by having a single "Download" button per model tier, with SHA-256 verification and resume support. No raw fields needed.

**The fix**: Add a "Custom Model URL" flow that lets users paste a HuggingFace URL or GGUF path, auto-detects the filename, and downloads with full verification.

### 2.3 Problem 3: No Download Safety Guards

**Root cause**: Our download code uses basic `urllib` or `requests` with no verification, no resume, no disk space check.

**The fix**: Adopt Blender Buddy's five safety patterns:
1. SHA-256 verification before atomic rename
2. HTTP Range resume from `.part` files
3. Disk space preflight before download
4. Cancel support via `threading.Event`
5. Fallback mirror URLs

### 2.4 Problem 4: Inference Defaults Are Suboptimal

**Root cause**: We use flat temperature 0.3 for everything, no top_k, 8K context, and 16K max_tokens. This causes:
- MoE repetition loops (no top_k to truncate)
- Hallucinated API calls (temperature too high for code gen)
- Robotic prose (temperature too low for Ask mode)
- Truncated conversations (8K context too small)
- Rambling responses (16K max_tokens too high)

**The fix**: Adopt Blender Buddy's battle-tested sampling parameters.

---

## 3. Blender Buddy's Approach — What to Adopt

### 3.1 Model Selection: One Family, Three Quants

Blender Buddy's model selection is dead simple:

```
┌─ 2. Models (3/4 downloaded — 3/3 text, vision ✓) ──────────┐
│ Your system: 32 GB RAM · NVIDIA GPU: 23.9 GB                 │
│                                                               │
│ Low       ~9.7 GB · 16 GB RAM          [Download]            │
│ Medium    ~14.7 GB · 24 GB RAM  (active) [✓]                 │
│ High      ~21.7 GB · 32 GB RAM  (recommended) [Download]     │
│                                                               │
│ Vision    ~5.8 GB · optional            [✓]                  │
└───────────────────────────────────────────────────────────────┘
```

**Key design decisions:**
- **One model architecture** (Qwen3-30B-A3B-Instruct-2507) — same tool-calling behavior, same prompt format, same reliability across all tiers
- **Three quantization tiers** — users only choose based on their RAM
- **"Recommended" badge** — based on detected hardware, not marketing
- **Vision model is separate** — only downloaded when needed
- **System specs shown** — helps users understand *why* a tier is recommended

### 3.2 Download UX: One Button Per Tier

Each tier has:
- A **"Select" button** (only enabled if downloaded) — makes it the active model
- A **"Download" button** (or "✓" if already downloaded) — one click
- **Size + RAM requirement** displayed inline
- **Progress bar with cancel** during download
- **"Recommended" badge** based on hardware detection

No raw `repo_id`/`filename` fields. No "Advanced" section. Everything is one click.

### 3.3 Safety Guards: Five Layers

| Guard | What It Does | Lines in Buddy |
|---|---|---|
| **SHA-256 verification** | Computes hash of downloaded file, compares to expected. Raises clear error on mismatch. | `_verify_sha256()` ~15 lines |
| **HTTP Range resume** | If a `.part` file exists, sends `Range: bytes=N-` header. Server returns 206, we append. | `download_file()` ~20 lines |
| **Disk space preflight** | HEAD requests the URL to get Content-Length, compares to `shutil.disk_usage()` free space. | `_assert_free_space()` ~10 lines |
| **Cancel support** | `threading.Event` checked between chunks. Leaves `.part` for resume. | `_download_cancel` ~5 lines |
| **Fallback mirrors** | Primary URL (Unsloth HF) + fallback URL (our mirror). Tries primary first, falls back on failure. | `download_file_with_fallback()` ~20 lines |

### 3.4 GPU Auto-Detection: One Function

```python
def autodetect_gpu_layers(model_file, context_size, backend):
    free = _free_gpu_memory_mb(backend)  # nvidia-smi / sysctl
    model_mb = os.path.getsize(model_file) / (1024 * 1024)
    kv_mb = (context_size / 1024) * 70     # 70 MB per 1K context
    usable_mb = free - 700 - kv_mb         # 700 MB runtime overhead
    if usable_mb >= model_mb * 1.05:
        return 99  # Full GPU offload
    per_layer = model_mb / 33  # ~33 layers typical
    return max(0, min(33, int(usable_mb / per_layer)))
```

This eliminates the #1 cause of "llama-server crashed at startup" — wrong `--n-gpu-layers`.

### 3.5 Inference Tuning: Smart Defaults

| Parameter | Buddy's Value | Why |
|---|---|---|
| `temperature` | 0.2 (code) / 0.35 (prose) — auto-switches | Code needs deterministic output; prose needs natural flow |
| `top_k` | 20 | Prevents MoE repetition loops at low temperatures |
| `top_p` | 0.8 | Conservative tail truncation |
| `repeat_penalty` | 1.1 | Prevents word/phrase repetition |
| `min_p` | 0.0 | Explicitly disabled |
| `max_tokens` | 1024 default / 4096 deep | Low cap + more tool rounds = more efficient |
| `context_size` | 16384 default | Sweet spot for tool-calling sessions |

---

## 4. Implementation Plan

### Phase 1: Simplify Model Presets (~150 LOC, 2 files)

**What**: Reduce from 9 presets across 3 categories to a **single recommended model family** with quantization tiers, plus a few alternative models for specific needs.

**Reference**: Blender Buddy's `TEXT_MODEL_VARIANTS` dictionary — one model, three quants.

**Implementation:**

**Step 1.1 — New preset structure**

```python
# One primary model family with quantization tiers.
# Users pick based on their RAM, not model architecture.
PRIMARY_MODEL = {
    "repo_id": "unsloth/Qwen3-30B-A3B-Instruct-2507-GGUF",
    "family": "Qwen3-30B-A3B (MoE, 3.3B active)",
    "variants": {
        "low": {
            "label": "Low",
            "filename": "Qwen3-30B-A3B-Instruct-2507-UD-IQ1_M.gguf",
            "size_gb": 9.7,
            "ram_gb": 16,
            "quant": "UD-IQ1_M",
            "sha256": "d527a854db2a1582a3ce746a17b1f42d860334ece18d385ede9e2e395058b39e",
        },
        "medium": {
            "label": "Medium",
            "filename": "Qwen3-30B-A3B-Instruct-2507-Q3_K_M.gguf",
            "size_gb": 14.7,
            "ram_gb": 24,
            "quant": "Q3_K_M",
            "sha256": "e145c9d2f5d11c9583eb099aa75100b7ab943e77d5240c9a2cd936f81c89ef43",
        },
        "high": {
            "label": "High",
            "filename": "Qwen3-30B-A3B-Instruct-2507-Q5_K_M.gguf",
            "size_gb": 21.7,
            "ram_gb": 32,
            "quant": "Q5_K_M",
            "sha256": "74cf6e525344a184e59f8dbd1d18e59587f1a03eaff66f6b1fbd0ee3a53a3d68",
        },
    },
}

# Alternative models for specific needs (kept but de-emphasized).
# These are "power user" options, not primary choices.
ALTERNATIVE_MODELS = [
    {
        "identifier": "gpt_oss_20b_q4",
        "name": "GPT-OSS 20B",
        "why": "Native function calling — best for complex tool use",
        "repo_id": "unsloth/gpt-oss-20b-GGUF",
        "filename": "gpt-oss-20b-Q4_K_M.gguf",
        "size_gb": 12,
        "ram_gb": 16,
    },
    {
        "identifier": "gemma4_e4b_q4",
        "name": "Gemma 4 E4B",
        "why": "Runs on 4 GB — best for low-end hardware",
        "repo_id": "unsloth/gemma-4-E4B-it-GGUF",
        "filename": "gemma-4-E4B-it-Q4_K_M.gguf",
        "size_gb": 5,
        "ram_gb": 6,
    },
]

VISION_MODEL = {
    "repo_id": "unsloth/Qwen3-VL-8B-Instruct-GGUF",
    "filename": "Qwen3-VL-8B-Instruct-Q4_K_M.gguf",
    "mmproj_filename": "mmproj-F16.gguf",
    "size_gb": 5.8,
    "label": "Vision Model (screenshots)",
}
```

**Step 1.2 — New preferences UI**

```
┌─ Model ─────────────────────────────────────────────────────┐
│ Your system: 32 GB RAM · NVIDIA RTX 4090: 23.9 GB            │
│                                                               │
│ ★ Recommended: Qwen3-30B-A3B (MoE, 3.3B active)              │
│                                                               │
│   Low     ~9.7 GB · 16 GB RAM    [Select] [Download]         │
│   Medium  ~14.7 GB · 24 GB RAM   [Active] [✓]               │
│   High    ~21.7 GB · 32 GB RAM   [Select] [Download]  ★     │
│                                                               │
│ ── Vision (optional) ─────────────────────────────────────── │
│   Vision Model  ~5.8 GB          [Download]                  │
│                                                               │
│ ── Alternative Models ────────────────────────────────────── │
│   GPT-OSS 20B  ~12 GB            [Select] [Download]         │
│   Native function calling — best for complex tool use         │
│                                                               │
│   Gemma 4 E4B  ~5 GB             [Select] [Download]         │
│   Runs on 4 GB — best for low-end hardware                    │
│                                                               │
│ ── Custom Model ──────────────────────────────────────────── │
│   HuggingFace URL or GGUF path: [________________________]   │
│   [Download & Verify]                                         │
│                                                               │
│   Or use a local file:                                        │
│   [Browse...]  /path/to/model.gguf                            │
└───────────────────────────────────────────────────────────────┘
```

**Step 1.3 — Hardware detection for recommendation**

```python
def _recommend_variant() -> str:
    """Recommend the best quantization variant based on detected hardware."""
    ram_mb, gpu_label, gpu_mb = _hardware_info()
    usable_mb = gpu_mb or (ram_mb or 0)
    if usable_mb >= 28 * 1024:
        return "high"
    if usable_mb >= 16 * 1024:
        return "medium"
    return "low"
```

**Files modified:**
- `addon/bfa_coworker/llm_manager.py` — new preset structure, hardware detection
- `addon/bfa_coworker/preferences.py` — new UI layout

**Verification:**
1. Launch on 32 GB system → "High ★ Recommended" badge
2. Launch on 16 GB system → "Medium ★ Recommended" badge
3. Launch on 8 GB system → "Low ★ Recommended" badge
4. Alternative models are collapsed by default, expandable
5. Custom model URL field accepts HuggingFace URLs and local paths

---

### Phase 2: Download Safety Guards (~200 LOC, 1 file)

**What**: Add SHA-256 verification, HTTP Range resume, disk space preflight, cancel support, and fallback mirrors to `llm_manager.py`.

**Reference**: Blender Buddy's `download_file()`, `_verify_sha256()`, `_assert_free_space()`, `download_file_with_fallback()`, `_download_cancel`.

**Implementation:**

**Step 2.1 — SHA-256 verification**

```python
def _verify_sha256(path: Path, expected: str) -> None:
    """Compute SHA-256 of path, raise RuntimeError on mismatch."""
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    got = h.hexdigest().lower()
    want = expected.lower().strip()
    if got != want:
        raise RuntimeError(
            f"Checksum mismatch for {path.name}: "
            f"expected {want[:12]}…, got {got[:12]}…. "
            f"Delete the file and re-download."
        )
```

**Step 2.2 — HTTP Range resume with .part files**

```python
def _download_with_resume(
    url: str, dest: Path, label: str,
    expected_sha256: str = "", progress_cb=None,
) -> None:
    """Download with resume support. Verifies SHA-256 before atomic rename."""
    tmp = dest.with_suffix(dest.suffix + ".part")
    already = tmp.stat().st_size if tmp.exists() else 0
    
    headers = {"User-Agent": "bfa-coworker/1.0"}
    if already > 0:
        headers["Range"] = f"bytes={already}-"
    
    req = urllib.request.Request(url, headers=headers)
    mode = "ab" if already > 0 else "wb"
    total = already
    
    with urllib.request.urlopen(req, timeout=60) as r:
        total_from_resp = int(r.headers.get("Content-Length") or 0)
        total_expected = total_from_resp + already if already else total_from_resp
        
        with open(tmp, mode) as f:
            while True:
                if _download_cancel.is_set():
                    raise RuntimeError("Download cancelled by user.")
                buf = r.read(1024 * 256)
                if not buf:
                    break
                f.write(buf)
                total += len(buf)
                if progress_cb and total_expected:
                    progress_cb(total / total_expected)
    
    if expected_sha256:
        _verify_sha256(tmp, expected_sha256)
    os.replace(tmp, dest)  # atomic rename
```

**Step 2.3 — Disk space preflight**

```python
def _assert_free_space(dest: Path, needed_bytes: int) -> None:
    """Raise RuntimeError if the filesystem doesn't have room."""
    free = shutil.disk_usage(dest.parent).free
    headroom = 128 * 1024 * 1024  # 128 MB safety margin
    if free < needed_bytes + headroom:
        raise RuntimeError(
            f"Not enough disk space. Need {needed_bytes / 1e9:.1f} GB, "
            f"have {free / 1e9:.1f} GB free in {dest.parent}."
        )
```

**Step 2.4 — Fallback URLs**

```python
def _download_with_fallback(
    urls: list[str], dest: Path, label: str,
    expected_sha256: str = "", progress_cb=None,
) -> None:
    """Try each URL in order. On failure, try the next."""
    last_err = None
    for i, url in enumerate(urls):
        try:
            _download_with_resume(url, dest, label, expected_sha256, progress_cb)
            return
        except Exception as e:
            last_err = e
            # Clean up partial file before trying next URL
            tmp = dest.with_suffix(dest.suffix + ".part")
            if tmp.exists():
                tmp.unlink()
    raise last_err or RuntimeError("All download sources failed")
```

**Step 2.5 — Wire into existing download_model()**

Replace the current download logic with the new safe functions. Add `expected_sha256` to each preset. Call `_assert_free_space()` before starting. Show progress via the existing `_set_download_progress()` callback.

**Files modified**: `addon/bfa_coworker/llm_manager.py`

**Verification:**
1. Download with SHA-256 → passes verification, file renamed atomically
2. Corrupt .part file → SHA-256 mismatch error with clear message
3. Interrupt download mid-way → resume from .part on retry
4. Fill disk → preflight error before download starts
5. Primary URL fails → falls back to mirror URL
6. Cancel during download → .part preserved for resume

---

### Phase 3: GPU Auto-Detection (~120 LOC, 1 file)

**What**: Auto-detect GPU VRAM and compute optimal `--n-gpu-layers` value.

**Reference**: Blender Buddy's `autodetect_gpu_layers()`, `_nvidia_free_vram_mb()`, `_hardware_info()`.

**Implementation:**

**Step 3.1 — Hardware info cache**

```python
_HARDWARE_INFO_CACHE: dict = {}

def _hardware_info() -> tuple[int | None, str | None, int | None]:
    """Return (ram_mb, gpu_label, gpu_mb). Cached for the session."""
    if _HARDWARE_INFO_CACHE:
        return (_HARDWARE_INFO_CACHE["ram_mb"],
                _HARDWARE_INFO_CACHE["gpu_label"],
                _HARDWARE_INFO_CACHE["gpu_mb"])
    
    ram_mb = detect_system_ram_gb() * 1024 if detect_system_ram_gb() else None
    gpu_label = None
    gpu_mb = None
    
    if shutil.which("nvidia-smi"):
        try:
            name = subprocess.check_output(
                ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                text=True, timeout=5,
            ).strip().splitlines()[0]
            total = int(subprocess.check_output(
                ["nvidia-smi", "--query-gpu=memory.total",
                 "--format=csv,noheader,nounits"],
                text=True, timeout=5,
            ).strip().splitlines()[0])
            gpu_label = name
            gpu_mb = total
        except Exception:
            pass
    elif sys.platform == "darwin":
        try:
            out = subprocess.check_output(
                ["sysctl", "-n", "hw.memsize"], text=True, timeout=5,
            )
            total_mb = int(out.strip()) // (1024 * 1024)
            gpu_label = "Apple unified memory"
            gpu_mb = int(total_mb * 0.6)  # ~60% usable for model
        except Exception:
            pass
    
    _HARDWARE_INFO_CACHE.update(
        ram_mb=ram_mb, gpu_label=gpu_label, gpu_mb=gpu_mb)
    return ram_mb, gpu_label, gpu_mb
```

**Step 3.2 — GPU layer auto-detection**

```python
_RUNTIME_OVERHEAD_MB = 700
_KV_MB_PER_1K_CTX = 70
_TYPICAL_LAYERS = 33
_FULL_OFFLOAD = 99

def autodetect_gpu_layers(model_path: Path, context_size: int) -> int:
    """Calculate optimal --n-gpu-layers for the given model and hardware."""
    backend = _detect_gpu_backend()
    if backend == "cpu":
        return 0
    
    _, _, gpu_mb = _hardware_info()
    if gpu_mb is None:
        return _FULL_OFFLOAD  # Can't detect — try full offload
    
    model_mb = model_path.stat().st_size / (1024 * 1024)
    kv_mb = (context_size / 1024) * _KV_MB_PER_1K_CTX
    usable_mb = gpu_mb - _RUNTIME_OVERHEAD_MB - kv_mb
    
    if usable_mb <= 0:
        return 0  # Not enough VRAM for GPU offload
    if usable_mb >= model_mb * 1.05:
        return _FULL_OFFLOAD  # Full GPU offload
    
    per_layer = model_mb / _TYPICAL_LAYERS
    return max(0, min(_TYPICAL_LAYERS, int(usable_mb / per_layer)))
```

**Step 3.3 — Wire into start_local_llama()**

Replace the current manual `--n-gpu-layers` logic with auto-detected value:

```python
# In start_local_llama():
ngl = autodetect_gpu_layers(model_path, ctx_size)
cmd.extend(["-ngl", str(ngl)])
print(f"[🛠️Coworker] auto-detected GPU layers: {ngl} "
      f"(model: {model_path.name}, ctx: {ctx_size})")
```

**Files modified**: `addon/bfa_coworker/llm_manager.py`

**Verification:**
1. NVIDIA GPU with 24 GB + 15 GB model → full offload (ngl=99)
2. NVIDIA GPU with 8 GB + 15 GB model → partial offload (calculated layers)
3. CPU-only → ngl=0
4. Metal (Apple Silicon) → calculated based on unified memory
5. No GPU detected → ngl=0, clear log message

---

### Phase 4: Inference Sampling Overhaul (~80 LOC, 2 files)

**What**: Adopt Blender Buddy's sampling parameters with temperature auto-switching.

**Reference**: Blender Buddy's `_CHAT_SAMPLING`, `DEFAULT_TEMPERATURE_CODE`, `DEFAULT_TEMPERATURE_PROSE`.

**Implementation:**

**Step 4.1 — New sampling constants in agent_controller.py**

```python
# Sampling parameters tuned for MoE local models.
# Reference: Blender Buddy v9.13.1 — these values prevent
# greedy repetition loops in Qwen3/GPT-OSS MoE architectures.
_CHAT_SAMPLING = {
    "repeat_penalty": 1.1,
    "top_p": 0.8,
    "top_k": 20,
    "min_p": 0.0,
}

# Temperature auto-switches based on mode:
# - Agent mode (code gen) → 0.2: sharp, deterministic
# - Ask mode (prose/UI)  → 0.35: natural writing
DEFAULT_TEMPERATURE_CODE = 0.2
DEFAULT_TEMPERATURE_PROSE = 0.35

# Blender Buddy uses low max_tokens + more tool rounds.
# This is more token-efficient and gives incremental progress.
DEFAULT_MAX_TOKENS = 1024
DEEP_MAX_TOKENS = 4096
MAX_TOOL_ITERATIONS = 8
DEEP_TOOL_ITERATIONS = 30
```

**Step 4.2 — Wire temperature auto-switch into _openai_chat_completions()**

```python
def _openai_chat_completions(
    url, messages, tools, api_key=None, model=None,
    max_tokens=None, chat_mode="AGENT",
):
    temperature = (
        DEFAULT_TEMPERATURE_CODE if chat_mode == "AGENT"
        else DEFAULT_TEMPERATURE_PROSE
    )
    body = {
        "messages": messages,
        "stream": False,
        "max_tokens": max_tokens if max_tokens is not None else DEFAULT_MAX_TOKENS,
        "temperature": temperature,
        **_CHAT_SAMPLING,
    }
    # ... rest of function unchanged ...
```

**Step 4.3 — Default context size to 16K**

```python
# In llm_manager.py LLMConfig:
local_ctx_size: int = 16384  # Was 8192 — 16K is the sweet spot

# Update recommend_context_size() to default to 16K:
def recommend_context_size(ram_gb: float | None = None,
                            vram_gb: float | None = None,
                            backend: str = "auto") -> int:
    """Recommend a context size based on available memory.
    Default floor is now 16384 (was 8192)."""
    # ... existing logic, but with 16384 as the minimum recommendation ...
```

**Step 4.4 — Add tool iteration cap**

```python
# In run_conversation_turn(), add iteration cap:
max_iterations = MAX_TOOL_ITERATIONS
for iteration in range(max_iterations):
    # ... existing tool loop ...
    if iteration >= max_iterations - 1:
        print("[🛠️Coworker] tool iteration cap reached — forcing final answer")
        # Force a final non-tool response
```

**Files modified:**
- `addon/bfa_coworker/agent_controller.py` — sampling constants, temperature auto-switch, tool iteration cap
- `addon/bfa_coworker/llm_manager.py` — default context size, recommendation floor

**Verification:**
1. Agent mode → temperature 0.2, top_k 20 → no repetition loops
2. Ask mode → temperature 0.35 → natural prose
3. Tool loop caps at 8 iterations → no infinite loops
4. 16K context → longer conversations without truncation
5. Default max_tokens 1024 → more concise responses, more tool rounds

---

### Phase 5: Custom Model URL Flow (~100 LOC, 2 files)

**What**: Add a "Custom Model URL" field that accepts HuggingFace URLs or GGUF paths, auto-detects the filename, and downloads with full verification.

**Reference**: Blender Buddy's single-click download per variant.

**Implementation:**

**Step 5.1 — URL parsing**

```python
def _parse_model_url(url: str) -> tuple[str, str, str] | None:
    """Parse a HuggingFace URL or direct GGUF link.
    Returns (repo_id, filename, sha256_or_none) or None."""
    url = url.strip()
    
    # HuggingFace blob URL:
    # https://huggingface.co/unsloth/Qwen3-30B-A3B-Instruct-2507-GGUF/resolve/main/file.gguf
    hf_match = re.match(
        r'https?://huggingface\.co/([^/]+/[^/]+)/resolve/([^/]+)/(.+)',
        url
    )
    if hf_match:
        return hf_match.group(1), hf_match.group(3), None
    
    # Direct GGUF link (any URL ending in .gguf)
    if url.lower().endswith('.gguf'):
        filename = url.rsplit('/', 1)[-1]
        return "", filename, None
    
    # Local file path
    if os.path.isfile(url) and url.lower().endswith('.gguf'):
        return "", os.path.basename(url), None
    
    return None
```

**Step 5.2 — Custom model UI**

```python
# In preferences.py draw(), add after the alternative models section:
custom_box = box.box()
custom_box.label(text="Custom Model", icon='URL')
custom_box.prop(self, "custom_model_url", text="URL or Path")
row = custom_box.row(align=True)
row.operator("bfacw.download_custom_model", icon='IMPORT', text="Download & Verify")
row.operator("bfacw.browse_custom_model", icon='FILE_FOLDER', text="Browse")

# Show progress during custom download
if llm_state.download_active and llm_state.download_kind == "custom_model":
    # ... progress bar + cancel button ...
```

**Step 5.3 — Download operator**

```python
class BFACW_OT_download_custom_model(Operator):
    """Download a model from a HuggingFace URL or direct GGUF link."""
    bl_idname = "bfacw.download_custom_model"
    bl_label = "Download Custom Model"
    bl_description = "Download a model from a HuggingFace URL or direct .gguf link"
    
    def execute(self, context):
        prefs = context.preferences.addons[__package__].preferences
        url = prefs.custom_model_url.strip()
        
        parsed = _parse_model_url(url)
        if not parsed:
            self.report({'ERROR'}, 
                "Invalid URL. Paste a HuggingFace URL "
                "(https://huggingface.co/.../resolve/main/file.gguf) "
                "or a direct .gguf link.")
            return {'CANCELLED'}
        
        repo_id, filename, _ = parsed
        dest = Path(prefs.downloaded_models_dir) / filename
        
        # Set as the active model path
        prefs.existing_model_path = str(dest)
        
        # Spawn download in background thread
        thread = threading.Thread(
            target=_download_custom_model_thread,
            args=(url, dest, filename),
            daemon=True,
        )
        thread.start()
        
        self.report({'INFO'}, f"Downloading {filename}...")
        return {'FINISHED'}
```

**Files modified:**
- `addon/bfa_coworker/llm_manager.py` — URL parser, custom download thread
- `addon/bfa_coworker/preferences.py` — custom model UI, operator class

**Verification:**
1. Paste `https://huggingface.co/unsloth/.../file.gguf` → auto-detects filename
2. Paste direct `.gguf` link → downloads with verification
3. Paste local path → uses existing file
4. Invalid URL → clear error message
5. Download progress shown with cancel button

---

### Phase 6: Server Lifecycle Hardening (~80 LOC, 1 file)

**What**: Pin llama.cpp release, add port fallback, improve crash diagnostics.

**Reference**: Blender Buddy's `LLAMACPP_PINNED_TAG`, `start_server()`, `_poll_server_ready()`.

**Implementation:**

**Step 6.1 — Pin llama.cpp release**

```python
# Pinned and tested release tag. Bump deliberately after verification
# that tool calling, vision, and chat-completion shape all work.
LLAMACPP_PINNED_TAG = "b10154"  # Currently in use — verify before pinning

# Update the download URL to use the pinned tag:
LLAMA_SERVER_DOWNLOAD_URL = (
    "https://github.com/ggml-org/llama.cpp/releases/tag/"
    f"{LLAMACPP_PINNED_TAG}"
)
```

**Step 6.2 — Port fallback scan**

```python
def _find_free_port(start: int, attempts: int = 10) -> int:
    """Find a free port starting from `start`, scanning upward."""
    for offset in range(attempts):
        port = start + offset
        if not _port_is_taken(port):
            if offset > 0:
                print(f"[🛠️Coworker] port {start} busy; using {port} instead.")
            return port
    raise RuntimeError(
        f"Ports {start}-{start + attempts - 1} are all in use. "
        f"Kill any stray llama-server processes and try again."
    )
```

**Step 6.3 — Better crash diagnostics in health poll**

```python
def _poll_server_ready(base_url: str, timeout: float = 120.0,
                        proc=None) -> bool:
    """Poll /health until ready. Surface log tail on early exit."""
    start = time.time()
    while time.time() - start < timeout:
        if proc and proc.poll() is not None:
            exit_code = proc.returncode
            tail = get_llama_server_log_tail(12)
            if tail:
                raise RuntimeError(
                    f"llama-server exited with code {exit_code} "
                    f"before becoming ready.\n\n"
                    f"--- llama-server.log (tail) ---\n{tail}"
                )
            raise RuntimeError(
                f"llama-server exited with code {exit_code} "
                f"before becoming ready."
            )
        # ... existing health check logic ...
    return False
```

**Step 6.4 — Mode-aware restart (text vs vision)**

```python
# Track current server mode so we can restart when switching:
_server_mode: str | None = None  # "text" | "vision" | None

def start_local_llama(model_path=None, mode="text"):
    global _server_mode
    
    if _llama_proc and _llama_proc.poll() is None:
        if _server_mode == mode:
            return _llama_proc  # Already running in correct mode
        # Mode switch needed — stop and restart
        stop_local_llama()
        time.sleep(0.4)  # OS port release grace period
    
    _server_mode = mode
    # ... rest of launch logic ...
```

**Files modified**: `addon/bfa_coworker/llm_manager.py`

**Verification:**
1. Port 8081 in use → auto-selects 8082
2. llama-server crashes at startup → log tail surfaced in error message
3. Pinned release tag → same binary for all users
4. Switch from text to vision mode → auto-restarts server

---

## 5. Summary of Changes

| Phase | Feature | Files Changed | LOC | Priority |
|---|---|---|---|---|
| 1 | Simplify model presets (1 family + 3 quants) | 2 | ~150 | 🔴 CRITICAL |
| 2 | Download safety guards (SHA-256, resume, preflight) | 1 | ~200 | 🔴 CRITICAL |
| 3 | GPU auto-detection (VRAM + layer calculation) | 1 | ~120 | 🔴 CRITICAL |
| 4 | Inference sampling overhaul (top_k, temp auto-switch) | 2 | ~80 | 🔴 CRITICAL |
| 5 | Custom model URL flow | 2 | ~100 | 🟡 HIGH |
| 6 | Server lifecycle hardening (pin, port fallback, diagnostics) | 1 | ~80 | 🟡 HIGH |
| **Total** | | **3** | **~730** | |

### Files Modified

| File | Phases |
|---|---|
| `addon/bfa_coworker/llm_manager.py` | 1, 2, 3, 5, 6 |
| `addon/bfa_coworker/preferences.py` | 1, 5 |
| `addon/bfa_coworker/agent_controller.py` | 4 |

### Key Design Decisions

| Decision | Rationale |
|---|---|
| **One primary model family, three quants** | Blender Buddy's approach. Users pick based on their RAM, not model specs. Same tool-calling behavior across all tiers. |
| **Keep alternative models but de-emphasize** | GPT-OSS and Gemma 4 serve real needs (native function calling, low-end hardware). Keep them as "power user" options behind a collapsed section. |
| **SHA-256 on every download** | Non-negotiable. A single corrupted byte in a 15 GB GGUF causes "missing tensor" errors that waste hours. |
| **Auto-detect GPU layers** | Eliminates the #1 cause of "llama-server crashed at startup." |
| **Custom URL field instead of raw repo_id/filename** | Users paste a URL, we parse it. No need to know HuggingFace's repo structure. |
| **Temperature auto-switching** | Code gen needs 0.2, prose needs 0.35. One flat value is wrong for both. |
| **Pin llama.cpp release** | Daily releases break things. Pin to a tested tag, bump deliberately. |

### What Changes for Users

**Before (Tier 3f):**
- 9 presets across 3 VRAM-based categories
- "Flagship" vs "Mid-Range" vs "Lightweight" — confusing hierarchy
- Raw `model_repo_id` and `model_filename` fields in Advanced
- "Existing model" file picker at the bottom
- No download verification
- Manual `--n-gpu-layers`
- Flat temperature 0.3 for everything
- 8K context window

**After (Tier 3f):**
- 1 primary model (Qwen3-30B-A3B) at 3 quantization tiers
- "Low / Medium / High ★ Recommended" — pick based on your RAM
- 2 alternative models (GPT-OSS, Gemma 4) in collapsed section
- "Custom Model URL" field — paste any HuggingFace link
- SHA-256 verified downloads with resume
- Auto-detected GPU layers
- Temperature auto-switches (0.2 code / 0.35 prose)
- 16K context window
- top_k=20 prevents MoE repetition loops
- Pinned llama.cpp release for reproducibility
- Port fallback — no more "port in use" errors
- Better crash diagnostics with log tail surfacing

### Testing Guide

#### Phase 1: Model Presets

| Step | Expected Result |
|---|---|
| Open Preferences → Model section | Shows system specs, primary model with 3 tiers, "★ Recommended" badge |
| Click "Download" on a tier | Download starts with progress bar + cancel |
| Click "Select" on a downloaded tier | Becomes active model |
| Expand "Alternative Models" | Shows GPT-OSS and Gemma 4 with descriptions |
| Collapse "Alternative Models" | Hidden from view |

#### Phase 2: Download Safety

| Step | Expected Result |
|---|---|
| Download with valid SHA-256 | Passes verification, file appears in models dir |
| Delete a model, re-download | Fresh download (no .part resume) |
| Interrupt download (kill Blender) | .part file preserved |
| Re-download same model | Resumes from .part |
| Fill disk before download | Clear error message about disk space |

#### Phase 3: GPU Auto-Detection

| Step | Expected Result |
|---|---|
| Start on NVIDIA GPU | Auto-detected GPU layers logged |
| Start on CPU-only | ngl=0, model loads in system RAM |
| Start on Apple Silicon | Auto-detected based on unified memory |

#### Phase 4: Inference Sampling

| Step | Expected Result |
|---|---|
| Agent mode message | temperature=0.2, top_k=20, max_tokens=1024 |
| Ask mode message | temperature=0.35, top_k=20, max_tokens=1024 |
| Tool loop with 9+ iterations | Capped at 8, forced final answer |
| 16K context conversation | No truncation in normal use |

#### Phase 5: Custom Model URL

| Step | Expected Result |
|---|---|
| Paste HF URL | Auto-detects filename, downloads with verification |
| Paste direct .gguf link | Downloads with verification |
| Paste invalid URL | Clear error message |
| Paste local file path | Uses existing file |

#### Phase 6: Server Lifecycle

| Step | Expected Result |
|---|---|
| Port 8081 in use | Auto-selects 8082 |
| llama-server crashes | Log tail in error message |
| Switch text → vision mode | Auto-restarts server |