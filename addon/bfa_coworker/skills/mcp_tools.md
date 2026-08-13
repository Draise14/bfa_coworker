# MCP Tool Usage Guide

When a dedicated MCP tool exists for an operation, use it instead of `execute_blender_code`.
Dedicated tools are pre-tested and version-safe. Raw code is the last resort.

## Scene Exploration (Use These First)

- `get_blendfile_summary_datablocks` — Quick overview: data-block counts, active workspace, render engine.
- `get_objects_summary` — Collection hierarchy with object names, types, parents, visibility.
- `get_object_detail_summary` — Deep dive on one object: transforms, modifiers, constraints, materials.
- `get_blendfile_summary_path_info` — Blend file location, save status, age.
- `get_blendfile_summary_missing_files` — Missing external references (images, libraries, caches).
- `get_blendfile_summary_of_linked_libraries` — Library dependency tree.

## Documentation (API & Manual)

- `search_api_docs(query)` — Full-text search of bundled Blender Python API reference.
- `search_manual_docs(query)` — Full-text search of bundled Blender user manual.
- `get_python_api_docs(identifier)` — Exact API doc for a symbol (e.g. `bpy.types.Modifier`).
  Supports `*` wildcard for top-level listing and `X.*` for namespace children.

## Navigation

- `jump_to_tab_by_name(name)` — Switch workspace tab.
- `jump_to_view3d_object_by_name(name)` — Focus 3D viewport on an object.
- `jump_to_view3d_object_data_by_name(name)` — Focus 3D viewport by data-block.

## Visual Feedback

- `get_screenshot_of_area_as_image(area_ui_type)` — Screenshot a single Blender area.
- `get_screenshot_of_window_as_image()` — Screenshot the entire window.
- `get_screenshot_of_window_as_json()` — JSON layout with areas, active object, selection.
- `render_viewport_to_path(output_path)` — Render the current viewport to a file.

## When to Use `execute_blender_code`

Only fall back to raw code for:
- Modifier stack operations not covered by dedicated tools
- Complex multi-step operations that can't be expressed as tool calls
- Custom operators not available through any tool

## Operation History

- `get_operation_history(count=N)` — Check recent tool calls to avoid repeating failed operations.
  Use this before attempting any destructive change.

## Entity Tracking

After each `execute_blender_code` call, the system snapshots the scene and detects
newly created datablocks (objects, materials, node groups, etc.). A context message
is injected into the conversation listing what you've created so far this turn.
Use this information to modify existing entities instead of creating duplicates.
