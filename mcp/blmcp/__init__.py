# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
MCP server for Blender.

Provides tools for LLM's, connecting to Blender via a bridge-server.
All tools send code to the add-on to run.
"""

__all__ = (
    "main",
)

import argparse
import importlib
import os
import pkgutil

import yaml
from mcp.server.fastmcp import FastMCP  # pylint: disable=import-error,no-name-in-module

# NOTE(@ideasman42): this was written to support LLAMA-C++'s Web UI,
# which is one of the nicer ways to run this locally.
# It is not full HTTP support because there looks to be many options for this protocol.
# This could be disabled if it no longer serves its purpose - as most agents wont use STDIO.
_USE_HTTP_SUPPORT = True

_TRANSPORTS = ("stdio", *(("http",) if _USE_HTTP_SUPPORT else ()))


def main() -> int:
    parser = argparse.ArgumentParser(description="MCP server for Blender.")
    parser.add_argument(
        "--transport", "-t",
        choices=_TRANSPORTS,
        default="stdio",
        help="Transport protocol (default: stdio).",
    )
    if _USE_HTTP_SUPPORT:
        parser.add_argument(
            "--host",
            default="127.0.0.1",
            help="Host to bind to for HTTP transports (default: 127.0.0.1).",
        )
        parser.add_argument(
            "--port", "-p",
            type=int,
            default=9191,
            help="Port to bind to for HTTP transports (default: 9191).",
        )
    args = parser.parse_args()

    # Load prompts.
    data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    with open(os.path.join(data_dir, "prompts.yml"), encoding="utf-8") as fh:
        prompts = yaml.safe_load(fh)

    mcp = FastMCP("bfa-coworker", instructions=str(prompts["initial_instructions"]))

    # Engine 9: every tool's result is kept short (a preview + a handle to the rest).
    # #479: a blocking tool runs in a worker thread, so one waiting call never holds up the rest.
    from blmcp.tools_helpers.bounded_output import bounded, off_the_loop
    _register_tool = mcp.tool

    def _bounded_tool(*args, **kwargs):
        if len(args) == 1 and callable(args[0]) and not kwargs:     # bare @mcp.tool
            return _register_tool(bounded(off_the_loop(args[0])))
        decorate = _register_tool(*args, **kwargs)
        return lambda fn: decorate(bounded(off_the_loop(fn)))
    mcp.tool = _bounded_tool  # type: ignore[method-assign]

    # Auto-discover and register all tools (they are never un-registered).
    import blmcp.tools as tools_pkg

    for _importer, modname, _ispkg in pkgutil.iter_modules(tools_pkg.__path__):
        if modname.endswith("_toolcode") or modname.startswith("_template_"):
            continue
        mod = importlib.import_module("blmcp.tools.{:s}".format(modname))
        if hasattr(mod, "register"):
            mod.register(mcp)

    # Engine 7: for agents that can follow a changing tool list (Coworker's External
    # Agent sets this), list the surface tools only; load_tools adds a domain's.
    if os.environ.get("BFACW_TOOLS_ON_DEMAND") == "1":
        from blmcp import tool_domains
        tool_domains.enable_on_demand(mcp)

    transport = args.transport
    if _USE_HTTP_SUPPORT and transport == "http":
        transport = "streamable-http"
        _configure_http(mcp, args.host, args.port)

    mcp.run(transport=transport)
    return 0


def _configure_http(mcp: FastMCP, host: str, port: int) -> None:
    """Set up the HTTP endpoint: settings, Host/Origin checks, token gate, CORS."""
    # pylint: disable-next=import-error,no-name-in-module
    from mcp.server.fastmcp.server import TransportSecuritySettings  # type: ignore[attr-defined]
    from starlette.applications import Starlette
    from starlette.middleware.cors import CORSMiddleware

    from blmcp.tools_helpers import http_auth

    mcp.settings.host = host
    mcp.settings.port = port
    mcp.settings.streamable_http_path = "/"
    mcp.settings.stateless_http = True
    # Security 1 (#345): refuse a foreign Host (DNS rebinding) or a browser
    # page that isn't served from this computer -- even when the token is off.
    mcp.settings.transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=http_auth.allowed_hosts(host, port),
        allowed_origins=http_auth.allowed_origins(),
    )

    # Add CORS middleware so browser-based clients
    # (e.g. llama.cpp web UI) can connect without preflight failures.
    _orig = mcp.streamable_http_app

    def _app_with_cors() -> Starlette:
        app = _orig()
        # Engine 8: require the session token when one was provided.
        # Added BEFORE the CORS middleware, so CORS stays the outermost
        # layer (preflights are answered and a 401 carries CORS headers).
        _token = os.environ.get("BFACW_TOKEN", "")
        if _token:
            app.add_middleware(http_auth.TokenGateMiddleware, token=_token)
        app.add_middleware(http_auth.ThreadHeaderMiddleware)     # Threads 1 (#357)
        # Pages served from this computer only (any port), not "*".
        app.add_middleware(
            CORSMiddleware,
            allow_origins=http_auth.extra_origins(),
            allow_origin_regex=http_auth.CORS_ORIGIN_REGEX,
            allow_methods=["*"],
            allow_headers=["*"],
        )
        return app

    mcp.streamable_http_app = _app_with_cors  # type: ignore[method-assign]
