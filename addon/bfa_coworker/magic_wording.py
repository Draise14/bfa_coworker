# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Magic wording (#528): Coworker's theme is the wand, so what you SEE about helper chats and
about the model thinking is worded as magic -- helpers are mages, wizards or magicians, and
"Thinking..." is a word like "Conjuring...".

Only what is drawn changes.  Tool names, prompts, the system message, the history, the logs
and everything sent to the model keep today's text byte for byte (the Local prompt cache
depends on that, #567).  The preference "Magic wording" (on by default) turns all of it off.

Bpy-free apart from reading the preference, which fails soft: no preferences (the unit
tests) = plain wording.  The flag is read on the main thread and remembered, so a worker
thread that words a status line never touches Blender's data.
"""

from __future__ import annotations

__all__ = (
    "AGENT_WORD",
    "AGENT_ICON_NAME",
    "FRAME_PREFIX",
    "RESULTS_PREFIX",
    "THINKING_WORDS",
    "HELPER_WORDS",
    "agent_icon",
    "agent_icon_args",
    "agent_label",
    "agent_word",
    "enabled",
    "free_agent_icon",
    "group_noun",
    "helper_label",
    "helper_name",
    "helper_tip",
    "redacted_agent_text",
    "rewrite_agent_text",
    "shown_agent_text",
    "is_agent_written",
    "thinking_word",
    "set_enabled_for_tests",
)

import re
import threading
import zlib
from typing import Any

# What a helper chat can be called on screen; used interchangeably.
HELPER_WORDS: tuple[str, ...] = ("Mage", "Wizard", "Magician")

# The main agent, on screen (#608): one fixed word (the helpers rotate through three).
AGENT_WORD = "Wizard"
# Bforartists' wizard icon; the GPU chat draws its shipped full-colour PNG in every build.
from .ui_gpu.icons import AGENT_ICON as AGENT_ICON_NAME     # noqa: E402  "WIZARD", the GPU chat's own name

# One word per thinking stretch ("Conjuring...").  Short, friendly, fit for work; written for
# Coworker.
THINKING_WORDS: tuple[str, ...] = (
    "Conjuring", "Brewing", "Casting", "Scrying", "Weaving a spell",
    "Enchanting", "Divining", "Summoning", "Channeling", "Incanting",
    "Charming", "Transmuting", "Kindling", "Stirring the cauldron", "Consulting the runes",
    "Reading the stars", "Drawing sigils", "Gathering mana", "Forging a charm", "Waving the wand",
)

# The two prefixes are defined once, in helpers.py (what framing() and results_text() write).
from .helpers import FRAMING_PREFIX as FRAME_PREFIX, RESULTS_PREFIX     # noqa: E402

# The exact headings the code writes: "[Helper for "<parent>"] <task>" and
# "[Helpers] Your N helper(s) finished (...)".  A message you typed that merely starts like
# them is yours (#418).
_FRAME_HEAD = re.compile(re.escape(FRAME_PREFIX) + r' ".*?"\] ')
_RESULTS_HEAD = re.compile(re.escape(RESULTS_PREFIX) + r" Your (\d+) helpers? finished")

_flag: bool | None = None          # the preference as last read on the main thread
_forced: bool | None = None        # tests


def set_enabled_for_tests(value: bool | None) -> None:
    global _forced
    _forced = value


def _read_pref() -> bool | None:
    """The preference's value, or None when there are no preferences to ask."""
    try:
        import bpy  # pylint: disable=import-error,import-outside-toplevel
        prefs = bpy.context.preferences.addons[__package__].preferences
        return bool(getattr(prefs, "magic_wording", False))
    except Exception:  # pylint: disable=broad-exception-caught
        return None


def enabled() -> bool:
    """True when the wording is magic.  Reads the preference on the main thread; a worker
    thread gets the last value read there."""
    global _flag
    if _forced is not None:
        return _forced
    if threading.current_thread() is threading.main_thread():
        value = _read_pref()
        if value is not None:
            _flag = value
    return bool(_flag)


