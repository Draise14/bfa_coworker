# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Agent Controller — orchestrates the conversation loop inside Blender.

Manages the MCP server subprocess and the LLM conversation
loop. All async I/O runs on a background daemon thread and communicates
results back via ``bpy.app.timers`` for Blender UI integration.
"""

__all__ = (
    "AgentState",
    "ensure_event_loop",
    "schedule_coro",
    "start_mcp_server",
    "start_mcp_server_network",
    "stop_mcp_server",
    "list_mcp_tools",
    "run_conversation_turn",
    "cleanup",
    "ping_agent",
    "check_ports_available",
    "migrate_vendor_deps",
    "generate_mcp_client_config",
)

import asyncio
import concurrent.futures
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import textwrap


# ---------------------------------------------------------------------------
# Constants

_MCP_SERVER_DEFAULT_PORT = 9191
_MCP_SERVER_HEALTH_URL = "http://127.0.0.1:{:d}/health"
_MCP_TOOLS_URL = "http://127.0.0.1:{:d}/tools/list"
_LLM_CHAT_URL = "http://127.0.0.1:{:d}/v1/chat/completions"
_MAX_TOOL_ITERATIONS = 8
_STREAM_TIMEOUT = 600.0

# Maximum conversation history messages to send per turn.
# Huge history balloons the prompt and makes small models loop.
_MAX_HISTORY_MESSAGES = 20

# ---------------------------------------------------------------------------
# System prompt (loaded lazily)

_system_prompt: str | None = None


def _get_system_prompt() -> str:
    """Load the system prompt from the MCP's prompts.yml, with a local cache."""
    global _system_prompt
    if _system_prompt is not None:
        return _system_prompt

    # Search for prompts.yml relative to this file's location.
    # Typical layout: addon/bfa_coworker/agent_controller.py
    # and            mcp/blmcp/data/prompts.yml
    this_dir = Path(__file__).resolve().parent
    candidates = [
        this_dir.parent.parent / "mcp" / "blmcp" / "data" / "prompts.yml",
    ]
    for prompt_path in candidates:
        if prompt_path.is_file():
            try:
                with open(str(prompt_path), encoding="utf-8") as fh:
                    raw = fh.read()
                # Parse single-key YAML with literal block scalar (|) without yaml lib.
                # Format: "initial_instructions: |\n  indented text..."
                marker = "initial_instructions: |"
                if marker in raw:
                    _, _, body = raw.partition(marker)
                    _system_prompt = textwrap.dedent(body).strip()
                if _system_prompt:
                    print("[🛠️Coworker] _get_system_prompt: loaded {:d} chars from {:s}".format(
                        len(_system_prompt), str(prompt_path)))
                    return _system_prompt
            except Exception as ex:  # pylint: disable=broad-exception-caught
                print("[🛠️Coworker] _get_system_prompt: error loading {:s}: {:s}".format(
                    str(prompt_path), str(ex)))

    # Fallback: a brief built-in system prompt.
    _system_prompt = (
        "You are a Blender automation assistant. "
        "You have access to tools that can execute Python code in Blender. "
        "Think aloud in full paragraphs. Explain your reasoning step by step. "
        "Summarize tool results in a few words. Avoid fluff and polite filler. "
        "Execute code to complete the user's request, "
        "then respond with a brief summary of what was done."
    )
    return _system_prompt


def _get_system_prompt_with_rules() -> str:
    """Return the system prompt with skills, project rules, and version info."""
    base = _get_system_prompt()
    try:
        import bpy  # pylint: disable=import-error

        # ── Blender version announcement ──────────────────────
        version_str = ".".join(str(v) for v in bpy.app.version[:3])
        version_header = (
            "You are connected to Blender {:s}. "
            "All code you write must be compatible with this version.\n\n"
            "STYLE: Think aloud in full paragraphs. Explain your reasoning step by step — "
            "what you observe, what you plan to do, and why. The user should be able to "
            "follow your thought process. Be thorough but not repetitive. "
            "When reporting tool results, be brief — just state what happened and whether "
            "it succeeded."
        ).format(version_str)

        # ── Built-in skills (version-aware, from addon/skills/) ──
        try:
            from . import skills as _skills_mod  # pylint: disable=import-error
            # Get user custom skills text from preferences.
            custom_text = ""
            try:
                prefs = bpy.context.preferences.addons[__package__].preferences
                if hasattr(prefs, "custom_skills_text"):
                    custom_text = prefs.custom_skills_text or ""
            except Exception:
                pass
            skills_block = _skills_mod.get_always_loaded_skills(
                bpy_version=bpy.app.version,
                custom_text=custom_text,
            )
        except Exception:
            skills_block = ""

        # ── Project rules (user .md files) ────────────────────
        rules_dir = Path(bpy.utils.user_resource("SCRIPTS")) / "bfa_coworker_rules"
        rules_parts = []
        global_rules = rules_dir / "global.md"
        if global_rules.exists():
            rules_parts.append(global_rules.read_text(encoding="utf-8"))
        if bpy.data.filepath:
            stem = Path(bpy.data.filepath).stem
            blend_rules = rules_dir / "{:s}.md".format(stem)
            if blend_rules.exists():
                rules_parts.append(blend_rules.read_text(encoding="utf-8"))

        # ── Assemble ──────────────────────────────────────────
        parts: list[str] = [version_header]

        if skills_block:
            parts.append("## Built-in Skills\n{:s}".format(skills_block))

        if rules_parts:
            rules_text = "\n\n".join(rules_parts)
            parts.append(
                "## Project Rules\n"
                "The following project rules MUST be followed:\n\n"
                "{:s}".format(rules_text)
            )

        parts.append("## Instructions\n{:s}".format(base))
        return "\n\n".join(parts)
    except Exception:
        pass
    return base


def _clear_system_prompt_cache() -> None:
    """Clear the cached system prompt and skills so they're rebuilt on next call."""
    global _system_prompt
    _system_prompt = None
    try:
        from . import skills as _skills_mod  # pylint: disable=import-error
        _skills_mod.clear_cache()
    except Exception:
        pass


