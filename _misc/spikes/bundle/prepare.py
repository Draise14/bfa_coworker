# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Bundle 0 spike (#380): lay out a throwaway Bforartists profile.

Plain Python, no bpy.  Run with ``python -I prepare.py --zip <release zip> --scratch <dir>``.

Creates under ``--scratch`` (wiped first, it must end in ``bundle-380``):

* ``res/``                     -> BLENDER_USER_RESOURCES (config, scripts, user extensions)
* ``sysext/system/bfa_coworker`` -> BLENDER_SYSTEM_EXTENSIONS: the release zip unpacked,
  the way a Bforartists build step would ship the built-in copy
* ``home/``                    -> USERPROFILE / LOCALAPPDATA / APPDATA, so Coworker's own
  caches (``~/.cache/bfa_coworker``, process guard, ...) stay out of the real profile
* ``pkgs/bfa_coworker-<next>.zip`` -> the same zip with the manifest version bumped,
  standing in for the next release served by the update repository
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
import zipfile


def bump(version: str) -> str:
    parts = [int(p) for p in version.split(".")]
    parts[-1] += 1
    return ".".join(str(p) for p in parts)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--zip", required=True)
    ap.add_argument("--scratch", required=True)
    args = ap.parse_args()

    scratch = os.path.abspath(args.scratch)
    if os.path.basename(scratch.rstrip("\\/")) != "bundle-380":
        print("refusing: --scratch must end in 'bundle-380'", file=sys.stderr)
        return 2
    for sub in ("res", "sysext", "home", "pkgs", "logs"):
        path = os.path.join(scratch, sub)
        if os.path.isdir(path):
            shutil.rmtree(path)
        os.makedirs(path)
    for sub in ("AppData/Local", "AppData/Roaming"):
        os.makedirs(os.path.join(scratch, "home", sub))

    # The built-in copy: <system extensions>/system/bfa_coworker
    system_repo = os.path.join(scratch, "sysext", "system")
    os.makedirs(system_repo)
    with zipfile.ZipFile(args.zip) as zf:
        names = zf.namelist()
        top = {n.split("/", 1)[0] for n in names}
        if top != {"bfa_coworker"}:
            print("unexpected zip layout: {!r}".format(sorted(top)[:5]), file=sys.stderr)
            return 2
        zf.extractall(system_repo)
        manifest = zf.read("bfa_coworker/blender_manifest.toml").decode("utf-8")

    version = re.search(r'^version\s*=\s*"([^"]+)"', manifest, re.M).group(1)
    nxt = bump(version)

    # The "next release": same files, manifest version bumped.  Built as an
    # extension zip (files at the root, like Blender's `extension build`).
    out = os.path.join(scratch, "pkgs", "bfa_coworker-{:s}.zip".format(nxt))
    with zipfile.ZipFile(args.zip) as src, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            data = src.read(info.filename)
            name = info.filename.split("/", 1)[1]
            if not name:
                continue
            if name == "blender_manifest.toml":
                data = re.sub(r'^version\s*=\s*"[^"]+"', 'version = "{:s}"'.format(nxt),
                              data.decode("utf-8"), count=1, flags=re.M).encode("utf-8")
            dst.writestr(name, data)

    print("built-in copy: {:s} ({:s})".format(os.path.join(system_repo, "bfa_coworker"), version))
    print("next release:  {:s} ({:s})".format(out, nxt))
    return 0


if __name__ == "__main__":
    sys.exit(main())
