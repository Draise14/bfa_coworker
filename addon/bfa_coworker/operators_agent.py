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
    "_BFACW_OT_test_step",
    "_BFACW_OT_test_step_reset",
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
# Multi-Step Test Suites — natural artist workflow sequences
#
# Each test suite is a list of (step_number, label, prompt) tuples.
# Steps are designed to be run in order, building on each other like
# a real artist would work. The prompts use natural language — as if
# you're asking a colleague to do something.

_TEST_SUITES: dict[str, list[tuple[int, str, str]]] = {
    # ── Scene Building Workflow ──────────────────────────────────────
    # Tests object creation, collections, materials, lighting, camera
    "scene_build": [
        (1, "Ground",
         "I'm setting up a scene. First, create a rounded ground — "
         "like a stage floor or backdrop. "
         "Name it \"Ground\"."),
        (2, "Props",
         "Now scatter some random objects on the ground: add a few cubes, "
         "spheres, and cylinders. Arrange them in a loose circle like toys "
         "around the center. Vary their sizes and shapes so they look interesting."),
        (3, "Collections",
         "Organize things into collections. Create three collections "
         "with color tags: \"Props\" (blue), \"Ground\" (green), and "
         "\"Lighting\" (yellow). Move the ground into Ground, "
         "all the scattered objects into Props."),
        (4, "Materials",
         "Give each object in the Props collection a material. Use a "
         "mix: some metallic (gold/copper), some rough (stone/concrete), "
         "and one glass. Name each material after the object."),
        (5, "Lighting",
         "Now light the scene. Add a Sun lamp angled from above-right "
         "for key light, and a warm-colored Point light near the center "
         "for fill. Put both in the Lighting collection."),
        (6, "Camera",
         "Place the camera to frame the whole scene from a slight "
         "high angle — like a product shot. Use a nice portrait "
         "focal length. Render at a decent HD resolution."),
    ],
    # ── Animation Workflow ──────────────────────────────────────────
    # Tests keyframes, timeline, motion paths
    "animation": [
        (1, "Ball",
         "Create a sphere at the origin, name it \"Bouncing Ball\". "
         "Give it a shiny red rubber material with smooth shading and a bit of SSS."),
        (2, "Floor",
         "Add floor below the ball — position it "
         "just under the ball and scale it wide enoguh for the animation. "
         "Give it a simple tiled floor material (like a gym floor) and name it \"Floor\"."),
        (3, "Bounce Keys",
         "Animate the ball bouncing up and down. "
         "It starts on the floor, jumps up high, comes back down, "
         "then does a few smaller bounces that settle back to the "
         "floor. Make the whole animation about 4 seconds long at "
         "30 fps. Try make the hit on the ground feel solid and the bounces feel natural."),
        (4, "Squash & Stretch",
         "Add squash and stretch to the bounce. When the ball hits "
         "the floor it should flatten (squash), and when it's in the "
         "air it should elongate slightly (stretch). Keyframe the "
         "scale to match the motion."),
        (5, "Camera Move",
         "Add a camera that slowly moves around the ball. "
         "Keep the camera tracked a little towards the ball the whole time "
         "so the viewer sees the bounce but follow it. "
         "Make sure the framing covers the full arc."),
    ],
    # ── Modifier Chain Workflow ─────────────────────────────────────
    # Tests modifier stacking, applying, and mesh operations
    # Goal: build a sculpt-ready head base mesh
    "modifiers": [
        (1, "Rough Head",
         "I want to make a head for sculpting. Start with a subdivided cube — "
         "roughly head-sized. Stretch it a bit taller than wide and "
         "slightly narrower on the sides to suggest a skull shape. "),
        (2, "Subdivide",
         "Smooth it out, enough levels to look smooth but "
         "not too dense yet. add in a bit of a squarish shape. Keep it symmetrical."),
        (3, "Mirror",
         "Chop it in half and mirror it so we only need to "
         "sculpt one side. Make sure clipping is on so the center "
         "seam stays clean. Apply a remesh. I want this procedural and in half. "),
        (4, "Apply & Cut",
         "Apply the Mirror modifier. Then cut it in half along the center line — "
         "delete the left half. "
         "Mirror again — this way the center "
         "line is perfectly flat and ready for sculpting."),
        (5, "Jaw & Chin",
         "Now shape the jawline. In Edit Mode, pull the bottom-front "
         "vertices forward a bit to suggest a chin. Widen the lower "
         "sides slightly for the jaw. Keep it symmetrical through "
         "the Mirror modifier."),
        (6, "Finalize",
         "Apply all remaining modifiers. Then add a remesh "
         "modifier with a nice resolutions so it's ready for "
         "sculpting. Name it \"Sculpt_Ready_Head\"."),
    ],
    # ── Asset & Material Workflow ───────────────────────────────────
    # Tests Poly Haven integration, material assignment, world setup
    "assets_materials": [
        (1, "World",
         "Download a sunset HDRI and set it as "
         "the world environment."),
        (2, "Objects",
         "Create a shaderball at the origin. "
         "Add a plane below it as a display surface."),
        (3, "Texture",
         "Download a brick wall texture from Poly Haven and apply "
         "it as a material on the plane."),
        (4, "Material",
         "Create a glass material for the shaderball: high "
         "transmission, low roughness, and a realistic IOR "
         "for glass. Name it \"Glass\"."),
        (5, "Render",
         "Add three-point lighting for EEVEE: a key light from the right, "
         "fill from the left, rim light from behind. Set the camera "
         "to frame the shaderball nicely. Frame and render at square res."),
    ],
    # ── Baseline Latency (quick sanity — fun scene) ─────────────────
    "baseline": [
        (1, "Stone Ring",
         "Can you make a stonehenge? First, create a ring of stone "
         "pillars — tall, rough-hewn blocks arranged in a circle "
         "around the center, evenly spaced. Make them look like "
         "standing stones. Name them \"Pillar_1\" through \"Pillar_8\"."),
        (2, "Lintels",
         "Now add lintels on top of the pillars. For each pair of "
         "adjacent pillars, create a horizontal beam resting on top. "
         "Make each one thick enough to look like a solid stone "
         "crossbeam. Name them \"Lintel_1\" through \"Lintel_8\"."),
        (3, "Ground",
         "Add a large flat ground plane beneath the circle — a wide "
         "flat disc. Give it a grassy green material."),
        (4, "Material",
         "Give all the stone pillars and lintels a rough stone "
         "material: high roughness, low specular, a warm gray color. "
         "Use a noise texture to add some surface variation."),
        (5, "Lighting",
         "Add dramatic lighting: a sun lamp angled low from the east "
         "(like sunrise) with warm orange tint, and a faint blue fill "
         "light from the opposite side for contrast."),
        (6, "Camera",
         "Place the camera at a low angle looking up at the stones, "
         "framing the circle with the sun behind the pillars. "
         "Use a wide focal length for a dramatic shot. "
         "Frame and render at square resolution."),
    ],
    # ── Error Handling (ambiguous prompts) ──────────────────────────
    "error_handling": [
        (1, "Vague",
         "Make it nicer."),
        (2, "Impossible",
         "Render a 16K IMAX movie with 10 million polygons."),
        (3, "Contradiction",
         "Delete everything but keep all objects."),
    ],
}

