"""Video 0 spike (#374): the Sequencer API, probed live.

  blender [-b | --enable-event-simulate] --factory-startup --python sequencer_probe.py -- <media_dir> <out_dir>

Background mode runs everything the API allows without a window; UI mode (no -b) re-runs
the window-only parts (export_subtitles context, render.opengl(sequencer=True), the
preview redraw timing at 1080x1920, a screenshot) and quits itself ~3 s after the last check.
Writes <out_dir>/seq_<bg|ui>.json and seq_<bg|ui>.log. Each check: PASS / FAIL + detail.
"""
import json
import os
import sys
import time
import traceback

import bpy

argv = sys.argv[sys.argv.index("--") + 1:]
MEDIA, OUT = argv[0], argv[1]
os.makedirs(OUT, exist_ok=True)
BG = bpy.app.background
TAG = "bg" if BG else "ui"
LOG = open(os.path.join(OUT, "seq_%s.log" % TAG), "w", encoding="utf-8")
RES = {"blender": bpy.app.version_string, "mode": TAG, "checks": []}
MOVIE = os.path.join(MEDIA, "talking_16x9.mp4")
WAV44 = os.path.join(MEDIA, "speech_44k_stereo.wav")


def say(*a):
    LOG.write(" ".join(str(x) for x in a) + "\n")
    LOG.flush()


def check(name, ok, detail=""):
    RES["checks"].append({"name": name, "ok": bool(ok), "detail": detail})
    say("CHECK", "PASS" if ok else "FAIL", name, "|", detail)


def guard(name):
    def deco(fn):
        def run():
            try:
                fn()
            except Exception:
                check(name + " (crashed)", False, traceback.format_exc()[-1500:])
        run.__name__ = fn.__name__
        return run
    return deco


def rna_props(obj, words=None):
    out = {}
    for p in obj.bl_rna.properties:
        k = p.identifier
        if k == "rna_type" or (words and not any(w in k for w in words)):
            continue
        try:
            v = getattr(obj, k)
            if hasattr(v, "__len__") and not isinstance(v, str) and p.type in {"FLOAT", "INT", "BOOLEAN"}:
                v = list(v)
            elif p.type in {"POINTER", "COLLECTION"}:
                v = "<%s>" % p.type
            out[k] = v if isinstance(v, (int, float, str, bool, list, type(None))) else repr(v)
        except Exception as ex:
            out[k] = "<err %s>" % ex
    return out


def fcurves_of(id_):
    ad = getattr(id_, "animation_data", None)
    act = ad and ad.action
    if not act:
        return []
    if hasattr(act, "fcurves") and len(getattr(act, "fcurves", [])):
        return list(act.fcurves)
    fcs = []
    for layer in getattr(act, "layers", []):
        for st in layer.strips:
            for cb in getattr(st, "channelbags", []):
                fcs.extend(cb.fcurves)
    return fcs


scene = bpy.context.scene
scene.render.resolution_x, scene.render.resolution_y = 1920, 1080
scene.render.fps = 24
ed = scene.sequence_editor_create()
S = {}


