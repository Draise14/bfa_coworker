# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Coworker's own maintenance code has its own route into the bridge (#414).

The local loop's bookkeeping -- the scene snapshot, undo bookmarks, the
cleanup and restore after a failed step (``agent_controller``) and the
co-work lock / unlock (``co_work_guard``) -- used to travel as
``execute_blender_code`` through the MCP server.  Since #409 that tool's code
is always fully checked, so an object the model named ``wm.save_mainfile``
would make a recovery step ask first or be refused.

This route is how that code reaches the bridge instead:

* **Straight to this window's bridge socket**, never through the MCP server:
  it is not an MCP tool, so the model can't list or call it, and the MCP
  server has nothing to forward.
* **Stamped with a key only this Blender process knows.**  :data:`_KEY` is
  made when the add-on loads and lives in memory only: it is not written to
  disk, put in the environment, or given to the MCP server or an External
  Agent (unlike the session token, which the MCP server and Harness clients
  are handed).  The bridge runs a :data:`TOOL` request unchecked only when the
  key matches; without it the request is refused.
* **Names travel as data.**  The code is fixed text; object and collection
  names come in the request's ``data`` (the code reads them from ``data``), so
  a name can never read as code or trip a text check.

Deliberately free of ``bpy``; :func:`call` must run on a worker thread (the
bridge is serviced on the main thread, which would deadlock).
"""

from __future__ import annotations

__all__ = (
    "TOOL",
    "call",
    "is_internal",
    "make_request",
)

import hmac
import json
import secrets
import socket
from typing import Any

# The request's tool name.  No MCP tool has it, and the bridge refuses it without the key.
TOOL = "coworker_internal"
# This process only, memory only (see the module docstring).
_KEY = secrets.token_urlsafe(32)

_TIMEOUT = 60.0
_RECV_BUFFER_SIZE = 65536


def is_internal(request: dict[str, Any]) -> bool:
    """*request* came by this route: the :data:`TOOL` name and this process's key."""
    if str(request.get("tool") or "") != TOOL:
        return False
    given = request.get("internal")
    return isinstance(given, str) and hmac.compare_digest(given, _KEY)


def make_request(code: str, data: dict[str, Any] | None = None, thread: str = "") -> dict[str, Any]:
    """The bridge request for *code*, with *data* (names etc.) as JSON arguments."""
    from . import session_identity
    request: dict[str, Any] = {
        "type": "execute",
        "tool": TOOL,
        "internal": _KEY,
        "code": code,
        "data": data or {},
        # Coworker's own code: its result is always plain JSON.
        "strict_json": True,
        "session": session_identity.SESSION_ID,
    }
    if thread:
        request["thread"] = thread
    if session_identity.token_required():
        request["token"] = session_identity.session_token()
    return request


def _bridge_address() -> tuple[str, int]:
    from . import mcp_to_blender_server as bridge
    host = str(bridge.get_actual_host() or "")
    if host in ("", "0.0.0.0", "::"):
        host = "127.0.0.1"
    return host, int(bridge.get_actual_port() or 0)


def call(code: str, data: dict[str, Any] | None = None, thread: str = "") -> str:
    """Run *code* in this window's bridge; return its response as JSON text (the same
    shape ``execute_blender_code`` answered with), or an error line when unreachable."""
    host, port = _bridge_address()
    if not port:
        return "Error calling {:s}: the bridge isn't running".format(TOOL)
    payload = (json.dumps(make_request(code, data, thread)) + "\0").encode("utf-8")
    buf = bytearray()
    try:
        with socket.create_connection((host, port), timeout=_TIMEOUT) as sock:
            sock.sendall(payload)
            while b"\0" not in buf:
                chunk = sock.recv(_RECV_BUFFER_SIZE)
                if not chunk:
                    break
                buf.extend(chunk)
    except OSError as ex:
        return "Error calling {:s}: {:s}".format(TOOL, str(ex))
    line = bytes(buf).partition(b"\0")[0]
    try:
        return json.dumps(json.loads(line.decode("utf-8")))
    except (ValueError, UnicodeDecodeError):
        return "Error calling {:s}: no valid answer from the bridge".format(TOOL)
