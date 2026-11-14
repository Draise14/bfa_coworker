# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Operators for the Speech tab (#375): download whisper-server and models, start / stop it.

The work runs on whisper_manager's own threads; these only start it and keep the
Preferences redrawn while a download or a start is under way.
"""

__all__ = (
    "BFACW_OT_speech_download_server",
    "BFACW_OT_speech_download_model",
    "BFACW_OT_speech_cancel_download",
    "BFACW_OT_speech_start",
    "BFACW_OT_speech_stop",
    "BFACW_OT_speech_open_folder",
    "CLASSES",
)

import bpy  # pylint: disable=import-error
from bpy.props import StringProperty  # pylint: disable=import-error

from . import whisper_manager


def _prefs(context):
    addon = context.preferences.addons.get(__package__)
    return addon.preferences if addon is not None else None


def _offline(op) -> bool:
    """Blender's "Allow Online Access" is off: say so instead of downloading."""
    if getattr(bpy.app, "online_access", True):
        return False
    from .shared import STATE_OFFLINE_ERROR_MESSAGE
    op.report({'ERROR'}, STATE_OFFLINE_ERROR_MESSAGE)
    return True


def _redraw_prefs() -> float | None:
    """Timer: redraw the Preferences while something runs; one last redraw after."""
    for wm in bpy.data.window_managers:
        for win in wm.windows:
            for area in win.screen.areas:
                if area.type == 'PREFERENCES':
                    area.tag_redraw()
    st = whisper_manager.get_state()
    return 0.5 if (st.download_active or st.starting) else None


def _keep_redrawing() -> None:
    if not bpy.app.timers.is_registered(_redraw_prefs):
        bpy.app.timers.register(_redraw_prefs, first_interval=0.3)


class BFACW_OT_speech_download_server(bpy.types.Operator):  # type: ignore[misc]
    bl_idname = "bfacw.speech_download_server"
    bl_label = "Download whisper-server"
    bl_description = ("Download whisper.cpp's speech server from its GitHub releases: the CPU build "
                      "(9 MB), plus the CUDA 12 build (about 690 MB) with an NVIDIA GPU on Auto")

    def execute(self, context):
        if _offline(self):
            return {'CANCELLED'}
        prefs = _prefs(context)
        device = getattr(prefs, "speech_device", "AUTO") if prefs is not None else "AUTO"
        if not whisper_manager.start_download("server", device):
            self.report({'WARNING'}, whisper_manager.get_state().error or "A download is already running")
            return {'CANCELLED'}
        _keep_redrawing()
        return {'FINISHED'}


class BFACW_OT_speech_download_model(bpy.types.Operator):  # type: ignore[misc]
    bl_idname = "bfacw.speech_download_model"
    bl_label = "Download Speech Model"
    bl_description = "Download this whisper model from Hugging Face (ggerganov/whisper.cpp)"

    model_id: StringProperty(name="Model", default="small")  # type: ignore[valid-type]

    def execute(self, context):
        del context
        if _offline(self):
            return {'CANCELLED'}
        if whisper_manager.get_preset(self.model_id) is None:
            self.report({'ERROR'}, "Unknown speech model {:s}".format(self.model_id))
            return {'CANCELLED'}
        if not whisper_manager.start_download("model", self.model_id):
            self.report({'WARNING'}, "A download is already running")
            return {'CANCELLED'}
        _keep_redrawing()
        return {'FINISHED'}


class BFACW_OT_speech_cancel_download(bpy.types.Operator):  # type: ignore[misc]
    bl_idname = "bfacw.speech_cancel_download"
    bl_label = "Cancel Download"
    bl_description = "Stop the speech download (the partial file is deleted)"

    def execute(self, context):
        del context
        whisper_manager.cancel_download()
        _keep_redrawing()
        return {'FINISHED'}


class BFACW_OT_speech_start(bpy.types.Operator):  # type: ignore[misc]
    bl_idname = "bfacw.speech_start"
    bl_label = "Start Speech Server"
    bl_description = "Start whisper-server with the chosen model and device"

    def execute(self, context):
        from .shared import effective_whisper_port
        prefs = _prefs(context)
        device = getattr(prefs, "speech_device", "AUTO") if prefs is not None else "AUTO"
        build = whisper_manager.resolve_build(device)
        model = whisper_manager.resolve_model(getattr(prefs, "speech_model", "AUTO") if prefs else "AUTO", build)
        problem = whisper_manager.start_server(model, build, effective_whisper_port(prefs))
        if problem:
            self.report({'ERROR'}, problem)
            return {'CANCELLED'}
        _keep_redrawing()
        return {'FINISHED'}


class BFACW_OT_speech_stop(bpy.types.Operator):  # type: ignore[misc]
    bl_idname = "bfacw.speech_stop"
    bl_label = "Stop Speech Server"
    bl_description = "Stop whisper-server (frees its memory; it starts again when needed)"

    def execute(self, context):
        del context
        whisper_manager.stop_server()
        _keep_redrawing()
        return {'FINISHED'}


class BFACW_OT_speech_open_folder(bpy.types.Operator):  # type: ignore[misc]
    bl_idname = "bfacw.speech_open_folder"
    bl_label = "Open Speech Folder"
    bl_description = "Open the folder with whisper-server and the speech models"

    def execute(self, context):
        del context
        bpy.ops.wm.path_open(filepath=str(whisper_manager.data_dir()))
        return {'FINISHED'}


CLASSES = (
    BFACW_OT_speech_download_server,
    BFACW_OT_speech_download_model,
    BFACW_OT_speech_cancel_download,
    BFACW_OT_speech_start,
    BFACW_OT_speech_stop,
    BFACW_OT_speech_open_folder,
)