@guard("strips API")
def probe_strips():
    names = ["strips", "strips_all", "sequences", "sequences_all", "meta_stack", "active_strip",
             "selected_strips", "channels", "display_stack"]
    has = {n: hasattr(ed, n) for n in names}
    check("strips: SequenceEditor collections", has.get("strips") and has.get("strips_all"), has)
    fn = bpy.types.StripsTopLevel.bl_rna.functions
    check("strips: StripsTopLevel functions", True, sorted(fn.keys()))
    eff = [e.identifier for e in fn["new_effect"].parameters["type"].enum_items]
    check("strips: new_effect types", True, eff)
    made = {}
    try:
        made["movie"] = ed.strips.new_movie("movie", MOVIE, 1, 1, fit_method="FIT")
    except TypeError:
        made["movie"] = ed.strips.new_movie("movie", MOVIE, 1, 1)
    made["sound"] = ed.strips.new_sound("sound", WAV44, 2, 1)
    made["color"] = ed.strips.new_effect("color", "COLOR", 3, 1, length=48)
    made["text"] = ed.strips.new_effect("text", "TEXT", 4, 1, length=48)
    sc2 = bpy.data.scenes.new("Title Template")
    made["scene"] = ed.strips.new_scene("scene", sc2, 5, 1)
    made["adjust"] = ed.strips.new_effect("adjust", "ADJUSTMENT", 6, 1, length=48)
    info = {k: {"type": s.type, "rna": s.bl_rna.identifier} for k, s in made.items()}
    check("strips: created without ops (new_movie/new_sound/new_effect COLOR,TEXT,ADJUSTMENT/new_scene)",
          all(made.values()), info)
    mv = made["movie"]
    frame_props = rna_props(mv, ["frame", "handle", "offset", "channel", "duration", "length", "left", "right", "start", "end", "speed"])
    check("strips: movie frame properties", "frame_final_start" in frame_props, frame_props)
    # Move the left handle and see which properties move.
    before = (mv.frame_start, mv.frame_final_start, mv.frame_final_end, mv.frame_offset_start)
    try:
        mv.left_handle = 49
        lh = "left_handle"
    except Exception:
        mv.frame_final_start = 49
        lh = "frame_final_start"
    after = (mv.frame_start, mv.frame_final_start, mv.frame_final_end, mv.frame_offset_start)
    check("strips: trim left handle via %s" % lh, after[1] == 49 and after[0] == before[0],
          {"before(frame_start,final_start,final_end,offset_start)": before, "after": after})
    mv.frame_start = 101
    check("strips: move by frame_start", mv.frame_final_start == 149, (mv.frame_start, mv.frame_final_start))
    snd = made["sound"]
    check("strips: sound strip", snd.type == "SOUND", rna_props(snd, ["frame", "volume", "pan", "sound", "show_waveform", "speed", "pitch"]))
    check("strips: sound datablock", bool(snd.sound), rna_props(snd.sound))
    # Splitting without ops: Strip.split?
    sfn = sorted(made["color"].bl_rna.functions.keys())
    check("strips: Strip functions", True, sfn)
    S.update(made)
    S["movie_meta"] = rna_props(mv.elements[0]) if len(getattr(mv, "elements", [])) else None
    check("strips: movie element (orig size)", True, S["movie_meta"])
    for k, s in list(made.items()):
        if k not in ("movie", "sound"):
            ed.strips.remove(s)
    ed.strips.remove(made["sound"])
    S.pop("sound")
    # The movie's own audio as a sound strip (what the Add Movie operator does)
    try:
        ms = ed.strips.new_sound("movie_audio", MOVIE, 2, 101)
        check("strips: new_sound(<mp4>) = the movie's audio track", ms.type == "SOUND",
              {"dur": ms.duration, "movie_dur": mv.duration, "rate": ms.sound.samplerate})
        S["movie_audio"] = ms
    except Exception as ex:
        check("strips: new_sound(<mp4>)", False, str(ex))


@guard("compositor modifier")
def probe_compositor():
    mv = S["movie"]
    mods_fn = bpy.types.StripModifiers.bl_rna.functions["new"]
    types = [e.identifier for e in mods_fn.parameters["type"].enum_items]
    check("compositor: StripModifiers.new types", "COMPOSITOR" in types, types)
    ng = bpy.data.node_groups.new("CW Reframe", "CompositorNodeTree")
    it = ng.interface
    it.new_socket("Image", in_out="INPUT", socket_type="NodeSocketColor")
    zoom = it.new_socket("Zoom", in_out="INPUT", socket_type="NodeSocketFloat")
    zoom.default_value = 1.0
    blur = it.new_socket("Blur", in_out="INPUT", socket_type="NodeSocketFloat")
    blur.default_value = 0.0
    it.new_socket("Image", in_out="OUTPUT", socket_type="NodeSocketColor")
    gi = ng.nodes.new("NodeGroupInput")
    go = ng.nodes.new("NodeGroupOutput")
    tr = ng.nodes.new("CompositorNodeTransform")
    bl = ng.nodes.new("CompositorNodeBlur")
    check("compositor: Transform node inputs", True, [s.name for s in tr.inputs])
    check("compositor: Blur node inputs/props", True, {"inputs": [s.name for s in bl.inputs],
                                                     "props": rna_props(bl, ["size", "filter", "relative", "factor"])})
    L = ng.links.new
    L(gi.outputs["Image"], tr.inputs["Image"])
    L(gi.outputs["Zoom"], tr.inputs["Scale"])
    L(tr.outputs["Image"], bl.inputs["Image"])
    size_in = next((s for s in bl.inputs if s.name in ("Size", "Size X")), None)
    if size_in is not None:
        L(gi.outputs["Blur"], size_in)
    L(bl.outputs["Image"], go.inputs["Image"])
    mod = mv.modifiers.new("Reframe", type="COMPOSITOR")
    check("compositor: modifier rna", mod.bl_rna.identifier == "SequencerCompositorModifierData",
          {"rna": mod.bl_rna.identifier, "props": rna_props(mod)})
    mod.node_group = ng
    check("compositor: node_group assigned", mod.node_group == ng, mod.node_group and mod.node_group.name)
    # 5.2: per-modifier input values live in mod.properties.inputs.<socket identifier>.value
    # (like the geometry nodes modifier). Re-fetch mod.properties.inputs after any interface
    # change: a handle held across new_socket() crashes 5.2.4 (EXCEPTION_ACCESS_VIOLATION).
    inputs = mod.properties.inputs
    pinfo = {"properties": mod.properties.bl_rna.identifier, "inputs": type(inputs).__name__,
             "zoom id": zoom.identifier, "blur id": blur.identifier,
             "zoom value": getattr(inputs, zoom.identifier).value,
             "path": getattr(inputs, zoom.identifier).path_from_id("value")}
    check("compositor: per-modifier inputs = mod.properties.inputs.<identifier>.value", True, pinfo)

    def setin(item, v):
        getattr(mod.properties.inputs, item.identifier).value = v
    S["setin"] = setin
    S["ng"], S["mod"], S["zoom_item"], S["blur_item"], S["blur_node"] = ng, mod, zoom, blur, bl
    S["tr_node"] = tr


