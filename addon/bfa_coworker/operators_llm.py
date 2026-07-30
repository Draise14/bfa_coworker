# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Operators for local LLM management: download, start/stop, scan, select.
"""

__all__ = (
    "_BFACW_OT_download_model",
    "_BFACW_OT_cancel_download",
    "_BFACW_OT_start_llm",
    "_BFACW_OT_stop_llm",
    "_BFACW_OT_download_llama_server",
    "_BFACW_OT_scan_existing_models",
    "_BFACW_OT_select_preset",
    "_BFACW_OT_select_existing_model",
)

import bpy  # pylint: disable=import-error
from bpy.props import StringProperty  # pylint: disable=import-error

import os
import threading
from pathlib import Path

from .shared import effective_ports, get_llm_manager


# ---------------------------------------------------------------------------
# Download Model

class _BFACW_OT_download_model(bpy.types.Operator):  # type: ignore[misc]
    bl_idname = "bfacw.download_model"
    bl_label = "Download Model"
    bl_description = "Download the configured GGUF model from HuggingFace and start llama-server"

    _timer: float | None = None
    _thread = None
    _error: str = ""
    _done: bool = False
    _start_msg_shown: bool = False
    _latest_progress: str = ""
    _model_dest: str = ""  # Path to check after download

    def modal(self, context: bpy.types.Context, event: bpy.types.Event) -> set[str]:
        del event

        llm = get_llm_manager()
        state = llm.get_state()

        if not self._start_msg_shown:
            self.report({"INFO"}, "Download started — see Preferences for progress")
            self._start_msg_shown = True

        # Show progress if it changed.
        prog = state.download_progress
        if prog and prog != self._latest_progress:
            self._latest_progress = prog
            if "error" in prog.lower() or "timed out" in prog.lower():
                self.report({"ERROR"}, prog)
            elif "complete" in prog.lower():
                self.report({"INFO"}, prog)

        # Surface errors from the error field (not just progress text).
        if state.error and state.error != self._error:
            self._error = state.error
            self.report({"ERROR"}, state.error)

        if not self._done:
            # Re-draw preferences so the progress label updates.
            for wm in bpy.data.window_managers:
                for win in wm.windows:
                    for area in win.screen.areas:
                        if area.type == 'PREFERENCES':
                            area.tag_redraw()
            return {'PASS_THROUGH'}

        if self._timer is not None:
            bpy.app.timers.unregister(self._timer)

        # Redraw all PREFERENCES areas so the status updates immediately.
        for wm in bpy.data.window_managers:
            for win in wm.windows:
                for area in win.screen.areas:
                    if area.type == 'PREFERENCES':
                        area.tag_redraw()

        if state.is_running and not state.error:
            self.report({"INFO"}, "Model downloaded and llama-server is running")
            return {"FINISHED"}
        # If the model file was downloaded, report success even if there's
        # a stale error from a transient direct-download failure.
        model_path = Path(self._model_dest) if self._model_dest else None
        if model_path and model_path.exists():
            self.report({"INFO"}, "Model downloaded to {:s}".format(str(model_path)))
            return {"FINISHED"}
        if self._error:
            self.report({"ERROR"}, self._error)
            return {"CANCELLED"}
        self.report({"ERROR"}, self._error or "Download failed")
        return {"CANCELLED"}

    def execute(self, context: bpy.types.Context) -> set[str]:
        llm = get_llm_manager()
        prefs = context.preferences.addons[__package__].preferences

        cfg = llm.get_config()
        cfg.model_repo_id = prefs.model_repo_id
        cfg.model_filename = prefs.model_filename
        cfg.downloaded_models_dir = prefs.downloaded_models_dir
        cfg.local_ctx_size = prefs.local_ctx_size
        cfg.hf_token = prefs.hf_token
        _bridge_port, _mcp_port, _llm_port = effective_ports(prefs)
        cfg.local_port = _llm_port
        llm.set_config(cfg)

        # Store expected model path for completion check.
        models_dir = Path(prefs.downloaded_models_dir) if prefs.downloaded_models_dir else (Path.home() / "bfa_coworker_models")
        if prefs.model_filename:
            self._model_dest = str(models_dir / prefs.model_filename)

        self._done = False
        self._error = ""
        self._start_msg_shown = False
        self._latest_progress = ""

        # download_model returns None immediately — we poll state for completion.
        llm.download_model(progress_callback=None)

        self._timer = bpy.app.timers.register(
            _make_download_poll(self),
            first_interval=0.5,
            persistent=True,
        )

        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}


def _make_download_poll(op):
    def _poll() -> float | None:
        llm = get_llm_manager()
        state = llm.get_state()
        # Download is done when the active flag clears AND
        # either the server is running, the model file exists, or there was an error.
        if not state.download_active:
            op._done = True
            op._error = state.error
            # If no error but model file exists, check if we should auto-set existing_model_path.
            if not state.error and op._model_dest and Path(op._model_dest).exists():
                pass  # The model file is there — download succeeded.
            return None
        for wm in bpy.data.window_managers:
            for win in wm.windows:
                for area in win.screen.areas:
                    if area.type == 'PREFERENCES':
                        area.tag_redraw()
        return 0.5
    return _poll


# ---------------------------------------------------------------------------
# Cancel Download

class _BFACW_OT_cancel_download(bpy.types.Operator):  # type: ignore[misc]
    bl_idname = "bfacw.cancel_download"
    bl_label = "Cancel Download"
    bl_description = "Cancel the in-progress model download"

    def execute(self, context: bpy.types.Context) -> set[str]:
        del context
        llm = get_llm_manager()
        llm.cancel_download()
        self.report({"INFO"}, "Cancelling download...")
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# Start Local LLM

class _BFACW_OT_start_llm(bpy.types.Operator):  # type: ignore[misc]
    bl_idname = "bfacw.start_llm"
    bl_label = "Start Local LLM"
    bl_description = "Start the local llama-server with the configured model"

    def execute(self, context: bpy.types.Context) -> set[str]:
        llm = get_llm_manager()
        prefs = context.preferences.addons[__package__].preferences

        cfg = llm.get_config()
        cfg.llama_path = prefs.llama_path
        cfg.model_repo_id = prefs.model_repo_id
        cfg.model_filename = prefs.model_filename
        cfg.downloaded_models_dir = prefs.downloaded_models_dir
        cfg.local_ctx_size = prefs.local_ctx_size
        cfg.hf_token = prefs.hf_token
        _bridge_port, _mcp_port, _llm_port = effective_ports(prefs)
        cfg.local_port = _llm_port
        llm.set_config(cfg)

        def _do_start():
            # If an existing model path is set, use it directly.
            existing_path = prefs.existing_model_path
            if existing_path and os.path.isfile(existing_path):
                proc = llm.start_local_llama(model_path=existing_path)
            else:
                proc = llm.start_local_llama()
            if proc is None:
                return  # Error already set by start_local_llama.
            # Wait for the server to become healthy and surface the result.
            ready = llm.wait_until_ready(timeout=120.0, proc=proc)
            state = llm.get_state()
            if ready:
                state_error = ""
            else:
                state_error = state.error or "llama-server failed to start"
            # Report back on the main thread via a timer.
            def _report():
                # Redraw preferences so the status updates.
                for wm in bpy.data.window_managers:
                    for win in wm.windows:
                        for area in win.screen.areas:
                            if area.type == 'PREFERENCES':
                                area.tag_redraw()
                return None
            bpy.app.timers.register(_report, first_interval=0.1)
            if state_error:
                print("[🛠️Coworker] start_llm: {:s}".format(state_error))

        thread = threading.Thread(target=_do_start, daemon=True)
        thread.start()

        self.report({"INFO"}, "Starting llama-server in background...")
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# Stop Local LLM

class _BFACW_OT_stop_llm(bpy.types.Operator):  # type: ignore[misc]
    bl_idname = "bfacw.stop_llm"
    bl_label = "Stop Local LLM"
    bl_description = "Stop the local llama-server"

    def execute(self, context: bpy.types.Context) -> set[str]:
        del context
        llm = get_llm_manager()
        llm.stop_local_llama()
        self.report({"INFO"}, "llama-server stopped")
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# Download llama-server

class _BFACW_OT_download_llama_server(bpy.types.Operator):  # type: ignore[misc]
    bl_idname = "bfacw.download_llama_server"
    bl_label = "Download llama-server"
    bl_description = "Download and install the llama-server binary from GitHub releases"

    _timer: float | None = None
    _thread: threading.Thread | None = None
    _done: bool = False
    _error: str = ""
    _latest_progress: str = ""

    def modal(self, context: bpy.types.Context, event: bpy.types.Event) -> set[str]:
        del event

        llm = get_llm_manager()
        state = llm.get_state()

        # Show progress if it changed.
        prog = state.download_progress
        if prog and prog != self._latest_progress:
            self._latest_progress = prog
            if "Error" in prog or "fail" in prog.lower():
                self.report({"ERROR"}, prog)
            elif "installed" in prog.lower() or "already" in prog.lower():
                self.report({"INFO"}, prog)

        if not self._done:
            for wm in bpy.data.window_managers:
                for win in wm.windows:
                    for area in win.screen.areas:
                        if area.type == 'PREFERENCES':
                            area.tag_redraw()
            return {'PASS_THROUGH'}

        if self._timer is not None:
            bpy.app.timers.unregister(self._timer)

        # Redraw all PREFERENCES areas so the status updates immediately.
        for wm in bpy.data.window_managers:
            for win in wm.windows:
                for area in win.screen.areas:
                    if area.type == 'PREFERENCES':
                        area.tag_redraw()

        if self._error:
            self.report({"ERROR"}, self._error)
            return {"CANCELLED"}
        else:
            self.report({"INFO"}, "llama-server downloaded and installed")
            return {"FINISHED"}

    def execute(self, context: bpy.types.Context) -> set[str]:
        llm = get_llm_manager()

        # Check if already installed.
        existing = llm.find_llama_server()
        if existing:
            self.report({"INFO"}, "llama-server already available at: {:s}".format(existing))
            return {"FINISHED"}

        self._done = False
        self._error = ""
        self._latest_progress = ""

        def _do_download():
            result = llm.download_llama_server()
            if result is None:
                self._error = llm.get_state().error or "Download failed"
            self._done = True

        self._thread = threading.Thread(target=_do_download, daemon=True)
        self._thread.start()

        self._timer = bpy.app.timers.register(
            _make_llama_download_poll(self),
            first_interval=0.5,
            persistent=True,
        )

        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}


def _make_llama_download_poll(op):
    def _poll() -> float | None:
        if op._done:
            return None
        for wm in bpy.data.window_managers:
            for win in wm.windows:
                for area in win.screen.areas:
                    if area.type == 'PREFERENCES':
                        area.tag_redraw()
        return 0.5
    return _poll


# ---------------------------------------------------------------------------
# Scan Existing Models

class _BFACW_OT_scan_existing_models(bpy.types.Operator):  # type: ignore[misc]
    """Scan for existing GGUF models and populate the existing_model_path."""
    bl_idname = "bfacw.scan_existing_models"
    bl_label = "Scan for Models"
    bl_description = "Scan the models directory and HuggingFace cache for GGUF model files"

    _models: list[dict] = []
    _scan_done: bool = False

    def execute(self, context: bpy.types.Context) -> set[str]:
        llm = get_llm_manager()
        prefs = context.preferences.addons[__package__].preferences

        _BFACW_OT_scan_existing_models._scan_done = False

        def _do_scan():
            models = llm.scan_existing_models(models_dir=prefs.downloaded_models_dir)
            _BFACW_OT_scan_existing_models._models = models
            _BFACW_OT_scan_existing_models._scan_done = True

        self.report({"INFO"}, "Scanning for models...")
        thread = threading.Thread(target=_do_scan, daemon=True)
        thread.start()

        # Poll for completion, then show results.
        bpy.app.timers.register(
            _scan_poll_timer(context),
            first_interval=0.25,
            persistent=True,
        )
        return {"FINISHED"}


def _scan_poll_timer(context: bpy.types.Context):
    """Return a timer callback that shows scan results when done."""
    # Capture stable references before the closure.
    wm = context.window_manager

    def _poll() -> float | None:
        if not _BFACW_OT_scan_existing_models._scan_done:
            return 0.25  # Keep polling
        models = _BFACW_OT_scan_existing_models._models

        def _show_menu():
            if not models:
                def _empty_menu(_s, _c):
                    _s.layout.label(text="No GGUF models found.", icon='INFO')
                wm.popup_menu(_empty_menu, title="Scan Results", icon='FILE_FOLDER')
            else:
                def _draw_menu(_s, _c):
                    layout = _s.layout
                    layout.label(text="Found {:d} model(s):".format(len(models)), icon='INFO')
                    for m in models:
                        src_icon = 'FILE_FOLDER' if m["source"] == "models_dir" else 'URL'
                        op = layout.operator(
                            "bfacw.select_existing_model",
                            text="[{:s}] {:s} ({:s})".format(m["source"], m["filename"], m["size_gb"]),
                            icon=src_icon,
                        )
                        op.model_path = m["path"]
                wm.popup_menu(_draw_menu, title="Select Existing Model", icon='FILE_FOLDER')

            # Redraw preferences.
            for w in bpy.data.window_managers:
                for win in w.windows:
                    for area in win.screen.areas:
                        if area.type == 'PREFERENCES':
                            area.tag_redraw()

        bpy.app.timers.register(_show_menu, first_interval=0.1)
        return None
    return _poll


# ---------------------------------------------------------------------------
# Select Preset

class _BFACW_OT_select_preset(bpy.types.Operator):  # type: ignore[misc]
    """Select a model preset from the categorized visual list."""
    bl_idname = "bfacw.select_preset"
    bl_label = "Select Preset"
    bl_description = "Select this recommended model preset"

    preset_id: StringProperty(  # type: ignore[valid-type]
        name="Preset ID",
        default="",
    )

    def execute(self, context: bpy.types.Context) -> set[str]:
        prefs = context.preferences.addons[__package__].preferences
        if self.preset_id:
            prefs.model_preset = self.preset_id
            # Trigger the update handler manually since EnumProperty
            # assignment doesn't always fire the callback on all platforms.
            prefs._update_model_preset(context)
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# Select Existing Model

class _BFACW_OT_select_existing_model(bpy.types.Operator):  # type: ignore[misc]
    """Select a model from the scan results and set it as the active model."""
    bl_idname = "bfacw.select_existing_model"
    bl_label = "Use This Model"
    bl_description = "Use the selected model file directly"

    model_path: StringProperty(  # type: ignore[valid-type]
        name="Model Path",
        default="",
    )

    def execute(self, context: bpy.types.Context) -> set[str]:
        llm = get_llm_manager()
        prefs = context.preferences.addons[__package__].preferences
        if not self.model_path or not os.path.isfile(self.model_path):
            self.report({"ERROR"}, "Model file not found: {:s}".format(self.model_path))
            return {"CANCELLED"}

        # Set the existing model path and clear preset selection.
        prefs.existing_model_path = self.model_path
        prefs.model_preset = "_custom"
        # Keep repo_id/filename so the model is identifiable.
        # model_filename is always the basename.
        prefs.model_filename = os.path.basename(self.model_path)
        # Sync to llm_manager config immediately.
        cfg = llm.get_config()
        cfg.model_filename = os.path.basename(self.model_path)
        cfg.downloaded_models_dir = prefs.downloaded_models_dir
        cfg.local_ctx_size = prefs.local_ctx_size
        llm.set_config(cfg)
        self.report(
            {"INFO"},
            "Using existing model: {:s}".format(os.path.basename(self.model_path)),
        )
        return {"FINISHED"}