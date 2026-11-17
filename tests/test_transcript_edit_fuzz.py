# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
The second property test of Edit by Transcript (#498): a fuzzer with its own oracle (the review of #587
wrote it; this is a copy, adapted to unittest, with a fixed seed).

The text model carries, for every token, the transcript word it came from (built from the word list, not
from ``render``'s tokens); 23 operations (delete a line / a word / two adjacent words / a stutter, a typo, a
move, a swap, a copy, a copy-and-delete (a move by copy and paste), a line replaced by another line's text, a
mark edited to another line's mark, a mark edited to another line's mark while that line is deleted, a
selection from the middle of one line to the middle of the next deleted, three lines joined, a line split,
only the marks left, ...) and pairs of them are done to the text of random transcripts (repeated words,
identical takes, one- and two-word lines, bracket / number / unicode words, blank paragraph lines); the
words the model says were removed are compared with what ``compute_edit`` cuts:

* ``CUT_KEPT``: a word is cut that the text still carries;
* ``FLAG_AND_CUT``: a line is flagged and a word of the line it came from is cut;
* ``REAPPLY_NOT_EMPTY``: the timeline after the edit, rendered again, asks for something;
* ``STALE_CUT_KEPT`` / ``STALE2_CUT_KEPT``: the same after an Apply that leaves the text as the artist typed
  it (Live, or flags: ``carry_base`` is the base the next diff uses), and after a second edit on that text.

Compared with the review's original, the oracle accepts three things the rule of the module itself makes
equal (each one is stated here, so a reader can judge it):

* **runs**: the very same token twice in a row in one line ("I I think" -> "I think"): the kept text is the
  same whichever word goes, the module cuts the earliest (:func:`transcript_edit._delete_only`), the
  model's word is the other: they count as one word (``run_canon``);
* **identical takes**: words at the same place of lines with the very same words are one choice (the text of
  an edit where a line was copied and the copy edited, a mark moved onto an identical line: both histories
  leave the very same text and marks, so no implementation can tell them apart; ``twin_canon``).  An edit
  that mixes the tails of two lines (a selection from inside one line to inside a later one, deleted)
  counts as right when the timeline shows exactly the words typed (``realized``), and so does a second edit
  on text whose first edit read a trimmed line as a copy (the same text either way);
* a second edit may cut every word of the timeline the text no longer carries, also those an earlier
  edit left on it (a copied line the first edit could not tell from its original).

Everything else is as the review had it: a word that is cut and the text still carries, a flagged line
that cuts, a second edit after an Apply that cuts, an edit that is not empty after it is applied.

Run with::

    python -m unittest tests.test_transcript_edit_fuzz -v
