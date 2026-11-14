# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tool-code for reading a node tree as compact build code (#480).

Read only: the target tree is never created or changed.  Defaults come from
new nodes in a temporary node group, which is removed before returning.
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
    tree_type: str = ""
    node_tree_name: str = ""
    material_name: str = ""
    object_name: str = ""
    modifier_name: str = ""
    nodes: Any = None
    include_groups: bool = True
    max_chars: int = 6000
    node_offset: int = 0


class Result(NamedTuple):
    status: str
    tree_name: str
    tree_type: str
    editor_name: str
    code: str
    node_count: int
    printed_nodes: int
    cut_nodes: int
    next_node_offset: int
    groups: list[str]
    groups_not_printed: list[str]
    message: str


def _error(params: Params, message: str) -> Result:
    return Result("error", params.node_tree_name or params.material_name, params.tree_type, "", "", 0, 0, 0, -1,
                  [], [], message)


def main(params: Params) -> Result:
    import bpy  # pylint: disable=import-error

    tree, kind, name, problem = resolve_target(
        bpy, params.tree_type, params.node_tree_name, params.material_name, params.object_name,
        params.modifier_name)
    if tree is None:
        return _error(params, problem)
    only = None
    if params.nodes:
        wanted = [params.nodes] if isinstance(params.nodes, str) else list(params.nodes)
        names = [n.name for n in tree.nodes]
        missing = [n for n in wanted if n not in names]
        if missing:
            return _error(params, "No node named {:s} in '{:s}'{:s}".format(
                ", ".join(repr(m) for m in missing), tree.name,
                " -- nearest: " + ", ".join(nearest_names(missing[0], names)) if names else ""))
        only = set(wanted)
    budget = max(int(params.max_chars or 0), 500)
    offset = max(int(params.node_offset or 0), 0)
    scratch = Scratch(bpy)
    try:
        spec = read_tree(bpy, tree, scratch, kind, name, only)
        head = print_head(spec) if offset == 0 else print_tree_header(spec)
        blocks = []
        groups, skipped = [], []
        used = len(head) + 1
        if params.include_groups and offset == 0 and only is None:
            for group in used_groups(tree):
                text = print_tree(read_tree(bpy, group, scratch))
                if used + len(text) + 1 > budget * 2 // 3:
                    skipped.append(group.name)
                    continue
                blocks.append(text)
                groups.append(group.name)
                used += len(text) + 1
        lines = []
        printed = 0
        nodes = spec.nodes[offset:]
        for node in nodes:
            line = print_node(node)
            if lines and used + len(line) + 1 > budget:
                break
            lines.append(line)
            used += len(line) + 1
            printed += 1
        cut = len(nodes) - printed
        main_text = head + ("\n" if head else "") + "\n".join(lines) + "\n"
        next_offset = offset + printed if cut else -1
        if cut:
            main_text += "# cut here: {:d} more nodes -- call again with node_offset={:d}\n".format(cut, next_offset)
        blocks.append(main_text)
    finally:
        scratch.close()
    code = "\n".join(blocks)
    tree_type = tree.bl_idname
    message = "'{:s}' ({:s}): {:d} nodes{:s}{:s}{:s}".format(
        name, EDITOR_NAMES.get(tree_type, tree_type), len(spec.nodes),
        ", {:d} groups".format(len(groups)) if groups else "",
        "; cut after {:d} nodes, {:d} more: call again with node_offset={:d}".format(
            printed, cut, next_offset) if cut else "",
        "; groups not printed (too long): {:s} -- read them with node_tree_name".format(
            ", ".join(skipped)) if skipped else "")
    return Result("ok", name, tree_type, EDITOR_NAMES.get(tree_type, tree_type), code, len(spec.nodes),
                  printed, cut, next_offset, groups, skipped, message)
