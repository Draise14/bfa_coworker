# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tool-code for building node trees from compact code (#480).

Nothing half-built is left behind: every block is first built into a
temporary copy (a new tree for ``create``, a copy of the existing tree for
``replace`` / ``update``).  Any error -- an unknown node type, setting, socket
or node -- stops there, the copies are removed and the scene is unchanged.
Only then is the same build run on the real trees (``create`` keeps the copy
under its final name), and one undo step is pushed.
"""

__all__ = (
    "Params",
    "Result",
    "main",
)

from typing import Any, NamedTuple

# @include_begin: _node_code_format.py
from blmcp.tools._node_code_format import *  # noqa: F401,F403  pylint: disable=wildcard-import,unused-wildcard-import
# @include_end
# @include_begin: _node_code_bpy.py
from blmcp.tools._node_code_bpy import *  # noqa: F401,F403  pylint: disable=wildcard-import,unused-wildcard-import
# @include_end


class Params(NamedTuple):
    code: str = ""
    mode: str = "create"
    dry_run: bool = False
    tree_name: str = ""
    layout: str = ""


class Result(NamedTuple):
    status: str
    mode: str
    tree_name: str
    tree_type: str
    changes: list[str]
    not_set: list[str]
    errors: list[str]
    built: list[str]
    message: str


_MODES = ("create", "replace", "update")


def _result(params: Params, status: str, message: str, tree_name: str = "", tree_type: str = "",
            changes: Any = None, not_set: Any = None, errors: Any = None, built: Any = None) -> Result:
    return Result(status, (params.mode or "create").lower(), tree_name, tree_type, list(changes or []),
                  list(not_set or []), list(errors or []), list(built or []), message)


class _Step:
    """One block of the text and what happens to it."""

    def __init__(self, spec, name, action, existing, holder_kind):
        self.spec = spec
        self.name = name              # final datablock name
        self.action = action          # create / replace / update / reuse
        self.existing = existing      # the existing material or node group (None for create)
        self.kind = holder_kind       # "material" or "tree"
        self.holder = None            # phase 1 datablock (new or copy)
        self.tree = None              # its node tree


def _tree_of(holder, kind):
    return holder.node_tree if kind == "material" else holder


def _plan(bpy, code, params, mode, scratch, errors):
    steps = []
    seen = set()
    count = len(code.trees)
    for i, spec in enumerate(code.trees):
        is_main = i == count - 1
        if not spec.kind:
            if not is_main or count > 1:
                errors.append("line {:d}: this block needs a `tree \"Name\" (Type)` or `material \"Name\"` "
                              "header".format(spec.line))
                continue
            if mode == "create" or not params.tree_name:
                errors.append("The text has no `tree` / `material` header: add one (`tree \"Name\" "
                              "(GeometryNodeTree)`), or give tree_name with mode='update' / 'replace'")
                continue
            existing = bpy.data.node_groups.get(params.tree_name)
            kind = "tree"
            if existing is None and bpy.data.materials.get(params.tree_name) is not None:
                existing, kind = bpy.data.materials[params.tree_name], "material"
            if existing is None:
                errors.append("No node group or material named {!r}{:s}".format(params.tree_name, choices_text(
                    params.tree_name, user_names(bpy.data.node_groups) + user_names(bpy.data.materials))))
                continue
            spec.kind = kind
            spec.name = existing.name
            spec.tree_type = _tree_of(existing, kind).bl_idname
            steps.append(_Step(spec, existing.name, mode, existing, kind))
            continue
        name = params.tree_name if is_main and params.tree_name else spec.name
        if spec.kind == "tree" and spec.tree_type not in EDITOR_NAMES:
            errors.append("line {:d}: unknown tree type '{:s}'{:s}".format(
                spec.line, spec.tree_type, choices_text(spec.tree_type, list(EDITOR_NAMES))))
            continue
        if (spec.kind, name) in seen:
            errors.append("line {:d}: {:s} {!r} is in the text twice".format(spec.line, spec.kind, name))
            continue
        seen.add((spec.kind, name))
        coll = bpy.data.materials if spec.kind == "material" else bpy.data.node_groups
        existing = coll.get(name)
        if existing is not None and spec.kind == "tree" and existing.bl_idname != spec.tree_type:
            errors.append("line {:d}: '{:s}' exists and is a {:s}, not a {:s}".format(
                spec.line, name, existing.bl_idname, spec.tree_type))
            continue
        if is_main:
            if mode == "create" and existing is not None:
                errors.append("line {:d}: a {:s} named {!r} already exists: use mode='replace' to rebuild it "
                              "in place, mode='update' to change only the nodes in the text, or another name "
                              "(tree_name=...)".format(spec.line, "material" if spec.kind == "material"
                                                        else "node group", name))
                continue
            if mode != "create" and existing is None:
                errors.append("line {:d}: there is no {:s} named {!r} to {:s}: use mode='create'{:s}".format(
                    spec.line, spec.kind if spec.kind == "material" else "node group", name, mode,
                    choices_text(name, user_names(coll))))
                continue
            steps.append(_Step(spec, name, mode if existing is not None else "create", existing, spec.kind))
            continue
        if existing is None:
            steps.append(_Step(spec, name, "create", None, spec.kind))
            continue
        if existing.library is not None:
            steps.append(_Step(spec, name, "reuse", existing, spec.kind))
            continue
        same = print_tree(read_tree(bpy, _tree_of(existing, spec.kind), scratch, spec.kind, name)) == print_tree(spec)
        if same:
            steps.append(_Step(spec, name, "reuse", existing, spec.kind))
        elif mode == "create":
            errors.append("line {:d}: node group {!r} exists and differs from this block: rename the block "
                          "(and its uses), remove the block to use the existing group, or use mode='replace' "
                          "to rebuild it in place".format(spec.line, name))
        else:
            steps.append(_Step(spec, name, "replace", existing, spec.kind))
    return steps


def _new_holder(bpy, step):
    import uuid
    temp = ".coworker_build_" + uuid.uuid4().hex[:8]
    if step.action == "create":
        if step.kind == "material":
            holder = bpy.data.materials.new(temp)
            if holder.node_tree is None and hasattr(holder, "use_nodes"):
                holder.use_nodes = True
        else:
            holder = bpy.data.node_groups.new(temp, step.spec.tree_type)
    else:
        holder = step.existing.copy()
        holder.name = temp
    return holder


def _remove(bpy, holder, kind):
    try:
        if kind == "material":
            bpy.data.materials.remove(holder)
        else:
            bpy.data.node_groups.remove(holder)
    except Exception:  # pylint: disable=broad-except
        pass


def _undo_push(bpy, message):
    wm = bpy.context.window_manager
    window = bpy.context.window or (wm.windows[0] if wm.windows else None)
    if window is None:
        return False
    try:
        with bpy.context.temp_override(window=window):
            bpy.ops.ed.undo_push(message=message)
        return True
    except Exception:  # pylint: disable=broad-except
        return False


def main(params: Params) -> Result:
    import bpy  # pylint: disable=import-error

    mode = (params.mode or "create").lower()
    if mode not in _MODES:
        return _result(params, "error", "Unknown mode {!r}: use create, replace or update".format(params.mode),
                       errors=["mode must be create, replace or update"])
    try:
        code = parse_code(params.code or "")
    except CodeError as ex:
        return _result(params, "error", "The text has {:d} error(s); nothing was built.".format(len(ex.errors)),
                       errors=ex.errors)
    if not code.trees or not any(t.nodes or t.interface or t.deletes or t.props for t in code.trees):
        return _result(params, "error", "The text has no nodes.", errors=["empty text"])
    layout = (params.layout or "").lower()
    scratch = Scratch(bpy)
    holders = []
    try:
        errors = []
        steps = _plan(bpy, code, params, mode, scratch, errors)
        if errors:
            return _result(params, "error", "{:d} problem(s); nothing was built.".format(len(errors)), errors=errors)
        main_step = steps[-1]
        tree_type = main_step.spec.tree_type
        # Phase 1: build every block into a temporary datablock.
        report = BuildReport()
        groups = {}
        for step in steps:
            if step.action == "reuse":
                groups[step.spec.name] = _tree_of(step.existing, step.kind)
                continue
            step.holder = _new_holder(bpy, step)
            holders.append(step)
            step.tree = _tree_of(step.holder, step.kind)
            groups[step.spec.name] = step.tree
            step_layout = layout or ("auto" if step.action == "create" else "keep")
            build_tree(bpy, step.spec, step.tree, step.action, BuildCtx(bpy, scratch, report, groups), step_layout)
        if report.errors:
            return _result(params, "error", "{:d} error(s); nothing was built (the scene is unchanged).".format(
                len(report.errors)), main_step.name, tree_type, errors=report.sorted_errors(), not_set=report.not_set)
        changes = []
        # The phase 1 trees use the temporary groups: list them under their final names.
        temp_names = {step.holder.name: step.name for step in steps if step.holder is not None}
        for step in steps:
            if step.action == "reuse":
                changes.append("use the existing group {!r} as is".format(step.name))
                continue
            old = read_tree(bpy, _tree_of(step.existing, step.kind), scratch, step.kind, step.name) \
                if step.existing is not None else None
            new = read_tree(bpy, step.tree, scratch, step.kind, step.name)
            for node in new.nodes:
                node.args = [(k, temp_names.get(v, v) if k == "node_tree" and isinstance(v, str) else v, q)
                             for k, v, q in node.args]
            prefix = "{:s}: ".format(step.name) if len(steps) > 1 else ""
            changes.extend(prefix + c for c in diff_trees(old, new))
            if old is not None and not diff_trees(old, new):
                changes.append(prefix + "no changes")
        if params.dry_run:
            return _result(params, "dry_run", "Dry run: {:d} change(s); nothing was changed.".format(len(changes)),
                           main_step.name, tree_type, changes=changes, not_set=report.not_set)
        # Phase 2: the real trees.
        real = {}
        late = BuildReport()
        built = []
        for step in steps:
            if step.action == "reuse":
                real[step.spec.name] = _tree_of(step.existing, step.kind)
                continue
            if step.action == "create":
                step.holder.name = step.name
                holders.remove(step)
                real[step.spec.name] = step.tree
                built.append(step.holder.name)
                continue
            target = _tree_of(step.existing, step.kind)
            step_layout = layout or "keep"
            build_tree(bpy, step.spec, target, step.action, BuildCtx(bpy, scratch, late, real), step_layout)
            real[step.spec.name] = target
            built.append(step.name)
            if step.kind == "tree":
                try:
                    step.holder.user_remap(step.existing)
                except Exception:  # pylint: disable=broad-except
                    pass
        for step in steps:
            if step.action == "create" and step.kind == "tree" and step.holder.users == 0:
                step.holder.use_fake_user = True
        if late.errors:
            report.not_set.extend("not applied: " + e for e in late.errors)
        pushed = _undo_push(bpy, "Build node tree '{:s}'".format(main_step.name))
        message = "{:s} {!r} ({:s}): {:d} change(s){:s}{:s}.".format(
            {"create": "Created", "replace": "Rebuilt", "update": "Updated"}[mode], main_step.name,
            EDITOR_NAMES.get(tree_type, tree_type), len(changes),
            "; {:d} value(s) not set (see not_set)".format(len(report.not_set)) if report.not_set else "",
            "; one undo step" if pushed else "")
        return _result(params, "ok", message, main_step.name, tree_type, changes=changes,
                       not_set=report.not_set, built=built)
    finally:
        for step in reversed(holders):
            if step.holder is not None:
                _remove(bpy, step.holder, step.kind)
        scratch.close()
