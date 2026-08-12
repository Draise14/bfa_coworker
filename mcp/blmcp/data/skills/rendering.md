# Rendering

## Render Engines

- **Cycles** — physically-based, GPU/CPU, production quality
- **Eevee** — real-time, GPU only, previews and stylized renders
- **Workbench** — fast viewport render, no materials

```python
bpy.context.scene.render.engine = 'CYCLES'  # or 'BLENDER_EEVEE_NEXT'
```

## Render Settings

```python
scene = bpy.context.scene

# Resolution
scene.render.resolution_x = 1920
scene.render.resolution_y = 1080
scene.render.resolution_percentage = 100

# Samples (Cycles)
scene.cycles.samples = 256

# Frame range
scene.frame_start = 1
scene.frame_end = 250

# Output path
scene.render.filepath = "//render.png"
scene.render.image_settings.file_format = 'PNG'
```

## Render to File (Preferred Over `execute_blender_code`)

Use `render_viewport_to_path(output_path)` — it uses current render settings.

Use `render_thumbnail_to_path(output_path)` — for quick low-quality previews.

## Devices

```python
prefs = bpy.context.preferences.addons['cycles'].preferences
prefs.compute_device_type = 'CUDA'  # or 'OPTIX', 'HIP', 'METAL'
prefs.get_devices()

scene.cycles.device = 'GPU'
```

## World / Environment

```python
world = bpy.context.scene.world
world.use_nodes = True
# Add Environment Texture node connected to Background node
```

## Version Notes (5.2+)

- Eevee Next replaced Eevee Legacy — use `BLENDER_EEVEE_NEXT`
- `scene.render.engine` values unchanged from 5.0
