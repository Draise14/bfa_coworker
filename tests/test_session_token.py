# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for the session token (Tier 4i "Engine 8").

1. ``session_identity``: the token is created once in a user-private file and
   reused; a token in the environment wins; ``check_token`` accepts only the
   right token (or anything when the preference is off).
2. The MCP server's bridge client sends the token from ``BFACW_TOKEN``.
3. The HTTP gate refuses requests without ``Authorization: Bearer <token>``
   and lets CORS preflight, ``/health`` and lifespan through.
4. The add-on hands the token to the MCP server environment, the External
   Harness launcher and its own HTTP requests.

Run with::

    python -m unittest tests.test_session_token -v
"""

__all__ = ()

import asyncio
import importlib.util
import json
import os
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _fresh_identity():
    return _load("session_identity_under_test", _REPO / "addon" / "bfa_coworker" / "session_identity.py")


class TestToken(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        env = {"LOCALAPPDATA": self._tmp.name, "HOME": self._tmp.name, "USERPROFILE": self._tmp.name}
        patcher = unittest.mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("BFACW_TOKEN", None)

    def test_created_once_and_reused(self):
        si = _fresh_identity()
        token = si.session_token()
        self.assertGreaterEqual(len(token), 20)
        path = si._token_path()
        self.assertEqual(path.read_text(encoding="utf-8"), token)
        # Never the shared temp folder: LOCALAPPDATA on Windows, ~/.cache elsewhere.
        expected_root = Path(self._tmp.name) / ("" if os.name == "nt" else ".cache")
        self.assertEqual(path.parent.parent, expected_root)
        self.assertEqual(_fresh_identity().session_token(), token)  # a new process reads the file

    def test_environment_token_wins(self):
        os.environ["BFACW_TOKEN"] = "given-by-automation-0123456789"
        si = _fresh_identity()
        self.assertEqual(si.session_token(), "given-by-automation-0123456789")
        self.assertFalse(si._token_path().exists())

    def test_check_token(self):
        si = _fresh_identity()
        token = si.session_token()
        self.assertEqual(si.check_token({"token": token}), "")
        self.assertIn("session token", si.check_token({}))
        self.assertIn("session token", si.check_token({"token": "wrong"}))
        self.assertIn("session token", si.check_token({"token": 123}))
        si.set_token_required(False)
        self.assertEqual(si.check_token({}), "")
        self.assertFalse(si.token_required())


class TestBridgeClientSendsToken(unittest.TestCase):

    def test_payload_carries_token(self):
        sys.path.insert(0, str(_REPO / "mcp"))
        self.addCleanup(sys.path.remove, str(_REPO / "mcp"))
        from blmcp.tools_helpers import connection
        sent = []

        class _Sock:
            def __init__(self, *_a): pass
            def __enter__(self): return self
            def __exit__(self, *_a): return False
            def settimeout(self, _t): pass
            def connect(self, _addr): pass
            def sendall(self, data): sent.append(data)
            def recv(self, _n): return json.dumps({"status": "ok", "result": {}}).encode() + b"\0"

        with unittest.mock.patch.dict(os.environ, {"BFACW_TOKEN": "tok-abcdefghijklmnopqrstu"}), \
                unittest.mock.patch.object(connection.socket, "socket", _Sock):
            connection.send_code("x = 1", strict_json=False)
        payload = json.loads(sent[0].rstrip(b"\0"))
        self.assertEqual(payload["token"], "tok-abcdefghijklmnopqrstu")


class TestHttpGate(unittest.TestCase):

    def _gate(self):
        mod = _load("http_auth_under_test", _REPO / "mcp" / "blmcp" / "tools_helpers" / "http_auth.py")
        calls = []

        async def app(scope, receive, send):
            calls.append(scope.get("path"))
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

        return mod.TokenGateMiddleware(app, "secret-token"), calls

    def _run(self, gate, scope):
        sent = []

        async def send(msg):
            sent.append(msg)

        asyncio.run(gate(scope, None, send))
        return sent[0].get("status") if sent else None

    def test_requires_bearer_token(self):
        gate, calls = self._gate()
        base = {"type": "http", "method": "POST", "path": "/"}
        self.assertEqual(self._run(gate, dict(base, headers=[])), 401)
        self.assertEqual(self._run(gate, dict(base, headers=[(b"authorization", b"Bearer nope")])), 401)
        self.assertEqual(self._run(gate, dict(base, headers=[(b"Authorization", b"Bearer secret-token")])), 200)
        self.assertEqual(calls, ["/"])

    def test_preflight_health_and_lifespan_pass(self):
        gate, calls = self._gate()
        self.assertEqual(self._run(gate, {"type": "http", "method": "OPTIONS", "path": "/", "headers": []}), 200)
        self.assertEqual(self._run(gate, {"type": "http", "method": "GET", "path": "/health", "headers": []}), 200)
        self._run(gate, {"type": "lifespan"})
        self.assertEqual(calls, ["/", "/health", None])


class TestAddonWiring(unittest.TestCase):
    """The add-on passes the token on (source checks; agent_controller needs bpy)."""

    @classmethod
    def setUpClass(cls):
        cls.src = (_REPO / "addon" / "bfa_coworker" / "agent_controller.py").read_text(encoding="utf-8")

    def test_mcp_env_launcher_and_http_headers(self):
        self.assertIn("env[_session_identity.TOKEN_ENV_VAR] = _session_identity.session_token()", self.src)
        self.assertIn('info["token"] = _session_identity.session_token()', self.src)
        self.assertIn('os.environ["BFACW_TOKEN"] = _info["token"]', self.src)  # launcher source
        self.assertEqual(self.src.count("headers=_mcp_headers(),"), 2)

    def test_bridge_checks_token(self):
        bridge = (_REPO / "addon" / "bfa_coworker" / "mcp_to_blender_server.py").read_text(encoding="utf-8")
        self.assertIn("session_identity.check_token(request)", bridge)


if __name__ == "__main__":
    unittest.main()
