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
    "BFACW_OT_copy_mcp_config",
    "BFACW_OT_mcp_server_start",
    "BFACW_OT_mcp_server_stop",
    "BFACW_OT_save_provider",
    "BFACW_OT_delete_provider",
    "BFACW_OT_load_provider",
    "BFACW_OT_test_polyhaven_hdri",
    "BFACW_OT_test_polyhaven_texture",
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
    "polyhaven_hdri": (
        "Use the search_polyhaven_assets tool to find a sunset HDRI, "
        "then use download_polyhaven_asset to download and apply it "
        "as the world environment. Asset ID: belfast_sunset, type: hdris."
    ),
    "polyhaven_texture": (
        "Use the search_polyhaven_assets tool to find a brick wall texture, "
        "then use download_polyhaven_asset to download and apply it "
        "as a material on the active object. Asset ID: brick_wall_001, type: textures."
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


# ---------------------------------------------------------------------------
# Copy MCP Client Config (External Harness)

class BFACW_OT_copy_mcp_config(bpy.types.Operator):  # type: ignore[misc]
    """Copy MCP client configuration to the clipboard."""
    bl_idname = "bfacw.copy_mcp_config"
    bl_label = "Copy MCP Config"
    bl_description = "Copy the MCP client configuration to the clipboard"

    client_type: bpy.props.EnumProperty(  # type: ignore[valid-type]
        name="Client",
        items=[
            ("claude", "Claude Desktop", "Claude Desktop config format"),
            ("vscode", "VS Code / Cursor", "VS Code / Cursor config format"),
        ],
        default="claude",
    )

    def execute(self, context: bpy.types.Context) -> set[str]:
        prefs = context.preferences.addons[__package__].preferences
        _bridge_port, _, _ = effective_ports(prefs)
        config = _ac_mod.generate_mcp_client_config(
            client_type=self.client_type,
            blender_host=prefs.host,
            blender_port=_bridge_port,
        )
        context.window_manager.clipboard = config
        self.report({"INFO"}, "MCP config copied to clipboard")
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# MCP Server Start (Network Mode)

class BFACW_OT_mcp_server_start(bpy.types.Operator):  # type: ignore[misc]
    """Start the MCP server in Network mode."""
    bl_idname = "bfacw.mcp_server_start"
    bl_label = "Start MCP Server"
    bl_description = "Start the MCP HTTP server for external clients"

    def execute(self, context: bpy.types.Context) -> set[str]:
        prefs = context.preferences.addons[__package__].preferences
        _bridge_port, _mcp_port, _ = effective_ports(prefs)

        mcp_host = prefs.mcp_server_host
        mcp_port = prefs.mcp_server_port_override if prefs.mcp_server_port_override > 0 else _mcp_port

        proc = _ac_mod.start_mcp_server_network(
            host=mcp_host,
            port=mcp_port,
            blender_host=prefs.host,
            blender_port=_bridge_port,
        )
        if proc is None:
            self.report({"ERROR"}, _ac_mod._agent_state.error)
            return {"CANCELLED"}

        self.report({"INFO"}, "MCP server started on {:s}:{:d}".format(mcp_host, mcp_port))
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# MCP Server Stop

class BFACW_OT_mcp_server_stop(bpy.types.Operator):  # type: ignore[misc]
    """Stop the MCP server."""
    bl_idname = "bfacw.mcp_server_stop"
    bl_label = "Stop MCP Server"
    bl_description = "Stop the MCP HTTP server"

    def execute(self, context: bpy.types.Context) -> set[str]:
        _ac_mod.stop_mcp_server()
        self.report({"INFO"}, "MCP server stopped")
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# BYOK Multi-Provider (Tier 2)

class BFACW_OT_save_provider(bpy.types.Operator):  # type: ignore[misc]
    """Save the current remote API configuration as a named provider profile."""
    bl_idname = "bfacw.save_provider"
    bl_label = "Save Provider"
    bl_description = "Save the current remote API configuration as a named profile"

    profile_name: bpy.props.StringProperty(  # type: ignore[valid-type]
        name="Profile Name",
        default="",
        description="A name for this provider profile (e.g. 'OpenAI', 'Anthropic')",
    )

    def invoke(self, context: bpy.types.Context, event: bpy.types.Event) -> set[str]:
        del event
        wm = context.window_manager
        return wm.invoke_props_dialog(self, width=400)

    def draw(self, context: bpy.types.Context) -> None:
        del context
        layout = self.layout
        layout.prop(self, "profile_name")

    def execute(self, context: bpy.types.Context) -> set[str]:
        prefs = context.preferences.addons[__package__].preferences
        name = self.profile_name.strip()
        if not name:
            self.report({"ERROR"}, "Profile name cannot be empty")
            return {"CANCELLED"}

        profile = {
            "name": name,
            "provider": prefs.remote_provider,
            "api_url": prefs.remote_api_url,
            "api_key": prefs.remote_api_key,
            "model": prefs.remote_model,
        }

        providers = prefs._get_saved_providers()
        # Replace existing profile with same name.
        providers = [p for p in providers if p.get("name") != name]
        providers.append(profile)
        prefs._set_saved_providers(providers)

        self.report({"INFO"}, "Saved provider profile '{:s}'".format(name))
        return {"FINISHED"}


class BFACW_OT_delete_provider(bpy.types.Operator):  # type: ignore[misc]
    """Delete a saved provider profile."""
    bl_idname = "bfacw.delete_provider"
    bl_label = "Delete Provider"
    bl_description = "Delete a saved provider profile"

    profile_name: bpy.props.StringProperty(  # type: ignore[valid-type]
        name="Profile Name",
        default="",
    )

    def execute(self, context: bpy.types.Context) -> set[str]:
        prefs = context.preferences.addons[__package__].preferences
        providers = prefs._get_saved_providers()
        providers = [p for p in providers if p.get("name") != self.profile_name]
        prefs._set_saved_providers(providers)
        self.report({"INFO"}, "Deleted provider profile '{:s}'".format(self.profile_name))
        return {"FINISHED"}


class BFACW_OT_load_provider(bpy.types.Operator):  # type: ignore[misc]
    """Load a saved provider profile into the current remote API configuration."""
    bl_idname = "bfacw.load_provider"
    bl_label = "Load Provider"
    bl_description = "Load a saved provider profile"

    profile_name: bpy.props.StringProperty(  # type: ignore[valid-type]
        name="Profile Name",
        default="",
    )

    def execute(self, context: bpy.types.Context) -> set[str]:
        prefs = context.preferences.addons[__package__].preferences
        providers = prefs._get_saved_providers()
        for p in providers:
            if p.get("name") == self.profile_name:
                prefs.remote_provider = p.get("provider", "_custom")
                prefs.remote_api_url = p.get("api_url", "")
                prefs.remote_api_key = p.get("api_key", "")
                prefs.remote_model = p.get("model", "")
                self.report({"INFO"}, "Loaded provider profile '{:s}'".format(self.profile_name))
                return {"FINISHED"}
        self.report({"ERROR"}, "Profile '{:s}' not found".format(self.profile_name))
        return {"CANCELLED"}


# ---------------------------------------------------------------------------
# Poly Haven Test Operators

class BFACW_OT_test_polyhaven_hdri(bpy.types.Operator):  # type: ignore[misc]
    """Download a test HDRI from Poly Haven to verify the integration."""
    bl_idname = "bfacw.test_polyhaven_hdri"
    bl_label = "Download Test HDRI"
    bl_description = "Download a sunset HDRI from Poly Haven and set it as world environment"

    def execute(self, context: bpy.types.Context) -> set[str]:
        # Use the MCP tool via agent controller if running, else direct download.
        if get_agent_controller()._agent_state.mcp_server_running:
            self.report({"INFO"}, "Sending Poly Haven HDRI download request to agent...")
            _run_benchmark(context, "polyhaven_hdri")
            return {"FINISHED"}

        # Direct download fallback.
        self.report({"INFO"}, "Agent not running. Starting direct download...")
        import threading
        def _do_download():
            try:
                from . import agent_controller as _ac
                result = _ac._call_mcp_tool_sync(
                    "download_polyhaven_asset",
                    {"asset_id": "sunset_meadow", "asset_type": "hdris", "resolution": "2k"},
                )
                print("[🛠️Coworker] Poly Haven test HDRI result: {:s}".format(str(result)[:200]))
            except Exception as ex:
                print("[🛠️Coworker] Poly Haven test HDRI failed: {:s}".format(str(ex)))
        thread = threading.Thread(target=_do_download, daemon=True)
        thread.start()
        return {"FINISHED"}


class BFACW_OT_test_polyhaven_texture(bpy.types.Operator):  # type: ignore[misc]
    """Download a test texture from Poly Haven to verify the integration."""
    bl_idname = "bfacw.test_polyhaven_texture"
    bl_label = "Download Test Texture"
    bl_description = "Download a brick texture from Poly Haven and apply it as a material"

    def execute(self, context: bpy.types.Context) -> set[str]:
        if get_agent_controller()._agent_state.mcp_server_running:
            self.report({"INFO"}, "Sending Poly Haven texture download request to agent...")
            _run_benchmark(context, "polyhaven_texture")
            return {"FINISHED"}

        self.report({"INFO"}, "Agent not running. Starting direct download...")
        import threading
        def _do_download():
            try:
                from . import agent_controller as _ac
                result = _ac._call_mcp_tool_sync(
                    "download_polyhaven_asset",
                    {"asset_id": "brick_wall_001", "asset_type": "textures", "resolution": "2k"},
                )
                print("[🛠️Coworker] Poly Haven test texture result: {:s}".format(str(result)[:200]))
            except Exception as ex:
                print("[🛠️Coworker] Poly Haven test texture failed: {:s}".format(str(ex)))
        thread = threading.Thread(target=_do_download, daemon=True)
        thread.start()
        return {"FINISHED"}