def render_still(path):
    scene.render.filepath = path
    scene.render.image_settings.file_format = "PNG"
    t = time.perf_counter()
    bpy.ops.render.render(write_still=True)
    return time.perf_counter() - t


def img_stats(path):
    import numpy as np
    im = bpy.data.images.load(path, check_existing=False)
    w, h = im.size
    px = np.empty(w * h * 4, dtype=np.float32)
    im.pixels.foreach_get(px)
    bpy.data.images.remove(im)
    px = px.reshape(h, w, 4)
    # mean luminance in the centre column band, and a sharpness proxy (mean |dx|)
    lum = px[..., :3].mean(axis=2)
    return {"size": [w, h], "mean": round(float(lum.mean()), 4),
            "sharp": round(float(abs(np.diff(lum, axis=1)).mean()), 5)}


@guard("render timings")
def probe_render():
    scene.render.resolution_x, scene.render.resolution_y = 1080, 1920
    scene.render.resolution_percentage = 100
    scene.frame_set(400)
    check("render: use_sequencer / compositor_device", True,
          {"use_sequencer": scene.render.use_sequencer,
           "compositor_device": getattr(scene.render, "compositor_device", None),
           "compositor_precision": getattr(scene.render, "compositor_precision", None)})
    for k in [p.identifier for p in ed.bl_rna.properties if p.identifier.startswith("use_cache")]:
        setattr(ed, k, False)
    mod, ng = S["mod"], S["ng"]
    rows = {}
    mod.mute = True
    p0 = os.path.join(OUT, "r_%s_nomod.png" % TAG)
    rows["no modifier"] = round(render_still(p0), 3)
    rows["no modifier (2nd)"] = round(render_still(p0), 3)
    st0 = img_stats(p0)
    mod.mute = False
    p1 = os.path.join(OUT, "r_%s_mod_default.png" % TAG)
    rows["modifier zoom 1 blur 0"] = round(render_still(p1), 3)
    st1 = img_stats(p1)
    rows["modifier zoom 1 blur 0 (2nd)"] = round(render_still(p1), 3)
    # Changing the interface default does NOT reach an existing modifier (values are per modifier).
    S["zoom_item"].default_value = 2.0
    p2i = os.path.join(OUT, "r_%s_mod_zoom2_iface.png" % TAG)
    render_still(p2i)
    st2i = img_stats(p2i)
    check("compositor: interface default_value change reaches the modifier? (expected no)", st2i == st1, {"iface zoom2": st2i})
    S["setin"](S["zoom_item"], 2.0)
    p2 = os.path.join(OUT, "r_%s_mod_zoom2.png" % TAG)
    rows["modifier zoom 2 (per-modifier input)"] = round(render_still(p2), 3)
    st2 = img_stats(p2)
    S["setin"](S["blur_item"], 40.0)
    p3 = os.path.join(OUT, "r_%s_mod_zoom2_blur40.png" % TAG)
    rows["modifier zoom 2 blur 40"] = round(render_still(p3), 3)
    st3 = img_stats(p3)
    for dev in ("CPU", "GPU"):
        if hasattr(scene.render, "compositor_device"):
            try:
                scene.render.compositor_device = dev
                rows["modifier zoom 2 blur 40 on %s" % dev] = round(render_still(p3), 3)
            except Exception as ex:
                rows["device %s" % dev] = "err %s" % ex
    scene.render.compositor_device = "CPU"
    check("compositor: per-modifier input change changes the render",
          st2 != st1, {"default": st1, "zoom2": st2})
    check("compositor: blur input changes the render", st3["sharp"] < st2["sharp"], {"zoom2": st2, "blur40": st3})
    check("render: 1080x1920 still render timings (s)", True, rows)
    check("render: modifier off vs on image", st0 != st1, {"nomod": st0, "mod": st1})
    try:
        it = getattr(mod.properties.inputs, S["zoom_item"].identifier)
        ok = it.keyframe_insert("value", frame=1)
        check("compositor: keyframe the per-modifier input (lives on the scene's action)", ok,
              [f.data_path for f in fcurves_of(scene) if "modifiers" in f.data_path])
    except Exception as ex:
        check("compositor: keyframe the per-modifier input", False, str(ex))
    try:
        S["tr_node"].inputs["X"].keyframe_insert("default_value", frame=1)
        check("compositor: keyframe a node input inside the group", True, [f.data_path for f in fcurves_of(ng)])
    except Exception as ex:
        check("compositor: keyframe a node input inside the group", False, str(ex))
    # Two strips sharing one group: is the input per strip? (no -> one group per strip)
    check("compositor: group users", True, {"users": ng.users})
    # render.opengl in this mode
    try:
        t = time.perf_counter()
        r = bpy.ops.render.opengl(sequencer=True, write_still=False)
        check("render: render.opengl(sequencer=True) in %s" % TAG, "FINISHED" in r,
              {"ret": list(r), "s": round(time.perf_counter() - t, 3)})
    except Exception as ex:
        check("render: render.opengl(sequencer=True) in %s" % TAG, False, str(ex)[-400:])
    scene.render.resolution_x, scene.render.resolution_y = 1920, 1080


