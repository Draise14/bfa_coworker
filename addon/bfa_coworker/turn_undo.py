# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
One Ctrl+Z per chat turn (#501): the rules, free of ``bpy``.

A chat turn that changed the scene is one step of Edit > Undo History, pushed
when the turn ends (:mod:`coworker_api`); a turn that changed nothing pushes
none.  Nothing pushes in between: a tool called from a turn leaves its own
step out (the turn's step covers it), and the loop keeps its save points as
snapshots, not undo steps.  While chats work side by side each finished write
call still gets its own step, named by its chat (#359).

* :func:`call_changes` -- may a tool call have changed the scene?  (A turn
  with no such call pushes no step.)
* :func:`script_only_reads` -- a script that only looks (prints, reads values).
* :func:`reload_allowed` -- may a failed script's partial changes be taken back
  by loading the newest undo step again (undo, then redo: no step is added)?
* :func:`revert_target` -- where Revert goes on the undo list (5.3+).
"""

from __future__ import annotations

__all__ = (
    "call_changes",
    "reload_allowed",
    "revert_target",
    "same_step",
    "script_only_reads",
)

import ast
from typing import Iterable

# Tools that are no scene change although they may do something: a helper chat's turn
# is its own, with its own step (#479).
_NO_SCENE_CHANGE = frozenset({"delegate_task"})

# Calls a script may make and still only read.
_READ_FUNCS = frozenset({
    "print", "len", "str", "repr", "int", "float", "bool", "round", "abs", "min", "max", "sum", "sorted",
    "list", "tuple", "dict", "set", "frozenset", "range", "enumerate", "zip", "isinstance", "issubclass",
    "hasattr", "getattr", "type", "format", "any", "all", "reversed", "map", "filter", "divmod", "ord", "chr",
})
_READ_METHODS = frozenset({
    "get", "keys", "values", "items", "find", "index", "count", "startswith", "endswith", "join", "split",
    "rsplit", "strip", "lstrip", "rstrip", "lower", "upper", "title", "format", "replace", "dumps",
    "to_tuple", "to_euler", "to_matrix", "to_quaternion", "to_translation", "to_scale", "to_2d", "to_3d",
    "to_4d", "to_3x3", "to_4x4", "normalized", "dot", "cross", "angle", "decompose",
})
# What a script may do to the lists / dicts it made itself.
_LOCAL_METHODS = frozenset({"append", "extend", "insert", "add", "update", "setdefault", "pop", "remove",
                            "clear", "sort", "discard", "copy"})
_CONTAINERS = (ast.List, ast.Dict, ast.Set, ast.ListComp, ast.DictComp, ast.SetComp, ast.Tuple)


def _local_containers(tree: ast.AST) -> set[str]:
    """The names the script binds to a list / dict / set it makes itself."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and (isinstance(node.value, _CONTAINERS) or (
                isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name)
                and node.value.func.id in {"list", "dict", "set"})):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
    return names


def _root_name(node: ast.AST) -> str:
    while isinstance(node, (ast.Attribute, ast.Subscript)):
        node = node.value
    return node.id if isinstance(node, ast.Name) else ""


def script_only_reads(code: str) -> bool:
    """*code* (a script for ``execute_blender_code``) only looks: it reads values, prints
    them and fills lists / dicts of its own -- no operator, no assignment to anything of
    Blender's, no call that could change it.  Unsure (or not Python): False."""
    try:
        tree = ast.parse(str(code or ""))
    except (SyntaxError, ValueError):
        return False
    local = _local_containers(tree)
    funcs = {n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Global, ast.Nonlocal, ast.ClassDef, ast.AsyncFunctionDef, ast.Await)):
            return False
        targets: list[ast.AST] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign, ast.For, ast.AsyncFor, ast.comprehension)):
            targets = [node.target]
        elif isinstance(node, ast.Delete):
            targets = list(node.targets)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            targets = [item.optional_vars for item in node.items if item.optional_vars is not None]
        for target in targets:
            for part in ast.walk(target):
                if isinstance(part, (ast.Attribute, ast.Subscript)) and _root_name(part) not in local:
                    return False        # ob.location.x = ..., bpy.data.objects["Cube"]["k"] = ...
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                if func.id not in _READ_FUNCS and func.id not in funcs:
                    return False
            elif isinstance(func, ast.Attribute):
                if func.attr in _READ_METHODS or func.attr.endswith("_get"):
                    continue            # objects.get("Cube"), ob.select_get(), evaluated_depsgraph_get()
                if func.attr in _LOCAL_METHODS and isinstance(func.value, ast.Name) and func.value.id in local:
                    continue            # names.append(ob.name)
                return False
            else:
                return False
    return True


def call_changes(tool: str, code: str = "", plan_safe: Iterable[str] = ()) -> bool:
    """May chat call *tool* (its bridge *code*) have changed the scene?  The tools that
    only look (*plan_safe*), a helper chat's start, and a script that only reads: no.
    Everything else, finished or failed: yes (a failed script may have changed half)."""
    tool = str(tool or "")
    if tool in set(plan_safe) or tool in _NO_SCENE_CHANGE:
        return False
    if tool == "execute_blender_code" and script_only_reads(code):
        return False
    return True


def reload_allowed(first_change: bool, undo_safe: bool, others_stepped: Iterable[str],
                   others_working: Iterable[str]) -> bool:
    """A failed script's partial changes may be taken back by loading the newest undo
    step again (undo, then redo; no step is added) only when that step still holds the
    scene from just before the script: the script was the turn's first change
    (*first_change*: nothing of this turn would go with it), you didn't change the scene
    meanwhile (*undo_safe*), and no other chat stepped since or is working now (their
    changes not yet on the undo list would go too).  Otherwise the loop's scoped cleanup
    alone: what the script made is removed, its edits to existing data stay."""
    return bool(first_change and undo_safe and not list(others_stepped) and not list(others_working))


def same_step(shown: str, pushed: str) -> bool:
    """Undo History shows the step *pushed* as *shown* (Blender keeps 63 bytes of a name)."""
    if shown == pushed:
        return True
    raw = pushed.encode("utf-8")
    return len(raw) > 63 and shown == raw[:63].decode("utf-8", "ignore")


def revert_target(count: int, names: list[str], active: int | None, newest: str) -> tuple[int, str]:
    """Where Revert jumps on the undo list (``wm.undo_stack``, 5.3+): ``(index, "")``, or
    ``(-1, why)`` when it must not.  *count*: how many steps to go back (the turn's own,
    or with Revert both every Coworker step since the turn began); *active*: the active
    step's index (None: the newest); *newest*: the name of Coworker's newest push, which
    must be the active, newest step -- else something else stepped since."""
    if count < 1:
        return -1, "There's no finished turn to revert."
    if not names:
        return -1, "Blender's undo list is empty, so there is nothing to revert."
    if active is None:
        active = len(names) - 1
    if active != len(names) - 1 or not same_step(names[active], newest):
        return -1, "The undo history changed after this turn, so use Edit > Undo History."
    target = active - count
    if target < 0:
        return -1, ("This turn made more steps than the undo history keeps (Preferences > System > "
                    "Undo Steps), so use Edit > Undo History.")
    return target, ""
