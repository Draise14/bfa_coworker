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
    "_BFACW_OT_asset_selftest_run",
    "_BFACW_OT_asset_selftest_reset",
    "BFACW_OT_copy_mcp_config",
    "BFACW_OT_install_client_config",
    "BFACW_OT_test_mcp_config",
    "BFACW_OT_mcp_server_start",
    "BFACW_OT_mcp_server_stop",
    "BFACW_OT_save_provider",
    "BFACW_OT_delete_provider",
    "BFACW_OT_load_provider",
    "BFACW_OT_test_polyhaven_hdri",
    "BFACW_OT_test_polyhaven_texture",
    "BFACW_OT_open_harness_prefs",
    "BFACW_OT_open_addon_prefs",
    "BFACW_OT_open_url",
    "BFACW_OT_open_log",
    "BFACW_OT_compare_benchmarks",
)

import bpy  # pylint: disable=import-error

import threading

from .shared import effective_ports, get_llm_manager, get_agent_controller, HARNESS_PRESET_ITEMS
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
            self.report({"ERROR"}, "Remote API connection failed -- check URL and key")
        return {"FINISHED"}


class _BFACW_OT_refresh_remote_models(bpy.types.Operator):  # type: ignore[misc]
    bl_idname = "bfacw.refresh_remote_models"
    bl_label = "Refresh Models"
    bl_description = "Fetch the live model list from the remote API"

    def execute(self, context: bpy.types.Context) -> set[str]:
        prefs = context.preferences.addons[__package__].preferences
        if not prefs.remote_api_url:
            self.report({"ERROR"}, "No API URL configured -- select a provider first")
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
            self.report({"INFO"}, "All ports available -- {:s}".format(summary))
        else:
            self.report({"WARNING"}, "Some ports in use -- {:s}".format(summary))

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
                operating_mode=prefs.operating_mode,
                check_harness_config=(prefs.operating_mode == "EXTERNAL_HARNESS"),
                use_blender_python=getattr(prefs, "use_blender_python_for_harness", True),
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
            ("harness_config", "Harness Config"),
        ]:
            val = result.get(key, "not tested")
            if val == "N/A":
                continue
            lines.append("{:s}: {:s}".format(label, val))

        summary = " | ".join(lines)
        if result.get("all_ok"):
            self.report({"INFO"}, "All OK -- {:s}".format(summary))
        else:
            self.report({"ERROR"}, summary)

        return {"FINISHED"}


# ---------------------------------------------------------------------------
# Multi-Step Test Suites -- natural artist workflow sequences
#
# Each test suite is a list of (step_number, label, prompt) tuples.
# Steps are designed to be run in order, building on each other like
# a real artist would work. The prompts use natural language -- as if
# you're asking a colleague to do something.

