# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Session-token gate for the HTTP transport (Tier 4i "Engine 8").

The HTTP endpoint listens on localhost and allows any origin (so browser
based MCP clients work), which means any web page open in the user's browser
could send it requests.  When the server is started with ``BFACW_TOKEN`` set,
this ASGI middleware lets a request through only with
``Authorization: Bearer <token>``.  CORS preflight (``OPTIONS``) and
``/health`` stay open; non-HTTP scopes (lifespan) pass through untouched.
"""

__all__ = (
    "TokenGateMiddleware",
)

import hmac
import json
from typing import Any, Awaitable, Callable

_OPEN_PATHS = frozenset(("/health",))


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