@guard("text strips")
def probe_text():
    tx = ed.strips.new_effect("caption", "TEXT", 8, 1, length=96)
    tx.text = "Hello captions"
    props = rna_props(tx)
    check("text: all TextStrip properties", True, props)
    style = {k: v for k, v in props.items() if any(w in k for w in ("box", "outline", "shadow", "anchor", "align", "location", "wrap", "font", "bold", "italic", "color"))}
    check("text: style property names", all(k in props for k in ("use_box", "use_outline", "use_shadow", "anchor_x", "anchor_y", "location")), style)
    enums = {}
    for k in ("anchor_x", "anchor_y", "alignment_x", "alignment_y"):
        p = tx.bl_rna.properties.get(k)
        if p is not None and p.type == "ENUM":
            enums[k] = [e.identifier for e in p.enum_items]
    check("text: enum values", True, enums)
    results = {}
    for path, idx, vals in (("location", -1, [(0.5, -0.1), (0.5, 0.2)]), ("font_size", -1, [10, 90]),
                            ("color", 3, [0.0, 1.0]), ("blend_alpha", -1, [0.0, 1.0])):
        for f, v in zip((1, 13), vals):
            if idx >= 0:
                c = list(tx.color)
                c[idx] = v
                tx.color = c
            else:
                setattr(tx, path, v)
            try:
                ok = tx.keyframe_insert(path, index=idx, frame=f)
            except Exception as ex:
                ok = "err %s" % ex
            results.setdefault(path, []).append(ok)
    fcs = fcurves_of(scene)
    check("text: keyframe_insert results", all(all(x is True for x in v) for v in results.values()), results)
    check("text: fcurve data paths", len(fcs) >= 4, sorted({"%s[%d]" % (f.data_path, f.array_index) for f in fcs}))
    samples = {}
    for f in (1, 7, 13):
        scene.frame_set(f)
        samples[f] = {"loc": [round(x, 3) for x in tx.location], "size": round(tx.font_size, 2),
                      "alpha": round(tx.color[3], 3), "blend_alpha": round(tx.blend_alpha, 3)}
    check("text: animated values evaluate at frames 1/7/13", samples[7]["size"] not in (10, 90), samples)
    # Style it the caption way and check it sticks.
    for k, v in (("use_box", True), ("box_margin", 0.02), ("use_outline", True), ("outline_width", 0.1),
                 ("use_shadow", True), ("anchor_x", "CENTER"), ("anchor_y", "BOTTOM"), ("wrap_width", 0.8),
                 ("use_bold", True), ("box_roundness", 0.5), ("shadow_blur", 0.2), ("shadow_angle", 0.7),
                 ("shadow_offset", 0.05), ("alignment_x", "CENTER")):
        try:
            setattr(tx, k, v)
        except Exception as ex:
            check("text: set %s" % k, False, str(ex))
    check("text: styled", tx.use_box and tx.use_outline, rna_props(tx, ["box", "outline", "shadow", "anchor", "align", "wrap", "bold"]))
    # Font: load a system font
    try:
        f = bpy.data.fonts.load("C:/Windows/Fonts/arialbd.ttf", check_existing=True)
        tx.font = f
        check("text: font from bpy.data.fonts.load", tx.font == f, f.name)
    except Exception as ex:
        check("text: font load", False, str(ex))
    S["text"] = tx
    # A second caption for the SRT
    tx2 = ed.strips.new_effect("caption2", "TEXT", 8, 120, length=48)
    tx2.text = "Second line, with a comma"
    S["text2"] = tx2


