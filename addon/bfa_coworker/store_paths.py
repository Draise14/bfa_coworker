# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Real paths for a Microsoft Store (MSIX) Bforartists / Blender (#390).

A Store copy runs inside a package, and Windows redirects the **new** files and
folders it writes under AppData into the package's own folder::

    %LOCALAPPDATA%\\bfa_coworker\\...      ->  %LOCALAPPDATA%\\Packages\\<family>\\LocalCache\\Local\\bfa_coworker\\...
    %APPDATA%\\Bforartists\\...            ->  %LOCALAPPDATA%\\Packages\\<family>\\LocalCache\\Roaming\\Bforartists\\...

Bforartists itself still sees them at the usual path, but programs outside the
package (the External Agent, the MCP server it launches, an MCP client) look at
the usual path and find nothing: "cwd does not exist on the machine running the
agent".  :func:`real_path` turns such a path into the one on disk, so every path
handed to another program works.  Outside a Store copy it changes nothing.

Deliberately free of ``bpy``; never raises.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

__all__ = (
    "is_redirected",
    "package_family_name",
    "real_path",
    "redirected_copy",
)

_APPMODEL_ERROR_NO_PACKAGE = 15700
_ERROR_INSUFFICIENT_BUFFER = 122
_FAMILY: list[str] = []         # cached: [] not looked up yet, [""] not packaged


def package_family_name() -> str:
    """The package family of this process (e.g. ``Trinumedia.Bforartists5_abv20tse8q4c6``),
    or "" when it isn't a Store copy (or not Windows)."""
    if _FAMILY:
        return _FAMILY[0]
    name = ""
    if sys.platform == "win32":
        try:
            import ctypes  # pylint: disable=import-outside-toplevel
            from ctypes import wintypes  # pylint: disable=import-outside-toplevel
            get_family = ctypes.windll.kernel32.GetCurrentPackageFamilyName
            get_family.argtypes = [ctypes.POINTER(wintypes.UINT), wintypes.LPWSTR]
            get_family.restype = wintypes.LONG
            length = wintypes.UINT(0)
            if get_family(ctypes.byref(length), None) == _ERROR_INSUFFICIENT_BUFFER:
                buf = ctypes.create_unicode_buffer(length.value)
                if get_family(ctypes.byref(length), buf) == 0:
                    name = buf.value
        except (AttributeError, OSError, ValueError):     # pre-Windows 8, no kernel32 export
            name = ""
    _FAMILY.append(name)
    return name


def _redirected(path: Path, family: str, env: dict[str, str]) -> Path | None:
    """Where the package keeps *path*, if it is under the redirected AppData folders."""
    local = env.get("LOCALAPPDATA", "")
    if not local:
        return None
    cache = Path(local) / "Packages" / family / "LocalCache"
    for base, part in ((env.get("APPDATA", ""), "Roaming"), (local, "Local")):
        if not base:
            continue
        try:
            rel = os.path.relpath(path, base)
        except ValueError:          # another drive
            continue
        if rel == os.curdir or rel.startswith(os.pardir) or os.path.isabs(rel):
            continue
        if part == "Local" and rel.split(os.sep, 1)[0].lower() == "packages":
            return None             # already a package folder
        return cache / part / rel
    return None


def is_redirected(path: str | os.PathLike[str], family: str | None = None,
                  env: dict[str, str] | None = None) -> bool:
    """Would a new file at *path* land in the package's folder instead (a Store copy, under AppData)?"""
    family = package_family_name() if family is None else family
    text = os.fspath(path)
    if not family or not os.path.isabs(text):
        return False
    return _redirected(Path(text), family, dict(os.environ) if env is None else env) is not None


def real_path(path: str | os.PathLike[str], family: str | None = None,
              env: dict[str, str] | None = None) -> str:
    """*path* as programs outside Bforartists see it: the package's copy when a Store
    copy wrote it there, else *path* unchanged.  *family* / *env* are for tests."""
    text = os.fspath(path)
    family = package_family_name() if family is None else family
    if not family or not text or not os.path.isabs(text):
        return text
    moved = _redirected(Path(text), family, dict(os.environ) if env is None else env)
    if moved is not None and moved.exists():
        return str(moved)
    return text


def redirected_copy(path: str | os.PathLike[str], family: str | None = None,
                    env: dict[str, str] | None = None) -> str:
    """Where a Store copy would keep its own copy of *path* ("" when nothing would be
    redirected), whether or not that copy exists yet."""
    text = os.fspath(path)
    family = package_family_name() if family is None else family
    if not family or not text or not os.path.isabs(text):
        return ""
    moved = _redirected(Path(text), family, dict(os.environ) if env is None else env)
    return str(moved) if moved is not None else ""
