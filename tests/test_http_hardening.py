# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for the MCP HTTP endpoint's Host / Origin checks (Security 1, #345).

1. ``http_auth.allowed_hosts`` / ``allowed_origins``: loopback names on the
   bound port; the bound address in Network mode; extras from the environment.
2. The real endpoint (``blmcp._configure_http`` on a FastMCP app, driven with
   Starlette's test client): a foreign ``Host`` (DNS rebinding) or a browser
   ``Origin`` that isn't this computer is refused, with or without the token;
   the requests Coworker's own clients send still pass.
3. Coworker's own HTTP clients use a ``Host`` the endpoint accepts.

Needs the MCP SDK (``mcp``), Starlette and httpx -- the server's own
dependencies; skipped without them.  No Blender.

Run with::

    python -m unittest tests.test_http_hardening -v
"""

__all__ = ()

import importlib.util
import json
import logging
import os
import sys
import threading
import time
import unittest
import unittest.mock
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_PORT = 9191


def _load_http_auth():
    path = _REPO / "mcp" / "blmcp" / "tools_helpers" / "http_auth.py"
    spec = importlib.util.spec_from_file_location("http_auth_hardening_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _have(*names):
    return all(importlib.util.find_spec(n) is not None for n in names)


class TestAllowLists(unittest.TestCase):

    def setUp(self):
        self.mod = _load_http_auth()
        patcher = unittest.mock.patch.dict(os.environ)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("BFACW_ALLOWED_HOSTS", None)
        os.environ.pop("BFACW_ALLOWED_ORIGINS", None)

    def test_loopback_bind(self):
        self.assertEqual(self.mod.allowed_hosts("127.0.0.1", 9191),
                         ["127.0.0.1:9191", "localhost:9191", "[::1]:9191"])
        self.assertEqual(self.mod.allowed_hosts("::1", 9200)[-1], "[::1]:9200")
        self.assertEqual(len(self.mod.allowed_hosts("::1", 9200)), 3)

    def test_network_bind_adds_the_address(self):
        self.assertIn("192.168.1.5:9191", self.mod.allowed_hosts("192.168.1.5", 9191))
        self.assertIn("[fe80::1]:9191", self.mod.allowed_hosts("fe80::1", 9191))

    def test_wildcard_bind_adds_this_computer(self):
        with unittest.mock.patch.object(self.mod.socket, "gethostname", return_value="STUDIO-PC"), \
                unittest.mock.patch.object(self.mod.socket, "gethostbyname_ex",
                                           return_value=("STUDIO-PC", [], ["192.168.1.7"])):
            hosts = self.mod.allowed_hosts("0.0.0.0", 9191)
        for want in ("127.0.0.1:9191", "STUDIO-PC:9191", "studio-pc:9191", "192.168.1.7:9191"):
            self.assertIn(want, hosts)
        self.assertNotIn("0.0.0.0:9191", hosts)

    def test_wildcard_bind_survives_slow_dns(self):
        # #403: a resolver that hangs must not hold up server start.
        release = threading.Event()
        self.addCleanup(release.set)
        calls = []

        def hanging_lookup(name):
            calls.append(name)
            release.wait(5)
            return (name, [], ["10.9.9.9"])

        with unittest.mock.patch.object(self.mod.socket, "gethostname", return_value="STUDIO-PC"), \
                unittest.mock.patch.object(self.mod.socket, "gethostbyname_ex", side_effect=hanging_lookup), \
                unittest.mock.patch.object(self.mod, "_primary_address", return_value=["192.168.1.7"],
                                           create=True):
            start = time.monotonic()
            hosts = self.mod.allowed_hosts("0.0.0.0", 9191)
            elapsed = time.monotonic() - start
            again = self.mod.allowed_hosts("::", 9200)
        self.assertLess(elapsed, 2.0)
        for want in ("127.0.0.1:9191", "localhost:9191", "[::1]:9191", "STUDIO-PC:9191", "192.168.1.7:9191"):
            self.assertIn(want, hosts)
        self.assertNotIn("10.9.9.9:9191", hosts)
        # Looked up once per process: the second start doesn't wait again.
        self.assertEqual(calls, ["STUDIO-PC"])
        self.assertIn("192.168.1.7:9200", again)
        self.assertIn("127.0.0.1:9200", again)

    def test_environment_extras(self):
        os.environ["BFACW_ALLOWED_HOSTS"] = "studio.lan:9191, "
        os.environ["BFACW_ALLOWED_ORIGINS"] = "http://192.168.1.5:8080"
        self.assertEqual(self.mod.allowed_hosts("127.0.0.1", 9191)[-1], "studio.lan:9191")
        self.assertEqual(self.mod.extra_origins(), ["http://192.168.1.5:8080"])
        self.assertIn("http://192.168.1.5:8080", self.mod.allowed_origins())

    def test_origins_are_this_computer_only(self):
        origins = self.mod.allowed_origins()
        self.assertIn("http://127.0.0.1:*", origins)
        self.assertIn("http://localhost:*", origins)
        self.assertNotIn("*", origins)
        import re
        regex = re.compile(self.mod.CORS_ORIGIN_REGEX)
        for ok in ("http://127.0.0.1:8081", "http://localhost:8080", "http://[::1]:8081", "http://localhost"):
            self.assertTrue(regex.fullmatch(ok), ok)
        for bad in ("https://evil.example", "http://127.0.0.1.evil.example", "http://localhost.evil.example:80",
                    "null"):
            self.assertFalse(regex.fullmatch(bad), bad)


_LIST_TOOLS = json.dumps({"jsonrpc": "2.0", "id": "1", "method": "tools/list"})
_HEADERS = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}


@unittest.skipUnless(_have("mcp", "starlette", "httpx", "yaml"), "needs the MCP server's dependencies")
class TestEndpoint(unittest.TestCase):

    def setUp(self):
        sys.path.insert(0, str(_REPO / "mcp"))
        self.addCleanup(sys.path.remove, str(_REPO / "mcp"))
        patcher = unittest.mock.patch.dict(os.environ)
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in ("BFACW_TOKEN", "BFACW_ALLOWED_HOSTS", "BFACW_ALLOWED_ORIGINS"):
            os.environ.pop(name, None)
        logging.disable(logging.CRITICAL)      # the SDK logs every request
        self.addCleanup(logging.disable, logging.NOTSET)

    def _client(self, bind_host="127.0.0.1", token=""):
        import blmcp
        from mcp.server.fastmcp import FastMCP
        from starlette.testclient import TestClient
        if token:
            os.environ["BFACW_TOKEN"] = token
        mcp = FastMCP("hardening-test")

        @mcp.tool()
        def ping() -> str:
            """Answer pong."""
            return "pong"

        blmcp._configure_http(mcp, bind_host, _PORT)
        client = TestClient(mcp.streamable_http_app(), base_url="http://127.0.0.1:{:d}".format(_PORT))
        client.__enter__()      # runs the lifespan (the session manager)
        self.addCleanup(client.__exit__, None, None, None)
        return client

    def _post(self, client, **headers):
        return client.post("/", content=_LIST_TOOLS, headers={**_HEADERS, **headers})

    def test_local_clients_pass(self):
        client = self._client()
        for host in ("127.0.0.1:9191", "localhost:9191", "[::1]:9191"):
            resp = self._post(client, host=host)
            self.assertEqual(resp.status_code, 200, host)
            self.assertIn("ping", resp.text)

    def test_foreign_host_refused(self):
        client = self._client()
        for host in ("evil.example", "evil.example:9191", "127.0.0.1:9999", "192.168.1.5:9191"):
            self.assertEqual(self._post(client, host=host).status_code, 421, host)

    def test_llama_web_ui_origin_passes_with_cors(self):
        client = self._client()
        for origin in ("http://127.0.0.1:8081", "http://localhost:8080"):
            resp = self._post(client, origin=origin)
            self.assertEqual(resp.status_code, 200, origin)
            self.assertEqual(resp.headers.get("access-control-allow-origin"), origin)
            pre = client.options("/", headers={"origin": origin, "access-control-request-method": "POST",
                                               "access-control-request-headers": "content-type,authorization"})
            self.assertEqual(pre.status_code, 200, origin)

    def test_foreign_origin_refused(self):
        client = self._client()
        resp = self._post(client, origin="https://evil.example")
        self.assertEqual(resp.status_code, 403)
        self.assertNotIn("access-control-allow-origin", resp.headers)
        pre = client.options("/", headers={"origin": "https://evil.example", "access-control-request-method": "POST"})
        self.assertEqual(pre.status_code, 400)
        self.assertNotIn("access-control-allow-origin", pre.headers)

    def test_refused_even_with_the_token(self):
        client = self._client(token="t" * 32)
        auth = {"authorization": "Bearer " + "t" * 32}
        self.assertEqual(self._post(client, **auth).status_code, 200)
        self.assertEqual(self._post(client).status_code, 401)
        self.assertEqual(self._post(client, host="evil.example:9191", **auth).status_code, 421)
        self.assertEqual(self._post(client, origin="https://evil.example", **auth).status_code, 403)

    def test_network_mode_bind_address_passes(self):
        client = self._client(bind_host="192.168.1.5")
        self.assertEqual(self._post(client, host="192.168.1.5:9191").status_code, 200)
        self.assertEqual(self._post(client, host="127.0.0.1:9191").status_code, 200)
        self.assertEqual(self._post(client, host="evil.example:9191").status_code, 421)

    def test_extra_origin_from_environment(self):
        os.environ["BFACW_ALLOWED_ORIGINS"] = "http://192.168.1.5:8080"
        client = self._client()
        resp = self._post(client, origin="http://192.168.1.5:8080")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers.get("access-control-allow-origin"), "http://192.168.1.5:8080")


class TestCoworkerClients(unittest.TestCase):
    """The add-on's own HTTP clients (Local / Remote engines) send an allowed Host."""

    def test_addon_urls_use_loopback(self):
        src = (_REPO / "addon" / "bfa_coworker" / "agent_controller.py").read_text(encoding="utf-8")
        self.assertEqual(src.count('url = "http://127.0.0.1:{:d}/".format(port)'), 2)  # tools/list, tools/call
        self.assertNotIn('"http://localhost:{:d}/"', src)


if __name__ == "__main__":
    unittest.main()