def seq_scene_ctx():
    """5.x: sequencer ops poll on context.sequencer_scene (the workspace's sequencer scene)."""
    info = {"context.sequencer_scene": getattr(bpy.context, "sequencer_scene", "<no attr>")}
    ws = bpy.context.workspace or (bpy.data.workspaces[0] if bpy.data.workspaces else None)
    info["workspace"] = ws and ws.name
    if ws is not None and hasattr(ws, "sequencer_scene"):
        try:
            ws.sequencer_scene = scene
            info["set workspace.sequencer_scene"] = True
        except Exception as ex:
            info["set workspace.sequencer_scene"] = str(ex)
    info["after"] = getattr(bpy.context, "sequencer_scene", "<no attr>")
    info["after"] = info["after"] and getattr(info["after"], "name", info["after"])
    return info


def try_op(label, op, **kw):
    """Plain context, then temp_override(sequencer_scene=..., workspace=...), then a Sequencer area (UI)."""
    tries = [("plain", {})]
    ws = bpy.context.workspace or (bpy.data.workspaces[0] if bpy.data.workspaces else None)
    tries.append(("override scene+sequencer_scene+workspace", {"scene": scene, "sequencer_scene": scene, "workspace": ws}))
    if not BG:
        win = bpy.context.window_manager.windows[0]
        for a in win.screen.areas:
            if a.type == "SEQUENCE_EDITOR":
                reg = next(r for r in a.regions if r.type == "WINDOW")
                tries.append(("SEQUENCE_EDITOR area view=%s" % a.spaces.active.view_type,
                              {"window": win, "area": a, "region": reg}))
    log = []
    for name, ov in tries:
        try:
            with bpy.context.temp_override(**ov):
                r = op(**kw)
            log.append((name, list(r)))
            if "FINISHED" in r:
                check(label, True, log)
                return True
        except Exception as ex:
            log.append((name, str(ex)[-300:]))
    check(label, False, log)
    return False


@guard("export_subtitles")
def probe_srt():
    ops = sorted(o for o in dir(bpy.ops.sequencer))
    check("srt: sequencer ops containing subtitle/srt/text/import", True,
          [o for o in ops if any(w in o for w in ("subtitle", "srt", "text", "import"))])
    check("srt: all sequencer ops", True, ops)
    path = os.path.join(OUT, "captions_%s.srt" % TAG)
    if os.path.exists(path):
        os.remove(path)
    op = bpy.ops.sequencer.export_subtitles
    check("srt: poll() before setting the sequencer scene", True, op.poll())
    try_op("srt: export_subtitles(filepath) before setting the sequencer scene", op, filepath=path)
    check("srt: set workspace.sequencer_scene", True, seq_scene_ctx())
    check("srt: poll() after", True, op.poll())
    if not BG:
        win = bpy.context.window_manager.windows[0]
        polls = {}
        for a in win.screen.areas:
            if a.type == "SEQUENCE_EDITOR":
                reg = next(r for r in a.regions if r.type == "WINDOW")
                with bpy.context.temp_override(window=win, area=a, region=reg):
                    polls[a.spaces.active.view_type] = {
                        "export_subtitles": op.poll(), "select_all": bpy.ops.sequencer.select_all.poll(),
                        "refresh_all": bpy.ops.sequencer.refresh_all.poll(),
                        "ctx.sequencer_scene": getattr(bpy.context.sequencer_scene, "name", None),
                        "ctx.space_data": getattr(bpy.context.space_data, "type", None)}
        check("srt: polls inside each Sequencer area", True, polls)
    if not os.path.exists(path):
        try_op("srt: export_subtitles(filepath) after setting the sequencer scene", op, filepath=path)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            check("srt: output", True, fh.read()[:600])


