# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Guards for the HTTP transport.

Session token (Tier 4i "Engine 8"): any web page open in the user's browser
can send requests to localhost.  When the server is started with
``BFACW_TOKEN`` set, :class:`TokenGateMiddleware` lets a request through only
with ``Authorization: Bearer <token>``.  CORS preflight (``OPTIONS``) and
``/health`` stay open; non-HTTP scopes (lifespan) pass through untouched.

Host and Origin (Security 1, #345), defence in depth behind the token:
:func:`allowed_hosts` feeds the MCP SDK's DNS-rebinding protection (a page on
``evil.example`` whose name was re-pointed at 127.0.0.1 still sends
``Host: evil.example``), and :func:`allowed_origins` / :data:`CORS_ORIGIN_REGEX`
let only pages served from this computer (the llama.cpp web UI, on whichever
port it runs) call the endpoint from a browser.  ``BFACW_ALLOWED_HOSTS`` /
``BFACW_ALLOWED_ORIGINS`` (comma separated) add more, for Network mode.
"""

__all__ = (
    "CORS_ORIGIN_REGEX",
    "TokenGateMiddleware",
    "allowed_hosts",
    "allowed_origins",
    "extra_origins",
)

import hmac
import json
import os
import socket
import threading
from typing import Any, Awaitable, Callable

_OPEN_PATHS = frozenset(("/health",))

_LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "[::1]")
_WILDCARD_BINDS = ("", "0.0.0.0", "::", "[::]")
_EXTRA_HOSTS_ENV = "BFACW_ALLOWED_HOSTS"
_EXTRA_ORIGINS_ENV = "BFACW_ALLOWED_ORIGINS"

# A wildcard bind looks up this computer's addresses; slow or broken DNS must
# not hold up server start (#403), so the lookup gets this long, once.
_RESOLVE_TIMEOUT = 1.0
_this_computer: list[str] | None = None

# Browser pages served from this computer, any port (Starlette's CORS check).
CORS_ORIGIN_REGEX = r"https?://(?:127\.0\.0\.1|localhost|\[::1\])(?::\d+)?"


def _env_list(name: str) -> list[str]:
    return [v.strip() for v in os.environ.get(name, "").split(",") if v.strip()]


def _primary_address() -> list[str]:
    """This computer's outward IPv4 address, without DNS (a UDP "connect" sends nothing)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 9))  # TEST-NET-1: only picks the route
            address = probe.getsockname()[0]
    except OSError:
        return []
    return [address] if address != "0.0.0.0" else []


def _this_computer_names() -> list[str]:
    """This computer's name and addresses, best effort, cached for the process.

    ``gethostbyname_ex`` has no timeout of its own, so it runs in a daemon
    thread; past :data:`_RESOLVE_TIMEOUT` the name plus the primary address
    (no DNS) is used instead.
    """
    global _this_computer
    if _this_computer is not None:
        return list(_this_computer)
    try:
        name = socket.gethostname()
    except OSError:
        return []
    found: list[str] = []

    def resolve() -> None:
        try:
            found.extend(socket.gethostbyname_ex(name)[2])
        except (OSError, UnicodeError):
            pass

    worker = threading.Thread(target=resolve, name="bfacw-host-lookup", daemon=True)
    worker.start()
    worker.join(_RESOLVE_TIMEOUT)
    addresses = _primary_address() if worker.is_alive() else list(found)
    _this_computer = [name, name.lower()] + addresses
    return list(_this_computer)


def allowed_hosts(bind_host: str, port: int) -> list[str]:
    """``Host`` header values the endpoint answers (``host:port``, the SDK's form).

    This computer's loopback names; plus the bound address when Network mode
    binds another one; for a wildcard bind (``0.0.0.0``) also this computer's
    name and addresses, best effort (see :func:`_this_computer_names`).
    """
    names = list(_LOOPBACK_HOSTS)
    host = bind_host.strip()
    if host in _WILDCARD_BINDS:
        names += _this_computer_names()
    else:
        names.append("[" + host + "]" if ":" in host and not host.startswith("[") else host)
    out: list[str] = []
    for value in ["{:s}:{:d}".format(n, port) for n in names] + _env_list(_EXTRA_HOSTS_ENV):
        if value not in out:
            out.append(value)
    return out


def extra_origins() -> list[str]:
    """Origins added with ``BFACW_ALLOWED_ORIGINS`` (exact, e.g. ``http://192.168.1.5:8080``)."""
    return _env_list(_EXTRA_ORIGINS_ENV)


def allowed_origins() -> list[str]:
    """``Origin`` values the endpoint answers, in the SDK's form (``:*`` = any port).

    A request without ``Origin`` (every non-browser client) is not affected.
    """
    out: list[str] = []
    for scheme in ("http", "https"):
        for host in _LOOPBACK_HOSTS:
            out += ["{:s}://{:s}".format(scheme, host), "{:s}://{:s}:*".format(scheme, host)]
    return out + extra_origins()


class TokenGateMiddleware:
    """Refuse HTTP requests without ``Authorization: Bearer <token>``."""

    def __init__(self, app: Callable[..., Awaitable[None]], token: str) -> None:
        self.app = app
        self._expected = "Bearer " + token

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if (scope.get("type") != "http" or scope.get("method") == "OPTIONS"
                or scope.get("path") in _OPEN_PATHS):
            await self.app(scope, receive, send)
            return
        auth = ""
        for name, value in scope.get("headers") or []:
            if name.lower() == b"authorization":
                auth = value.decode("latin-1")
                break
        if hmac.compare_digest(auth, self._expected):
            await self.app(scope, receive, send)
            return
        body = json.dumps({
            "error": "unauthorized",
            "message": "Coworker MCP server: send 'Authorization: Bearer <token>'. "
                       "The token is in Coworker's session_token file; the add-on "
                       "and its External Harness launcher send it for you.",
        }).encode("utf-8")
        await send({"type": "http.response.start", "status": 401,
                    "headers": [(b"content-type", b"application/json"),
                                (b"content-length", str(len(body)).encode("ascii"))]})
        await send({"type": "http.response.body", "body": body})