def _drop_orphaned_tool_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Remove ``tool``-role messages that have no preceding ``assistant``
    message with ``tool_calls``.  This is a safety fix: slicing a
    conversation history can break tool-call pairs, and llama-server
    ``--jinja`` will throw a hard error when it encounters an orphaned
    tool message.
    """
    cleaned: list[dict[str, Any]] = []
    for msg in messages:
        if msg.get("role") == "tool":
            # Find the preceding assistant message IN THE CLEANED LIST
            # (i.e. what we are sending to the LLM).
            has_pair = any(
                p.get("role") == "assistant" and p.get("tool_calls")
                for p in reversed(cleaned)
            )
            if not has_pair:
                # Drop this orphaned tool message.
                continue
        cleaned.append(msg)
    return cleaned


# ---------------------------------------------------------------------------
# SSE (Server-Sent Events) parser
# FastMCP in stateless_http mode returns SSE streams even for
# non-streaming JSON-RPC requests.  This extracts JSON payloads
# from each ``data:`` line.

def _parse_sse_json(raw: str) -> dict[str, Any] | None:
    """
    Parse the first JSON payload from an SSE (text/event-stream) body.

    Returns the parsed ``data:`` field as a dict, or ``None`` if no
    valid payload is found.
    """
    for line in raw.splitlines():
        if line.startswith("data: "):
            try:
                return json.loads(line[6:])
            except json.JSONDecodeError:
                continue
    return None


def _parse_sse_text_response(raw: str) -> str:
    """
    Parse SSE body for a tool result, extracting text content blocks.

    Handles both ``type: "text"`` and ``type: "image"`` content blocks.
    For images, returns a descriptive message so the LLM knows the
    screenshot was captured (the image data is not passed to the LLM
    via this path — it goes through the MCP ``Image`` return type).
    """
    result = _parse_sse_json(raw)
    if result is None:
        return "Error: empty or unparseable SSE response"
    if "error" in result:
        return "Error: {:s}".format(str(result["error"]))
    content = result.get("result", {}).get("content", [])
    texts = []
    has_image = False
    for block in content:
        if isinstance(block, dict):
            block_type = block.get("type", "")
            if block_type == "text":
                texts.append(block.get("text", ""))
            elif block_type in ("image", "image/png", "image/jpeg", "image/webp"):
                has_image = True
    if texts:
        return "\n".join(texts)
    if has_image:
        return "Screenshot captured successfully (image data returned to LLM)"
    return "Error: no text content in tool result"


# ---------------------------------------------------------------------------
# Data types

@dataclass
class AgentState:
    """Runtime state of the agent controller."""

    mcp_server_running: bool = False
    llm_connected: bool = False
    is_thinking: bool = False
    status_text: str = "Idle"
    error: str = ""
    tool_count: int = 0  # Number of MCP tools available (0 = not loaded yet)
    conversation_history: list[dict[str, Any]] = field(default_factory=list)
    streaming_text: str = ""
    reasoning_text: str = ""  # Chain-of-thought from reasoning models
    thinking_dots: int = 0  # Animated spinner state (0-3)

    # ── Liveness tracking (Tier 1) ─────────────────────────────────
    last_bridge_activity: float = 0.0
    last_mcp_activity: float = 0.0
    last_llm_activity: float = 0.0
    bridge_live: bool = False
    mcp_live: bool = False
    llm_live: bool = False


_agent_state = AgentState()

# Set to request the in-flight conversation turn to abort. The conversation
# loop checks this between iterations and inside the LLM request path.
_stop_event = threading.Event()


def request_stop() -> None:
    """Request the current generation to stop as soon as possible."""
    print("[🛠️Coworker] request_stop: stop requested")
    _stop_event.set()
    _agent_state.is_thinking = False


def clear_stop() -> None:
    """Clear the stop flag before starting a new turn."""
    _stop_event.clear()


# ---------------------------------------------------------------------------
# Async event loop (background thread)

_loop: asyncio.AbstractEventLoop | None = None
_loop_thread: threading.Thread | None = None


def _run_async_loop(loop: asyncio.AbstractEventLoop) -> None:
    asyncio.set_event_loop(loop)
    loop.run_forever()


def ensure_event_loop() -> asyncio.AbstractEventLoop:
    """Get or create the background async event loop."""
    global _loop, _loop_thread
    if _loop is None or not _loop.is_running():
        _loop = asyncio.new_event_loop()
        _loop_thread = threading.Thread(target=_run_async_loop, args=(_loop,), daemon=True)
        _loop_thread.start()
    return _loop


def schedule_coro(coro) -> concurrent.futures.Future:
    """Schedule a coroutine on the background event loop and return a Future."""
    loop = ensure_event_loop()
    return asyncio.run_coroutine_threadsafe(coro, loop)


# ---------------------------------------------------------------------------
# MCP server subprocess management

_mcp_server_process: subprocess.Popen | None = None
_mcp_launch_retry_count: int = 0
_mcp_shutting_down: bool = False

def _get_vendor_deps_dir() -> Path:
    """Return the cache directory for vendored Python dependencies.

    Returns ``~/.cache/bfa_coworker/vendor_deps/``, creating the directory
    if needed.  On first call, migrates any existing ``vendor/deps/`` from
    the legacy addon-relative location into the cache — this removes the
    directory from the addon tree so Blender's sandbox no longer scans it.
    """
    cache = Path.home() / ".cache" / "bfa_coworker" / "vendor_deps"

    # Migration: if the old addon-relative vendor/deps/ still exists,
    # move it to the cache location now.
    legacy = Path(__file__).resolve().parent / "vendor" / "deps"
    if legacy.is_dir() and not cache.is_dir():
        print("[🛠️Coworker] _get_vendor_deps_dir: migrating legacy vendor/deps/ to {:s}".format(str(cache)))
        cache.parent.mkdir(parents=True, exist_ok=True)
        try:
            legacy.rename(cache)
            print("[🛠️Coworker] _get_vendor_deps_dir: migration successful — removed from addon tree")
        except OSError:
            # Rename may fail across filesystems — fall back to copy.
            print("[🛠️Coworker] _get_vendor_deps_dir: rename failed, copying instead...")
            import shutil as _shutil
            _shutil.copytree(str(legacy), str(cache))
            _shutil.rmtree(str(legacy), ignore_errors=True)
            print("[🛠️Coworker] _get_vendor_deps_dir: copy+remove successful")
    elif not cache.is_dir():
        cache.mkdir(parents=True, exist_ok=True)

    return cache


def migrate_vendor_deps() -> None:
    """Eagerly migrate vendor/deps/ out of the addon tree if present.

    Called from ``__init__.py`` during ``register()``, before any sandbox
    scan might detect the vendored top-level packages.
    """
    _get_vendor_deps_dir()


def _find_blender_python() -> str | None:
    """Return the path to Blender's bundled Python executable.

    Blender ships with its own Python interpreter.  On Windows the Python
    binary lives at ``{sys.prefix}/bin/python.exe``; on Linux/macOS it is
    ``{sys.prefix}/bin/python3``.

    We do **not** use ``sys.executable`` here because in Blender's embedded
    Python that points to the Blender executable (``blender.exe``), not a
    Python interpreter.

    Returns ``None`` if no suitable Python is found (unlikely in a running
    Blender add-on, but handled gracefully).
    """
    if sys.platform == "win32":
        # Standard Blender layout: sys.prefix/bin/python.exe
        py_path = Path(sys.prefix) / "bin" / "python.exe"
        if py_path.is_file():
            return str(py_path)
        # Some installations put python.exe directly in sys.prefix.
        py_path = Path(sys.prefix) / "python.exe"
        if py_path.is_file():
            return str(py_path)
        return None

    # Linux/macOS
    py_path = Path(sys.prefix) / "bin" / "python3"
    return str(py_path) if py_path.is_file() else None


def _find_vendor_pythonpath() -> str:
    """Build a PYTHONPATH string pointing at the addon's vendor directories.

    Returns a ``os.pathsep``-joined string suitable for the ``PYTHONPATH``
    environment variable.  The returned path includes:

    * ``~/.cache/bfa_coworker/vendor_deps/`` — pip-installed pure-Python
      dependencies (mcp, pyyaml, docutils, and their transitive deps).
    * ``vendor/`` — parent of ``vendor/blmcp/``, so ``import blmcp``
      resolves to ``vendor/blmcp/__init__.py``.

    If a directory does not exist, it is silently omitted so the addon
    can fall back gracefully during development.
    """
    this_dir = Path(__file__).resolve().parent
    vendor_dir = this_dir / "vendor"
    parts: list[str] = []

    deps_dir = _get_vendor_deps_dir()
    if deps_dir.is_dir():
        parts.append(str(deps_dir))

        # pywin32 layout: the importable ``pywintypes``/``pythoncom`` modules
        # live in ``win32/lib/`` and are normally exposed via a ``pywin32.pth``
        # file.  ``.pth`` files are only processed for real site-packages
        # directories at interpreter startup — NOT for PYTHONPATH entries.
        # Since the MCP subprocess only gets these dirs via PYTHONPATH, the
        # .pth is ignored, so we must add the pywin32 subdirectories directly.
        for sub in ("win32", "win32/lib", "win32com", "win32comext"):
            sub_dir = deps_dir / sub
            if sub_dir.is_dir():
                parts.append(str(sub_dir))

    # Add vendor/ itself so blmcp resolves from vendor/blmcp/.
    if vendor_dir.is_dir():
        parts.append(str(vendor_dir))

    return os.pathsep.join(parts)


def _ensure_vendor_deps() -> bool:
    """Check that vendor deps exist with required packages; auto-install if missing.

    Handles the case where a user installs the addon from source
    (e.g. by copying the addon directory) without running ``build_addon.py``
    first.  If the vendor deps cache is missing or empty, we attempt to install
    the required packages using Blender's ``pip``.

    Returns ``True`` if the deps are available (or were installed), ``False``
    if installation failed.
    """
    this_dir = Path(__file__).resolve().parent
    deps_dir = _get_vendor_deps_dir()

    # Quick check: does the cache exist and contain mcp?
    if deps_dir.is_dir() and (deps_dir / "mcp" / "__init__.py").is_file():
        return True

    print("[🛠️Coworker] _ensure_vendor_deps: vendor deps cache is missing or empty — attempting auto-install...")

    # Try to install using Blender's pip.
    blender_py = _find_blender_python()
    if not blender_py:
        print("[🛠️Coworker] _ensure_vendor_deps: cannot find Blender's Python for auto-install")
        return False

    # Bootstrap pip if needed (ensurepip is stdlib, always available).
    try:
        subprocess.run(
            [blender_py, "-m", "ensurepip", "--upgrade"],
            capture_output=True, text=True, timeout=60,
        )
    except Exception:  # pylint: disable=broad-exception-caught
        pass  # pip may already be installed.

    try:
        deps_dir.mkdir(parents=True, exist_ok=True)
        pip_packages = ["mcp[cli]>=1.2.0,<2.0.0", "pyyaml", "docutils"]
        if sys.platform == "win32":
            pip_packages.append("pywin32")
        result = subprocess.run(
            [blender_py, "-m", "pip", "install",
             "--target", str(deps_dir),
             "--no-compile",
             ] + pip_packages,
            capture_output=True,
            text=True,
            timeout=180,
        )
        if result.returncode != 0:
            print("[🛠️Coworker] _ensure_vendor_deps: pip install failed (exit {:d})".format(
                result.returncode))
            print("[🛠️Coworker] _ensure_vendor_deps: stderr = {:s}".format(result.stderr[-2000:] or "(empty)"))
            print("[🛠️Coworker] _ensure_vendor_deps: stdout = {:s}".format(result.stdout[-2000:] or "(empty)"))
            return False
        # Verify that the critical import actually works.
        blender_py_verify = _find_blender_python()
        if blender_py_verify:
            vendor_pp = _find_vendor_pythonpath()
            verify_env = os.environ.copy()
            if vendor_pp:
                verify_env["PYTHONPATH"] = vendor_pp
            # On Windows, pywin32 DLLs must be on PATH for import verification.
            if sys.platform == "win32":
                pywin32_system32 = _get_vendor_deps_dir() / "pywin32_system32"
                if pywin32_system32.is_dir():
                    verify_env["PATH"] = str(pywin32_system32) + os.pathsep + verify_env.get("PATH", "")
            verify = subprocess.run(
                [blender_py_verify, "-c", "import mcp.server.fastmcp"],
                capture_output=True, text=True, timeout=30, env=verify_env,
            )
            if verify.returncode != 0:
                print("[🛠️Coworker] _ensure_vendor_deps: post-install import verification FAILED")
                print("[🛠️Coworker] _ensure_vendor_deps: verify stderr = {:s}".format(
                    verify.stderr[-1500:] or "(empty)"))
                return False
            print("[🛠️Coworker] _ensure_vendor_deps: post-install import verification OK")
        # Clean __pycache__ to save space.
        for root, dirs, _files in os.walk(str(deps_dir)):
            if '__pycache__' in dirs:
                shutil.rmtree(os.path.join(root, '__pycache__'), ignore_errors=True)
        print("[🛠️Coworker] _ensure_vendor_deps: auto-install succeeded")
        return True
    except Exception as ex:
        print("[🛠️Coworker] _ensure_vendor_deps: auto-install failed — {:s}".format(str(ex)))
        return False


def _start_pipe_drainer(proc: subprocess.Popen) -> tuple[list[threading.Thread], list[str], list[str]]:
    """Spawn background threads to drain stdout/stderr pipes.

    Without this, ``subprocess.PIPE`` buffers (4 KB on Windows) fill up
    and the child process blocks on write, never reaching ``mcp.run()``.
    Collected lines are appended to the returned lists for diagnostics.

    Returns ``(drainer_threads, stdout_lines, stderr_lines)``.
    """
    stdout_lines: list[str] = []
    stderr_lines: list[str] = []
    lock = threading.Lock()
    threads: list[threading.Thread] = []

    def _drain_stderr() -> None:
        if proc.stderr is None:
            return
        for line in proc.stderr:
            decoded = line.decode(errors="replace").rstrip("\n\r")
            with lock:
                stderr_lines.append(decoded)

    def _drain_stdout() -> None:
        if proc.stdout is None:
            return
        for line in proc.stdout:
            decoded = line.decode(errors="replace").rstrip("\n\r")
            with lock:
                stdout_lines.append(decoded)

    t1 = threading.Thread(target=_drain_stderr, daemon=True)
    t1.start()
    threads.append(t1)

    t2 = threading.Thread(target=_drain_stdout, daemon=True)
    t2.start()
    threads.append(t2)

    return threads, stdout_lines, stderr_lines


def _kill_process_on_port(port: int) -> None:
    """Kill any process listening on *port* (platform-independent)."""
    if sys.platform == "win32":
        try:
            result = subprocess.run(
                ["netstat", "-ano"],
                capture_output=True, text=True, timeout=10,
            )
            for line in result.stdout.splitlines():
                if ":{} ".format(port) in line and "LISTENING" in line:
                    parts = line.strip().split()
                    pid = parts[-1]
                    subprocess.run(
                        ["taskkill", "/f", "/pid", pid],
                        capture_output=True, timeout=5,
                    )
                    print("[🛠️Coworker] _kill_process_on_port: killed PID {:s} on port {:d}".format(pid, port))
                    break
        except Exception:  # pylint: disable=broad-exception-caught
            pass
    else:
        try:
            subprocess.run(
                ["fuser", "-k", "{:d}/tcp".format(port)],
                capture_output=True, timeout=10,
            )
        except Exception:  # pylint: disable=broad-exception-caught
            pass


def _wait_for_port(
    host: str,
    port: int,
    timeout: float = 15.0,
    interval: float = 1.0,
) -> bool:
    """Wait for *port* to start accepting TCP connections.

    Polls ``socket.create_connection`` every *interval* seconds, up to
    *timeout* total.  Returns ``True`` as soon as the port accepts,
    ``False`` if the timeout expires.
    """
    import time
    deadline = time.monotonic() + timeout
    attempt = 0
    while time.monotonic() < deadline:
        attempt += 1
        try:
            with socket.create_connection((host, port), timeout=0.5):
                elapsed = timeout - (deadline - time.monotonic())
                print("[🛠️Coworker] _wait_for_port: {:s}:{:d} ready after {:.1f}s".format(
                    host, port, elapsed))
                return True
        except (OSError, socket.error):
            pass
        if attempt % 2 == 0:
            print("[🛠️Coworker] _wait_for_port: still waiting for {:s}:{:d} ({:.0f}s remaining)".format(
                host, port, deadline - time.monotonic()))
        time.sleep(interval)
    print("[🛠️Coworker] _wait_for_port: TIMEOUT — {:s}:{:d} not ready after {:.1f}s".format(
        host, port, timeout))
    return False


def check_ports_available(
    bridge_port: int = 9876,
    mcp_port: int = 9191,
    llm_port: int = 8081,
) -> dict[str, bool]:
    """Test whether each port is available (not in use) by attempting to bind.

    Returns ``{port_label: is_available, ...}``.
    """
    result: dict[str, bool] = {}
    for label, p in [("bridge", bridge_port), ("mcp", mcp_port), ("llm", llm_port)]:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            if sys.platform == "win32":
                s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            s.bind(("127.0.0.1", p))
            s.close()
            result[label] = True
        except (OSError, socket.error) as ex:
            s.close()
            result[label] = False
            print("[🛠️Coworker] check_ports_available: {:s} port {:d} is in use — {:s}".format(
                label, p, str(ex)))
    return result


def _resolve_mcp_python() -> tuple[str | None, bool]:
    """Resolve the Python executable and whether to use ``-m blmcp``.

    Resolution order:
    1. ``bfa-coworker-mcp`` console_scripts entry point (if user has it on PATH).
    2. Blender's bundled Python (``sys.prefix/bin/python.exe``) with
       ``vendor/deps/`` and ``vendor/blmcp/`` on ``PYTHONPATH``.
    3. ``python`` from PATH as a last resort.

    Returns ``(python_path, use_module)`` where *use_module* is True
    when the MCP server should be launched via ``python -m blmcp``.
    """
    mcp_exe: str | None = None
    use_module = False

    # 1. Check for a pip-installed console_scripts entry point.
    mcp_exe = (
        shutil.which("bfa-coworker-mcp") or
        shutil.which("bfa-coworker-mcp.exe") or
        shutil.which("bfa-coworker-mcp.bat")
    )

    # 2. Fall back to Blender's bundled Python with vendor deps.
    if not mcp_exe:
        if not _ensure_vendor_deps():
            _agent_state.error = (
                "MCP server dependencies not found in vendor deps cache. "
                "Run 'python build_addon.py' to build the extension, "
                "or install manually: pip install --target ~/.cache/bfa_coworker/vendor_deps/ mcp[cli] pyyaml docutils"
            )
            return (None, False)

        blender_py = _find_blender_python()
        if blender_py:
            mcp_exe = blender_py
            use_module = True
            print("[🛠️Coworker] _resolve_mcp_python: using Blender's Python at {:s}".format(mcp_exe))

    # 3. Last resort: system python.
    if not mcp_exe:
        mcp_exe = shutil.which("python") or "python"
        use_module = True
        print("[🛠️Coworker] _resolve_mcp_python: falling back to system python at {:s}".format(mcp_exe))

    return (mcp_exe, use_module)


def _build_mcp_env(
    blender_host: str = "localhost",
    blender_port: int = 9876,
) -> dict[str, str]:
    """Build environment dict for the MCP server subprocess.

    Sets ``BFACW_HOST``, ``BFACW_PORT``, and configures ``PYTHONPATH``
    with vendor directories when using Blender's Python.
    """
    env = os.environ.copy()
    env["BFACW_HOST"] = blender_host
    env["BFACW_PORT"] = str(blender_port)

    # Build PYTHONPATH from vendor directories.
    vendor_pythonpath = _find_vendor_pythonpath()
    existing_pp = env.get("PYTHONPATH", "")
    if vendor_pythonpath:
        env["PYTHONPATH"] = vendor_pythonpath + (os.pathsep + existing_pp if existing_pp else "")

    # On Windows, pywin32 needs its _system32/ DLL directory on PATH.
    if sys.platform == "win32":
        pywin32_system32 = _get_vendor_deps_dir() / "pywin32_system32"
        if pywin32_system32.is_dir():
            env["PATH"] = str(pywin32_system32) + os.pathsep + env.get("PATH", "")

    return env


def start_mcp_server(
    port: int = _MCP_SERVER_DEFAULT_PORT,
    blender_host: str = "localhost",
    blender_port: int = 9876,
    _retry_depth: int = 0,
) -> subprocess.Popen | None:
    """
    Launch the MCP server as a subprocess with HTTP transport.

    Python resolution order:
    1. ``bfa-coworker-mcp`` console_scripts entry point (if user has it on PATH).
    2. Blender's bundled Python (``sys.prefix/bin/python.exe``) with
       ``vendor/deps/`` and ``vendor/blmcp/`` on ``PYTHONPATH``.
    3. ``python`` from PATH as a last resort.

    *``_retry_depth``* is an internal parameter to cap dependency reinstall
    retries at 1 to prevent infinite recursion when imports keep failing.

    Returns the ``Popen`` handle, or ``None`` on failure.
    """
    global _mcp_server_process, _mcp_launch_retry_count, _mcp_shutting_down

    if _mcp_shutting_down:
        print("[🛠️Coworker] start_mcp_server: shutdown in progress — skipping launch")
        return None

    # Kill existing process if known.
    if _mcp_server_process is not None:
        try:
            _mcp_server_process.terminate()
            _mcp_server_process.wait(timeout=3)
        except Exception:  # pylint: disable=broad-exception-caught
            try:
                _mcp_server_process.kill()
            except Exception:  # pylint: disable=broad-exception-caught
                pass
        _mcp_server_process = None
        # Brief delay for OS to release the port.
        import time
        time.sleep(0.5)

    # Kill any stale process occupying the port (from addon reinstall or crash).
    _kill_process_on_port(port)
    import time
    time.sleep(0.5)  # Let OS release the port.

    env = _build_mcp_env(blender_host=blender_host, blender_port=blender_port)

    # --- Resolution order ---
    mcp_exe, use_module = _resolve_mcp_python()

    if not mcp_exe:
        _agent_state.error = "Cannot find Python to run MCP server"
        return None

    # --- Launch ---

    try:
        if use_module:
            print("[🛠️Coworker] start_mcp_server: running {:s} -m blmcp with PYTHONPATH={:s}".format(
                mcp_exe, env.get("PYTHONPATH", "(unset)")))
            proc = subprocess.Popen(
                [mcp_exe, "-m", "blmcp", "--transport", "http", "--port", str(port)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
            )
        else:
            proc = subprocess.Popen(
                [mcp_exe, "--transport", "http", "--port", str(port)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
            )
    except FileNotFoundError as ex:
        _agent_state.error = "Failed to launch MCP server: {:s}".format(str(ex))
        return None
    except OSError as ex:
        _agent_state.error = "Failed to launch MCP server: {:s}".format(str(ex))
        return None

    _mcp_server_process = proc
    _agent_state.mcp_server_running = True
    _agent_state.error = ""
    print("[🛠️Coworker] start_mcp_server: launched pid={:d}".format(proc.pid))
    print("[🛠️Coworker] start_mcp_server: command = {:s}".format(str(mcp_exe or "python -m blmcp")))
    print("[🛠️Coworker] start_mcp_server: BFACW_HOST={:s} BFACW_PORT={:d}".format(
        blender_host, blender_port))

    # Spawn background threads to drain stdout/stderr pipes.
    # Without this, PIPE buffers fill and the child process deadlocks.
    _drainer_threads, _stdout_lines, _stderr_lines = _start_pipe_drainer(proc)

    # Health check: wait for the MCP HTTP server to bind its port.
    # Check for early exit first (fast path), then poll the port.
    import time
    time.sleep(0.5)  # Brief pause for process to start or fail.
    if proc.poll() is not None:
        # Process exited — collect from drainer.
        time.sleep(0.5)  # Let drainer finish reading.
        stderr_output = "\n".join(_stderr_lines[-100:])
        stdout_output = "\n".join(_stdout_lines[-100:])
        error_detail = (stderr_output or stdout_output or "no output")
        print("[🛠️Coworker] start_mcp_server: process already exited with code {:d}".format(
            proc.returncode))
        if stderr_output:
            print("[🛠️Coworker] start_mcp_server: stderr (tail) = {:s}".format(stderr_output[-1500:]))
        if stdout_output:
            print("[🛠️Coworker] start_mcp_server: stdout (tail) = {:s}".format(stdout_output[-1500:]))

        # Check if it's a ModuleNotFoundError (likely wrong Python version).
        if "ModuleNotFoundError" in error_detail or "ImportError" in error_detail:
            if _retry_depth >= 1:
                print("[🛠️Coworker] start_mcp_server: import error after retry — giving up")
                _agent_state.error = "MCP server import failed after reinstall: {:s}".format(
                    error_detail.split("\n")[-1].strip()[:200])
                _agent_state.mcp_server_running = False
                _mcp_server_process = None
                return None
            print("[🛠️Coworker] start_mcp_server: import error detected — attempting dependency reinstall")
            # Clear deps and retry once with Blender's Python.
            deps_dir = _get_vendor_deps_dir()
            if deps_dir.is_dir():
                shutil.rmtree(str(deps_dir), ignore_errors=True)
                if _ensure_vendor_deps():
                    # Try launching again (depth-limited).
                    print("[🛠️Coworker] start_mcp_server: deps reinstalled — retrying launch (attempt {:d})".format(
                        _retry_depth + 1))
                    return start_mcp_server(
                        port=port, blender_host=blender_host, blender_port=blender_port,
                        _retry_depth=_retry_depth + 1,
                    )
        _agent_state.error = "MCP server exited immediately: {:s}".format(error_detail[:200])
        _agent_state.mcp_server_running = False
        _mcp_server_process = None
        return None

    # Process is alive — actively wait for the port to accept connections.
    # FastMCP + Starlette imports can take 5-10s, so we poll up to 15s.
    print("[🛠️Coworker] start_mcp_server: process alive, waiting for port {:d}...".format(port))
    port_ready = _wait_for_port("127.0.0.1", port, timeout=15.0, interval=1.0)

    if not port_ready:
        # Port never came up — collect drainer output for diagnostics.
        time.sleep(1.0)
        stderr_output = "\n".join(_stderr_lines[-100:])
        stdout_output = "\n".join(_stdout_lines[-100:])
        error_detail = (stderr_output or stdout_output or "no output")
        print("[🛠️Coworker] start_mcp_server: port {:d} never became ready".format(port))
        if stderr_output:
            print("[🛠️Coworker] start_mcp_server: stderr (tail) = {:s}".format(stderr_output[-1500:]))
        if stdout_output:
            print("[🛠️Coworker] start_mcp_server: stdout (tail) = {:s}".format(stdout_output[-1500:]))
        _agent_state.error = "MCP server started but port {:d} never accepted connections".format(port)
        _agent_state.mcp_server_running = False
        _mcp_server_process = None
        return None

    print("[🛠️Coworker] start_mcp_server: port {:d} is ready".format(port))

    # Log collected output for diagnostics.
    if _stdout_lines:
        print("[🛠️Coworker] start_mcp_server: process alive, stdout so far ({:d} lines):".format(
            len(_stdout_lines)))
        for line in _stdout_lines[-15:]:
            print("[🛠️Coworker] start_mcp_server:   stdout | {:s}".format(line))
    if _stderr_lines:
        print("[🛠️Coworker] start_mcp_server: process alive, stderr so far ({:d} lines):".format(
            len(_stderr_lines)))
        for line in _stderr_lines[-15:]:
            print("[🛠️Coworker] start_mcp_server:   stderr | {:s}".format(line))

    return proc


def stop_mcp_server() -> None:
    """Terminate the MCP server subprocess."""
    global _mcp_server_process, _mcp_shutting_down

    if _mcp_shutting_down:
        return

    _mcp_shutting_down = True
    try:
        proc = _mcp_server_process
        if proc is None:
            return

        try:
            proc.terminate()
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
        except Exception:  # pylint: disable=broad-exception-caught
            pass

        _mcp_server_process = None
        _agent_state.mcp_server_running = False
    finally:
        _mcp_shutting_down = False


# ---------------------------------------------------------------------------
# MCP server — Network mode (External Harness)

def start_mcp_server_network(
    host: str = "127.0.0.1",
    port: int = 9191,
    blender_host: str = "localhost",
    blender_port: int = 9876,
) -> subprocess.Popen | None:
    """Launch the MCP server in network (HTTP) mode for external clients.

    This is similar to ``start_mcp_server()`` but binds to a configurable
    *host*:*port* instead of always using 127.0.0.1.  Useful for:
    - Browser-based MCP clients on the same machine
    - Remote MCP clients on the same network (use with caution)

    Returns the ``Popen`` handle, or ``None`` on failure.
    """
    global _mcp_server_process, _mcp_shutting_down

    if _mcp_shutting_down:
        print("[🛠️Coworker] start_mcp_server_network: shutdown in progress — skipping")
        return None

    # Kill existing process if known.
    if _mcp_server_process is not None:
        try:
            _mcp_server_process.terminate()
            _mcp_server_process.wait(timeout=3)
        except Exception:  # pylint: disable=broad-exception-caught
            try:
                _mcp_server_process.kill()
            except Exception:  # pylint: disable=broad-exception-caught
                pass
        _mcp_server_process = None
        import time
        time.sleep(0.5)

    # Kill any stale process on the port.
    _kill_process_on_port(port)
    import time
    time.sleep(0.5)

    env = _build_mcp_env(blender_host=blender_host, blender_port=blender_port)

    # --- Resolution order ---
    mcp_exe, use_module = _resolve_mcp_python()

    if not mcp_exe:
        _agent_state.error = "Cannot find Python to run MCP server"
        return None

    try:
        if use_module:
            proc = subprocess.Popen(
                [mcp_exe, "-m", "blmcp", "--transport", "http",
                 "--host", host, "--port", str(port)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
            )
        else:
            proc = subprocess.Popen(
                [mcp_exe, "--transport", "http",
                 "--host", host, "--port", str(port)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
            )
    except (FileNotFoundError, OSError) as ex:
        _agent_state.error = "Failed to launch MCP server: {:s}".format(str(ex))
        return None

    _mcp_server_process = proc
    _agent_state.mcp_server_running = True
    _agent_state.error = ""

    # Drain pipes.
    _start_pipe_drainer(proc)

    # Wait for port.
    import time
    time.sleep(0.5)
    if proc.poll() is not None:
        _agent_state.error = "MCP server exited immediately"
        _agent_state.mcp_server_running = False
        _mcp_server_process = None
        return None

    port_ready = _wait_for_port(host, port, timeout=15.0, interval=1.0)
    if not port_ready:
        _agent_state.error = "MCP server port {:d} never accepted connections".format(port)
        _agent_state.mcp_server_running = False
        _mcp_server_process = None
        return None

    return proc


# ---------------------------------------------------------------------------
# MCP client config generation (External Harness)

def generate_mcp_client_config(
    client_type: str = "claude",
    blender_host: str = "localhost",
    blender_port: int = 9876,
) -> str:
    """Generate MCP client configuration JSON for external tools.

    *client_type*: ``"claude"`` (Claude Desktop), ``"vscode"`` (VS Code / Cursor),
    or ``"generic"`` (generic JSON-RPC config).

    Returns a JSON string suitable for the client's config file.
    """
    if client_type == "claude":
        config = {
            "mcpServers": {
                "bfa-coworker": {
                    "command": "python",
                    "args": ["-m", "blmcp", "--transport", "stdio"],
                    "env": {
                        "BFACW_HOST": blender_host,
                        "BFACW_PORT": str(blender_port),
                    },
                }
            }
        }
    elif client_type == "vscode":
        config = {
            "servers": {
                "bfa-coworker": {
                    "type": "stdio",
                    "command": "python",
                    "args": ["-m", "blmcp", "--transport", "stdio"],
                    "env": {
                        "BFACW_HOST": blender_host,
                        "BFACW_PORT": str(blender_port),
                    },
                }
            }
        }
    else:
        config = {
            "command": "python",
            "args": ["-m", "blmcp", "--transport", "stdio"],
            "env": {
                "BFACW_HOST": blender_host,
                "BFACW_PORT": str(blender_port),
            },
        }

    return json.dumps(config, indent=2)


# ---------------------------------------------------------------------------
# Operation History Log (Tier 1)

def _log_operation(tool_name: str, params: dict, result: str) -> None:
    """Append a tool execution to the operation history JSONL file."""
    import time as _time
    log_path = Path.home() / ".cache" / "bfa_coworker" / "operations.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": _time.time(),
        "tool": tool_name,
        "params": params,
        "result": result[:500],  # Truncate for log size.
    }
    try:
        with open(str(log_path), "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass  # Best-effort logging.


# ---------------------------------------------------------------------------
# Liveness Check (Tier 1)

def _check_liveness() -> None:
    """Update liveness booleans based on activity timestamps."""
    import time as _time
    now = _time.monotonic()
    _agent_state.bridge_live = (now - _agent_state.last_bridge_activity) < 20.0
    _agent_state.mcp_live = (now - _agent_state.last_mcp_activity) < 20.0
    _agent_state.llm_live = (now - _agent_state.last_llm_activity) < 20.0


# ---------------------------------------------------------------------------
# MCP tool listing

async def list_mcp_tools(port: int = _MCP_SERVER_DEFAULT_PORT) -> list[dict[str, Any]]:
    """
    Return the list of tools from the MCP server via HTTP.

    Tries both the streamable-http tool listing endpoint and the
    standard MCP list-tools mechanism.
    """
    url = "http://127.0.0.1:{:d}/".format(port)
    print("[🛠️Coworker] list_mcp_tools: trying {:s}".format(url))

    # Use urllib (stdlib, avoids Blender sandbox policy violation from vendored httpx).
    try:
        payload = {"jsonrpc": "2.0", "id": "1", "method": "tools/list"}
        data_bytes = json.dumps(payload).encode()
        req = urllib.request.Request(
            url,
            data=data_bytes,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
            method="POST",
        )
        print("[🛠️Coworker] list_mcp_tools: urllib POST {:s}".format(url))
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode()
            print("[🛠️Coworker] list_mcp_tools: urllib status={:d}, {:d} bytes".format(resp.status, len(raw)))
            print("[🛠️Coworker] list_mcp_tools: urllib first 300 chars: {:s}".format(raw[:300]))
            # FastMCP in stateless_http mode returns SSE
            # (``event: message`` / ``data: {...}``) even for
            # single-response JSON-RPC calls.
            data = _parse_sse_json(raw)
            if data is None:
                print("[🛠️Coworker] list_mcp_tools: urllib SSE parse returned None")
                return []
            tools = data.get("result", {}).get("tools", [])
            print("[🛠️Coworker] list_mcp_tools: urllib returned {:d} tools".format(len(tools)))
            return tools
    except Exception as ex:  # pylint: disable=broad-exception-caught
        print("[🛠️Coworker] list_mcp_tools: urllib failed — {:s}".format(str(ex)))

    return []


def _list_tools_sync(port: int = _MCP_SERVER_DEFAULT_PORT) -> list[dict[str, Any]]:
    """Synchronous wrapper for listing MCP tools, with retry on 0 tools."""
    import time
    max_retries = 5
    for attempt in range(1, max_retries + 1):
        print("[🛠️Coworker] _list_tools_sync: port={:d} attempt={:d}/{:d}".format(
            port, attempt, max_retries))
        future = schedule_coro(list_mcp_tools(port))
        try:
            result = future.result(timeout=15)
            count = len(result) if result else 0
            print("[🛠️Coworker] _list_tools_sync: got {:d} tools".format(count))
            if count > 0:
                _agent_state.tool_count = count
                return result
            # 0 tools — retry if server is still running.
            if not _agent_state.mcp_server_running:
                print("[🛠️Coworker] _list_tools_sync: server not running, aborting")
                return result or []
            if attempt < max_retries:
                delay = min(1.0 * attempt, 4.0)  # Backoff: 1s, 2s, 3s, 4s.
                print("[🛠️Coworker] _list_tools_sync: 0 tools, retrying in {:.0f}s...".format(delay))
                time.sleep(delay)
        except Exception as ex:  # pylint: disable=broad-exception-caught
            print("[🛠️Coworker] _list_tools_sync: attempt {:d} FAILED — {:s}".format(attempt, str(ex)))
            if attempt < max_retries:
                time.sleep(1.0)
                continue
            return []
    return []


# ---------------------------------------------------------------------------
# LLM conversation loop (synchronous, called from timer)

def _openai_chat_completions(
    url: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    api_key: str | None = None,
    model: str | None = None,
    max_tokens: int | None = None,
) -> dict[str, Any] | None:
    """POST to a chat completions endpoint and return the parsed JSON response.

    *model* — when provided, included in the request body. Required for
    remote APIs (OpenRouter, OpenAI, etc.). Omitted for local llama-server
    which auto-detects the model.
    *max_tokens* — max output tokens per call. ``None`` uses 16384 default.
    """
    body: dict[str, Any] = {
        "messages": messages,
        "stream": False,
        # Cap output so the model doesn't generate endlessly.
        "max_tokens": max_tokens if max_tokens is not None else 16384,
        # Parameters tuned for small local models (Gemma 4 26B etc.):
        # - temperature: 0.3 gives focused, non-erratic output
        # - top_p: 0.9 limits random tail tokens
        # - stop: prevent the model from generating tool-call syntax
        #   that it can't actually execute.
        "temperature": 0.3,
        "top_p": 0.9,
        "repeat_penalty": 1.05,
    }
    if model:
        body["model"] = model
    if tools:
        body["tools"] = tools

    data_bytes = json.dumps(body).encode()
    headers = {
        "Content-Type": "application/json",
        "HTTP-Referer": "https://bforartists.org",
        "X-OpenRouter-Title": "Bforartists Coworker",
    }
    if api_key:
        headers["Authorization"] = "Bearer {:s}".format(api_key)

    print("[🛠️Coworker] _openai_chat_completions: POST {:s}".format(url))
    print("[🛠️Coworker] _openai_chat_completions:   model = {:s}".format(model or "(auto-detect)"))
    print("[🛠️Coworker] _openai_chat_completions:   messages = {:d}, tools = {:d}, body = {:d} bytes".format(
        len(messages), len(tools), len(data_bytes)))

    req = urllib.request.Request(url, data=data_bytes, headers=headers, method="POST")

    # Retry loop for transient failures (e.g. server just became ready
    # but the HTTP worker hasn't started yet).
    import time as _time
    max_retries = 3
    for attempt in range(max_retries):
        try:
            with urllib.request.urlopen(req, timeout=_STREAM_TIMEOUT) as resp:
                raw = resp.read().decode()
                print("[🛠️Coworker] _openai_chat_completions: status={:d}, response={:d} bytes".format(
                    resp.status, len(raw)))
                print("[🛠️Coworker] _openai_chat_completions: first 500 chars: {:s}".format(raw[:500]))
                result: dict[str, Any] = json.loads(raw)
                # Log the assistant message content and any tool calls.
                choice = result.get("choices", [{}])[0]
                msg = choice.get("message", {})
                finish = choice.get("finish_reason", "")
                content = msg.get("content") or ""
                tool_calls = msg.get("tool_calls") or []
                print("[🛠️Coworker] _openai_chat_completions: finish_reason={:s}".format(finish))
                print("[🛠️Coworker] _openai_chat_completions: content   = {:s}".format(
                    repr(content[:200]) if content else "(empty)"))
                print("[🛠️Coworker] _openai_chat_completions: tool_calls= {:d}".format(len(tool_calls)))
                for i, tc in enumerate(tool_calls):
                    fn = tc.get("function", {})
                    print("[🛠️Coworker] _openai_chat_completions:   tool[{:d}] = {:s}({:s})".format(
                        i, fn.get("name", "?"), str(fn.get("arguments", ""))[:120]))
                # Log reasoning content (chain-of-thought) for debugging.
                reasoning = msg.get("reasoning_content") or msg.get("reasoning") or ""
                if reasoning:
                    print("[🛠️Coworker] _openai_chat_completions: reasoning ({:d} chars):".format(
                        len(reasoning)))
                    print(reasoning)
                    print("[🛠️Coworker] _openai_chat_completions: --- end reasoning ---")
                return result
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as ex:
            if attempt < max_retries - 1:
                print("[🛠️Coworker] _openai_chat_completions: attempt {:d}/{:d} FAILED — {:s}, retrying in 2s...".format(
                    attempt + 1, max_retries, str(ex)))
                _time.sleep(2)
                continue
            print("[🛠️Coworker] _openai_chat_completions: all {:d} attempts FAILED — {:s}".format(
                max_retries, str(ex)))
            _agent_state.error = "LLM request failed: {:s}".format(str(ex))
            return None
    return None


def _mcp_tools_to_openai(mcp_tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert MCP tool metadata to OpenAI ``tools`` format."""
    result = []
    for t in mcp_tools:
        result.append({
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get("inputSchema", {}),
            },
        })
    return result