@guard("scene asset strip")
def probe_scene_asset():
    has = hasattr(bpy.ops.sequencer, "add_scene_strip_from_scene_asset")
    check("asset: sequencer.add_scene_strip_from_scene_asset exists", has)
    if not has:
        return
    rt = bpy.ops.sequencer.add_scene_strip_from_scene_asset.get_rna_type()
    check("asset: operator properties", True, [p.identifier for p in rt.properties if p.identifier != "rna_type"])
    tpl = bpy.data.scenes.new("CW Lower Third")
    tpl.sequence_editor_create()
    t = tpl.sequence_editor.strips.new_effect("name", "TEXT", 1, 1, length=72)
    t.text = "Jane Doe"
    tpl.asset_mark()
    tpl.asset_data.description = "Lower third template"
    check("asset: Scene.asset_mark()", tpl.asset_data is not None, tpl.name)
    n0 = len(ed.strips)
    kw = dict(asset_library_type="LOCAL", asset_library_identifier="",
              relative_asset_identifier="Scene/CW Lower Third", frame_start=200, channel=9, move_strips=False)
    for rid in ("Scene/CW Lower Third", "Scene\CW Lower Third"):
        kw["relative_asset_identifier"] = rid
        if try_op("asset: add_scene_strip_from_scene_asset LOCAL %r" % rid,
                  bpy.ops.sequencer.add_scene_strip_from_scene_asset, **kw):
            break
    new = [s for s in ed.strips if s.channel == 9]
    check("asset: strip added", len(ed.strips) > n0,
          [{"name": s.name, "type": s.type, "scene": s.scene.name if s.scene else None,
            "frame": s.frame_final_start} for s in new])
    check("asset: scenes after", True, [s.name for s in bpy.data.scenes])
    cp = tpl.copy()
    cp.asset_clear()
    fb = ed.strips.new_scene("lower third (copy)", cp, 10, 200)
    check("asset: fallback new_scene(tpl.copy()) works", fb.type == "SCENE", {"scene": fb.scene.name})


