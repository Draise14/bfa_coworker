# BFA Coworker — Tier 4: Deep Editor Integration Plan

**Date**: 2026-08-05
**Status**: Research & Planning — Long-Term Vision
**Depends on**: Phase 2 (External Harness) + Tier 1-3 features

---

## Overview

This plan explores how to make the BFA Coworker addon feel like a deeply integrated
Blender editor — not just a sidebar panel, but a first-class workspace with its own
visual identity, feedback systems, and creative tools. All implementations are
**pure Python addon** — no C++ fork required.

The three pillars:

1. **Coworker Editor** — A dedicated workspace with rich chat, prompt system, and agent controls
2. **Viewport Agent Feedback** — Real-time visual overlays showing what the agent is doing
3. **Moodboard Editor** — A visual reference board integrated with the agent for image-aware workflows

---

## Technical Foundation: What's Possible from Python

| Capability | Feasibility | Approach |
|---|---|---|
| Custom Space types | ❌ C++ only | Use existing spaces + GPU takeover |
| **GPU draw handler takeover** of `SpaceTextEditor` | ✅ | Hide native regions, paint full UI with `gpu`/`blf` |
| **Custom Header** registration | ✅ | `bpy.types.Header` subclass — registerable like Panel |
| GPU 2D overlays (text, rects, images) | ✅ | `draw_handler_add(POST_PIXEL)` + `gpu` + `blf` |
| GPU 3D overlays (lines, wireframes) | ✅ | `draw_handler_add(POST_VIEW)` + built-in shaders |
| Custom GLSL shaders | ✅ | `gpu.types.GPUShaderCreateInfo` |
| Offscreen rendering | ✅ | `gpu.types.GPUOffScreen` |
| Modal operators (input capture) | ✅ | `invoke()` → `modal_handler_add()` → `modal()` |
| Modal + timer hybrid | ✅ | `bpy.app.timers.register()` inside modal |
| Programmatic workspace creation | ✅ | `bpy.data.workspaces.new()` + `bpy.ops.screen` operators |
| Screen layout manipulation | ⚠️ | `bpy.ops.screen.area_split/join/close` — needs pixel coordinates |
| Image Editor as canvas | ✅ | Create composite images + GPU overlay annotations |
| Local image generation | ⚠️ | `diffusers` library — large models (2-6 GB), CPU or GPU |

### The GPU Takeover Pattern — Key Insight

**Blender Python addons CANNOT create new `bpy.types.Space` subclasses** (C++ only).
But they **CAN** take over an existing Space type completely via GPU draw handlers:

1. Pick a space type (e.g., `SpaceTextEditor`)
2. Hide all native regions: `show_region_header = False`, `show_region_ui = False`, etc.
3. Register a `POST_PIXEL` draw handler that paints the entire area with `gpu`/`blf`
4. Register a custom `bpy.types.Header` subclass for that space type
5. Use a modal operator (`invoke()` → `modal_handler_add()` → `modal()` event loop)
   to capture keyboard/mouse input for the custom UI

This is the closest a Python addon can get to a "custom editor" — **80% of the native
feel with 0% of the C++ build system headache**. Blender's own interactive tools
(Knife, Transform, Annotation) use this exact pattern.

**Why `SpaceTextEditor` is the best takeover target:**
- Has minimal native UI (just a text area with line numbers)
- `region_location_from_cursor(line, column)` gives cursor-to-pixel mapping
- `font_size` and `show_line_numbers` properties for text rendering control
- Can set `text` to a `bpy.data.texts` data-block programmatically
- Users intuitively understand it as a "text/code" context — natural fit for chat

---

## Pillar 1: The Coworker Editor (GPU Takeover)

### Vision

A dedicated editor area that feels like a purpose-built AI collaboration environment
inside Blender. Not a sidebar panel — a full-area editor registered at the same level
as Blender's built-in tabs (Layout, Modeling, Sculpting, etc.).

The key insight: **FreePencil2 doesn't create a custom space** (it uses standard
sidebar panels, same as BFA Coworker already does). But we CAN take this further:

### The GPU Takeover Pattern