def _call_mcp_tool_sync(
    tool_name: str,
    arguments: dict[str, Any],
    port: int = _MCP_SERVER_DEFAULT_PORT,
) -> str:
    """Call an MCP tool synchronously via the HTTP endpoint."""
    import time as _time
    url = "http://127.0.0.1:{:d}/".format(port)
    payload = {
        "jsonrpc": "2.0",
        "id": "tool_{:s}".format(tool_name),
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": arguments,
        },
    }
    data_bytes = json.dumps(payload).encode()
    req = urllib.request.Request(
        url,
        data=data_bytes,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
        method="POST",
    )
    print("[🛠️Coworker] _call_mcp_tool_sync: {:s} args={:s}".format(
        tool_name, json.dumps(arguments)[:200]))
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode()
            # FastMCP in stateless_http mode wraps the JSON-RPC
            # response in SSE (``event: message`` / ``data: {...}``).
            result = _parse_sse_text_response(raw)
            print("[🛠️Coworker] _call_mcp_tool_sync: result = {:s}".format(
                result[:300]))
            # Update liveness and log operation.
            _agent_state.last_mcp_activity = _time.monotonic()
            _log_operation(tool_name, arguments, result)
            return result
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as ex:
        print("[🛠️Coworker] _call_mcp_tool_sync: FAILED — {:s}".format(str(ex)))
        return "Error calling tool '{:s}': {:s}".format(tool_name, str(ex))


