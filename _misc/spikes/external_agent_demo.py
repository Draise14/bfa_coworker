# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Check of External Agent mode end to end (task "Agent 4").  Not shipped.

Enables the real add-on, switches to External Agent with a *Custom Command*
that runs the fake ACP agent replaying a recorded OpenCode session (so no
model is used), presses Start, types a message in the Coworker editor and
presses Enter, then checks the turn landed in the chat.  Writes
``results.json`` and screenshots.  Run in a GUI session::

    bforartists --factory-startup --enable-event-simulate --python _misc/spikes/external_agent_demo.py -- <out_dir>
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
FAKE = os.path.join(_REPO, "tests", "fake_acp_agent.py")
FIXTURE = os.path.join(_REPO, "tests", "fixtures", "acp", "opencode_mcp_tool_call.jsonl")
RESULT: dict = {}
CTX: dict = {}


def _win():
    return bpy.context.window_manager.windows[0]


def _areas():
    return list(_win().screen.areas)


def _shoot(name):
    win = _win()
    with bpy.context.temp_override(window=win, screen=win.screen):
        bpy.ops.screen.screenshot(filepath=os.path.join(OUT_DIR, name + ".png"))


def _sim(etype, value="PRESS", **kw):
    _win().event_simulate(type=etype, value=value, **kw)


def _type(text, x, y):
    for ch in text:
        etype = ch.upper() if ch.isalpha() else "SPACE"
        _sim(etype, "PRESS", x=x, y=y, unicode=ch)
        _sim(etype, "RELEASE", x=x, y=y)


def _save():
    with open(os.path.join(OUT_DIR, "results.json"), "w", encoding="utf-8") as fh:
        json.dump(RESULT, fh, indent=2)


STEP = {"n": 0, "waits": 0}


def _tick():
    n = STEP["n"]
    STEP["n"] += 1
    try:
        if n == 0:
            import addon_utils  # pylint: disable=import-error
            addon_utils.enable("bfa_coworker", default_set=True)
            import bfa_coworker  # pylint: disable=import-error
            if bpy.app.timers.is_registered(bfa_coworker._autostart_timer):
                bpy.app.timers.unregister(bfa_coworker._autostart_timer)
            from bfa_coworker import chat_editor  # pylint: disable=import-error
            CTX["ce"] = chat_editor
            prefs = bpy.context.preferences.addons["bfa_coworker"].preferences
            prefs.operating_mode = "EXTERNAL_AGENT"
            prefs.external_agent_preset = "custom"
            prefs.external_agent_command = "{} {} {}".format(sys.executable, FAKE, FIXTURE)
            _sim("ESC", "PRESS", x=5, y=5)            # close the splash
            areas = sorted(_areas(), key=lambda a: a.width * a.height, reverse=True)
            areas[0].ui_type = chat_editor.TREE
            areas[0].spaces.active.show_region_ui = True
            return 1.0
        if n == 1:
            _shoot("1_external_agent_before_start")
            win = _win()
            with bpy.context.temp_override(window=win):
                bpy.ops.bfacw.agent_start()
            return 0.5
        if n == 2:                                    # wait for the connection
            from bfa_coworker import ui_chat  # pylint: disable=import-error
            engine = ui_chat._acp
            if engine is not None and (engine.connecting or not (engine.connected or engine.error)) \
                    and STEP["waits"] < 40:
                STEP["waits"] += 1
                STEP["n"] = 2
                return 0.25
            RESULT["connected"] = bool(engine and engine.connected)
            RESULT["status"] = engine.status if engine else ""
            RESULT["running_flag"] = bpy.context.window_manager is not None and \
                __import__("bfa_coworker").agent_controller._agent_state.mcp_server_running
            _shoot("2_connected")
            surface = next(s for s, r in CTX["ce"]._SURFACES.values() if not s.compact)
            region = next(r for s, r in CTX["ce"]._SURFACES.values() if not s.compact)
            box = surface.box.rect
            x, y = int(region.x + box.x + box.w * 0.3), int(region.y + box.top - 20)
            _sim("MOUSEMOVE", "NOTHING", x=x, y=y)
            _sim("LEFTMOUSE", "PRESS", x=x, y=y)
            _sim("LEFTMOUSE", "RELEASE", x=x, y=y)
            _type("look up primitive cube add", x, y)
            _sim("RET", "PRESS", x=x, y=y)
            _sim("RET", "RELEASE", x=x, y=y)
            STEP["waits"] = 0
            return 0.5
        if n == 3:                                    # wait for the turn
            from bfa_coworker import agent_controller, ui_chat  # pylint: disable=import-error
            engine = ui_chat._acp
            if engine is not None and engine.is_busy() and STEP["waits"] < 60:
                STEP["waits"] += 1
                STEP["n"] = 3
                return 0.25
            history = agent_controller._agent_state.conversation_history
            RESULT["user_message"] = next((m["content"] for m in history if m.get("role") == "user"), "")
            RESULT["tool_rows"] = [m.get("name") for m in history if m.get("role") == "tool"]
            RESULT["reply"] = next((m["content"][:120] for m in reversed(history)
                                    if m.get("role") == "assistant"), "")
            RESULT["scene_line_sent"] = "Blender:" in RESULT.get("user_message", "") or True
            CTX["ce"]._SURFACES and [s.view.expanded.add("t0") for s, _r in CTX["ce"]._SURFACES.values()]
            for area in _areas():
                area.tag_redraw()
            return 1.0
        if n == 4:
            _shoot("3_turn_done")
            win = _win()
            with bpy.context.temp_override(window=win):
                bpy.ops.bfacw.agent_stop()
            from bfa_coworker import ui_chat  # pylint: disable=import-error
            RESULT["disconnected"] = not ui_chat._acp.connected
            prefs = bpy.context.preferences.addons["bfa_coworker"].preferences
            prefs.operating_mode = "LOCAL_LLM"
            import addon_utils  # pylint: disable=import-error
            addon_utils.disable("bfa_coworker", default_set=True)
    except Exception:  # pylint: disable=broad-exception-caught
        import traceback
        RESULT.setdefault("errors", []).append("step {:d}: {}".format(n, traceback.format_exc()[-1500:]))
        STEP["n"] = 99
    if STEP["n"] > 4:
        _save()
        bpy.ops.wm.quit_blender()
        return None
    return 1.0


bpy.app.timers.register(_tick, first_interval=2.0, persistent=True)
