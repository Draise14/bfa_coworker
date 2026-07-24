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
)

import asyncio
import concurrent.futures
import json
import os
import shutil
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


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
    conversation_history: list[dict[str, Any]] = field(default_factory=list)
    streaming_text: str = ""


_agent_state = AgentState()


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


def start_mcp_server(
    port: int = _MCP_SERVER_DEFAULT_PORT,
    blender_host: str = "localhost",
    blender_port: int = 9876,
) -> subprocess.Popen | None:
    """
    Launch the MCP server as a subprocess with HTTP transport.

    Returns the ``Popen`` handle, or ``None`` on failure.
    """
    global _mcp_server_process

    if _mcp_server_process is not None and _mcp_server_process.poll() is None:
        _agent_state.error = "MCP server is already running"
        return None

    # Find the MCP server executable.
    mcp_exe = (
        shutil.which("bfa-coworker-mcp") or
        shutil.which("bfa-coworker-mcp.exe") or
        shutil.which("bfa-coworker-mcp.bat")
    )

    _use_module = False
    env = os.environ.copy()
    env["BFACW_HOST"] = blender_host
    env["BFACW_PORT"] = str(blender_port)

    if not mcp_exe:
        # Look for the MCP virtual environment.  Walk up from this
        # file to find the workspace root, then check:
        #   mcp/.venv/Scripts/python.exe  (development layout)
        #   vendor/python_env/Scripts/python.exe  (installed-addon layout)
        _this_dir = Path(__file__).resolve().parent
        _py = None
        _p = _this_dir
        for _depth in range(6):
            _candidate = _p / "mcp" / ".venv" / "Scripts" / "python.exe"
            if _candidate.is_file():
                _py = _candidate
                break
            _p = _p.parent

        # Also check vendor/python_env/ (installed addon layout).
        if not _py:
            _candidate = _this_dir / "vendor" / "python_env" / "Scripts" / "python.exe"
            if _candidate.is_file():
                _py = _candidate

        # Hard fallback.
        if not _py:
            _candidate = Path("c:/bfa_coworker/mcp/.venv/Scripts/python.exe")
            if _candidate.is_file():
                _py = _candidate

        if _py:
            mcp_exe = str(_py)
            _use_module = True
            # Ensure the vendor venv's site-packages is on PYTHONPATH so
            # that blmcp (and its dependencies) are importable even when
            # editable-install metadata is missing or stale.
            _sp = str(Path(mcp_exe).resolve().parent.parent / "Lib" / "site-packages")
            existing = env.get("PYTHONPATH", "")
            env["PYTHONPATH"] = _sp + (os.pathsep + existing if existing else "")
        else:
            mcp_exe = shutil.which("python") or "python"
            _use_module = True

    if not mcp_exe:
        _agent_state.error = "Cannot find Python to run MCP server"
        return None

    try:
        if _use_module:
            print("[🛠️Coworker] start_mcp_server: running {:s} -m blmcp with PYTHONPATH={:s}".format(mcp_exe, env.get("PYTHONPATH", "(unset)")))
            proc = subprocess.Popen(
                [mcp_exe, "-m", "blmcp", "--transport", "http", "--port", str(port)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
            )
        elif mcp_exe:
            proc = subprocess.Popen(
                [mcp_exe, "--transport", "http", "--port", str(port)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
            )
        else:
            # Final fallback: try system python.
            proc = subprocess.Popen(
                [shutil.which("python") or "python", "-m", "blmcp",
                 "--transport", "http", "--port", str(port)],
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
    print("[🛠️Coworker] start_mcp_server: command = {:s}".format(
        str(mcp_exe or "python -m blmcp")))
    print("[🛠️Coworker] start_mcp_server: BFACW_HOST={:s} BFACW_PORT={:d}".format(
        blender_host, blender_port))

    # Quick health check — read stderr for startup errors.
    import time
    time.sleep(0.5)
    if proc.poll() is not None:
        # Process already exited — read stderr.
        stderr_output = proc.stderr.read().decode(errors="replace") if proc.stderr else ""
        print("[🛠️Coworker] start_mcp_server: process already exited with code {:d}".format(
            proc.returncode))
        print("[🛠️Coworker] start_mcp_server: stderr = {:s}".format(stderr_output[:500]))
        _agent_state.error = "MCP server exited immediately: {:s}".format(stderr_output[:200])
        _agent_state.mcp_server_running = False
        _mcp_server_process = None
        return None

    return proc


def stop_mcp_server() -> None:
    """Terminate the MCP server subprocess."""
    global _mcp_server_process

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
    """Synchronous wrapper for listing MCP tools."""
    print("[🛠️Coworker] _list_tools_sync: port={:d}".format(port))
    future = schedule_coro(list_mcp_tools(port))
    try:
        result = future.result(timeout=15)
        print("[🛠️Coworker] _list_tools_sync: got {:d} tools".format(len(result)) if result else "[🛠️Coworker] _list_tools_sync: got 0 tools")
        return result
    except Exception as ex:  # pylint: disable=broad-exception-caught
        print("[🛠️Coworker] _list_tools_sync: FAILED — {:s}".format(str(ex)))
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
        print("[🛠️Coworker] _openai_chat_completions: FAILED — {:s}".format(str(ex)))
        _agent_state.error = "LLM request failed: {:s}".format(str(ex))
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
    if llm_url is None:
        # Use local llama-server default.  Read the configured port
        # from llm_manager so we stay in sync.
        from . import llm_manager as _llm_mgr
        _llm_cfg = _llm_mgr.get_config()
        llm_url = _LLM_CHAT_URL.format(_llm_cfg.local_port)
    else:
        # Remote API URL — ensure it ends with /v1/chat/completions.
        base = llm_url.rstrip("/")
        if not base.endswith("/chat/completions"):
            if base.endswith("/v1"):
                llm_url = "{:s}/chat/completions".format(base)
            else:
                llm_url = "{:s}/v1/chat/completions".format(base)

    iterations = 0
    while iterations < _MAX_TOOL_ITERATIONS:
        iterations += 1

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