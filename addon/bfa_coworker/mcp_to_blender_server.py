# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Non-blocking TCP socket server that runs inside Blender.

Listens for null-byte-delimited JSON requests, executes Python code
directly in the calling thread, and returns JSON responses.
All socket operations are non-blocking so the server never blocks
Blender's main thread.
"""

__all__ = (
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "TIMER_INTERVAL_ACTIVE",
    "is_running",
    "poll",
    "poll_blocking",
    "start",
    "stop",
    "timer_idle_interval",
    "timer_idle_reset",
    "timer_internal_vars_calc",
    "use_log",
    "log_level",
    "get_actual_port",
)

import json
import math
import random
import re
import select
import socket
import sys
import time
import traceback
from collections.abc import Callable
from typing import NamedTuple

DEFAULT_HOST = "localhost"
DEFAULT_PORT = 9876

# Seconds between main-thread timer ticks.
TIMER_INTERVAL_ACTIVE = 0.05
# Seconds between main-thread timer ticks while idle (no pending work).
_TIMER_INTERVAL_IDLE = 1.0
# Seconds of inactivity before switching to the idle interval.
_TIMER_INTERVAL_IDLE_DELAY = 5.0


class _TimerState:
    """
    Mutable singleton holding timer-related runtime state.

    This is manipulated from the preferences and updated via ``timer_internal_vars_calc``.
    """

    __slots__ = (
        "interval_active",
        "interval_idle",
        "interval_idle_delay",
        "idle_countdown_reset",
        "idle_countdown",
        "client_timeout_countdown",
    )

    def __init__(self) -> None:
        self.interval_active: float = TIMER_INTERVAL_ACTIVE
        self.interval_idle: float = _TIMER_INTERVAL_IDLE
        self.interval_idle_delay: float = _TIMER_INTERVAL_IDLE_DELAY
        # Number of active-rate ticks before switching to idle.
        self.idle_countdown_reset: int = 0
        # Current countdown. When zero, `timer_idle_interval` returns idle.
        self.idle_countdown: int = 0
        # Poll ticks before an idle client is evicted.
        self.client_timeout_countdown: int = 2


_timer = _TimerState()


# Actual port the bridge server bound to (may differ from DEFAULT_PORT
# if auto-shuffle kicked in).  0 = not started yet.
_actual_port: int = 0


def get_actual_port() -> int:
    """Return the port the bridge server is actually listening on, or 0."""
    return _actual_port


def _find_available_port(preferred: int, max_offset: int = 100) -> int:
    """Return the first available port starting at *preferred*.

    Tries ``preferred``, ``preferred + 1``, … up to ``preferred + max_offset``.
    Returns the first port that can be bound, or 0 if none are available.
    """
    for offset in range(max_offset + 1):
        candidate = preferred + offset
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            if sys.platform == "win32":
                s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            s.bind(("127.0.0.1", candidate))
            s.close()
            if offset > 0:
                print("[🛠️Coworker] _find_available_port: port {:d} in use, shuffled to {:d}".format(
                    preferred, candidate))
            return candidate
        except (OSError, socket.error):
            s.close()
            continue
    print("[🛠️Coworker] _find_available_port: no port available in range {:d}–{:d}".format(
        preferred, preferred + max_offset))
    return 0


def timer_internal_vars_calc(
        active: float | None = None,
        idle: float | None = None,
        idle_delay: float | None = None,
) -> None:
    """
    Optionally update ``TIMER_*`` constants and recalculate internal variables.

    When keyword arguments are provided they replace the corresponding
    module-level ``TIMER_*`` value. Pass ``None`` (the default) to leave
    a value unchanged.
    """
    if active is not None:
        _timer.interval_active = active
    if idle is not None:
        _timer.interval_idle = idle
    if idle_delay is not None:
        _timer.interval_idle_delay = idle_delay
    # Round up so the delay is never shorter than requested.
    _timer.idle_countdown_reset = math.ceil(_timer.interval_idle_delay / _timer.interval_active)
    _timer.idle_countdown = _timer.idle_countdown_reset
    _timer.client_timeout_countdown = max(2, math.ceil(_CLIENT_TIMEOUT / _timer.interval_active))


def timer_idle_reset() -> None:
    """
    Signal that work was processed, resetting the idle countdown.
    """
    _timer.idle_countdown = _timer.idle_countdown_reset


def timer_idle_interval() -> float:
    """
    Return the appropriate timer interval, decrementing the idle countdown.

    Returns ``TIMER_INTERVAL_ACTIVE`` while the countdown is positive,
    then ``_TIMER_INTERVAL_IDLE`` once it reaches zero.
    """
    if _timer.idle_countdown > 0:
        _timer.idle_countdown -= 1
        return _timer.interval_active
    return _timer.interval_idle


# When True, print every request and response status to STDERR.
use_log: bool = False
# Granular log level: "OFF", "ERRORS_ONLY", or "ALL".
log_level: str = "OFF"

_MAX_REQUEST_BYTES = 10 * 1024 * 1024  # 10 MiB.
# Maximum number of queued incoming connections.
_LISTEN_BACKLOG = 5
_RECV_BUFFER_SIZE = 4096
# Seconds before a client that has not sent a complete request is closed.
_CLIENT_TIMEOUT = 10.0
# How often `poll_blocking` checks for shutdown.
_POLL_BLOCKING_TIMEOUT = 1.0
_DEFERRED_UNSUPPORTED_MESSAGE = (
    "Deferred responses via `check_is_finished` are only supported "
    "by the interactive addon server, and are not available in "
    "background mode. Finish the request synchronously instead."
)

timer_internal_vars_calc()


def _should_log_request() -> bool:
    """Return True when the current log settings want request logging."""
    return use_log or log_level == "ALL"


def _should_log_response(is_error: bool = False) -> bool:
    """Return True when the current log settings want response logging.

    If ``is_error`` is True, both ``ALL`` and ``ERRORS_ONLY`` log levels
    will produce output.
    """
    if use_log or log_level == "ALL":
        return True
    if is_error and log_level == "ERRORS_ONLY":
        return True
    return False


# ---------------------------------------------------------------------------
# Client connection state.

class _Client:
    """
    Per-connection state for a client (the MCP server process) that has not yet sent a complete request.
    """

    __slots__ = (
        "conn",
        "buffer",
        "timeout",
    )

    def __init__(self, conn: socket.socket) -> None:
        self.conn: socket.socket = conn
        # Accumulates data until the null-byte delimiter is received.
        self.buffer: bytearray = bytearray()
        # Poll ticks remaining before this client is evicted.
        self.timeout: int = _timer.client_timeout_countdown


# ---------------------------------------------------------------------------
# Server state.

class _State:
    """
    Mutable singleton holding the runtime state of this socket server (the Blender add-on side).
    """

    __slots__ = (
        "sock",
        "clients",
    )

    def __init__(self) -> None:
        # The listening socket, or `None` when not running.
        self.sock: socket.socket | None = None
        # Connected clients that have not yet sent a complete request.
        self.clients: list[_Client] = []


_state = _State()


class _ExecResult(NamedTuple):
    """
    Result of executing tool-code.

    When *check_fn* is not ``None``, the caller must defer the response
    and poll the callable for completion (see ``deferred_tool``).
    Otherwise *response* is the final result to send.
    """

    response: dict[str, object]
    check_fn: Callable[[], dict[str, object] | None] | None = None


def _encode_response(response: dict[str, object]) -> bytes:
    """
    Serialize a response dict as null-byte-delimited JSON bytes.
    """
    return (json.dumps(response) + "\0").encode("utf-8")


def _safe_depsgraph_sync(allow_full_sync: bool = True) -> None:
    """
    Synchronize the depsgraph using the safest available strategy.

    Blender 5.3 can crash (EXCEPTION_ACCESS_VIOLATION in
    ``deg_eval_copy_is_expanded``) when ``view_layer.update()`` is called
    after collection-manipulation operations (creating collections, moving
    objects between them, setting color tags).  This helper tries multiple
    strategies in order of safety:

    1. Tag each object for update (lightweight, no full rebuild).
    2. Fall back to ``view_layer.update()`` only if tagging is unavailable
       and *allow_full_sync* is ``True``.

    :param allow_full_sync: When ``False``, only the lightweight
        ``update_tag()`` strategy is attempted.  Use this after code that
        manipulates collections, where a full depsgraph rebuild is known
        to crash in Blender 5.3.
    """
    try:
        import bpy as _bpy  # pylint: disable=import-error
    except ImportError:
        return  # Not running inside Blender.

    # ── Strategy 1: Tag each object for update ──────────────────────
    # This is the safest approach — it marks objects as needing a
    # re-evaluation without triggering a full depsgraph rebuild.
    # The rebuild is what crashes in Blender 5.3 after collection ops.
    try:
        for _obj in _bpy.data.objects:
            try:
                _obj.update_tag()
            except (ReferenceError, AttributeError):
                pass  # Object may have been deleted mid-iteration.
        # print("[🛠️Coworker] _safe_depsgraph_sync: used update_tag strategy")
        return
    except Exception:  # pylint: disable=broad-exception-caught
        if not allow_full_sync:
            return  # Don't fall through to view_layer.update().
        pass  # Fall through to strategy 2.

    # ── Strategy 2: Full view_layer.update() ────────────────────────
    # This is the traditional approach.  It can crash in Blender 5.3
    # after collection manipulation, but is the only option when
    # update_tag() is not available (e.g. very old Blender versions).
    try:
        _bpy.context.view_layer.update()
        # print("[🛠️Coworker] _safe_depsgraph_sync: used view_layer.update strategy")
    except Exception:  # pylint: disable=broad-exception-caught
        pass  # Best-effort; view_layer may not be available.


def _code_touches_collections(code: str) -> bool:
    """Heuristic check: does *code* manipulate collections?

    Returns ``True`` if the code contains patterns that are known to
    trigger depsgraph crashes in Blender 5.3 after a full
    ``view_layer.update()``.

    Covers both direct collection API calls (``collections.new``,
    ``.link()``, ``.unlink()``) and attribute assignments on
    layer-collection objects (``layer_col.exclude``,
    ``layer_col.hide_viewport``) which are generated by the
    ``jump_to_view3d_*`` tool templates.
    """
    _COLLECTION_PATTERNS = (
        "collections.new",
        ".unlink(",
        ".link(",
        "color_tag",
        "children.link",
        "objects.unlink",
        "layer_col.exclude",
        "layer_col.hide_viewport",
    )
    return any(p in code for p in _COLLECTION_PATTERNS)


def _code_is_undo_or_push(code: str) -> bool:
    """Heuristic check: is *code* an undo/push operation?

    Undo/push operations trigger depsgraph notifiers.  If we've tagged
    objects with ``update_tag()`` before the undo, the event loop will
    try to evaluate those now-stale tagged objects and crash.
    """
    return "bpy.ops.ed.undo()" in code or "bpy.ops.ed.undo_push(" in code



# ---------------------------------------------------------------------------
# Preflight code validation — catches common LLM mistakes before exec().
# Returns a list of (pattern_name, guidance) tuples.  Empty = no issues.
# ---------------------------------------------------------------------------

def _preflight_check(code: str) -> list[tuple[str, str]]:
    """Validate *code* for common LLM-generated mistakes before execution.

    Returns a list of ``(pattern_name, guidance)`` tuples.  An empty list
    means the code passed all checks.  Each check is a lightweight regex —
    total cost is <1ms.
    """
    issues: list[tuple[str, str]] = []

    # 1. Missing bpy import — most common first-time failure.
    uses_bpy = re.search(r"\bbpy\.", code) or "bpy.ops." in code
    has_import = "import bpy" in code
    if uses_bpy and not has_import:
        issues.append((
            "missing_bpy",
            "Missing 'import bpy'. Add 'import bpy' at the top of your code.",
        ))

    # 2. Wrong subdivision modifier attribute.
    if re.search(r"\.subdivisions\s*=", code) and "SUBSURF" in code.upper():
        issues.append((
            "wrong_subdiv_attr",
            "Blender 5.3: mod.subdivisions was renamed. Use mod.levels instead.",
        ))

    # 3. Wrong Principled BSDF attribute access.
    if re.search(r"\.(base_color|base_color_input)\s*=", code):
        issues.append((
            "wrong_principled_attr",
            "Principled BSDF has no base_color attribute. "
            "Use principled.inputs['Base Color'].default_value = (R, G, B, 1.0)",
        ))

    # 4. Wrong torus primitive keywords.
    if "primitive_torus_add" in code and re.search(r"ring_count\s*=", code):
        issues.append((
            "wrong_torus_kw",
            "primitive_torus_add has no ring_count parameter. "
            "Use major_radius, minor_radius, major_segments, minor_segments.",
        ))

    # 5. Wrong sequencer API (sequences vs strips).
    if re.search(r"\.sequences\b", code) and "sequence_editor" in code:
        issues.append((
            "wrong_sequencer_api",
            "Blender 5.x: editor.sequences was renamed to editor.strips.",
        ))

    # 6. Wrong auto smooth API.
    if "use_auto_smooth" in code:
        issues.append((
            "wrong_auto_smooth",
            "mesh.use_auto_smooth was removed in Blender 5.3. "
            "Use mesh.auto_smooth_angle instead.",
        ))

    # 7. Accessing action.fcurves (removed in Blender 5.0+).
    if re.search(r"action\.fcurves", code):
        issues.append((
            "wrong_fcurves",
            "action.fcurves was removed in Blender 5.0+. "
            "Use keyframe_insert() for keyframe creation.",
        ))

    # 8. Using bpy.ops in a loop without context override.
    ops_in_loop = re.search(
        r"(for|while)\s+.+:\s*\n\s+bpy\.ops\.", code
    )
    if ops_in_loop:
        issues.append((
            "ops_in_loop",
            "Calling bpy.ops inside a loop is slow and may lose context. "
            "Batch operations with bpy.data or bpy.context instead.",
        ))

    # 9. No output — code runs but returns nothing visible.
    has_result = "result" in code and ("=" in code or "{" in code)
    has_print = "print(" in code
    if not has_result and not has_print and len(code.strip()) > 50:
        issues.append((
            "no_output",
            "Your code has no print() or result assignment. "
            "Add print() to see output, or assign to a 'result' dict.",
        ))


    # 10. Wrong lamp/light API (lamps -> lights since Blender 4.0).
    if re.search(r"bpy\.data\.lamps", code):
        issues.append((
            "wrong_lamps_api",
            "bpy.data.lamps was renamed to bpy.data.lights in Blender 4.0.",
        ))

    # 11. Wrong render engine name (EEVEE -> BLENDER_EEVEE since Blender 4.0).
    if re.search(r'["\']EEVEE["\']', code) and "render.engine" in code:
        issues.append((
            "wrong_eevee_name",
            "render.engine = 'EEVEE' was renamed to 'BLENDER_EEVEE' in Blender 4.0.",
        ))

    # 12. Accessing scene.render.eevee (moved in Blender 4.0).
    if re.search(r"render\.eevee\.", code):
        issues.append((
            "wrong_eevee_access",
            "scene.render.eevee was removed. EEVEE settings are now on "
            "scene.eevee (e.g. scene.eevee.use_ssr).",
        ))

    return issues

def _execute_code(
        code: str,
        strict_json: bool,
) -> _ExecResult:
    """
    Execute *code* and return an ``_ExecResult``.

    :param strict_json: When true, the response *must* be serializable.
        Should always be true, when executing Python code we have full-control over,
        because any non-serializable data is effectively a bug.

        Only allow it to be false when executing arbitrary LLM generated code,
        in this case it's not worth the overhead of correcting the LLM mistake,
        just ``__repr__`` the value so it can fumble its way forward.
    """
    from .capture_output import CaptureOutput
    from .weak_sandbox import WeakSandboxForLLM

    # Pre-populate common modules so LLM-generated code doesn't need to
    # import them explicitly — reduces a common failure mode.
    namespace: dict[str, object] = {
        "result": {},
        "math": math,
        "random": random,
        "time": time,
        "json": json,
        "re": re,
    }
    with CaptureOutput() as captured, WeakSandboxForLLM():
        try:
            # NOTE: We intentionally do NOT sync the depsgraph before
            # executing LLM code.  Tagging all objects with update_tag()
            # schedules a massive depsgraph evaluation.  If the smart-undo
            # system then fires bpy.ops.ed.undo() (reverting the scene),
            # the event loop tries to evaluate those now-stale tagged
            # objects and crashes with EXCEPTION_ACCESS_VIOLATION in
            # pyrna_struct_CreatePyObject.
            #
            # A Python-level exception from stale layer-collection data
            # is always preferable to an unrecoverable C-level segfault.

            # Preflight: validate code before execution.
            preflight_issues = _preflight_check(code)
            if preflight_issues:
                hint_lines = ["[Preflight] Found {:d} issue(s):".format(len(preflight_issues))]
                for _name, guidance in preflight_issues:
                    hint_lines.append("  - {:s}".format(guidance))
                hint_lines.append("")
                hint_lines.append("Fix these issues and retry. Do NOT retry the same code.")
                return _ExecResult({"status": "error", "message": "\n".join(hint_lines)})

            exec(code, namespace)

            # Force a depsgraph update after successful code execution.
            # Without this, creating many objects in a single call can leave
            # the depsgraph in an inconsistent state, causing a hard crash
            # (EXCEPTION_ACCESS_VIOLATION in layer_collection_sync) on the
            # next viewport redraw timer tick.
            # If the code manipulates collections or is an undo/push op,
            # skip the full sync to avoid Blender 5.3 depsgraph crashes.
            _safe_depsgraph_sync(
                allow_full_sync=(
                    not _code_touches_collections(code)
                    and not _code_is_undo_or_push(code)
                )
            )
            # NOTE: Deferred layer-collection sync via timers was removed.
            # Calling view_layer.update() from a timer triggers a full
            # depsgraph rebuild in a deferred context, which crashes in
            # Blender 5.3 with EXCEPTION_ACCESS_VIOLATION in build_materials
            # when materials are in an inconsistent state (e.g. rapid object
            # creation with scatter operations).  The update_tag() strategy
            # in _safe_depsgraph_sync already handles this safely.
        except Exception:  # pylint: disable=broad-exception-caught
            # Truncate traceback to last 3 frames + exception message.
            # Full tracebacks are verbose and eat context window space.
            tb_str = traceback.format_exc()
            tb_lines = tb_str.splitlines()
            if len(tb_lines) > 8:
                # Keep: "Traceback (most recent call last):" + last 3 frames
                # + blank line + exception type + message.
                # Typical format:
                #   Traceback (most recent call last):
                #     File "...", line N, in ...
                #       code
                #     File "...", line N, in ...
                #       code
                #     File "...", line N, in ...
                #       code
                #   ExceptionType: message
                # We keep header + last 3 frame pairs (file+code) + exception.
                header = tb_lines[0]  # "Traceback (most recent call last):"
                # Find the exception line (last non-empty line).
                exc_line = tb_lines[-1] if tb_lines[-1].strip() else tb_lines[-2]
                # Take last 6 lines before the exception (3 frames × 2 lines each).
                frame_lines = tb_lines[-7:-1] if len(tb_lines) >= 8 else tb_lines[1:-1]
                tb_str = "{:s}\n{:s}\n{:s}".format(header, "\n".join(frame_lines), exc_line)
                tb_str += "\n[Traceback truncated to last 3 frames]"
            # Detect common LLM mistakes and append actionable hints.
            if "__contains__: expected a string or a tuple of strings" in tb_str:
                tb_str += (
                    "\n\nHINT: You used `identifier in modifier` to check a Geometry Nodes socket. "
                    "This pattern is REMOVED in Blender 5.2+. "
                    "Use `getattr(modifier.properties.inputs, identifier)` instead:\n"
                    "    try:\n"
                    "        socket = getattr(mod.properties.inputs, \"Socket_3\")\n"
                    "    except AttributeError:\n"
                    "        pass  # Socket doesn't exist"
                )
            if "Context missing active object" in tb_str or "Context missing object" in tb_str:
                tb_str += (
                    "\n\nHINT: The scene has no active object. "
                    "Many operators (mode_set, transform, etc.) require an active object. "
                    "Create an object first with `bpy.ops.mesh.primitive_cube_add()` or "
                    "check `bpy.context.active_object` before calling mode-dependent operators."
                )
            if "use_auto_smooth" in tb_str and "has no attribute" in tb_str:
                tb_str += (
                    "\n\nHINT: `mesh.use_auto_smooth` was REMOVED in Blender 5.3. "
                    "Use `mesh.auto_smooth_angle` directly instead:\n"
                    "    mesh.auto_smooth_angle = radians(30)  # Sets angle, auto-smooth is implicit\n"
                    "To disable auto-smooth, set the angle to 0:\n"
                    "    mesh.auto_smooth_angle = 0"
                )
            if "has no attribute 'fcurves'" in tb_str and "Action" in tb_str:
                tb_str += (
                    "\n\nHINT: `Action.fcurves` was REMOVED in Blender 5.0+ (layered animation system). "
                    "Use `keyframe_insert()` for all keyframe creation:\n"
                    "    obj.keyframe_insert(data_path=\"location\", frame=10)\n"
                    "To read existing F-Curves, use the read-only helper from the animation.md skill. "
                    "Never access `action.fcurves` directly."
                )
            if "'Context' object has no attribute 'selected_" in tb_str:
                tb_str += (
                    "\n\nHINT: `bpy.context` has NO `selected_edges` / `selected_faces` / "
                    "`selected_verts` attribute — edit-mode selections live on the mesh data, "
                    "not on context. Read them with bmesh:\n"
                    "    import bmesh\n"
                    "    bm = bmesh.from_edit_mesh(bpy.context.active_object.data)\n"
                    "    sel_edges = [e for e in bm.edges if e.select]\n"
                    "    sel_faces = [f for f in bm.faces if f.select]\n"
                    "    sel_verts = [v for v in bm.verts if v.select]\n"
                    "To write selections, set `e.select` / `f.select` / `v.select` and call "
                    "`bm.select_flush_mode()`, or use `bmesh.ops.select_*`. "
                    "`bpy.context.selected_objects` IS valid — but only in OBJECT mode "
                    "for objects."
                )
            if "Converting py args to operator properties" in tb_str and "unrecognized" in tb_str:
                tb_str += (
                    "\n\nHINT: You passed a keyword argument that this operator does not accept "
                    "(e.g. `ring_segments` — the UV sphere uses `segments` and `ring_count`). "
                    "Discover the real parameters from the operator docstring:\n"
                    "    print(bpy.ops.mesh.primitive_uv_sphere_add.__doc__)\n"
                    "Common primitive keywords: cube/plane/monkey/grid: `size=`; "
                    "uv_sphere: `segments=` + `ring_count=`; ico_sphere: `subdivisions=`; "
                    "circle: `vertices=`; cylinder/cone: `vertices=` + `radius=`/`radius1=` "
                    "+ `depth=`; torus: `major_radius=` + `minor_radius=` + "
                    "`major_segments=` + `minor_segments=`."
                )
            if "StructRNA of type " in tb_str and "has been removed" in tb_str:
                tb_str += (
                    "\n\nHINT: You are using a STALE reference to a datablock that no longer "
                    "exists. This usually happens because a previous failed attempt was "
                    "automatically undone (deleting the objects it created) while you kept "
                    "the old reference, or because you deleted/replaced an object. "
                    "Re-fetch references fresh right before each use:\n"
                    "    obj = bpy.data.objects.get('Name')  # or bpy.context.active_object\n"
                    "    if obj is None:\n"
                    "        ...create or find it again...\n"
                    "Never reuse an object/material reference captured in an earlier tool call, "
                    "and guard lookups with try/except ReferenceError."
                )
            if "ShaderNodeBsdfPrincipled" in tb_str and "has no attribute" in tb_str and ("base_color" in tb_str or "Base Color" in tb_str):
                tb_str += (
                    "\n\nHINT: ShaderNodeBsdfPrincipled has NO `base_color` or `base_color_input" \
                    "` attribute. Access colors through the node's inputs dictionary:\n"
                    "    principled = nodes.new('ShaderNodeBsdfPrincipled')\n"
                    "    principled.inputs['Base Color'].default_value = (0.8, 0.2, 0.2, 1.0)\n"
                    "Other common inputs: 'Metallic', 'Roughness', 'Alpha', 'Emission Color'.\n"
                    "Run `print([i.name for i in principled.inputs])` to list all available inputs."
                )
            if 'Subdivision' in tb_str and 'has no attribute' in tb_str:
                tb_str += (
                    "\n\nHINT: Blender 5.3 changed subdivision modifier attributes. "
                    "Use print(dir(modifier)) to see available attributes. "
                    "Common names: levels (viewport), render_levels (render), "
                    "subdivision_type (CATMULL_CLARK or SIMPLE). "
                    "The old subdivisions attribute was renamed."
                )
            if "lamps" in tb_str and "has no attribute" in tb_str:
                tb_str += (
                    "\n\nHINT: 'BlendData' has no attribute 'lamps'. "
                    "It was renamed to 'lights' in Blender 4.0. "
                    "Use bpy.data.lights instead of bpy.data.lamps."
                )
            if '"EEVEE"' in tb_str and "not found in" in tb_str:
                tb_str += (
                    "\n\nHINT: The render engine name 'EEVEE' was renamed "
                    "to 'BLENDER_EEVEE' in Blender 4.0. "
                    "Use: scene.render.engine = 'BLENDER_EEVEE'"
                )
            if "RenderSettings" in tb_str and "'eevee'" in tb_str and "has no attribute" in tb_str:
                tb_str += (
                    "\n\nHINT: scene.render.eevee was moved to scene.eevee "
                    "in Blender 4.0. Use scene.eevee.use_ssr etc."
                )
            response: dict[str, object] = {"status": "error", "message": tb_str}
            if captured.stdout:
                response["stdout"] = captured.stdout
            if captured.stderr:
                response["stderr"] = captured.stderr
            return _ExecResult(response)

    # Check for a deferred response (background job in progress).
    check_fn_raw = namespace.get("check_is_finished")
    if check_fn_raw is not None and callable(check_fn_raw):
        check_fn: Callable[[], dict[str, object] | None] = check_fn_raw
        response = {}
        if captured.stdout:
            response["stdout"] = captured.stdout
        if captured.stderr:
            response["stderr"] = captured.stderr
        return _ExecResult(response, check_fn)

    result = namespace["result"]
    if not isinstance(result, dict):
        response = {
            "status": "error",
            "message": (
                "The `result` variable must be a dict, not {:s}. "
                "Wrap your return value: `result = {{\"key\": value}}`"
            ).format(type(result).__name__),
        }
    else:
        # Guard against LLM-generated code storing non-serializable values
        # such as Blender objects, e.g. `result = {"obj": bpy.context.active_object}`.
        # Without this, `json.dumps` fails inside `_encode_response`.
        if strict_json:
            try:
                json.dumps(result)
            except (TypeError, ValueError) as ex:
                response = {
                    "status": "error",
                    "message": "The `result` value is not JSON-serializable: {:s}".format(str(ex)),
                }
            else:
                response = {"status": "ok", "result": result}
        else:
            # Use `repr` as a fallback so non-serializable objects
            # (e.g. Blender ID types) appear as their string representation.
            result = json.loads(json.dumps(result, default=repr))
            response = {"status": "ok", "result": result}
    if captured.stdout:
        response["stdout"] = captured.stdout
    if captured.stderr:
        response["stderr"] = captured.stderr

    # Update bridge activity timestamp for liveness tracking.
    try:
        from . import agent_controller as _ac
        import time as _time
        _ac._agent_state.last_bridge_activity = _time.monotonic()
    except Exception:
        pass

    return _ExecResult(response)


def _execute_code_from_request(
        data: bytes,
) -> tuple[_ExecResult, bool]:
    """
    Parse a raw request and execute it.

    Return ``(exec_result, strict_json)``.
    """

    # NOTE: This function is not expected to raise exceptions because we control the MCP server, tool-code and add-on.
    # If there is an error, it will be handled by the caller (the LLM will get the stack trace).
    #
    # Even so, if a tool is misbehaving, or a change in the code causes an error,
    # give a "helpful" response - to avoid the hassles of searching about for the root cause.

    # Invalid JSON is not expected since the MCP server serializes requests with `json.dumps`.
    # Any error should be rare, the "default" exception path is fine.
    request = json.loads(data)

    if request.get("type") != "execute":
        return _ExecResult({
            "status": "error",
            "message": "Unknown request type: {!r}".format(request.get("type")),
        }), False
    code = request.get("code", "")

    # Not expected in normal use, but a clear message beats a cryptic trace-back,
    # Also make it clear where the error should be addressed.
    strict_json = request.get("strict_json")
    if not isinstance(strict_json, bool):
        return (
            _ExecResult({
                "status": "error",
                "message": (
                    "Internal error: a Coworker tool sent a request without the required 'strict_json' boolean key. "
                    "This is a bug in the tool that generated this code"
                ),
            }),
            False,
        )

    if _should_log_request():
        print("request:\n{:s}".format(code), file=sys.stderr)
    exec_result = _execute_code(code, strict_json=strict_json)
    _is_error = (
        exec_result.response.get("status") == "error"
        if not exec_result.check_fn
        else False
    )
    if _should_log_response(is_error=_is_error):
        if exec_result.check_fn is not None:
            print("response: deferred", file=sys.stderr)
        else:
            print("response: {:s}".format(json.dumps(exec_result.response, indent=2)), file=sys.stderr)

    return exec_result, strict_json


def _close_client(client: _Client) -> None:
    """
    Close a client connection and remove it from the active list.
    """
    try:
        client.conn.close()
    except Exception:  # pylint: disable=broad-exception-caught
        pass
    try:
        _state.clients.remove(client)
    except ValueError:
        pass


# ---------------------------------------------------------------------------
# Polling (called from the execution modules).

def _accept_clients() -> None:
    """
    Accept all pending connections on the listening socket.
    """
    if _state.sock is None:
        return
    while True:
        try:
            conn, _addr = _state.sock.accept()
            conn.setblocking(False)
            _state.clients.append(_Client(conn))
        except BlockingIOError:
            break
        except OSError:
            break


def _service_clients() -> bool:
    """
    Read from all connected clients, execute complete requests.

    Return ``True`` if at least one request was executed.
    """
    did_work = False
    # Iterate over a copy since clients may be removed during the loop.
    for client in _state.clients[:]:
        # Evict clients that have not sent a complete request in time.
        client.timeout -= 1
        if client.timeout <= 0:
            try:
                err: dict[str, object] = {
                    "status": "error",
                    "message": "Client timed out",
                }
                client.conn.sendall(_encode_response(err))
            except OSError:
                pass
            _close_client(client)
            continue

        try:
            chunk = client.conn.recv(_RECV_BUFFER_SIZE)
        except BlockingIOError:
            # No data available yet.
            continue
        except OSError:
            _close_client(client)
            continue

        if not chunk:
            # Client disconnected.
            _close_client(client)
            continue

        client.buffer.extend(chunk)

        # Guard against unbounded input from a misbehaving client.
        if len(client.buffer) > _MAX_REQUEST_BYTES:
            try:
                err = {
                    "status": "error",
                    "message": "Request exceeds {:d} byte limit".format(_MAX_REQUEST_BYTES),
                }
                client.conn.sendall(_encode_response(err))
            except OSError:
                pass
            _close_client(client)
            continue

        if b"\0" not in client.buffer:
            # Request not yet complete.
            continue

        # Execute the request and send the response.
        request_data = bytes(client.buffer[:client.buffer.index(b"\0")])
        try:
            exec_result, strict_json = _execute_code_from_request(request_data)
        except Exception:  # pylint: disable=broad-exception-caught
            exec_result = _ExecResult({"status": "error", "message": traceback.format_exc()})
            strict_json = False

        if exec_result.check_fn is not None:
            # Deferred response: hand the connection to deferred_tool.
            from . import deferred_tool
            deferred_tool.add(
                client.conn,
                exec_result.check_fn,
                strict_json,
                str(exec_result.response.get("stdout", "")),
                str(exec_result.response.get("stderr", "")),
            )
            # Remove from clients without closing the socket.
            try:
                _state.clients.remove(client)
            except ValueError:
                pass
        else:
            try:
                client.conn.sendall(_encode_response(exec_result.response))
            except OSError:
                pass
            _close_client(client)
        did_work = True

    return did_work


def poll() -> bool:
    """
    Non-blocking poll: accept new connections, service existing clients,
    and check deferred responses.

    Return ``True`` if work was done or deferred clients are pending.
    """
    from . import deferred_tool
    _accept_clients()
    did_work = _service_clients()
    if deferred_tool.poll():
        did_work = True
    # Stay in active polling mode while deferred clients exist.
    if deferred_tool.has_pending():
        did_work = True
    return did_work


def _handle_blocking_client(conn: socket.socket) -> bool:
    """
    Handle a single client connection synchronously with blocking I/O.

    Return ``True`` if a request was executed.
    """
    conn.settimeout(_CLIENT_TIMEOUT)
    try:
        buf = bytearray()
        while b"\0" not in buf:
            chunk = conn.recv(_RECV_BUFFER_SIZE)
            if not chunk:
                # Client disconnected.
                return False
            buf.extend(chunk)
            if len(buf) > _MAX_REQUEST_BYTES:
                err: dict[str, object] = {
                    "status": "error",
                    "message": "Request exceeds {:d} byte limit".format(_MAX_REQUEST_BYTES),
                }
                conn.sendall(_encode_response(err))
                return False

        request_data = bytes(buf[:buf.index(b"\0")])
        try:
            exec_result, _strict_json = _execute_code_from_request(request_data)
            if exec_result.check_fn is not None:
                # Unpack to preserve stdout/stderr captured before the deferred handler was set up.
                response = {**exec_result.response, "status": "error", "message": _DEFERRED_UNSUPPORTED_MESSAGE}
                exec_result = _ExecResult(response)
        except Exception:  # pylint: disable=broad-exception-caught
            exec_result = _ExecResult({"status": "error", "message": traceback.format_exc()})
        conn.sendall(_encode_response(exec_result.response))
        return True
    except socket.timeout:
        try:
            err = {"status": "error", "message": "Client timed out"}
            conn.sendall(_encode_response(err))
        except OSError:
            pass
        return False
    except OSError:
        return False
    finally:
        conn.close()


def poll_blocking(timeout: float = _POLL_BLOCKING_TIMEOUT) -> bool:
    """
    Block until a connection arrives (up to *timeout* seconds), then
    handle it synchronously with blocking I/O.

    For use in background mode where the GUI is not running.
    Return ``True`` if a request was executed.
    """
    if _state.sock is None:
        return False

    try:
        readable, _writable, _errored = select.select([_state.sock], [], [], timeout)
    except (OSError, ValueError):
        return False

    if not readable:
        return False

    try:
        conn, _addr = _state.sock.accept()
    except (BlockingIOError, OSError):
        return False

    return _handle_blocking_client(conn)


# ---------------------------------------------------------------------------
# Public API.

def start(host: str, port: int) -> None:
    """
    Bind the listening socket and begin accepting connections.

    This does not block. The caller must arrange for ``poll`` to be
    called periodically (see ``execute_interactive`` and
    ``execute_blocking``).

    If *port* is in use, the function automatically tries the next
    available port (``port + 1``, ``port + 2``, … up to +100) and
    stores the actual port in ``_actual_port``.

    Callers should catch ``Exception`` broadly rather than specific types,
    since failures may be:
    - ``RuntimeError``, e.g. server already running.
    - ``OSError``, e.g. address already in use.
    ...other exceptions that are difficult to predict exhaustively.
    """

    # Track the actual port we end up binding.
    global _actual_port
    _actual_port = port

    if is_running():
        raise RuntimeError("Server is already running")

    # Pre-bind conflict detection: try connecting to the target port first.
    # If it succeeds, another Blender instance (or another process) already
    # owns this port.  We check *before* binding so we can give a clear
    # error message instead of silently sharing the port (which happens
    # with SO_REUSEADDR on Windows).
    _probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        _probe.settimeout(0.5)
        _probe.connect((host, port))
        _probe.close()
        # Port is in use — auto-shuffle to the next available port.
        new_port = _find_available_port(port)
        if new_port == 0:
            raise OSError(
                "Port {:d} is in use and no subsequent port is available. "
                "Increase port_offset in Preferences (Advanced tab) to use a "
                "different set of ports.".format(port)
            )
        print("[🛠️Coworker] start: port {:d} in use — shuffled to {:d}".format(port, new_port))
        port = new_port
        _actual_port = port
    except (ConnectionRefusedError, TimeoutError, OSError):
        # Expected — port is free (connection refused or timed out).
        _probe.close()

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if sys.platform == "win32":
            # SO_EXCLUSIVEADDRUSE prevents a second process from binding
            # the same port, even with SO_REUSEADDR.  Without this, a
            # second Blender instance would silently share port 9876 and
            # the OS would non-deterministically route code-execution
            # requests to the wrong instance.
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setblocking(False)
        sock.bind((host, port))
        sock.listen(_LISTEN_BACKLOG)
    except OSError:
        sock.close()
        raise

    _state.sock = sock


def stop() -> None:
    """
    Close the listening socket, all client connections, and deferred responses.
    """
    from . import deferred_tool

    sock = _state.sock
    _state.sock = None
    if sock is not None:
        try:
            sock.close()
        except Exception:  # pylint: disable=broad-exception-caught
            pass

    for client in _state.clients:
        try:
            client.conn.close()
        except Exception:  # pylint: disable=broad-exception-caught
            pass
    _state.clients.clear()

    deferred_tool.close_all()


def is_running() -> bool:
    """
    Return whether the server is currently listening.
    """
    return _state.sock is not None
