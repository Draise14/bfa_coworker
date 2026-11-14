# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Claims: what each running chat is changing (Tier 4, Threads 3, #359).

While chats work side by side, Blender runs their tool calls one at a time,
but nothing stops two chats from changing the same thing in turn ("rename
Cube" in the 3D chat while the Node chat renames it too).  So every write
call records what it changes -- objects, node trees, strips, text blocks,
collections, materials -- as its chat's *claims*, which last for that chat's
running turn.  A write to something another running chat has claimed first
waits or asks you: "The 3D chat is changing Cube -- wait for it, or go
ahead?".  In Auto mode (or with nobody to ask) it waits.

``execute_blender_code`` can't be analysed precisely, so it counts as a
whole-scene write: it conflicts with any other chat's claim on a name its code
mentions, and what it really changed is claimed after it runs, from the
depsgraph's updates (:meth:`ClaimTable.note_writer` / :meth:`claim_updates`).

The table and the decisions are free of ``bpy`` (the bridge feeds them).
"""

from __future__ import annotations

__all__ = (
    "ASK",
    "Decision",
    "ClaimTable",
    "REFUSE",
    "RUN",
    "TABLE",
    "WAIT",
    "WAIT_LIMIT",
    "code_names",
    "conflict_question",
    "decide",
    "targets_of",
    "writes",
)

import json
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

# What a write call may change: (kind, name).
Target = tuple[str, str]

# Tool argument -> the kind of datablock it names.
_ARG_KINDS = {
    "object_name": "object", "object_names": "object", "target_object": "object", "objects": "object",
    "node_tree_name": "node_tree", "tree_name": "node_tree", "node_group": "node_tree",
    "node_group_name": "node_tree", "strip": "strip", "strip_name": "strip", "strip_names": "strip",
    "strips": "strip", "text_name": "text", "text_block": "text", "collection_name": "collection",
    "collection": "collection", "material_name": "material",
}
# Tools that look or only move the view: they claim nothing.
_NON_WRITING_PREFIXES = ("get_", "search_", "list_", "jump_to_", "read_")
_CODE_TOOLS = frozenset({"execute_blender_code"})
_LITERAL_RE = re.compile(r"""(?P<q>['"])(?P<s>[^'"\\\n]{1,63})(?P=q)""")
# Words a script quotes that are no datablock names.
_NOT_NAMES = frozenset({"OBJECT", "EDIT", "MESH", "CURVE", "LIGHT", "CAMERA", "EMPTY", "SUN", "POINT", "AREA",
                        "SPOT", "VIEW_3D", "WINDOW", "ADD", "SELECT", "DESELECT", "TOGGLE", "result", "status",
                        "ok", "error", "name", "location", "rotation_euler", "scale", "BSDF", "Principled BSDF",
                        "Base Color", "Material Output", "Surface", "Strength", "Color", "data", "value"})

WAIT_LIMIT = 45.0       # seconds a call waits for another chat's claim (the loop's own call times out at 60 s)
_WRITER_WINDOW = 1.0    # seconds a code call's depsgraph updates are still its own

RUN, ASK, WAIT, REFUSE = "run", "ask", "wait", "refuse"


def writes(tool: str, plan_safe: Iterable[str] = ()) -> bool:
    """*tool* may change the scene (claims apply): not a read, view or plan tool."""
    tool = str(tool or "")
    if not tool or tool in set(plan_safe):
        return False
    return not tool.startswith(_NON_WRITING_PREFIXES)


def _args_dict(args: Any) -> dict[str, Any]:
    """The call's arguments: a dict, or the bridge's JSON text (cut at 400 characters,
    so a cut one is read key by key)."""
    if isinstance(args, dict):
        return args
    text = str(args or "")
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else {}
    except ValueError:
        pass
    found: dict[str, Any] = {}
    for key, raw in re.findall(r'"(\w+)":\s*("(?:[^"\\]|\\.)*"|\[[^\]]*\])', text):
        try:
            found[key] = json.loads(raw)
        except ValueError:
            continue
    return found


def code_names(code: str) -> set[str]:
    """The names a script quotes (its string literals that could be datablock names)."""
    out = set()
    for match in _LITERAL_RE.finditer(str(code or "")):
        word = match.group("s").strip()
        if word and word not in _NOT_NAMES and not word.startswith(("bpy.", "#")) and "{" not in word:
            out.add(word)
    return out


def targets_of(tool: str, args: Any = None, code: str = "",
               exists: Callable[[str], bool] | None = None) -> tuple[set[Target], bool]:
    """``(targets, whole_scene)`` of one write call: the datablocks its arguments name,
    and for generated code (whole-scene) every name its code quotes, as ``("any", name)``
    -- with *exists*, only the quoted names of datablocks that exist now (a dict key or
    a name the code is about to make is no claim; what it made is claimed from the
    depsgraph after it ran)."""
    targets: set[Target] = set()
    values = _args_dict(args)
    for key, value in values.items():
        kind = _ARG_KINDS.get(key)
        if kind is None:
            continue
        for name in (value if isinstance(value, list) else [value]):
            if isinstance(name, str) and name.strip():
                targets.add((kind, name.strip()))
    whole = tool in _CODE_TOOLS
    if whole:
        source = str(code or "") or str(values.get("code") or "")      # the bridge's is whole
        targets.update(("any", name) for name in code_names(source) if exists is None or exists(name))
    return targets, whole


def _matches(a: Target, b: Target) -> bool:
    return a[1] == b[1] and (a[0] == b[0] or "any" in (a[0], b[0]))


@dataclass
class Decision:
    """What a write call does now: ``action`` (run / ask / wait / refuse), whose claim
    is in the way (``holder``, its tag) and on which names."""

    action: str
    holder: str = ""
    names: list[str] = field(default_factory=list)


class ClaimTable:
    """Every running chat's claims (thread-safe: the bridge writes on the main thread,
    turns end on their workers)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._claims: dict[str, set[Target]] = {}
        self._writers: dict[str, float] = {}      # chat -> when its code call ran (depsgraph claims)
        self._waiting: dict[str, str] = {}        # chat -> the chat whose claim it waits for

    def claim(self, chat: str, targets: Iterable[Target]) -> None:
        targets = {(str(k), str(n)) for k, n in targets if n}
        if not targets:
            return
        with self._lock:
            self._claims.setdefault(str(chat or ""), set()).update(targets)

    def claims_of(self, chat: str) -> set[Target]:
        with self._lock:
            return set(self._claims.get(str(chat or ""), set()))

    def release(self, chat: str) -> None:
        """*chat*'s turn ended: its claims go."""
        chat = str(chat or "")
        with self._lock:
            self._claims.pop(chat, None)
            self._writers.pop(chat, None)
            self._waiting.pop(chat, None)
            for waiter, holder in list(self._waiting.items()):
                if holder == chat:
                    self._waiting.pop(waiter, None)

    def clear(self) -> None:
        with self._lock:
            self._claims.clear()
            self._writers.clear()
            self._waiting.clear()

    def conflicts(self, chat: str, targets: Iterable[Target], running: Iterable[str]) -> dict[str, list[str]]:
        """The other running chats whose claims *targets* touch: ``{holder: [names]}``."""
        chat, running = str(chat or ""), {str(r or "") for r in running}
        targets = list(targets)
        out: dict[str, list[str]] = {}
        with self._lock:
            for holder, held in self._claims.items():
                if holder == chat or holder not in running:
                    continue
                names = sorted({t[1] for t in targets for h in held if _matches(t, h)})
                if names:
                    out[holder] = names
        return out

    # -- generated code: its changes are claimed after it ran ---------------------------
    def note_writer(self, chat: str, now: float | None = None) -> None:
        """*chat*'s generated code just ran: the next depsgraph updates are its changes."""
        with self._lock:
            self._writers[str(chat or "")] = time.monotonic() if now is None else now

    def has_writers(self) -> bool:
        with self._lock:
            return bool(self._writers)

    def claim_updates(self, targets: Iterable[Target], now: float | None = None) -> list[str]:
        """The depsgraph changed *targets*: they are claimed by the chats whose code ran
        within the last second (every such chat: two in one tick share them).  Returns
        those chats."""
        now = time.monotonic() if now is None else now
        targets = {(str(k), str(n)) for k, n in targets if n}
        with self._lock:
            chats = [c for c, at in self._writers.items() if now - at <= _WRITER_WINDOW]
            for c in list(self._writers):
                if c not in chats:
                    self._writers.pop(c, None)
            for c in chats:
                self._claims.setdefault(c, set()).update(targets)
        return chats

    # -- who waits for whom (no two chats wait for each other) -------------------------
    def set_waiting(self, chat: str, holder: str) -> None:
        with self._lock:
            self._waiting[str(chat or "")] = str(holder or "")

    def clear_waiting(self, chat: str) -> None:
        with self._lock:
            self._waiting.pop(str(chat or ""), None)

    def waits_for(self, chat: str) -> str:
        with self._lock:
            return self._waiting.get(str(chat or ""), "")


TABLE = ClaimTable()


def decide(table: ClaimTable, chat: str, targets: Iterable[Target], running: Iterable[str],
           ask: bool = True) -> Decision:
    """What *chat*'s write call on *targets* does while *running* chats work.

    * nobody else claims them, or *chat* isn't a running chat: **run**;
    * the chat holding one waits for *chat* (they'd wait for each other): **refuse**,
      so the model does something else first;
    * else **ask** you (wait / go ahead), or with *ask* off (Auto mode, nobody to ask)
      **wait** for that chat's turn to end."""
    running = [str(r or "") for r in running]
    chat = str(chat or "")
    if chat not in running:
        return Decision(RUN)
    found = table.conflicts(chat, targets, running)
    if not found:
        return Decision(RUN)
    holder = sorted(found)[0]
    names = found[holder]
    if table.waits_for(holder) == chat:
        return Decision(REFUSE, holder, names)
    return Decision(ASK if ask else WAIT, holder, names)


def conflict_question(holder_title: str, names: list[str]) -> str:
    """The card's question: "The 3D chat is changing Cube -- wait for it, or go ahead?"."""
    shown = ", ".join(names[:3]) + (" and {:d} more".format(len(names) - 3) if len(names) > 3 else "")
    return "The {:s} chat is changing {:s} -- wait for it to finish, or go ahead anyway?".format(
        holder_title or "other", shown)