# -- thinking ----------------------------------------------------------------------------
def thinking_word(start: Any) -> str:
    """The word for the thinking stretch that began at *start* (the state's
    ``thinking_start_time``): the same for every redraw of the stretch, another for the next
    one.  A pure function of *start*, so nothing is stored and nothing can flicker."""
    try:
        seed = int(float(start or 0.0) * 1000.0)
    except (TypeError, ValueError):
        seed = 0
    # A hash that spreads neighbouring starts (zlib.crc32 of the decimal text).
    return THINKING_WORDS[zlib.crc32(str(seed).encode("ascii")) % len(THINKING_WORDS)]


# -- the main agent (#608) -----------------------------------------------------------
# What a friendly label calls Coworker's own agent today, longest first.  Only labels that
# go through agent_label() change; engine and mode names ("External Agent", "Coworker" mode)
# and every error message never do.
_AGENT_PHRASES = (
    (re.compile(r"\bthe Coworker agent\b"), "the " + AGENT_WORD),
    (re.compile(r"\b([Tt])he Coworker\b"), lambda m: m.group(1) + "he " + AGENT_WORD),
    (re.compile(r"\b([Tt])he agent\b"), lambda m: m.group(1) + "he " + AGENT_WORD),
    (re.compile(r"\bCoworker\b"), AGENT_WORD),
)


def agent_word(plain: str = "Coworker") -> str:
    """"Wizard" with the wording on (always the same word), else *plain*."""
    return AGENT_WORD if enabled() else plain


def agent_label(text: str) -> str:
    """*text*, a friendly label that names Coworker's own agent ("Start Coworker", "the
    agent"), as it is shown: with the wording on the agent is the Wizard ("Start Wizard"),
    off it is returned as it is.  Shown only: never use it on what the model gets."""
    if not enabled() or not text:
        return text
    for pattern, repl in _AGENT_PHRASES:
        text = pattern.sub(repl, text)
    return text


def agent_icon(plain: str) -> str:
    """The icon name for the agent in the GPU chat: the wizard with the wording on (its
    PNG ships, so it is the same in Blender), else *plain* (as today)."""
    return AGENT_ICON_NAME if enabled() else plain


_preview: dict[str, Any] = {"set": None}


def agent_icon_args(plain: str) -> dict[str, Any]:
    """Keyword arguments for a ``UILayout`` call: ``{"icon": plain}`` (as today), or with the
    wording on the wizard: Bforartists' own icon, else (Blender has none) the shipped
    full-colour PNG as an ``icon_value``, else *plain*."""
    if not enabled():
        return {"icon": plain}
    from .shared import icon as safe_icon
    if safe_icon(AGENT_ICON_NAME) == AGENT_ICON_NAME:
        return {"icon": AGENT_ICON_NAME}
    try:
        import bpy.utils.previews  # pylint: disable=import-error,import-outside-toplevel
        import os
        if _preview["set"] is None:
            path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui_gpu", "icons", "wizard_32.png")
            if not os.path.isfile(path):
                return {"icon": plain}          # no picture shipped: today's icon, not a blank
            previews = bpy.utils.previews.new()
            try:
                previews.load(AGENT_ICON_NAME, path, "IMAGE")
            except Exception:  # pylint: disable=broad-exception-caught
                bpy.utils.previews.remove(previews)       # none made, none leaked per redraw
                raise
            _preview["set"] = previews
        return {"icon_value": _preview["set"][AGENT_ICON_NAME].icon_id}
    except Exception:  # pylint: disable=broad-exception-caught
        return {"icon": plain}


def free_agent_icon() -> None:
    previews, _preview["set"] = _preview["set"], None
    if previews is not None:
        try:
            import bpy.utils.previews  # pylint: disable=import-error,import-outside-toplevel
            bpy.utils.previews.remove(previews)
        except Exception:  # pylint: disable=broad-exception-caught
            pass


