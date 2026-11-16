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
export, run another program, write outside the project folder (the .blend's
folder; the temp folder is fine) or connect to the internet (those two are
read from the syntax tree, :mod:`code_guard`, #347), the chat asks first with
the approval card (:mod:`approvals`).  *Allow once* runs it, *Always* lets that kind of step
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
    "card_args",
    "is_toolcode",
    "reason",
    "untrusted",
    "wrap",
)

import ast
import functools
import re
from typing import Any, Callable

from .answer_envelope import answer_of, answers_in_full
from .approvals import ApprovalRequest, Approvals

# The open "may I?" questions about risky steps (the chat shows them like the agent's).
APPROVALS = Approvals()
auto_declines = [False]    # Auto mode is running: decline instead of waiting
auto_threads: set[str] = set()   # Threads 2b (#417): the threads (tags) whose Auto mode runs


def auto_on(thread: str = "") -> bool:
    """Auto mode runs for *thread* (its tag): nobody is there to answer its cards."""
    return auto_declines[0] or thread in auto_threads

_SKIP_MARK = "# blmcp-toolcode-skip-preflight"
# Tools whose code the model wrote (#347): the skip marker in it is never honoured,
# or a model -- or text planted in a scene -- could switch the checks off.
MODEL_CODE_TOOLS = frozenset(("execute_blender_code", "execute_blender_plan"))

# Deleting or moving files, and running another program: read from the text (so code inside a
# string that is run later counts too), plus the same calls through an import alias or a plain
# assignment (#549: ``import os as o``, ``from shutil import rmtree``, ``rm = os.remove``).
_FILE_FUNCS = frozenset(("os.remove", "os.unlink", "os.rmdir", "os.removedirs", "os.rename", "os.replace",
                         "shutil.rmtree", "shutil.move"))
_PROGRAM_FUNCS = frozenset(("os.system", "os.popen", "os.startfile"))     # and every ``subprocess.<name>``
_FILE_CALL = re.compile(r"\b(?:os\.(?:remove|unlink|rmdir|removedirs|rename|replace)|shutil\.(?:rmtree|move))\s*\(|"
                        r"\.rmdir\s*\(")
_PROGRAM_CALL = re.compile(r"\b(?:subprocess\.\w+|os\.system|os\.popen|os\.startfile)\s*\(")
_UNLINK = re.compile(r"\.unlink\s*\(")
# Blender's own unlinks touch nothing on disk (#549): an object or a child collection out of a
# collection (``coll.objects.unlink(ob)``, ``scene.collection.children.unlink(c)``) and an
# operator (``bpy.ops.text.unlink()``).  Every other ``.unlink(`` -- ``Path(p).unlink()``, a
# variable holding a path, or one that can't be told -- still asks.
_BLENDER_UNLINK = re.compile(r"(?:\.objects|\.children|\bops\.\w+)\s*$")


def _dotted(node: ast.AST) -> str:
    """``a.b.c`` for a name / attribute chain, "" for anything else."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return ""
    parts.append(node.id)
    return ".".join(reversed(parts))


@functools.lru_cache(maxsize=8)
def _called(code: str) -> frozenset[str]:
    """The full names of the functions *code* calls, import aliases and plain assignments
    followed (``import os as o`` / ``o.remove(p)`` -> ``os.remove``).  Empty when it doesn't
    parse: it can't run then, and the text rules still see it."""
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return frozenset()
    names: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update((alias.asname, alias.name) for alias in node.names if alias.asname)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names.update((alias.asname or alias.name, node.module + "." + alias.name) for alias in node.names)

    def full(node: ast.AST) -> str:
        root, dot, rest = _dotted(node).partition(".")
        return names.get(root, root) + dot + rest if root else ""

    for node in sorted((n for n in ast.walk(tree) if isinstance(n, ast.Assign)),
                       key=lambda n: (n.lineno, n.col_offset)):
        value = full(node.value)
        for target in node.targets:
            if value and isinstance(target, ast.Name):
                names[target.id] = value
    return frozenset(full(node.func) for node in ast.walk(tree) if isinstance(node, ast.Call)) - {""}


def _deletes_files(code: str) -> bool:
    if _FILE_CALL.search(code) or _called(code) & _FILE_FUNCS:
        return True
    return any(not _BLENDER_UNLINK.search(code[max(0, hit.start() - 200):hit.start()])
               for hit in _UNLINK.finditer(code))


def _runs_program(code: str) -> bool:
    return bool(_PROGRAM_CALL.search(code)) or any(
        name in _PROGRAM_FUNCS or name.startswith("subprocess.") for name in _called(code))


_RULES: tuple[tuple[str, Callable[[str], Any]], ...] = (
    ("save the .blend file", re.compile(r"\bwm\.save_(?:as_)?mainfile\b").search),
    ("open, reload or quit -- unsaved work is lost",
     re.compile(r"\bwm\.(?:open_mainfile|read_homefile|read_factory_settings|quit_blender|revert_mainfile)\b").search),
    ("delete or move files", _deletes_files),
    ("export files", re.compile(r"\bbpy\.ops\.(?:export_\w+|wm\.\w+_export)\b").search),
    ("run another program", _runs_program),
)


