# Tier 4l — Local inference, November: three well-oiled setups

**Status**: 📝 Pitch for review (2026-10-30). A November lane that runs **in parallel**
with Threads (1.1.42) and the Sequencer (1.1.43): it lives in `llm_manager.py` and the
Local preferences, not in `agent_controller.py` / `ui_chat.py`.
**Research**: web sources as of 2026-10-30 (listed in §8). Vendor and third-party
benchmarks; the bake-off (§4) decides what we ship.

## 1. The pitch in one paragraph

An artist shouldn't pick quants, context sizes or offload flags. Coworker asks **one
thing**, how strong the machine is (and pre-selects the answer from the detected
graphics memory and RAM), and runs one of **three tested setups: Low, Mid, High**. Each
setup is one model plus a launch recipe we have measured on our own benchmarks
inside Bforartists, with headroom left for the viewport and the scene. Everything else
moves under *Advanced*. The local landscape changed in our favour since September:
**35B mixture-of-experts models with ~3B active parameters now run fast on a 12 GB card**
by keeping their experts in system RAM, and **multi-token prediction** roughly doubles
the speed of dense 27B models on 24 GB cards.

## 2. Where we are (main @ 7a4e875)

| Today | Problem for an artist |
|---|---|
| 9 presets (3 flagship, 3 mid, 3 light) incl. merges and uncensored fine-tunes (Fable Fusion Heretic, Nail) | Too many choices; some merges follow tool formats less reliably; names don't say what they're for |
| `recommend_context_size()` assumes ~256 KB KV per token and caps at 128K | Right idea, but per-model numbers differ a lot; q8 KV halves it; MoE models need far less |
| No `--n-cpu-moe` | MoE models must fit entirely in VRAM, so 12–16 GB machines can't run the best mid models |
| No MTP / speculative decoding | 24 GB machines leave ~2× speed on the table |
| Sampling and reasoning are generic | Each model family has its own recommended temp / top-p / top-k and thinking switch |
| llama-server is downloaded "latest" | Flags break between builds (`--flash-attn` value syntax; the MTP flag renamed as a hard break in August) |
| The viewport and Bforartists share the GPU and RAM | A setup that "fits" in a benchmark can starve Eevee / Cycles or swap the scene |

## 3. The three setups (candidates; the bake-off picks one default + one alternate each)

| Setup | Who it's for | Candidate models (GGUF) | Launch recipe | Expected |
|---|---|---|---|---|
| **High** | 24 GB+ VRAM (3090 / 4090 / 5090), 32 GB+ RAM | **Qwen3.8-27B** (dense, vision) Q4_K_M / Q5_K_M **+ MTP sidecar**; alternate: a **35B-A3B MoE fully on GPU** for speed | all layers on GPU, `--spec-type draft-mtp` + `--spec-draft-model` (27B), q8 KV, 64–128K context, flash attention | 27B: ~45 → ~110 tok/s with MTP (4090, reported); MoE on GPU: ~180 tok/s (3090, reported) |
| **Mid** | 12–16 GB VRAM (3060 12 GB, 4060 Ti 16 GB, 4070), **32 GB RAM** | a **35B-A3B MoE** at Q4_K_M: **Qwen3.6-35B-A3B** (base), **Empero Qwen3.8-35B-A3B Distill** (shorter thinking), **Ornith-1.5-35B-A3B** (coding-agent tune, MIT) | `--n-cpu-moe N` **chosen by Coworker** from free VRAM, all other layers on GPU, q8 KV, up to 262K context, `-np 1`, flash attention | ~50 tok/s decode, ~550 tok/s prefill at 262K on a 3060 12 GB + 16 GB RAM (community report, Oct 2026) |
| **Low** | 8 GB VRAM, laptops, or CPU only; 16 GB RAM | a **9B dense** model: **Qwen3.5-9B** (today's), **Ornith-1.5-9B** (agent tune); **Gemma 4 E4B** for CPU only | full GPU offload at Q4/Q5, q8 KV, 32K context; CPU: smaller context, fewer threads for the UI | Good single-step tool use; plans kept short (Coworker's plan + stop rules already help small models) |

