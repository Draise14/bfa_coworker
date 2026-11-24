# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Socket client for communicating with the Blender add-on.

Used by MCP "tools" that send-code to the Blender add-on.
"""

__all__ = (
    "get_connection_params",
    "send_code",
)

import json
import os
import socket

_DEFAULT_HOST = "localhost"
_DEFAULT_PORT = 9876
_TIMEOUT = 300.0
_RECV_BUFFER_SIZE = 65536


def get_connection_params() -> tuple[str, int]:
    host = os.environ.get("BFACW_HOST", _DEFAULT_HOST)
    port = int(os.environ.get("BFACW_PORT", str(_DEFAULT_PORT)))
    return host, port


_ARGS_CHARS = 400
_ARG_VALUE_CHARS = 120
_URL_CHARS = 2100


def _args_text(inputs: dict[str, object]) -> str:
    """The tool's input as short JSON text, for the bridge's activity view and approval card
    (#500): a long text value is cut on its own, so the whole stays JSON the card can read."""
    # A web address is kept whole (#637): the approval card shows it in full, and the host in it is
    # what "Always" remembers.  read_web_page refuses addresses over _URL_CHARS before it asks.
    short = {key: value[:_ARG_VALUE_CHARS] + "…" if isinstance(value, str) and len(value) > _ARG_VALUE_CHARS
             and key != "url" else value for key, value in inputs.items()}
    return json.dumps(short, ensure_ascii=False, default=str)[:_ARGS_CHARS + (_URL_CHARS if "url" in short else 0)]


def send_code(code: str, strict_json: bool) -> dict[str, object]:
    """
    Send Python code to the Blender add-on socket server for execution.

    Returns the full response dict from the add-on containing
    ``status`` (``"ok"`` or ``"error"``), ``result`` (on success),
    ``message`` (on error), and optionally ``stdout``/``stderr``
    captured during execution.

    Raises ``ConnectionError`` when Blender is unreachable or
    returns an invalid response.
    """
    host, port = get_connection_params()
    payload: dict[str, object] = {
        "type": "execute",
        "code": code,
        "strict_json": strict_json,
    }
    # Fork (#85): when launched by a Coworker window, stamp the request with
    # that window's id so the bridge can refuse it if another window owns the
    # port.  External clients don't set this and are unaffected.
    session = os.environ.get("BFACW_SESSION_ID", "")
    if session:
        payload["session"] = session
    # Threads 1 (#357): which Coworker thread calls -- the loop's request header, or the
    # env of an External Agent session's own server.  Unknown keys are fine for the bridge.
    from blmcp.tools_helpers.bounded_output import CURRENT_THREAD, CURRENT_TOOL
    thread = CURRENT_THREAD.get() or os.environ.get("BFACW_THREAD_ID", "")
    if thread:
        payload["thread"] = thread
    # Agent 9: which tool this is for, so Blender can show it (External Harness activity).
    tool, inputs = CURRENT_TOOL.get()
    if tool:
        payload["tool"] = tool
        try:
            payload["args"] = _args_text(inputs)
        except (TypeError, ValueError):
            pass
    # Engine 8: the bridge refuses requests without the user's session token.
    # The add-on and the External Harness launcher set it for this process.
    token = os.environ.get("BFACW_TOKEN", "")
    if token:
        payload["token"] = token
    request = json.dumps(payload) + "\0"

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(_TIMEOUT)
            sock.connect((host, port))
            sock.sendall(request.encode("utf-8"))

            # Read response until the null byte delimiter.
            buf = bytearray()
            while True:
                chunk = sock.recv(_RECV_BUFFER_SIZE)
                if not chunk:
                    break
                buf.extend(chunk)
                if b"\0" in buf:
                    break
    except ConnectionRefusedError as ex:
        raise ConnectionError(
            "Cannot connect to Blender at {:s}:{:d}. "
            "Ensure Blender is running with the MCP addon enabled and the server started.".format(host, port)
        ) from ex
    except socket.timeout as ex:
        # Tools 6 (#309): long jobs answer with a job id instead of holding the call.
        raise ConnectionError(
            "Blender connection timed out at {:s}:{:d} after {:.0f} s; Blender may still be busy. "
            "A long bake or render inside execute_blender_code blocks Blender: use bake_textures "
            "or render_viewport_to_path(background=True), then get_job_status.".format(host, port, _TIMEOUT)
        ) from ex
    except OSError as ex:
        # NOTE: intentionally not catching `Exception` here.
        # Callers use `ConnectionError` to trigger a fallback path;
        # a broader catch would mask real bugs as connection failures.
        raise ConnectionError(
            "Socket error communicating with Blender at {:s}:{:d}: {:s}".format(host, port, str(ex))
        ) from ex

    if not buf:
        raise ConnectionError("Empty response from Blender")

    # Parse only up to the first null byte delimiter.
    line, _sep, _rest = buf.partition(b"\0")
    try:
        response: dict[str, object] = json.loads(line.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as ex:
        raise ConnectionError(
            "Invalid response from Blender at {:s}:{:d}: {:s}".format(host, port, str(ex))
        ) from ex
    return response