```
┌──────────────────────────────────────────────────────────────────┐
│  Coworker Editor (GPU-painted SpaceTextEditor takeover)          │
├──────────────────────────────────────────────────────────────────┤
│  Custom Header: [Chat] [Prompts] [Settings]  🟢 Connected       │
├──────────────────────────────────────────────────────────────────┤
│                                                                   │
│  ┌─ Chat Tab ─────────────────────────────────────────────────┐  │
│  │                                                              │  │
│  │  ┌──────────────────────────────────────────────────────┐   │  │
│  │  │  You · 12:34 PM                                       │   │  │
│  │  │  Create a red cube at the origin                      │   │  │
│  │  └──────────────────────────────────────────────────────┘   │  │
│  │                                                              │  │
│  │  ┌──────────────────────────────────────────────────────┐   │  │
│  │  │  Agent · 12:34 PM                          [Copy] [↻]│   │  │
│  │  │  I'll create a red cube for you.                      │   │  │
│  │  │  ┌──────────────────────────────────────────────┐     │   │  │
│  │  │  │ import bpy                                     │     │   │  │
│  │  │  │ bpy.ops.mesh.primitive_cube_add(size=2)        │     │   │  │
│  │  │  │ obj = bpy.context.active_object                │     │   │  │
│  │  │  │ mat = bpy.data.materials.new("Red")            │     │   │  │
│  │  │  │ mat.diffuse_color = (1, 0, 0, 1)              │     │   │  │
│  │  │  │ obj.data.materials.append(mat)                 │     │   │  │
│  │  │  └──────────────────────────────────────────────┘     │   │  │
│  │  │  ✓ Done. Red cube created at origin.                  │   │  │
│  │  └──────────────────────────────────────────────────────┘   │  │
│  │                                                              │  │
│  │  ┌──────────────────────────────────────────────────────┐   │  │
│  │  │  [Tool] execute_blender_code · 12:34 PM               │   │  │
│  │  │  Status: success · 0.042s                             │   │  │
│  │  └──────────────────────────────────────────────────────┘   │  │
│  │                                                              │  │
│  ├──────────────────────────────────────────────────────────────┤  │
│  │  ┌──────────────────────────────────────────────────────┐   │  │
│  │  │ > Make it spin                                       │   │  │
│  │  └──────────────────────────────────────────────────────┘   │  │
│  │  [Send]  [Clear]  [Stop]          Mode: ● Agent  ○ Ask      │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                                                                   │
└──────────────────────────────────────────────────────────────────┘
```

### 1.1 GPU Takeover Architecture

```python
class CoworkerEditorOverlay:
    """GPU-powered takeover of SpaceTextEditor for dedicated chat UI.

    This is the core pattern for making the Coworker editor feel native.
    Instead of a sidebar panel, we paint the ENTIRE Text Editor area with
    our own GPU-drawn UI, capturing all keyboard/mouse input via a modal
    operator.
    """

    _draw_handler: object | None = None
    _modal_op: object | None = None
    _active: bool = False

    # ── State ──
    _chat_messages: list[dict] = []       # Message history
    _input_text: str = ""                 # Current input line
    _cursor_pos: int = 0                  # Cursor in input
    _scroll_offset: int = 0               # Chat scroll position
    _selected_tab: str = "chat"           # chat / prompts / settings
    _hovered_element: str | None = None   # For click detection

    @classmethod
    def activate(cls, context: bpy.types.Context) -> None:
        """Take over the current Text Editor area."""
        if cls._active:
            return

        area = context.area
        if area.type != 'TEXT_EDITOR':
            return

        space = area.spaces[0]

        # 1. Hide all native regions.
        space.show_region_header = False
        space.show_region_ui = False       # Sidebar
        space.show_region_footer = False
        space.show_line_numbers = False
        space.show_syntax_highlight = False
        space.show_word_wrap = True
        space.show_margin = False

        # 2. Register our custom draw handler.
        cls._draw_handler = bpy.types.SpaceTextEditor.draw_handler_add(
            cls._draw_editor, (context,), 'WINDOW', 'POST_PIXEL'
        )

        # 3. Launch modal operator for input capture.
        bpy.ops.bfacw.coworker_editor_modal('INVOKE_DEFAULT')

        cls._active = True

    @classmethod
    def deactivate(cls) -> None:
        """Restore native Text Editor."""
        if cls._draw_handler is not None:
            bpy.types.SpaceTextEditor.draw_handler_remove(
                cls._draw_handler, 'WINDOW')
            cls._draw_handler = None
        cls._active = False

    @classmethod
    def _draw_editor(cls, context: bpy.types.Context) -> None:
        """Main draw callback — called every frame."""
        import gpu
        import blf
        from gpu_extras.presets import draw_texture_2d

        region = context.region
        width = region.width
        height = region.height

        # ── Background ──
        _draw_rect((0, 0), (width, height), (0.12, 0.12, 0.14, 1.0))

        # ── Tab bar ──
        tab_bar_h = 36
        _draw_rect((0, height - tab_bar_h), (width, tab_bar_h),
                    (0.08, 0.08, 0.10, 1.0))
        tabs = [("chat", "Chat"), ("prompts", "Prompts"), ("settings", "Settings")]
        tab_w = width // len(tabs)
        for i, (tab_id, tab_label) in enumerate(tabs):
            x = i * tab_w
            is_active = (cls._selected_tab == tab_id)
            bg = (0.18, 0.45, 0.80, 1.0) if is_active else (0.08, 0.08, 0.10, 0.0)
            _draw_rect((x, height - tab_bar_h), (tab_w, tab_bar_h), bg)
            _draw_text(tab_label, x + tab_w // 2, height - tab_bar_h // 2,
                       size=14, align='CENTER', valign='CENTER')

        # ── Status indicator (right side of tab bar) ──
        status_color = (0.2, 1.0, 0.3, 0.9)  # Green = connected
        from gpu_extras.presets import draw_circle_2d
        draw_circle_2d((width - 60, height - 18), status_color, 6.0)

        # ── Content area ──
        content_y_start = 60  # Above input area
        content_h = height - tab_bar_h - content_y_start

        if cls._selected_tab == "chat":
            cls._draw_chat_messages(0, content_y_start, width, content_h)
        elif cls._selected_tab == "prompts":
            cls._draw_prompts_tab(0, content_y_start, width, content_h)
        elif cls._selected_tab == "settings":
            cls._draw_settings_tab(0, content_y_start, width, content_h)

        # ── Input area ──
        input_h = 50
        _draw_rect((10, 5), (width - 20, input_h),
                    (0.08, 0.08, 0.10, 1.0))
        _draw_rect((10, 5), (width - 20, input_h),
                    (0.3, 0.5, 0.8, 0.3), is_outline=True)
        _draw_text("> " + cls._input_text, 20, 20, size=16,
                    color=(0.9, 0.9, 0.9, 1.0))
        # Blinking cursor.
        import time
        if int(time.monotonic() * 2) % 2 == 0:
            cursor_x = 20 + cls._cursor_pos * 9 + 15
            _draw_rect((cursor_x, 15), (2, 20), (0.8, 0.8, 0.8, 0.7))

        # ── Action buttons ──
        btn_y = 5
        btn_w = 60
        btn_h = 40
        _draw_button("Send", width - 190, btn_y, btn_w, btn_h,
                      (0.2, 0.6, 0.3, 1.0))
        _draw_button("Clear", width - 120, btn_y, btn_w, btn_h,
                      (0.5, 0.3, 0.3, 1.0))
        _draw_button("Stop", width - 50, btn_y, btn_w, btn_h,
                      (0.6, 0.4, 0.2, 1.0))

    @classmethod
    def _draw_chat_messages(cls, x, y, w, h) -> None:
        """Draw the chat message history with bubble styling."""
        # Clip to content area.
        # For each message in _chat_messages (scrolled by _scroll_offset):
        #   - User messages: right-aligned, blue-ish bubble
        #   - Agent messages: left-aligned, dark bubble, with code blocks
        #   - Tool messages: compact, gray, indented
        ...
```