# ── Friendly tool names for UI status ─────────────────────────────

_TOOL_FRIENDLY_NAMES: dict[str, str] = {
    "execute_blender_code": "Running code in Blender",
    "get_blendfile_summary_datablocks_toolcode": "Reading scene data",
    "download_polyhaven_asset": "Downloading asset",
    "get_object_info": "Inspecting object",
    "create_object": "Creating object",
    "modify_object": "Modifying object",
    "delete_object": "Removing object",
    "set_material": "Applying material",
    "render_scene": "Rendering",
}


def _friendly_tool_status(tool_name: str) -> str:
    """Return a user-friendly status string for a tool name."""
    friendly = _TOOL_FRIENDLY_NAMES.get(tool_name)
    if friendly:
        return "{:s}...".format(friendly)
    # Fallback: convert camelCase/snake_case to readable text.
    import re
    readable = re.sub(r"_+", " ", tool_name)
    readable = re.sub(r"([a-z])([A-Z])", r"\1 \2", readable)
    return "{:s}...".format(readable.capitalize())


# ── Tool error formatting ─────────────────────────────────────────

def _format_tool_error(result_text: str) -> str:
    """Extract a human-readable summary from a tool error result.

    Parses ``{"status": "error", "message": "Traceback..."}`` and returns
    a friendly message like ``"I had trouble with that step — AttributeError"``.

    Returns *result_text* unchanged if it doesn't match the error pattern.
    """
    if '"status": "error"' not in result_text:
        return result_text

    # Try to extract just the exception type from the traceback.
    import re
    m = re.search(r'"message":\s*"([^"]*(?:\\.[^"]*)*)"', result_text, re.DOTALL)
    if m:
        raw_msg = m.group(1)
        raw_msg = raw_msg.replace("\\n", "\n").replace("\\t", "\t").replace('\\"', '"')
        lines = raw_msg.strip().splitlines()
        # Walk backwards to find the actual exception line (skip Traceback, File, and blank lines).
        for line in reversed(lines):
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("Traceback") or stripped.startswith("["):
                continue
            if stripped.startswith("File"):
                continue
            # This is the actual exception.
            exc_type = stripped.split(":")[0].strip() if ":" in stripped else stripped
            return "Work had an error \u2014 {:s}, trying again".format(exc_type)
    return result_text


