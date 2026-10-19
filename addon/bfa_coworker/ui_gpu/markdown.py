# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
A small markdown parser for the GPU chat (Tier 4, task "GPU 2").

Covers what chat replies use: headings, paragraphs, bullet and numbered
lists (nested by indentation), quotes, fenced code blocks, horizontal rules,
tables (turned into aligned monospace rows) and the inline styles bold,
italic, ``code`` and ``[links](url)``.  Anything else stays plain text.

The result is a list of :class:`Block` values made of :class:`Span` runs;
``chat_view`` lays them out.  ``blf`` has no italic and no font weights, so
the view draws bold with a double pass and italic in a softer colour.
Free of ``bpy``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

__all__ = (
    "Block",
    "Span",
    "parse",
    "parse_inline",
)


@dataclass(frozen=True)
class Span:
    """A run of text in one style: plain, bold, italic, code or link."""

    text: str
    style: str = "plain"
    url: str = ""


@dataclass
class Block:
    """One block.  *kind*: heading, para, item, quote, code, table, hr."""

    kind: str
    spans: list[Span] = field(default_factory=list)
    level: int = 0          # heading level, or list nesting depth
    marker: str = ""        # list marker ("-" or "3.")
    lines: list[str] = field(default_factory=list)   # code / table rows
    lang: str = ""

    @property
    def text(self) -> str:
        return "".join(s.text for s in self.spans)


_INLINE = re.compile(
    r"(`[^`\n]+`)"                                  # `code`
    r"|(\*\*[^*\n]+\*\*|__[^_\n]+__)"               # **bold** / __bold__
    r"|(\[[^\]\n]+\]\([^)\s]+\))"                   # [text](url)
    r"|((?<![\w*])\*[^*\s][^*\n]*(?<!\s)\*(?![\w*])"  # *italic*
    r"|(?<![\w_])_[^_\s][^_\n]*(?<!\s)_(?![\w_]))"    # _italic_
)


def parse_inline(text: str) -> list[Span]:
    """Split one paragraph's text into styled spans."""
    spans: list[Span] = []
    pos = 0
    for m in _INLINE.finditer(text):
        if m.start() > pos:
            spans.append(Span(text[pos:m.start()]))
        token = m.group(0)
        if m.group(1):
            spans.append(Span(token[1:-1], "code"))
        elif m.group(2):
            spans.append(Span(token[2:-2], "bold"))
        elif m.group(3):
            label, url = token[1:].split("](", 1)
            spans.append(Span(label, "link", url[:-1]))
        else:
            spans.append(Span(token[1:-1], "italic"))
        pos = m.end()
    if pos < len(text):
        spans.append(Span(text[pos:]))
    return [s for s in spans if s.text] or [Span("")]


_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_ITEM = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$")
_HR = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$")
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")


def _table_rows(rows: list[str]) -> list[str]:
    """Format markdown table rows as aligned monospace lines."""
    cells = [[c.strip() for c in row.strip().strip("|").split("|")] for row in rows]
    cols = max(len(r) for r in cells)
    for r in cells:
        r += [""] * (cols - len(r))
    widths = [max(len(r[i]) for r in cells) for i in range(cols)]
    out = []
    for n, r in enumerate(cells):
        out.append("  ".join(c.ljust(widths[i]) for i, c in enumerate(r)).rstrip())
        if n == 0:
            out.append("  ".join("-" * w for w in widths))
    return out


def parse(text: str) -> list[Block]:
    """Parse markdown *text* into blocks."""
    lines = text.replace("\r\n", "\n").split("\n")
    blocks: list[Block] = []
    para: list[str] = []

    def flush() -> None:
        if para:
            blocks.append(Block("para", parse_inline(" ".join(p.strip() for p in para))))
            para.clear()

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if stripped.startswith("```"):
            flush()
            lang, code = stripped[3:].strip(), []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code.append(lines[i])
                i += 1
            blocks.append(Block("code", lines=code, lang=lang))
            i += 1  # closing fence (or end of text while streaming)
            continue
        if not stripped:
            flush()
            i += 1
            continue
        heading = _HEADING.match(stripped)
        if heading:
            flush()
            blocks.append(Block("heading", parse_inline(heading.group(2)), level=len(heading.group(1))))
            i += 1
            continue
        if _HR.match(stripped):
            flush()
            blocks.append(Block("hr"))
            i += 1
            continue
        if stripped.startswith("|") and i + 1 < len(lines) and _TABLE_SEP.match(lines[i + 1]):
            flush()
            rows = [line]
            i += 2
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(lines[i])
                i += 1
            blocks.append(Block("table", lines=_table_rows(rows)))
            continue
        item = _ITEM.match(line)
        if item:
            flush()
            depth = len(item.group(1).replace("\t", "  ")) // 2
            marker = item.group(2) if item.group(2)[0].isdigit() else "-"
            blocks.append(Block("item", parse_inline(item.group(3)), level=depth, marker=marker))
            i += 1
            continue
        if stripped.startswith(">"):
            flush()
            blocks.append(Block("quote", parse_inline(stripped.lstrip("> ").strip())))
            i += 1
            continue
        para.append(line)
        i += 1
    flush()
    return blocks
