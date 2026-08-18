#########
Local LLM
#########

Prerequisites
=============

- This repository.
- Blender 5.1+ (or Bforartists equivalent).

``llama.cpp`` is optional — the add-on can auto-download ``llama-server``
for you from the preferences UI (see `Auto-Download`_ below).


Quick Start (Self-Contained)
============================

The add-on is fully self-contained — no command line, no Docker, no separate
Python installs needed.

1. Open Blender → **Edit → Preferences → Add-ons** → find **Coworker**.
2. In the **LLM Configuration** section, set mode to **Local**.
3. If you see **"llama-server: Not installed"**, click **Download llama-server**.
4. Pick a model preset (Flagship / Mid-Range / Lightweight) and click
   **Download & Start**. The model downloads directly from HuggingFace with
   a real-time progress bar showing percentage, speed, and ETA.
5. In the **Agent Control** section, click **Start Agent**.
6. Open the **3D Viewport sidebar** (press ``N`` if hidden) and find the
   **Coworker** tab. Type your message and press **Send**.

That's it. No manual ``llama.cpp`` install, no PATH setup, no separate
chat client.


Troubleshooting
===============

``llama-server`` crashes or never becomes ready
   The add-on writes llama-server's full output to
   ``~/.cache/bfa_coworker_llama/llama-server.log`` and automatically
   surfaces the last lines when startup fails — check that file (or the
   chat status bar) for the real error.  Common causes:

   * **Truncated/corrupt model file** — the most common cause with local
     files: a GGUF cut off mid-download or mid-copy makes llama-server die
     with ``missing tensor ...`` after loading a few dozen layers.  The
     add-on compares your file's size against HuggingFace and suggests
     re-downloading when it detects this.  Delete the file and re-download.
   * **Outdated llama-server build** — the curated presets (Qwen3.8,
     Qwen3.6 fine-tunes, ...) need a recent llama.cpp.  Prefer the bundled
     **Download llama-server** button over an older PATH/WinGet install;
     the add-on logs the detected build version at launch.
   * **Mismatched mmproj** — several vision presets share the generic
     ``mmproj-F16.gguf`` filename, so when multiple models live in one
     folder a single projector can end up attached to the wrong model and
     llama-server exits with ``mismatch between text model ... and mmproj``.
     The add-on now saves each model's projector under its own name
     (``mmproj-F16-Qwen3.5-9B.gguf``), only uses a generic projector when it
     is unambiguous, and runs vision models text-only instead of crashing
     when no matching projector is found.  Fix stray files by deleting them
     and re-downloading via the **Download & Start** button.
   * **GPU out of memory** — with ``--n-gpu-layers 99`` the weights *and*
     the KV cache go into VRAM, so a GPU without enough free memory dies
     mid-load (often as a crash with exit code ``0xC0000005`` = access
     violation).  The log shows ``ggml_vulkan ... ErrorOutOfDeviceMemory``
     or ``CUDA error: out of memory`` and the add-on now explains it and
     suggests fixes: lower **Context Size** (64K context on a 27B model
     needs many GB of KV-cache memory; 16K-32K is plenty for agent work),
     lower ``--n-gpu-layers`` so part of the model stays in RAM, or switch
     the backend to CUDA (with the bundled **Download llama-server**
     button) if you have an NVIDIA GPU — the WinGet/PATH build is Vulkan,
     which is often less memory-efficient and may pick an integrated GPU
     on laptops.


Components
==========

Before going into details, here are the components which will need to run
with a brief explanation of what they do.

``llama.cpp`` (external project)
   Runs the LLM model.

``bfa-coworker-mcp``: ``./mcp/``
   The MCP server that provides tools and connects the agent controller
   to the add-on's bridge server.

``bfa_coworker`` add-on: ``./addon/bfa_coworker/``
   The Blender add-on containing:
   - ``llm_manager.py`` — LLM lifecycle (download, start/stop, health check)
   - ``agent_controller.py`` — Conversation orchestrator (MCP client + LLM API calls)
   - ``ui_chat.py`` — In-Blender chat panel (3D Viewport + Text Editor)
   - ``mcp_to_blender_server.py`` — TCP socket bridge server
   - ``preferences.py`` — Add-on preferences with all configuration

