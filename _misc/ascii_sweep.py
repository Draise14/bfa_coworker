# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
One-off ASCII sweep for tracked source (issue #74, G1).

Removes non-ASCII characters from this project's own source under ``addon/``,
``mcp/`` and ``chat_client/`` (the same dirs ``check_ascii.py`` scans), so the
repository can be ASCII-only and console output can never raise
``UnicodeEncodeError`` on a non-UTF-8 console.

Two things make this more than a blind transliteration:

* ``ui_chat._LATEX_SYMBOLS`` is a deliberate ASCII->Unicode table (``\\infty``
  -> the infinity glyph, the Greek alphabet, math operators).  Its values are
  converted to ``\\uXXXX`` escapes instead of being changed, so the LaTeX
  converter keeps emitting the same glyphs while the *source* becomes ASCII.
* Only the decoration/emoji characters are transliterated everywhere else
  (box-drawing in comments, em/en dashes, arrows, emoji prefixes, ...).

Idempotent: running it again changes nothing.  Exits non-zero if any non-ASCII
remains in scope, so it can be used as a check.

Usage::

    python _misc/ascii_sweep.py            # apply
    python _misc/ascii_sweep.py --check     # report only, no writes
"""

__all__ = ()

import os
import re
import sys

_SCAN_DIRS = ("addon", "mcp", "chat_client")
# Subpaths to skip (match check_ascii.py: vendored deps + upstream API examples).
_SKIP_PARTS = ("vendor",)
_SKIP_SUBPATHS = (os.path.join("data", "api", "examples"),)

# Ordered replacements: longest / multi-char sequences first.
_SEQUENCES = (
    ("[\U0001f6e0\ufe0fCoworker]", "[Coworker]"),
    ("[\u26a0\ufe0fCoworker]", "[Coworker][WARN]"),
    ("\U0001f6e0\ufe0f", ""),
    ("\u26a0\ufe0f", "WARN "),
    ("\U0001f6e0", ""),
    ("\u26a0", "WARN "),
    ("\ufe0f", ""),
)

# Single-character transliterations (decoration / emoji / math leftovers).
_SINGLES = {
    "\u2500": "-",      # box drawings light horizontal
    "\u2502": "|",
    "\u2514": "-",       # box-drawing corner -> ASCII dash (NOT a backslash:
    #                       a bare backslash makes an invalid string escape)
    "\u258e": "|",
    "\u2014": "--",     # em dash
    "\u2013": "-",      # en dash
    "\u2192": "->",     # right arrow
    "\u2026": "...",    # ellipsis
    "\u00b7": "*",      # middle dot
    "\u2022": "*",      # bullet
    "\u2248": "~",
    "\u2264": "<=",
    "\u2265": ">=",
    "\u00d7": "x",
    "\u00f7": "/",
    "\u00b1": "+/-",
    "\u2213": "-/+",
    "\u2260": "!=",
    "\u221e": "inf",
    "\u221a": "sqrt",
    "\u222b": "int",
    "\u2202": "d",
    "\u2207": "grad",
    "\u27e8": "<",
    "\u27e9": ">",
    "\u2308": "ceil(",
    "\u2309": ")",
    "\u230a": "floor(",
    "\u230b": ")",
    "\u2016": "||",
    "\u00b2": "^2",
    "\u00b3": "^3",
    "\u00b9": "^1",
    "\u2070": "^0",
    "\u2074": "^4",
    "\u2075": "^5",
    "\u2076": "^6",
    "\u2077": "^7",
    "\u2078": "^8",
    "\u2079": "^9",
    "\u207a": "^+",
    "\u207b": "^-",
    "\u03a3": "Sum",
    "\u03a0": "Prod",
    "\u2728": "*",
    "\u2019": "'",
    "\u00e9": "e",
    "\u00e8": "e",
}

# Greek letters: only ever appear as values in the protected LaTeX table, but
# included so any stray occurrence is handled rather than left behind.
for _cp, _name in (
    (0x393, "Gamma"), (0x394, "Delta"), (0x398, "Theta"), (0x39b, "Lambda"),
    (0x39e, "Xi"), (0x3a6, "Phi"), (0x3a8, "Psi"), (0x3a9, "Omega"),
    (0x3b1, "alpha"), (0x3b2, "beta"), (0x3b3, "gamma"), (0x3b4, "delta"),
    (0x3b5, "epsilon"), (0x3b6, "zeta"), (0x3b7, "eta"), (0x3b8, "theta"),
    (0x3b9, "iota"), (0x3ba, "kappa"), (0x3bb, "lambda"), (0x3bc, "mu"),
    (0x3bd, "nu"), (0x3be, "xi"), (0x3c0, "pi"), (0x3c1, "rho"),
    (0x3c3, "sigma"), (0x3c4, "tau"), (0x3c6, "phi"), (0x3c7, "chi"),
    (0x3c8, "psi"), (0x3c9, "omega"),
):
    _SINGLES[chr(_cp)] = _name


def _escape_non_ascii(text: str) -> str:
    """Return *text* with every non-ASCII char replaced by a ``\\uXXXX`` escape."""
    out = []
    for ch in text:
        if ord(ch) < 128:
            out.append(ch)
        elif ord(ch) <= 0xFFFF:
            out.append("\\u{:04x}".format(ord(ch)))
        else:
            out.append("\\U{:08x}".format(ord(ch)))
    return "".join(out)


_LATEX_BLOCK_RE = re.compile(r"(_LATEX_SYMBOLS\s*=\s*\{)(.*?)(\n\})", re.DOTALL)
# ``str.maketrans(a, b)`` requires ``a`` and ``b`` to have EQUAL length.  A
# transliteration that turns one superscript char into ``^2`` would break that
# pairing, so maketrans tables are preserved by escaping their non-ASCII to
# ``\uXXXX`` instead of being transliterated.
_MAKETRANS_RE = re.compile(r"(str\.maketrans\s*\(.*?\))", re.DOTALL)


def _protect_latex_map(text: str) -> str:
    """Escape the ``_LATEX_SYMBOLS`` values so the glyph table survives."""
    def _repl(m: "re.Match[str]") -> str:
        return m.group(1) + _escape_non_ascii(m.group(2)) + m.group(3)

    return _LATEX_BLOCK_RE.sub(_repl, text)


def _protect_maketrans(text: str) -> str:
    """Escape non-ASCII inside ``str.maketrans(...)`` calls (length-paired)."""
    return _MAKETRANS_RE.sub(lambda m: _escape_non_ascii(m.group(1)), text)


def _transliterate(text: str) -> str:
    for src, dst in _SEQUENCES:
        text = text.replace(src, dst)
    for src, dst in _SINGLES.items():
        if src in text:
            text = text.replace(src, dst)
    return text


def _iter_files():
    for root in _SCAN_DIRS:
        if not os.path.isdir(root):
            continue
        for dirpath, _dirnames, filenames in os.walk(root):
            parts = dirpath.split(os.sep)
            if any(p in _SKIP_PARTS for p in parts):
                continue
            if any(sub in dirpath for sub in _SKIP_SUBPATHS):
                continue
            for fname in filenames:
                if fname.endswith((".py", ".toml")):
                    yield os.path.join(dirpath, fname)


def main() -> int:
    check_only = "--check" in sys.argv
    changed = 0
    leftover = 0
    for path in _iter_files():
        with open(path, "r", encoding="utf-8") as fh:
            original = fh.read()
        if original.isascii():
            continue
        text = _protect_latex_map(original)
        text = _protect_maketrans(text)
        text = _transliterate(text)
        still = sorted({c for c in text if ord(c) > 127})
        if still:
            leftover += 1
            print("LEFTOVER {:s}: {:s}".format(
                path, " ".join("U+%04X" % ord(c) for c in still)))
        if text != original:
            changed += 1
            if check_only:
                print("would change {:s}".format(path))
            else:
                with open(path, "w", encoding="utf-8", newline="") as fh:
                    fh.write(text)
                print("swept {:s}".format(path))
    print("----")
    print("{:d} file(s) {:s}".format(changed, "to change" if check_only else "changed"))
    print("{:d} file(s) with leftover non-ASCII".format(leftover))
    return 1 if leftover else 0


if __name__ == "__main__":
    sys.exit(main())
