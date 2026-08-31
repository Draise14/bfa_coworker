#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tool Smoke Test — calls every MCP tool with minimal arguments.

Run against a running Blender/Bforartists + MCP server (default http://127.0.0.1:9191).

Setup (Bforartists / Blender):
-------------------------------

  1. Install the Coworker addon in Bforartists/Blender
     (Edit → Preferences → Add-ons → Install → select the .zip)

  2. Open the addon preferences:
     Edit → Preferences → Add-ons → Coworker → Advanced tab

  3. Start the MCP server using one of these methods:

     Option A — Self-Contained mode (simplest):
       - Set Operating Mode to "Local LLM" or "Remote API"
       - Click "Start Agent" in the Chat panel (3D Viewport sidebar N)
       - The MCP server starts automatically on port 9191

     Option B — External Harness mode (standalone MCP):
       - Set Operating Mode to "External Harness"
       - In the "MCP Server" section, set mode to "NETWORK"
       - Click "Start MCP Server"
       - Server runs on port 9191 (or your configured port)

  4. Verify the server is running:
     Open http://127.0.0.1:9191/ in a browser — you should see
     "MCP Server is running" or similar.

  5. Run this smoke test (from the repo root):

     python tests/tool_smoke_test.py

Usage::

    # Default (port 9191)
    python tests/tool_smoke_test.py

    # Custom port
    python tests/tool_smoke_test.py --port 9192

    # Verbose (show full responses)
    python tests/tool_smoke_test.py --verbose

    # Test specific tools only
    python tests/tool_smoke_test.py --filter get_objects_summary,execute_blender_code

    # List available tools without testing
    python tests/tool_smoke_test.py --list

Exit code: 0 if all tests pass, 1 if any fail.
"""

__all__ = ()

import argparse
import json
import sys
import urllib.error
import urllib.request
from typing import Any


# ---------------------------------------------------------------------------
# Default test arguments for each tool.
# Keys are tool names, values are dicts of arguments to send.
# Tools not listed here are called with {} (no arguments).
# If a tool needs specific args to succeed, add them here.

_TOOL_TEST_ARGS: dict[str, dict[str, Any]] = {
    # ── Code Execution ──────────────────────────────────────────────
    "execute_blender_code": {
        "code": "result = {'status': 'ok', 'message': 'hello from smoke test'}"
    },
    "execute_blender_code_for_cli": {
        "blend_file": "",
        "code": "result = {'status': 'ok'}"
    },

    # ── Scene Inspection ────────────────────────────────────────────
    "get_objects_summary": {},
    "get_object_detail_summary": {"name": "Cube"},

    # ── Blend-File Summary ──────────────────────────────────────────
    "get_blendfile_summary_datablocks": {},
    "get_blendfile_summary_datablocks_for_cli": {"blend_file": ""},
    "get_blendfile_summary_missing_files": {},
    "get_blendfile_summary_missing_files_for_cli": {"blend_file": ""},
    "get_blendfile_summary_of_linked_libraries": {},
    "get_blendfile_summary_of_linked_libraries_for_cli": {"blend_file": ""},
    "get_blendfile_summary_path_info": {},
    "get_blendfile_summary_path_info_for_cli": {"blend_file": ""},
    "get_blendfile_summary_usage_guess": {},
    "get_blendfile_summary_usage_guess_for_cli": {"blend_file": ""},

    # ── Operation History ───────────────────────────────────────────
    "get_operation_history": {"count": 3},

    # ── Navigation ──────────────────────────────────────────────────
    "jump_to_tab_by_name": {"name": "Layout"},
    "jump_to_tab_by_space_type": {"space_type": "VIEW_3D"},
    "jump_to_view3d_object_by_name": {"name": "Cube"},
    "jump_to_view3d_object_data_by_name": {"name": "Cube"},

    # ── Screenshots ─────────────────────────────────────────────────
    "get_screenshot_of_area_as_image": {"area_ui_type": "VIEW_3D"},
    "get_screenshot_of_window_as_image": {},
    "get_screenshot_of_window_as_json": {},

    # ── Rendering ───────────────────────────────────────────────────
    "render_thumbnail_to_path": {"output_path": ""},
    "render_viewport_to_path": {"output_path": ""},

    # ── Documentation Search ────────────────────────────────────────
    "search_api_docs": {"query": "bpy.ops.mesh"},
    "search_manual_docs": {"query": "modifier"},
    "get_python_api_docs": {"identifier": "bpy.types.Scene"},

    # ── Poly Haven ──────────────────────────────────────────────────
    "get_polyhaven_status": {},
    "search_polyhaven_assets": {"category": "hdris"},
    "download_polyhaven_asset": {"asset_id": "", "asset_type": "hdris"},

    # ── Assets ──────────────────────────────────────────────────────
    "get_asset_libraries": {},
    "jump_to_asset_browser": {"allow_edits": False},
}


# ---------------------------------------------------------------------------
# Tools that are expected to fail with default args (documented reasons).

_TOOL_EXPECTED_FAILURES: dict[str, str] = {
    "execute_blender_code_for_cli": "requires a valid blend file path",
    "get_blendfile_summary_datablocks_for_cli": "requires a valid blend file path",
    "get_blendfile_summary_missing_files_for_cli": "requires a valid blend file path",
    "get_blendfile_summary_of_linked_libraries_for_cli": "requires a valid blend file path",
    "get_blendfile_summary_path_info_for_cli": "requires a valid blend file path",
    "get_blendfile_summary_usage_guess_for_cli": "requires a valid blend file path",
    "jump_to_tab_by_name": "may fail if 'Layout' tab doesn't exist",
    "jump_to_view3d_object_by_name": "may fail if 'Cube' doesn't exist",
    "jump_to_view3d_object_data_by_name": "may fail if 'Cube' data-block doesn't exist",
    "get_object_detail_summary": "may fail if 'Cube' doesn't exist",
    "render_thumbnail_to_path": "requires a valid output path",
    "render_viewport_to_path": "requires a valid output path",
    "download_polyhaven_asset": "requires a valid asset_id",
    "jump_to_asset_browser": "may fail if no Asset Browser is open (allow_edits=False)",
}


# ---------------------------------------------------------------------------
# Helpers

def _mcp_request(
    url: str,
    method: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Send a JSON-RPC request to the MCP server."""
    payload = {
        "jsonrpc": "2.0",
        "id": "smoke_test",
        "method": method,
    }
    if params is not None:
        payload["params"] = params
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read().decode()
    # Handle SSE-wrapped responses (FastMCP stateless_http mode).
    for line in raw.splitlines():
        if line.startswith("data: "):
            return json.loads(line[6:])
    return json.loads(raw)


def _list_tools(url: str) -> list[dict[str, Any]]:
    """Fetch the list of tools from the MCP server."""
    result = _mcp_request(url, "tools/list")
    return result.get("result", {}).get("tools", [])


def _call_tool(url: str, name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Call a tool and return the full JSON-RPC response."""
    return _mcp_request(url, "tools/call", {"name": name, "arguments": args})


# ---------------------------------------------------------------------------
# Main

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Smoke test all MCP tools against a running Blender + MCP server."
    )
    parser.add_argument(
        "--port", type=int, default=9191,
        help="MCP server HTTP port (default: 9191)",
    )
    parser.add_argument(
        "--host", type=str, default="127.0.0.1",
        help="MCP server host (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Show full response for each tool",
    )
    parser.add_argument(
        "--filter", type=str, default="",
        help="Comma-separated list of tool names to test (default: all)",
    )
    parser.add_argument(
        "--list", action="store_true",
        help="List available tools and exit",
    )
    args = parser.parse_args()

    base_url = "http://{:s}:{:d}/".format(args.host, args.port)

    # ── Fetch tools from server ────────────────────────────────────
    print("=" * 60)
    print("BFA Coworker — Tool Smoke Test")
    print("Server: {:s}".format(base_url))
    print("=" * 60)

    try:
        tools = _list_tools(base_url)
    except Exception as ex:
        print("\nERROR: Cannot connect to MCP server at {:s}".format(base_url))
        print("  {:s}".format(str(ex)))
        print("\nMake sure Blender is running with the Coworker addon enabled")
        print("and the MCP server is started (port {:d}).".format(args.port))
        return 1

    if not tools:
        print("\nERROR: Server returned empty tool list.")
        return 1

    print("\n{:d} tools available.\n".format(len(tools)))

    # ── List mode ──────────────────────────────────────────────────
    if args.list:
        print("Available tools:")
        for t in tools:
            name = t.get("name", "?")
            desc = t.get("description", "").strip().split("\n")[0][:100]
            print("  {:40s} {:s}".format(name, desc))
        return 0

    # ── Filter ─────────────────────────────────────────────────────
    filter_names: list[str] = []
    if args.filter:
        filter_names = [n.strip() for n in args.filter.split(",")]

    # ── Run tests ──────────────────────────────────────────────────
    passed = 0
    failed = 0
    skipped = 0
    expected_failures = 0
    results: list[dict[str, Any]] = []

    for tool in tools:
        name = tool.get("name", "?")

        # Apply filter.
        if filter_names and name not in filter_names:
            skipped += 1
            continue

        # Determine test arguments.
        test_args = _TOOL_TEST_ARGS.get(name, {})

        # Determine if this is an expected failure.
        expected_fail_reason = _TOOL_EXPECTED_FAILURES.get(name)

        # Call the tool.
        try:
            response = _call_tool(base_url, name, test_args)
        except Exception as ex:
            response = {"error": str(ex)}

        # Evaluate result.
        error = response.get("error")
        result_data = response.get("result", {})
        result_error = result_data.get("isError", False) if isinstance(result_data, dict) else False
        content = result_data.get("content", []) if isinstance(result_data, dict) else []

        is_error = bool(error) or result_error

        if is_error and expected_fail_reason:
            status = "EXPECTED FAIL"
            expected_failures += 1
        elif is_error:
            status = "FAIL"
            failed += 1
        else:
            status = "PASS"
            passed += 1

        # Build result entry.
        entry = {
            "name": name,
            "status": status,
            "error": error or (str(content[:200]) if result_error else ""),
            "expected": expected_fail_reason or "",
        }
        results.append(entry)

        # Print result line.
        icon = {
            "PASS": "\u2713",
            "FAIL": "X",
            "EXPECTED FAIL": "~",
        }.get(status, "?")
        print("  [{:s}] {:40s} {:s}".format(icon, name, status))

        if args.verbose and (is_error or status == "EXPECTED FAIL"):
            if entry["error"]:
                print("         Error: {:s}".format(entry["error"][:200]))
            if entry["expected"]:
                print("         Note: {:s}".format(entry["expected"]))

    # ── Summary ────────────────────────────────────────────────────
    total_tested = passed + failed + expected_failures
    print("\n" + "=" * 60)
    print("Results: {:d} passed, {:d} failed, {:d} expected failures, {:d} skipped".format(
        passed, failed, expected_failures, skipped,
    ))
    print("         {:d}/{:d} tools tested".format(total_tested, len(tools)))

    if failed > 0:
        print("\nFailed tools:")
        for r in results:
            if r["status"] == "FAIL":
                print("  - {:s}: {:s}".format(r["name"], r["error"][:150]))
        return 1

    if passed == total_tested and total_tested > 0:
        print("\nAll tested tools passed!")
    elif expected_failures > 0 and failed == 0:
        print("\nAll tested tools passed ({:d} expected failures).".format(expected_failures))

    return 0


if __name__ == "__main__":
    sys.exit(main())
