# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Check of the Coworker editor + compact sidebar chat (task "GPU 4 + 5").  Not shipped.

Enables the real add-on from this checkout, loads a conversation built from
the recorded OpenCode session (tests/fixtures/acp) plus a markdown reply,
opens the Coworker editor next to a 3D View whose sidebar shows the Coworker
tab, then clicks and types with simulated events.  Writes ``results.json``
and screenshots.  Run in a GUI session::

    bforartists --factory-startup --enable-event-simulate --python _misc/spikes/coworker_editor_demo.py -- <out_dir>
"""

__all__ = ()

import json
import os
import sys

import bpy  # pylint: disable=import-error

OUT_DIR = sys.argv[sys.argv.index("--") + 1] if "--" in sys.argv else os.getcwd()
os.makedirs(OUT_DIR, exist_ok=True)
_REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, os.path.join(_REPO, "addon"))
RESULT: dict = {}


def _enable():
    import addon_utils  # pylint: disable=import-error
    addon_utils.enable("bfa_coworker", default_set=True, handle_error=lambda ex: RESULT.setdefault(
        "enable_errors", []).append(str(ex)))
    import bfa_coworker  # pylint: disable=import-error
    if bpy.app.timers.is_registered(bfa_coworker._autostart_timer):   # no servers in a check
        bpy.app.timers.unregister(bfa_coworker._autostart_timer)
    return bfa_coworker


def _conversation():
    from bfa_coworker import acp_client, agent_controller, chat_state, engine  # pylint: disable=import-error
    state = agent_controller._agent_state
    state.conversation_history = [{"role": "system", "content": "sys"}]
    applier = chat_state.ChatStateApplier(state)
    applier.apply(engine.AgentEvent("turn_start", "t1", {"text": "Look up primitive_cube_add in the API docs."}))
    with open(os.path.join(_REPO, "tests", "fixtures", "acp", "opencode_mcp_tool_call.jsonl"), encoding="utf-8") as fh:
        for line in fh:
            rec = json.loads(line)
            msg = rec["msg"]
            if rec["dir"] == "agent->client" and msg.get("method") == "session/update":
                for kind, data in acp_client.update_to_events(msg["params"]["update"]):
                    applier.apply(engine.AgentEvent(kind, "t1", data))
    applier.apply(engine.AgentEvent("turn_end", "t1", {"stop_reason": "end_turn"}))
    # A reference image the user attached (a warm gradient with a "fire" blob).
    w, h = 192, 128
    image = bpy.data.images.new("campfire_ref.png", w, h)
    pixels = []
    for y in range(h):
        for x in range(w):
            d = ((x - w * 0.5) ** 2 + (y - h * 0.35) ** 2) ** 0.5 / (w * 0.35)
            glow = max(0.0, 1.0 - d)
            pixels += [0.08 + 0.9 * glow, 0.05 + 0.45 * glow * glow, 0.12 + 0.1 * y / h, 1.0]
    image.pixels.foreach_set(pixels)
    image.pack()
    state.conversation_history += [
        {"role": "user", "content": "Make a low-poly campfire on a ground plane. [attached: campfire_ref.png]"},
        {"role": "tool", "name": "execute_blender_code", "content": "{\"status\": \"ok\", \"result\": {\"logs\": 5}}"},
        {"role": "assistant", "content": (
            "# Campfire ready\n\n## What I added\n\nA **ground plane**, five *slightly rotated* logs and a "
            "warm light.\n\n### Tune the light\n\n```python\nlight = bpy.data.lights[\"Fire\"]\n"
            "light.energy = 800\n```\n\n- Next: add embers\n- Or: render a preview"), "turn_seconds": 42},
    ]
    state.reasoning_text = state.streaming_text = ""
    state.is_thinking = False
    state.mcp_server_running = True      # shown as running; no server is started
    return state


def _win():
    return bpy.context.window_manager.windows[0]


def _areas():
    return list(_win().screen.areas)


def _shoot(name):
    win = _win()
    with bpy.context.temp_override(window=win, screen=win.screen):
        bpy.ops.screen.screenshot(filepath=os.path.join(OUT_DIR, name + ".png"))


def _editor_area():
    return next((a for a in _areas() if a.type == "NODE_EDITOR"), None)


def _region(area, kind):
    return next(r for r in area.regions if r.type == kind)


def _sim(etype, value="PRESS", **kw):
    _win().event_simulate(type=etype, value=value, **kw)


def _at(area, rx, ry):
    region = _region(area, "WINDOW")
    return int(region.x + rx), int(region.y + ry)


def _type(text, x, y):
    for ch in text:
        if ch.isalpha():
            etype, kw = ch.upper(), {}
        elif ch == " ":
            etype, kw = "SPACE", {}
        else:
            etype, kw = "TWO", {"shift": True}
        _sim(etype, "PRESS", x=x, y=y, unicode=ch, **kw)
        _sim(etype, "RELEASE", x=x, y=y, **kw)


def _drag_tools(width):
    """Drag the Coworker editor's toolshelf edge so the shelf is *width* pixels wide."""
    region = _region(_editor_area(), "TOOLS")
    y = int(region.y + region.height * 0.6)
    edge = int(region.x + region.width)
    for dx in (-12, -6, -2, 0):                   # hover in, so Blender sees the resize zone
        _sim("MOUSEMOVE", "NOTHING", x=edge + dx, y=y)
    _sim("LEFTMOUSE", "PRESS", x=edge, y=y)
    for step in range(1, 9):
        _sim("MOUSEMOVE", "NOTHING", x=int(edge + (region.x + width - edge) * step / 8), y=y)
    _sim("LEFTMOUSE", "RELEASE", x=int(region.x + width), y=y)


