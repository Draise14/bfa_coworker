"""Video 0 spike (#374): the two Sequencer operators that need a window, in UI mode.

  blender --factory-startup --python sequencer_ui_ops_probe.py -- <out_dir>

export_subtitles: poll needs a SEQUENCE_EDITOR area, the workspace sequencer scene AND an
active TEXT strip. add_scene_strip_from_scene_asset: EXEC returns PASS_THROUGH whatever the
context; INVOKE_DEFAULT (with a Sequencer area override) adds the strip. Also the fallback:
libraries.write / libraries.load(assets_only=True) + strips.new_scene. Log: <out_dir>/explore_ui.log
"""
import bpy, os, sys
OUT = sys.argv[sys.argv.index("--") + 1]
L = open(os.path.join(OUT, "explore_ui.log"), "w", encoding="utf-8")
def P(*a): L.write(" ".join(str(x) for x in a) + "\n"); L.flush()
sc = bpy.context.scene
ed = sc.sequence_editor_create()
S = {}
def prep():
    win = bpy.context.window_manager.windows[0]
    win.event_simulate(type="ESC", value="PRESS"); win.event_simulate(type="ESC", value="RELEASE")
    areas = sorted(win.screen.areas, key=lambda a: a.width * a.height, reverse=True)
    areas[0].type = "SEQUENCE_EDITOR"; areas[0].spaces.active.view_type = "SEQUENCER"
    areas[1].type = "FILE_BROWSER"; areas[1].ui_type = "ASSETS"
    win.workspace.sequencer_scene = sc
    t1 = ed.strips.new_effect("cap1", "TEXT", 2, 1, length=24); t1.text = "First caption"
    t2 = ed.strips.new_effect("cap2", "TEXT", 2, 30, length=24); t2.text = "Second, caption"
    col = ed.strips.new_effect("bg", "COLOR", 1, 1, length=60)
    tpl = bpy.data.scenes.new("CW Lower Third"); tpl.sequence_editor_create()
    tpl.sequence_editor.strips.new_effect("n", "TEXT", 1, 1, length=48).text = "Jane"
    tpl.asset_mark()
    S["t1"], S["col"] = t1, col
    bpy.app.timers.register(step, first_interval=2.0)
def ctx():
    win = bpy.context.window_manager.windows[0]
    a = next(a for a in win.screen.areas if a.type == "SEQUENCE_EDITOR")
    r = next(r for r in a.regions if r.type == "WINDOW")
    return bpy.context.temp_override(window=win, area=a, region=r)
def srt(label):
    p = os.path.join(OUT, "explore_%s.srt" % label)
    with ctx():
        pl = bpy.ops.sequencer.export_subtitles.poll()
        r = None
        if pl:
            r = bpy.ops.sequencer.export_subtitles(filepath=p)
    P("SRT", label, "poll", pl, r, os.path.exists(p) and open(p, encoding="utf-8").read().replace("\n", "\n"))
def asset(label, **kw):
    n = len(ed.strips)
    base = dict(asset_library_type="LOCAL", asset_library_identifier="", relative_asset_identifier="Scene/CW Lower Third",
                frame_start=100, channel=5, move_strips=False)
    base.update(kw)
    with ctx():
        try:
            r = bpy.ops.sequencer.add_scene_strip_from_scene_asset(**base)
        except Exception as ex:
            r = str(ex)[-200:]
    P("ASSET", label, base["asset_library_type"], repr(base["relative_asset_identifier"]), r, "added", len(ed.strips) - n,
      [ (s.name, s.type, s.scene and s.scene.name) for s in ed.strips if s.type == "SCENE"])
