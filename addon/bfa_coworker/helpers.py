# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Helper chats: a chat hands parts of a job to sub-agents (Tier 4, #479).

The asking chat (the *parent*) calls ``delegate_task``: Coworker opens a helper
tab, queues the task there as its first message and answers at once -- the
parent's turn never waits.  When every helper of the parent's open batch has
finished (done, failed or stopped), ONE message with their results is queued to
the parent, which runs its next turn with them.

The schedule is the thread runner's: with one turn at a time across tabs the
helpers run one after another (Local, and every engine today); with Threads 3
(#359) the online engines run them side by side.  Nothing here starts a turn.

Limits: a helper can't start helpers (depth 1), and a parent may start at most
:data:`DEFAULT_CAP` helpers per turn (a preference).  Stop in the parent stops
its unfinished helpers and no results message is sent.

bpy-free and thread-safe: :class:`HelperRegistry` keeps who asked whom; the
add-on (``helper_chats``) opens the tabs and sends the messages.
"""

from __future__ import annotations

__all__ = (
    "DEFAULT_CAP",
    "EDITOR_KINDS",
    "FINISHED",
    "FRAMING_PREFIX",
    "HELPERS",
    "Helper",
    "HelperRegistry",
    "RESULTS_PREFIX",
    "framing",
    "last_answer",
    "parse_editor",
    "results_text",
    "short_title",
    "summary",
    "waiting_text",
)

import threading
import time
from dataclasses import dataclass, field
from typing import Any

DEFAULT_CAP = 4                 # helpers per asking turn (preference "Helpers per Turn")
ANSWER_CHARS = 1500             # each helper's answer in the results message, at most
TITLE_CHARS = 24

QUEUED, RUNNING, DONE, FAILED, STOPPED = "queued", "running", "done", "failed", "stopped"
FINISHED = frozenset((DONE, FAILED, STOPPED))

# The messages Coworker writes itself start with these (like Auto mode's "[Auto mode]"):
# the chat, History and the Goal & Plan see they aren't the user typing.
RESULTS_PREFIX = "[Helpers]"
FRAMING_PREFIX = "[Helper for"

# The editors a helper can work in: the sidebar chat's editor kinds ("" = All editors).
EDITOR_KINDS: dict[str, str] = {
    "VIEW_3D": "3D Viewport", "IMAGE_EDITOR": "Image Editor", "NODE_EDITOR": "Node Editor",
    "SEQUENCE_EDITOR": "Video Sequencer", "CLIP_EDITOR": "Movie Clip Editor",
    "DOPESHEET_EDITOR": "Dope Sheet", "GRAPH_EDITOR": "Graph Editor", "NLA_EDITOR": "NLA Editor",
    "TEXT_EDITOR": "Text Editor", "SPREADSHEET": "Spreadsheet",
}
# Words a model may use for them.
_EDITOR_WORDS: dict[str, str] = {
    "3d": "VIEW_3D", "3d view": "VIEW_3D", "3d viewport": "VIEW_3D", "viewport": "VIEW_3D", "view3d": "VIEW_3D",
    "image": "IMAGE_EDITOR", "node": "NODE_EDITOR", "nodes": "NODE_EDITOR", "shader editor": "NODE_EDITOR",
    "geometry nodes": "NODE_EDITOR", "sequencer": "SEQUENCE_EDITOR", "video sequencer": "SEQUENCE_EDITOR",
    "vse": "SEQUENCE_EDITOR", "clip": "CLIP_EDITOR", "dope sheet": "DOPESHEET_EDITOR",
    "dopesheet": "DOPESHEET_EDITOR", "graph": "GRAPH_EDITOR", "nla": "NLA_EDITOR", "text": "TEXT_EDITOR",
    "spreadsheet": "SPREADSHEET",
}
_ALL_WORDS = frozenset(("", "all", "all editors", "any", "none", "scene"))

_STATUS_WORDS = {QUEUED: "queued", RUNNING: "running", DONE: "done", FAILED: "failed", STOPPED: "stopped"}


def parse_editor(value: Any) -> str | None:
    """The editor kind for *value* ("" = All editors), or None when it names none."""
    text = " ".join(str(value or "").replace("_", " ").split()).lower()
    if text in _ALL_WORDS:
        return ""
    for kind, name in EDITOR_KINDS.items():
        if text in (kind.lower().replace("_", " "), name.lower()):
            return kind
    if text.endswith(" editor") and text[:-7] in _EDITOR_WORDS:
        return _EDITOR_WORDS[text[:-7]]
    return _EDITOR_WORDS.get(text)


def short_title(text: Any, chars: int = TITLE_CHARS) -> str:
    """A tab-sized title: *text* on one line, cut at a word."""
    line = " ".join(str(text or "").split())
    if len(line) <= chars:
        return line or "Helper"
    cut = line[:chars - 1]
    if " " in cut[chars // 2:]:
        cut = cut[:cut.rfind(" ")]
    return cut.rstrip(" ,.;:-") + "…"


def framing(parent_title: str, task: str, editor: str = "") -> str:
    """The helper's first message: whose helper it is, the task, and what to end with."""
    where = " in the {:s}'s context".format(EDITOR_KINDS.get(editor, editor)) if editor else ""
    return ("{:s} \"{:s}\"] {:s}\n\n"
            "You are a helper chat. Do only this task{:s}, then end with a short, concrete result for "
            "the chat that asked: what you did and the names of what you made or changed. You can't "
            "start helpers yourself.".format(FRAMING_PREFIX, parent_title or "Chat", str(task or "").strip(),
                                              where))


def last_answer(history: list[dict[str, Any]] | None) -> str:
    """The turn's final reply: the last assistant text after the last real user message."""
    for message in reversed(history or []):
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        if role == "assistant":
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                return content.strip()
        elif role == "user" and not message.get("system_note") and not message.get("ui_only") and \
                not str(message.get("content") or "").startswith("[System:"):
            return ""
    return ""


def asked(history: list[dict[str, Any]] | None, text: str) -> bool:
    """True when *text* (a helper's task message) is in *history*: its turn really ran."""
    head = " ".join(str(text or "").split())[:80]
    for message in history or []:
        if isinstance(message, dict) and message.get("role") == "user":
            content = message.get("content")
            if isinstance(content, list):
                content = " ".join(str(p.get("text", "")) for p in content if isinstance(p, dict))
            if head and head in " ".join(str(content or "").split()):
                return True
    return False


def waiting_text(count: int) -> str:
    return "Waiting for {:d} helper{:s}".format(count, "" if count == 1 else "s")


@dataclass
class Helper:
    """One helper chat and what became of its task."""

    id: str                      # its thread id
    parent: str                  # the asking thread's id
    title: str = ""
    editor: str = ""
    task: str = ""
    message: str = ""            # the task as sent (framing included)
    parent_title: str = ""
    status: str = QUEUED
    answer: str = ""
    error: str = ""
    config: Any = None           # the engine settings its task was sent with (the results use them)
    created: float = field(default_factory=time.time)

    @property
    def finished(self) -> bool:
        return self.status in FINISHED

    @property
    def where(self) -> str:
        return EDITOR_KINDS.get(self.editor, self.editor) if self.editor else "All editors"


def summary(helpers: list[Helper], status_of: Any = None) -> str:
    """"2 helpers: Node tidy -- running; Strip translate -- queued"."""
    status_of = status_of or (lambda h: _STATUS_WORDS.get(h.status, h.status))
    head = "{:d} helper{:s}".format(len(helpers), "" if len(helpers) == 1 else "s")
    return head + ": " + "; ".join("{:s} — {:s}".format(h.title, status_of(h)) for h in helpers)


def _trimmed(text: str, chars: int) -> tuple[str, bool]:
    text = str(text or "").strip()
    if len(text) <= chars:
        return text, False
    cut = text[:chars]
    if " " in cut[chars * 3 // 4:]:
        cut = cut[:cut.rfind(" ")]
    return cut.rstrip() + " …", True


def results_text(helpers: list[Helper], chars: int = ANSWER_CHARS) -> str:
    """The message the asking chat gets when its helpers have finished."""
    done = sum(1 for h in helpers if h.status == DONE)
    lines = ["{:s} Your {:d} helper{:s} finished ({:d} done). Their results:".format(
        RESULTS_PREFIX, len(helpers), "" if len(helpers) == 1 else "s", done)]
    for n, helper in enumerate(helpers, 1):
        head = "{:d}. {:s} ({:s}) — {:s}".format(n, helper.title, helper.where,
                                                     _STATUS_WORDS.get(helper.status, helper.status))
        if helper.status == FAILED:
            lines += ["", head + ": " + (helper.error or "it stopped with an error") + "."]
            if helper.answer:
                lines.append(_trimmed(helper.answer, chars)[0])
            continue
        if helper.status == STOPPED:
            lines += ["", head + ": it was stopped before it finished" + (
                "; what it said so far:" if helper.answer else ".")]
            if helper.answer:
                lines.append(_trimmed(helper.answer, chars)[0])
            continue
        answer, cut = _trimmed(helper.answer, chars)
        lines += ["", head, answer or "(It finished without a written answer.)"]
        if cut:
            lines.append("(Shortened to {:d} characters: the whole answer is in its tab, \"{:s}\".)".format(
                chars, helper.title))
    lines += ["", "Carry on with the job using these results; don't redo what the helpers did."]
    return "\n".join(lines)


class HelperRegistry:
    """Who asked whom: helpers per asking thread, their status, and the open batch.

    A parent's *open batch* is its helpers whose results haven't gone back yet.  When
    every one of them has finished, :meth:`take_results` hands them over (once)."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._helpers: dict[str, Helper] = {}
        self._open: dict[str, list[str]] = {}            # parent id -> helper ids not reported yet
        self._turns: dict[str, tuple[str, int]] = {}      # parent id -> (turn key, helpers started in it)

    # -- who is who ---------------------------------------------------------------
    def get(self, helper_id: str) -> Helper | None:
        with self._lock:
            return self._helpers.get(helper_id)

    def is_helper(self, thread_id: str) -> bool:
        with self._lock:
            return thread_id in self._helpers

    def parent_of(self, thread_id: str) -> str:
        with self._lock:
            helper = self._helpers.get(thread_id)
            return helper.parent if helper is not None else ""

    def batch(self, parent_id: str) -> list[Helper]:
        """The parent's open batch, oldest first."""
        with self._lock:
            return [self._helpers[h] for h in self._open.get(parent_id, ()) if h in self._helpers]

    def waiting(self, parent_id: str) -> list[Helper]:
        """The parent's helpers that haven't finished yet."""
        return [h for h in self.batch(parent_id) if not h.finished]

    # -- starting -----------------------------------------------------------------
    def refusal(self, parent_id: str, turn_key: str = "", cap: int = DEFAULT_CAP) -> str:
        """Why *parent_id* can't start a helper now ("" when it can)."""
        with self._lock:
            if parent_id in self._helpers:
                return ("A helper chat can't start helpers of its own. Do this part yourself, or say in "
                        "your result what is left for the chat that asked.")
            key, count = self._turns.get(parent_id, ("", 0))
            if key == turn_key and count >= max(1, int(cap)):
                return ("This chat already started {:d} helper{:s} this turn (the most per turn: Preferences "
                        "> Helpers per Turn). Do the rest yourself, or wait for their results.".format(
                            count, "" if count == 1 else "s"))
            return ""

    def add(self, parent_id: str, helper_id: str, title: str = "", editor: str = "", task: str = "",
            turn_key: str = "", message: str = "", parent_title: str = "") -> Helper:
        """A helper was opened for *parent_id* in its turn *turn_key*."""
        helper = Helper(id=helper_id, parent=parent_id, title=title or short_title(task), editor=editor,
                        task=task, message=message, parent_title=parent_title)
        with self._lock:
            self._helpers[helper_id] = helper
            self._open.setdefault(parent_id, []).append(helper_id)
            key, count = self._turns.get(parent_id, ("", 0))
            self._turns[parent_id] = (turn_key, (count if key == turn_key else 0) + 1)
        return helper

    # -- how it goes ----------------------------------------------------------------
    def started(self, helper_id: str) -> None:
        """The helper's turn began (queued -> running; nothing else changes)."""
        with self._lock:
            helper = self._helpers.get(helper_id)
            if helper is not None and helper.status == QUEUED:
                helper.status = RUNNING

    def ended(self, helper_id: str, stop_reason: str = "end_turn", answer: str = "", error: str = "") -> str:
        """The helper's (first) turn ended: its status and answer.  Returns its parent's id
        when that parent's whole batch has finished now (send the results), else ""."""
        with self._lock:
            helper = self._helpers.get(helper_id)
            if helper is None or helper.finished:
                return ""
            helper.answer = str(answer or "").strip()
            if stop_reason == "cancelled":
                helper.status = STOPPED
            elif stop_reason == "error" or (error and not helper.answer):
                helper.status, helper.error = FAILED, str(error or "").strip()
            else:
                helper.status = DONE
            return helper.parent if self._complete(helper.parent) else ""

    def closed(self, thread_id: str) -> str:
        """A tab closed.  A helper counts as stopped (returns its parent's id when that
        finished the batch); a parent's batch is forgotten: its helpers are plain chats now."""
        with self._lock:
            self._open.pop(thread_id, None)
            self._turns.pop(thread_id, None)
            helper = self._helpers.get(thread_id)
            if helper is None:
                return ""
            if not helper.finished:
                helper.status = STOPPED
            parent = helper.parent
            if parent in self._open and self._complete(parent):
                return parent
            return ""

    def stop_parent(self, parent_id: str) -> list[Helper]:
        """Stop in the asking chat: its unfinished helpers are stopped (returned, so their
        turns and queued tasks can be stopped) and no results message will be sent."""
        with self._lock:
            out = []
            for helper in self.batch(parent_id):
                if not helper.finished:
                    helper.status = STOPPED
                    out.append(helper)
            self._open.pop(parent_id, None)
            return out

    def take_results(self, parent_id: str) -> list[Helper]:
        """The parent's finished batch, once (then it's closed); [] while any still runs."""
        with self._lock:
            if not self._complete(parent_id):
                return []
            batch = self.batch(parent_id)
            self._open.pop(parent_id, None)
            return batch

    def _complete(self, parent_id: str) -> bool:
        batch = self.batch(parent_id)
        return bool(batch) and all(h.finished for h in batch)

    def clear(self) -> None:
        with self._lock:
            self._helpers.clear()
            self._open.clear()
            self._turns.clear()


HELPERS = HelperRegistry()
