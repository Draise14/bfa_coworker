# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
External Harness clients: config formats, file locations and writers.

Everything here is checked against each client's own documentation
(issue #100).  The server block itself (``command`` / ``args`` / ``env``)
comes from ``agent_controller.build_mcp_server_entry``; this module only
wraps it in each client's schema, finds the client's config file, merges
the entry into it, and reads an installed entry back for checking.

Stdlib only, no ``bpy`` -- importable from tests outside Blender.
"""

__all__ = (
    "SERVER_NAME",
    "format_config",
    "config_paths",
    "can_install",
    "install",
    "read_installed_entry",
)

import glob
import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any

SERVER_NAME = "bfa-coworker"

# Clients whose config file location is documented, so "Add to <client>"
# can write it.  Cline (IDE extension), VS Code (per-profile), Freebuff and
# Generic have no documented fixed path: copy/paste only.
_INSTALLABLE = ("claude_desktop", "claude_code", "codex", "cursor", "windsurf", "opencode")


# -----------------------------------------------------------------------------
# Formats


def _toml_str(value: str) -> str:
    # JSON string escapes (\\, \", \uXXXX) are valid TOML basic-string escapes.
    return json.dumps(value)


def _codex_toml(entry: dict[str, Any]) -> str:
    lines = [
        "[mcp_servers.{:s}]".format(SERVER_NAME),
        "command = {:s}".format(_toml_str(entry["command"])),
        "args = [{:s}]".format(", ".join(_toml_str(a) for a in entry.get("args", []))),
    ]
    env = entry.get("env") or {}
    if env:
        lines.append("")
        lines.append("[mcp_servers.{:s}.env]".format(SERVER_NAME))
        for key, val in env.items():
            lines.append("{:s} = {:s}".format(key, _toml_str(str(val))))
    return "\n".join(lines) + "\n"


def _opencode_entry(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "local",
        "command": [entry["command"], *entry.get("args", [])],
        "environment": dict(entry.get("env") or {}),
        "enabled": True,
    }


def _client_entry(client_type: str, entry: dict[str, Any]) -> dict[str, Any]:
    """The server entry as it appears inside the client's servers object."""
    if client_type == "opencode":
        return _opencode_entry(entry)
    if client_type in ("claude_code", "cursor", "vscode"):
        # Claude Code and VS Code document `type`; Cursor lists it as required.
        return {"type": "stdio", **entry}
    return dict(entry)


# Top-level key holding the servers, per client.
_TOP_KEY = {
    "vscode": "servers",
    "opencode": "mcp",
}


def format_config(client_type: str, entry: dict[str, Any]) -> str:
    """Return the text to copy for *client_type*: JSON, or TOML for Codex."""
    if client_type == "codex":
        return _codex_toml(entry)
    config: dict[str, Any] = {}
    if client_type == "opencode":
        config["$schema"] = "https://opencode.ai/config.json"
    config[_TOP_KEY.get(client_type, "mcpServers")] = {
        SERVER_NAME: _client_entry(client_type, entry),
    }
    return json.dumps(config, indent=2)


# -----------------------------------------------------------------------------
# Locations


def _home() -> Path:
    return Path.home()


def _xdg_config() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or (_home() / ".config"))