# Track which step the user is on for each suite.
# Keyed by suite name, value is the current step index (0-based).
_test_suite_progress: dict[str, int] = {}


class _BFACW_OT_test_step(bpy.types.Operator):  # type: ignore[misc]
    """Run the next step in a multi-step test suite."""
    bl_idname = "bfacw.test_step"
    bl_label = "Run Step"
    bl_description = "Run the next step in this test suite"

    suite: bpy.props.StringProperty(  # type: ignore[valid-type]
        name="Suite",
        default="",
    )

    def execute(self, context: bpy.types.Context) -> set[str]:
        if not get_agent_controller()._agent_state.mcp_server_running:
            self.report({"ERROR"}, "Agent is not running. Start it from Preferences.")
            return {"CANCELLED"}

        suite = _TEST_SUITES.get(self.suite)
        if not suite:
            self.report({"ERROR"}, "Unknown test suite '{:s}'".format(self.suite))
            return {"CANCELLED"}

        # Get current step index.
        step_idx = _test_suite_progress.get(self.suite, 0)
        if step_idx >= len(suite):
            self.report({"INFO"}, "All steps completed! Reset to run again.")
            return {"FINISHED"}

        step_num, step_label, prompt = suite[step_idx]
        _run_test_step(context, self.suite, step_num, step_label, prompt)

        # Advance progress.
        _test_suite_progress[self.suite] = step_idx + 1

        total = len(suite)
        remaining = total - (step_idx + 1)
        if remaining == 0:
            self.report({"INFO"}, "Step {:d}/{:d} '{:s}' done — all finished!".format(
                step_num, total, step_label))
        else:
            self.report({"INFO"}, "Step {:d}/{:d} '{:s}' done — {:d} more to go".format(
                step_num, total, step_label, remaining))
        return {"FINISHED"}


