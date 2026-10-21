# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Suggestion buttons (#220): what a reply offers as the next step.

A reply that ends with a short list after a question or a "next steps" line
(the options it offers) gives one button per item; a reply that ends with a
question gives two choices: its two halves ("..., or ...?"), else a "yes" ("Shall I add a
roof?" -> "Yes, add a roof.") and a "no" that lets you type what instead.
The chat draws them under the newest reply; a click sends the text as your
next message.  Free of ``bpy``.
"""

from __future__ import annotations

__all__ = (
    "MAX_BUTTONS",
    "TYPE",
    "suggestions",
)

import re

MAX_BUTTONS = 5
_MAX_CHARS = 140
_ITEM = re.compile(r"^\s*(?:[-*+•]|\d{1,2}[.)]|[a-eA-E][.)])\s+(.+?)\s*$")
# The line before a list must offer it: a question, or words like these ("Next, I could:").
_LEAD_IN = re.compile(r"\?\s*:?\s*$|\b(next|options?|choose|pick|could|would you|want|ideas?|suggest\w*)\b",
                      re.IGNORECASE)
_YES = (
    re.compile(r"^(?:shall|should|can|could|may) i (.+)$", re.IGNORECASE),
    re.compile(r"^(?:do you want|would you like) me to (.+)$", re.IGNORECASE),
    re.compile(r"^(?:want|like) me to (.+)$", re.IGNORECASE),
)
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def _plain(text: str) -> str:
    """Markdown emphasis and code marks off, one line."""
    text = re.sub(r"[*_`]+", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)       # [label](link) -> label
    return " ".join(text.split())


def _mine(text: str) -> str:
    """The model's "your" is the user's "my"."""
    swap = {"your": "my", "yours": "mine", "yourself": "myself"}
    return re.sub(r"\b(your|yours|yourself)\b", lambda m: swap[m.group(1).lower()], text, flags=re.IGNORECASE)


def _from_list(lines: list[str]) -> list[tuple[str, str]]:
    items: list[str] = []
    i = len(lines) - 1
    while i >= 0 and _ITEM.match(lines[i]):
        items.insert(0, _plain(_ITEM.match(lines[i]).group(1)))
        i -= 1
    if not 2 <= len(items) <= 8 or i < 0 or not _LEAD_IN.search(lines[i]):
        return []
    items = [t.rstrip(".") for t in items if t and len(t) <= _MAX_CHARS]
    return [(t, t) for t in items[:MAX_BUTTONS]]


# A button whose text is TYPE puts "No, " in the input for you to finish, instead of sending.
TYPE = ""


def _split_or(body: str) -> tuple[str, str] | None:
    """ "A, or B" / "A or B" at the top level (not inside brackets) -> (A, B)."""
    depth = 0
    for i, ch in enumerate(body):
        depth += ch in "([{"
        depth -= ch in ")]}"
        if depth == 0:
            for sep in (", or ", " or "):
                if body.startswith(sep, i):
                    a, b = body[:i].strip(" ,"), body[i + len(sep):].strip(" ,")
                    return (a, b) if a and b else None
    return None


def _yes(body: str) -> str:
    """ "Shall I add a roof" -> "Yes, add a roof." (your -> my); "" when it isn't that shape."""
    for pattern in _YES:
        found = pattern.match(body)
        if found:
            ask = _mine(found.group(1)).rstrip(" ,")
            if len(ask) <= _MAX_CHARS:
                return "Yes, {:s}.".format(ask)
    return ""


_AS_ANSWER = (
    (re.compile(r"^is (this|that|it) (.+)$", re.IGNORECASE), "{0} is {1}"),          # "is this X" -> "this is X"
    (re.compile(r"^(?:shall|should|can|could|may) i (.+)$", re.IGNORECASE), "{0}"),
    (re.compile(r"^(?:do you want|would you like|want) me to (.+)$", re.IGNORECASE), "{0}"),
    (re.compile(r"^would you rather (.+)$", re.IGNORECASE), "{0}"),
)


def _as_answer(half: str) -> str:
    """The second half of "A, or B?" in your words: "is this a good point for you" ->
    "this is a good point for me"."""
    for pattern, shape in _AS_ANSWER:
        found = pattern.match(half)
        if found:
            half = shape.format(*found.groups())
            break
    return re.sub(r"\byou\b", "me", _mine(half), flags=re.IGNORECASE)


def _sentence(text: str) -> str:
    text = text.strip().rstrip(".")
    return text[:1].upper() + text[1:] + "." if text else ""


def _from_question(last_line: str) -> list[tuple[str, str]]:
    """Always two choices at least (#270): "A, or B?" -> A and B; else Yes and No."""
    sentence = _SENTENCE_END.split(_plain(last_line))[-1]
    if not sentence.endswith("?") or len(sentence) > _MAX_CHARS * 2:
        return []
    body = sentence[:-1].strip()
    pair = _split_or(body)
    if pair is not None:
        first = _yes(pair[0]) or _sentence(_mine(pair[0]))
        second = _sentence(_as_answer(pair[1]))
        if first and second and len(second) <= _MAX_CHARS:
            return [(first, first), (second, second)]
    yes = _yes(body) or "Yes, go ahead."
    return [(yes, yes), ("No, something else...", TYPE)]


def suggestions(reply: str) -> list[tuple[str, str]]:
    """(label, text to send) for each next step *reply* offers; empty when none."""
    lines = [line for line in (reply or "").strip().splitlines() if line.strip()]
    if not lines:
        return []
    if "```" in lines[-1]:          # ends in code: nothing to suggest
        return []
    return _from_list(lines) or _from_question(lines[-1])
