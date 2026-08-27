# Bforartists Coworker

**AI-powered 3D creation, built right into Blender.**

Coworker is a self-contained Blender add-on that puts a local AI assistant
directly in your 3D viewport. No external tools, no server setup, no Python
environment wrangling -- install the add-on, pick a model, start chatting.

The AI can inspect your scene, write and execute Blender Python code, place
objects, set up materials, configure lighting, animate, render, and more -- all
through natural conversation.

---

## Why Coworker?

- **Zero setup** -- Download llama-server with one click, pick a model preset, start talking. No terminal, no Docker, no API keys required for local use.
- **Runs locally** -- Your scenes never leave your machine. Models run on your own GPU via llama.cpp. No cloud dependency.
- **Actually useful** -- 30+ tools let the AI inspect scenes, execute code, render viewport screenshots, browse assets, and modify your project in real time.
- **Works with what you have** -- Already downloaded a GGUF model? Coworker finds it automatically. Has a HuggingFace token? Paste any model URL to download.
- **Remote API too** -- Prefer a cloud model? OpenRouter, OpenAI, and Anthropic are one dropdown away with automatic URL configuration.

---

## Quick Start

1. **Install** -- Edit > Preferences > Add-ons > Install from Disk > select the .zip
2. **Download llama-server** -- Click the one-click download button in preferences (auto-detects CUDA/Vulkan/CPU)
3. **Pick a model** -- Choose from curated presets organized by your hardware:
   - **Flagship** (24 GB+ VRAM): Qwen3.8-27B, Fable Fusion 27B, Nail 35B
   - **Mid-Range** (16 GB): GPT-OSS 20B *(default)*, Qwen3.8-27B Q4
   - **Lightweight** (<=8 GB): Gemma 4 E4B, Qwen3.5-9B variants
4. **Download & Start** -- One click downloads the model and launches the server
5. **Chat** -- Open the N-panel in the 3D Viewport, find the Coworker tab, and start creating

---

## What Can It Do?

| Area | Examples |
|------|----------|
| **Modeling** | "Create a stonehenge with 8 pillars in a circle" |
| **Materials** | "Apply a weathered stone material to all pillars" |
| **Lighting** | "Set up dramatic three-point lighting" |
| **Animation** | "Make the ball bounce with squash and stretch" |
| **Rendering** | "Render a thumbnail from the active camera" |
| **Scene Management** | "Organize everything into named collections" |
| **Asset Downloads** | "Download a forest HDRI from Polyhaven" |
| **Vision** | "Look at the viewport and reframe the camera for a hero shot" (vision models) |

The AI writes and executes Blender Python code in real time -- you see changes happen live.

---

## Download

| | |
|---|---|
| **Latest Release** | [Download v1.1.37](https://github.com/bforartists/bfa_coworker/releases/latest) |
| **All Releases** | [GitHub Releases](https://github.com/bforartists/bfa_coworker/releases) |
| **Source Code** | [Clone this repo](https://github.com/bforartists/bfa_coworker) |

Requirements: Blender 5.1+ (or Bforartists equivalent), ~4-28 GB RAM depending on model.

---

## Get Involved

Coworker is open source under GPL-3.0. Contributions are welcome -- whether it is
bug reports, feature ideas, model testing, documentation, or code.

- **Report issues** -- [GitHub Issues](https://github.com/bforartists/bfa_coworker/issues)
- **Discuss ideas** -- [GitHub Discussions](https://github.com/bforartists/bfa_coworker/discussions)
- **Read the docs** -- [Wiki](https://github.com/bforartists/bfa_coworker/wiki)
- **See what changed** -- [CHANGELOG](CHANGELOG.md)

---

## How It Works

```
You type in the Chat UI
        |
   Agent Controller talks to Local LLM or Remote API
        |
   MCP Server receives tool calls
        |
   Blender executes the code
        |
   You see the result in your viewport
```

Everything runs inside Blender -- no external processes to manage, no ports to configure.

---

## License

GPL-3.0-or-later -- see [LICENSE](LICENSE).
