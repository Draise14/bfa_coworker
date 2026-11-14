"""Video 0 spike (#374): render the 16:9 test movie for the Sequencer probes.

Run in background:  blender -b --factory-startup --python make_test_movie.py -- <speech.wav> <out.mp4>
A 1920x1080, 24 fps H.264 + AAC movie, as long as the speech clip: a dark background, an
off-centre "subject" (a coloured block that drifts left and right, so a vertical reframe has
something to follow) and a title, with the speech muxed in. Made from Blender's own strips,
so no media is downloaded.
"""
import sys
import time

import bpy

argv = sys.argv[sys.argv.index("--") + 1:]
WAV, OUT = argv[0], argv[1]

scene = bpy.context.scene
scene.render.resolution_x, scene.render.resolution_y = 1920, 1080
scene.render.resolution_percentage = 100
scene.render.fps = 24
ed = scene.sequence_editor_create()
snd = ed.strips.new_sound("speech", WAV, 1, 1)
end = snd.frame_final_end
scene.frame_start, scene.frame_end = 1, end - 1

bg = ed.strips.new_effect("bg", "COLOR", 2, 1, length=end - 1)
bg.color = (0.08, 0.09, 0.12)
subj = ed.strips.new_effect("subject", "COLOR", 3, 1, length=end - 1)
subj.color = (0.85, 0.45, 0.2)
subj.blend_type = "ALPHA_OVER"
subj.transform.scale_x, subj.transform.scale_y = 0.18, 0.6
for f, x in ((1, -300), (end // 3, 250), (2 * end // 3, -150), (end - 1, 300)):
    subj.transform.offset_x = x
    subj.transform.keyframe_insert("offset_x", frame=f)
title = ed.strips.new_effect("title", "TEXT", 4, 1, length=end - 1)
title.text = "Coworker test clip"
title.font_size = 64
title.location = (0.5, 0.9)

r = scene.render
if hasattr(r.image_settings, "media_type"):  # 5.x splits image / video formats
    r.image_settings.media_type = "VIDEO"
r.image_settings.file_format = "FFMPEG"
r.ffmpeg.format = "MPEG4"
r.ffmpeg.codec = "H264"
r.ffmpeg.audio_codec = "AAC"
r.ffmpeg.audio_channels = "STEREO"
r.ffmpeg.audio_mixrate = 48000
r.filepath = OUT
t = time.perf_counter()
bpy.ops.render.render(animation=True)
print("MOVIE", OUT, "frames", scene.frame_end, "render_s", round(time.perf_counter() - t, 1))
