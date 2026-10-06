# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Pinned goal & plan memory (Tier 3 hardening).

Compaction summarizes old turns into a lossy memory note, and the request
builder trims the oldest messages to fit a small local window.  Both used to
erase the one thing a long task needs most: *what the user asked for* and
*how far along the agent is*.  This module keeps that state OUTSIDE the
compactable conversation:

* ``session_goal`` -- the first real request of the thread (the intent).
* ``turn_goal``    -- the request the user sent most recently.
* ``steps``        -- a short plan the model maintains through the local
  ``update_plan`` tool (never sent to the MCP server).
* ``user_notes``   -- free text the user adds by editing the plan file.

The state is rendered into a small block appended to the system prompt of
every request, so no compaction, trim or memory-writer failure can lose it.
It is mirrored to an editable ``Coworker Plan.md`` text datablock by the UI
(see ``ui_chat``), and parsed back when the user edits that file.

Deliberately free of ``bpy`` so it can be unit-tested outside Blender.
"""

from __future__ import annotations

import json
import re
from typing import Any

__all__ = (
    "GoalPlan",
    "PLAN_TOOL_NAME",
    "PLAN_TOOL_SCHEMA",
    "PLAN_TEXT_NAME",
    "request_text",
)

# Name of the local plan tool (intercepted by the turn loop like
# ``load_tools``; never forwarded to the MCP server).
PLAN_TOOL_NAME = "update_plan"

# Text datablock the plan is mirrored to.  Deliberately NOT ``Coworker_*``:
# those code-history texts are wiped by "New Thread" and ignored by entity
# tracking under that prefix.
PLAN_TEXT_NAME = "Coworker Plan.md"

# Size caps.  The goal is kept generously (a detailed brief must survive), the
# plan is kept short (small models follow 3-8 steps far better than 20).
_SESSION_GOAL_CHARS = 700
_TURN_GOAL_CHARS = 500
_STEP_CHARS = 120
_MAX_STEPS = 12
_NOTES_CHARS = 500

_STATUSES = ("todo", "doing", "done")
_MARK = {"todo": " ", "doing": ">", "done": "x"}
_MARK_TO_STATUS = {" ": "todo", "": "todo", ">": "doing", "~": "doing",
                   "x": "done", "X": "done"}

PLAN_TOOL_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": PLAN_TOOL_NAME,
        "description": (
            "Keep a short step plan for multi-step requests (3+ steps). "
            "Call once with `steps` to set the plan, then call with `done` "
            "(step numbers) as you finish steps. The plan is pinned in your "
            "instructions and survives context compaction."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "steps": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Full ordered list of short steps (replaces the plan).",
                },
                "done": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "1-based numbers of steps that are now finished.",
                },
                "current": {
                    "type": "integer",
                    "description": "1-based number of the step you are working on now.",
                },
            },
        },
    },
}


def request_text(content: Any) -> str:
    """Flatten a user message ``content`` into goal text.

    Multimodal content keeps its text blocks and notes attached images (the
    image itself cannot be pinned, but knowing one was attached matters:
    "make it look like the image" is meaningless without that hint).
    """
    if isinstance(content, list):
        texts: list[str] = []
        images = 0
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "image_url" or "image_url" in block:
                images += 1
            elif block.get("text"):
                texts.append(str(block.get("text")))
        text = " ".join(texts).strip()
        if images:
            text = "{:s} (+{:d} image{:s} attached)".format(
                text, images, "" if images == 1 else "s").strip()
        return text
    return str(content or "").strip()


def _clip(text: str, limit: int) -> str:
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


class GoalPlan:
    """Pinned goal + plan state for one chat thread."""

    def __init__(self) -> None:
        self.session_goal: str = ""
        self.turn_goal: str = ""
        self.steps: list[dict[str, str]] = []
        self.user_notes: str = ""
        # Bumped on every change so the UI knows when to rewrite the file.
        self.revision: int = 0
        # Steps completed during the CURRENT request (progress signal for the
        # auto-continue decision).
        self.done_this_request: int = 0

    # -- lifecycle ---------------------------------------------------------

    def reset(self) -> None:
        self.__init__()  # type: ignore[misc]

    def _touch(self) -> None:
        self.revision += 1

    def set_request(self, content: Any) -> None:
        """Record a new real user request (goal capture).

        The first request of the thread becomes the session goal.  Each
        request becomes the turn goal.  A plan whose steps are all done is
        cleared (a new request starts a new plan); an unfinished plan is kept
        so "continue" picks up where it left off -- the model can replace it.
        """
        text = request_text(content)
        if not text:
            return
        if not self.session_goal:
            self.session_goal = _clip(text, _SESSION_GOAL_CHARS)
        self.turn_goal = _clip(text, _TURN_GOAL_CHARS)
        if self.steps and not self.pending_steps():
            self.steps = []
        self.done_this_request = 0
        self._touch()

    # -- plan --------------------------------------------------------------

    def pending_steps(self) -> list[dict[str, str]]:
        return [s for s in self.steps if s.get("status") != "done"]

    def next_step(self) -> dict[str, str] | None:
        for s in self.steps:
            if s.get("status") == "doing":
                return s
        pend = self.pending_steps()
        return pend[0] if pend else None

    def progress(self) -> tuple[int, int]:
        """``(done, total)`` step counts."""
        return (sum(1 for s in self.steps if s.get("status") == "done"),
                len(self.steps))

    def apply_update(self, args: dict[str, Any] | None) -> str:
        """Handle an ``update_plan`` tool call.  Returns the tool result text.

        Lenient by design (small models send odd shapes): ``steps`` may be a
        list of strings or of ``{"text"/"step", "status"/"done"}`` dicts, or
        one newline-separated string; ``done`` may be ints or numeric strings;
        markdown checkboxes in step text are honoured.
        """
        args = args if isinstance(args, dict) else {}
        changed = False
        raw_steps = args.get("steps")
        if isinstance(raw_steps, str):
            raw_steps = [ln for ln in raw_steps.splitlines() if ln.strip()]
        if isinstance(raw_steps, list) and raw_steps:
            new_steps: list[dict[str, str]] = []
            for item in raw_steps[:_MAX_STEPS]:
                status = "todo"
                if isinstance(item, dict):
                    text = str(item.get("text") or item.get("step")
                               or item.get("title") or "")
                    st = str(item.get("status") or "").lower()
                    if item.get("done") is True or st in ("done", "complete", "completed"):
                        status = "done"
                    elif st in ("doing", "in_progress", "active", "current"):
                        status = "doing"
                else:
                    text = str(item)
                m = re.match(r"^\s*(?:[-*]\s*)?(?:\d+[.)]\s*)?\[([ xX>~]?)\]\s*(.*)$", text)
                if m:
                    status = _MARK_TO_STATUS.get(m.group(1), "todo")
                    text = m.group(2)
                else:
                    text = re.sub(r"^\s*(?:[-*]\s*)?\d+[.)]\s*", "", text)
                text = _clip(text, _STEP_CHARS)
                if text:
                    new_steps.append({"text": text, "status": status})
            if new_steps:
                self.steps = new_steps
                changed = True
        done = args.get("done")
        if isinstance(done, (int, str)):
            done = [done]
        if isinstance(done, list):
            for n in done:
                try:
                    i = int(n) - 1
                except (TypeError, ValueError):
                    continue
                if 0 <= i < len(self.steps) and self.steps[i]["status"] != "done":
                    self.steps[i]["status"] = "done"
                    self.done_this_request += 1
                    changed = True
        current = args.get("current")
        if current is not None:
            try:
                ci = int(current) - 1
            except (TypeError, ValueError):
                ci = -1
            if 0 <= ci < len(self.steps) and self.steps[ci]["status"] != "done":
                for s in self.steps:
                    if s["status"] == "doing":
                        s["status"] = "todo"
                self.steps[ci]["status"] = "doing"
                changed = True
        if changed:
            self._touch()
        if not self.steps:
            return ("No plan set. Call update_plan with `steps` (a short "
                    "ordered list) to create one.")
        d, t = self.progress()
        return "Plan updated ({:d}/{:d} done):\n{:s}".format(d, t, self._steps_text())

    def _steps_text(self, collapse_done: bool = False) -> str:
        lines: list[str] = []
        done_run: list[int] = []
        for i, s in enumerate(self.steps, 1):
            if collapse_done and s["status"] == "done":
                done_run.append(i)
                continue
            if done_run:
                lines.append(" [x] steps {:s} done".format(
                    "{:d}-{:d}".format(done_run[0], done_run[-1])
                    if len(done_run) > 1 else str(done_run[0])))
                done_run = []
            lines.append(" [{:s}] {:d}. {:s}".format(_MARK[s["status"]], i, s["text"]))
        if done_run:
            lines.append(" [x] steps {:s} done".format(
                "{:d}-{:d}".format(done_run[0], done_run[-1])
                if len(done_run) > 1 else str(done_run[0])))
        return "\n".join(lines)

    # -- rendering ---------------------------------------------------------

    def is_empty(self) -> bool:
        return not (self.session_goal or self.turn_goal or self.steps or self.user_notes)

    def render_block(self, max_chars: int = 1600, with_tool_hint: bool = False) -> str:
        """The pinned block appended to the system prompt of every request."""
        if self.is_empty():
            return ""
        header = "[Goal & plan -- pinned; this survives context compaction]"
        session = self.session_goal
        request = self.turn_goal
        if request and request == _clip(session, _TURN_GOAL_CHARS):
            request = ""  # same request -- show it once
        hint = (
            "For multi-step work (3+ steps) call update_plan first with "
            "short steps, then mark steps done as you finish them. Stay "
            "on the current request; reply to the user when it is done."
        ) if with_tool_hint else ""
        notes = _clip(self.user_notes, _NOTES_CHARS) if self.user_notes else ""

        def _assemble(sess: str, req: str, collapse: bool, nts: str) -> str:
            lines = [header]
            if sess:
                lines.append("Session goal: {:s}".format(sess))
            if req:
                lines.append("Current request: {:s}".format(req))
            if self.steps:
                d, t = self.progress()
                lines.append("Plan ({:d}/{:d} done):".format(d, t))
                lines.append(self._steps_text(collapse_done=collapse))
            if nts:
                lines.append("User notes: {:s}".format(nts))
            if hint:
                lines.append(hint)
            return "\n".join(lines)

        block = _assemble(session, request, False, notes)
        if len(block) <= max_chars:
            return block
        # Over budget, in priority order: the OPEN plan steps matter most
        # (they say what to do next), so finished steps collapse first, then
        # the goal texts shrink to the room left (current request keeps a
        # larger share), then notes go.
        block = _assemble(session, request, True, notes)
        if len(block) <= max_chars:
            return block
        fixed = len(_assemble("", "", True, notes)) + 40
        room = max(120, max_chars - fixed)
        if request:
            req_room = max(60, int(room * 0.55))
            sess_room = max(60, room - req_room)
        else:
            req_room, sess_room = 0, room
        block = _assemble(_clip(session, sess_room), _clip(request, req_room) if request else "",
                          True, notes)
        if len(block) > max_chars and notes:
            block = _assemble(_clip(session, sess_room), _clip(request, req_room) if request else "",
                              True, "")
        if len(block) > max_chars:
            block = block[: max(0, max_chars - 3)].rstrip() + "..."
        return block

    def status_line(self) -> str:
        """Short one-liner for the UI (e.g. ``Plan 2/5 -- next: add roof``)."""
        if not self.steps:
            return ""
        d, t = self.progress()
        nxt = self.next_step()
        if nxt is None:
            return "Plan {:d}/{:d} done".format(d, t)
        return "Plan {:d}/{:d} -- next: {:s}".format(d, t, _clip(nxt["text"], 60))

    # -- editable text mirror ---------------------------------------------

    def render_markdown(self) -> str:
        """Editable mirror written to the ``Coworker Plan.md`` text block."""
        out = [
            "# Coworker Plan",
            "<!-- Edit freely: the goal, the steps ([ ] todo, [>] doing, [x] done)",
            "     and the notes are sent to the coworker on its next request. -->",
            "",
            "## Session goal",
            self.session_goal or "(set by your first request)",
            "",
            "## Current request",
            self.turn_goal or "(none yet)",
            "",
            "## Steps",
        ]
        if self.steps:
            for s in self.steps:
                out.append("- [{:s}] {:s}".format(_MARK[s["status"]], s["text"]))
        else:
            out.append("(no plan yet -- the coworker creates one for multi-step requests)")
        out += ["", "## Notes", self.user_notes or ""]
        return "\n".join(out).rstrip() + "\n"

    def parse_markdown(self, text: str) -> bool:
        """Apply the user's edits from the text mirror.  Returns True if changed."""
        sections: dict[str, list[str]] = {}
        current = None
        for raw in str(text or "").splitlines():
            line = raw.rstrip()
            if line.startswith("<!--") or line.startswith("     ") and line.endswith("-->"):
                continue
            if line.startswith("## "):
                current = line[3:].strip().lower()
                sections[current] = []
                continue
            if line.startswith("# "):
                continue
            if current is not None:
                sections[current].append(line)

        def _body(name: str) -> str:
            body = "\n".join(sections.get(name, [])).strip()
            return "" if body.startswith("(") and body.endswith(")") else body

        before = json.dumps(self.to_dict(), sort_keys=True)
        if "session goal" in sections:
            self.session_goal = _clip(_body("session goal"), _SESSION_GOAL_CHARS)
        if "current request" in sections:
            self.turn_goal = _clip(_body("current request"), _TURN_GOAL_CHARS)
        if "steps" in sections:
            steps: list[dict[str, str]] = []
            for ln in sections["steps"]:
                m = re.match(r"^\s*[-*]\s*\[([ xX>~]?)\]\s*(.+)$", ln)
                if m:
                    steps.append({"text": _clip(m.group(2), _STEP_CHARS),
                                  "status": _MARK_TO_STATUS.get(m.group(1), "todo")})
                elif ln.strip().startswith(("- ", "* ")) and ln.strip()[2:].strip():
                    steps.append({"text": _clip(ln.strip()[2:], _STEP_CHARS), "status": "todo"})
            self.steps = steps[:_MAX_STEPS]
        if "notes" in sections:
            self.user_notes = _clip(_body("notes"), _NOTES_CHARS)
        changed = json.dumps(self.to_dict(), sort_keys=True) != before
        if changed:
            self._touch()
        return changed

    # -- persistence -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_goal": self.session_goal,
            "turn_goal": self.turn_goal,
            "steps": [dict(s) for s in self.steps],
            "user_notes": self.user_notes,
        }

    def load_dict(self, data: Any) -> None:
        if not isinstance(data, dict):
            return
        self.session_goal = str(data.get("session_goal", "") or "")
        self.turn_goal = str(data.get("turn_goal", "") or "")
        steps = data.get("steps")
        self.steps = []
        if isinstance(steps, list):
            for s in steps[:_MAX_STEPS]:
                if isinstance(s, dict) and s.get("text"):
                    st = str(s.get("status", "todo"))
                    self.steps.append({"text": str(s["text"]),
                                       "status": st if st in _STATUSES else "todo"})
        self.user_notes = str(data.get("user_notes", "") or "")
        self._touch()
