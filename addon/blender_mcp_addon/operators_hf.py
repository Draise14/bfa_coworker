# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Operators for HuggingFace cache management.
"""

__all__ = (
    "_BLMCP_OT_open_hf_cache",
    "_BLMCP_OT_clear_hf_cache",
)

import bpy  # pylint: disable=import-error

import os
import shutil


class _BLMCP_OT_open_hf_cache(bpy.types.Operator):  # type: ignore[misc]
    bl_idname = "blmcp.open_hf_cache"
    bl_label = "Open HF Cache"
    bl_description = "Open the HuggingFace cache folder where models are cached"

    def execute(self, context: bpy.types.Context) -> set[str]:
        hf_home = os.environ.get("HF_HOME") or os.environ.get("HF_HUB_CACHE")
        if hf_home:
            hf_cache = str(os.path.join(hf_home, "hub"))
        else:
            hf_cache = str(os.path.expanduser("~/.cache/huggingface/hub"))
        import webbrowser
        webbrowser.open(hf_cache)
        self.report({"INFO"}, "Opened {:s}".format(hf_cache))
        return {"FINISHED"}


class _BLMCP_OT_clear_hf_cache(bpy.types.Operator):  # type: ignore[misc]
    bl_idname = "blmcp.clear_hf_cache"
    bl_label = "Clear HF Cache"
    bl_description = "Delete all cached HuggingFace models (frees disk space)"

    def execute(self, context: bpy.types.Context) -> set[str]:
        hf_home = os.environ.get("HF_HOME") or os.environ.get("HF_HUB_CACHE")
        if hf_home:
            hf_cache = os.path.join(hf_home, "hub")
        else:
            hf_cache = os.path.expanduser("~/.cache/huggingface/hub")

        if not os.path.isdir(hf_cache):
            self.report({"INFO"}, "HF cache is already empty")
            return {"FINISHED"}

        # Count what's being deleted.
        total_bytes = 0
        for root, _dirs, files in os.walk(hf_cache):
            for f in files:
                fp = os.path.join(root, f)
                try:
                    total_bytes += os.path.getsize(fp)
                except OSError:
                    pass

        try:
            shutil.rmtree(hf_cache)
        except OSError as ex:
            self.report({"ERROR"}, "Failed to clear cache: {:s}".format(str(ex)))
            return {"CANCELLED"}

        # Recreate the empty directory so HF tools don't break.
        os.makedirs(hf_cache, exist_ok=True)

        if total_bytes > 0:
            size_str = "{:.1f} GB".format(total_bytes / (1024 ** 3)) if total_bytes >= 1024 ** 3 else "{:.0f} MB".format(total_bytes / (1024 ** 2))
            self.report({"INFO"}, "Cleared {:s} from HF cache".format(size_str))
        else:
            self.report({"INFO"}, "HF cache cleared")
        return {"FINISHED"}