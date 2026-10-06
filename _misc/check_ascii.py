# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Verify that source files contain only ASCII characters.
"""

__all__ = (
    "main",
)

import os
import sys

# Directories to scan.
_SCAN_DIRS = (
    os.path.join("mcp"),
    os.path.join("addon"),
    os.path.join("chat_client"),
)

# Directories to skip (relative to the repository root).
_SKIP_DIRS = (
    os.path.join("mcp", "blmcp", "data", "api", "examples"),
    # Vendored third-party dependencies (rich, docutils, idna, ...) are
    # downloaded at build time and are not this project's source.  They
    # legitimately contain Unicode tables and must not be ASCII-linted.
    os.path.join("addon", "bfa_coworker", "vendor"),
)

# Directory names skipped at any depth: local virtualenvs (``mcp/.venv``, a
# vendored ``.venv``) hold third-party packages, never this project's source.
_SKIP_DIR_NAMES = (
    ".venv",
    "__pycache__",
)

# File extensions to check.
_EXTENSIONS = (
    ".py",
    ".toml",
)


def main() -> int:
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    fail = 0
    for scan_dir in _SCAN_DIRS:
        scan_dir_abs = os.path.join(repo_root, scan_dir)
        for dirpath, dirnames, filenames in os.walk(scan_dir_abs):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIR_NAMES]
            dirpath_rel = os.path.relpath(dirpath, repo_root)
            if any(dirpath_rel == d or dirpath_rel.startswith(d + os.sep) for d in _SKIP_DIRS):
                continue
            for filename in filenames:
                if not any(filename.endswith(ext) for ext in _EXTENSIONS):
                    continue
                filepath = os.path.join(dirpath, filename)
                filepath_rel = os.path.relpath(filepath, repo_root)
                with open(filepath, "rb") as fh:
                    for line_number, line in enumerate(fh, 1):
                        try:
                            line.decode("ascii")
                        except UnicodeDecodeError:
                            # Escaped, so a non-UTF-8 console (Windows cp1252)
                            # cannot crash the report on the very glyph it flags.
                            text = line.decode("utf-8", errors="replace").rstrip()
                            print("{:s}:{:d}:{:s}".format(
                                filepath_rel, line_number,
                                text.encode("ascii", errors="backslashreplace").decode("ascii"),
                            ))
                            fail = 1

    if fail:
        print("ERROR: non-ASCII characters found")
    return fail


if __name__ == "__main__":
    sys.exit(main())
