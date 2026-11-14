# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
The fixed sample timeline for the ``vse`` evaluation questions (``vse.xml``, #304 / #323).

Builds it in the open file (scene "Edit"), so the questions have one answer each.  Media
are made here, nothing is downloaded.  Run it in Blender 5.2+ and save, e.g.::

    blender --background --factory-startup --python tests/mcp_evals/vse_sample_scene.py -- out_dir

which writes ``out_dir/vse_sample.blend`` (with ``logo.png`` and ``tone.wav`` beside it).
"""

import math
import os
import struct
import sys
import wave

import bpy


def build(out_dir: str) -> bpy.types.Scene:
    os.makedirs(out_dir, exist_ok=True)
    scene = bpy.context.scene
    scene.name = "Edit"
    scene.render.resolution_x, scene.render.resolution_y = 1920, 1080
    scene.render.fps, scene.render.fps_base = 25, 1.0
    scene.frame_start, scene.frame_end = 1, 250
    scene.frame_current = 1
    ed = scene.sequence_editor_create()
    strips = ed.strips

    logo = os.path.join(out_dir, "logo.png")
    img = bpy.data.images.new("logo", 128, 64)
    img.pixels[:] = [1.0, 0.8, 0.0, 1.0] * (128 * 64)
    img.filepath_raw, img.file_format = logo, "PNG"
    img.save()
    bpy.data.images.remove(img)
    tone = os.path.join(out_dir, "tone.wav")
    with wave.open(tone, "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(8000)
        fh.writeframes(b"".join(struct.pack("<h", int(6000 * math.sin(i * 0.06))) for i in range(8000 * 10)))

    intro = strips.new_effect(name="Intro BG", type="COLOR", channel=1, frame_start=1, length=75)
    intro.color = (0.05, 0.1, 0.4)
    main = strips.new_effect(name="Main BG", type="COLOR", channel=1, frame_start=90, length=161)
    main.color = (0.3, 0.3, 0.3)
    warm = main.modifiers.new(name="Warm", type="COLOR_BALANCE")
    warm.color_balance.gain = (1.2, 1.0, 0.8)
    title = strips.new_effect(name="Title", type="TEXT", channel=2, frame_start=10, length=50)
    title.text, title.font_size = "Coworker Demo", 96
    lower = strips.new_effect(name="Lower Third", type="TEXT", channel=2, frame_start=120, length=60)
    lower.text = "Ana Silva - Director"
    picture = strips.new_image(name="Logo", filepath=logo, channel=3, frame_start=200)
    picture.right_handle = 251
    picture.blend_alpha = 0.8
    music = strips.new_sound(name="Music", filepath=tone, channel=4, frame_start=1)
    music.volume = 0.5
    ed.channels[4].name = "Audio"
    flash = strips.new_effect(name="Flash", type="COLOR", channel=5, frame_start=140, length=10)
    flash.mute = True
    credits = strips.new_meta(name="Credits", channel=6, frame_start=230)
    inner = credits.strips.new_effect(name="Credit Text", type="TEXT", channel=1, frame_start=230, length=21)
    inner.text = "Thanks for watching"
    credits.right_handle = 251      # a meta made from Python keeps its first one-frame range
    scene.timeline_markers.new("Cut A", frame=76)
    scene.timeline_markers.new("Cut B", frame=120)
    ed.active_strip = lower
    return scene


if __name__ == "__main__":
    out = sys.argv[sys.argv.index("--") + 1] if "--" in sys.argv else bpy.app.tempdir
    build(out)
    if bpy.app.background:
        bpy.ops.wm.save_as_mainfile(filepath=os.path.join(out, "vse_sample.blend"))