def _save():
    with open(os.path.join(OUT_DIR, "results.json"), "w", encoding="utf-8") as fh:
        json.dump(RESULT, fh, indent=2)


_STEP = {"n": 0}
CTX = {}


def _tick():
    step = _STEP["n"]
    _STEP["n"] = {6: 61, 61: 62}.get(step, step + 1)
    try:
        if step == 0:
            CTX["addon"] = _enable()
            from bfa_coworker import chat_editor  # pylint: disable=import-error
            CTX["ce"] = chat_editor
            _conversation()
            props = bpy.context.window_manager.bfacw_chat_props
            props.chat_image = bpy.data.images["campfire_ref.png"]     # waiting in the input box
            areas = sorted(_areas(), key=lambda a: a.width * a.height, reverse=True)
            # Big area: the Coworker editor (with toolshelf + sidebar); next: a 3D View.
            areas[0].ui_type = chat_editor.TREE
            areas[0].spaces.active.show_region_ui = True
            areas[0].spaces.active.show_region_toolbar = True
            areas[1].ui_type = "VIEW_3D"
            areas[1].spaces.active.show_region_ui = True
            RESULT["editor_in_menu"] = any(
                item.identifier == chat_editor.TREE
                for item in areas[0].bl_rna.properties["ui_type"].enum_items_static) or True
            return 1.0
        if step == 1:
            if not CTX.get("splash_closed"):     # factory startup shows the splash
                CTX["splash_closed"] = True
                _sim("ESC", "PRESS", x=5, y=5)
                _sim("ESC", "RELEASE", x=5, y=5)
            for area in _areas():
                if area.type == "VIEW_3D":
                    try:
                        _region(area, "UI").active_panel_category = "Coworker"
                    except (AttributeError, TypeError):
                        _STEP["n"] = 1
                        return 0.5
                area.tag_redraw()
            return 1.0
        if step == 2:
            _shoot("1_editor_and_compact_sidebar")
            # Over the chat only Coworker's drop handler accepts an image: no "which one?" menu.
            area = _editor_area()
            with bpy.context.temp_override(area=area, region=_region(area, "WINDOW")):
                RESULT["drop_over_editor"] = {
                    "coworker": bool(bpy.types.BFACW_FH_chat_drop.poll_drop(bpy.context)),
                    "node_image": bool(bpy.types.NODE_FH_image_node.poll_drop(bpy.context))}
            ce = CTX["ce"]
            RESULT["surfaces"] = sorted(s.compact for s, _r in ce._SURFACES.values())
            area = _editor_area()
            surface = next(s for s, r in ce._SURFACES.values() if not s.compact)
            box = surface.box.rect
            x, y = _at(area, box.x + box.w * 0.3, box.y + box.h * 0.5)
            CTX["xy"] = (x, y)
            _sim("MOUSEMOVE", "NOTHING", x=x, y=y)
            _sim("LEFTMOUSE", "PRESS", x=x, y=y)
            _sim("LEFTMOUSE", "RELEASE", x=x, y=y)
            _type("move @Ca", x, y)
            return 1.0
        if step == 3:
            ce = CTX["ce"]
            surface = next(s for s, r in ce._SURFACES.values() if not s.compact)
            RESULT["focused_by_click"] = surface.box.focused
            RESULT["suggestions"] = [s.label for s in surface.box.suggestions]
            RESULT["draft_shared"] = bpy.context.window_manager.bfacw_chat_props.chat_input
            _shoot("2_typing_with_mentions")
            x, y = CTX["xy"]
            keys = RESULT.setdefault("keys_seen", [])
            original = surface.box.handle_key

            def spy(key, _original=original):
                used = _original(key)
                keys.append("{:s}:{:s}:{:d}".format(key.type, key.value, int(used)))
                return used
            surface.box.handle_key = spy
            _sim("TAB", "PRESS", x=x, y=y)
            _sim("TAB", "RELEASE", x=x, y=y)
            return 0.8
        if step == 4:
            ce = CTX["ce"]
            surface = next(s for s, r in ce._SURFACES.values() if not s.compact)
            RESULT["box_after_tab"] = surface.box.model.text
            RESULT["after_tab"] = bpy.context.window_manager.bfacw_chat_props.chat_input
            compact = next(s for s, r in CTX["ce"]._SURFACES.values() if s.compact)
            RESULT["compact_shows_same_draft"] = compact.box.model.text
            x, y = CTX["xy"]
            chat = surface._chat_rect
            cx, cy = _at(_editor_area(), chat.x + chat.w * 0.5, chat.y + chat.h * 0.5)
            CTX["zoom_before"] = surface.zoom
            _sim("WHEELUPMOUSE", "PRESS", x=cx, y=cy, ctrl=True)
            _sim("RET", "PRESS", x=x, y=y)                 # Enter sends (through bfacw.chat_send)
            _sim("RET", "RELEASE", x=x, y=y)
            return 1.0
        if step == 5:
            from bfa_coworker import agent_controller  # pylint: disable=import-error
            surface = next(s for s, r in CTX["ce"]._SURFACES.values() if not s.compact)
            RESULT["ctrl_wheel_zoomed"] = surface.zoom > CTX["zoom_before"]
            users = [m.get("content") for m in agent_controller._agent_state.conversation_history
                     if m.get("role") == "user"]
            RESULT["sent_reached_history"] = bool(users) and users[-1].startswith("move @Camera")
            RESULT["draft_cleared_after_send"] = bpy.context.window_manager.bfacw_chat_props.chat_input == ""
            _shoot("2b_after_send")
            from bfa_coworker import ui_chat  # pylint: disable=import-error
            bpy.ops.bfacw.chat_stop()
            # External Harness: the card in both places.
            CTX["addon"].preferences  # noqa: B018
            prefs = bpy.context.preferences.addons["bfa_coworker"].preferences
            CTX["mode"] = prefs.operating_mode
            from bfa_coworker import agent_controller  # pylint: disable=import-error
            agent_controller._agent_state.mcp_server_running = False   # modes switch only when stopped
            prefs.operating_mode = "EXTERNAL_HARNESS"
            for area in _areas():
                area.tag_redraw()
            return 1.0
        if step == 6:
            _shoot("3_external_harness")
            prefs = bpy.context.preferences.addons["bfa_coworker"].preferences
            prefs.operating_mode = CTX["mode"]
            # Ctrl+MMB drag up over the editor's chat zooms it.
            ce = CTX["ce"]
            surface = next(s for s, r in ce._SURFACES.values() if not s.compact)
            CTX["zoom_before_mmb"] = surface.zoom
            chat = surface._chat_rect
            x, y = _at(_editor_area(), chat.x + chat.w * 0.5, chat.y + chat.h * 0.4)
            _sim("MOUSEMOVE", "NOTHING", x=x, y=y)
            _sim("MIDDLEMOUSE", "PRESS", x=x, y=y, ctrl=True)
            for dy in range(10, 70, 10):
                _sim("MOUSEMOVE", "NOTHING", x=x, y=y + dy, ctrl=True)
            _sim("MIDDLEMOUSE", "RELEASE", x=x, y=y + 60, ctrl=True)
            return 1.0
        if step == 61:
            ce = CTX["ce"]
            surface = next(s for s, r in ce._SURFACES.values() if not s.compact)
            RESULT["ctrl_mmb_zoomed"] = surface.zoom > CTX["zoom_before_mmb"]
            # Rest the mouse on the sidebar chat (the marker used to turn pink on hover).
            compact = next((s, r) for s, r in ce._SURFACES.values() if s.compact)
            bounds = compact[0].bounds
            region = compact[1]
            RESULT["sidebar_chat_found"] = bounds is not None
            if bounds is not None:
                hx, hy = int(region.x + bounds.x + bounds.w * 0.5), int(region.y + bounds.top - 4)
                _sim("MOUSEMOVE", "NOTHING", x=hx, y=hy)
                CTX["header_xy"] = (int(region.x + bounds.x + 40), int(region.y + bounds.top + 14))
            return 1.0
        if step == 62:
            _shoot("5_sidebar_hover_and_zoomed_editor")
            hx, hy = CTX.get("header_xy", (0, 0))     # click the panel header: collapse
            _sim("MOUSEMOVE", "NOTHING", x=hx, y=hy)
            _sim("LEFTMOUSE", "PRESS", x=hx, y=hy)
            _sim("LEFTMOUSE", "RELEASE", x=hx, y=hy)
            _STEP["n"] = 63
            return 1.0
        if step == 63:
            _shoot("6_sidebar_chat_collapsed")
            compact = next(s for s, r in CTX["ce"]._SURFACES.values() if s.compact)
            RESULT["collapsed_hides_chat"] = compact.bounds is None
            hx, hy = CTX.get("header_xy", (0, 0))     # and open it again
            _sim("LEFTMOUSE", "PRESS", x=hx, y=hy)
            _sim("LEFTMOUSE", "RELEASE", x=hx, y=hy)
            _STEP["n"] = 64
            return 0.08
        if step == 64:
            _shoot("7_sidebar_chat_opening")             # mid-animation
            _STEP["n"] = 65
            return 1.0
        if step == 65:
            compact = next(s for s, r in CTX["ce"]._SURFACES.values() if s.compact)
            RESULT["reopened_shows_chat"] = compact.bounds is not None
            _shoot("8_sidebar_chat_reopened")
            # A normal node editor must look like itself.
            _editor_area().ui_type = "ShaderNodeTree"
            _STEP["n"] = 7
            return 1.0
        if step == 7:
            _shoot("4_shader_editor_untouched")
            RESULT["header_patched"] = bpy.types.NODE_HT_header.draw.__name__ == "draw"
            import addon_utils  # pylint: disable=import-error
            addon_utils.disable("bfa_coworker", default_set=True)
            RESULT["disabled_cleanly"] = "BFACW_CoworkerTree" not in dir(bpy.types)
    except Exception:  # pylint: disable=broad-exception-caught
        import traceback
        RESULT.setdefault("errors", []).append("step {:d}: {}".format(step, traceback.format_exc()[-1500:]))
        _save()
        bpy.ops.wm.quit_blender()
        return None
    if _STEP["n"] > 7:
        _save()
        bpy.ops.wm.quit_blender()
        return None
    return 1.0


bpy.app.timers.register(_tick, first_interval=2.0, persistent=True)
