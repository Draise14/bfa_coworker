# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Audit every curated model preset against HuggingFace.

For each preset:
  * HEAD the model GGUF -> status + size (bytes)
  * if the preset declares an mmproj, HEAD it too
  * if the mmproj 404s, list the repo siblings and report the actual
    projector filename(s) so the preset can be corrected

Run from the repo root:
    python _misc/audit_presets.py
"""

import importlib.util
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path


def load_llm_manager():
    repo_root = Path(__file__).resolve().parents[1]
    module_path = repo_root / "addon" / "bfa_coworker" / "llm_manager.py"
    spec = importlib.util.spec_from_file_location("llm_manager", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


def head(url: str) -> tuple[int, int | None, str | None]:
    """Return (http_status, content_length, error)."""
    req = urllib.request.Request(url, method="HEAD")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            try:
                length = int(resp.headers.get("Content-Length") or 0) or None
            except (TypeError, ValueError):
                length = None
            return resp.status, length, None
    except urllib.error.HTTPError as ex:
        return ex.code, None, str(ex)
    except Exception as ex:  # pylint: disable=broad-exception-caught
        return 0, None, str(ex)


def repo_siblings(repo_id: str) -> list[str]:
    """Return the file list of a HF repo (empty on failure)."""
    url = "https://huggingface.co/api/models/{:s}".format(repo_id)
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode())
            return [s.get("rfilename", "") for s in data.get("siblings", [])]
    except Exception:  # pylint: disable=broad-exception-caught
        return []


def fmt_gb(size: int | None) -> str:
    if not size:
        return "?"
    return "{:.1f} GB".format(size / (1024 ** 3))


def main() -> int:
    lm = load_llm_manager()
    presets = [p for p in lm.PRESET_MODELS if p.identifier and p.filename]

    problems = 0
    for p in presets:
        print("=" * 78)
        print("{} — {}".format(p.identifier, p.name))
        print("  repo     : {}".format(p.repo_id))
        print("  model    : {}".format(p.filename))

        status, size, err = head(
            "https://huggingface.co/{}/resolve/main/{}".format(p.repo_id, p.filename)
        )
        print("  model    : HTTP {}  {}  {}".format(status, fmt_gb(size), err or ""))
        if status != 200:
            problems += 1

        if not p.mmproj_filename:
            print("  mmproj   : (none declared — text-only model)")
            continue

        mstatus, msize, merr = head(
            "https://huggingface.co/{}/resolve/main/{}".format(p.repo_id, p.mmproj_filename)
        )
        print("  mmproj   : {}  HTTP {}  {}".format(
            p.mmproj_filename, mstatus, fmt_gb(msize) if mstatus == 200 else (merr or "")))
        if mstatus == 200:
            # sanity: a real projector is hundreds of MB, not KB
            if msize and msize < 50 * 1024 * 1024:
                print("  !! mmproj is suspiciously small ({}) — likely the wrong file".format(fmt_gb(msize)))
                problems += 1
        else:
            problems += 1
            siblings = repo_siblings(p.repo_id)
            candidates = [s for s in siblings if "mmproj" in s.lower()]
            if candidates:
                print("  !! declared mmproj missing. Actual projector files in repo:")
                for c in candidates:
                    print("      - {}".format(c))
            else:
                print("  !! declared mmproj missing and no mmproj files found in repo"
                      + (" (repo list unavailable)" if not siblings else ""))

    print("=" * 78)
    print("Audit complete — {} issue(s) found".format(problems))
    return 0 if problems == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
