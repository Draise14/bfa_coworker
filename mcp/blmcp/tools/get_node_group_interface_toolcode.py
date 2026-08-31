# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tool-code for reading the interface of a node group.
"""

__all__ = (
    "Params",
    "Result",
    "main",
)

from typing import Any, NamedTuple


class Params(NamedTuple):
    group_name: str


class Result(NamedTuple):
    status: str
    group_name: str
    editor_type: str
    inputs: list[dict[str, Any]]
    outputs: list[dict[str, Any]]
    message: str


def main(params: Params) -> Result:
    """Return the interface of ``bpy.data.node_groups[params.group_name]``.

    Every input/output socket is reported with its socket type, default
    value, min/max range (when present) and description, plus the group's
    editor type so a caller knows which editor the group was built for.
    """
    import bpy  # pylint: disable=import-error

    ng = bpy.data.node_groups.get(params.group_name)
    if ng is None:
        available = ", ".join(sorted(bpy.data.node_groups.keys())[:20])
        return Result(
            status="error",
            group_name=params.group_name,
            editor_type="",
            inputs=[],
            outputs=[],
            message="Node group '{:s}' not found in bpy.data.node_groups. "
            "Load it first (load_asset_in_context). Available: {:s}".format(
                params.group_name, available or "none"),
        )

    editor_type = str(ng.type)  # GeometryNodeTree / ShaderNodeTree / CompositorNodeTree
    inputs, outputs = _interface_sockets(ng)
    return Result(
        status="ok",
        group_name=params.group_name,
        editor_type=editor_type,
        inputs=inputs,
        outputs=outputs,
        message="Interface of '{:s}' ({:d} inputs, {:d} outputs)".format(
            params.group_name, len(inputs), len(outputs)),
    )


def _socket_info(item: Any) -> dict[str, Any]:
    """Serialize one ``NodeTreeInterfaceSocket`` (or a legacy socket)."""
    info: dict[str, Any] = {
        "name": item.name,
    }
    try:
        info["socket_type"] = item.socket_type
    except AttributeError:
        info["socket_type"] = getattr(item, "type", str(type(item).__name__))
    info["type"] = _short_socket_type(info["socket_type"])
    try:
        info["default"] = _jsonable(item.default_value)
    except AttributeError:
        pass
    for attr in ("min_value", "max_value"):
        try:
            value = getattr(item, attr)
        except AttributeError:
            continue
        if value is not None:
            info[attr] = _jsonable(value)
    try:
        info["description"] = getattr(item, "description", "") or ""
    except AttributeError:
        pass
    return info


def _short_socket_type(socket_type: str) -> str:
    """Map Blender ``NodeSocketFloat``-style names to short types."""
    aliases = {
        "NodeSocketFloat": "FLOAT",
        "NodeSocketFloatAngle": "FLOAT",
        "NodeSocketFloatFactor": "FLOAT",
        "NodeSocketFloatUnsigned": "FLOAT",
        "NodeSocketInt": "INT",
        "NodeSocketBool": "BOOL",
        "NodeSocketVector": "VECTOR",
        "NodeSocketVectorXYZ": "VECTOR",
        "NodeSocketVectorTranslation": "VECTOR",
        "NodeSocketVectorEuler": "VECTOR",
        "NodeSocketColor": "RGBA",
        "NodeSocketString": "STRING",
        "NodeSocketShader": "SHADER",
        "NodeSocketGeometry": "GEOMETRY",
        "NodeSocketMaterial": "MATERIAL",
        "NodeSocketObject": "OBJECT",
        "NodeSocketCollection": "COLLECTION",
        "NodeSocketTexture": "TEXTURE",
        "NodeSocketImage": "IMAGE",
        "NodeSocketMenu": "MENU",
    }
    return aliases.get(socket_type, socket_type)


def _interface_sockets(ng: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (inputs, outputs) from a node tree using either API generation."""
    inputs: list[dict[str, Any]] = []
    outputs: list[dict[str, Any]] = []

    # Blender 4.x+: interface.items_tree (NodeTreeInterfaceSocket / Panels).
    interface = getattr(ng, "interface", None)
    if interface is not None and hasattr(interface, "items_tree"):
        for item in interface.items_tree:
            item_type = type(item).__name__
            if item_type != "NodeTreeInterfaceSocket":
                continue  # Skip panels; sockets nested in panels are reported flat.
            direction = getattr(item, "in_out", "")
            if direction == "INPUT":
                inputs.append(_socket_info(item))
            elif direction == "OUTPUT":
                outputs.append(_socket_info(item))
        if inputs or outputs:
            return inputs, outputs

    # Legacy: ng.inputs / ng.outputs.
    try:
        for sock in ng.inputs:
            inputs.append(_socket_info(sock))
    except AttributeError:
        pass
    try:
        for sock in ng.outputs:
            outputs.append(_socket_info(sock))
    except AttributeError:
        pass
    return inputs, outputs


def _jsonable(value: Any) -> Any:
    """Convert a socket default (float / color / vector) to a plain value."""
    if isinstance(value, (int, float, str, bool)) or value is None:
        return value
    try:
        return list(value)
    except TypeError:
        return repr(value)