class _BFACW_OT_test_step_reset(bpy.types.Operator):  # type: ignore[misc]
    """Reset progress for a test suite so it starts from step 1 again."""
    bl_idname = "bfacw.test_step_reset"
    bl_label = "Reset"
    bl_description = "Reset this test suite back to step 1"

    suite: bpy.props.StringProperty(  # type: ignore[valid-type]
        name="Suite",
        default="",
    )

    def execute(self, context: bpy.types.Context) -> set[str]:
        del context
        _test_suite_progress.pop(self.suite, None)
        self.report({"INFO"}, "Test suite '{:s}' reset to step 1".format(self.suite))
        return {"FINISHED"}


def _run_test_step(
    context: bpy.types.Context,
    suite_key: str,
    step_num: int,
    step_label: str,
    prompt: str,
) -> None:
    """Run a single test step through the full agent pipeline in a background thread."""
    _ac = get_agent_controller()

    prefs = context.preferences.addons[__package__].preferences
    _bridge_port, _mcp_port, _llm_port = effective_ports(prefs)

    # Resolve LLM config (same as chat_send).
    llm = get_llm_manager()
    from . import ui_chat as _ui_chat
    _ui_chat._sync_prefs_to_config(prefs)
    llm_cfg = llm.get_config()
    llm_url = None
    api_key = None
    model = None
    if llm_cfg.mode == "remote":
        llm_url = llm_cfg.remote_api_url
        api_key = llm_cfg.remote_api_key
        model = llm_cfg.remote_model or None

    print("[🛠️Coworker] test suite '{:s}': step {:d}/{:s} starting...".format(
        suite_key, step_num, step_label))
    print("[🛠️Coworker] test suite '{:s}': prompt = {:s}".format(suite_key, prompt))

    def _do_step():
        try:
            _ac.run_conversation_turn(
                user_message=prompt,
                on_text=None,
                on_status=lambda s: print("[🛠️Coworker] test suite '{:s}': status = {:s}".format(
                    suite_key, s)),
                llm_url=llm_url or None,
                api_key=api_key or None,
                model=model,
                mcp_port=_mcp_port,
            )
            print("[🛠️Coworker] test suite '{:s}': step {:d}/{:s} completed".format(
                suite_key, step_num, step_label))
        except Exception as ex:
            print("[🛠️Coworker] test suite '{:s}': step {:d}/{:s} FAILED — {:s}".format(
                suite_key, step_num, step_label, str(ex)))
            _ac._agent_state.error = str(ex)

    thread = threading.Thread(target=_do_step, daemon=True)
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
            _run_test_step(context, "polyhaven", 1, "HDRI",
                           "Download a sunset HDRI from Poly Haven and set it as "
                           "the world environment. Asset: belfast_sunset, type: hdris.")
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
            _run_test_step(context, "polyhaven", 2, "Texture",
                           "Download a brick wall texture from Poly Haven and apply "
                           "it as a material on the active object. "
                           "Asset: brick_wall_001, type: textures.")
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


# ---------------------------------------------------------------------------
# Reload Skills

class BFACW_OT_reload_skills(bpy.types.Operator):  # type: ignore[misc]
    """Reload the built-in skills and system prompt cache."""
    bl_idname = "bfacw.reload_skills"
    bl_label = "Reload Skills"
    bl_description = "Reload built-in skills and clear the system prompt cache"
    bl_options = {'INTERNAL'}

    def execute(self, context: bpy.types.Context) -> set[str]:
        from . import agent_controller as _ac
        _ac._clear_system_prompt_cache()
        self.report({"INFO"}, "Skills and system prompt cache cleared")
        return {"FINISHED"}