# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Composite tool: keyframe multiple properties on multiple objects in one call.

Sets keyframes on one or more objects across multiple frames, all in a
single ``execute_blender_code`` call.  Saves the LLM N round-trips
(one per object per frame per property).
"""

__all__ = (
    "register",
)

import json

from blmcp.tools_helpers.connection import send_code
from mcp.server.fastmcp import FastMCP  # pylint: disable=import-error,no-name-in-module
from mcp.types import ToolAnnotations  # pylint: disable=import-error,no-name-in-module


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        annotations=ToolAnnotations(
            title="Batch Keyframe Insert",
            destructiveHint=True,
        )
    )
    def batch_keyframe_insert(
        keyframes_json: str,
    ) -> dict[str, object]:
        """
        Insert keyframes on multiple objects across multiple frames.

        *keyframes_json* is a JSON string of the form::

            {
              "objects": [
                {
                  "name": "Cube",
                  "frames": [
                    {"frame": 1, "location": [0, 0, 0], "rotation": [0, 0, 0]},
                    {"frame": 50, "location": [5, 0, 0], "rotation": [0, 0, 1.57]}
                  ]
                },
                {
                  "name": "Sphere",
                  "frames": [
                    {"frame": 1, "location": [0, 2, 0], "scale": [1, 1, 1]},
                    {"frame": 30, "location": [0, 5, 0], "scale": [2, 2, 2]}
                  ]
                }
              ]
            }

        Each frame entry can include ``location``, ``rotation`` (euler radians),
        ``scale``, or custom ``data_path`` keyframes.
        Rotation is in radians, XYZ Euler.
        """
        try:
            data = json.loads(keyframes_json)
        except (json.JSONDecodeError, TypeError) as ex:
            return {"status": "error", "message": "Invalid JSON: {:s}".format(str(ex))}

        code = _build_keyframe_code(data)
        return send_code(code, strict_json=True)


def _build_keyframe_code(data: dict) -> str:
    """Generate Blender Python code for batch keyframe insertion."""
    lines = [
        "import bpy",
        "result = {'status': 'ok', 'keyframed': []}",
        "scene = bpy.context.scene",
        "",
    ]

    objects = data.get("objects", [])
    for obj_data in objects:
        name = obj_data.get("name", "")
        frames = obj_data.get("frames", [])

        lines.append("# Object: {:s}".format(name))
        lines.append("obj = bpy.data.objects.get('{:s}')".format(name))
        lines.append("if obj is None:")
        lines.append("    result['status'] = 'error'")
        lines.append("    result['message'] = 'Object {:s} not found'".format(name))
        lines.append("    result['keyframed'] = []")
        lines.append("    raise SystemExit(0)")

        for f in frames:
            frame = f.get("frame", 1)
            lines.append("")
            lines.append("scene.frame_set({:d})".format(frame))

            if "location" in f:
                loc = f["location"]
                lines.append("obj.location = ({:f}, {:f}, {:f})".format(*loc))
                lines.append("obj.keyframe_insert(data_path='location', frame={:d})".format(frame))

            if "rotation" in f:
                rot = f["rotation"]
                lines.append("obj.rotation_euler = ({:f}, {:f}, {:f})".format(*rot))
                lines.append("obj.keyframe_insert(data_path='rotation_euler', frame={:d})".format(frame))

            if "scale" in f:
                s = f["scale"]
                lines.append("obj.scale = ({:f}, {:f}, {:f})".format(*s))
                lines.append("obj.keyframe_insert(data_path='scale', frame={:d})".format(frame))

            if "data_paths" in f:
                for dp in f["data_paths"]:
                    path = dp.get("path", "")
                    value = dp.get("value", 0.0)
                    lines.append("obj.{:s} = {:f}".format(path, value))
                    lines.append("obj.keyframe_insert(data_path='{:s}', frame={:d})".format(path, frame))

            lines[-1] = lines[-1]  # no-op

        lines.append("result['keyframed'].append('{:s}')".format(name))
        lines.append("")

    lines.append("result['message'] = 'Keyframed {:d} objects'".format(len(objects)))
    return "\n".join(lines)