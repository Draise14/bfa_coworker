# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Visual check for the GPU chat view (task "GPU 2").  Not shipped.

Builds a conversation from REAL data -- the OpenCode session recorded in
``tests/fixtures/acp/opencode_mcp_tool_call.jsonl``, replayed through
``acp_client.update_to_events`` and ``chat_state.ChatStateApplier`` -- plus a
local-style turn with markdown (heading, list, code, table), then draws it
with ``ui_gpu.chat_view`` in the Coworker editor (full) and the 3D View's
Coworker sidebar tab (compact).  Run in a GUI session::

    bforartists --factory-startup --python _misc/spikes/gpu_chat_view_demo.py -- <out_dir>
"""

__all__ = ()

import importlib
import json
import os
import sys
import types

import bpy  # pylint: disable=import-error

OUT_DIR = sys.argv[sys.argv.index("--") + 1] if "--" in sys.argv else os.getcwd()
os.makedirs(OUT_DIR, exist_ok=True)
_REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
_pkg = types.ModuleType("cwk")
_pkg.__path__ = [os.path.join(_REPO, "addon", "bfa_coworker")]
sys.modules["cwk"] = _pkg
engine = importlib.import_module("cwk.engine")
acp = importlib.import_module("cwk.acp_client")
chat_state = importlib.import_module("cwk.chat_state")
style_mod = importlib.import_module("cwk.ui_gpu.style")
layout = importlib.import_module("cwk.ui_gpu.layout")
render = importlib.import_module("cwk.ui_gpu.render")
chat_view = importlib.import_module("cwk.ui_gpu.chat_view")

TREE = "DEMO_CoworkerTree"


class _State:
    conversation_history: list = []
    streaming_text = reasoning_text = turn_phase = ui_status = error = ""
    is_thinking = False
    thinking_start_time = 0.0


def _conversation():
    state = _State()
    state.conversation_history = [{"role": "system", "content": "sys"}]
    applier = chat_state.ChatStateApplier(state)
    applier.apply(engine.AgentEvent("turn_start", "t1", {"text": "Look up primitive_cube_add in the API docs."}))
    with open(os.path.join(_REPO, "tests", "fixtures", "acp", "opencode_mcp_tool_call.jsonl"), encoding="utf-8") as fh:
        for line in fh:
            rec = json.loads(line)
            msg = rec["msg"]
            if rec["dir"] == "agent->client" and msg.get("method") == "session/update":
                for kind, data in acp.update_to_events(msg["params"]["update"]):
                    applier.apply(engine.AgentEvent(kind, "t1", data))
    applier.apply(engine.AgentEvent("turn_end", "t1", {"stop_reason": "end_turn"}))
    state.conversation_history += [
        {"role": "user", "content": "Make a low-poly campfire on a ground plane."},
        {"role": "tool", "name": "execute_blender_code", "content": "{\"status\": \"ok\", \"result\": {\"logs\": 5}}"},
        {"role": "assistant", "content": (
            "# Campfire ready\n\n## What I added\n\nI added a **ground plane**, five *slightly rotated* "
            "logs and a warm light.\n\n### Tune the light\n\nUse `light.energy` and the colour:\n\n"
            "```python\nlight = bpy.data.lights[\"Fire\"]\nlight.energy = 800\nlight.color = (1.0, 0.55, 0.2)\n```\n\n"
            "| Object | Count |\n|---|---|\n| Log | 5 |\n| Light | 1 |\n\n"
            "- Next: add embers\n- Or: render a preview with `render_viewport_to_path`"),
         "turn_seconds": 42},
        {"role": "user", "content": "Great. Now make the logs a bit darker."},
    ]
    state.is_thinking = True
    state.turn_phase = "thinking"
    state.streaming_text = "Darkening the **log material**: base colour from 0.35 to 0.18..."
    return state


STATE = _conversation()
VIEWS = {"full": chat_view.ChatView(), "compact": chat_view.ChatView()}


def _draw(kind):
    region = bpy.context.region
    style = style_mod.style_from_preferences(bpy.context.preferences)
    canvas = layout.Canvas(style, render.measure)
    width = region.width - (24 * style.ui_scale if kind == "compact" else 0)
    canvas.fill(layout.Rect(0, 0, width, region.height), style.back)
    VIEWS[kind].draw(canvas, layout.Rect(0, 0, width, region.height), STATE.conversation_history,
                     live=STATE, compact=(kind == "compact"))
    render.render(canvas.commands)


def _draw_center():
    if getattr(bpy.context.space_data, "tree_type", "") == TREE:
        _draw("full")


def _draw_sidebar():
    if getattr(bpy.context.region, "active_panel_category", "") == "Coworker":
        _draw("compact")


class DEMO_Tree(bpy.types.NodeTree):
    bl_idname = TREE
    bl_label = "Coworker"
    bl_icon = "GHOST_ENABLED"


class DEMO_PT_tab(bpy.types.Panel):
    bl_label = "Coworker"
    bl_idname = "DEMO_PT_tab"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Coworker"
    bl_options = {"HIDE_HEADER"}

    def draw(self, context):
        self.layout.label(text="")


def _areas():
    return list(bpy.context.window_manager.windows[-1].screen.areas)


def _shoot(name):
    win = bpy.context.window_manager.windows[-1]
    with bpy.context.temp_override(window=win, screen=win.screen):
        bpy.ops.screen.screenshot(filepath=os.path.join(OUT_DIR, name + ".png"))


def _redraw():
    for area in _areas():
        area.tag_redraw()


_STEP = {"n": 0}
RESULT = {}


def _tick():
    step = _STEP["n"]
    _STEP["n"] += 1
    try:
        if step == 0:
            for cls in (DEMO_Tree, DEMO_PT_tab):
                bpy.utils.register_class(cls)
            bpy.types.SpaceNodeEditor.draw_handler_add(_draw_center, (), "WINDOW", "POST_PIXEL")
            bpy.types.SpaceView3D.draw_handler_add(_draw_sidebar, (), "UI", "POST_PIXEL")
            with bpy.context.temp_override(window=bpy.context.window_manager.windows[0]):
                bpy.ops.wm.window_new_main()
            return 1.0
        if step == 1:
            areas = sorted(_areas(), key=lambda a: a.width * a.height, reverse=True)
            areas[0].ui_type = TREE
            areas[0].spaces.active.show_region_ui = False
            areas[0].spaces.active.show_region_toolbar = False
            areas[1].ui_type = "VIEW_3D"
            areas[1].spaces.active.show_region_ui = True
            return 0.8
        if step == 2:
            for area in _areas():
                for region in area.regions:
                    if area.type == "VIEW_3D" and region.type == "UI":
                        try:
                            region.active_panel_category = "Coworker"
                        except AttributeError:
                            _STEP["n"] = 2
                            return 0.5
            _redraw()
            return 0.8
        if step == 3:
            _shoot("1_full_and_compact")
            VIEWS["full"].expanded.update({"t0", "t1"})
            VIEWS["compact"].expanded.add("t1")
            _redraw()
            return 0.8
        if step == 4:
            _shoot("2_workshops_open")
            VIEWS["full"].scroll = 260.0
            _redraw()
            return 0.8
        if step == 5:
            _shoot("3_scrolled_jump_button")
            RESULT["full_content_height"] = round(VIEWS["full"].content_height)
            RESULT["cached_turn_heights"] = len(VIEWS["full"]._heights)
    except Exception:  # pylint: disable=broad-exception-caught
        import traceback
        RESULT.setdefault("errors", []).append(traceback.format_exc()[-900:])
    with open(os.path.join(OUT_DIR, "results.json"), "w", encoding="utf-8") as fh:
        json.dump(RESULT, fh, indent=2)
    bpy.ops.wm.quit_blender()
    return None


bpy.app.timers.register(_tick, first_interval=2.0, persistent=True)
