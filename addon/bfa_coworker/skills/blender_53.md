# Blender 5.3 Specifics

This addon was developed on Blender 5.3. These are the known specifics.

## Sandbox Policy

Blender 5.3+ enforces a sandbox policy for addon directories. Any subdirectory
matching a known top-level Python package name (rich/, click/, httpx/, etc.)
will trigger a policy violation — even if never imported.

Dependencies are stored outside the addon tree at `~/.cache/bfa_coworker/vendor_deps/`.

## UI Layout

`UILayout.label_multiline(text=...)` is available for native multi-line text wrapping.
Prefer this over manual `textwrap.fill()` when drawing multi-line content.

## Online Access

`bpy.app.online_access` controls whether the addon can make network requests.
Auto-start is skipped when offline unless `--online-mode` is passed at launch.