Notes from the research:
- **The Mid recipe is the big win.** The community config that runs a 35B-A3B on a
  12 GB RTX 3060 (`-ngl 99 --n-cpu-moe 26 -c 262144 -fa on --jinja -np 1
  --cache-type-k q8_0 --cache-type-v q8_0`, temp 0.6 / top-p 0.95 / top-k 20, reasoning
  on) used 16 GB RAM; inside Bforartists we should ask for **32 GB RAM**, because the scene
  and the app need theirs. 16 GB machines get Low.
- **Distill vs base:** an independent test (RTX 3090, Oct 8) found the Empero distill
  thinks ~5× shorter (faster turns) but ignored the system prompt on 6 of 47 routing
  prompts where the base never did. For an agent that must follow our tool format, that
  matters: **bake-off, not hype**.
- **Ornith 1.5** is tuned for coding agents (vendor: SWE-bench Verified 79.0, MCP-Atlas
  70.2); BenchLM's aggregate puts Qwen3.8-27B ahead overall (54 vs 36) with many Ornith
  cells still empty. Blender work is closer to tool-driven agent work than to pure coding;
  our benchmark will tell.
- **2-bit 27Bs** are the ones to watch for Low: PrismML's **Ternary Bonsai 2 27B** (5.9 GB,
  Apache 2.0, ~98% of Qwen3.8-27B on their aggregate, tool-use combo 77.6 vs 79.7) needs
  PrismML's own low-bit kernels, not mainline llama.cpp, so it's a **watch item**, not a
  default. A 2-bit Qwen3.8-27B GGUF (~8.4 GB) did 16/20 on one community agentic benchmark,
  but doesn't leave room for the viewport on an 8 GB card.
- **Vision** matters to Coworker (screens, review sheets, video next): each default must
  ship its `mmproj` file, or we say clearly that it's text-only.
- **Below 7B**, community tool-calling tests report malformed calls past trivial tasks:
  we keep 9B as the floor for Low.

## 4. The bake-off (first week of November)

Our own benchmark decides, on the machines artists actually have:

| What | How |
|---|---|
| Suite | The 6 core benchmarks + 3 quick challenges (Engine 10), plus 3 Blender tasks with screenshots (vision) |
| Machines | **High**: a 24 GB card · **Mid**: a 12 GB card + 32 GB RAM · **Low**: an 8 GB card or a laptop; your machine is the first reference |
| Per candidate | success rate, tool-call format failures, turns to finish, first-token latency, decode and prefill tok/s at 8K / 32K / 128K, peak VRAM and RAM **with a scene open in Eevee** |
| Output | `_misc/benchmarks/local_november.md`: one table per setup; the winner + an alternate |

## 5. What we build in November

