# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Simple synchronous MCP client for testing.

Speaks newline-delimited JSON-RPC 2.0 over stdio to a subprocess.
"""

__all__ = (
    "MCPClient",
)

import json
import os
import select
import subprocess
import time
from typing import Any, Self

# Scale all timeouts (e.g. `GLOBAL_TIMEOUT_SCALE=2` doubles every limit).
_TIMEOUT_SCALE = float(os.environ.get("GLOBAL_TIMEOUT_SCALE", "1"))

# Per-request timeout in seconds.
_REQUEST_TIMEOUT = int(30 * _TIMEOUT_SCALE)

# Maximum time to wait for a local process to respond or exit (seconds).
_TIMEOUT_LOCAL_PROC = int(5 * _TIMEOUT_SCALE)


class MCPClient:
    """
    Synchronous MCP client that communicates via stdin/stdout with a subprocess.
    """

    def __init__(
        self,
        command: list[str],
        env: dict[str, str] | None = None,
    ) -> None:
        self._proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            env=env,
        )
        self._next_id = 1

    def _send_request(self, method: str, params: dict[str, object] | None = None) -> dict[str, Any]:
        """
        Send a JSON-RPC request and wait for the matching response.
        """
        request_id = self._next_id
        self._next_id += 1

        msg: dict[str, object] = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
        }
        if params is not None:
            msg["params"] = params

        assert self._proc.stdin is not None
        assert self._proc.stdout is not None

        data = json.dumps(msg) + "\n"
        self._proc.stdin.write(data.encode("utf-8"))
        self._proc.stdin.flush()

        # Read lines until a response with matching id arrives.
        deadline = time.monotonic() + _REQUEST_TIMEOUT
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError(
                    "Timeout ({:d}s) waiting for response to {:s}".format(_REQUEST_TIMEOUT, method)
                )
            ready, _, _ = select.select([self._proc.stdout], [], [], remaining)
            if not ready:
                raise RuntimeError(
                    "Timeout ({:d}s) waiting for response to {:s}".format(_REQUEST_TIMEOUT, method)
                )
            line = self._proc.stdout.readline()
            if not line:
                raise RuntimeError("MCP server closed stdout unexpectedly")
            line = line.strip()
            if not line:
                continue
            try:
                response = json.loads(line)
            except json.JSONDecodeError:
                continue

            # Skip notifications and responses for other requests.
            if response.get("id") != request_id:
                continue

            if "error" in response:
                raise RuntimeError("JSON-RPC error: {!r}".format(response["error"]))

            return response.get("result", {})

    def _send_notification(self, method: str, params: dict[str, object] | None = None) -> None:
        """
        Send a JSON-RPC notification (no id, no response expected).
        """
        msg: dict[str, object] = {
            "jsonrpc": "2.0",
            "method": method,
        }
        if params is not None:
            msg["params"] = params

        assert self._proc.stdin is not None

        data = json.dumps(msg) + "\n"
        self._proc.stdin.write(data.encode("utf-8"))
        self._proc.stdin.flush()

    def initialize(self) -> dict[str, Any]:
        """
        Perform the MCP initialize handshake.
        """
        result = self._send_request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "test-client", "version": "0.1.0"},
        })
        self._send_notification("notifications/initialized")
        return result

    def list_tools(self) -> list[str]:
        """
        Return the names of all tools the server exposes.
        """
        result = self._send_request("tools/list")
        return [t["name"] for t in result.get("tools", [])]

    def call_tool(
        self,
        name: str,
        arguments: dict[str, object] | None = None,
    ) -> dict[str, Any]:
        """
        Call an MCP tool by name and return the result.
        """
        params: dict[str, object] = {"name": name}
        if arguments is not None:
            params["arguments"] = arguments
        return self._send_request("tools/call", params)

    def close(self) -> None:
        """
        Close the connection and terminate the subprocess.
        """
        if self._proc.stdin is not None:
            self._proc.stdin.close()
        if self._proc.stdout is not None:
            self._proc.stdout.close()
        self._proc.terminate()
        try:
            self._proc.wait(timeout=_TIMEOUT_LOCAL_PROC)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            self._proc.wait(timeout=_TIMEOUT_LOCAL_PROC)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