def _tool_result_summary(result_text: str, max_len: int = 150) -> str:
    """Return a short summary of a tool result for UI display.

    For errors, uses ``_format_tool_error``. For successes, extracts a brief
    status or truncates the result.
    """
    if '"status": "error"' in result_text:
        return _format_tool_error(result_text)
    # Try to extract a success message.
    import re
    m = re.search(r'"status":\s*"ok"', result_text)
    if m:
        msg_m = re.search(r'"message":\s*"([^"]*)"', result_text)
        if msg_m:
            return msg_m.group(1)[:max_len]
        return "Done"
    if len(result_text) <= max_len:
        return result_text
    return result_text[:max_len] + "..."


# ---------------------------------------------------------------------------
# Smart undo helpers — detect code iteration and auto-undo duplicates

def _extract_code_operations(code: str) -> set[str]:
    """Extract operation signatures from a code string for overlap detection.

    Returns a set of strings representing operations: ``bpy.ops`` calls,
    ``bpy.data.*.new/remove`` calls, and quoted name literals.
    """
    ops: set[str] = set()
    # Extract bpy.ops.* calls (e.g. bpy.ops.mesh.primitive_cube_add).
    for m in re.finditer(r"bpy\.ops\.([a-z_]+)\.([a-z_]+)", code):
        ops.add("op:{:s}.{:s}".format(m.group(1), m.group(2)))
    # Extract bpy.data.*.new() / .remove() / .load() calls.
    for m in re.finditer(r"bpy\.data\.([a-z_]+)\.(new|remove|load)", code):
        ops.add("data:{:s}.{:s}".format(m.group(1), m.group(2)))
    # Extract quoted string literals that look like names (2+ chars, no spaces).
    for m in re.finditer(r'"([A-Za-z_][A-Za-z0-9_.]{1,40})"', code):
        ops.add("name:{:s}".format(m.group(1)))
    return ops