@guard("audio")
def probe_audio():
    import aud
    check("audio: aud module", True, {"file": aud.__file__ if hasattr(aud, "__file__") else None,
                                      "Sound attrs": sorted(a for a in dir(aud.Sound) if not a.startswith("_"))})
    try:
        import numpy as np
        check("audio: numpy", True, np.__version__)
    except Exception as ex:
        check("audio: numpy", False, str(ex))
        return
    snd = bpy.data.sounds.load(WAV44, check_existing=True)
    check("audio: bpy.types.Sound props", True, rna_props(snd))
    check("audio: Sound has factory/samples API", True,
          {k: hasattr(snd, k) for k in ("factory", "samplerate", "channels", "pack", "use_mono", "use_memory_cache")})
    t = time.perf_counter()
    a = aud.Sound.file(bpy.path.abspath(snd.filepath))
    specs = a.specs
    length = a.length
    data = a.data()
    t_read = time.perf_counter() - t
    check("audio: aud.Sound.file(path).data()", data is not None,
          {"specs(rate,channels)": specs, "length_samples": length, "shape": list(data.shape),
           "dtype": str(data.dtype), "MB": round(data.nbytes / 1e6, 1), "read_s": round(t_read, 3)})
    # Movie audio straight from the mp4
    t = time.perf_counter()
    m = aud.Sound.file(MOVIE)
    md = m.data()
    check("audio: aud reads the mp4's audio track", md is not None and md.shape[0] > 0,
          {"specs": m.specs, "shape": list(md.shape), "s": round(time.perf_counter() - t, 3)})
    # Lighter: resample to 16 kHz mono first (what whisper wants too)
    t = time.perf_counter()
    s16 = m.resample(16000, False).rechannel(1) if hasattr(m, "rechannel") else None
    d16 = s16.data() if s16 is not None else None
    check("audio: resample(16000).rechannel(1).data()", d16 is not None,
          {"shape": list(d16.shape) if d16 is not None else None,
           "MB": round(d16.nbytes / 1e6, 1) if d16 is not None else None, "s": round(time.perf_counter() - t, 3)})
    # Write a 16 kHz mono WAV for whisper from the mp4 (no ffmpeg needed)
    out_wav = os.path.join(OUT, "from_mp4_16k_%s.wav" % TAG)
    try:
        t = time.perf_counter()
        s16.write(out_wav, 16000, aud.CHANNELS_MONO, aud.FORMAT_S16, aud.CONTAINER_WAV, aud.CODEC_PCM)
        import wave
        w = wave.open(out_wav)
        check("audio: aud Sound.write -> 16 kHz mono S16 WAV", w.getframerate() == 16000 and w.getnchannels() == 1,
              {"rate": w.getframerate(), "ch": w.getnchannels(), "sec": round(w.getnframes() / w.getframerate(), 2),
               "s": round(time.perf_counter() - t, 3), "path": out_wav})
        w.close()
    except Exception as ex:
        check("audio: aud Sound.write(16 kHz mono S16 WAV PCM) from resampled mp4", False, str(ex))
        variants = [
            ("file(mp4).write(path, 16000, MONO, S16, WAV, PCM) no resample chain",
             lambda p: aud.Sound.file(MOVIE).write(p, 16000, aud.CHANNELS_MONO, aud.FORMAT_S16, aud.CONTAINER_WAV, aud.CODEC_PCM)),
            ("file(wav44).write(path, 44100, STEREO, S16, WAV, PCM)",
             lambda p: aud.Sound.file(WAV44).write(p, 44100, aud.CHANNELS_STEREO, aud.FORMAT_S16, aud.CONTAINER_WAV, aud.CODEC_PCM)),
            ("resampled.write(path, 16000, MONO, FLOAT32, WAV, PCM)",
             lambda p: s16.write(p, 16000, aud.CHANNELS_MONO, aud.FORMAT_FLOAT32, aud.CONTAINER_WAV, aud.CODEC_PCM)),
            ("resampled.write(path, 16000, MONO, S16, FLAC, FLAC)",
             lambda p: s16.write(p.replace(".wav", ".flac"), 16000, aud.CHANNELS_MONO, aud.FORMAT_S16, aud.CONTAINER_FLAC, aud.CODEC_FLAC)),
            ("buffer(data,16000).write(path, 16000, MONO, S16, WAV, PCM)",
             lambda p: aud.Sound.buffer(d16, 16000).write(p, 16000, aud.CHANNELS_MONO, aud.FORMAT_S16, aud.CONTAINER_WAV, aud.CODEC_PCM)),
        ]
        for i, (lbl, fn) in enumerate(variants):
            pth = out_wav.replace(".wav", "_v%d.wav" % i)
            try:
                fn(pth)
                ok = os.path.exists(pth) or os.path.exists(pth.replace(".wav", ".flac"))
                det = {"path": pth}
                if os.path.exists(pth):
                    import wave
                    with wave.open(pth) as w:
                        det.update(rate=w.getframerate(), ch=w.getnchannels(), sec=round(w.getnframes() / w.getframerate(), 2))
                elif ok:
                    chk = aud.Sound.file(pth.replace(".wav", ".flac"))
                    det.update(specs=chk.specs, sec=round(chk.length / chk.specs[0], 2))
                check("audio: aud write variant: " + lbl, ok, det)
            except Exception as ex2:
                check("audio: aud write variant: " + lbl, False, str(ex2))
        # Fallback that needs nothing but numpy + the stdlib: data() -> int16 -> wave
        import wave
        t = time.perf_counter()
        pcm = (np.clip(d16[:, 0], -1, 1) * 32767).astype("<i2").tobytes()
        with wave.open(out_wav, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(pcm)
        check("audio: fallback numpy + wave module -> 16 kHz mono WAV", os.path.exists(out_wav),
              {"path": out_wav, "s": round(time.perf_counter() - t, 3), "MB": round(len(pcm) / 1e6, 1)})
    # Silence finder (RMS in 20 ms windows, -40 dBFS, >= 0.35 s)
    t = time.perf_counter()
    x = d16[:, 0] if d16.ndim == 2 else d16
    rate = 16000
    win = int(0.02 * rate)
    n = len(x) // win
    rms = np.sqrt((x[: n * win].reshape(n, win).astype(np.float64) ** 2).mean(axis=1))
    db = 20 * np.log10(np.maximum(rms, 1e-9))
    quiet = db < -40.0
    ranges, start = [], None
    for i, q in enumerate(np.append(quiet, False)):
        if q and start is None:
            start = i
        elif not q and start is not None:
            if (i - start) * 0.02 >= 0.35:
                ranges.append((round(start * 0.02, 2), round(i * 0.02, 2)))
            start = None
    t_sil = time.perf_counter() - t
    check("audio: RMS silence finder on 16 kHz mono (-40 dBFS, >=0.35 s)", len(ranges) >= 8,
          {"n": len(ranges), "s": round(t_sil, 4), "ranges": ranges})
    # Same straight on 44.1 kHz stereo data
    t = time.perf_counter()
    y = data.mean(axis=1) if data.ndim == 2 else data
    win = int(0.02 * specs[0])
    n = len(y) // win
    rms2 = np.sqrt((y[: n * win].reshape(n, win).astype(np.float64) ** 2).mean(axis=1))
    check("audio: RMS on 44.1k stereo", True, {"windows": int(n), "s": round(time.perf_counter() - t, 4)})
    # Sound strip waveform / sequencer-level API?
    check("audio: Scene audio settings", True, rna_props(scene, ["audio"]))


@guard("mixdown")
def probe_mixdown():
    has = hasattr(bpy.ops.sound, "mixdown")
    path = os.path.join(OUT, "mixdown_%s.wav" % TAG)
    ma = S.get("movie_audio")
    if ma is not None:
        scene.frame_start, scene.frame_end = ma.left_handle, ma.right_handle - 1
    if not has:
        check("audio: sound.mixdown exists", False)
        return
    rt = bpy.ops.sound.mixdown.get_rna_type()
    check("audio: sound.mixdown properties", True, [p.identifier for p in rt.properties if p.identifier.startswith(("c", "f", "m", "a", "s"))])
    try:
        t = time.perf_counter()
        r = bpy.ops.sound.mixdown(filepath=path, container="WAV", codec="PCM", format="S16", mixrate=16000,
                                  channels="MONO")
        info = {"ret": list(r), "s": round(time.perf_counter() - t, 2), "exists": os.path.exists(path)}
        if os.path.exists(path):
            import wave
            w = wave.open(path)
            info.update(rate=w.getframerate(), ch=w.getnchannels(), sec=round(w.getnframes() / w.getframerate(), 2))
            w.close()
        info["scene_range"] = (scene.frame_start, scene.frame_end)
        check("audio: sound.mixdown(WAV,PCM,S16,16000,MONO) of the scene", info.get("sec", 0) > 1, info)
    except Exception as ex:
        check("audio: sound.mixdown", False, str(ex)[-400:])


@guard("ui preview timing")
def probe_ui_preview():
    if BG:
        return
    win = bpy.context.window_manager.windows[0]
    big = next(a for a in win.screen.areas if a.type == "SEQUENCE_EDITOR" and a.spaces.active.view_type == "PREVIEW")
    reg = next(r for r in big.regions if r.type == "WINDOW")
    scene.render.resolution_x, scene.render.resolution_y = 1080, 1920
    for k in [p.identifier for p in ed.bl_rna.properties if p.identifier.startswith("use_cache")]:
        setattr(ed, k, False)
    timings = {}
    for label, mute, blur in (("no modifier", True, 0.0), ("modifier zoom2", False, 0.0), ("modifier zoom2 blur40", False, 40.0)):
        S["mod"].mute = mute
        S["setin"](S["blur_item"], blur)
        frames = list(range(300, 310))
        t = time.perf_counter()
        with bpy.context.temp_override(window=win, area=big, region=reg):
            for f in frames:
                scene.frame_set(f)
                bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=1)
        timings[label] = round((time.perf_counter() - t) / len(frames), 3)
    check("ui: preview redraw per frame at 1080x1920, cache off (s)", True, timings)
    bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=1)
    try:
        bpy.ops.screen.screenshot(filepath=os.path.join(OUT, "ui_preview.png"))
        check("ui: screenshot", True, os.path.join(OUT, "ui_preview.png"))
    except Exception as ex:
        check("ui: screenshot", False, str(ex))
    with bpy.context.temp_override(window=win, area=big, region=reg):
        try:
            scene.render.filepath = os.path.join(OUT, "opengl_seq_ui.png")
            t = time.perf_counter()
            r = bpy.ops.render.opengl(sequencer=True, write_still=True)
            check("ui: render.opengl(sequencer=True, write_still=True) with override",
                  os.path.exists(scene.render.filepath), {"ret": list(r), "s": round(time.perf_counter() - t, 3)})
        except Exception as ex:
            check("ui: render.opengl(sequencer=True) with override", False, str(ex)[-400:])


