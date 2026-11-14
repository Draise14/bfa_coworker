# Video 0 spike (#374)

Throwaway measuring scripts for `_misc/plan_tier4v_video_sequencer.md` §6. Not shipped,
nothing here is imported by the add-on. Run on 2026-11-14 in **Blender 5.2.4 portable**
(Windows 11, Ryzen 9 7950X, RTX 4090); Bforartists 5.3 is the maintainer's check.
No media, binaries or models are kept in the repo: everything below makes or downloads
them into an outside folder.

| Script | Runs in | What it does / measures |
|---|---|---|
| `make_speech_clip.ps1` | PowerShell (System.Speech) | A 125 s speech clip with pauses (15 sentences, 0.4–4 s gaps): `speech_16k_mono.wav`, `speech_44k_stereo.wav`, and `<wav>.words.tsv` with the synthesizer's own word-start times (ground truth; the 44.1 kHz file's reported positions are wrong, use the 16 kHz one for both) |
| `make_test_movie.py` | Blender `-b` | `talking_16x9.mp4`: 1920×1080, 24 fps, H.264 + AAC, the speech muxed in, a drifting "subject" block and a title (56 s render) |
| `sequencer_probe.py` | Blender `-b` and UI | Strips API, frame properties, compositor strip modifier (create / attach / per-modifier inputs / keyframes / 1080×1920 render and preview timings), text strip keyframes and style names, `export_subtitles`, `add_scene_strip_from_scene_asset`, `aud` samples, silence finder, 16 kHz WAV writing, `sound.mixdown`, `render.opengl(sequencer=True)` |
| `sequencer_ui_ops_probe.py` | Blender UI | The two window-only operators: what `export_subtitles` polls on, and `add_scene_strip_from_scene_asset` EXEC vs INVOKE; the `libraries.load` + `new_scene` fallback |
| `whisper_bench.py` | `python -I` | whisper-cli per build / model / mode (token times, DTW, VAD, 44.1 kHz input, mp4 input), wall time, word-start error against the ground truth |
| `whisper_server_probe.py` | `python -I` | whisper-server start-up → `/health` readiness, two `/inference` requests per build / model |
| `transcript_from_whisper.py` | `python -I` | The frozen conversion: whisper-server `verbose_json` → `bfa_coworker.transcript/1` (+ error vs ground truth) |
| `sample_transcript.json` | — | Real output of the frozen route (CUDA, large-v3-turbo) on the test clip |
| `results/` | — | Raw results: `seq_bg.json`, `seq_ui.json`, `sequencer_ui_ops.log`, `export_subtitles_sample.srt`, `bench1-3.jsonl`, `server_probe.json` (the 44k row in `bench1.jsonl` was scored against the wrong reference; `bench2.jsonl` has it right) |

## Rerun

```sh
OUT=F:/bfa_coworker_wt/_scratch/spike374         # anything outside the repo
powershell -NoProfile -ExecutionPolicy Bypass -File make_speech_clip.ps1 "$OUT/media"
blender -b --factory-startup --python make_test_movie.py -- "$OUT/media/speech_44k_stereo.wav" "$OUT/media/talking_16x9.mp4"
# Take the shared live lock (C:/Users/USER/AppData/Local/Temp/bfa_live.lock) around each launch.
blender -b --factory-startup --python sequencer_probe.py -- "$OUT/media" "$OUT/seq"          # -> seq_bg.json
blender --factory-startup --python sequencer_probe.py -- "$OUT/media" "$OUT/seq"             # -> seq_ui.json (quits itself)
blender --factory-startup --python sequencer_ui_ops_probe.py -- "$OUT/seq"
# whisper.cpp: <root>/{cpu,cuda124}/Release/*.exe and <root>/models/ggml-*.bin (see 4v §6.2)
python -I whisper_bench.py <root> "$OUT/media" "$OUT/wout" cuda124:large-v3-turbo:wdtw cpu:small:wdtw cpu:base:words:vad
python -I whisper_server_probe.py <root> "$OUT/media/speech_16k_mono.wav" "$OUT/srv" cuda124:large-v3-turbo:large.v3.turbo
python -I transcript_from_whisper.py "$OUT/srv/req_cuda124_large-v3-turbo.json" <media> large-v3-turbo cuda-12.4 out.json "$OUT/media/speech_16k_mono.wav.words.tsv"
```
