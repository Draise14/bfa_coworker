# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Re-read the memory numbers of every curated Local model preset (#558).

The preset table in ``addon/bfa_coworker/llm_manager.py`` carries, per preset,
the numbers the #543 VRAM estimate needs (``file_bytes``, ``projector_bytes``,
``kv_bytes_per_token``, ``recurrent_bytes``, ``block_count``), so its VRAM text
is right before the model is downloaded.  This script reads them again:

* a model file found in one of the ``--local`` folders is read from disk;
* any other one from its Hugging Face URL with HTTP range requests -- only the
  start of the header, up to the first ``tokenizer.*`` key (the model's own
  keys come before the vocabulary), a few hundred KB, never the model;
* the projector only needs its size: on disk, or one HEAD request.

Nothing is written next to the models.  ``--save DIR`` keeps the header bytes
that were fetched and a JSON of the results.  The numbers come from the add-on's
own :func:`model_memory_profile`, so the script and the add-on cannot disagree.

Run from the repo root::

    python _misc/read_preset_headers.py --local "Z:/models" --save _scratch/headers

Then paste the printed ``file_bytes=...`` lines into the preset table and update the
"read on" comment above ``PRESET_MODELS``.
"""

import argparse
import importlib.util
import json
import os
import struct
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.dont_write_bytecode = True

_UA = "Mozilla/5.0"
_CHUNK = 256 * 1024


def load_llm_manager():
    repo_root = Path(__file__).resolve().parents[1]
    module_path = repo_root / "addon" / "bfa_coworker" / "llm_manager.py"
    spec = importlib.util.spec_from_file_location("llm_manager_presets", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


def hf_url(repo_id: str, filename: str) -> str:
    return "https://huggingface.co/{:s}/resolve/main/{:s}".format(repo_id, filename)


class _Source:
    """Sequential reads over a local file or a URL fetched in ranges."""

    def __init__(self, path: str = "", url: str = "", cap: int = 4 * 1024 * 1024) -> None:
        self.path, self.url, self.cap = path, url, cap
        self.buf = b""
        self.pos = 0
        self.total = None          # whole file size (Content-Range or stat)
        self.requests = 0
        if path:
            self.total = os.path.getsize(path)

    def _fetch(self, need_end: int) -> None:
        if need_end > self.cap:
            raise ValueError("header longer than the {:d} byte cap".format(self.cap))
        if self.path:
            with open(self.path, "rb") as fh:
                fh.seek(len(self.buf))
                self.buf += fh.read(max(need_end - len(self.buf), _CHUNK))
            return
        start = len(self.buf)
        end = min(max(need_end, start + _CHUNK), self.cap) - 1
        req = urllib.request.Request(self.url, headers={"User-Agent": _UA,
                                                        "Range": "bytes={:d}-{:d}".format(start, end)})
        with urllib.request.urlopen(req, timeout=60) as resp:
            if resp.status != 206:
                raise ValueError("no range support (HTTP {:d})".format(resp.status))
            rng = resp.headers.get("Content-Range") or ""
            if "/" in rng and rng.rsplit("/", 1)[1].isdigit():
                self.total = int(rng.rsplit("/", 1)[1])
            data = resp.read(end - start + 1)
        self.requests += 1
        if not data:
            raise ValueError("empty range reply")
        self.buf += data

    def read(self, n: int) -> bytes:
        while self.pos + n > len(self.buf):
            before = len(self.buf)
            self._fetch(self.pos + n)
            if len(self.buf) == before:
                raise ValueError("end of data")
        out = self.buf[self.pos:self.pos + n]
        self.pos += n
        return out

    def skip(self, n: int) -> None:
        self.read(n)


def read_model_keys(src: _Source, llm) -> "tuple[dict, str]":
    """The metadata before the first ``tokenizer.*`` key, the way ``_read_gguf`` keeps it."""
    if src.read(4) != b"GGUF":
        raise ValueError("not a GGUF file")
    version, = struct.unpack("<I", src.read(4))
    if version < 2:
        raise ValueError("GGUF v{:d}".format(version))
    _n_tensors, n_kv = struct.unpack("<QQ", src.read(16))
    scalars, t_str, t_arr = llm._GGUF_SCALARS, llm._GGUF_STRING, llm._GGUF_ARRAY

    def rstr() -> str:
        n, = struct.unpack("<Q", src.read(8))
        return src.read(n).decode("utf-8", errors="replace")

    def value(vtype: int):
        if vtype in scalars:
            fmt = scalars[vtype]
            return struct.unpack(fmt, src.read(struct.calcsize(fmt)))[0]
        if vtype == t_str:
            n, = struct.unpack("<Q", src.read(8))
            if n <= 4096:
                return src.read(n).decode("utf-8", errors="replace")
            src.skip(n)
            return None
        if vtype == t_arr:
            atype, count = struct.unpack("<IQ", src.read(12))
            if atype in scalars:
                fmt = scalars[atype]
                size = struct.calcsize(fmt)
                if count <= 1024:
                    return list(struct.unpack("<{:d}{:s}".format(count, fmt[1]), src.read(size * count)))
                src.skip(size * count)
                return None
            for _ in range(count):
                value(atype)
            return None
        raise ValueError("unknown GGUF value type {:d}".format(vtype))

    meta: dict = {}
    for _ in range(n_kv):
        key = rstr()
        if key.startswith("tokenizer."):
            return meta, key
        vtype, = struct.unpack("<I", src.read(4))
        v = value(vtype)
        if v is not None:
            meta[key] = v
    return meta, ""


def head_size(url: str) -> "int | None":
    """The file size from one HEAD request (Hugging Face's ``X-Linked-Size`` or the length)."""
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=60) as resp:
        for name in ("X-Linked-Size", "Content-Length"):
            v = resp.headers.get(name)
            if v and v.isdigit():
                return int(v)
    return None


