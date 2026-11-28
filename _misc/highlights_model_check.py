# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Highlights model check (#613): run Find and Rank on the written test transcripts with a REAL model
and say how many of the six planted moments are in the top five.  Not part of the unit suite (it
needs a model and takes minutes); run it when the prompts, the criteria or the weights change, and
put its numbers in the pull request.

Target (design, section 5): at least four of the six planted moments in the top five of the two
transcripts, and no pick starting or ending inside a sentence (the code guarantees the second).

Local model (llama-server already running, e.g. the one Coworker started)::

    python _misc/highlights_model_check.py --port 8080 --context 32768

Remote (an OpenAI-style endpoint; the key is read from an environment variable)::

    python _misc/highlights_model_check.py --url https://api.example/v1/chat/completions \\
        --model some-model --key-env MY_API_KEY --context 128000

Time per hour of talk is printed per transcript (``seconds_taken``, requests, seconds per request),
and with ``--out FILE`` everything is written as JSON.

Only the transcript text is sent: the written test transcripts are made up, nothing private.
"""

from __future__ import annotations

__all__ = ("main",)

import argparse
import importlib
import json
import os
import sys
import threading
import types
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "tests"))
FPS = 30


def _package(name: str = "bfacw_hlcheck_pkg") -> str:
    if name not in sys.modules:
        pkg = types.ModuleType(name)
        pkg.__path__ = [str(_REPO / "addon" / "bfa_coworker")]
        sys.modules[name] = pkg
    return name


def _sentences(data: dict, sr) -> list[dict]:
    right = 1 + int((data["minutes"] * 60 + 30) * FPS)
    src = {"key": "a" * 16, "path": "a.wav", "words": data["words"],
           "pieces": [{"name": "A", "left": 1, "right": right, "content_start": 1.0, "offset": 0.0, "channel": 1}]}
    return sr.read_scene([src], FPS)["sentences"]


def _planted(data: dict, sentences: list[dict]) -> list[dict]:
    out = []
    for p in data["planted"]:
        nums = [s["n"] for s in sentences if p["start"] - 0.5 <= s["t"] <= p["end"]]
        out.append({"label": p["label"], "nums": nums})
    return out


def _asker(args, aj):
    lt = importlib.import_module(_package() + ".llm_transport")
    url = args.url or "http://127.0.0.1:{:d}/v1/chat/completions".format(args.port)
    key = os.environ.get(args.key_env, "") if args.key_env else ""
    scratch = types.SimpleNamespace(error="", error_full="", error_kind="", is_thinking=False)

    def transport(*a, **k):
        with lt.using(scratch, threading.Event()):
            return lt.openai_chat_completions(*a, **k)
    transport.error_kind = lambda: str(getattr(scratch, "error_kind", "") or "")      # type: ignore[attr-defined]
    return aj.make_asker((url, key, args.model or ""), transport), scratch


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--port", type=int, default=8080, help="Local llama-server port (default 8080)")
    ap.add_argument("--url", default="", help="full chat-completions URL (Remote); overrides --port")
    ap.add_argument("--model", default="", help="model name for a Remote endpoint")
    ap.add_argument("--key-env", default="", help="name of the environment variable holding the API key")
    ap.add_argument("--context", type=int, default=32768, help="the engine's context size in tokens")
    ap.add_argument("--which", choices=("podcast", "tutorial", "both"), default="both")
    ap.add_argument("--kind", choices=("auto", "podcast", "tutorial", "talk"), default="auto")
    ap.add_argument("--count", type=int, default=5)
    ap.add_argument("--max-seconds", type=float, default=60.0)
    ap.add_argument("--out", default="", help="write the numbers as JSON to this file")
    args = ap.parse_args(argv)

    pkg = _package()
    sr = importlib.import_module(pkg + ".scene_reader")
    aj = importlib.import_module(pkg + ".ask_json")
    hl = importlib.import_module(pkg + ".highlights")
    import highlights_texts_613 as texts            # pylint: disable=import-error,import-outside-toplevel

    ask, _scratch = _asker(args, aj)
    names = ("podcast", "tutorial") if args.which == "both" else (args.which,)
    results = {}
    found_total = 0
    planted_total = 0
    for name in names:
        data = getattr(texts, name)()
        sents = _sentences(data, sr)
        planted = _planted(data, sents)
        print("[{:s}] {:d} sentences, {:.0f} minutes; Find+Rank needs at most {}".format(
            name, len(sents), data["minutes"], hl.estimate_requests(sents, args.context, args.count)))
        try:
            out = hl.find_highlights(sents, FPS, ask, kind=args.kind, max_seconds=args.max_seconds,
                                     count=args.count, context=args.context,
                                     progress=lambda t: print("   " + t, flush=True))
        except aj.Unavailable as err:
            print("   no answer from the engine: {:s}".format(str(err)))
            return 2
        found = []
        for p in planted:
            for rank, pick in enumerate(out["picks"][:5], 1):
                inside = [n for n in p["nums"] if pick["first"] <= n <= pick["last"]]
                if len(inside) * 2 >= len(p["nums"]):
                    found.append((p["label"], rank))
                    break
        found_total += len(found)
        planted_total += len(planted)
        r = out["report"]
        per = r["seconds_taken"] / max(1, r["requests"])
        print("   in the top five: {} of {:d}".format(len(found), len(planted)))
        for label, rank in found:
            print("     {:s}: place {:d}".format(label, rank))
        for i, pick in enumerate(out["picks"], 1):
            print("   {:d}. S{:d}-S{:d} {:.0f}s total {:.1f} ({:s}) {:s}".format(
                i, pick["first"], pick["last"], pick["seconds"], pick["total"], pick["kind"], pick["title"]))
        print("   requests {:d}, retries {:d}, parts failed {:d}, {:.0f} s ({:.1f} s a request), kind {:s}".format(
            r["requests"], r["retries"], r["windows_failed"], r["seconds_taken"], per, r["kind"]))
        for m in r["messages"]:
            print("   note: " + m)
        results[name] = {"found": found, "planted": [p["label"] for p in planted], "report": r, "picks": out["picks"]}
    print("TOTAL planted moments in the top five: {:d} of {:d} (target: at least 4 of 6)".format(
        found_total, planted_total))
    if args.out:
        Path(args.out).write_text(json.dumps(results, indent=1), encoding="utf-8")
    return 0 if found_total >= 4 else 1


if __name__ == "__main__":
    raise SystemExit(main())