### 1.2 Custom Header Registration

```python
class BFACW_HT_coworker_header(bpy.types.Header):
    """Custom header for the Coworker Text Editor takeover."""
    bl_idname = "BFACW_HT_coworker_header"
    bl_space_type = 'TEXT_EDITOR'
    bl_label = "Coworker"

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        # Only show our header when the Coworker takeover is active.
        return CoworkerEditorOverlay._active

    def draw(self, context: bpy.types.Context) -> None:
        layout = self.layout
        layout.label(text="Coworker", icon='CONSOLE')

        # Agent status.
        state = agent_controller._agent_state
        if state.mcp_server_running:
            layout.label(text="🟢 Connected", icon='CHECKMARK')
        else:
            layout.label(text="🔴 Offline", icon='X')

        # Quick actions.
        layout.operator("bfacw.agent_start", text="", icon='PLAY')
        layout.operator("bfacw.agent_stop", text="", icon='CANCEL')
```

### 1.3 Modal Operator for Input Capture

```python
class BFACW_OT_coworker_editor_modal(bpy.types.Operator):
    """Modal operator that captures input for the Coworker GPU editor."""
    bl_idname = "bfacw.coworker_editor_modal"
    bl_label = "Coworker Editor Modal"
    bl_options = {'REGISTER'}

    def invoke(self, context, event):
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        overlay = CoworkerEditorOverlay

        if event.type == 'ESC':
            overlay.deactivate()
            return {'CANCELLED'}

        # ── Mouse handling ──
        if event.type == 'MOUSEMOVE':
            # Track hover for click detection.
            overlay._mouse_x = event.mouse_x
            overlay._mouse_y = event.mouse_y
            context.area.tag_redraw()

        elif event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            # Check if click hit a button, tab, or scrollbar.
            handled = overlay._handle_click(event.mouse_x, event.mouse_y)
            if handled:
                context.area.tag_redraw()
                return {'RUNNING_MODAL'}

        elif event.type == 'WHEELUPMOUSE':
            overlay._scroll_offset = max(0, overlay._scroll_offset - 3)
            context.area.tag_redraw()

        elif event.type == 'WHEELDOWNMOUSE':
            overlay._scroll_offset += 3
            context.area.tag_redraw()

        # ── Keyboard handling ──
        elif event.type == 'RET' and event.value == 'PRESS':
            if event.shift:
                overlay._input_text += '\n'
                overlay._cursor_pos += 1
            else:
                # Send message.
                overlay._send_message()
            context.area.tag_redraw()

        elif event.type == 'BACK_SPACE' and event.value == 'PRESS':
            if overlay._cursor_pos > 0:
                overlay._input_text = (
                    overlay._input_text[:overlay._cursor_pos - 1] +
                    overlay._input_text[overlay._cursor_pos:]
                )
                overlay._cursor_pos -= 1
            context.area.tag_redraw()

        elif event.type == 'DEL' and event.value == 'PRESS':
            if overlay._cursor_pos < len(overlay._input_text):
                overlay._input_text = (
                    overlay._input_text[:overlay._cursor_pos] +
                    overlay._input_text[overlay._cursor_pos + 1:]
                )
            context.area.tag_redraw()

        elif event.type == 'LEFT_ARROW' and event.value == 'PRESS':
            overlay._cursor_pos = max(0, overlay._cursor_pos - 1)
            context.area.tag_redraw()

        elif event.type == 'RIGHT_ARROW' and event.value == 'PRESS':
            overlay._cursor_pos = min(
                len(overlay._input_text), overlay._cursor_pos + 1)
            context.area.tag_redraw()

        elif event.value == 'PRESS' and event.unicode:
            # Insert typed character.
            overlay._input_text = (
                overlay._input_text[:overlay._cursor_pos] +
                event.unicode +
                overlay._input_text[overlay._cursor_pos:]
            )
            overlay._cursor_pos += 1
            context.area.tag_redraw()

        return {'PASS_THROUGH'} if event.type in {
            'MIDDLEMOUSE', 'NUMPAD_PERIOD'
        } else {'RUNNING_MODAL'}
```