def run_all():
    for fn in (probe_strips, probe_compositor, probe_text, probe_render, probe_srt, probe_scene_asset,
               probe_audio, probe_mixdown, probe_ui_preview):
        say("==", fn.__name__)
        fn()
    with open(os.path.join(OUT, "seq_%s.json" % TAG), "w", encoding="utf-8") as fh:
        json.dump(RES, fh, indent=1, default=str)
    say("DONE")


if BG:
    run_all()
else:
    def _prep():
        # Area type changes only take effect after the event loop redraws, so prepare the
        # screen in one tick and probe in a later one.
        win = bpy.context.window_manager.windows[0]
        win.event_simulate(type="ESC", value="PRESS")  # close the splash
        win.event_simulate(type="ESC", value="RELEASE")
        areas = sorted(win.screen.areas, key=lambda a: a.width * a.height, reverse=True)
        areas[0].type = "SEQUENCE_EDITOR"
        areas[0].spaces.active.view_type = "PREVIEW"
        tl = next(a for a in win.screen.areas if a.type in ("DOPESHEET_EDITOR", "TIMELINE") or a.ui_type == "TIMELINE")
        tl.type = "SEQUENCE_EDITOR"
        tl.spaces.active.view_type = "SEQUENCER"
        win.workspace.sequencer_scene = scene
        bpy.app.timers.register(_tick, first_interval=2.0)
        return None

    def _tick():
        try:
            run_all()
        finally:
            bpy.app.timers.register(lambda: bpy.ops.wm.quit_blender() and None, first_interval=3.0)
        return None
    bpy.app.timers.register(_prep, first_interval=2.0)
