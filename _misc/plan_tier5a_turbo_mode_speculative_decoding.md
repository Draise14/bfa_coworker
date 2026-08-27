# BFA Coworker — Tier 5a: Turbo Mode — Speculative Decoding with DFlash2

**Date**: 2026-08-26
**Status**: Planning — Blocked on Upstream
**Depends on**: Tier 3f (Buddy Optimizations — model UX, GPU auto-detection), Tier 5 (generative systems infrastructure)
**Blocks**: Nothing — this is an accelerator, not a dependency

**Upstream Dependency**: [llama.cpp PR #27342](https://github.com/ggml-org/llama.cpp/pull/27342) — DFlash2 speculative decoding support. Must be merged into main and included in a tagged release before we can ship.

---

## Table of Contents

1. [What Is Speculative Decoding?](#1-what-is-speculative-decoding)
2. [Why It Matters for Local Inference](#2-why-it-matters-for-local-inference)
3. [Upstream Status & Timeline](#3-upstream-status--timeline)
4. [Technical Architecture](#4-technical-architecture)
5. [Implementation Plan](#5-implementation-plan)
6. [Summary of Changes](#6-summary-of-changes)

---

## 1. What Is Speculative Decoding?

Speculative decoding is a technique that makes LLM inference **2-3x faster with zero quality loss**. It works by running a small "draft" model alongside the main model:

```
┌──────────────────────────────────────────────────┐
│                                                  │
│  1. Draft model (DFlash2, 1.1 GB) predicts      │
│     7 tokens ahead in a single fast pass         │
│                                                  │
│  2. Main model (Qwen3.8-27B, 17 GB) verifies     │
│     all 7 tokens in one forward pass             │
│                                                  │
│  3. If correct → accept all 7 tokens (speedup)   │
│     If wrong   → accept correct ones, regenerate │
│                                                  │
│  Result: 2-3x faster, lossless, same output      │
│                                                  │
└──────────────────────────────────────────────────┘
```

### Key Facts

| Property | Value |
|---|---|
| **Speedup** | 2-3x tokens per second |
| **Quality** | Lossless — greedy output matches exactly |
| **Draft model size** | 1.1 GB (Q4_K_M) or 2.0 GB (Q8_0) |
| **Extra VRAM** | ~1.5 GB (both models loaded simultaneously) |
| **Acceptance length** | 5.39 tokens per draft block (Q4_K_M) |
| **License** | Apache 2.0 |

### Why DFlash2 Specifically?

DFlash2 is a **block-diffusion drafter** — it predicts all 7 tokens in a single parallel pass, then a lightweight selector traces one coherent path through them. This is faster than traditional autoregressive drafters (which predict one token at a time).

From the [DFlash2 blog post](https://inco.ai/blog/dflash2/):
> "DFlash 2 is a block-diffusion drafter for speculative decoding. It predicts a whole block of tokens in a single pass and keeps the top candidates at every position. A lightweight selector then traces one coherent path through them."

---

## 2. Why It Matters for Local Inference

### 2.1 The Local Speed Problem

Local LLMs are slow. A Qwen3.8-27B at Q4_K_M on an RTX 4090 generates ~30-40 tokens/second. For a 500-token response, that's 12-17 seconds of waiting. Tool-calling makes it worse — each round adds another generation step.

**With DFlash2: ~70-100 tokens/second. Same 500-token response: 5-7 seconds.**

### 2.2 Competitive Advantage

| Addon | Local Speed | Speculative Decoding |
|---|---|---|
| Blender Buddy | 30-40 tok/s | ❌ |
| BlenderMCP Pro | 30-40 tok/s (Ollama) | ❌ |
| **BFA Coworker (after 5a)** | **70-100 tok/s** | ✅ |

No other Blender AI addon has speculative decoding. This is a **unique competitive advantage** for local model users.

### 2.3 User Experience Impact

- **Chat responses** appear 2-3x faster — feels more like a remote API
- **Tool-calling loops** complete faster — each round is 2-3x quicker
- **Code generation** streams faster — users see code appear more responsively
- **Long conversations** are more practical — reduced waiting between turns

---

## 3. Upstream Status & Timeline

### 3.1 Current Status

| Component | Status | Details |
|---|---|---|
| **llama.cpp PR #27342** | ❌ NOT merged | Feature branch only — requires custom build |
| **Official tagged release** | ❌ Not yet | No release includes DFlash2 support |
| **DFlash2 GGUF models** | ✅ Available | `z-lab/Qwen3.8-27B-DFlash2-GGUF` on HuggingFace |
| **Target model GGUFs** | ✅ Available | `unsloth/Qwen3.8-27B-GGUF` (our primary family) |

### 3.2 Timeline Estimate

| Milestone | Earliest | Likely | Latest |
|---|---|---|---|
| PR #27342 merged into main | 1 week | 2-4 weeks | 3+ months |
| First tagged release with support | +1 day | +1 day | +1 day |
| We test with Qwen3.8-27B + Coworker | +1 week | +1 week | +1 week |
| We pin to tested release | +1 day | +1 day | +1 day |
| **Ready to ship** | **~2 weeks** | **~3-5 weeks** | **~4+ months** |

### 3.3 Blocker Resolution

**When the PR is merged:**
1. We update `LLAMACPP_PINNED_TAG` to the first release containing the merge
2. We test with Qwen3.8-27B + DFlash2 draft model
3. We verify vision still works with the draft model loaded
4. We ship as Tier 5a

**If the PR is rejected or stalls:**
- We wait. This is an accelerator, not a requirement.
- The main model works fine without it.
- We can revisit when llama.cpp adds speculative decoding through another mechanism.

---

## 4. Technical Architecture

### 4.1 llama-server Command

```bash
llama-server \
  -m Qwen3.8-27B-Q4_K_M.gguf \           # Main model
  --mmproj mmproj-F16.gguf \              # Vision projector
  -md Qwen3.8-27B-DFlash2-Q4_K_M.gguf \  # Draft model (new flag)
  --spec-type draft-dflash \              # Speculative decoding type (new flag)
  --spec-draft-n-max 7 \                  # Tokens per draft block
  --host 127.0.0.1 \                      # Same as current
  --port 8081 \                           # Same as current
  -ngl 99 \                               # Same as current
  -c 16384                                # Same as current
```

**New flags:**
- `-md` / `--model-draft` — path to the draft model GGUF
- `--spec-type` — speculative decoding algorithm (`draft-dflash` for DFlash2)
- `--spec-draft-n-max` — maximum tokens per draft block (7 is the DFlash2 default)

**API compatibility**: The `/v1/chat/completions` endpoint is unchanged. The draft model is transparent to the client — same requests, same responses, just faster.

### 4.2 VRAM Budget

| Tier | Main Model | Draft Model | Total | Minimum VRAM |
|---|---|---|---|---|
| Light | 13.5 GB (IQ3_M) | N/A | 13.5 GB | 16 GB — **not enough for draft** |
| Balanced | 17.0 GB (Q4_K_M) | 1.1 GB (Q4_K_M) | 18.1 GB | 24 GB ✅ |
| Max | 22.0 GB (Q6_K) | 2.0 GB (Q8_0) | 24.0 GB | 32 GB ✅ |

**The Light tier cannot use speculative decoding** — the 16 GB RAM floor doesn't have room for both models. The Turbo button only appears for Balanced and Max tiers.

### 4.3 GPU Auto-Detection Integration

Since Tier 3f adds GPU auto-detection, we can check at startup:

```python
def _turbo_is_available(model_path: Path, draft_path: Path,
                         context_size: int) -> bool:
    """Check if the system has enough VRAM for both models."""
    backend = _detect_gpu_backend()
    if backend == "cpu":
        return False  # CPU-only — speculative decoding adds latency
    
    _, _, gpu_mb = _hardware_info()
    if gpu_mb is None:
        return False  # Can't detect — be safe
    
    main_mb = model_path.stat().st_size / (1024 * 1024)
    draft_mb = draft_path.stat().st_size / (1024 * 1024)
    kv_mb = (context_size / 1024) * 70
    total_mb = main_mb + draft_mb + kv_mb + 700  # 700 MB overhead
    
    return gpu_mb >= total_mb
```

---

## 5. Implementation Plan

### Phase 5a.1: Draft Model Download (~60 LOC, 1 file)

**What**: Add the DFlash2 draft model to the preset structure and download it alongside the main model.

**Implementation:**

```python
# In PRIMARY_FAMILY, add draft model info:
PRIMARY_FAMILY = {
    "repo_id": "unsloth/Qwen3.8-27B-GGUF",
    "family": "Qwen3.8-27B (latest, vision + agentic)",
    "mmproj_filename": "mmproj-F16.gguf",
    "draft_model": {  # NEW — speculative decoding accelerator
        "repo_id": "z-lab/Qwen3.8-27B-DFlash2-GGUF",
        "variants": {
            "balanced": {
                "filename": "Qwen3.8-27B-DFlash2-Q4_K_M.gguf",
                "size_gb": 1.1,
            },
            "max": {
                "filename": "Qwen3.8-27B-DFlash2-Q8_0.gguf",
                "size_gb": 2.0,
            },
        },
    },
    "variants": { ... },  # unchanged
}
```

**Files modified**: `addon/bfa_coworker/llm_manager.py`

---

### Phase 5a.2: Turbo UI (~80 LOC, 1 file)

**What**: Add a "⚡ Turbo" toggle in the preferences that downloads the draft model and enables speculative decoding.

**Implementation:**

```python
# In preferences.py, add after the primary model variant rows:
turbo_box = box.box()
turbo_row = turbo_box.row(align=True)

# Only show for Balanced and Max tiers
if selected_variant in ("balanced", "max"):
    draft_variant = PRIMARY_FAMILY["draft_model"]["variants"][selected_variant]
    draft_filename = draft_variant["filename"]
    draft_path = models_dir / draft_filename
    
    if draft_path.exists():
        # Draft model already downloaded
        turbo_row.label(
            text=f"⚡ Turbo: DFlash2 draft model — 2-3x faster",
            icon='CHECKMARK',
        )
        turbo_row.prop(self, "turbo_enabled", text="Enable Turbo",
                        toggle=True)
    else:
        # Offer download
        turbo_row.label(
            text=f"⚡ Turbo: {draft_variant['size_gb']:.1f} GB download — 2-3x faster",
            icon='LIGHTNING',
        )
        op = turbo_row.operator(
            "bfacw.download_turbo", icon='IMPORT',
            text=f"Download Turbo ({draft_variant['size_gb']:.1f} GB)",
        )
        op.variant = selected_variant
    
    # VRAM check
    if self.turbo_enabled:
        can_run = _turbo_is_available(main_model_path, draft_path, ctx_size)
        if not can_run:
            turbo_box.label(
                text="⚠ Not enough VRAM for Turbo — disabling",
                icon='ERROR',
            )
else:
    turbo_box.label(
        text="⚡ Turbo requires Balanced or Max tier (24 GB+ VRAM)",
        icon='INFO',
    )
```

**Files modified**: `addon/bfa_coworker/preferences.py`

---

### Phase 5a.3: Server Launch Integration (~40 LOC, 1 file)

**What**: Pass the draft model flags to llama-server when Turbo is enabled.

**Implementation:**

```python
# In start_local_llama(), after building the base command:
if prefs.turbo_enabled and _turbo_is_available(model_path, draft_path, ctx_size):
    cmd.extend([
        "-md", str(draft_path),
        "--spec-type", "draft-dflash",
        "--spec-draft-n-max", "7",
    ])
    print(f"[🛠️Coworker] Turbo mode enabled — DFlash2 draft model loaded")
else:
    print(f"[🛠️Coworker] Turbo mode disabled")
```

**Files modified**: `addon/bfa_coworker/llm_manager.py`

---

### Phase 5a.4: Benchmark & Verify (~40 LOC, 1 file)

**What**: Add a simple benchmark that measures tokens/second with and without Turbo.

**Implementation:**

```python
def _benchmark_turbo(base_url: str) -> dict:
    """Quick benchmark: generate 100 tokens, measure speed.
    Returns {"tokens_per_second": float, "total_time": float}."""
    import time
    prompt = "Write a Python function that creates a cube in Blender."
    
    start = time.time()
    response = _openai_chat_completions(
        base_url + "/v1/chat/completions",
        messages=[{"role": "user", "content": prompt}],
        tools=[],  # No tools — pure generation speed
        max_tokens=100,
    )
    elapsed = time.time() - start
    
    # Rough token count (chars/4 heuristic)
    content = response["choices"][0]["message"]["content"]
    tokens = len(content) // 4
    return {
        "tokens_per_second": tokens / elapsed if elapsed > 0 else 0,
        "total_time": elapsed,
        "turbo_enabled": _turbo_enabled,
    }
```

**Files modified**: `addon/bfa_coworker/llm_manager.py`

---

## 6. Summary of Changes

| Phase | Feature | Files Changed | LOC | Priority |
|---|---|---|---|---|
| 5a.1 | Draft model download | 1 | ~60 | 🟡 HIGH |
| 5a.2 | Turbo UI (toggle + download button) | 1 | ~80 | 🟡 HIGH |
| 5a.3 | Server launch integration | 1 | ~40 | 🟡 HIGH |
| 5a.4 | Benchmark & verify | 1 | ~40 | 🟢 MEDIUM |
| **Total** | | **2** | **~220** | |

### Files Modified

| File | Phases |
|---|---|
| `addon/bfa_coworker/llm_manager.py` | 5a.1, 5a.3, 5a.4 |
| `addon/bfa_coworker/preferences.py` | 5a.2 |

### Key Design Decisions

| Decision | Rationale |
|---|---|
| **Turbo is optional, not automatic** | Users explicitly opt in via a toggle. Draft model adds 1.1-2.0 GB VRAM — not everyone wants that. |
| **Light tier excluded** | 16 GB RAM can't fit both the main model (13.5 GB) and the draft model (1.1 GB) plus KV cache overhead. |
| **Separate download** | The draft model is a separate GGUF from a different repo. Users download it once, like the vision mmproj. |
| **Auto-detect VRAM before enabling** | If the user's GPU doesn't have enough VRAM, Turbo is disabled with a clear message. |
| **Same API, just faster** | The `/v1/chat/completions` endpoint is unchanged. No client-side changes needed. |

### What Users See

```
┌─ Model ─────────────────────────────────────────────────────┐
│ ★ Recommended — Qwen3.8-27B (vision + agentic)               │
│                                                               │
│   Light     ~13.5 GB · 16 GB RAM    [Select] [Download]      │
│   Balanced  ~17.0 GB · 24 GB RAM    [Active] [✓]  ★         │
│   Max       ~22.0 GB · 32 GB RAM    [Select] [Download]      │
│                                                               │
│   🎯 Vision: built-in — works out of the box                 │
│                                                               │
│   ⚡ Turbo: DFlash2 draft model — 2-3x faster                 │
│          [Download Turbo (1.1 GB)]  [ ] Enable Turbo          │
│                                                               │
│ ── Or use a local file ───────────────────────────────────── │
│   ...                                                        │
└───────────────────────────────────────────────────────────────┘
```

After downloading and enabling:
```
│   ⚡ Turbo: 2-3x faster  ✓  [✓] Enable Turbo                  │
│         ~70-100 tok/s (was ~30-40 tok/s without Turbo)       │
```

### Blockers to Track

| Blocker | How to Check | Resolution |
|---|---|---|
| **PR #27342 not merged** | Watch [llama.cpp PR #27342](https://github.com/ggml-org/llama.cpp/pull/27342) | Wait for merge → tagged release → test → pin |
| **DFlash2 flag names change** | Check the merged PR for final flag names | Update `-md` / `--spec-type` / `--spec-draft-n-max` to match |
| **Vision incompatible with draft model** | Test with screenshot + Turbo enabled | If broken, disable Turbo when vision is in use |
| **Draft model HF repo changes** | Check `z-lab/Qwen3.8-27B-DFlash2-GGUF` still exists | Switch to a mirror or re-upload |

### Future Enhancements (Post Tier 5a)

- **Auto-benchmark on first launch**: Run a quick speed test with and without Turbo, show the user the actual speedup
- **Per-model draft models**: DFlash2 drafters for other model families (GPT-OSS, Gemma 4) if they become available
- **Dynamic Turbo**: Automatically enable/disable based on current VRAM availability (if Blender is using more VRAM, disable Turbo)
- **Turbo stats in Status panel**: Show current tokens/second with Turbo enabled