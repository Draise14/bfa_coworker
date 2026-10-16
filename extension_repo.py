# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

r"""
Maintain the Coworker online extension repository (issue #86).

Coworker updates through Blender's own extension system: the repository is a
static ``index.json`` (Blender's "v1" listing format) kept in this git repo at
``extensions/index.json`` and served from::

    https://raw.githubusercontent.com/Draise14/bfa_coworker/main/extensions/index.json

Each entry points at a zip attached to a GitHub release, so the git repo never
stores the (~25 MB) packages.  Blender checks the index on startup, shows the
new version in Preferences > Get Extensions, and upgrades in place.

Usage::

    # After building bfa_coworker-1.1.38.zip, add it to the listing
    # (the URL is where the zip will be downloadable once the release is published):
    python extension_repo.py add releases/bfa_coworker-1.1.38.zip --tag v1.1.38

    # Show what the listing currently offers:
    python extension_repo.py show

Pure Python (no Blender needed).  The format mirrors what Blender's own
``blender --command extension server-generate`` writes, with absolute
``archive_url`` values (supported by Blender's installer).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.parse
import zipfile
from typing import Any

try:
    import tomllib
except ImportError:  # Python < 3.11
    tomllib = None  # type: ignore[assignment]

__all__ = (
    "REPO_INDEX_URL",
    "RELEASE_ASSET_URL",
    "manifest_from_zip",
    "index_entry",
    "load_index",
    "upsert",
    "save_index",
    "drag_install_url",
    "build_installer",
    "LATEST_INSTALLER_URL",
    "GITHUB_REPO",
    "SCRIPT_DIR",
    "DEFAULT_INDEX",
    "INSTALLER_SRC",
    "INSTALLER_ID",
    "main",
)

GITHUB_REPO = "Draise14/bfa_coworker"
REPO_INDEX_URL = "https://raw.githubusercontent.com/{:s}/main/extensions/index.json".format(GITHUB_REPO)
# Always the newest release's installer (its file name carries no version).
LATEST_INSTALLER_URL = "https://github.com/{:s}/releases/latest/download/bfa_coworker_installer.zip".format(
    GITHUB_REPO)
RELEASE_ASSET_URL = "https://github.com/{:s}/releases/download/{{tag}}/{{filename}}".format(GITHUB_REPO)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_INDEX = os.path.join(SCRIPT_DIR, "extensions", "index.json")

# Manifest keys copied into the listing (same set Blender's server-generate keeps).
_MANIFEST_KEYS = (
    "id", "schema_version", "name", "version", "tagline", "maintainer", "type",
    "permissions", "tags", "blender_version_min", "blender_version_max",
    "license", "copyright", "website", "platforms", "python_versions",
)


def _parse_toml(text: str) -> dict[str, Any]:
    if tomllib is not None:
        return tomllib.loads(text)
    raise RuntimeError("Python 3.11+ is required (tomllib) to read blender_manifest.toml")


def manifest_from_zip(zip_path: str) -> dict[str, Any]:
    """Read ``blender_manifest.toml`` from an extension zip (flat or one folder deep)."""
    with zipfile.ZipFile(zip_path) as zf:
        names = [n for n in zf.namelist()
                 if n == "blender_manifest.toml" or (n.count("/") == 1 and n.endswith("/blender_manifest.toml"))]
        if not names:
            raise ValueError("{:s}: no blender_manifest.toml at the top level".format(zip_path))
        return _parse_toml(zf.read(sorted(names, key=len)[0]).decode("utf-8"))


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def index_entry(zip_path: str, archive_url: str) -> dict[str, Any]:
    """One listing entry for *zip_path*, downloadable from *archive_url*."""
    manifest = manifest_from_zip(zip_path)
    entry: dict[str, Any] = {k: manifest[k] for k in _MANIFEST_KEYS if k in manifest}
    # Blender stores permissions as a table; the listing keeps it as-is.
    entry["archive_url"] = archive_url
    entry["archive_size"] = os.path.getsize(zip_path)
    entry["archive_hash"] = "sha256:" + _sha256(zip_path)
    return entry


def load_index(path: str = DEFAULT_INDEX) -> dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        data = {}
    data.setdefault("version", "v1")
    data.setdefault("blocklist", [])
    data.setdefault("data", [])
    return data


def upsert(index: dict[str, Any], entry: dict[str, Any]) -> dict[str, Any]:
    """Replace the listing's entry for ``entry['id']`` (one version per id).

    Blender offers the newest listed version; listing older ones only adds
    noise.  Rollback is the installer's bundled copy or a release zip.
    """
    index["data"] = [e for e in index.get("data", []) if e.get("id") != entry["id"]] + [entry]
    index["data"].sort(key=lambda e: e.get("id", ""))
    return index


def save_index(index: dict[str, Any], path: str = DEFAULT_INDEX) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(index, fh, indent=2, ensure_ascii=True)
        fh.write("\n")


def drag_install_url(entry: dict[str, Any], repo_url: str = REPO_INDEX_URL) -> str:
    """A link that installs the extension AND adds the repository when dragged into Blender.

    Blender reads the ``repository`` query value from a dropped link; when that
    repository isn't known it offers "Add Repository..." first, then installs
    -- so updates work from then on with no manual setup.
    """
    query = {"repository": repo_url}
    if entry.get("blender_version_min"):
        query["blender_version_min"] = entry["blender_version_min"]
    if entry.get("platforms"):
        query["platforms"] = ",".join(entry["platforms"])
    return entry["archive_url"] + "?" + urllib.parse.urlencode(query)


INSTALLER_SRC = os.path.join(SCRIPT_DIR, "installer", "bfa_coworker_installer")
INSTALLER_ID = "bfa_coworker_installer"


def build_installer(coworker_zip: str, output_dir: str) -> str:
    """Build the Coworker Installer zip with *coworker_zip* bundled inside.

    Layout (one top-level folder, like the Coworker zip)::

        bfa_coworker_installer/
            blender_manifest.toml     (version = the bundled Coworker's)
            __init__.py
            payload/bfa_coworker.zip  (the version lives in its manifest)

    The installer zip's file NAME carries no version either
    (``bfa_coworker_installer.zip``), so the newest one is always at
    ``releases/latest/download/bfa_coworker_installer.zip``; its manifest
    version follows the bundled Coworker automatically.

    Returns the path of the written zip.
    """
    manifest = manifest_from_zip(coworker_zip)
    version = str(manifest["version"])
    with open(os.path.join(INSTALLER_SRC, "blender_manifest.toml"), "r", encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    lines = ['version = "{:s}"'.format(version) if ln.startswith("version ") else ln for ln in lines]
    out = os.path.join(output_dir, INSTALLER_ID + ".zip")
    os.makedirs(output_dir, exist_ok=True)
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(INSTALLER_ID + "/blender_manifest.toml", "\n".join(lines) + "\n")
        for name in sorted(os.listdir(INSTALLER_SRC)):
            path = os.path.join(INSTALLER_SRC, name)
            if name == "blender_manifest.toml" or not name.endswith(".py"):
                continue
            zf.write(path, "{:s}/{:s}".format(INSTALLER_ID, name))
        # Already compressed: store as-is.
        zf.write(coworker_zip, "{:s}/payload/bfa_coworker.zip".format(INSTALLER_ID),
                 compress_type=zipfile.ZIP_STORED)
    return out


def _cmd_installer(args: argparse.Namespace) -> int:
    out = build_installer(args.zip, args.output_dir)
    print("Built {:s} ({:d} bytes)".format(out, os.path.getsize(out)))
    return 0


def _cmd_add(args: argparse.Namespace) -> int:
    filename = os.path.basename(args.zip)
    url = args.url or RELEASE_ASSET_URL.format(tag=args.tag, filename=filename)
    if not args.url and not args.tag:
        print("ERROR: pass --tag (release tag) or --url")
        return 2
    entry = index_entry(args.zip, url)
    index = upsert(load_index(args.index), entry)
    save_index(index, args.index)
    print("Listed {:s} {:s}\n  archive: {:s}\n  size: {:d}  {:s}".format(
        entry["id"], entry["version"], url, entry["archive_size"], entry["archive_hash"]))
    print("  drag-to-install link:\n  {:s}".format(drag_install_url(entry)))
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    index = load_index(args.index)
    print("Repository: {:s}".format(REPO_INDEX_URL))
    for e in index["data"]:
        print("  {:s} {:s}  {:s}".format(e.get("id", "?"), e.get("version", "?"), e.get("archive_url", "")))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Maintain the Coworker extension repository listing.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_add = sub.add_parser("add", help="Add or replace a package in the listing")
    p_add.add_argument("zip", help="Path to the built extension zip")
    p_add.add_argument("--tag", default="", help="GitHub release tag the zip is attached to (e.g. v1.1.38)")
    p_add.add_argument("--url", default="", help="Explicit download URL (overrides --tag)")
    p_add.add_argument("--index", default=DEFAULT_INDEX)
    p_add.set_defaults(func=_cmd_add)
    p_inst = sub.add_parser("installer", help="Build the Coworker Installer around a Coworker zip")
    p_inst.add_argument("zip", help="Path to the built Coworker zip")
    p_inst.add_argument("--output-dir", default=os.path.join(SCRIPT_DIR, "releases"))
    p_inst.set_defaults(func=_cmd_installer)
    p_show = sub.add_parser("show", help="Print the listing")
    p_show.add_argument("--index", default=DEFAULT_INDEX)
    p_show.set_defaults(func=_cmd_show)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