def config_paths(client_type: str) -> list[Path]:
    """Documented config files for *client_type*, existing ones first.

    Empty when the client has no documented fixed location.
    """
    paths: list[Path] = []
    appdata = os.environ.get("APPDATA", "")
    if client_type == "claude_desktop":
        if sys.platform == "win32":
            if appdata:
                paths.append(Path(appdata) / "Claude" / "claude_desktop_config.json")
            # Some Windows installs keep it per account under Claude-Data
            # (seen in the field, not in the docs) -- only used when present.
            local = os.environ.get("LOCALAPPDATA", "")
            if local:
                pattern = os.path.join(local, "Claude-Data", "claude_*", "claude_desktop_config.json")
                paths.extend(Path(p) for p in sorted(glob.glob(pattern)))
        elif sys.platform == "darwin":
            paths.append(_home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json")
        else:
            paths.append(_xdg_config() / "Claude" / "claude_desktop_config.json")
    elif client_type == "claude_code":
        config_dir = os.environ.get("CLAUDE_CONFIG_DIR")
        paths.append((Path(config_dir) if config_dir else _home()) / ".claude.json")
    elif client_type == "codex":
        paths.append(_home() / ".codex" / "config.toml")
    elif client_type == "cursor":
        paths.append(_home() / ".cursor" / "mcp.json")
    elif client_type == "windsurf":
        # Windsurf's docs moved to Devin Desktop with a new path; older
        # installs still use ~/.codeium/windsurf/.
        if sys.platform == "win32" and appdata:
            paths.append(Path(appdata) / "devin" / "mcp_config.json")
        else:
            paths.append(_xdg_config() / "devin" / "mcp_config.json")
        paths.append(_home() / ".codeium" / "windsurf" / "mcp_config.json")
    elif client_type == "opencode":
        paths.append(_xdg_config() / "opencode" / "opencode.json")
    return sorted(paths, key=lambda p: not p.is_file())


def can_install(client_type: str) -> bool:
    return client_type in _INSTALLABLE


# -----------------------------------------------------------------------------
# Writers


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        shutil.copy2(str(path), str(path) + ".bfacw.bak")
    tmp = path.with_name(path.name + ".bfacw.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(str(tmp), str(path))


def _read_json_object(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    raw = path.read_text(encoding="utf-8")
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as ex:
        raise ValueError("{:s} is not valid JSON ({:s}) -- not touching it".format(str(path), str(ex))) from ex
    if not isinstance(data, dict):
        raise ValueError("{:s} is not a JSON object -- not touching it".format(str(path)))
    return data


def _merge_json(path: Path, client_type: str, entry: dict[str, Any]) -> None:
    data = _read_json_object(path)
    if client_type == "opencode" and not data:
        data["$schema"] = "https://opencode.ai/config.json"
    top_key = _TOP_KEY.get(client_type, "mcpServers")
    servers = data.get(top_key)
    if not isinstance(servers, dict):
        servers = {}
        data[top_key] = servers
    servers[SERVER_NAME] = _client_entry(client_type, entry)
    _write_atomic(path, json.dumps(data, indent=2))


# `[mcp_servers.bfa-coworker]`, `[mcp_servers."bfa-coworker".env]`, ...
_RE_OUR_TOML_TABLE = re.compile(r'^\s*\[\s*mcp_servers\s*\.\s*"?' + re.escape(SERVER_NAME) + r'"?\s*(\.|\])')
_RE_TOML_TABLE = re.compile(r"^\s*\[")


def _merge_codex_toml(path: Path, entry: dict[str, Any]) -> None:
    import tomllib
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    try:
        tomllib.loads(text)
    except tomllib.TOMLDecodeError as ex:
        raise ValueError("{:s} is not valid TOML ({:s}) -- not touching it".format(str(path), str(ex))) from ex
    # Drop our previous tables; keep everything else byte for byte.
    kept: list[str] = []
    skipping = False
    for line in text.splitlines():
        if _RE_TOML_TABLE.match(line):
            skipping = bool(_RE_OUR_TOML_TABLE.match(line))
        if not skipping:
            kept.append(line)
    new_text = "\n".join(kept).rstrip()
    new_text = (new_text + "\n\n" if new_text else "") + _codex_toml(entry)
    tomllib.loads(new_text)  # Never write a file Codex can't parse.
    _write_atomic(path, new_text)


def install(
    client_type: str,
    entry: dict[str, Any],
    paths: list[Path] | None = None,
) -> list[Path]:
    """Add (or replace) the server in the client's config file(s).

    Every other key is kept, the previous file is copied to
    ``<name>.bfacw.bak`` and the new one is written atomically.  A file that
    can't be parsed is never overwritten (``ValueError``).  Returns the
    paths written.

    For clients with several known locations (Claude Desktop, Windsurf),
    every location that already exists is updated; Claude Desktop falls back
    to its documented default, Windsurf asks for the file to be created from
    the app first (its location depends on the version).
    """
    if not can_install(client_type):
        raise ValueError("No documented config file for this client -- copy the config instead")
    if paths is None:
        candidates = config_paths(client_type)
        existing = [p for p in candidates if p.is_file()]
        if client_type in ("claude_desktop", "windsurf"):
            if existing:
                paths = existing
            elif client_type == "claude_desktop":
                paths = candidates[:1]
            else:
                raise ValueError(
                    "Windsurf's MCP config file was not found. In Cascade, open "
                    "... > MCPs > Open MCP config file once, then try again."
                )
        else:
            if client_type == "opencode":
                jsonc = candidates[0].with_suffix(".jsonc")
                if jsonc.is_file() and not candidates[0].is_file():
                    raise ValueError(
                        "{:s} has comments (JSONC) and can't be edited safely -- copy the "
                        "config into it instead".format(str(jsonc))
                    )
            paths = candidates[:1]
    for path in paths:
        if client_type == "codex":
            _merge_codex_toml(path, entry)
        else:
            _merge_json(path, client_type, entry)
    return list(paths)


# -----------------------------------------------------------------------------
# Reading back


def read_installed_entry(client_type: str) -> tuple[Path | None, dict[str, Any] | None, str]:
    """Find the server entry the client will actually use.

    Returns ``(path, entry, error)`` where *entry* is normalized to
    ``{"command", "args", "env"}``.  *path* is ``None`` when no config file
    exists; *entry* is ``None`` when the file has no ``bfa-coworker`` entry
    (or can't be read -- then *error* says why).
    """
    for path in config_paths(client_type):
        if not path.is_file():
            continue
        try:
            if client_type == "codex":
                import tomllib
                data = tomllib.loads(path.read_text(encoding="utf-8"))
                raw = (data.get("mcp_servers") or {}).get(SERVER_NAME)
            else:
                data = _read_json_object(path)
                raw = (data.get(_TOP_KEY.get(client_type, "mcpServers")) or {}).get(SERVER_NAME)
        except Exception as ex:  # pylint: disable=broad-exception-caught
            return path, None, str(ex)
        if not isinstance(raw, dict):
            continue
        if client_type == "opencode":
            cmd = list(raw.get("command") or [])
            entry = {"command": cmd[0] if cmd else "", "args": cmd[1:], "env": dict(raw.get("environment") or {})}
        else:
            entry = {
                "command": raw.get("command", ""),
                "args": list(raw.get("args") or []),
                "env": dict(raw.get("env") or {}),
            }
        return path, entry, ""
    existing = [p for p in config_paths(client_type) if p.is_file()]
    return (existing[0] if existing else None), None, ""