# -- helpers -----------------------------------------------------------------------------
def _word_for(message: str) -> str:
    """The word for a helper's first message: from its first 48 characters (one line), so a
    title cut from the message (History, a reopened tab) gets the same word."""
    head = " ".join(str(message or "").split())[:48]
    return HELPER_WORDS[zlib.crc32(head.encode("utf-8")) % len(HELPER_WORDS)]


def helper_name(message: str, number: int) -> str:
    """The name a helper gets when it is created ("Wizard 2"): the word follows its first
    message, so it is the same wherever that message is drawn, also after a chat is saved
    and reopened (the number is only known while the helper is registered)."""
    return "{:s} {:d}".format(_word_for(message), max(1, int(number)))


def group_noun(count: int) -> str:
    """"mage" / "mages" for a group (the heading, the waiting line)."""
    return "mage" if count == 1 else "mages"


def helper_label(helper: Any, title: str = "") -> str:
    """A helper's tab label / row: "Mage 1: Node tidy" (*title* given or its own)."""
    title = title or getattr(helper, "title", "")
    name = getattr(helper, "name", "")
    return "{:s}: {:s}".format(name, title) if name and title else (name or title)


def helper_tip(helper: Any) -> str:
    """The start of a helper tab's tooltip: one plain sentence saying what it is."""
    name = getattr(helper, "name", "") or "Helper"
    return "{:s}, a helper chat that does part of the task of \"{:s}\": ".format(
        name, getattr(helper, "parent_title", ""))


# -- the agent-written messages on screen ----------------------------------------------
def is_agent_written(text: str, loose: bool = False) -> bool:
    """True for a message Coworker writes itself (a helper's first message, the "[Helpers]"
    results): not typed by you, so it is redacted on screen like any agent text.  *loose*:
    for a text cut short (a title, a memory line) whose heading may be cut: its start is
    enough."""
    text = str(text or "")
    if loose:
        return text.startswith(FRAME_PREFIX) or text.startswith(RESULTS_PREFIX)
    return bool(_FRAME_HEAD.match(text) or _RESULTS_HEAD.match(text))


def redacted_agent_text(text: str, loose: bool = False) -> str:
    """*text* with keys as ``***`` when Coworker wrote it itself, else as it is."""
    if not is_agent_written(text, loose):
        return text
    from . import log
    return log.redact(text)


def shown_agent_text(text: str, loose: bool = False) -> str:
    """*text* as the chat shows it, when Coworker wrote it itself: no key in it (#528), the
    magic heading when that wording is on.  Anything else is returned as it is (what you
    typed stays as typed, #418)."""
    if not is_agent_written(text, loose):
        return text
    from . import log
    return log.redact(rewrite_agent_text(text, loose))


def rewrite_agent_text(text: str, loose: bool = False) -> str:
    """*text* as shown with the magic wording: the heading of the helper's first message
    and of the results message.  The rest (the task, the answers) is untouched; the history
    keeps the original."""
    if not enabled() or not is_agent_written(text, loose):
        return text
    if text.startswith(FRAME_PREFIX):
        from .helpers import HELPERS
        name = ""
        for helper in HELPERS.all():
            if helper.message == text:
                name = helper.name
                break
        return _frame(text, name)
    match = _RESULTS_HEAD.match(text)
    if match:
        count = int(match.group(1))
        return "[Mages] Your {:d} {:s} finished".format(count, group_noun(count)) + text[match.end():]
    return "[Mages]" + text[len(RESULTS_PREFIX):]


def _frame(text: str, name: str) -> str:
    """``[Helper for "x"] ...`` as ``[Wizard 2 for "x"] ...``."""
    return "[{:s} for".format(name or _word_for(text)) + text[len(FRAME_PREFIX):]
