# Tier 3b — Smarter llama-server download + model list refresh

## Goal
Make local llama-server hardware-aware (Vulkan/CUDA/CPU selector + auto-detect, unified progress
bars, PATH note, better tooltips) and curate a 9-model preset list (3 flagship / 3 mid / 3 light)
tuned for Blender: good coding + reasoning, low resource use, and image-to-text (vision).

## Key decisions
- Model list: 9 presets (3/3/3), GPT-OSS 20B = DEFAULT. Removed: Gemma 3 12B Vision (#23), Phi-4 14B Q3 (#32).
- Vision: PLAN full implementation — download mmproj, pass --mmproj, send viewport screenshots.
- GPU selector: auto-detect + manual override; CUDA 12.4 only (bundles cudart DLLs).
- Context window: each preset stores native ctx; addon caps at 65536 for consumer GPU safety.

## Curated 9-model list
### Flagship (24 GB+ VRAM)
1. Qwen3.8-27B Q8_0 — unsloth/Qwen3.8-27B-GGUF / Qwen3.8-27B-Q8_0.gguf (29GB) ctx=262144 vision ✓
2. Fable Fusion 27B Q6_K — DavidAU/...NEO-MAX-MTP-GGUF / ...-NEO-Q6_K.gguf (23.6GB) ctx=262144 vision ✓
3. Nail 35B A3B UD-Q4_K_XL — peculiar-ragdoll/Nail-Qwen3.6-35B-A3B-GGUF / ...-UD-Q4_K_XL.gguf (22.4GB) ctx=262144 vision ✓
### Mid (16–20 GB VRAM)
4. GPT-OSS 20B Q4_K_M — unsloth/gpt-oss-20b-GGUF / gpt-oss-20b-Q4_K_M.gguf (11.6GB) ctx=131072 — DEFAULT
5. Qwen3.8-27B Q4_K_M — unsloth/Qwen3.8-27B-GGUF / Qwen3.8-27B-Q4_K_M.gguf (17.1GB) ctx=262144 vision ✓
6. Fable Fusion 27B IQ4_XS — DavidAU/...NEO-IQ4_XS.gguf (16.6GB) ctx=262144 vision ✓
### Lightweight (≤8 GB)
7. Gemma 4 E4B Q4_K_M — unsloth/gemma-4-E4B-it-GGUF / gemma-4-E4B-it-Q4_K_M.gguf (4.98GB) ctx=131072 vision ✓
8. Qwen3.5-9B DeepSeek-V4-Flash Q4_K_M — Jackrong/...-GGUF / ...-Flash-Q4_K_M.gguf (5.63GB) ctx=262144 vision ✓
9. Qwen3.5-9B Q8_0 — unsloth/Qwen3.5-9B-GGUF / Qwen3.5-9B-Q8_0.gguf (9.53GB) ctx=262144 vision ✓

## Phases
### Phase 1 — Model list refresh (llm_manager.py + shared.py + preferences.py)
- Extend ModelPreset with vision, mmproj_filename, hardware_note, why fields
- Rewrite PRESET_MODELS to 9 entries with correct ctx/max_tokens
- Update MODEL_PRESET_ITEMS in shared.py
- Update preferences.py defaults to GPT-OSS

### Phase 2 — GPU backend selector (preferences.py, llm_manager.py, operators_llm.py)
- Add llama_backend to LLMConfig + EnumProperty
- Add _detect_gpu_backend()
- Extend download_llama_server(backend=...) for CUDA/Vulkan/CPU variants
- Update find_llama_server() to prefer backend binary
- Add --n-gpu-layers in start_local_llama()

### Phase 3 — Unified download progress bars (llm_manager.py, preferences.py)
- Add download_kind to LLMState
- Replace string-match progress logic with single progress block

### Phase 4 — Vision pipeline (llm_manager.py, agent_controller.py)
- Download mmproj alongside model
- Pass --mmproj in start_local_llama()
- Convert screenshot tool results to image_url content blocks

### Phase 5 — UI polish + PATH note + docs
- Multiline model labels with single icon
- Tooltips with hardware + GPU gen suggestions
- PATH instructions in tooltips
- Update CHANGELOG, SKILL.md, wiki

## Files to edit
- addon/bfa_coworker/llm_manager.py — ModelPreset, PRESET_MODELS, LLMConfig/State, find_llama_server, download_llama_server, download_model, start_local_llama
- addon/bfa_coworker/shared.py — MODEL_PRESET_ITEMS
- addon/bfa_coworker/preferences.py — llama_backend prop, defaults, _draw_tab_local_llm
- addon/bfa_coworker/operators_llm.py — _BFACW_OT_download_llama_server
- addon/bfa_coworker/agent_controller.py — _parse_tool_result, run_conversation_turn