def read_preset(preset, llm, local_dirs: list, save: "Path | None", cap: int) -> dict:
    row: dict = {"identifier": preset.identifier, "filename": preset.filename}
    local = next((str(Path(d) / preset.filename) for d in local_dirs if (Path(d) / preset.filename).is_file()), "")
    url = hf_url(preset.repo_id, preset.filename)
    src = _Source(path=local) if local else _Source(url=url, cap=cap)
    row["source"] = local or url
    meta, stop_key = read_model_keys(src, llm)
    row["header_bytes_read"] = len(src.buf)
    row["range_requests"] = src.requests
    row["stopped_at"] = stop_key
    if save is not None:
        (save / "{:s}.header.bin".format(preset.identifier)).write_bytes(src.buf)
    if local:
        row["file_bytes"] = os.path.getsize(local)
        # The full header through the add-on's own reader: the keys after the
        # vocabulary must not change the numbers (they don't on real files).
        full = llm.model_memory_profile(llm.gguf_info(local))
        row["full_header_profile"] = full
    else:
        row["file_bytes_head"] = head_size(url)
        row["file_bytes"] = src.total or row["file_bytes_head"]
    profile = llm.model_memory_profile({"meta": meta})
    if local and row["full_header_profile"] != profile:
        raise ValueError("{:s}: the keys after the vocabulary change the profile".format(preset.identifier))
    arch = str(meta.get("general.architecture") or "")
    row["architecture"] = arch
    row["block_count"] = int(meta.get(arch + ".block_count") or 0)
    # Sliding-window layers are counted as full by model_memory_profile: the
    # estimate is then an upper bound, and the text says so.
    row["sliding_window"] = int(meta.get(arch + ".attention.sliding_window") or 0)
    row["kv_bytes_per_token"] = int(profile["kv_bytes_per_token"])
    row["recurrent_bytes"] = int(profile["recurrent_bytes"])
    row["from_header"] = bool(profile["from_header"])
    row["projector_bytes"] = 0
    if preset.mmproj_filename:
        names = (llm._local_mmproj_name(preset), preset.mmproj_filename)
        proj = next((str(Path(d) / n) for d in ([os.path.dirname(local)] if local else []) + local_dirs
                     for n in names if (Path(d) / n).is_file()), "")
        if proj:
            row["projector_source"] = proj
            row["projector_bytes"] = os.path.getsize(proj)
        else:
            purl = hf_url(preset.repo_id, preset.mmproj_filename)
            row["projector_source"] = purl
            row["projector_bytes"] = head_size(purl) or 0
    return row


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--local", action="append", default=[], help="a folder with model files (read only)")
    ap.add_argument("--save", default="", help="keep the fetched header bytes and a JSON here")
    ap.add_argument("--only", action="append", default=[], help="preset identifier(s) to read")
    ap.add_argument("--cap", type=int, default=4 * 1024 * 1024, help="most header bytes fetched per file")
    args = ap.parse_args()
    llm = load_llm_manager()
    save = Path(args.save) if args.save else None
    if save is not None:
        save.mkdir(parents=True, exist_ok=True)
    rows = []
    for preset in llm.PRESET_MODELS:
        if args.only and preset.identifier not in args.only:
            continue
        try:
            row = read_preset(preset, llm, args.local, save, args.cap)
        except (OSError, ValueError, struct.error, urllib.error.URLError) as ex:
            row = {"identifier": preset.identifier, "error": str(ex)}
        rows.append(row)
        print(json.dumps(row, indent=1, default=str))
    print()
    print("# read on {:s}".format(time.strftime("%Y-%m-%d")))
    for row in rows:
        if "error" in row:
            print("# {:s}: NOT READ -- {:s}".format(row["identifier"], row["error"]))
            continue
        print("# {:s}\nfile_bytes={:d}, projector_bytes={:d}, kv_bytes_per_token={:d}, "
              "recurrent_bytes={:d}, block_count={:d}, sliding_window={:d},".format(
                  row["identifier"], row["file_bytes"], row["projector_bytes"], row["kv_bytes_per_token"],
                  row["recurrent_bytes"], row["block_count"], row["sliding_window"]))
    if save is not None:
        (save / "preset_headers.json").write_text(json.dumps(rows, indent=1, default=str), encoding="utf-8")
    return 1 if any("error" in r for r in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
