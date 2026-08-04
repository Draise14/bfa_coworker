# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Operators for remote API testing, model browsing, and agent ping.
"""

__all__ = (
    "_BFACW_OT_test_remote_api",
    "_BFACW_OT_refresh_remote_models",
    "_BFACW_OT_open_model_browser",
    "_BFACW_OT_ping_agent",
    "_BFACW_OT_check_ports",
    "_BFACW_OT_benchmark_objects",
    "_BFACW_OT_benchmark_scene",
    "_BFACW_OT_benchmark_animation",
    "_BFACW_OT_benchmark_collections",
)

import bpy  # pylint: disable=import-error

import threading

from .shared import effective_ports, get_llm_manager, get_agent_controller
from . import agent_controller as _ac_mod


class _BFACW_OT_test_remote_api(bpy.types.Operator):  # type: ignore[misc]
    bl_idname = "bfacw.test_remote_api"
    bl_label = "Test Connection"
    bl_description = "Test the remote API connection"

    def execute(self, context: bpy.types.Context) -> set[str]:
        prefs = context.preferences.addons[__package__].preferences
        if not prefs.remote_api_url:
            self.report({"ERROR"}, "No API URL configured")
            return {"CANCELLED"}
        if not prefs.remote_api_key:
            self.report({"ERROR"}, "No API key configured")
            return {"CANCELLED"}

        llm = get_llm_manager()
        ok = llm.check_remote_api(prefs.remote_api_url, prefs.remote_api_key)
        if ok:
            self.report({"INFO"}, "Remote API connection successful")
        else:
            self.report({"ERROR"}, "Remote API connection failed — check URL and key")
        return {"FINISHED"}


class _BFACW_OT_refresh_remote_models(bpy.types.Operator):  # type: ignore[misc]
    bl_idname = "bfacw.refresh_remote_models"
    bl_label = "Refresh Models"
    bl_description = "Fetch the live model list from the remote API"

    def execute(self, context: bpy.types.Context) -> set[str]:
        prefs = context.preferences.addons[__package__].preferences
        if not prefs.remote_api_url:
            self.report({"ERROR"}, "No API URL configured — select a provider first")
            return {"CANCELLED"}
        if not prefs.remote_api_key:
            self.report({"ERROR"}, "No API key configured")
            return {"CANCELLED"}

        llm = get_llm_manager()
        models, error = llm.fetch_remote_models(prefs.remote_api_url, prefs.remote_api_key)

        if error:
            prefs.remote_models_count = 0
            prefs.remote_models_fetch_error = error
            self.report({"ERROR"}, error)
            return {"CANCELLED"}

        prefs.remote_models_count = len(models)
        prefs.remote_models_fetch_error = ""

        self.report({"INFO"}, "{:d} models available from the API".format(len(models)))
        return {"FINISHED"}


class _BFACW_OT_open_model_browser(bpy.types.Operator):  # type: ignore[misc]
    bl_idname = "bfacw.open_model_browser"
    bl_label = "Browse Models"
    bl_description = "Open openrouter.ai/models in your browser to find model IDs"

    def execute(self, context: bpy.types.Context) -> set[str]:
        import webbrowser
        webbrowser.open("https://openrouter.ai/models")
        return {"FINISHED"}


class _BFACW_OT_check_ports(bpy.types.Operator):  # type: ignore[misc]
    bl_idname = "bfacw.check_ports"
    bl_label = "Check Ports"
    bl_description = "Test whether the default ports are available or in use"

    _result: dict = {}  # class-level storage for display in draw()

    def execute(self, context: bpy.types.Context) -> set[str]:
        prefs = context.preferences.addons[__package__].preferences
        _bridge_port, _mcp_port, _llm_port = effective_ports(prefs)

        def _do_check():
            _BFACW_OT_check_ports._result = _ac_mod.check_ports_available(
                bridge_port=_bridge_port,
                mcp_port=_mcp_port,
                llm_port=_llm_port,
            )

        thread = threading.Thread(target=_do_check, daemon=True)
        thread.start()
        thread.join(timeout=10)

        result = _BFACW_OT_check_ports._result
        if not result:
            self.report({"ERROR"}, "Port check timed out")
            return {"CANCELLED"}

        lines = []
        for label_key in [("bridge", "Bridge"), ("mcp", "MCP"), ("llm", "LLM")]:
            available = result.get(label_key[0], False)
            lines.append("{:s}: {:s}".format(label_key[1], "Available" if available else "In Use"))

        summary = "  |  ".join(lines)
        if all(result.values()):
            self.report({"INFO"}, "All ports available — {:s}".format(summary))
        else:
            self.report({"WARNING"}, "Some ports in use — {:s}".format(summary))

        return {"FINISHED"}


class _BFACW_OT_ping_agent(bpy.types.Operator):  # type: ignore[misc]
    bl_idname = "bfacw.ping_agent"
    bl_label = "Ping"
    bl_description = "Test connectivity to MCP server and LLM backend"

    _result: dict = {}  # class-level storage for display in draw()

    def execute(self, context: bpy.types.Context) -> set[str]:
        _ac = get_agent_controller()
        prefs = context.preferences.addons[__package__].preferences
        _bridge_port, _mcp_port, _llm_port = effective_ports(prefs)

        def _do_ping():
            _BFACW_OT_ping_agent._result = _ac.ping_agent(
                mcp_port=_mcp_port, llm_port=_llm_port, bridge_port=_bridge_port,
            )

        thread = threading.Thread(target=_do_ping, daemon=True)
        thread.start()
        thread.join(timeout=35)

        result = _BFACW_OT_ping_agent._result

        if not result:
            self.report({"ERROR"}, "Ping timed out or failed")
            return {"CANCELLED"}

        lines = []
        for key, label in [
            ("bridge_server", "Bridge"),
            ("mcp_server", "MCP"),
            ("llm_health", "LLM Health"),
            ("llm_chat", "LLM Chat"),
        ]:
            val = result.get(key, "not tested")
            lines.append("{:s}: {:s}".format(label, val))

        summary = " | ".join(lines)
        if result.get("all_ok"):
            self.report({"INFO"}, "All OK — {:s}".format(summary))
        else:
            self.report({"ERROR"}, summary)

        return {"FINISHED"}


# ---------------------------------------------------------------------------
# Benchmark operators — test prompts for the MCP agent

_BENCHMARK_PROMPTS = {
    "objects": (
        "Create 12 random objects (mix of cubes, spheres, cylinders, cones, toruses) "
        "distributed in 3 groups around the scene. Assign each group to its own "
        "collection: \"Group_A\", \"Group_B\", \"Group_C\". Give each collection's "
        "objects a random color. Position groups at (-5, 3, 0), (5, -2, 2), and "
        "(0, -5, -3)."
    ),
    "scene": (
        "Set up a scene: add a round ground plane (a flat cylinder scaled wide), "
        "place 6 stone columns (cylinders) in a circle on the ground. Add a "
        "sun light and a point light for warm illumination. Place the camera to "
        "frame the columns from a dramatic low angle. Render at 1920x1080."
    ),
    "animation": (
        "Switch to the Animation workspace. Create a torus at the world origin. "
        "Animate it with keyframes: frame 1 at origin, frame 30 at (0, 0, 5), "
        "frame 60 at (5, 0, 5), frame 90 at (5, 0, 0), frame 120 back at origin. "
        "Make the animation loop seamlessly. Set the timeline range from 1 to 120."
    ),
    "collections": (
        "Create three collections: \"SET\", \"LIT\", \"ANIM\". Assign them the "
        "correct color tags: SET = blue, LIT = yellow, ANIM = red. Add a cube "
        "to SET, a point light to LIT, and an empty to ANIM."
    ),
}


class _BFACW_OT_benchmark_objects(bpy.types.Operator):  # type: ignore[misc]
    bl_idname = "bfacw.benchmark_objects"
    bl_label = "Objects"
    bl_description = "Benchmark: create random objects in colored groups"

    def execute(self, context: bpy.types.Context) -> set[str]:
        if not get_agent_controller()._agent_state.mcp_server_running:
            self.report({"ERROR"}, "Agent is not running. Start it from Preferences.")
            return {"CANCELLED"}
        _run_benchmark(context, "objects")
        self.report({"INFO"}, "Benchmark 'objects' started — check console")
        return {"FINISHED"}


class _BFACW_OT_benchmark_scene(bpy.types.Operator):  # type: ignore[misc]
    bl_idname = "bfacw.benchmark_scene"
    bl_label = "Scene"
    bl_description = "Benchmark: setup scene with ground, columns, lighting, camera"

    def execute(self, context: bpy.types.Context) -> set[str]:
        if not get_agent_controller()._agent_state.mcp_server_running:
            self.report({"ERROR"}, "Agent is not running. Start it from Preferences.")
            return {"CANCELLED"}
        _run_benchmark(context, "scene")
        self.report({"INFO"}, "Benchmark 'scene' started — check console")
        return {"FINISHED"}


class _BFACW_OT_benchmark_animation(bpy.types.Operator):  # type: ignore[misc]
    bl_idname = "bfacw.benchmark_animation"
    bl_label = "Animation"
    bl_description = "Benchmark: animate a torus with keyframes"

    def execute(self, context: bpy.types.Context) -> set[str]:
        if not get_agent_controller()._agent_state.mcp_server_running:
            self.report({"ERROR"}, "Agent is not running. Start it from Preferences.")
            return {"CANCELLED"}
        _run_benchmark(context, "animation")
        self.report({"INFO"}, "Benchmark 'animation' started — check console")
        return {"FINISHED"}


class _BFACW_OT_benchmark_collections(bpy.types.Operator):  # type: ignore[misc]
    bl_idname = "bfacw.benchmark_collections"
    bl_label = "Collections"
    bl_description = "Benchmark: create SET/LIT/ANIM collections with color tags"

    def execute(self, context: bpy.types.Context) -> set[str]:
        if not get_agent_controller()._agent_state.mcp_server_running:
            self.report({"ERROR"}, "Agent is not running. Start it from Preferences.")
            return {"CANCELLED"}
        _run_benchmark(context, "collections")
        self.report({"INFO"}, "Benchmark 'collections' started — check console")
        return {"FINISHED"}


def _run_benchmark(context: bpy.types.Context, bench_key: str) -> None:
    """Run a benchmark prompt through the full agent pipeline in a background thread."""
    _ac = get_agent_controller()

    prompt = _BENCHMARK_PROMPTS.get(bench_key, "")
    if not prompt:
        return

    prefs = context.preferences.addons[__package__].preferences
    _bridge_port, _mcp_port, _llm_port = effective_ports(prefs)

    # Resolve LLM config (same as chat_send).
    llm = get_llm_manager()
    llm_cfg = llm.get_config()
    llm_url = None
    api_key = None
    model = None
    if llm_cfg.mode == "remote":
        llm_url = llm_cfg.remote_api_url
        api_key = llm_cfg.remote_api_key
        model = llm_cfg.remote_model or None

    print("[🛠️Coworker] benchmark: starting '{:s}' test...".format(bench_key))
    print("[🛠️Coworker] benchmark: prompt = {:s}".format(prompt))

    def _do_benchmark():
        try:
            _ac.run_conversation_turn(
                user_message=prompt,
                on_text=None,
                on_status=lambda s: print("[🛠️Coworker] benchmark: status = {:s}".format(s)),
                llm_url=llm_url or None,
                api_key=api_key or None,
                model=model,
                mcp_port=_mcp_port,
            )
            print("[🛠️Coworker] benchmark: '{:s}' completed".format(bench_key))
        except Exception as ex:
            print("[🛠️Coworker] benchmark: '{:s}' FAILED — {:s}".format(bench_key, str(ex)))
            _ac._agent_state.error = str(ex)

    thread = threading.Thread(target=_do_benchmark, daemon=True)
    thread.start()