N = [0]
def step():
    N[0] += 1
    k = N[0]
    if k == 1:
        for s in ed.strips: s.select = False
        srt("none_selected")
        ed.active_strip = S["col"]; srt("active_color")
        S["t1"].select = True; ed.active_strip = S["t1"]; srt("active_text")
        asset("tick1")
        for rid in ("Scene/CW Lower Third", "Scene\CW Lower Third"):
            with ctx():
                n = len(ed.strips)
                try:
                    rr = bpy.ops.sequencer.add_scene_strip_from_scene_asset("INVOKE_DEFAULT", asset_library_type="LOCAL",
                            relative_asset_identifier=rid, frame_start=100, channel=8, move_strips=False)
                except Exception as ex:
                    rr = str(ex)[-200:]
                P("ASSET INVOKE tick1 (asset browser not yet on LOCAL)", repr(rid), rr, "added", len(ed.strips) - n,
                  [(s.name, s.channel, s.left_handle, s.scene.name) for s in ed.strips if s.type == "SCENE"])
        asset("tick1_all", asset_library_type="ALL")
    elif k == 2:
        win = bpy.context.window_manager.windows[0]
        ab = next(a for a in win.screen.areas if a.type == "FILE_BROWSER")
        sp = ab.spaces.active
        try:
            sp.params.asset_library_reference = "LOCAL"
        except Exception as ex:
            P("libref err", ex)
        P("asset browser params", getattr(sp.params, "asset_library_reference", None))
    elif k == 3:
        win = bpy.context.window_manager.windows[0]
        ab = next(a for a in win.screen.areas if a.type == "FILE_BROWSER")
        r = next(r for r in ab.regions if r.type == "WINDOW")
        with bpy.context.temp_override(window=win, area=ab, region=r):
            try:
                bpy.ops.file.select_all(action="SELECT")
            except Exception as ex:
                P("select err", ex)
            sel = list(getattr(bpy.context, "selected_assets", None) or [])
        P("selected_assets", [(x.name, x.id_type, getattr(x, "full_library_path", None), getattr(x, "full_path", None)) for x in sel])
        if sel:
            sa = next(a for a in win.screen.areas if a.type == "SEQUENCE_EDITOR")
            sr = next(r for r in sa.regions if r.type == "WINDOW")
            for kw in ({}, {"asset_library_type": "LOCAL", "relative_asset_identifier": "Scene\CW Lower Third"}):
                n = len(ed.strips)
                with bpy.context.temp_override(window=win, area=sa, region=sr, asset=sel[0]):
                    try:
                        rr = bpy.ops.sequencer.add_scene_strip_from_scene_asset(frame_start=100, channel=5, move_strips=False, **kw)
                    except Exception as ex:
                        rr = str(ex)[-200:]
                P("ASSET ctx-asset", kw, rr, "added", len(ed.strips) - n, [(s.name, s.scene and s.scene.name) for s in ed.strips if s.type == "SCENE"])
            P("asset repr attrs", [d for d in dir(sel[0]) if not d.startswith("_")])
    elif k == 4:
        try:
            with ctx(): P("refresh", bpy.ops.asset.library_refresh.poll())
        except Exception as ex: P("refresh err", ex)
        asset("tick2"); asset("tick2_all", asset_library_type="ALL")
    elif k == 5:
        asset("tick3"); asset("tick3_rel_noprefix", relative_asset_identifier="CW Lower Third")
        asset("tick3_backslash", relative_asset_identifier="Scene\CW Lower Third")
        asset("tick3_backslash_move", relative_asset_identifier="Scene\CW Lower Third", move_strips=True)
        with ctx():
            n = len(ed.strips)
            try:
                rr = bpy.ops.sequencer.add_scene_strip_from_scene_asset("INVOKE_DEFAULT", asset_library_type="LOCAL",
                        relative_asset_identifier="Scene\CW Lower Third", frame_start=100, channel=6, move_strips=False)
            except Exception as ex:
                rr = str(ex)[-200:]
            P("ASSET INVOKE_DEFAULT", rr, "added", len(ed.strips) - n)
        # Fallback: a template .blend written with libraries.write, appended with libraries.load
        tpl = bpy.data.scenes["CW Lower Third"]
        lib = os.path.join(OUT, "cw_templates.blend")
        bpy.data.libraries.write(lib, {tpl}, fake_user=True)
        with bpy.data.libraries.load(lib, link=False, assets_only=True) as (src, dst):
            P("lib scenes", src.scenes)
            dst.scenes = [n for n in src.scenes if n == "CW Lower Third"]
        got = dst.scenes[0]
        st = ed.strips.new_scene("lower third", got, 7, 100)
        P("FALLBACK libraries.load + new_scene", got.name, st.type, st.scene.name, st.duration, "asset_data", got.asset_data is not None)
        P("ops sequencer with asset", [o for o in dir(bpy.ops.sequencer) if "asset" in o])
        P("DONE")
        bpy.app.timers.register(lambda: bpy.ops.wm.quit_blender() and None, first_interval=3.0)
        return None
    return 2.0
bpy.app.timers.register(prep, first_interval=2.0)