### 1.4 Workspace Setup Operator

```python
class BFACW_OT_setup_coworker_workspace(bpy.types.Operator):
    """Create and configure the dedicated Coworker workspace."""
    bl_idname = "bfacw.setup_coworker_workspace"
    bl_label = "Setup Coworker Workspace"
    bl_description = "Create a dedicated workspace with the Coworker editor"

    def execute(self, context):
        ws_name = "Coworker"

        # Create workspace if missing.
        if ws_name not in bpy.data.workspaces:
            ws = bpy.data.workspaces.new(ws_name)
            ws.object_mode = 'OBJECT'
        else:
            ws = bpy.data.workspaces[ws_name]

        # Switch to it.
        context.window.workspace = ws

        # Get the screen and configure areas.
        screen = context.window.screen

        # Strategy: use bpy.ops.screen to split the default area
        # into a layout: Text Editor (left) | 3D Viewport (right)
        for area in screen.areas:
            if area.type == 'VIEW_3D':
                # Override context to this area, split it vertically.
                with context.temp_override(area=area):
                    bpy.ops.screen.area_split(direction='VERTICAL', factor=0.35)

        # Find the new area and set it to TEXT_EDITOR.
        for area in screen.areas:
            if area.type == 'VIEW_3D':
                # The smaller left area should become Text Editor.
                # (Area splitting creates a new area; we need to find it.)
                ...

        self.report({"INFO"}, "Coworker workspace set up")
        return {"FINISHED"}
```

### 1.5 Registration Pattern

```python
# In __init__.py register():
_classes = (
    # ... existing classes ...
    BFACW_HT_coworker_header,          # Custom header
    BFACW_OT_coworker_editor_modal,    # Modal for input capture
    BFACW_OT_setup_coworker_workspace,  # One-click setup
)

def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
```

**Key points about this approach:**
- The `CoworkerEditorOverlay` class manages the GPU draw handler lifecycle
- The custom `bpy.types.Header` replaces the native Text Editor header
- The modal operator captures ALL keyboard/mouse input for the custom UI
- The workspace setup operator creates the layout programmatically
- Users can switch back to any workspace — the takeover is per-area, not global
- **No C++ required** — 100% Python addon

### 1.3 VS Code-Style Prompting

The user asked about "being prompted like we do it here in VS Code." This means:

**Inline agent commands** — Type `/` in the chat input to access commands:
- `/fix` — "Fix the selected object/modifier/setting"
- `/explain` — "Explain what this node tree does"
- `/create` — "Create a new [object/material/scene]"
- `/search` — "Search the Blender manual for..."
- `/optimize` — "Optimize the selected mesh"
- `/doc` — "Generate documentation for this node group"

**Agent suggestions** — The agent proactively suggests next steps:
- After creating an object: "Would you like me to add a material?"
- After an error: "I noticed the modifier failed. Would you like me to fix it?"
- On idle: "You have 3 objects without materials. Want me to set them up?"

**Context-aware prompts** — The system prompt is dynamically enriched with:
- Current selection (object names, types)
- Active tool/mode
- Recent operations (from operation history log)
- Scene statistics (vertex count, material count, etc.)

---

## Pillar 2: Viewport Agent Feedback

### Vision

When the agent is working, the 3D Viewport provides real-time visual feedback:
what the agent is looking at, what it's modifying, and what it's about to do.

### 2.1 Agent Focus Highlight

When the agent is about to modify an object, it's highlighted with a pulsing glow:

```
Implementation:
- GPU overlay (POST_VIEW) drawing a wireframe outline around the target object
- Animated alpha (pulse via timer) — breathing effect
- Color-coded: blue = reading, orange = modifying, green = done, red = error
```

