"""Video 0 spike (#374): whisper-server verbose_json -> the frozen transcript format.

  python -I transcript_from_whisper.py <verbose_json> <media_path> <model> <build> <out.json> [truth.tsv]

The request this expects (frozen route, see 4v §6):
  POST /inference  file=<16 kHz mono WAV>  response_format=verbose_json  max_len=1
                   split_on_word=true  language=<code|auto>  [prompt=<names>]
  on a server started with  -nfa --dtw <preset>
so every non-empty segment is one word, and its first token carries a DTW onset (t_dtw,
10 ms units). Word start = t_dtw / 100 + DTW_OFFSET_S (DTW lags ~0.24 s on the test clip,
for base, small and large-v3-turbo alike); end = the next word's start, at most
MAX_WORD_S after the start. With truth.tsv (word-start ground truth) it prints the error.
"""
import datetime
import difflib
import hashlib
import json
import os
import re
import sys

DTW_OFFSET_S = -0.24
MAX_WORD_S = 1.0


def media_key(path):
    """Cheap, stable content key: sha1 of size + first and last MiB (whole-file hashing is
    too slow for multi-GB movies). 16 hex chars."""
    size = os.path.getsize(path)
    h = hashlib.sha1(str(size).encode())
    with open(path, "rb") as fh:
        h.update(fh.read(1 << 20))
        if size > 2 << 20:
            fh.seek(-(1 << 20), 2)
            h.update(fh.read(1 << 20))
    return h.hexdigest()[:16], size


def convert(vj, media, model, build):
    raw = []
    for seg in vj.get("segments", []):
        text = seg.get("text", "").strip()
        toks = seg.get("words") or []
        if not text or not toks:
            continue
        t_dtw = toks[0].get("t_dtw", -1)
        start = t_dtw / 100.0 + DTW_OFFSET_S if t_dtw >= 0 else float(seg["start"])
        p = min(t.get("probability", 1.0) for t in toks)
        raw.append([max(0.0, start), float(seg["end"]), text, p])
    # Monotonic starts, ends from the next start
    for i in range(1, len(raw)):
        raw[i][0] = max(raw[i][0], raw[i - 1][0] + 0.01)
    duration = float(vj.get("duration") or 0.0)
    words = []
    for i, (s, _e, w, p) in enumerate(raw):
        nxt = raw[i + 1][0] if i + 1 < len(raw) else (duration or s + MAX_WORD_S)
        words.append({"w": w, "start": round(s, 3), "end": round(min(nxt, s + MAX_WORD_S), 3), "p": round(p, 3)})
    langs = vj.get("language_probabilities") or {}
    lang = max(langs, key=langs.get) if langs else vj.get("language", "")
    key, size = media_key(media)
    return {
        "schema": "bfa_coworker.transcript/1",
        "media": {"path": media.replace("\\", "/"), "key": key, "size": size, "duration_s": round(duration, 3)},
        "engine": {"name": "whisper.cpp", "version": "1.9.5", "build": build, "model": model,
                   "route": "whisper-server /inference verbose_json max_len=1 split_on_word -nfa --dtw",
                   "timing": "dtw", "dtw_offset_s": DTW_OFFSET_S},
        "language": lang,
        "created": datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat(),
        "words": words,
    }


def score(words, truth_path):
    def norm(w):
        return re.sub(r"[^a-z0-9']", "", w.lower())
    ref = []
    with open(truth_path, encoding="utf-8-sig") as fh:
        for line in fh:
            t, w = line.rstrip("\n").split("\t", 1)
            ref.append((float(t), w))
    a = [norm(w["w"]) for w in words]
    b = [norm(r[1]) for r in ref]
    errs = []
    for blk in difflib.SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks():
        for k in range(blk.size):
            errs.append(words[blk.a + k]["start"] - ref[blk.b + k][0])
    ab = sorted(abs(e) for e in errs)
    return {"matched": len(errs), "ref": len(ref), "hyp": len(words),
            "mean_signed_s": round(sum(errs) / len(errs), 3), "mean_abs_s": round(sum(ab) / len(ab), 3),
            "p90_abs_s": round(ab[int(0.9 * (len(ab) - 1))], 3), "max_abs_s": round(ab[-1], 3)}


if __name__ == "__main__":
    src, media, model, build, out = sys.argv[1:6]
    with open(src, encoding="utf-8") as fh:
        vj = json.load(fh)
    tr = convert(vj, media, model, build)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(tr, fh, indent=1, ensure_ascii=False)
    print("words", len(tr["words"]), "language", tr["language"], "key", tr["media"]["key"])
    if len(sys.argv) > 6:
        print(json.dumps(score(tr["words"], sys.argv[6])))