def _codes_overlap(prev_code: str, new_code: str) -> bool:
    """Return ``True`` if two code strings share operations (indicating iteration).

    Compares extracted operations from both code strings. If they share
    any ``bpy.ops`` calls, ``bpy.data.new/remove`` calls, or name literals,
    the new code is likely iterating on the same task as the previous code.
    """
    prev_ops = _extract_code_operations(prev_code)
    new_ops = _extract_code_operations(new_code)
    return bool(prev_ops & new_ops)


# ---------------------------------------------------------------------------
# Text editor memory bank helpers

_code_sequence_counter: int = 0


def _next_code_sequence() -> str:
    """Return the next zero-padded 3-digit sequence number (001, 002, ...)."""
    global _code_sequence_counter
    _code_sequence_counter += 1
    return "{:03d}".format(_code_sequence_counter)


def _clear_coworker_text_blocks() -> None:
    """Remove all Coworker_* text datablocks from Blender's text editor."""
    global _code_sequence_counter
    _code_sequence_counter = 0
    try:
        import bpy as _bpy  # pylint: disable=import-error
        for text_block in list(_bpy.data.texts):
            if text_block.name.startswith("Coworker_"):
                _bpy.data.texts.remove(text_block)
    except Exception:
        pass  # Best-effort.


def run_conversation_turn(
    user_message: str,
    on_text: Callable[[str], None] | None = None,
    on_status: Callable[[str], None] | None = None,
    on_reasoning: Callable[[str], None] | None = None,
    llm_url: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
    mcp_port: int = _MCP_SERVER_DEFAULT_PORT,
    chat_mode: str = "AGENT",
) -> list[dict[str, Any]]:
    """
    Run a full conversation turn.

    1. Prepends system prompt (if not already present).
    2. Appends user message to history.
    3. Sends to LLM, handles tool calls via MCP.
    4. Returns updated conversation history.

    When *chat_mode* is ``"ASK"``, tool execution is skipped and the LLM
    responds with text only (read-only Q&A).

    This is a BLOCKING call — run it via ``schedule_coro`` or in a thread.
    """
    clear_stop()
    history = _agent_state.conversation_history

    # Ensure the first message is the system prompt.
    if not history or history[0].get("role") != "system":
        system_text = _get_system_prompt_with_rules()
        history.insert(0, {"role": "system", "content": system_text})
        print("[🛠️Coworker] run_conversation_turn: inserted system prompt ({:d} chars)".format(
            len(system_text)))

    history.append({"role": "user", "content": user_message})

    # ── Smart undo tracking (per-turn) ────────────────────────────────
    # Tracks the last execute_blender_code call to detect iteration and
    # auto-undo duplicates. Reset at the start of each turn.
    _prev_code: str | None = None
    _prev_code_errored: bool = False
    _undo_pushed: bool = False  # True once we've pushed the first undo state.

    # In Ask mode, skip tool listing and execution entirely.
    if chat_mode == "ASK":
        openai_tools = []
    else:
        # Get MCP tools.
        tools = _list_tools_sync(mcp_port)
        openai_tools = _mcp_tools_to_openai(tools) if tools else []

    if on_status:
        on_status("Thinking...")
    _agent_state.is_thinking = True
    _agent_state.streaming_text = ""
    _agent_state.reasoning_text = ""
    _agent_state.thinking_dots = 0

    # Determine LLM URL.
    llm_port_local: int | None = None
    if llm_url is None:
        # No URL provided — resolve from config mode.
        from . import llm_manager as _llm_mgr
        _llm_cfg = _llm_mgr.get_config()
        if _llm_cfg.mode == "remote":
            # Build remote URL from config.
            llm_url = _llm_cfg.remote_api_url
            api_key = _llm_cfg.remote_api_key or api_key
            model = _llm_cfg.remote_model or model
        else:
            # Use local llama-server default.
            llm_url = _LLM_CHAT_URL.format(_llm_cfg.local_port)
            llm_port_local = _llm_cfg.local_port

    # Ensure URL ends with /v1/chat/completions (for both local and remote).
    if llm_url:
        base = llm_url.rstrip("/")
        if not base.endswith("/chat/completions"):
            if base.endswith("/v1"):
                llm_url = "{:s}/chat/completions".format(base)
            else:
                llm_url = "{:s}/v1/chat/completions".format(base)

    # Wait for local LLM port to become ready.
    # The model can take 30-120s to load into memory before the server
    # accepts connections. Without this wait, the first chat request
    # would fail with "connection refused".
    if llm_port_local is not None:
        print("[🛠️Coworker] run_conversation_turn: waiting for LLM on 127.0.0.1:{:d}...".format(llm_port_local))
        if not _wait_for_port("127.0.0.1", llm_port_local, timeout=120.0):
            _agent_state.is_thinking = False
            _agent_state.error = "LLM server did not become ready after 120s"
            if on_status:
                on_status("Error: LLM server not ready")
            return history

    # Resolve max_tokens from config (local or remote).
    from . import llm_manager as _llm_mgr
    _llm_cfg = _llm_mgr.get_config()
    max_tokens = _llm_cfg.local_max_tokens if llm_port_local is not None else 16384
    print("[🛠️Coworker] run_conversation_turn: using max_tokens={:d}".format(max_tokens))

    iterations = 0
    while iterations < _MAX_TOOL_ITERATIONS:
        iterations += 1

        # Abort early if the user pressed Stop.
        if _stop_event.is_set():
            print("[🛠️Coworker] run_conversation_turn: aborted by user")
            _agent_state.is_thinking = False
            if on_status:
                on_status("Stopped")
            return history

        # Slice history to avoid unbounded context growth.
        # Always keep the system prompt (index 0) if present.
        # Must preserve tool-call pairs: each "tool" role message
        # MUST follow an "assistant" message with "tool_calls".
        if len(history) > _MAX_HISTORY_MESSAGES:
            keep = min(_MAX_HISTORY_MESSAGES, len(history))
            # Keep system message + last N messages.
            if history[0].get("role") == "system":
                history_to_send = [history[0]] + history[-(keep - 1):]
                # Walk forward from the system message and remove any
                # orphaned "tool" messages that lost their assistant pair.
                history_to_send = _drop_orphaned_tool_messages(history_to_send)
            else:
                history_to_send = _drop_orphaned_tool_messages(history[-keep:])
        else:
            history_to_send = history

        response = _openai_chat_completions(llm_url, history_to_send, openai_tools, api_key, model, max_tokens)
        if response is None:
            _agent_state.is_thinking = False
            _agent_state.error = "No response from LLM"
            if on_status:
                on_status("Error: No response from LLM")
            return history

        # Safety: if the LLM returned HTTP 500, the context may be too large
        # for the model.  Log the approximate body size for debugging.
        body_approx = len(json.dumps(history_to_send, default=str))
        if body_approx > 30000:
            print("[🛠️Coworker] run_conversation_turn: WARNING — history body is {:d} bytes, "
                  "may exceed model context window".format(body_approx))

        choice = response.get("choices", [{}])[0]
        msg = choice.get("message", {})
        finish_reason = choice.get("finish_reason", "")

        # Extract text content.
        content = msg.get("content") or ""

        # ── Auto-continue on finish_reason=length ─────────────────────
        # Reasoning models (Qwen, DeepSeek, Gemma 4) can hit the token
        # limit mid-reasoning before emitting tool calls or text.
        # We detect this and ask the model to continue.
        continue_attempts = 0
        while finish_reason == "length" and continue_attempts < 2:
            continue_attempts += 1
            print("[🛠️Coworker] run_conversation_turn: finish_reason=length, "
                  "auto-continue attempt {:d}/2".format(continue_attempts))

            # Append partial assistant message to history so the model
            # can pick up where it left off.
            partial_msg: dict[str, Any] = {"role": "assistant", "content": content}
            if msg.get("tool_calls"):
                partial_msg["tool_calls"] = msg["tool_calls"]
            history.append(partial_msg)

            # Send a brief continuation prompt.
            history.append({"role": "user", "content": "Continue."})

            # Re-request with the same max_tokens.
            continue_response = _openai_chat_completions(
                llm_url, history, openai_tools, api_key, model, max_tokens,
            )
            if continue_response is None:
                break

            # Pop the "Continue." user message so it doesn't pollute history.
            history.pop()
            # Pop the partial assistant message — we'll replace it with the
            # concatenated version.
            history.pop()

            # Merge results: concatenate content, merge tool_calls.
            cont_choice = continue_response.get("choices", [{}])[0]
            cont_msg = cont_choice.get("message", {})
            cont_content = cont_msg.get("content") or ""
            cont_tool_calls = cont_msg.get("tool_calls") or []

            content = content + cont_content
            if cont_tool_calls:
                # Merge tool calls from continuation.
                existing = msg.get("tool_calls") or []
                msg["tool_calls"] = existing + cont_tool_calls
            msg["content"] = content
            finish_reason = cont_choice.get("finish_reason", "")
            print("[🛠️Coworker] run_conversation_turn:   after continue: "
                  "finish_reason={:s}, content_len={:d}, tool_calls={:d}".format(
                      finish_reason, len(content), len(msg.get("tool_calls") or [])))

        # ── End auto-continue ─────────────────────────────────────────

        # Deliver reasoning (chain-of-thought) to UI if present.
        # Different providers use different field names:
        #   - Local llama-server / DeepSeek: "reasoning_content"
        #   - OpenRouter: "reasoning"
        reasoning = msg.get("reasoning_content") or msg.get("reasoning") or ""
        if reasoning:
            print("[🛠️Coworker] run_conversation_turn: reasoning ({:d} chars) — storing in history".format(
                len(reasoning)))
            _agent_state.reasoning_text = reasoning
            # Pick a random thinking label that sticks for this reasoning block.
            import random as _random
            _thinking_labels = [
                "Considering", "Expanding", "Scheming", "Working",
                "Adjusting", "Thinking", "Planning", "Figuring",
                "Reasoning", "Pondering",
            ]
            label = _random.choice(_thinking_labels)
            history.append({"role": "reasoning", "content": reasoning, "label": label})
            if on_reasoning:
                on_reasoning(reasoning)

            _agent_state.last_llm_activity = time.monotonic()

        if content and on_text:
            on_text(content)
            _agent_state.streaming_text = content

        # Check for tool calls.
        raw_tool_calls = msg.get("tool_calls")

        # Process tool calls if present.
        if raw_tool_calls and finish_reason == "tool_calls":
            # Add assistant message with tool calls to history.
            history.append({"role": "assistant", "content": content, "tool_calls": raw_tool_calls})

            # Process each tool call.
            for tc in raw_tool_calls:
                if _stop_event.is_set():
                    print("[🛠️Coworker] run_conversation_turn: aborted during tool calls")
                    _agent_state.is_thinking = False
                    if on_status:
                        on_status("Stopped")
                    return history
                fn = tc.get("function", {})
                try:
                    args = json.loads(fn.get("arguments", "{}"))
                except (json.JSONDecodeError, TypeError):
                    args = {}

                tool_name = fn.get("name", "")
                tool_id = tc.get("id", "")

                if on_status:
                    on_status(_friendly_tool_status(tool_name))

                # ── Smart undo: auto-undo before re-executing code ─────
                # If this is execute_blender_code and we've already run it
                # this turn, check whether the new code is iterating on the
                # same task (overlapping operations) or retrying after an
                # error. If so, undo the previous attempt first.
                if tool_name == "execute_blender_code" and _prev_code is not None:
                    new_code = args.get("code", "")
                    should_undo = False
                    reason = ""
                    if _prev_code_errored:
                        should_undo = True
                        reason = "previous call errored"
                    elif _codes_overlap(_prev_code, new_code):
                        should_undo = True
                        reason = "iterating on same task (overlapping operations)"
                    if should_undo:
                        print("[🛠️Coworker] run_conversation_turn: smart undo triggered — {:s}".format(reason))
                        # Undo to the state before the previous execute_blender_code.
                        _call_mcp_tool_sync("execute_blender_code",
                            {"code": "bpy.ops.ed.undo()"}, mcp_port)
                        # Push a fresh undo state so the next iteration can undo this one.
                        _call_mcp_tool_sync("execute_blender_code",
                            {"code": "bpy.ops.ed.undo_push(message=\"bfa_coworker_pre_script\")"},
                            mcp_port)

                # ── Push initial undo state before first code execution ─
                if tool_name == "execute_blender_code" and not _undo_pushed:
                    _call_mcp_tool_sync("execute_blender_code",
                        {"code": "bpy.ops.ed.undo_push(message=\"bfa_coworker_pre_script\")"},
                        mcp_port)
                    _undo_pushed = True

                # Call the MCP tool.
                result_text = _call_mcp_tool_sync(tool_name, args, mcp_port)

                # ── Track code execution for smart undo ────────────────
                if tool_name == "execute_blender_code":
                    _prev_code = args.get("code", "")
                    _prev_code_errored = '"status": "error"' in result_text

                    # ── Save to text editor memory bank ────────────────
                    if not _prev_code_errored:
                        try:
                            import bpy as _bpy  # pylint: disable=import-error
                            prefs = _bpy.context.preferences.addons[__package__].preferences
                            if getattr(prefs, "save_code_to_text_editor", True):
                                seq = _next_code_sequence()
                                name = "Coworker_{:s}".format(seq)
                                text_block = _bpy.data.texts.new(name)
                                text_block.write(_prev_code)
                                print("[🛠️Coworker] run_conversation_turn: saved code to text editor '{:s}'".format(name))
                        except Exception:
                            pass  # Best-effort; don't break the agent loop.

                # Build a human-readable summary for the UI.
                result_summary = _tool_result_summary(result_text)

                # Truncate tool result content in history to avoid context bloat.
                # Full results can be thousands of chars (scene dumps, etc.) and
                # balloon the prompt past small local models' context windows.
                # The LLM only needs the gist of past tool results — the current
                # turn's result is still available in the truncated form.
                _MAX_TOOL_RESULT_CHARS = 500
                truncated = result_text[:_MAX_TOOL_RESULT_CHARS]
                if len(result_text) > _MAX_TOOL_RESULT_CHARS:
                    truncated += "\n...[+{:d} more chars]".format(
                        len(result_text) - _MAX_TOOL_RESULT_CHARS)

                # Add tool result to history.
                history.append({
                    "role": "tool",
                    "tool_call_id": tool_id,
                    "name": tool_name,
                    "content": truncated,
                    "summary": result_summary,
                })

            # After processing tool calls, ask the LLM for a final text response.
            # We send ONE more request without looping. If the model decides to
            # call tools again, we process them and STOP — no infinite loops.
            continue

        # No more tool calls — add the final assistant message and we're done.
        history.append({"role": "assistant", "content": content})
        break

    # If we hit the iteration limit, the LLM kept calling tools.
    # Add an explicit instruction to summarize and make one final call.
    if iterations >= _MAX_TOOL_ITERATIONS:
        print("[🛠️Coworker] run_conversation_turn: hit max iterations, forcing summary")
        history.append({
            "role": "user",
            "content": "All tool calls are complete. Please summarize what was done in 1-2 sentences.",
        })
        final_response = _openai_chat_completions(llm_url, history, openai_tools, api_key, model, max_tokens)
        if final_response:
            final_choice = final_response.get("choices", [{}])[0]
            final_msg = final_choice.get("message", {})
            final_content = final_msg.get("content") or ""
            if final_content:
                if on_text:
                    on_text(final_content)
                _agent_state.streaming_text = final_content
                history.append({"role": "assistant", "content": final_content})

    _agent_state.is_thinking = False
    if on_status:
        on_status("Idle")
    return history