``chat_client``: ``./chat_client/chat_client.py`` (optional/legacy)
   A simple text mode chat client for testing. Not needed for normal use.

``blender``: (external project)
   An instance of Blender running the ``bfa_coworker`` add-on,
   this can run in background or with a GUI.

   For the purpose of this document, we assume a graphical session.


Auto-Download (easiest)
=======================

The add-on preferences include a **"Download llama-server"** button that
downloads the latest ``llama-server`` binary from GitHub releases and
unpacks it to ``~/.cache/bfa_coworker_llama/``.

1. Open Blender → **Edit → Preferences → Add-ons** → find **Coworker**.
2. In the **LLM Configuration** section, set mode to **Local**.
3. If you see **"llama-server: Not installed"**, click **Download llama-server**.
4. Once installed, pick a model preset and click **Download & Start**.

No command line, no manual PATH setup, no separate ``llama.cpp`` install.

The download uses direct HTTP streaming in 64 KB chunks with real-time
progress (percentage, speed, ETA, visual progress bar). If the model
requires authentication, set your **HuggingFace Token** in the Advanced
section. The add-on also checks the ``HF_TOKEN`` and
``HUGGINGFACE_TOKEN`` environment variables.

You can cancel an in-progress download at any time — partial files are
cleaned up automatically.


Manual Setup
============

Using ``virtualenv`` is optional but assumed in the instructions below.

Create & Activate a Virtual Environment:
   .. code-block::

      virtualenv .venv -p python
      source .venv/bin/activate

Install Requirements & MCP Server:
   .. code-block::

      pip install -r mcp/requirements.txt
      pip install -e mcp

   Check that ``bfa-coworker-mcp`` runs (it will do nothing, press Ctrl-C to exit).

Build the Blender Extension:
   .. code-block::

      blender -c extension build --source-dir ./addon/bfa_coworker
      blender -c extension install-file bfa_coworker-1.1.36.zip --repo=user_default

Download a Model via LLAMA.cpp:
   .. code-block::

      # This will download AND run the model, running afterwards will use the cache.

      # These are just examples.
      export HF_REPO="HeYujie/Qwen3.5-35B-A3B-abliterated-GGUF"
      export HF_FILE="Qwen3.5-35B-A3B-abliterated-Q8_0.gguf"

      llama-server --jinja --hf-repo $HF_REPO --hf-file $HF_FILE

   You can visit ``http://127.0.0.1:8080/`` to check this is working.


Alternative Usage (Advanced)
============================

The primary workflow is the self-contained chat UI inside Blender (see
`Quick Start`_ above). The following are alternative/legacy methods for
testing and advanced use cases.


Text Mode Client
----------------

Ensure the ``llama-server`` is running:
   .. code-block::

      llama-server --jinja --hf-repo $HF_REPO --hf-file $HF_FILE

Run the chat client:
   Activate the virtual-environment (if you haven't already).

   .. code-block::

      python chat_client/chat_client.py openai --api-url http://localhost:8080

   Note that this will start ``bfa-coworker-mcp``.

Run Blender:
   Ensure "Online Access" is enabled.

   In the Add-on preferences, check the "MCP Bridge Server" is running,
   if not - start it.


LLAMA.CPP Web UI
----------------

Ensure the ``llama-server`` is running:
   .. code-block::

      llama-server --jinja --hf-repo $HF_REPO --hf-file $HF_FILE

Run ``bfa-coworker-mcp`` with HTTP enabled:
   Activate the virtual-environment (if you haven't already).

   .. code-block::

      bfa-coworker-mcp --transport http --port 9191

Connect LLAMA.CPP to ``bfa-coworker-mcp``:
   - Open ``http://127.0.0.1:8080/`` in a web browser - you should see a chat prompt.
   - Click on the "Cog" icon at the top right.
   - Click on the "MCP" section in the settings.
   - Click on the "Add New Server" button.
   - Enter ``http://127.0.0.1:9191/`` for the "Server URL".
   - Click on "Update", you should see server instructions, tools ... etc.
   - Enable the MCP server (a switch at the top right).

     NOTE: for each new chat, you will need to enable ``bfa-coworker-mcp`` from the web UI.

Run Blender:
   Ensure "Online Access" is enabled.

   In the Add-on preferences, check the "MCP Bridge Server" is running,
   if not - start it.