_TEST_SUITES: dict[str, list[tuple[int, str, str]]] = {
    # == Core benchmarks (#104) =========================================
    # Six multi-purpose workflows, 4-5 steps each.  Together they cover
    # everything the 15 older suites did, in about half the steps.

    # -- Scene: objects, collections, materials, lights, camera, render ---
    # (was Baseline + Scene Build)
    "scene": [
        (1, "Stone Ring",
         "Let's build a little stonehenge. Make a wide, flat grass ground "
         "disc named \"Ground\", then a ring of 8 tall rough-hewn standing "
         "stones evenly spaced around the center, named \"Pillar_1\" through "
         "\"Pillar_8\"."),
        (2, "Lintels & Props",
         "Lay a stone lintel across the tops of each pair of neighbouring "
         "pillars (\"Lintel_1\" to \"Lintel_8\"), resting on them. Then "
         "scatter a few small rocks and bushes around the outside of the "
         "ring, varying their size and viewport color."),
        (3, "Collections",
         "Organize the scene into collections with color tags: \"Stones\" "
         "(blue) for pillars and lintels, \"Props\" (green) for the rocks "
         "and bushes, \"Ground\" (red) for the ground, and an empty "
         "\"Lighting\" (yellow)."),
        (4, "Materials",
         "Give the stones a rough warm-gray stone material with a noise "
         "texture for surface variation, the ground a grassy green, and the "
         "props one metallic copper and one glass material."),
        (5, "Light & Camera",
         "Light it like sunrise: a low warm-orange sun from the east and a "
         "faint blue fill from the opposite side, both in Lighting. Add a "
         "low-angle camera looking up at the stones with a wide lens, set "
         "EEVEE at 1080x1080, and render a frame."),
    ],
    # -- Animation: material, keyframes, squash & stretch, tracked camera --
    "animation": [
        (1, "Ball & Floor",
         "Create a sphere named \"Bouncing Ball\" with smooth shading and a "
         "shiny red rubber material with a little subsurface scattering, and "
         "a wide floor named \"Floor\" just under it with a tiled gym-floor "
         "material."),
        (2, "Bounce Keys",
         "Animate the ball bouncing: it starts on the floor, jumps high, "
         "then a few smaller bounces that settle. About 4 seconds at 30 fps; "
         "make the floor contacts feel solid."),
        (3, "Squash & Stretch",
         "Add squash and stretch: flatten the ball on each floor contact "
         "and stretch it slightly in the air, keyframing the scale to match "
         "the motion."),
        (4, "Camera Move",
         "Add a camera that slowly orbits the ball while staying aimed at "
         "it, framing the full bounce arc. Report the keyframe count on the "
         "ball."),
    ],
    # -- Modeling: modifier stack, apply, remesh, measurements -------------
    "modeling": [
        (1, "Torus & Stack",
         "Create a torus named \"Gear_Base\" at the origin (major radius "
         "1.0, minor 0.3, 48 x 16 segments). Add an Array modifier (count 3, "
         "offset 2.5 on X) and a Bevel modifier (width 0.05, 2 segments). "
         "Don't apply them yet."),
        (2, "Apply & Measure",
         "Apply the Array modifier, then report the vertex count."),
        (3, "Remesh & Thicken",
         "Add a voxel Remesh (voxel size 0.1), a Solidify (thickness 0.02) "
         "and a Smooth modifier (factor 0.5, 3 iterations), and apply all "
         "three."),
        (4, "Finish",
         "Add a Subdivision Surface (viewport level 2) without applying it, "
         "rename the object to \"Mechanical_Part\", and report the final "
         "vertex count and modifier stack."),
    ],
    # -- Nodes: shader, geometry nodes, compositor, screenshot check -------
    # (was Shader Nodes + Geometry Nodes + Compositor + Multi-Editor)
    "nodes": [
        (1, "Shader",
         "Create a plane with a material named \"Procedural_Grass\" built "
         "from Noise Texture -> Color Ramp (two greens) -> Principled BSDF."),
        (2, "Geo Nodes",
         "Add a grid with a Geometry Nodes modifier: Distribute Points on "
         "Faces -> Instance on Points (Ico Sphere) -> Set Shade Smooth, with "
         "a Random Value node scaling the instances between 0.5 and 2.0."),
        (3, "Compositor",
         "Enable the compositor and build Render Layers -> Blur (Gaussian, "
         "10 px) -> Composite/Viewer."),
        (4, "Verify",
         "Take a screenshot of the 3D viewport and tell me whether the "
         "scattered spheres and the grass material look right."),
    ],
    # -- Assets: Poly Haven texture/model/HDRI, Asset Browser ---------------
    # (was Asset Browser + Poly Haven)
    "assets": [
        (1, "PBR Texture",
         "Search Poly Haven for a brick texture, make a PBR material from "
         "it, and apply it to a new cube."),
        (2, "Model",
         "Search Poly Haven for a chair model, download it and import it "
         "next to the cube, with its textures."),
        (3, "HDRI",
         "Create a new scene named \"Studio\" and give it a Poly Haven "
         "sunset HDRI as its world. Leave the first scene's world exactly as "
         "it was."),
        (4, "Asset Browser",
         "Back in the first scene, search the asset browser: assign a "
         "material asset to the cube, load a node group asset into its "
         "material, and load a collection asset into the scene. Say what "
         "you used, or what was missing."),
    ],
    # -- Vision: screenshot-checked placement and framing ------------------
    # (was Vision: Camera + Vision: Place) -- for vision-capable models.
    "vision": [
        (1, "Table & Cup",
         "Build a simple table (2 x 1.2 m top, 0.1 m thick, at 0.9 m on four "
         "thin legs) with a wood material, named \"Table\". Put a red cup "
         "(0.25 m cylinder) named \"Cup\" resting exactly on the table top, "
         "centered. Take a screenshot with get_screenshot_of_area_as_image, "
         "look at it, and fix any floating or sinking."),
        (2, "Stack & Touch",
         "Balance a green marble (0.08 m radius) named \"Marble\" on the "
         "cup's rim, and place a 0.4 m cube named \"Neighbor\" on the table "
         "so its side touches the cup's side with no gap or overlap. "
         "Screenshot, look, and adjust until both are right; say how you "
         "verified it."),
        (3, "Product Shot",
         "Add a camera for a 3/4 product shot from the front-left, slightly "
         "elevated, with everything on the table in frame and a little "
         "margin. Screenshot, look, and re-aim until the framing is good."),
        (4, "Camera Check",
         "Switch the viewport to camera view, take a final screenshot, and "
         "confirm nothing is clipped and the shot is balanced. Report the "
         "camera location, rotation and focal length."),
    ],

    # == Quick challenges (#104) ========================================
    # Short sanity checks: run these first, or when a full pass isn't needed.

    # -- Quick: 2D editors (was Sequencer + Image Editor) ------------------
    "quick_2d": [
        (1, "Sequencer",
         "In the Video Sequencer, add a blue color strip and a text strip "
         "reading \"BFA Coworker Test\" above it, and set the frame range to "
         "1-100."),
        (2, "Image",
         "Create a 512x512 image named \"Test_Pattern\", fill its center "
         "half with red, and save it as test_pattern.png."),
    ],
    # -- Quick: one-prompt latency check -----------------------------------
    "quick_sanity": [
        (1, "Suzanne",
         "Add a Suzanne monkey head with a Subdivision Surface modifier "
         "(level 2), smooth shading and a gold metallic material."),
    ],
    # -- Quick: judgement on vague / impossible / contradictory asks -------
    # (was Error Handling) -- the right answer is to ask or decline.
    "quick_judgement": [
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
# Busy guard: True while a step thread is running for a given suite.
_test_suite_running: dict[str, bool] = {}
# Timing data: keyed by (suite_name, step_number) -> elapsed seconds.
_test_suite_timings: dict[tuple[str, int], float] = {}
# Per-step outcome: keyed by (suite_name, step_number) -> "ok" | "failed".
# Without this a failed step was indistinguishable from a successful one in
# the Diagnostics panel -- both showed only a timing, so a suite that failed
# instantly looked like it had simply run quickly.
_test_suite_status: dict[tuple[str, int], str] = {}
# Per-step error text: keyed by (suite_name, step_number) -> message.
_test_suite_errors: dict[tuple[str, int], str] = {}

# Steps whose prompt is deliberately vague / impossible / contradictory: the
# correct behaviour is to ASK a clarifying question or DECLINE, not to act.
# The end-of-turn execution guarantee must NOT force these steps to act -- for
# the contradictory "delete everything but keep all objects" prompt, forcing
# action could nudge a destructive mistake.  Keyed by (suite, step_number).
_REFUSAL_STEPS: set[tuple[str, int]] = {
    ("quick_judgement", 1),
    ("quick_judgement", 2),
    ("quick_judgement", 3),
}
# Path to persist benchmark results.
import os as _os
_BENCHMARK_RESULTS_PATH = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "benchmark_results.json")


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
            self.report({"ERROR"}, "Coworker is not running. Start it from Preferences.")
            return {"CANCELLED"}

        suite = _TEST_SUITES.get(self.suite)
        if not suite:
            self.report({"ERROR"}, "Unknown test suite '{:s}'".format(self.suite))
            return {"CANCELLED"}

        # Busy guard: don't start a new step while one is running.
        if _test_suite_running.get(self.suite, False):
            self.report({"WARNING"}, "A step is already running for this suite.")
            return {"CANCELLED"}

        # Get current step index.
        step_idx = _test_suite_progress.get(self.suite, 0)
        if step_idx >= len(suite):
            self.report({"INFO"}, "All steps completed! Reset to run again.")
            return {"FINISHED"}

        step_num, step_label, prompt = suite[step_idx]
        # Capture the pre-step object set (main thread) so a FAILED step can
        # be rolled back and not poison the next step.
        _before_objects = {o.name for o in bpy.data.objects}
        _run_test_step(
            context, self.suite, step_num, step_label, prompt,
            before_objects=_before_objects,
        )

        # Advance progress.
        _test_suite_progress[self.suite] = step_idx + 1

        total = len(suite)
        remaining = total - (step_idx + 1)
        if remaining == 0:
            # Auto-reset on completion.
            _test_suite_progress[self.suite] = 0
            # Persist results.
            _save_benchmark_results()
            self.report({"INFO"}, "Step {:d}/{:d} '{:s}' done -- suite complete, auto-reset!".format(
                step_num, total, step_label))
        else:
            self.report({"INFO"}, "Step {:d}/{:d} '{:s}' done -- {:d} more to go".format(
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
        # Clear this suite's recorded outcomes so a re-run starts clean.
        for key in [k for k in _test_suite_timings if k[0] == self.suite]:
            _test_suite_timings.pop(key, None)
        for key in [k for k in _test_suite_status if k[0] == self.suite]:
            _test_suite_status.pop(key, None)
        for key in [k for k in _test_suite_errors if k[0] == self.suite]:
            _test_suite_errors.pop(key, None)
        self.report({"INFO"}, "Test suite '{:s}' reset to step 1".format(self.suite))
        return {"FINISHED"}


class _BFACW_OT_asset_selftest_run(bpy.types.Operator):  # type: ignore[misc]
    """Run the deterministic (LLM-free) asset-tool self-test suite."""
    bl_idname = "bfacw.asset_selftest_run"
    bl_label = "Run All Auto"
    bl_description = (
        "Run every asset tool against a throwaway fixture library in-session "
        "(no MCP server, no LLM) with PASS/FAIL + timing per step"
    )

    _timer = None

    def invoke(self, context: bpy.types.Context, _event) -> set[str]:
        from . import asset_selftests as _ast

        if _ast.is_running():
            self.report({"WARNING"}, "Self-test is already running.")
            return {"CANCELLED"}
        self.report({"INFO"}, "Starting asset self-test suite...")

        wm = context.window_manager
        self._timer = wm.event_timer_add(0.25, window=context.window)
        wm.modal_handler_add(self)

        def _do_run():
            try:
                _ast.run_auto_suite()
            except Exception as ex:  # pylint: disable=broad-exception-caught
                print("[Coworker] asset self-test crashed: {:s}".format(str(ex)))

        threading.Thread(target=_do_run, daemon=True).start()
        return {"RUNNING_MODAL"}

    def modal(self, context: bpy.types.Context, _event) -> set[str]:
        from . import asset_selftests as _ast

        # Redraw the preferences UI so step results appear as they land.
        for area in context.window.screen.areas:
            if area.type == "PREFERENCES":
                area.tag_redraw()
        if _ast.is_running():
            return {"RUNNING_MODAL"}
        wm = context.window_manager
        if self._timer is not None:
            wm.event_timer_remove(self._timer)
            self._timer = None
        return {"FINISHED"}


class _BFACW_OT_asset_selftest_reset(bpy.types.Operator):  # type: ignore[misc]
    """Clear asset self-test results (and any leftover fixture data)."""
    bl_idname = "bfacw.asset_selftest_reset"
    bl_label = "Reset"
    bl_description = "Clear asset self-test results"

    def execute(self, context: bpy.types.Context) -> set[str]:
        del context
        from . import asset_selftests as _ast
        _ast._results.clear()
        _ast._last_error = ""
        try:
            import bpy as _bpy  # pylint: disable=import-error
            _ast._purge_datablocks(_bpy)
        except Exception:
            pass
        self.report({"INFO"}, "Asset self-test results cleared.")
        return {"FINISHED"}


def _save_benchmark_results() -> None:
    """Save benchmark timings to a JSON file."""
    import json as _json
    import time as _time

    if not _test_suite_timings:
        return

    results = {
        "timestamp": _time.time(),
        "timings": {
            "{:s}:{:d}".format(k[0], k[1]): v
            for k, v in _test_suite_timings.items()
        },
    }

    try:
        # Load existing results if present.
        existing = []
        if _os.path.isfile(_BENCHMARK_RESULTS_PATH):
            with open(_BENCHMARK_RESULTS_PATH, "r") as f:
                existing = _json.load(f)
                if not isinstance(existing, list):
                    existing = [existing]
        existing.append(results)
        # Keep last 20 runs.
        existing = existing[-20:]
        with open(_BENCHMARK_RESULTS_PATH, "w") as f:
            _json.dump(existing, f, indent=2)
        print("[Coworker] Benchmark results saved to {:s}".format(_BENCHMARK_RESULTS_PATH))
    except Exception as ex:
        print("[Coworker] Failed to save benchmark results: {:s}".format(str(ex)))


def _load_previous_benchmark() -> dict | None:
    """Load the second-to-last benchmark run for comparison, or None."""
    import json as _json
    try:
        if _os.path.isfile(_BENCHMARK_RESULTS_PATH):
            with open(_BENCHMARK_RESULTS_PATH, "r") as f:
                data = _json.load(f)
                if isinstance(data, list) and len(data) >= 2:
                    return data[-2]
    except Exception:  # pylint: disable=broad-exception-caught
        pass
    return None


def _record_step_outcome(
    suite_key: str,
    step_num: int,
    elapsed: float,
    error: str = "",
) -> None:
    """Record a step's timing and outcome for the Diagnostics panel.

    *error* empty means the step succeeded.  A non-empty *error* marks the
    step failed and stores the message so the panel can show it -- previously
    only the timing was kept, so failures were invisible.
    """
    _test_suite_timings[(suite_key, step_num)] = elapsed
    if error:
        _test_suite_status[(suite_key, step_num)] = "failed"
        _test_suite_errors[(suite_key, step_num)] = error
    else:
        _test_suite_status[(suite_key, step_num)] = "ok"
        _test_suite_errors.pop((suite_key, step_num), None)


def _suite_failure_count(suite_key: str) -> int:
    """Return how many steps of *suite_key* have failed."""
    return sum(
        1 for (sk, _step), status in _test_suite_status.items()
        if sk == suite_key and status == "failed"
    )


def _reset_scene_to_before(before_objects: set[str]) -> None:
    """Delete objects created since *before_objects* -- main-thread rollback.

    Called after a FAILED benchmark step (when "Reset on failure" is on) so
    the step's leftover geometry does not corrupt the next step (duplicate
    objects, ``.001`` name drift).  MUST run on the main thread -- bpy data
    edits are not thread-safe, so the caller schedules this via a timer.
    """
    try:
        added = [o for o in list(bpy.data.objects) if o.name not in before_objects]
        for obj in added:
            try:
                bpy.data.objects.remove(obj, do_unlink=True)
            except Exception:  # pylint: disable=broad-exception-caught
                pass
        # Return to a neutral context so the next step starts cleanly.
        try:
            if bpy.context.mode != 'OBJECT':
                bpy.ops.object.mode_set(mode='OBJECT')
        except Exception:  # pylint: disable=broad-exception-caught
            pass
        # Purge datablocks the failed step orphaned (meshes, materials, ...).
        for coll in (
            bpy.data.meshes, bpy.data.materials, bpy.data.curves,
            bpy.data.lights, bpy.data.cameras, bpy.data.images,
            bpy.data.node_groups, bpy.data.collections,
        ):
            for block in [b for b in coll if b.users == 0]:
                try:
                    coll.remove(block)
                except Exception:  # pylint: disable=broad-exception-caught
                    pass
        print("[Coworker] benchmark reset: removed {:d} object(s) left by "
              "the failed step".format(len(added)))
    except Exception as _ex:  # pylint: disable=broad-exception-caught
        print("[Coworker] benchmark reset failed: {:s}".format(str(_ex)))


def _run_test_step(
    context: bpy.types.Context,
    suite_key: str,
    step_num: int,
    step_label: str,
    prompt: str,
    before_objects: set[str] | None = None,
) -> None:
    """Run a single test step through the full agent pipeline in a background thread.

    *before_objects* is the object-name set captured before the step; when a
    step FAILS and "Reset on failure" is enabled, the scene is rolled back to
    it so the failure does not poison the following step.
    """
    _ac = get_agent_controller()

    prefs = context.preferences.addons[__package__].preferences
    _bridge_port, _mcp_port, _llm_port = effective_ports(prefs)
    # Use actual port if auto-shuffle kicked in.
    actual_mcp = _ac._agent_state.mcp_port_actual
    test_mcp_port = actual_mcp if actual_mcp else _mcp_port

    # Refusal steps must not be force-nudged into acting (see _REFUSAL_STEPS).
    allow_action_nudge = (suite_key, step_num) not in _REFUSAL_STEPS
    reset_on_failure = bool(getattr(prefs, "reset_scene_on_failure", True))

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

    print("[Coworker] test suite '{:s}': step {:d}/{:s} starting...".format(
        suite_key, step_num, step_label))
    print("[Coworker] test suite '{:s}': prompt = {:s}".format(suite_key, prompt))

    def _do_step():
        import time as _time
        _test_suite_running[suite_key] = True
        t_start = _time.monotonic()
        _failed = False
        # Snapshot the error before the call.  We deliberately do NOT clear
        # _agent_state.error up front: the session log exports it, and
        # clearing it destroyed the evidence of what actually failed.
        _prev_error = _ac._agent_state.error or ""
        try:
            _ac.run_conversation_turn(
                user_message=prompt,
                on_text=None,
                on_status=lambda s: print("[Coworker] test suite '{:s}': status = {:s}".format(
                    suite_key, s)),
                llm_url=llm_url or None,
                api_key=api_key or None,
                model=model,
                mcp_port=test_mcp_port,
                allow_action_nudge=allow_action_nudge,
            )
            elapsed = _time.monotonic() - t_start
            # run_conversation_turn does NOT raise on an LLM failure -- it
            # returns normally and records the reason in _agent_state.error.
            # Checking the exception alone therefore reported every failed
            # step as a success, which is why a broken benchmark run looked
            # like it had simply done nothing.
            step_error = _ac._agent_state.error or ""
            # An error that differs from the pre-call snapshot is this step's
            # failure.  An unchanged error is stale (left by an earlier step)
            # and must not be attributed here.  The one case this cannot
            # distinguish is a step that fails with a byte-identical message
            # to the previous step's -- rare, and erring toward "success"
            # there keeps the log honest about which step introduced it.
            if step_error and step_error != _prev_error:
                _failed = True
                _record_step_outcome(suite_key, step_num, elapsed, step_error)
                print("[Coworker] test suite '{:s}': step {:d}/{:s} FAILED in {:.1f}s -- {:s}".format(
                    suite_key, step_num, step_label, elapsed, step_error))
            else:
                _record_step_outcome(suite_key, step_num, elapsed)
                print("[Coworker] test suite '{:s}': step {:d}/{:s} completed in {:.1f}s".format(
                    suite_key, step_num, step_label, elapsed))
        except Exception as ex:
            _failed = True
            elapsed = _time.monotonic() - t_start
            _record_step_outcome(suite_key, step_num, elapsed, str(ex))
            print("[Coworker] test suite '{:s}': step {:d}/{:s} FAILED in {:.1f}s -- {:s}".format(
                suite_key, step_num, step_label, elapsed, str(ex)))
            _ac._agent_state.error = str(ex)
        finally:
            _test_suite_running[suite_key] = False
            # Reset-on-failure: roll the scene back to its pre-step object set
            # so the failed step's leftovers do not corrupt the next step.
            # Scheduled on the MAIN thread -- bpy data edits are not thread-safe.
            if _failed and reset_on_failure and before_objects is not None:
                bpy.app.timers.register(
                    (lambda _b=set(before_objects): (
                        _reset_scene_to_before(_b), None)[1]),
                    first_interval=0.0,
                )

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
        items=HARNESS_PRESET_ITEMS,
        default="claude_desktop",
    )

    def execute(self, context: bpy.types.Context) -> set[str]:
        prefs = context.preferences.addons[__package__].preferences
        _bridge_port, _, _ = effective_ports(prefs)
        use_blender_py = getattr(prefs, "use_blender_python_for_harness", True)
        config = _ac_mod.generate_mcp_client_config(
            client_type=self.client_type,
            blender_host=prefs.host,
            blender_port=_bridge_port,
            use_blender_python=use_blender_py,
        )
        context.window_manager.clipboard = config

        # Preflight the config we just handed out.  The addon previously
        # emitted configs it had never executed, so a broken interpreter path
        # or missing PYTHONPATH entry only surfaced as an opaque traceback
        # inside the user's MCP client.  Copy anyway (the user may target a
        # different machine) but warn with the actionable hint.
        check = _ac_mod.validate_mcp_client_config(
            client_type=self.client_type,
            blender_host=prefs.host,
            blender_port=_bridge_port,
            use_blender_python=use_blender_py,
        )
        BFACW_OT_test_mcp_config._result = check
        if not check.get("ok"):
            self.report(
                {"WARNING"},
                "Config copied, but it may not work: {:s}".format(
                    check.get("hint") or check.get("summary") or "validation failed"),
            )
        elif check.get("bridge_ok") is False:
            self.report(
                {"WARNING"},
                "Config copied and valid -- but the bridge is not running on "
                "port {:d}. Click Start Bridge.".format(_bridge_port),
            )
        else:
            self.report({"INFO"}, "MCP config copied to clipboard (validated)")
        return {"FINISHED"}


class BFACW_OT_install_client_config(bpy.types.Operator):  # type: ignore[misc]
    """Add the bfa-coworker MCP server to the selected client's config file."""
    bl_idname = "bfacw.install_client_config"
    bl_label = "Add to Client"
    bl_description = (
        "Write the bfa-coworker server into the selected client's config file. "
        "Other settings are kept and the previous file is backed up as <name>.bfacw.bak"
    )

    client_type: bpy.props.EnumProperty(  # type: ignore[valid-type]
        name="Client",
        items=HARNESS_PRESET_ITEMS,
        default="claude_code",
    )

    def execute(self, context: bpy.types.Context) -> set[str]:
        prefs = context.preferences.addons[__package__].preferences
        _bridge_port, _, _ = effective_ports(prefs)
        try:
            paths = _ac_mod.install_mcp_client_config(
                self.client_type,
                blender_host=prefs.host,
                blender_port=_bridge_port,
                use_blender_python=getattr(prefs, "use_blender_python_for_harness", True),
            )
        except (OSError, ValueError) as ex:
            self.report({"ERROR"}, "Could not update the config: {:s}".format(str(ex)))
            return {"CANCELLED"}
        BFACW_OT_test_mcp_config._result = {}
        restart = (
            "quit it fully (File > Exit) and reopen it"
            if self.client_type == "claude_desktop" else "restart it"
        )
        self.report(
            {"INFO"},
            "Added bfa-coworker to {:s} -- {:s}".format(
                ", ".join(str(p) for p in paths), restart),
        )
        return {"FINISHED"}


class BFACW_OT_test_mcp_config(bpy.types.Operator):  # type: ignore[misc]
    """Validate the generated MCP client config before the user pastes it."""
    bl_idname = "bfacw.test_mcp_config"
    bl_label = "Test Config"
    bl_description = (
        "Check that the generated MCP config can actually start the server: "
        "verifies the Python interpreter, PYTHONPATH entries, and imports"
    )

    _result: dict = {}  # class-level storage for display in draw()

    def execute(self, context: bpy.types.Context) -> set[str]:
        prefs = context.preferences.addons[__package__].preferences
        _bridge_port, _, _ = effective_ports(prefs)
        use_blender_py = getattr(prefs, "use_blender_python_for_harness", True)
        client_type = getattr(prefs, "harness_preset", "claude_desktop")

        def _do_check() -> None:
            check = _ac_mod.validate_mcp_client_config(
                client_type=client_type,
                blender_host=prefs.host,
                blender_port=_bridge_port,
                use_blender_python=use_blender_py,
            )
            # Also check the entry the client will actually launch (#100).
            check["installed"] = _ac_mod.inspect_installed_client_config(
                client_type, blender_port=_bridge_port)
            BFACW_OT_test_mcp_config._result = check

        thread = threading.Thread(target=_do_check, daemon=True)
        thread.start()
        thread.join(timeout=45)

        result = BFACW_OT_test_mcp_config._result
        if not result:
            self.report({"ERROR"}, "Config check timed out")
            return {"CANCELLED"}

        installed = result.get("installed") or {}
        if result.get("ok"):
            if installed.get("problems"):
                self.report({"WARNING"}, installed.get("summary", ""))
            else:
                self.report({"INFO"}, result.get("summary", "Config OK"))
            return {"FINISHED"}

        self.report(
            {"ERROR"},
            "{:s} -- {:s}".format(
                result.get("summary", "Config invalid"),
                result.get("hint", ""),
            ).strip(" --"),
        )
        return {"CANCELLED"}


# ---------------------------------------------------------------------------
# Open Harness Preferences (from Chat Panel)

def _open_addon_prefs_filtered(context: bpy.types.Context, pref_tab: str | None = None) -> None:
    """Open Blender preferences filtered to the Coworker addon page.

    ``bpy.ops.preferences.addon_show`` is unreliable here: it only works for
    legacy add-ons whose module is registered in ``addon_utils.addons_fake_modules``
    (extension add-ons get a ``bl_ext.`` module prefix and are skipped), and it
    fails silently when called before the preferences window has finished
    building.  Instead we set the same state that operator would set -- the
    addon search filter -- directly, deferred until the preferences area exists.
    """
    bpy.ops.screen.userpref_show('INVOKE_DEFAULT')
    context.preferences.active_section = 'ADDONS'

    # Select the addon's internal tab (e.g. ADVANCED for harness setup).
    if pref_tab is not None:
        try:
            prefs = context.preferences.addons[__package__].preferences
            prefs.pref_tab = pref_tab
        except Exception:
            pass

    # Defer the search filter until the preferences window is fully drawn --
    # calling it synchronously races the addon list population and is dropped.
    def _apply_filter() -> float | None:
        try:
            wm = bpy.context.window_manager
            # Match the addon by its display name (what the search box filters
            # against), not the module name.
            wm.addon_search = "Coworker"
            wm.addon_filter = 'All'
            bpy.context.preferences.view.show_addons_enabled_only = False
            # Expand the addon so its preferences are immediately visible.
            # Set `show_expanded` directly instead of `preferences.addon_expand`
            # (which *toggles* and would collapse it on a second click).
            import addon_utils
            # Ensure the fake-module cache is populated (same as
            # ``PREFERENCES_OT_addon_show`` does) before searching it.
            addon_utils.modules(refresh=False)
            # The addon may be registered as an extension (module name gets a
            # ``bl_ext.<repo>.`` prefix), so match by suffix.
            mod = addon_utils.addons_fake_modules.get(__package__)
            if mod is None:
                for _mod_name, _mod in addon_utils.addons_fake_modules.items():
                    if _mod_name.endswith("." + __package__):
                        mod = _mod
                        break
            if mod is not None:
                addon_utils.module_bl_info(mod)["show_expanded"] = True
            for area in bpy.context.screen.areas:
                if area.type == 'PREFERENCES':
                    area.tag_redraw()
        except Exception:
            pass
        return None

    bpy.app.timers.register(_apply_filter, first_interval=0.1)


class BFACW_OT_open_harness_prefs(bpy.types.Operator):  # type: ignore[misc]
    """Open Blender preferences to the bfa_coworker addon Advanced tab for harness setup."""
    bl_idname = "bfacw.open_harness_prefs"
    bl_label = "Configure Harness"
    bl_description = "Open preferences to configure external MCP client harness"

    def execute(self, context: bpy.types.Context) -> set[str]:
        _open_addon_prefs_filtered(context, pref_tab="ADVANCED")
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# Open Addon Preferences (filters to bfa_coworker)

class BFACW_OT_open_addon_prefs(bpy.types.Operator):  # type: ignore[misc]
    """Open Blender preferences filtered to the Coworker addon page."""
    bl_idname = "bfacw.open_addon_prefs"
    bl_label = "Open Settings"
    bl_description = "Open preferences to configure Coworker"

    def execute(self, context: bpy.types.Context) -> set[str]:
        _open_addon_prefs_filtered(context)
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# Open URL in default browser

class BFACW_OT_open_url(bpy.types.Operator):  # type: ignore[misc]
    """Open a URL in the default web browser."""
    bl_idname = "bfacw.open_url"
    bl_label = "Open URL"
    bl_description = "Open documentation link in your web browser"

    url: bpy.props.StringProperty(  # type: ignore[valid-type]
        name="URL",
        default="",
    )

    def execute(self, context: bpy.types.Context) -> set[str]:
        import webbrowser
        if self.url:
            webbrowser.open(self.url)
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

        actual = _ac_mod._agent_state.mcp_port_actual
        display_port = actual if actual else mcp_port
        self.report({"INFO"}, "MCP server started on {:s}:{:d}".format(mcp_host, display_port))
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
            self.report({"INFO"}, "Sending Poly Haven HDRI download request to Coworker...")
            _run_test_step(context, "polyhaven", 1, "HDRI",
                           "Download a sunset HDRI from Poly Haven and set it as "
                           "the world environment. Asset: belfast_sunset, type: hdris.")
            return {"FINISHED"}

        # Direct download fallback.
        self.report({"INFO"}, "Coworker not running. Starting direct download...")
        import threading
        def _do_download():
            try:
                from . import agent_controller as _ac
                result = _ac._call_mcp_tool_sync(
                    "download_polyhaven_asset",
                    {"asset_id": "sunset_meadow", "asset_type": "hdris", "resolution": "2k"},
                )
                print("[Coworker] Poly Haven test HDRI result: {:s}".format(str(result)[:200]))
            except Exception as ex:
                print("[Coworker] Poly Haven test HDRI failed: {:s}".format(str(ex)))
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
            self.report({"INFO"}, "Sending Poly Haven texture download request to Coworker...")
            _run_test_step(context, "polyhaven", 2, "Texture",
                           "Download a brick wall texture from Poly Haven and apply "
                           "it as a material on the active object. "
                           "Asset: brick_wall_001, type: textures.")
            return {"FINISHED"}

        self.report({"INFO"}, "Coworker not running. Starting direct download...")
        import threading
        def _do_download():
            try:
                from . import agent_controller as _ac
                result = _ac._call_mcp_tool_sync(
                    "download_polyhaven_asset",
                    {"asset_id": "brick_wall_001", "asset_type": "textures", "resolution": "2k"},
                )
                print("[Coworker] Poly Haven test texture result: {:s}".format(str(result)[:200]))
            except Exception as ex:
                print("[Coworker] Poly Haven test texture failed: {:s}".format(str(ex)))
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


# ---------------------------------------------------------------------------
# Open Log

class BFACW_OT_open_log(bpy.types.Operator):  # type: ignore[misc]
    """Open the Blender console/log in the system default text editor."""
    bl_idname = "bfacw.open_log"
    bl_label = "Open Log"
    bl_description = "Open the Blender system console or log file for debugging"
    bl_options = {'INTERNAL'}

    def execute(self, context: bpy.types.Context) -> set[str]:
        import subprocess
        import sys
        import os

        # Try to find the Blender console/log location.
        log_path = None

        # On Windows, Blender console is often the terminal itself.
        if sys.platform == "win32":
            # Try opening the Blender log file if it exists.
            # Blender writes to stdout/stderr which can be redirected.
            # For now, just bring the console window to front.
            try:
                import ctypes
                kernel32 = ctypes.windll.kernel32
                kernel32.SetConsoleTitleW("BFA Coworker Log")
                # Try to get the console window handle.
                console_window = kernel32.GetConsoleWindow()
                if console_window:
                    import ctypes.wintypes
                    user32 = ctypes.windll.user32
                    user32.SetForegroundWindow(console_window)
                    self.report({"INFO"}, "Console window brought to front")
                    return {"FINISHED"}
            except Exception:  # pylint: disable=broad-exception-caught
                pass
            self.report({"INFO"}, "Check the Blender console window for logs")
            return {"FINISHED"}

        # On macOS/Linux, try to find and open the Blender log.
        # Blender console logs go to stderr/stdout.
        # Try to use 'tail -f' on the log or open system console.
        blender_app = bpy.app.binary_path
        log_dir = os.path.expanduser("~/Library/Logs/Blender") if sys.platform == "darwin" else \
                  os.path.expanduser("~/.config/blender")

        if os.path.isdir(log_dir):
            # Find the most recent log file.
            log_files = []
            for root, _dirs, files in os.walk(log_dir):
                for f in files:
                    if f.endswith(".log"):
                        log_files.append(os.path.join(root, f))
            if log_files:
                log_files.sort(key=os.path.getmtime, reverse=True)
                log_path = log_files[0]

        if log_path and os.path.isfile(log_path):
            try:
                if sys.platform == "darwin":
                    subprocess.Popen(["open", log_path])
                else:
                    subprocess.Popen(["xdg-open", log_path])
                self.report({"INFO"}, "Opened log: {:s}".format(log_path))
                return {"FINISHED"}
            except Exception:  # pylint: disable=broad-exception-caught
                pass

        self.report({"INFO"}, "Check the Blender console for log output")
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# Compare Benchmarks

class BFACW_OT_compare_benchmarks(bpy.types.Operator):  # type: ignore[misc]
    """Compare current benchmark timings with the previous run."""
    bl_idname = "bfacw.compare_benchmarks"
    bl_label = "Compare Benchmarks"
    bl_description = "Show timing delta from the previous benchmark run"
    bl_options = {'INTERNAL'}

    def execute(self, context: bpy.types.Context) -> set[str]:
        import json as _json
        import time as _time

        current = _test_suite_timings
        prev_data = _load_previous_benchmark()

        if not current:
            self.report({"INFO"}, "No current timings to compare.")
            return {"FINISHED"}

        if prev_data is None:
            self.report({"INFO"}, "No previous benchmark run found. Run a suite first.")
            return {"FINISHED"}

        prev_timings = prev_data.get("timings", {})
        prev_ts = prev_data.get("timestamp", 0)

        print("\n[Coworker] Benchmark Comparison")
        print("  Previous run: {:s}".format(
            _time.strftime("%Y-%m-%d %H:%M:%S", _time.localtime(prev_ts))))
        print("  Current run: {:s}".format(
            _time.strftime("%Y-%m-%d %H:%M:%S", _time.localtime(_time.time()))))
        print("  {:<25s} {:>8s} {:>8s} {:>8s}".format(
            "Step", "Prev", "Now", "Delta"))
        print("  " + "-" * 53)

        for (suite, step), elapsed in sorted(current.items()):
            key = "{:s}:{:d}".format(suite, step)
            prev_elapsed = prev_timings.get(key)
            if prev_elapsed is not None:
                delta = elapsed - prev_elapsed
                delta_str = "+{:.1f}s".format(delta) if delta > 0 else "{:.1f}s".format(delta)
                print("  {:<25s} {:>7.1f}s {:>7.1f}s {:>8s}".format(
                    "{:s}:{:d}".format(suite, step),
                    prev_elapsed, elapsed, delta_str))
            else:
                print("  {:<25s} {:>8s} {:>7.1f}s {:>8s}".format(
                    "{:s}:{:d}".format(suite, step),
                    "--", elapsed, "NEW"))

        self.report({"INFO"}, "Benchmark comparison printed to console.")
        return {"FINISHED"}