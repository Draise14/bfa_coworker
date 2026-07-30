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
    "stop_mcp_server",
    "list_mcp_tools",
    "run_conversation_turn",
    "cleanup",
    "ping_agent",
    "check_ports_available",
)

import asyncio
import concurrent.futures
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def _ensure_vendor_on_path() -> None:
    """Add vendor/deps/ to sys.path so httpx/pydantic imports work in-process.

    Uses ``os.add_dll_directory`` on Windows to avoid a Blender 5.2+ sandbox
    policy violation that bans ``sys.path.insert`` at the addon level.
    """
    agent_dir = Path(__file__).resolve().parent
    vendor_deps = agent_dir / "vendor" / "deps"
    if not vendor_deps.is_dir():
        return
    # On Windows, use os.add_dll_directory so pywin32 DLLs are found.
    if sys.platform == "win32":
        # ``add_dll_directory`` is idempotent for repeated calls with the same path.
        os.add_dll_directory(str(vendor_deps))
    # Only add to sys.path if not already present (avoids a Blender 5.2 policy warning).
    if str(vendor_deps) not in sys.path:
        sys.path.append(str(vendor_deps))


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
                import yaml  # pylint: disable=import-error
                with open(str(prompt_path), encoding="utf-8") as fh:
                    prompts = yaml.safe_load(fh)
                _system_prompt = str(prompts.get("initial_instructions", ""))
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
        "Be concise. Execute code to complete the user's request, "
        "then respond with a brief summary of what was done. "
        "Do NOT repeat tool calls that have already succeeded."
    )
    return _system_prompt


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

    Returns the concatenated text or an error string.
    """
    result = _parse_sse_json(raw)
    if result is None:
        return "Error: empty or unparseable SSE response"
    if "error" in result:
        return "Error: {:s}".format(str(result["error"]))
    content = result.get("result", {}).get("content", [])
    texts = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            texts.append(block.get("text", ""))
    return "\n".join(texts) if texts else "Error: no text content in tool result"


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

    * ``vendor/deps/`` — pip-installed pure-Python dependencies
      (mcp, pyyaml, docutils, and their transitive deps).
    * ``vendor/`` — parent of ``vendor/blmcp/``, so ``import blmcp``
      resolves to ``vendor/blmcp/__init__.py``.

    If a directory does not exist, it is silently omitted so the addon
    can fall back gracefully during development.
    """
    this_dir = Path(__file__).resolve().parent
    vendor_dir = this_dir / "vendor"
    parts: list[str] = []

    deps_dir = vendor_dir / "deps"
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
    """Check that vendor/deps/ exists with required packages; auto-install if missing.

    Handles the case where a user installs the addon from source
    (e.g. by copying the addon directory) without running ``build_addon.py``
    first.  If ``vendor/deps/`` is missing or empty, we attempt to install
    the required packages using Blender's ``pip``.

    Returns ``True`` if the deps are available (or were installed), ``False``
    if installation failed.
    """
    this_dir = Path(__file__).resolve().parent
    deps_dir = this_dir / "vendor" / "deps"

    # Quick check: does vendor/deps/ exist and contain mcp?
    if deps_dir.is_dir() and (deps_dir / "mcp" / "__init__.py").is_file():
        return True

    print("[🛠️Coworker] _ensure_vendor_deps: vendor/deps/ is missing or empty — attempting auto-install...")

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
                pywin32_system32 = this_dir / "vendor" / "deps" / "pywin32_system32"
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

    env = os.environ.copy()
    env["BFACW_HOST"] = blender_host
    env["BFACW_PORT"] = str(blender_port)

    # --- Resolution order ---

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
        # Ensure vendor dependencies are available (auto-install if missing).
        if not _ensure_vendor_deps():
            _agent_state.error = (
                "MCP server dependencies not found in vendor/deps/. "
                "Run 'python build_addon.py' to build the extension, "
                "or install manually: pip install --target vendor/deps/ mcp[cli] pyyaml docutils"
            )
            return None

        # Build PYTHONPATH from vendor directories.
        vendor_pythonpath = _find_vendor_pythonpath()
        existing_pp = env.get("PYTHONPATH", "")
        if vendor_pythonpath:
            env["PYTHONPATH"] = vendor_pythonpath + (os.pathsep + existing_pp if existing_pp else "")

        # On Windows, pywin32 needs its _system32/ DLL directory on PATH
        # so that ``import pywintypes`` can find pywintypes*.dll at runtime.
        # vendor/deps/ is not a site-packages dir, so .pth files are ignored.
        if sys.platform == "win32":
            agent_dir = this_dir = Path(__file__).resolve().parent
            pywin32_system32 = agent_dir / "vendor" / "deps" / "pywin32_system32"
            if pywin32_system32.is_dir():
                env["PATH"] = str(pywin32_system32) + os.pathsep + env.get("PATH", "")

        blender_py = _find_blender_python()
        if blender_py:
            mcp_exe = blender_py
            use_module = True
            print("[🛠️Coworker] start_mcp_server: using Blender's Python at {:s}".format(mcp_exe))

    # 3. Last resort: system python.
    if not mcp_exe:
        mcp_exe = shutil.which("python") or "python"
        use_module = True
        print("[🛠️Coworker] start_mcp_server: falling back to system python at {:s}".format(mcp_exe))

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
            agent_dir = Path(__file__).resolve().parent
            deps_dir = agent_dir / "vendor" / "deps"
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
# MCP tool listing