| # | Task | What | Size |
|---|---|---|---|
| L1 | **Bake-off** | §4, scripted so it reruns when new models land | S–M |
| L2 | **Launch recipes** | A `LaunchRecipe` per setup and model: offload (`--n-cpu-moe` computed from free VRAM after a viewport reserve), context, KV type, flash attention, `-np 1`, sampling, reasoning switch mapped to Coworker's *Effort*, MTP when the model has heads (`--spec-type draft-mtp`, sidecar draft file). Context presets up to 262K | M |
| L3 | **Fit check** | Before Start: weights + KV + viewport reserve vs VRAM / RAM; adjust context or offload, or say plainly why it won't fit | S |
| L4 | **One-question picker** | Local tab: *Your machine: 12 GB graphics, 32 GB RAM → **Mid** (recommended)*, three cards (what it's good at, download size, speed), one **Download & Start**. Old presets and custom GGUFs move to *Advanced* | M |
| L5 | **Pinned llama.cpp** | Download a tested llama.cpp release (CUDA / Vulkan / CPU / Linux), not "latest"; a test that our flags parse on it; deliberate bumps | S |
| L6 | **Preset cleanup** | Three defaults + alternates; merges and uncensored fine-tunes leave the default list (still usable via *Advanced* / custom GGUF) | S |
| L7 | **Docs + test round** | Wiki *Local LLM Setup* rewritten around the three setups; a short test list | S |

**Order**: L1 → (L2 + L5) → L3 → L4 + L6 → L7. It touches `llm_manager.py`, the Local
part of `preferences.py`, and the benchmark runner, so it can run alongside Threads
(`agent_controller.py` / `ui_chat.py`) without conflicts.

## 6. Done when (end of November)

- [ ] A new artist opens the Local tab, sees their machine's recommended setup, presses one
  button, and chats within the download time, with no other choice to make.
- [ ] Each setup passes the core benchmarks at or above today's best preset on that hardware,
  with the viewport usable (Eevee) during a turn.
- [ ] Mid runs a 35B-A3B on a 12 GB card at ≥ 40 tok/s; High runs ≥ 2× today's 27B speed
  with MTP.
- [ ] The llama.cpp build is pinned and a flag change can't break Start silently.

## 7. Risks

| Risk | Mitigation |
|---|---|
| Fine-tunes ignore the system prompt / tool format | The bake-off counts format failures; base models win ties |
| MTP flags or sidecar formats change again | Pinned llama.cpp (L5); MTP on only when the recipe's test passes |
| `--n-cpu-moe` on 16 GB RAM machines swaps with a heavy scene | Mid asks for 32 GB RAM; the fit check (L3) reserves RAM for Bforartists |
| Model names and numbers here come from a fast-moving landscape | Everything in §3 is a *candidate*; only bake-off winners ship |
| Licences | Defaults must be Apache 2.0 / MIT or similar; record each in the preset; distills inherit questions from their teachers (noted for Empero) |

## 8. Sources (2026-10-30)

- Community config: Qwen3.8-35B-A3B on an RTX 3060 12 GB (Doğukan, X; text supplied by you)
- [Qwen 3.8 35B-A3B Is a Distill: Tested Against Qwen 3.6 (InsiderLLM, Oct 8)](https://insiderllm.com/guides/qwen-3-8-35b-a3b-distill-vs-qwen-3-6-35b/)
- [empero-ai on Hugging Face](https://huggingface.co/models?other=empero-ai)
- [Ornith 1.5 35B agent setup guide (BetterClaw, Sep 11)](https://www.betterclaw.io/blog/ornith-1-5-35b-agent-setup-guide)
- [Ornith-1.5-35B-A3B vs Qwen3.8-27B (BenchLM, Oct 29)](https://benchlm.ai/md/compare/ornith-1-5-35b-a3b-vs-qwen3-8-27b.md) · [Ornith-1.5-9B vs Qwen3.8-27B](https://benchlm.ai/md/compare/ornith-1-5-9b-vs-qwen3-8-27b.md)
- [Bonsai 2 27B (PrismML)](https://prismml.com/news/bonsai-2-27b) · [Doğukan's 2-bit agentic benchmark (X)](https://x.com/DogukanUrker/status/2101259462245789701)
- [Speculative decoding (MTP) nearly tripled Qwen on one 4090 (Pedro Alonso, updated Aug 24)](https://www.pedroalonso.net/blog/qwen-mtp-speculative-decoding-4090/)
- [llama.cpp v0.6.0 and local-model news (Gekro, Oct 6)](https://gekro.com/news/2026-10-06/) · [llama.cpp ships MTP (RunAnywhere, W20)](https://www.runanywhere.ai/newsletter/2026-W20)
- [Best small models for tool calling (PromptQuorum, updated Sep 1)](https://www.promptquorum.com/power-local-llm/best-local-models-tool-calling-2026)