```python
def draw_agent_focus_highlight():
    """Draw a pulsing outline around objects the agent is working on."""
    import gpu
    from gpu_extras.batch import batch_for_shader
    import time

    shader = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
    gpu.state.line_width_set(3.0)

    # Pulse alpha based on time.
    alpha = 0.5 + 0.5 * abs(math.sin(time.monotonic() * 2.0))

    for obj_name in _agent_state.focused_objects:
        obj = bpy.data.objects.get(obj_name)
        if not obj:
            continue

        # Get world-space bounding box edges.
        bbox = [obj.matrix_world @ mathutils.Vector(c) for c in obj.bound_box]
        edges = [...]  # 12 edges of the bounding box.

        # Color based on action type.
        if _agent_state.action_type == 'modify':
            color = (1.0, 0.6, 0.0, alpha)  # Orange
        elif _agent_state.action_type == 'read':
            color = (0.3, 0.5, 1.0, alpha)  # Blue
        else:
            color = (0.0, 1.0, 0.0, alpha)  # Green

        shader.bind()
        shader.uniform_float("color", color)
        batch = batch_for_shader(shader, 'LINES', {"pos": edges})
        batch.draw(shader)
```

### 2.2 Agent Planning Visualization

When the agent is "thinking," show a visual indicator of its plan:

- **Spatial markers**: Small dots or crosses where the agent plans to place objects
- **Path preview**: Dashed lines showing planned movements/animations
- **Text labels**: Floating text near objects showing what the agent intends to do

```
┌─ Planning Visualization ──────────────────────────────────────┐
│                                                                │
│   ·  ← "Create cube here"                                     │
│      \                                                        │
│       ·  ← "Add sphere"                                       │
│        \                                                      │
│         ·  ← "Add light"                                      │
│                                                                │
│   ─ ─ ─ →  (dashed line = planned animation path)             │
│                                                                │
└────────────────────────────────────────────────────────────────┘
```

### 2.3 Tool Execution Progress

When a tool is executing (especially long-running ones like renders):

- **Progress ring** around the cursor or in the viewport corner
- **Status text** overlay: "Rendering thumbnail... 45%"
- **Flash effect** on completion: brief green flash on modified objects

### 2.4 Agent "Breathing" Indicator

A subtle ambient indicator that the agent is alive and listening:

- **Corner glow**: Soft pulsing gradient in the bottom-right corner of the viewport
- **Status dot**: Small colored dot in the viewport corner
  - Green pulsing = idle, listening
  - Blue spinning = thinking
  - Orange solid = executing tool
  - Red = error

```python
def draw_agent_status_indicator():
    """Draw a small status indicator in the viewport corner."""
    import gpu
    from gpu_extras.presets import draw_circle_2d

    # Position: bottom-right corner, 20px margin.
    x = region.width - 30
    y = 30

    # Color based on state.
    if _agent_state.is_thinking:
        color = (0.3, 0.5, 1.0, 0.8)  # Blue
    elif _agent_state.error:
        color = (1.0, 0.2, 0.2, 0.8)  # Red
    elif _agent_state.mcp_server_running:
        color = (0.2, 1.0, 0.3, 0.8)  # Green
    else:
        color = (0.5, 0.5, 0.5, 0.5)  # Gray

    draw_circle_2d((x, y), color, 8.0)
```

### 2.5 Implementation Architecture

```python
class AgentViewportOverlay:
    """Manages all agent-related viewport overlays."""

    _handler: object | None = None
    _enabled: bool = False

    @classmethod
    def enable(cls):
        if cls._handler is not None:
            return
        cls._handler = bpy.types.SpaceView3D.draw_handler_add(
            cls._draw_overlay, (), 'WINDOW', 'POST_PIXEL'
        )
        cls._enabled = True

    @classmethod
    def disable(cls):
        if cls._handler is None:
            return
        bpy.types.SpaceView3D.draw_handler_remove(cls._handler, 'WINDOW')
        cls._handler = None
        cls._enabled = False

    @classmethod
    def _draw_overlay(cls):
        # Draw status indicator.
        cls._draw_status_indicator()
        # Draw tool progress.
        cls._draw_tool_progress()
        # Draw planning markers (if thinking).
        if _agent_state.is_thinking:
            cls._draw_planning_markers()

    @classmethod
    def _draw_status_indicator(cls):
        ...  # See 2.4

    @classmethod
    def _draw_tool_progress(cls):
        ...  # See 2.3

    @classmethod
    def _draw_planning_markers(cls):
        ...  # See 2.2
```

---

## Pillar 3: The Moodboard Editor

### Vision

A visual reference board inside Blender where users can:
- Drag and drop reference images
- Arrange and annotate them
- Use them as context for the AI agent (vision models)
- Generate new images from text prompts (local or API)
- Use images to guide 3D creation workflows

### 3.1 Moodboard as Image Editor Extension

**Approach**: Use Blender's Image Editor as the canvas, enhanced with GPU overlays
and a companion panel.

