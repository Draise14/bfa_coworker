# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Deferred response handling for Blender background jobs.

When tool-code starts a background job (e.g. rendering with
``INVOKE_DEFAULT``), the response cannot be sent immediately. This
module holds the client connection open and polls a checker callable
until the operation completes, then sends the result.

The checker (``check_fn``) is called on the server's standard timer
which starts at an active interval but backs off when idle.

Checkers should be lightweight (e.g. check a flag or file existence)
so they don't block the UI, yet return promptly so the user is not left waiting after the job finishes.

*on_answer* (#423) is called once with the response that went out -- or an
error when the client hung up or the bridge stopped -- so the activity view
can finish the call's entry instead of leaving it "waiting".

"""

__all__ = (
    "add",
    "close_all",
    "has_pending",
    "poll",
)

import json
import socket
import time
import traceback
from collections.abc import Callable

from . import answer_envelope
from .mcp_to_blender_server import _encode_response

# Total wall-time in seconds allowed for a background task (e.g. rendering) to complete.
# When exceeded, an error response is sent and the connection is closed.
# The background task itself continues to run in Blender.
# One hour is long for what should typically be an interactive experience,
# but renders can take a long time and it's not desirable for them to simply give up.
_DEFERRED_TIMEOUT = (60.0 * 60.0)

# Tools 6 (#309): a checker marked with a ``job_kind`` attribute (a render, see
# ``_template_deferred_tool_check_for_file_output.py``) that is still running
# after this long is handed to ``long_jobs``, and the client gets its job id.
# The callers give up much sooner than the hour above: the local loop's HTTP
# call after 60 s, the MCP client's socket after 300 s.  So a short render
# answers as before, and a long one never times out the agent.
_HANDOFF_AFTER = 45.0


class _DeferredClient:
    """
    A client connection waiting for a background job to complete.
    """

    __slots__ = (
        "conn",
        "check_fn",
        "strict_json",
        "stdout",
        "stderr",
        "deadline",
        "handoff_at",
        "on_answer",
    )

    def __init__(
        self,
        conn: socket.socket,
        check_fn: Callable[[], dict[str, object] | None],
        strict_json: bool,
        stdout: str,
        stderr: str,
        on_answer: Callable[[dict[str, object]], None] | None = None,
    ) -> None:
        self.conn: socket.socket = conn
        self.check_fn: Callable[[], dict[str, object] | None] = check_fn
        self.strict_json: bool = strict_json
        self.stdout: str = stdout
        self.stderr: str = stderr
        self.deadline: float = time.monotonic() + _DEFERRED_TIMEOUT
        self.handoff_at: float = time.monotonic() + _HANDOFF_AFTER
        self.on_answer: Callable[[dict[str, object]], None] | None = on_answer


# Connections waiting for a background job to finish, polled each timer tick.
_deferred_clients: list[_DeferredClient] = []


def _answered(dc: _DeferredClient, response: dict[str, object]) -> None:
    """Tell *dc*'s ``on_answer`` (once) how the call ended."""
    on_answer, dc.on_answer = dc.on_answer, None
    if on_answer is not None:
        try:
            on_answer(response)
        except Exception:  # pylint: disable=broad-exception-caught
            traceback.print_exc()


def _send_and_close(dc: _DeferredClient, response: dict[str, object]) -> None:
    try:
        dc.conn.sendall(_encode_response(response))
    except OSError:
        pass
    _answered(dc, response)
    try:
        dc.conn.close()
    except OSError:
        pass
    try:
        _deferred_clients.remove(dc)
    except ValueError:
        pass


def _is_disconnected(conn: socket.socket) -> bool:
    """
    Return ``True`` if the remote end has closed the connection.
    """
    try:
        data = conn.recv(1, socket.MSG_PEEK)
        # Empty data means the peer closed the connection.
        return len(data) == 0
    except BlockingIOError:
        # No data available - connection is still alive.
        return False
    except OSError:
        return True


def _hand_off(dc: _DeferredClient) -> None:
    """Answer *dc* with a job id; ``long_jobs`` keeps checking the job."""
    from . import long_jobs
    kind = str(getattr(dc.check_fn, "job_kind", ""))
    job_id = long_jobs.start(kind, str(getattr(dc.check_fn, "job_label", "") or kind.lower()), dc.check_fn,
                             getattr(dc.check_fn, "job_on_finish", None))
    response: dict[str, object] = {"status": "ok", "result": {
        "status": "ok", "state": "running", "job_id": job_id,
        "message": ("Still working after {:.0f} s, so it carries on in the background. "
                    "Call get_job_status(job_id) to wait for the result.").format(_HANDOFF_AFTER),
    }}
    if dc.stdout:
        response["stdout"] = dc.stdout
    if dc.stderr:
        response["stderr"] = dc.stderr
    _send_and_close(dc, response)


def add(
    conn: socket.socket,
    check_fn: Callable[[], dict[str, object] | None],
    strict_json: bool,
    stdout: str,
    stderr: str,
    on_answer: Callable[[dict[str, object]], None] | None = None,
) -> None:
    """
    Register a deferred client to be polled for completion.

    *on_answer* gets the response once it went out (see the module docstring).
    """
    _deferred_clients.append(_DeferredClient(conn, check_fn, strict_json, stdout, stderr, on_answer))


def poll() -> bool:
    """
    Check all deferred clients for completion.

    Return ``True`` if at least one client was resolved or removed.
    """
    did_work = False
    for dc in _deferred_clients[:]:
        # Check for client disconnection.
        if _is_disconnected(dc.conn):
            try:
                dc.conn.close()
            except OSError:
                pass
            _answered(dc, {"status": "error", "message": "The client hung up before Blender answered."})
            try:
                _deferred_clients.remove(dc)
            except ValueError:
                pass
            did_work = True
            continue

        # Check for timeout.
        if time.monotonic() > dc.deadline:
            _send_and_close(dc, {
                "status": "error",
                "message": "Deferred operation timed out after {:.0f} seconds".format(_DEFERRED_TIMEOUT),
            })
            did_work = True
            continue

        # Call the checker.
        try:
            result = dc.check_fn()
        except Exception:  # pylint: disable=broad-exception-caught
            _send_and_close(dc, {
                "status": "error",
                "message": traceback.format_exc(),
            })
            did_work = True
            continue

        if result is None:
            # Still pending: a long job goes on in the background with a job id.
            if getattr(dc.check_fn, "job_kind", "") and time.monotonic() > dc.handoff_at:
                _hand_off(dc)
                did_work = True
            continue

        if not isinstance(result, dict):
            _send_and_close(dc, {
                "status": "error",
                "message": "check_is_finished must return None or dict, not {:s}".format(
                    type(result).__name__,
                ),
            })
            did_work = True
            continue

        # Validate JSON serializability when strict_json is set.
        if dc.strict_json:
            try:
                json.dumps(result)
            except (TypeError, ValueError) as ex:
                _send_and_close(dc, {
                    "status": "error",
                    "message": "Deferred result is not JSON-serializable: {:s}".format(str(ex)),
                })
                did_work = True
                continue

        # The standard envelope around a tool-code checker's bare result; a checker that
        # waited for the user answers with the whole answer already (#487: one envelope).
        response: dict[str, object] = dict(answer_envelope.answer_of(dc.check_fn, result))
        if dc.stdout:
            response.setdefault("stdout", dc.stdout)
        if dc.stderr:
            response.setdefault("stderr", dc.stderr)
        _send_and_close(dc, response)
        did_work = True

    return did_work


def has_pending() -> bool:
    """
    Return ``True`` if there are deferred clients awaiting completion.
    """
    return bool(_deferred_clients)


def close_all() -> None:
    """
    Close all deferred client connections without sending responses.
    """
    for dc in _deferred_clients:
        try:
            dc.conn.close()
        except OSError:
            pass
        _answered(dc, {"status": "error", "message": "The bridge stopped before Blender answered."})
    _deferred_clients.clear()
