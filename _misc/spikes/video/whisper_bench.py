"""Video 0 spike (#374): whisper.cpp CLI on Windows -- speed and word timestamps.

Plain Python, run with -I:
  python -I whisper_bench.py <whisper_root> <media_dir> <out_dir> <config>...
<whisper_root> holds cpu/Release, cuda124/Release (whichever exist) and models/.
A config is  build:model:mode[:extra]  e.g.  cpu:base:words  cuda124:large-v3-turbo:words:vad
  mode  words  = -ml 1 -sow -ojf  (one word per segment)
        tokens = -ojf             (sentence segments; words rebuilt from token offsets)
        dtw    = -ojf -nfa --dtw <model> (token onsets from DTW; DTW needs flash attention off)
        wdtw   = -ml 1 -sow -ojf -nfa --dtw <model> (one word per segment, onsets from DTW)
  extra vad    = --vad -vm models/ggml-silero-v5.1.2.bin
        44k    = feed the 44.1 kHz stereo WAV instead of the 16 kHz mono one
        mp4    = feed the movie (expected to be refused)
Each run's word list is scored against the synthesizer's word starts
(<wav>.words.tsv): matched words, mean / p90 / max |start error|.
"""
import difflib
import json
import os
import re
import subprocess
import sys
import time

ROOT, MEDIA, OUT = sys.argv[1:4]
CONFIGS = sys.argv[4:]
os.makedirs(OUT, exist_ok=True)
DTW = {"base": "base", "small": "small", "large-v3-turbo": "large.v3.turbo"}


def norm(w):
    return re.sub(r"[^a-z0-9']", "", w.lower())


def truth(path):
    rows = []
    with open(path, encoding="utf-8-sig") as fh:
        for line in fh:
            t, w = line.rstrip("\n").split("\t", 1)
            rows.append((float(t), w))
    return rows


def words_from(js, mode):
    out = []
    for seg in js.get("transcription", []):
        if mode == "words":
            text = seg["text"].strip()
            if text:
                out.append((seg["offsets"]["from"] / 1000, seg["offsets"]["to"] / 1000, text))
            continue
        cur = None
        for tok in seg.get("tokens", []):
            t = tok["text"]
            if t.startswith("[_") or t.startswith("<|"):
                continue
            start = tok["offsets"]["from"] / 1000
            if mode in ("dtw", "wdtw") and tok.get("t_dtw", -1) >= 0:
                start = tok["t_dtw"] / 100
            end = tok["offsets"]["to"] / 1000
            if cur is None or t.startswith(" "):
                if cur:
                    out.append(tuple(cur))
                cur = [start, end, t.strip()]
            else:
                cur[1] = end
                cur[2] += t
        if cur:
            out.append(tuple(cur))
    return out


def score(words, ref):
    a = [norm(w[2]) for w in words]
    b = [norm(r[1]) for r in ref]
    errs = []
    for blk in difflib.SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks():
        for k in range(blk.size):
            errs.append(abs(words[blk.a + k][0] - ref[blk.b + k][0]))
    errs.sort()
    if not errs:
        return {"matched": 0}
    return {"matched": len(errs), "ref_words": len(ref), "hyp_words": len(words),
            "mean_err_s": round(sum(errs) / len(errs), 3),
            "p90_err_s": round(errs[int(0.9 * (len(errs) - 1))], 3),
            "max_err_s": round(errs[-1], 3)}


results = []
for cfg in CONFIGS:
    parts = cfg.split(":")
    build, model, mode = parts[:3]
    extra = parts[3:]
    exe = os.path.join(ROOT, build, "Release", "whisper-cli.exe")
    mpath = os.path.join(ROOT, "models", "ggml-%s.bin" % model)
    wav = os.path.join(MEDIA, "speech_16k_mono.wav")
    if "44k" in extra:
        wav = os.path.join(MEDIA, "speech_44k_stereo.wav")
    if "mp4" in extra:
        wav = os.path.join(MEDIA, "talking_16x9.mp4")
    tag = cfg.replace(":", "_")
    of = os.path.join(OUT, tag)
    cmd = [exe, "-m", mpath, "-f", wav, "-t", "8", "-l", "en", "-ojf", "-of", of]
    if mode == "words":
        cmd += ["-ml", "1", "-sow"]
    if mode == "wdtw":
        cmd += ["-ml", "1", "-sow"]
    if mode in ("dtw", "wdtw"):
        cmd += ["-nfa", "--dtw", DTW[model]]
    if "vad" in extra:
        cmd += ["--vad", "-vm", os.path.join(ROOT, "models", "ggml-silero-v5.1.2.bin")]
    t0 = time.perf_counter()
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    wall = time.perf_counter() - t0
    log = p.stdout + p.stderr
    with open(of + ".log", "w", encoding="utf-8") as fh:
        fh.write(" ".join(cmd) + "\n\n" + log)
    row = {"config": cfg, "exit": p.returncode, "wall_s": round(wall, 2)}
    m = re.search(r"total time =\s+([\d.]+) ms", log)
    row["whisper_total_s"] = round(float(m.group(1)) / 1000, 2) if m else None
    m = re.search(r"load time =\s+([\d.]+) ms", log)
    row["load_s"] = round(float(m.group(1)) / 1000, 2) if m else None
    row["backend"] = sorted(set(re.findall(r"(?:using|loaded) (\w+) backend", log)))
    if p.returncode == 0 and os.path.exists(of + ".json"):
        with open(of + ".json", encoding="utf-8") as fh:
            js = json.load(fh)
        words = words_from(js, mode)
        row["words"] = len(words)
        # Same audio in every input; the 16 kHz file's SpeakProgress times are the reference
        # (the 44.1 kHz stereo file's reported positions are not usable).
        row["score"] = score(words, truth(os.path.join(MEDIA, "speech_16k_mono.wav.words.tsv")))
        row["text_head"] = " ".join(w[2] for w in words[:12])
    else:
        row["error_tail"] = log.strip().splitlines()[-3:]
    print(json.dumps(row), flush=True)
    results.append(row)

with open(os.path.join(OUT, "bench_%d.json" % int(time.time())), "w", encoding="utf-8") as fh:
    json.dump(results, fh, indent=1)