# ---------------------------------------------------------------------------
# Cleanup

def cleanup() -> None:
    """Stop the MCP server subprocess. Safe to call multiple times."""
    stop_mcp_server()
    _agent_state.conversation_history.clear()
    _agent_state.streaming_text = ""
    _agent_state.reasoning_text = ""
    _agent_state.thinking_dots = 0
    _agent_state.is_thinking = False


# ---------------------------------------------------------------------------
# Connectivity diagnostics

def ping_agent(
    mcp_port: int = _MCP_SERVER_DEFAULT_PORT,
    llm_port: int = 8081,
    bridge_port: int = 9876,
) -> dict[str, Any]:
    """
    Quick connectivity check for all three back-ends.

    Returns a dict with test results suitable for display in the UI::

        {
            "bridge_server":   "OK" | "FAIL: <reason>",
            "mcp_server":      "OK (N tools)" | "FAIL: <reason>",
            "llm_health":      "OK" | "FAIL: <reason>",
            "llm_chat":        "OK" | "FAIL: <reason>",
            "all_ok":          True | False,
        }
    """
    result: dict[str, Any] = {}

    # 1 — Bridge server (raw TCP inside Blender)
    import socket as _socket_mod
    try:
        s = _socket_mod.socket(_socket_mod.AF_INET, _socket_mod.SOCK_STREAM)
        s.settimeout(3)
        s.connect(("127.0.0.1", bridge_port))
        s.close()
        result["bridge_server"] = "OK"
    except Exception as ex:
        result["bridge_server"] = "FAIL: {:s}".format(str(ex))

    # 2 — LLM health
    try:
        url = "http://127.0.0.1:{:d}/health".format(llm_port)
        with urllib.request.urlopen(url, timeout=5) as resp:
            result["llm_health"] = "OK" if resp.status == 200 else "FAIL: HTTP {:d}".format(resp.status)
    except Exception as ex:
        result["llm_health"] = "FAIL: {:s}".format(str(ex))

    # 3 — LLM chat (simple echo)
    try:
        url = _LLM_CHAT_URL.format(llm_port)
        body = {
            "messages": [{"role": "user", "content": "Say ping."}],
            "stream": False,
            "max_tokens": 32,
        }
        data_bytes = json.dumps(body).encode()
        req = urllib.request.Request(
            url,
            data=data_bytes,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode()
            data = json.loads(raw)
            choice = data.get("choices", [{}])[0]
            reply = choice.get("message", {}).get("content", "")
            result["llm_chat"] = "OK ({:s})".format(reply[:80] if reply else "(empty)")
    except Exception as ex:
        result["llm_chat"] = "FAIL: {:s}".format(str(ex))

    # 4 — MCP server (verify with a real tools/list RPC;
    # FastMCP streamable-HTTP does NOT expose /health.)
    try:
        tools = _list_tools_sync(mcp_port)
        if tools:
            result["mcp_server"] = "OK ({:d} tools)".format(len(tools))
        else:
            result["mcp_server"] = "FAIL: no tools returned"
    except Exception as ex:
        result["mcp_server"] = "FAIL: {:s}".format(str(ex))

    result["all_ok"] = all(
        v.startswith("OK") for k, v in result.items() if k != "all_ok"
    )
    return result


# ---------------------------------------------------------------------------
# Module-level: migrate vendor/deps/ out of the addon tree immediately.
# Blender 5.3+ sandbox scans the addon directory tree at load time and
# flags any subdirectory matching a known top-level Python package
# (rich/, click/, httpx/, etc.) as a policy violation — even if never
# imported.  We move vendor/deps/ to ~/.cache/bfa_coworker/vendor_deps/
# at module import time so the scan never sees the package directories.
if (Path(__file__).resolve().parent / "vendor" / "deps").is_dir():
    _get_vendor_deps_dir()