"""

__all__ = ()

import collections
import copy
import itertools
import random
import re
import time
import unittest

import test_transcript_edit as T

TE = T.TE
FPS = 30

MARK = re.compile(r"^\[(\d+:)?\d+:\d\d\.\d+\] ")   # my own mark parser (the renderer's format only)

# ---------------------------------------------------------------- transcripts
PLAIN = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel", "india", "juliet", "kilo",
         "lima", "mike", "november", "oscar", "papa", "quebec", "romeo", "sierra", "tango", "uniform", "victor",
         "whiskey", "xray", "yankee", "zulu", "apples", "pears", "price", "market", "winter", "summer"]
ODD = ["3.5", "well-known", "naïve", "café", "Straße", "x[1]", "(sic)", "[laughs]", "50%", "co-op", "日本",
       "don't", "e-mail", "1,000", "#2", "&", "—", "[1]", "[2.5]"]
SMALL = ["the", "and", "we", "is", "a", "to", "so", "okay"]


def sentence(rng, vocab, n):
    return [rng.choice(vocab) for _ in range(n)]


def make_transcript(rng, repeats=True, odd=True, lines=(5, 9)):
    vocab = PLAIN + (SMALL if repeats else []) + (ODD if odd else [])
    sents = []
    used = set()
    for _ in range(rng.randint(*lines)):
        n = rng.randint(1, 8)
        if repeats:
            toks = sentence(rng, vocab, n)
            if n >= 2 and rng.random() < 0.25:                   # stutter
                k = rng.randrange(n - 1)
                toks.insert(k + 1, toks[k])
            if n >= 3 and rng.random() < 0.1:                    # run of three
                k = rng.randrange(n)
                toks[k:k + 1] = [toks[k]] * 3
        else:
            pool = [w for w in vocab if w not in used]
            if len(pool) < n:
                break
            toks = rng.sample(pool, n)
            used.update(toks)
        if rng.random() < 0.3 and len(toks) > 2:
            toks[rng.randrange(len(toks) - 1)] += ","
        toks[-1] += "."
        sents.append(toks)
    if repeats and len(sents) >= 2:
        for _ in range(rng.choice((1, 1, 2))):                   # identical takes
            sents.insert(rng.randrange(len(sents) + 1), list(rng.choice(sents)))
        if rng.random() < 0.3:                                   # a near-take (one word differs)
            s = list(rng.choice(sents))
            s[rng.randrange(len(s))] = rng.choice(PLAIN) + "."
            sents.insert(rng.randrange(len(sents) + 1), s)
    words, t = [], 1.0
    for toks in sents:
        for tok in toks:
            words.append({"w": tok, "start": round(t, 3), "end": round(t + 0.3, 3)})
            t += 0.4
        t += rng.choice((0.2, 0.5, 0.9, 1.5, 2.5))
    return words


def piece(name="talk", left=1, right=None, cs=1.0, off=0.0):
    return {"name": name, "left": left, "right": right if right is not None else 1 + 2000 * FPS,
            "content_start": cs, "offset": off}


# ---------------------------------------------------------------- model
class Model:
    def __init__(self, text, occ, words, retext=None):
        """Lines of the text: {"mark", "toks": [(tok, word)], "origin": set of base line numbers}."""
        seq = []                                         # the words' tokens, in timeline order (my mapping)
        for o in occ:
            shown = TE.display(o["i"], words, retext)
            for tok in shown.split():
                seq.append((tok, o["i"]))
        self.lines = []
        k = 0
        for n, raw in enumerate(text.split("\n")[:-1] if text.endswith("\n") else text.split("\n")):
            m = MARK.match(raw)
            mark = m.group(0)[:-1] if m else None
            body = raw[m.end():] if m else raw
            toks = []
            for tok in body.split():
                assert seq[k][0] == tok, (seq[k], tok, raw)
                toks.append(seq[k])
                k += 1
            self.lines.append({"mark": mark, "toks": toks, "origin": {n} if toks else set()})
        assert k == len(seq)
        self.made = 0

    def text(self):
        out = []
        for ln in self.lines:
            body = " ".join(t for t, _w in ln["toks"])
            if ln["mark"] is None:
                out.append(body)
            else:
                out.append(ln["mark"] + (" " + body if body else ""))
        return "\n".join(out) + "\n"

    def wordset(self):
        return {w for ln in self.lines for _t, w in ln["toks"] if w is not None}

    def order(self):
        seen, out = set(), []
        for ln in self.lines:
            for _t, w in ln["toks"]:
                if w is not None and w not in seen:
                    seen.add(w)
                    out.append(w)
        return out

    def textual(self):
        return [k for k, ln in enumerate(self.lines) if ln["toks"]]


# ---------------------------------------------------------------- operations
def _typo(tok, rng):
    letters = [i for i, c in enumerate(tok) if c.isalpha()]
    if len(letters) < 4:
        return None
    i = rng.choice(letters[1:-1])
    return tok[:i] + ("z" if tok[i] != "z" else "q") + tok[i + 1:]


def op_del_line(m, rng):
    ks = m.textual()
    if len(ks) < 2:
        return False
    del m.lines[rng.choice(ks)]
    return True


def op_del_word(m, rng):
    ks = [k for k in m.textual() if len(m.lines[k]["toks"]) >= 2]
    if not ks:
        return False
    k = rng.choice(ks)
    del m.lines[k]["toks"][rng.randrange(len(m.lines[k]["toks"]))]
    return True


def op_del_two(m, rng):
    ks = [k for k in m.textual() if len(m.lines[k]["toks"]) >= 3]
    if not ks:
        return False
    k = rng.choice(ks)
    j = rng.randrange(len(m.lines[k]["toks"]) - 1)
    del m.lines[k]["toks"][j:j + 2]
    return True


def op_typo(m, rng):
    spots = [(k, j) for k in m.textual() for j, (t, w) in enumerate(m.lines[k]["toks"]) if w is not None
             and _typo(t, random.Random(0))]
    if not spots:
        return False
    k, j = rng.choice(spots)
    t, w = m.lines[k]["toks"][j]
    m.lines[k]["toks"][j] = (_typo(t, rng), w)
    return True


def op_move(m, rng):
    ks = m.textual()
    if len(ks) < 2:
        return False
    k = rng.choice(ks)
    ln = m.lines.pop(k)
    p = rng.choice([q for q in range(len(m.lines) + 1) if q != k])
    m.lines.insert(p, ln)
    return True


def op_del_adjacent_lines(m, rng):
    ks = m.textual()
    pairs = [(ks[i], ks[i + 1]) for i in range(len(ks) - 1)]
    if len(ks) < 3:
        return False
    a, b = rng.choice(pairs)
    del m.lines[b]
    del m.lines[a]
    return True


def op_del_line_edit_neighbour(m, rng):
    ks = m.textual()
    if len(ks) < 3:
        return False
    i = rng.randrange(len(ks))
    k = ks[i]
    nb = ks[i + 1] if i + 1 < len(ks) else ks[i - 1]
    kind = rng.choice(("typo", "delword", "addword"))
    ln = m.lines[nb]
    if kind == "typo":
        js = [j for j, (t, w) in enumerate(ln["toks"]) if _typo(t, random.Random(0))]
        if not js:
            return False
        j = rng.choice(js)
        ln["toks"][j] = (_typo(ln["toks"][j][0], rng), ln["toks"][j][1])
    elif kind == "delword":
        if len(ln["toks"]) < 2:
            return False
        del ln["toks"][rng.randrange(len(ln["toks"]))]
    else:
        m.made += 1
        ln["toks"].insert(rng.randint(0, len(ln["toks"])), ("qqtyped%d" % m.made, None))
    del m.lines[k]
    return True


def op_dup_delete_original(m, rng):
    """Copy a line (Ctrl+C, Ctrl+V somewhere), then delete the original: a move done by copy."""
    ks = m.textual()
    if len(ks) < 2:
        return False
    k = rng.choice(ks)
    ln = copy.deepcopy(m.lines[k])
    p = rng.choice([q for q in range(len(m.lines) + 1) if q not in (k, k + 1)] or [None])
    if p is None:
        return False
    m.lines.insert(p, ln)
    del m.lines[k if p > k else k + 1]
    return True


def op_dup_only(m, rng):
    ks = m.textual()
    if not ks:
        return False
    k = rng.choice(ks)
    ln = copy.deepcopy(m.lines[k])
    m.lines.insert(rng.randint(0, len(m.lines)), ln)
    return True


def op_swap(m, rng):  # noqa
    ks = m.textual()
    if len(ks) < 2:
        return False
    # prefer identical lines when there are
    by = collections.defaultdict(list)
    for k in ks:
        by[tuple(TE.norm(t) for t, _w in m.lines[k]["toks"])].append(k)
    twins = [v for v in by.values() if len(v) >= 2]
    a, b = rng.sample(rng.choice(twins), 2) if twins and rng.random() < 0.6 else rng.sample(ks, 2)
    m.lines[a], m.lines[b] = m.lines[b], m.lines[a]
    return True


def op_delmark_move(m, rng):
    ks = [k for k in m.textual() if m.lines[k]["mark"]]
    if len(ks) < 2:
        return False
    k = rng.choice(ks)
    m.lines[k]["mark"] = None
    ln = m.lines.pop(k)
    m.lines.insert(rng.choice([q for q in range(len(m.lines) + 1) if q != k]), ln)
    return True


def op_mark_to_other(m, rng):
    ks = [k for k in m.textual() if m.lines[k]["mark"]]
    if len(ks) < 2:
        return False
    a, b = rng.sample(ks, 2)
    m.lines[a]["mark"] = m.lines[b]["mark"]
    return True


def op_mark_to_other_and_delete_that(m, rng):
    """Edit a line's mark to another line's mark, and delete that other line."""
    ks = [k for k in m.textual() if m.lines[k]["mark"]]
    if len(ks) < 3:
        return False
    a, b = rng.sample(ks, 2)
    m.lines[a]["mark"] = m.lines[b]["mark"]
    del m.lines[b]
    return True


def op_move_and_delete(m, rng):
    if not op_move(m, rng):
        return False
    return op_del_line(m, rng)


def op_several_moves(m, rng):
    for _ in range(rng.choice((2, 3))):
        if not op_move(m, rng):
            return False
    return True


def op_join3(m, rng):
    ks = m.textual()
    if len(ks) < 3:
        return False
    i = rng.randrange(len(ks) - 2)
    a, b, c = ks[i], ks[i + 1], ks[i + 2]
    m.lines[a]["toks"] = m.lines[a]["toks"] + m.lines[b]["toks"] + m.lines[c]["toks"]
    m.lines[a]["origin"] |= m.lines[b]["origin"] | m.lines[c]["origin"]
    for k in sorted(range(a + 1, c + 1), reverse=True):          # the lines between (blank ones too) go
        del m.lines[k]
    return True


def op_split_in_run(m, rng):
    spots = [(k, j) for k in m.textual() for j in range(1, len(m.lines[k]["toks"]))
             if TE.norm(m.lines[k]["toks"][j][0]) == TE.norm(m.lines[k]["toks"][j - 1][0])]
    if not spots:
        spots = [(k, j) for k in m.textual() for j in range(1, len(m.lines[k]["toks"]))]
    if not spots:
        return False
    k, j = rng.choice(spots)
    tail = m.lines[k]["toks"][j:]
    m.lines[k]["toks"] = m.lines[k]["toks"][:j]
    m.lines.insert(k + 1, {"mark": None, "toks": tail, "origin": set(m.lines[k]["origin"])})
    return True


def op_cross_line_delete(m, rng):
    """A selection from the middle of one line to the middle of the next, deleted."""
    ks = m.textual()
    if len(ks) < 2:
        return False
    i = rng.randrange(len(ks) - 1)
    a, b = ks[i], ks[i + 1]
    la, lb = m.lines[a], m.lines[b]
    j = rng.randint(1, len(la["toks"])) if len(la["toks"]) > 1 else 1
    jj = rng.randint(0, len(lb["toks"]) - 1)
    la["toks"] = la["toks"][:j] + lb["toks"][jj:]
    la["origin"] |= lb["origin"]
    for k in range(b, a, -1):
        del m.lines[k]
    return True


def op_replace_by_other(m, rng):
    ks = m.textual()
    if len(ks) < 2:
        return False
    a, b = rng.sample(ks, 2)
    m.lines[a]["toks"] = list(m.lines[b]["toks"])
    return True


def op_only_marks(m, rng):
    for ln in m.lines:
        ln["toks"] = []
    return True


def op_insert(m, rng):
    m.made += 1
    m.lines.insert(rng.randint(0, len(m.lines)), {"mark": None, "toks": [("qqnew%dx%d" % (m.made, i), None)
                                                                         for i in range(rng.randint(1, 4))],
                                                  "origin": set()})
    return True


def op_del_stutter(m, rng):
    spots = [(k, j) for k in m.textual() for j in range(1, len(m.lines[k]["toks"]))
             if TE.norm(m.lines[k]["toks"][j][0]) == TE.norm(m.lines[k]["toks"][j - 1][0])]
    if not spots:
        return False
    k, j = rng.choice(spots)
    del m.lines[k]["toks"][j]
    return True


def op_delete_mark(m, rng):
    ks = [k for k in m.textual() if m.lines[k]["mark"]]
    if not ks:
        return False
    m.lines[rng.choice(ks)]["mark"] = None
    return True


OPS = {
    "del_line": op_del_line, "del_word": op_del_word, "del_two": op_del_two, "typo": op_typo, "move": op_move,
    "del_adj_lines": op_del_adjacent_lines, "del_line_edit_nb": op_del_line_edit_neighbour,
    "dup_del_orig": op_dup_delete_original, "dup_only": op_dup_only, "swap": op_swap, "delmark_move": op_delmark_move,
    "mark_to_other": op_mark_to_other, "mark_to_other_del": op_mark_to_other_and_delete_that,
    "move_and_del": op_move_and_delete, "several_moves": op_several_moves, "join3": op_join3,
    "split_in_run": op_split_in_run, "cross_line_del": op_cross_line_delete, "replace_by_other": op_replace_by_other,
    "only_marks": op_only_marks, "insert": op_insert, "del_stutter": op_del_stutter, "delete_mark": op_delete_mark,
}


# ---------------------------------------------------------------- the applied timeline
def layout(words, base_order, final, fps=FPS):
    """Pieces of the source laid out in *final* order: consecutive runs of the base order are one piece each,
    cut at the middle of the pauses around them (what moves and ripple cuts leave)."""
    pos = {w: k for k, w in enumerate(base_order)}
    runs = []
    for w in final:
        if runs and pos[w] == pos[runs[-1][-1]] + 1:
            runs[-1].append(w)
        else:
            runs.append([w])
    pieces, cursor = [], 1
    for n, run in enumerate(runs):
        a, b = pos[run[0]], pos[run[-1]]
        ws = words[run[0]]["start"]
        we = words[run[-1]]["end"]
        s = (words[base_order[a - 1]]["end"] + ws) / 2 if a > 0 else ws - 0.3
        e = (we + words[base_order[b + 1]]["start"]) / 2 if b + 1 < len(base_order) else we + 0.3
        s = min(s, ws - 0.01)
        e = max(e, we + 0.01)
        length = int(round((e - s) * fps))
        left = cursor
        pieces.append({"name": "p%d" % n, "left": left, "right": left + length,
                       "content_start": left - s * fps, "offset": 0.0})
        cursor += length
    return pieces


# ---------------------------------------------------------------- running
def view(words, pieces, retext=None):
    occ = TE.timeline_words(words, pieces, FPS)
    return occ, TE.render(occ, words, retext, FPS)


def compute(v, text, words, retext=None):
    return TE.compute_edit(v["tokens"], v["order"], text, words, retext, base_lines=v["lines"])


Result = collections.namedtuple("Result", "kind case ops base text cut expect flags extra")


def run_canon(model):
    """word -> first word of the run of the very same token (as written) next to it in one line (the kept text is the same whichever)"""
    c = {}
    for ln in model.lines:
        first = None
        prev = None
        for t, w in ln["toks"]:
            if w is None:
                prev = None
                continue
            if prev is not None and prev == t:
                c[w] = first
            else:
                first = w
                c[w] = w
            prev = t
    return c


def twin_canon(model, *more, suffix=False):
    """run classes plus: words at the same position of lines with the very same words (identical takes) are one class"""
    parent = {}

    def find(x):
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        parent[find(a)] = find(b)
    by = collections.defaultdict(list)
    for mdl in (model,) + more:
        by = collections.defaultdict(list)
        for ln in mdl.lines:
            ws = [w for _t, w in ln["toks"]]
            if ws and all(w is not None for w in ws):
                by[tuple(TE.norm(t) for t, _w in ln["toks"])].append(ws)
        for group in (by.values()):
            for ws in group[1:]:
                for a, b in zip(group[0], ws):
                    union(a, b)
        for w, r in run_canon(mdl).items():
            union(w, r)
        if suffix:          # a selection from inside one line to inside a later one: the tails of two lines are one choice
            tl = [[(TE.norm(t), w) for t, w in ln["toks"]] for ln in mdl.lines if ln["toks"]]
            for x in range(len(tl)):
                for y in range(x + 1, len(tl)):
                    a, b = tl[x], tl[y]
                    for s_ in range(1, min(len(a), len(b)) + 1):
                        if [t for t, _w in a[-s_:]] != [t for t, _w in b[-s_:]]:
                            break
                        if a[-s_][1] is not None and b[-s_][1] is not None:
                            union(a[-s_][1], b[-s_][1])
    return {w: find(w) for w in list(parent)}


def realized(words, e, typed_text):
    """After the edit the timeline shows exactly the words the typed text has (marks aside): whichever words of the
    same text were meant, the result is what the user typed."""
    shown = [tok for w in e["order"] for tok in TE.display(w, words, e["retext"]).split()]
    typed = []
    for raw in typed_text.splitlines():
        m = MARK.match(raw)
        typed.extend((raw[m.end():] if m else raw).split())
    return shown == typed


def covered(cut, expect, canon):
    """multiset of canonical cut words inside the multiset of canonical expected words"""
    a = collections.Counter(canon.get(w, w) for w in cut)
    b = expect if isinstance(expect, collections.Counter) else collections.Counter(canon.get(w, w) for w in expect)
    return not (a - b)


def check(words, v, model_before, model_after, e, expect_removed, ops, case, results, stage=""):
    cut = set(e["cut"])
    flagged = {f["line"] for f in e["flags"]}
    ok = True
    canon = run_canon(model_before)
    if not covered(cut, expect_removed, canon):
        nc = sorted(TE.norm(words[w]["w"]) for w in cut - expect_removed)
        ne = sorted(TE.norm(words[w]["w"]) for w in expect_removed - cut)
        tails = "cross_line_del" in ops
        kind = "TAKE_SWAP" if nc == ne or covered(cut, expect_removed, twin_canon(model_before, suffix=tails)) \
            or (tails and realized(words, e, model_after.text())) else "CUT_KEPT"
        results.append(Result(kind + stage, case, ops, v["text"], model_after.text(), sorted(cut),
                              sorted(expect_removed), e["flags"], sorted(cut - expect_removed)))
        ok = False
    # a flagged line never produces a cut: no cut word comes from a base line the flagged line came from
    base_line_of = {}
    for n, ln in enumerate(model_before.lines):
        for _t, w in ln["toks"]:
            if w is not None:
                base_line_of.setdefault(w, set()).update(ln["origin"])
    for n in flagged:
        if n >= len(model_after.lines):
            continue
        origin = model_after.lines[n]["origin"]
        hit = [w for w in cut if base_line_of.get(w, set()) & origin]
        if hit:
            results.append(Result(("FLAG_AND_CUT" if not covered(set(hit), expect_removed, twin_canon(model_before, suffix="cross_line_del" in ops)) else "flag_and_cut_removed") + stage, case, ops, v["text"], model_after.text(), sorted(cut),
                                  sorted(expect_removed), e["flags"], hit))
            ok = False
            break
    if not e["flags"] and cut != expect_removed:
        # a block of n words written as m words is kept on purpose: words with an empty retext
        swallowed = {int(k) for k, val in e["retext"].items() if val == ""}
        if not covered(expect_removed - swallowed, cut, canon):
            results.append(Result("SILENT_MISS" + stage, case, ops, v["text"], model_after.text(), sorted(cut),
                                  sorted(expect_removed), e["flags"], sorted(expect_removed - cut)))
    return ok


def run(seed, cases, opnames, repeats=True, odd=True, pairs=True, stale=True, results=None, stats=None):
    rng = random.Random(seed)
    results = [] if results is None else results
    stats = collections.Counter() if stats is None else stats
    for case in range(cases):
        words = make_transcript(rng, repeats=repeats, odd=odd)
        if not words:
            continue
        p = piece()
        occ, v = view(words, [p])
        m0 = Model(v["text"], occ, words)
        m = copy.deepcopy(m0)
        ops = [rng.choice(opnames)]
        if pairs and rng.random() < 0.5:
            ops.append(rng.choice(opnames))
        done = []
        for op in ops:
            if OPS[op](m, rng):
                done.append(op)
        if not done:
            continue
        key = "+".join(done)
        stats[key, "n"] += 1
        expect = m0.wordset() - m.wordset()
        if [l for l in m.text().splitlines() if l.strip()] == [l for l in m0.text().splitlines() if l.strip()]:
            continue
        try:
            e = compute(v, m.text(), words)
        except Exception as ex:   # noqa
            results.append(Result("EXCEPTION", case, done, v["text"], m.text(), [], sorted(expect), [], repr(ex)))
            continue
        if e["flags"]:
            stats[key, "flagged"] += 1
        if set(e["cut"]) == expect and not e["flags"]:
            stats[key, "exact"] += 1
        ok = check(words, v, m0, m, e, expect, done, case, results)
        # applying it: the timeline as the edit leaves it; the text the user has (Live / flags: not rewritten)
        if not ok:
            continue
        final = e["order"]
        if not final:
            continue
        pieces2 = layout(words, v["order"], final)
        occ2, v2 = view(words, pieces2, e["retext"])
        # a) Apply without flags: the text becomes v2: nothing left to do
        again = compute(v2, v2["text"], words, e["retext"])
        if again["changed"] or again["flags"]:
            results.append(Result("REAPPLY_NOT_EMPTY", case, done, v["text"], m.text(), again["cut"], [],
                                  again["flags"], {"moved": again["moved"], "retexted": again["retexted"]}))
        if not stale:
            continue
        # b) Live / flagged Apply: the user's text stays as it was typed (old marks) against the new base
        still = m.wordset() | (expect - set(e["cut"]))      # what the user's text still asks for, plus uncut removals
        try:
            m2 = Model(v2["text"], occ2, words, e["retext"])  # (only to know v2 maps cleanly)
        except AssertionError:
            pass
        cb = TE.carry_base(e, v2)               # the base the module stores after an Apply that leaves the text as typed
        stats["stale", "carry_none"] += cb is None
        vb = dict(v2, lines=cb["lines"]) if cb else v2
        e2 = compute(vb, m.text(), words, e["retext"])
        on2 = {o["i"] for o in occ2}
        _cn = run_canon(m0)
        allowed = collections.Counter(_cn.get(w, w) for w in expect) - collections.Counter(_cn.get(w, w) for w in e["cut"])
        cut2 = set(e2["cut"])
        _tc = twin_canon(m0, m, suffix="cross_line_del" in done)
        allowed_t = collections.Counter(_tc.get(w, w) for w in expect) - collections.Counter(_tc.get(w, w) for w in e["cut"])
        if not covered(cut2, allowed_t, _tc):
            results.append(Result("STALE_CUT_KEPT", case, done, v2["text"], m.text(), sorted(cut2), sorted(allowed.elements()),
                                  e2["flags"], sorted(cut2)))
        stats["stale", "n"] += 1
        if e2["flags"] and not e["flags"]:
            stats["stale", "new_flags"] += 1
        if e2["moved"] and not e["flags"]:
            stats["stale", "spurious_move"] += 1
        # c) a second edit on that stale text
        if e["flags"]:
            continue
        mm = copy.deepcopy(m)
        # origins: re-anchor to the stale text's lines
        for n, ln in enumerate(mm.lines):
            ln["origin"] = {n} if ln["toks"] else set()
        mm_before = copy.deepcopy(mm)
        op2 = rng.choice(opnames)
        if not OPS[op2](mm, rng):
            continue
        expect2 = on2 - mm.wordset()      # the words of the timeline the text no longer carries
        e3 = compute(vb, mm.text(), words, e["retext"])
        stats["stale2", "n"] += 1
        if e3["flags"]:
            stats["stale2", "flagged"] += 1
        if not covered(set(e3["cut"]), expect2, twin_canon(m0, m, mm_before, suffix="cross_line_del" in done + [op2])) \
                and not realized(words, e3, mm.text()):
            results.append(Result("STALE2_CUT_KEPT", case, done + ["|"] + [op2], v2["text"], mm.text(),
                                  sorted(e3["cut"]), sorted(expect2), e3["flags"], sorted(set(e3["cut"]) - expect2)))
    return results, stats


class TestFuzz(unittest.TestCase):

    FRESH = 6800                     # cases (the stale second edits come with them: about 4,300)

    def test_nothing_the_text_keeps_is_cut(self):
        started = time.perf_counter()
        results, stats = run(2026, self.FRESH, list(OPS))
        took = time.perf_counter() - started
        fresh = sum(v for (k, f), v in stats.items() if f == "n" and k not in ("stale", "stale2"))
        self.assertGreaterEqual(fresh, 6000)
        self.assertGreaterEqual(stats["stale", "n"], 4000)
        self.assertGreaterEqual(stats["stale2", "n"], 4000)
        bad = [r for r in results if r.kind in ("CUT_KEPT", "FLAG_AND_CUT", "STALE_CUT_KEPT", "STALE2_CUT_KEPT",
                                                 "REAPPLY_NOT_EMPTY", "EXCEPTION")]
        self.assertEqual([(r.kind, r.ops) for r in bad[:5]], [], [r._replace(base="", text="") for r in bad[:2]])
        self.assertEqual(stats["stale", "carry_none"], 0)           # every Apply could keep the artist's lines
        self.assertLess(took, 30.0)

    def test_the_fuzzer_catches_a_cut_of_a_kept_word(self):
        # the oracle is not blind: a word the text still carries that is cut is reported
        words = make_transcript(random.Random(3), repeats=False, odd=False)
        occ, v = view(words, [piece()])
        m0 = Model(v["text"], occ, words)
        m = copy.deepcopy(m0)
        self.assertTrue(op_del_word(m, random.Random(1)))
        e = compute(v, m.text(), words)
        expect = m0.wordset() - m.wordset()
        self.assertEqual(set(e["cut"]), expect)
        out = []
        bogus = dict(e, cut=sorted(set(e["cut"]) | {sorted(m.wordset())[0]}))
        self.assertFalse(check(words, v, m0, m, bogus, expect, ["del_word"], 0, out))
        self.assertEqual([r.kind for r in out], ["CUT_KEPT"])


if __name__ == "__main__":
    unittest.main()