```
┌──────────────────────────────────────────────────────────────────┐
│  Moodboard Workspace                                              │
├──────────────────────────────────────────────────────────────────┤
│                            │                                      │
│   Image Editor              │   Moodboard Panel (sidebar)         │
│   ┌────────────────────┐   │   ┌─────────────────────────────┐   │
│   │                    │   │   │ Images (5)                   │   │
│   │  [ref1.jpg]        │   │   │ ┌─────┐ ┌─────┐ ┌─────┐   │   │
│   │                    │   │   │ │ img1│ │ img2│ │ img3│   │   │
│   │       [ref2.jpg]   │   │   │ └─────┘ └─────┘ └─────┘   │   │
│   │                    │   │   │ ┌─────┐ ┌─────┐            │   │
│   │  [ref3.jpg]        │   │   │ │ img4│ │ img5│  [+ Add]  │   │
│   │                    │   │   │ └─────┘ └─────┘            │   │
│   │           [ref4]   │   │   └─────────────────────────────┘   │
│   │                    │   │                                      │
│   └────────────────────┘   │   Selected: ref1.jpg                 │
│                            │   ┌─────────────────────────────┐   │
│                            │   │ [Use as Context]            │   │
│                            │   │ [Generate Similar]          │   │
│                            │   │ [Create 3D from Image]      │   │
│                            │   │ [Remove]                    │   │
│                            │   └─────────────────────────────┘   │
│                            │                                      │
│                            │   Generate:                          │
│                            │   ┌─────────────────────────────┐   │
│                            │   │ A cozy cabin in the woods    │   │
│                            │   └─────────────────────────────┘   │
│                            │   [Generate Image]                   │
│                            │                                      │
└────────────────────────────┴──────────────────────────────────────┘
```

### 3.2 Moodboard Data Model

```python
@dataclass
class MoodboardImage:
    """A single image on the moodboard."""
    name: str
    image: bpy.types.Image       # The Blender image data-block
    position: tuple[float, float] # Position on the board (0-1 UV)
    scale: float                  # Display scale
    notes: str                    # User annotations
    tags: list[str]               # Searchable tags
    source: str                   # 'file', 'generated', 'clipboard', 'url'

@dataclass
class Moodboard:
    """A collection of reference images."""
    name: str
    images: list[MoodboardImage]
    background_color: tuple[float, float, float, float]
    grid_size: tuple[int, int]    # Grid for auto-arrange
```

### 3.3 Moodboard Features

#### Drag & Drop Import
- Accept images dragged from file browser
- Paste from clipboard
- Load from URL
- Import from Poly Haven / Sketchfab thumbnails (Tier 1-2 integrations)

#### Image Arrangement
- Auto-grid layout
- Free-form placement (drag to reposition)
- Scale/rotate individual images
- Bring to front / send to back

