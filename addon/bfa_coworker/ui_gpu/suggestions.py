# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Suggestion buttons (#220): what a reply offers as the next step.

A reply that ends with a short list after a question or a "next steps" line
(the options it offers) gives one button per item; a reply that ends with a
question gives a "yes" to it ("Shall I add a roof?" -> "Yes, add a roof.").
The chat draws them under the newest reply; a click sends the text as your
next message.  Free of ``bpy``.
"""

from __future__ import annotations

__all__ = (
    "MAX_BUTTONS",
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


def _from_question(last_line: str) -> list[tuple[str, str]]:
    sentence = _SENTENCE_END.split(_plain(last_line))[-1]
    if not sentence.endswith("?") or len(sentence) > _MAX_CHARS * 2:
        return []
    body = sentence[:-1].strip()
    for pattern in _YES:
        found = pattern.match(body)
        if found:
            ask = _mine(found.group(1)).rstrip(" ,")
            if len(ask) <= _MAX_CHARS:
                text = "Yes, {:s}.".format(ask)
                return [(text, text)]
    return [("Yes, go ahead.", "Yes, go ahead.")]


def suggestions(reply: str) -> list[tuple[str, str]]:
    """(label, text to send) for each next step *reply* offers; empty when none."""
    lines = [line for line in (reply or "").strip().splitlines() if line.strip()]
    if not lines:
        return []
    if "```" in lines[-1]:          # ends in code: nothing to suggest
        return []
    return _from_list(lines) or _from_question(lines[-1])