async def list_mcp_tools(port: int = _MCP_SERVER_DEFAULT_PORT) -> list[dict[str, Any]]:
    """
    Return the list of tools from the MCP server via HTTP.

    Tries both the streamable-http tool listing endpoint and the
    standard MCP list-tools mechanism.
    """
    url = "http://127.0.0.1:{:d}/".format(port)
    print("[🛠️Coworker] list_mcp_tools: trying {:s}".format(url))

    # Lazy path setup (avoids policy violation at module level).
    _ensure_vendor_on_path()

    try:
        # Try the MCP streamable-HTTP POST endpoint first.
        import httpx  # pylint: disable=import-error
        async with httpx.AsyncClient(timeout=10) as client:
            payload = {"jsonrpc": "2.0", "id": "1", "method": "tools/list"}
            print("[🛠️Coworker] list_mcp_tools: POST {:s} with {:s}".format(url, json.dumps(payload)))
            resp = await client.post(url, json=payload)
            print("[🛠️Coworker] list_mcp_tools: status={:d}".format(resp.status_code))
            if resp.status_code == 200:
                data = resp.json()
                tools = data.get("result", {}).get("tools", [])
                print("[🛠️Coworker] list_mcp_tools: httpx returned {:d} tools".format(len(tools)))
                return tools
            print("[🛠️Coworker] list_mcp_tools: httpx unexpected status {:d}".format(resp.status_code))
    except Exception as ex:  # pylint: disable=broad-exception-caught
        print("[🛠️Coworker] list_mcp_tools: httpx failed — {:s}".format(str(ex)))

    # Fallback: use urllib (synchronous, but simpler).
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
) -> dict[str, Any] | None:
    """POST to a chat completions endpoint and return the parsed JSON response.

    *model* — when provided, included in the request body. Required for
    remote APIs (OpenRouter, OpenAI, etc.). Omitted for local llama-server
    which auto-detects the model.
    """
    body: dict[str, Any] = {
        "messages": messages,
        "stream": False,
        # Cap output so the model doesn't generate endlessly.
        "max_tokens": 4096,
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
            return result
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as ex:
        print("[🛠️Coworker] _call_mcp_tool_sync: FAILED — {:s}".format(str(ex)))
        return "Error calling tool '{:s}': {:s}".format(tool_name, str(ex))


def run_conversation_turn(
    user_message: str,
    on_text: Callable[[str], None] | None = None,
    on_status: Callable[[str], None] | None = None,
    llm_url: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
    mcp_port: int = _MCP_SERVER_DEFAULT_PORT,
) -> list[dict[str, Any]]:
    """
    Run a full conversation turn.

    1. Prepends system prompt (if not already present).
    2. Appends user message to history.
    3. Sends to LLM, handles tool calls via MCP.
    4. Returns updated conversation history.

    This is a BLOCKING call — run it via ``schedule_coro`` or in a thread.
    """
    clear_stop()
    history = _agent_state.conversation_history

    # Ensure the first message is the system prompt.
    if not history or history[0].get("role") != "system":
        system_text = _get_system_prompt()
        history.insert(0, {"role": "system", "content": system_text})
        print("[🛠️Coworker] run_conversation_turn: inserted system prompt ({:d} chars)".format(
            len(system_text)))

    history.append({"role": "user", "content": user_message})

    # Get MCP tools.
    tools = _list_tools_sync(mcp_port)
    openai_tools = _mcp_tools_to_openai(tools) if tools else []

    if on_status:
        on_status("Thinking...")
    _agent_state.is_thinking = True
    _agent_state.streaming_text = ""

    # Determine LLM URL.
    llm_port_local: int | None = None
    if llm_url is None:
        # Use local llama-server default.  Read the configured port
        # from llm_manager so we stay in sync.
        from . import llm_manager as _llm_mgr
        _llm_cfg = _llm_mgr.get_config()
        llm_url = _LLM_CHAT_URL.format(_llm_cfg.local_port)
        llm_port_local = _llm_cfg.local_port
    else:
        # Remote API URL — ensure it ends with /v1/chat/completions.
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

        response = _openai_chat_completions(llm_url, history_to_send, openai_tools, api_key, model)
        if response is None:
            _agent_state.is_thinking = False
            _agent_state.error = "No response from LLM"
            if on_status:
                on_status("Error: No response from LLM")
            return history

        choice = response.get("choices", [{}])[0]
        msg = choice.get("message", {})
        finish_reason = choice.get("finish_reason", "")

        # Extract text content.
        content = msg.get("content") or ""
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
                    on_status("Running tool: {:s}".format(tool_name))

                # Call the MCP tool.
                result_text = _call_mcp_tool_sync(tool_name, args, mcp_port)

                # Add tool result to history.
                history.append({
                    "role": "tool",
                    "tool_call_id": tool_id,
                    "name": tool_name,
                    "content": result_text,
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
        final_response = _openai_chat_completions(llm_url, history, openai_tools, api_key, model)
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