# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Threads 0 (#356): llama-server with 1 vs 2-3 slots -- speed and context per slot.

Throwaway spike, not shipped; plain Python, no Blender::

    python _misc/spikes/threads/llama_slots_spike.py <server_exe> <model.gguf> <out_dir> [ctx] [slots...]
    python _misc/spikes/threads/llama_slots_spike.py <server_exe> <model.gguf> <out_dir> [ctx] extras [slots...]

Starts llama-server with the add-on's own GPU flags (``llm_manager.start_local_llama``:
``--jinja --ctx-size --n-gpu-layers 99 --batch-size 2048 --ubatch-size 512
--cache-reuse 256``) once per slot setting -- ``auto`` (no ``-np``: what the add-on
runs today), then ``-np 1``, ``-np 2``, ``-np 3`` (``3+kvu`` adds ``--kv-unified``) -- and records:

* ``/props`` and ``/slots``: total slots and the context each slot gets;
* decode tok/s per request with 1..N requests running at once (from each response's
  ``timings``), and the total;
* whether one slot can still take a prompt bigger than ``ctx / N``;
* a small fixed quiz (exact answers, temperature 0) run solo and then N at a time --
  a proxy for "does a slot make the model dumber", not the add-on's quick challenges.

``extras`` instead: two threads' ~8K histories taking turns (does each keep its prompt
cache?) and two prompts of 55 % of ctx at once (a unified KV that can't hold both).

Writes ``<out_dir>/slots_<model>.json`` (``slots_extras_<model>.json``).  The server is stopped after each setting.
"""

from __future__ import annotations

import concurrent.futures as cf
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

EXE, MODEL, OUT = sys.argv[1], sys.argv[2], Path(sys.argv[3])
CTX = int(sys.argv[4]) if len(sys.argv) > 4 else 32768
SETTINGS = sys.argv[5:] or ["auto", "1", "2", "3"]
PORT = 18190
BASE = "http://127.0.0.1:{:d}".format(PORT)
OUT.mkdir(parents=True, exist_ok=True)

GEN_PROMPT = ("Write a short, practical guide (about 250 words) for a 3D artist on lighting a "
              "small product shot in Bforartists: key, fill and rim light, and one tip on HDRIs.")
QUIZ = [
    ("What is 17 * 23? Answer with the number only.", "391"),
    ("Spell the word 'node' backwards. Answer with the word only, lowercase.", "edon"),
    ("How many vertices does a cube have? Answer with the number only.", "8"),
    ("Which Blender data type stores a mesh object's material slots: Object or Scene? One word.", "object"),
    ("What is 2 to the power of 10? Answer with the number only.", "1024"),
    ("Return the JSON {\"name\": \"Cube\", \"x\": 1} exactly, nothing else.", '{"name": "cube", "x": 1}'),
    ("What is 144 / 12? Answer with the number only.", "12"),
    ("Which comes first alphabetically: 'lamp' or 'light'? One word.", "lamp"),
]


def _req(path: str, body: dict | None = None, timeout: float = 600) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _start(setting: str) -> subprocess.Popen:
    args = [EXE, "--jinja", "--host", "127.0.0.1", "--port", str(PORT), "--ctx-size", str(CTX),
            "--n-gpu-layers", "99", "--batch-size", "2048", "--ubatch-size", "512",
            "--cache-reuse", "256", "-m", MODEL]
    if setting != "auto":                       # "3" or "3+kvu" (unified KV across the slots)
        n, _, kvu = setting.partition("+")
        args += ["-np", n] + (["--kv-unified"] if kvu == "kvu" else [])
    log = open(OUT / "server_{:s}_{:s}.log".format(Path(MODEL).stem, setting), "w", encoding="utf-8")
    proc = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    for _ in range(600):
        if proc.poll() is not None:
            raise RuntimeError("llama-server exited ({})".format(proc.returncode))
        try:
            urllib.request.urlopen(BASE + "/health", timeout=2).read()
            return proc
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            time.sleep(0.5)
    proc.terminate()
    raise RuntimeError("llama-server did not become healthy")


def _stop(proc: subprocess.Popen) -> None:
    proc.terminate()
    try:
        proc.wait(20)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(10)


def _chat(prompt: str, max_tokens: int, nothink: bool = True) -> dict:
    body = {"messages": [{"role": "user", "content": prompt}], "max_tokens": max_tokens,
            "temperature": 0, "stream": False, "cache_prompt": False}
    if nothink:
        body["chat_template_kwargs"] = {"enable_thinking": False}
        body["reasoning_effort"] = "low"
    s = time.perf_counter()
    try:
        r = _req("/v1/chat/completions", body)
    except urllib.error.HTTPError as ex:
        return {"error": "HTTP {}: {}".format(ex.code, ex.read().decode()[:300])}
    t = r.get("timings") or {}
    msg = (r.get("choices") or [{}])[0].get("message") or {}
    return {"wall_s": round(time.perf_counter() - s, 2), "gen_tok": t.get("predicted_n"),
            "tok_s": round(t.get("predicted_per_second") or 0, 1),
            "prefill_tok_s": round(t.get("prompt_per_second") or 0, 1),
            "text": (msg.get("content") or "").strip()}


def _parallel(n: int, fn, *args) -> list:
    with cf.ThreadPoolExecutor(n) as ex:
        return list(ex.map(lambda _i: fn(*args), range(n)))


def _norm(text: str) -> str:
    return " ".join(text.strip().strip("`").strip().strip(".").lower().split())


def _quiz(concurrency: int) -> dict:
    def one(item):
        q, want = item
        r = _chat(q, 512)
        return _norm(r.get("text", "")).endswith(want) or _norm(r.get("text", "")) == want
    items = QUIZ * 2
    with cf.ThreadPoolExecutor(concurrency) as ex:
        ok = list(ex.map(one, items))
    return {"score": sum(ok), "of": len(ok)}


def _big_prompt(tokens: int) -> dict:
    """One request with a ~*tokens* prompt (filler), to see the per-slot limit."""
    filler = "The quick brown fox jumps over the lazy dog. " * max(1, tokens // 10)
    r = _chat(filler + "\nHow many foxes? One word.", 8)
    return {"approx_prompt_tokens": tokens, "ok": "error" not in r, "error": r.get("error", "")[:200]}


def _chat_raw(prompt: str, max_tokens: int) -> dict:
    """Like _chat, but with the prompt cache on (as the add-on sends) and the prefill numbers."""
    body = {"messages": [{"role": "user", "content": prompt}], "max_tokens": max_tokens,
            "temperature": 0, "stream": False, "cache_prompt": True,
            "chat_template_kwargs": {"enable_thinking": False}}
    s = time.perf_counter()
    try:
        r = _req("/v1/chat/completions", body)
    except urllib.error.HTTPError as ex:
        return {"error": "HTTP {}: {}".format(ex.code, ex.read().decode()[:200])}
    t = r.get("timings") or {}
    return {"wall_s": round(time.perf_counter() - s, 2), "prompt_n": t.get("prompt_n"),
            "cache_n": t.get("cache_n"), "prompt_ms": round(t.get("prompt_ms") or 0)}


def _history(tag: str, tokens: int) -> str:
    """A long, thread-specific prefix standing in for a thread's history."""
    line = "Thread {:s} note: the {:s} scene uses object {:s}_obj_{{:d}} at x={{:d}}.\n".format(tag, tag, tag)
    return "".join(line.format(i, i) for i in range(max(1, tokens // 22)))


def extras() -> dict:
    """Two threads taking turns on the server: does each keep its prompt cache?
    And with a unified KV (auto), what happens when two big prompts don't fit together?"""
    out: dict = {}
    a, b = _history("A", 8000), _history("B", 8000)
    seq = []
    for i, (tag, hist) in enumerate([("A", a), ("B", b), ("A", a), ("B", b), ("A", a), ("B", b)]):
        r = _chat_raw(hist + "Question {:d}: how many notes? One number.".format(i), 4)
        seq.append(dict(r, thread=tag))
    out["alternate_two_8k_histories"] = seq
    big = int(CTX * 0.55)
    with cf.ThreadPoolExecutor(2) as ex:
        rs = list(ex.map(lambda tag: _chat_raw(_history(tag, big) + "How many notes? One number.", 4),
                         ["C", "D"]))
    out["two_concurrent_prompts_of_55pct_ctx"] = rs
    return out


def run_setting(setting: str) -> dict:
    out: dict = {"setting": setting}
    proc = _start(setting)
    try:
        props = _req("/props")
        out["total_slots"] = props.get("total_slots")
        out["props_n_ctx"] = (props.get("default_generation_settings") or {}).get("n_ctx")
        try:
            slots = _req("/slots")
            out["slot_n_ctx"] = [s.get("n_ctx") for s in slots]
        except urllib.error.HTTPError as ex:
            out["slot_n_ctx"] = "HTTP {}".format(ex.code)
        _chat("Say hi.", 8)                                    # warm-up
        n_max = int(setting.split("+")[0]) if setting != "auto" else max(1, int(out["total_slots"] or 1))
        n_max = min(n_max, 3)
        out["decode"] = {}
        for n in range(1, n_max + 1):
            rs = _parallel(n, _chat, GEN_PROMPT, 300)
            ok = [r for r in rs if "error" not in r]
            out["decode"][str(n)] = {
                "per_request_tok_s": [r["tok_s"] for r in ok],
                "total_tok_s": round(sum(r["tok_s"] for r in ok), 1),
                "wall_s": max((r["wall_s"] for r in ok), default=0),
                "errors": [r["error"] for r in rs if "error" in r]}
        per_slot = (out["slot_n_ctx"][0] if isinstance(out["slot_n_ctx"], list) and out["slot_n_ctx"]
                    else CTX)
        out["prompt_over_ctx_div_n"] = _big_prompt(int(CTX / max(1, n_max)) + 2000) if n_max > 1 else None
        out["prompt_near_slot_ctx"] = _big_prompt(int(per_slot * 0.8))
        out["quiz_solo"] = _quiz(1)
        out["quiz_parallel"] = _quiz(n_max) if n_max > 1 else None
    finally:
        _stop(proc)
        time.sleep(3)
    return out


def main() -> None:
    if SETTINGS[0] == "extras":
        res = {"model": Path(MODEL).name, "ctx_size": CTX, "extras": []}
        for setting in SETTINGS[1:] or ["auto", "1"]:
            proc = _start(setting)
            try:
                props = _req("/props")
                r = {"setting": setting, "total_slots": props.get("total_slots")}
                _chat("Say hi.", 8)
                r.update(extras())
            except Exception as ex:  # pylint: disable=broad-exception-caught
                r = {"setting": setting, "error": "{}: {}".format(type(ex).__name__, ex)}
            finally:
                _stop(proc)
                time.sleep(3)
            print(json.dumps(r), flush=True)
            res["extras"].append(r)
            (OUT / "slots_extras_{:s}.json".format(Path(MODEL).stem)).write_text(
                json.dumps(res, indent=2), encoding="utf-8")
        return
    res = {"model": Path(MODEL).name, "ctx_size": CTX, "settings": []}
    for s in SETTINGS:
        print("== -np", s, flush=True)
        try:
            r = run_setting(s)
        except Exception as ex:  # pylint: disable=broad-exception-caught
            r = {"setting": s, "error": "{}: {}".format(type(ex).__name__, ex)}
        print(json.dumps(r), flush=True)
        res["settings"].append(r)
        (OUT / "slots_{:s}.json".format(Path(MODEL).stem)).write_text(json.dumps(res, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