# Tools whose effect is risky whatever their code says (their toolcode skips the
# code scan above): asked about like a risky step in Strict and Permissive.
_TOOL_RISKS: dict[str, str] = {
    # Overwrites image pixels outside the turn's undo step (Tools 6, #309).
    "bake_textures": "overwrite the target images' pixels (Undo can't bring them back)",
}
# Tools that change nothing in the scene themselves and so never ask in Ask mode: a helper
# chat (#479) asks for its own steps in its own tab.
_NEVER_ASK = frozenset(("delegate_task",))


def is_toolcode(code: str) -> bool:
    """Coworker's own tool-code: the skip marker is its first line.  Skips the risky-step
    check, autofix and preflight.  Code from :data:`MODEL_CODE_TOOLS` is :func:`untrusted` first."""
    return code.startswith(_SKIP_MARK + "\n")


def untrusted(code: str) -> str:
    """*code* the model wrote, with any skip marker made inert so every check runs (#347)."""
    return code.replace(_SKIP_MARK, "# (skip marker ignored: not Coworker's tool-code)")


def _blend_path() -> str:
    """The open .blend's path ("" when it isn't saved, or outside Blender)."""
    try:
        import bpy  # pylint: disable=import-error
        return str(bpy.data.filepath or "")
    except Exception:  # pylint: disable=broad-exception-caught
        return ""


def reason(code: str, blend_path: str | None = None) -> str:
    """What makes *code* risky, in plain words ("" when it isn't).  *blend_path*: the open
    .blend, whose folder is the project folder (None: ask Blender; "" = not saved)."""
    if not code or is_toolcode(code):
        return ""
    found = [what for what, rule in _RULES if rule(code)]
    # Security 3 (#347): writes outside the project folder, and the internet (syntax tree).
    from . import code_guard
    try:
        found += code_guard.findings(code, _blend_path() if blend_path is None else blend_path)
    except Exception as ex:  # pylint: disable=broad-exception-caught
        print("[Coworker] code guard: couldn't check this code ({:s})".format(type(ex).__name__))
    return " and ".join(found)


def ask_kind(tool: str, code: str, approval: str, engine: str, blend_path: str | None = None) -> tuple[str, str]:
    """What to ask before *code* runs, by the approval mode (#240): ``(kind, why)``, or
    ``("", "")`` to run it.  *kind* is what "Always" remembers: ``risky:<why>`` for a risky
    step, the tool's name for one that changes the scene (Ask, our own loop only -- an
    External Agent asks through its own permission card, a Harness client per tool itself)."""
    if approval == "AUTO":
        return "", ""
    why = reason(code, blend_path) or _TOOL_RISKS.get(str(tool or ""), "")
    if why:
        return "risky:" + why, why
    if approval == "ASK" and engine in ("LOCAL_LLM", "REMOTE_API") and tool:
        from .engine import PLAN_SAFE_TOOLS
        if tool not in PLAN_SAFE_TOOLS and tool not in _NEVER_ASK:     # looking never asks
            return tool, ""
    return "", ""


def card_args(tool: str, code: str, inputs: Any = None) -> Any:
    """What the approval card shows for a call (#500): the tool's own input (the bridge
    request's ``args``) when what runs is Coworker's own tool-code; else the code itself --
    code the model wrote (:data:`MODEL_CODE_TOOLS`), a client that sent no tool name, or
    code that isn't tool-code whatever tool name it came with."""
    if not tool or tool in MODEL_CODE_TOOLS or not inputs or not is_toolcode(code):
        return {"code": code}
    return inputs


def wrap(code: str, strict_json: bool, why: str, execute: Callable[[str, bool], Any], tool: str = "",
         source: str = "Coworker", kind: str = "", thread: str = "", inputs: Any = None,
         ) -> tuple[dict[str, object], Callable[[], dict[str, object] | None] | None]:
    """``(response, check_fn)`` for the bridge: ask first, then run *code* with *execute*.
    *thread* (#357): the calling thread's tag; its card and "Allow this turn" are its own.
    *inputs* (#500): the tool's own arguments, for the card (else it shows *code*)."""
    kind = kind or "risky:" + why
    if auto_on(thread):
        if not why:                 # Auto mode runs what Ask would ask about; only risky steps wait
            result = execute(code, strict_json)
            return result.response, result.check_fn
        return {"status": "error", "message": "Auto mode doesn't {:s} without the user. Leave this step for "
                                              "them, or do it another way.".format(why)}, None
    if APPROVALS.allowed_always(kind, thread):
        result = execute(code, strict_json)
        return result.response, result.check_fn
    state: dict[str, Any] = {"answer": None, "inner": None, "extra": None}
    request = ApprovalRequest(tool=kind if why else tool, source=source, args=card_args(tool, code, inputs),
                              coworker_tool=True, reason=why, thread=thread, name=tool)
    APPROVALS.ask(request, lambda choice: state.__setitem__("answer", choice))

    # #487: the card's answer is the call's whole answer, envelope included (one envelope).
    @answers_in_full
    def check() -> dict[str, object] | None:
        if state["inner"] is not None:          # allowed, and the code itself is deferred (a render)
            return answer_of(state["inner"], state["inner"](), state["extra"])
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
            state["extra"] = {k: result.response.get(k) for k in ("stdout", "stderr")}
            return None
        return result.response
    return {}, check
