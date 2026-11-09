# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Ask before Coworker's own risky steps (Tier 4, Engine 5, #166).

Opt-in (*Ask Before Risky Steps*, Preferences).  Code that reaches the bridge
from any engine -- the local loop, an External Agent, an External Harness
client -- is checked before it runs: if it would save the .blend file, open
or reload a file or quit (unsaved work is lost), delete or move files,
export, or run another program, the chat asks first with the approval card
(:mod:`approvals`).  *Allow once* runs it, *Always* lets that kind of step
through until Blender restarts, *Deny* tells the model you declined.

While Auto mode runs nobody is there to answer, so a risky step is declined
at once ("Auto mode doesn't do this without you").

The bridge holds the answer back (its deferred response) and runs the code
on the main thread once allowed.  Coworker's own repository tool-code is
never stopped (its first line is ``# blmcp-toolcode-skip-preflight``); code
the model wrote never skips the check, whatever it carries (#347).
"""

from __future__ import annotations

__all__ = (
    "APPROVALS",
    "MODEL_CODE_TOOLS",
    "ask_kind",
    "is_toolcode",
    "reason",
    "untrusted",
    "wrap",
)

import re
from typing import Any, Callable

from .approvals import ApprovalRequest, Approvals

# The open "may I?" questions about risky steps (the chat shows them like the agent's).
APPROVALS = Approvals()
auto_declines = [False]    # Auto mode is running: decline instead of waiting

_SKIP_MARK = "# blmcp-toolcode-skip-preflight"
# Tools whose code the model wrote (#347): the skip marker in it is never honoured,
# or a model -- or text planted in a scene -- could switch the checks off.
MODEL_CODE_TOOLS = frozenset(("execute_blender_code", "execute_blender_plan"))
_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("save the .blend file", re.compile(r"\bwm\.save_(?:as_)?mainfile\b")),
    ("open, reload or quit -- unsaved work is lost",
     re.compile(r"\bwm\.(?:open_mainfile|read_homefile|read_factory_settings|quit_blender|revert_mainfile)\b")),
    ("delete or move files",
     re.compile(r"\b(?:os\.(?:remove|unlink|rmdir|removedirs|rename|replace)|shutil\.(?:rmtree|move))\s*\(|"
                r"\.unlink\s*\(|\.rmdir\s*\(")),
    ("export files", re.compile(r"\bbpy\.ops\.(?:export_\w+|wm\.\w+_export)\b")),
    ("run another program", re.compile(r"\b(?:subprocess\.\w+|os\.system|os\.popen|os\.startfile)\s*\(")),
)


# Tools whose effect is risky whatever their code says (their toolcode skips the
# code scan above): asked about like a risky step in Strict and Permissive.
_TOOL_RISKS: dict[str, str] = {
    # Overwrites image pixels outside the turn's undo step (Tools 6, #309).
    "bake_textures": "overwrite the target images' pixels (Undo can't bring them back)",
}


def is_toolcode(code: str) -> bool:
    """Coworker's own tool-code: the skip marker is its first line.  Skips the risky-step
    check, autofix and preflight.  Code from :data:`MODEL_CODE_TOOLS` is :func:`untrusted` first."""
    return code.startswith(_SKIP_MARK + "\n")


def untrusted(code: str) -> str:
    """*code* the model wrote, with any skip marker made inert so every check runs (#347)."""
    return code.replace(_SKIP_MARK, "# (skip marker ignored: not Coworker's tool-code)")


def reason(code: str) -> str:
    """What makes *code* risky, in plain words ("" when it isn't)."""
    if not code or is_toolcode(code):
        return ""
    found = [what for what, rule in _RULES if rule.search(code)]
    return " and ".join(found)


def ask_kind(tool: str, code: str, approval: str, engine: str) -> tuple[str, str]:
    """What to ask before *code* runs, by the approval mode (#240): ``(kind, why)``, or
    ``("", "")`` to run it.  *kind* is what "Always" remembers: ``risky:<why>`` for a risky
    step, the tool's name for one that changes the scene (Ask, our own loop only -- an
    External Agent asks through its own permission card, a Harness client per tool itself)."""
    if approval == "AUTO":
        return "", ""
    why = reason(code) or _TOOL_RISKS.get(str(tool or ""), "")
    if why:
        return "risky:" + why, why
    if approval == "ASK" and engine in ("LOCAL_LLM", "REMOTE_API") and tool:
        from .engine import PLAN_SAFE_TOOLS
        if tool not in PLAN_SAFE_TOOLS:          # looking never asks
            return tool, ""
    return "", ""


def wrap(code: str, strict_json: bool, why: str, execute: Callable[[str, bool], Any], tool: str = "",
         source: str = "Coworker", kind: str = "", thread: str = "",
         ) -> tuple[dict[str, object], Callable[[], dict[str, object] | None] | None]:
    """``(response, check_fn)`` for the bridge: ask first, then run *code* with *execute*.
    *thread* (#357): the calling thread's tag; its card and "Allow this turn" are its own."""
    kind = kind or "risky:" + why
    if auto_declines[0]:
        if not why:                 # Auto mode runs what Ask would ask about; only risky steps wait
            result = execute(code, strict_json)
            return result.response, result.check_fn
        return {"status": "error", "message": "Auto mode doesn't {:s} without the user. Leave this step for "
                                              "them, or do it another way.".format(why)}, None
    if APPROVALS.allowed_always(kind, thread):
        result = execute(code, strict_json)
        return result.response, result.check_fn
    state: dict[str, Any] = {"answer": None, "inner": None}
    request = ApprovalRequest(tool=kind if why else tool, source=source, args={"code": code}, coworker_tool=True,
                              reason=why, thread=thread)
    APPROVALS.ask(request, lambda choice: state.__setitem__("answer", choice))

    def check() -> dict[str, object] | None:
        if state["inner"] is not None:          # allowed, and the code itself is deferred (a render)
            return state["inner"]()
        answer = state["answer"]
        if answer is None:
            return None
        if answer in ("deny", "cancel"):
            what = "it would {:s}".format(why) if why else "they said no to {:s}".format(tool or "this tool")
            return {"status": "error", "message": "The user declined this step ({:s}). Don't retry "
                                                  "it; ask them or do it another way.".format(what)}
        result = execute(code, strict_json)
        if result.check_fn is not None:
            state["inner"] = result.check_fn
            return None
        return result.response
    return {}, check