#### Annotation
- Draw on images (using Blender's annotation system)
- Text notes per image
- Color-coded tags
- Link images together (visual connections)

#### AI Context Integration
- Select images to include as context for the LLM
- Vision models (Gemma 3 Vision, GPT-4V, Claude) can "see" the moodboard
- Agent can reference images: "Make the material look like ref1.jpg"
- Image-to-3D workflows: "Create a 3D model based on this reference"

### 3.4 Local Image Generation

#### Can Local Models Run Image Generation?

**Yes, but with significant caveats:**

| Model | Size | RAM Needed | Speed (CPU) | Speed (GPU) | Quality |
|---|---|---|---|---|---|
| Stable Diffusion 1.5 | ~4 GB | 4-8 GB | ~2 min/img | ~5 sec/img | Good |
| SDXL | ~7 GB | 8-16 GB | ~5 min/img | ~15 sec/img | Very Good |
| SDXL Turbo | ~7 GB | 8-16 GB | ~1 min/img | ~2 sec/img | Good (fast) |
| FLUX.1-schnell | ~12 GB | 16-24 GB | ~10 min/img | ~30 sec/img | Excellent |
| FLUX.1-dev | ~12 GB | 16-24 GB | ~10 min/img | ~30 sec/img | Excellent |

**Recommendation**: For a self-contained addon, **SDXL Turbo** or **Stable Diffusion 1.5**
are the most practical. They run on consumer GPUs and produce good results quickly.

#### Integration Approach

```python
def generate_image_local(
    prompt: str,
    negative_prompt: str = "",
    width: int = 512,
    height: int = 512,
    steps: int = 4,  # SDXL Turbo only needs 1-4 steps!
    seed: int = -1,
) -> bpy.types.Image | None:
    """Generate an image using a local Stable Diffusion model.

    Requires ``diffusers`` and ``torch`` to be installed in the vendor deps.
    Downloads the model on first use (~7 GB for SDXL Turbo).
    """
    import torch
    from diffusers import AutoPipelineForText2Image

    pipe = AutoPipelineForText2Image.from_pretrained(
        "stabilityai/sdxl-turbo",
        torch_dtype=torch.float16,
        variant="fp16",
    )
    pipe.to("cuda" if torch.cuda.is_available() else "cpu")

    if seed >= 0:
        generator = torch.Generator().manual_seed(seed)
    else:
        generator = None

    result = pipe(
        prompt=prompt,
        negative_prompt=negative_prompt,
        width=width,
        height=height,
        num_inference_steps=steps,
        guidance_scale=0.0,  # SDXL Turbo uses 0.0
        generator=generator,
    ).images[0]

    # Convert PIL Image to Blender Image.
    import numpy as np
    img_array = np.array(result)
    img_array = np.flipud(img_array)  # Blender uses bottom-left origin.
    img_array = img_array.astype(np.float32) / 255.0

    bpy_image = bpy.data.images.new(
        name="generated_" + prompt[:20],
        width=width,
        height=height,
        alpha=False,
    )
    bpy_image.pixels = img_array.ravel()
    bpy_image.pack()

    return bpy_image
```

#### Alternative: API-Based Generation

For users who don't want to download 7 GB models:

```python
def generate_image_remote(
    prompt: str,
    provider: str = "openrouter",
    api_key: str = "",
) -> bpy.types.Image | None:
    """Generate an image using a remote API (OpenRouter, Stability AI, etc.)."""
    # Use existing remote API infrastructure from llm_manager.py.
    ...
```

### 3.5 Agentic Moodboard Workflows

The moodboard isn't just a passive reference board — it's an active agent tool:

#### Workflow 1: Reference → 3D
```
1. User adds reference images to moodboard
2. User selects images and clicks "Create 3D from References"
3. Agent (with vision) analyzes the images
4. Agent creates matching 3D scene:
   - Blocks out geometry matching the reference
   - Sets up lighting matching the reference mood
   - Creates materials matching the reference colors/textures
   - Positions camera to match the reference composition
```

#### Workflow 2: Moodboard → Variations
```
1. User has a moodboard with a design direction
2. User clicks "Generate Variations"
3. Agent uses the moodboard as image-to-image input
4. Local or remote image generation creates variations
5. New images are added to the moodboard
6. User selects favorites and iterates
```

#### Workflow 3: Text → Moodboard → 3D
```
1. User types: "A steampunk airship interior, warm lighting, brass and wood"
2. Agent generates 4 reference images (local or API)
3. Images appear on moodboard
4. User selects the best one
5. Agent creates 3D scene based on the selected reference
```

#### Workflow 4: Scene Analysis → Moodboard
```
1. User has an existing 3D scene
2. User clicks "Analyze Scene Style"
3. Agent renders viewport thumbnails from multiple angles
4. Agent extracts color palette, material references, lighting mood
5. Results appear on moodboard as a "style guide"
6. User can apply this style to other scenes
```

---

## Implementation Plan

### Phase 4a: Coworker Workspace & Enhanced Chat (Est. 300 LOC)

| Step | Description | Files | LOC |
|---|---|---|---|
| 4a.1 | Auto-create "Coworker" workspace on addon register | `__init__.py` | ~40 |
| 4a.2 | Enhanced chat panel with message bubbles | `ui_chat.py` | ~80 |
| 4a.3 | Prompt templates panel | `ui_prompts.py` (new) | ~100 |
| 4a.4 | Slash commands (`/fix`, `/create`, etc.) | `ui_chat.py` | ~50 |
| 4a.5 | Context-aware system prompt enrichment | `agent_controller.py` | ~30 |

### Phase 4b: Viewport Agent Feedback (Est. 350 LOC)

| Step | Description | Files | LOC |
|---|---|---|---|
| 4b.1 | `AgentViewportOverlay` class with enable/disable | `viewport_overlay.py` (new) | ~60 |
| 4b.2 | Status indicator (corner dot with pulse animation) | `viewport_overlay.py` | ~50 |
| 4b.3 | Agent focus highlight (pulsing wireframe on target objects) | `viewport_overlay.py` | ~80 |
| 4b.4 | Tool execution progress ring | `viewport_overlay.py` | ~60 |
| 4b.5 | Planning markers (spatial dots for planned placements) | `viewport_overlay.py` | ~60 |
| 4b.6 | Integration with agent state (update on tool calls) | `agent_controller.py` | ~40 |

### Phase 4c: Moodboard Editor (Est. 500 LOC)

| Step | Description | Files | LOC |
|---|---|---|---|
| 4c.1 | `Moodboard` and `MoodboardImage` data model | `moodboard.py` (new) | ~60 |
| 4c.2 | Moodboard panel UI (image grid, add/remove, arrange) | `ui_moodboard.py` (new) | ~150 |
| 4c.3 | Drag-and-drop import (file browser integration) | `operators_moodboard.py` (new) | ~80 |
| 4c.4 | Composite image rendering (arrange images on one canvas) | `moodboard.py` | ~80 |
| 4c.5 | AI context integration (send images to vision LLM) | `agent_controller.py` | ~50 |
| 4c.6 | Local image generation (SDXL Turbo via diffusers) | `image_gen.py` (new) | ~80 |

### Phase 4d: Polish & Integration (Est. 200 LOC)

| Step | Description | Files | LOC |
|---|---|---|---|
| 4d.1 | Workspace switching (one-click to Coworker workspace) | `ui_chat.py` | ~30 |
| 4d.2 | Preferences for overlay/moodboard toggles | `preferences.py` | ~40 |
| 4d.3 | Performance optimization (throttle overlay redraws) | `viewport_overlay.py` | ~30 |
| 4d.4 | Error handling & fallbacks (no GPU, no torch, etc.) | various | ~50 |
| 4d.5 | Documentation & user guide | `_misc/` | ~50 |

### Total Estimated: ~1,350 LOC across 6 new files + modifications to 4 existing files

---

## Dependencies & Prerequisites

### Required Python Packages (for image generation)

```
diffusers>=0.31.0
torch>=2.0.0
transformers>=4.40.0
accelerate>=0.30.0
Pillow>=10.0.0
numpy>=1.24.0
```

These would be added to the vendor deps auto-install system in `agent_controller.py`.
**Note**: `torch` is ~2 GB — this is a significant download. Image generation should
be an optional feature, not installed by default.

### Blender Version Requirements

- Blender 5.0+ for all GPU drawing features
- Blender 5.1+ for `gpu.types.GPUShaderCreateInfo` (custom shaders)
- No version-specific requirements for the basic overlay features

### Hardware Requirements

| Feature | Minimum | Recommended |
|---|---|---|
| Viewport overlays | Any GPU | Any GPU |
| Agent focus highlight | Any GPU | Any GPU |
| Local image generation (SD 1.5) | 8 GB RAM, any GPU | 8 GB VRAM |
| Local image generation (SDXL Turbo) | 16 GB RAM, 8 GB VRAM | 16 GB VRAM |
| Moodboard (no generation) | Any | Any |

---

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| GPU overlay performance impact | Medium | Medium | Throttle redraws, use simple shaders, disable when not visible |
| `torch`/`diffusers` install failures | High | Low | Make image generation optional, provide clear error messages, offer API fallback |
| Blender version incompatibility | Low | High | Version-guard all GPU code, test on 5.0-5.2 |
| Overlay visual clutter | Medium | Low | All overlays toggleable in preferences, sensible defaults |
| Moodboard image memory usage | Medium | Medium | Auto-downscale large images, warn on memory pressure |
| Vision model availability | Medium | Medium | Fall back to text-only context if no vision model available |

---

## Comparison: Python Addon vs C++ Fork (Mixar Approach)

| Aspect | Python Addon (Our Plan) | C++ Fork (Mixar) |
|---|---|---|
| **Custom editor spaces** | ❌ Not possible | ✅ Native C++ spaces |
| **Viewport overlays** | ✅ GPU draw handlers | ✅ Native rendering |
| **Chat rendering** | ⚠️ Panels + GPU text | ✅ Native C++ chat UI |
| **Performance** | ⚠️ Python overhead | ✅ Native speed |
| **Installation** | ✅ One-click addon install | ❌ Custom Blender build |
| **Portability** | ✅ Works on any Blender | ❌ Tied to fork version |
| **Maintenance** | ✅ Pure Python, easy to update | ❌ Must rebase on Blender updates |
| **User opt-in/out** | ✅ Enable/disable in preferences | ❌ Must switch Blender versions |
| **Feature velocity** | ✅ Fast iteration | ❌ Slow (C++ compile cycles) |

**Conclusion**: The Python addon approach trades some visual polish for massive gains
in portability, maintainability, and user choice. The GPU drawing capabilities in
Blender's Python API are sufficient for 80%+ of the Mixar visual experience.

---

## Decisions

| Decision | Rationale |
|---|---|
| **Use existing Space types + overlays** (not C++ fork) | Maintains portability and user opt-in/out. C++ fork is too invasive. |
| **SDXL Turbo for local generation** | Best speed/quality tradeoff. 1-4 steps, good results, runs on consumer GPUs. |
| **Image generation is optional** | `torch` is 2 GB. Users opt in via preferences. API fallback available. |
| **Moodboard uses Image Editor** | Reuses existing Blender infrastructure. No need to build a custom canvas. |
| **Overlays are toggleable** | Users who find them distracting can disable them. Sensible defaults. |
| **Coworker workspace is auto-created** | Reduces setup friction. Users can delete it if unwanted. |

---

## Further Considerations

1. **Vision model support**: The moodboard's AI context feature requires a vision-capable
   LLM. Gemma 3 12B Vision (already in model presets) supports this. Remote APIs
   (GPT-4V, Claude) also work.

2. **Multi-monitor**: The Coworker workspace could be placed on a second monitor
   while the main 3D view is on the primary. Blender supports this natively.

3. **Collaboration**: Future extension could allow sharing moodboards between users
   via the MCP protocol — one user's moodboard becomes another's context.

4. **Animation feedback**: The viewport overlay system could be extended to show
   animation previews, motion paths, and timing information during agent-driven
   animation workflows.

5. **Audio feedback**: Optional sound effects for agent state changes (thinking
   start/stop, tool complete, error) could improve the "presence" feeling without
   visual clutter.