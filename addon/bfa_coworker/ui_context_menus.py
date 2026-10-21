# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Coworker in Blender's right-click menus (Tier 4: #290 Explain This, #42 Translate).

Two places:

* **The button menu** (right-click on any property or operator button):
  ``UI_MT_button_context_menu``.  The draw reads what was clicked from the
  context -- ``button_pointer`` + ``button_prop`` for a property,
  ``button_operator`` for an operator button.
* **The editors' own menus** (right-click in the 3D View, the node editor, the
  sequencer...): "Explain This Editor".

Every entry asks the chat for you (the prompt says exactly what was clicked)
and is greyed out until Coworker is started.  :func:`clicked` is the shared
groundwork; later entries (Translate) add themselves to :data:`BUTTON_ENTRIES`.
"""

from __future__ import annotations

__all__ = (
    "BUTTON_ENTRIES",
    "chat_ready",
    "clicked",
    "register",
    "unregister",
)

import time
from types import SimpleNamespace
from typing import Any, Callable

import bpy  # pylint: disable=import-error
from bpy.props import BoolProperty, StringProperty  # pylint: disable=import-error
from bpy.types import Operator  # pylint: disable=import-error

_VALUE_CHARS = 200

# The editors' right-click menus "Explain This Editor" joins (the ones this build has).
_EDITOR_MENUS = (
    "VIEW3D_MT_object_context_menu", "VIEW3D_MT_edit_mesh_context_menu", "NODE_MT_context_menu",
    "SEQUENCER_MT_context_menu", "TEXT_MT_context_menu", "OUTLINER_MT_context_menu",
    "DOPESHEET_MT_context_menu", "GRAPH_MT_context_menu", "NLA_MT_context_menu",
    "CLIP_MT_tracking_context_menu", "CONSOLE_MT_context_menu", "INFO_MT_context_menu",
    "FILEBROWSER_MT_context_menu", "ASSETBROWSER_MT_context_menu", "IMAGE_MT_uvs_context_menu",
)


def chat_ready() -> bool:
    """Coworker is started and its own chat can take a message (not External Harness)."""
    try:
        prefs = bpy.context.preferences.addons[__package__].preferences
    except (KeyError, AttributeError):
        return False
    if prefs.operating_mode == "EXTERNAL_HARNESS":
        return False
    from . import agent_controller, ui_chat
    if prefs.operating_mode == "EXTERNAL_AGENT":
        return bool(ui_chat._acp is not None and ui_chat._acp.connected)
    return bool(agent_controller._agent_state.mcp_server_running)


def _short(value: Any) -> str:
    text = str(value)
    return text if len(text) <= _VALUE_CHARS else text[:_VALUE_CHARS] + "..."


def _editor_name(context: Any) -> str:
    area = getattr(context, "area", None)
    if area is None:
        return ""
    ui_type = getattr(area, "ui_type", "") or area.type
    try:
        items = bpy.types.Area.bl_rna.properties["ui_type"].enum_items
        return items[ui_type].name
    except (KeyError, AttributeError):
        return ui_type.replace("_", " ").title()


def clicked(context: Any) -> dict[str, Any] | None:
    """What the button menu was opened on: a property or an operator, as plain facts.

    ``{"kind": "property", "label", "path", "type", "value", "description", "editor", "rna"}`` or
    ``{"kind": "operator", "label", "idname", "description", "editor"}``; None for anything else.
    """
    editor = _editor_name(context)
    prop = getattr(context, "button_prop", None)
    pointer = getattr(context, "button_pointer", None)
    if prop is not None and pointer is not None:
        try:
            value = getattr(pointer, prop.identifier)
        except (AttributeError, TypeError):
            value = ""
        if prop.type == "ENUM":
            try:
                value = prop.enum_items[value].name if isinstance(value, str) and value else value
            except (KeyError, TypeError):
                pass
        elif prop.type == "POINTER":
            value = getattr(value, "name", "") or ""
        try:
            path = "{!r}.{:s}".format(pointer, prop.identifier)
        except (TypeError, ValueError):
            path = prop.identifier
        return {"kind": "property", "label": prop.name or prop.identifier, "path": path.replace("bpy.data.", ""),
                "type": prop.type.lower(), "subtype": getattr(prop, "subtype", ""), "value": _short(value),
                "description": prop.description, "editor": editor, "rna": pointer.bl_rna.identifier,
                "pointer": pointer, "identifier": prop.identifier}
    op = getattr(context, "button_operator", None)
    if op is not None:
        rna = getattr(op, "bl_rna", None)
        ident = getattr(rna, "identifier", "") or ""
        idname = ident.replace("_OT_", ".", 1).lower() if "_OT_" in ident else ident
        return {"kind": "operator", "label": getattr(rna, "name", "") or idname, "idname": idname,
                "description": getattr(rna, "description", "") or "", "editor": editor}
    return None


def _ask(context: Any, text: str, mode: str) -> bool:
    """Put *text* in the chat and send it (in *mode*); False when the chat can't take it."""
    props = getattr(context.window_manager, "bfacw_chat_props", None)
    if props is None or not chat_ready():
        return False
    props.chat_mode = mode
    props.chat_input = text
    bpy.ops.bfacw.chat_send()
    return True


# -- Explain This (#290) ------------------------------------------------------------

def explain_text(target: dict[str, Any]) -> str:
    """The question for *target* (a :func:`clicked` result, or an editor's facts)."""
    where = " in {:s}".format(target["editor"]) if target.get("editor") else ""
    if target["kind"] == "property":
        text = "Explain this setting: “{:s}”{:s} ({:s}, {:s}, now {:s}).".format(
            target["label"], where, target["path"], target["type"], target["value"] or "empty")
    elif target["kind"] == "operator":
        text = "Explain this tool: “{:s}”{:s} (the operator {:s}).".format(
            target["label"], where, target["idname"])
    else:
        text = "Explain this editor: the {:s}{:s}.".format(target["editor"] or "editor", target.get("state", ""))
    if target.get("description"):
        text += " Its tooltip says: “{:s}”.".format(target["description"])
    return text + " What does it do, and when would I use it? Keep it short and practical."


def _editor_facts(context: Any) -> dict[str, Any]:
    bits = []
    mode = getattr(context, "mode", "")
    if mode:
        bits.append("mode {:s}".format(mode.replace("_", " ").title()))
    obj = getattr(context, "active_object", None)
    if obj is not None:
        bits.append("active: {:s} ({:s})".format(obj.name, obj.type.title()))
    space = getattr(context, "space_data", None)
    tree = getattr(space, "edit_tree", None)
    if tree is not None:
        bits.append("node tree: {:s}".format(tree.name))
    return {"kind": "editor", "editor": _editor_name(context), "state": " ({:s})".format(", ".join(bits)) if bits else ""}


class BFACW_OT_explain(Operator):  # type: ignore[misc]
    """Ask Coworker to explain what you right-clicked, in Ask mode (it changes nothing)"""
    bl_idname = "bfacw.explain"
    bl_label = "Explain This"
    bl_options = {'INTERNAL'}

    text: StringProperty(options={'SKIP_SAVE', 'HIDDEN'})  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        if not chat_ready():
            cls.poll_message_set("Start Coworker first (its own chat: not in External Harness)")
            return False
        return True

    def execute(self, context: bpy.types.Context) -> set[str]:
        if not self.text or not _ask(context, self.text, "ASK"):
            return {'CANCELLED'}
        self.report({'INFO'}, "Asked Coworker -- the answer is in the chat")
        return {'FINISHED'}


def spot_text(editor: str, region: str) -> str:
    """The question for a button seen only in a picture (menus, popovers: Python can't name them)."""
    where = " in the {:s}".format(editor) if editor else ""
    part = {"HEADER": "header", "TOOL_HEADER": "tool settings bar", "UI": "sidebar", "TOOLS": "toolbar",
            "FOOTER": "footer", "WINDOW": "main area"}.get(region, "")
    if part:
        where += "'s {:s}".format(part) if editor else " in the {:s}".format(part)
    return ("Explain the button I right-clicked{:s}: it's the one inside the red ring in the attached "
            "picture (a crop of the screen around my mouse). Say what it is, what it does, and when I'd use it. "
            "Keep it short and practical.").format(where)


class BFACW_OT_explain_spot(Operator):  # type: ignore[misc]
    """Ask Coworker what the button you right-clicked is, with a picture of it (Ask mode)"""
    bl_idname = "bfacw.explain_spot"
    bl_label = "Explain This"
    bl_options = {'INTERNAL'}

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        if not chat_ready():
            cls.poll_message_set("Start Coworker first (its own chat: not in External Harness)")
            return False
        return True

    def invoke(self, context: bpy.types.Context, event) -> set[str]:
        # The right-click that opened this menu was the press before this click.
        x, y = event.mouse_prev_press_x, event.mouse_prev_press_y
        wm = context.window_manager
        index = next((i for i, w in enumerate(wm.windows) if w == context.window), 0)
        # Let the menu close and the window redraw before the picture is taken.
        bpy.app.timers.register(lambda: _explain_spot(index, x, y), first_interval=0.3)
        return {'FINISHED'}


def _ring(path: str, cx: int, cy: int, radius: int = 14) -> None:
    """Mark the clicked spot on the picture with a red ring, so the model looks there."""
    import numpy as np
    img = bpy.data.images.load(path, check_existing=False)
    try:
        w, h = img.size
        px = np.array(img.pixels[:], dtype=np.float32).reshape(h, w, 4)
        yy, xx = np.ogrid[:h, :w]
        d = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
        px[(d >= radius - 1.5) & (d <= radius + 1.5)] = (1.0, 0.15, 0.1, 1.0)
        img.pixels[:] = px.ravel()
        img.filepath_raw, img.file_format = path, "PNG"
        img.save()
    finally:
        bpy.data.images.remove(img)


def _explain_spot(window_index: int, x: int, y: int) -> None:
    from . import chat_attachments
    wm = bpy.context.window_manager
    if window_index >= len(wm.windows):
        return None
    window = wm.windows[window_index]
    editor, region = "", ""
    for area in window.screen.areas:
        if area.x <= x < area.x + area.width and area.y <= y < area.y + area.height:
            from .ui_chat import _EDITOR_NAMES
            editor = _EDITOR_NAMES.get(area.type) or _editor_name(SimpleNamespace(area=area))
            region = next((r.type for r in area.regions
                           if r.x <= x < r.x + r.width and r.y <= y < r.y + r.height), "")
            break
    half_w, half_h = 180, 70
    x0, y0 = max(0, x - half_w), max(0, y - half_h)
    path = chat_attachments.capture_screen_rect(window, x0, y0, min(window.width, x + half_w),
                                                min(window.height, y + half_h))
    if path:
        _ring(path, x - x0, y - y0)
    props = getattr(wm, "bfacw_chat_props", None)
    if path and props is not None:
        try:
            props.chat_image = bpy.data.images.load(path, check_existing=False)
            props.chat_image_send_once = True
        except (RuntimeError, OSError):
            pass
    with bpy.context.temp_override(window=window, screen=window.screen):
        _ask(bpy.context, spot_text(editor, region), "ASK")
    return None


# -- Translate (#42) -----------------------------------------------------------------

def _python_path(pointer: Any, identifier: str) -> str:
    try:
        return "{!r}.{:s}".format(pointer, identifier)
    except (TypeError, ValueError):
        return ""


def translate_text(path: str, current: str, language: str, what: str, source: str = "") -> str:
    """The question for the chat (Ask mode): just the translation back; Coworker writes it in
    itself (:func:`apply_translation`), so it works on every engine, with or without tools.
    *source* is the language the text is in ("": let the model detect it). *path* is unused
    here (kept for the callers): the reply alone is the answer."""
    del path
    if source:
        what = "{:s} from {:s}".format(what, source)
    return ("Translate {:s} into {:s}:\n\n{:s}\n\nReply with only the translated text: no quotes, no notes, "
            "nothing else. Keep line breaks, names, numbers, code and placeholders as they are.").format(
        what, language, current)


def clean_reply(reply: str) -> str:
    """The translation out of a reply: without a code fence or surrounding quotes."""
    text = reply.strip()
    if text.startswith("```") and text.endswith("```") and text.count("\n") >= 1:
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip("\n")
    for a, b in (("\u201c", "\u201d"), ('"', '"'), ("'", "'"), ("\u00ab", "\u00bb"), ("\u300c", "\u300d")):
        if len(text) >= 2 and text.startswith(a) and text.endswith(b):
            text = text[len(a):-len(b)]
            break
    return text


def splice(full: str, span: tuple[int, int, int, int], new: str) -> str:
    """*full* with the text from (line, char) to (line, char) replaced by *new*."""
    lines = full.split("\n")
    l0, c0, l1, c1 = span

    def offset(line: int, char: int) -> int:
        return sum(len(x) + 1 for x in lines[:line]) + char
    return full[:offset(l0, c0)] + new + full[offset(l1, c1):]


class BFACW_OT_translate(Operator):  # type: ignore[misc]
    """Translate this text with Coworker: pick the language (remembered), and it sets the text for you"""
    bl_idname = "bfacw.translate"
    bl_label = "Translate"
    bl_options = {'INTERNAL'}

    path: StringProperty(options={'SKIP_SAVE', 'HIDDEN'})  # type: ignore[valid-type]
    current: StringProperty(options={'SKIP_SAVE', 'HIDDEN'})  # type: ignore[valid-type]
    what: StringProperty(default="this text", options={'SKIP_SAVE', 'HIDDEN'})  # type: ignore[valid-type]
    shown: BoolProperty(options={'SKIP_SAVE', 'HIDDEN'})  # type: ignore[valid-type]  # the dialog was up
    span: StringProperty(options={'SKIP_SAVE', 'HIDDEN'})  # type: ignore[valid-type]  # Text Editor selection

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        if not chat_ready():
            cls.poll_message_set("Start Coworker first (its own chat: not in External Harness)")
            return False
        return True

    def invoke(self, context: bpy.types.Context, _event) -> set[str]:
        self.shown = True
        return context.window_manager.invoke_props_dialog(self, width=320, title="Translate", confirm_text="Translate")

    def draw(self, context: bpy.types.Context) -> None:
        prefs = context.preferences.addons[__package__].preferences
        layout = self.layout
        layout.label(text=self.what[:60], icon='FONT_DATA')
        if self.current:
            sub = layout.row()
            sub.scale_y = 0.8
            sub.label(text="\u201c{:s}\u201d".format(self.current[:60] + ("..." if len(self.current) > 60 else "")))
        # From [swap] To, both remembered (#42).
        row = layout.row(align=True)
        col = row.column(align=True)
        col.label(text="From")
        col.prop(prefs, "translate_from", text="")
        mid = row.column(align=True)
        mid.ui_units_x = 1.2            # a square button between the two lists
        mid.label(text="")
        mid.operator("bfacw.translate_swap", text="", icon='ARROW_LEFTRIGHT')
        col = row.column(align=True)
        col.label(text="To")
        col.prop(prefs, "translate_language", text="")

    def execute(self, context: bpy.types.Context) -> set[str]:
        if not self.path:
            return {'CANCELLED'}
        if not self.shown:      # run straight from a menu that executes its items: show the dialog first
            args = {"path": self.path, "current": self.current, "what": self.what, "span": self.span}
            index = next((i for i, w in enumerate(context.window_manager.windows) if w == context.window), 0)
            bpy.app.timers.register(lambda: _translate_dialog(index, args), first_interval=0.05)
            return {'FINISHED'}
        prefs = context.preferences.addons[__package__].preferences
        name = prefs.bl_rna.properties["translate_language"].enum_items[prefs.translate_language].name
        source = ("" if prefs.translate_from == "auto"
                  else prefs.bl_rna.properties["translate_from"].enum_items[prefs.translate_from].name)
        history = agent_controller_history()
        if not _ask(context, translate_text(self.path, self.current, name, self.what, source), "ASK"):
            return {'CANCELLED'}
        _PENDING.update(path=self.path, span=self.span, seen=len(history), since=time.time())
        if not bpy.app.timers.is_registered(_apply_when_answered):
            bpy.app.timers.register(_apply_when_answered, first_interval=0.5)
        self.report({'INFO'}, "Translating into {:s} -- it is written in when the answer comes".format(name))
        return {'FINISHED'}


# The translation waiting for its answer: written in by Coworker, not by the agent.
_PENDING: dict[str, Any] = {}


def agent_controller_history() -> list:
    from . import agent_controller
    return agent_controller._agent_state.conversation_history


def _apply_when_answered() -> float | None:
    from . import agent_controller
    if not _PENDING:
        return None
    state = agent_controller._agent_state
    history = agent_controller_history()
    reply = next((m.get("content") or "" for m in reversed(history[_PENDING["seen"]:])
                  if m.get("role") == "assistant" and (m.get("content") or "").strip()), "")
    if state.is_thinking or not reply:          # the turn still runs, or hasn't answered yet
        if time.time() - _PENDING["since"] > 600:       # no answer in 10 minutes: give up
            _PENDING.clear()
            return None
        return 0.5
    pending = dict(_PENDING)
    _PENDING.clear()
    text = clean_reply(reply)
    if text:
        try:
            apply_translation(pending["path"], pending["span"], text)
        except Exception as ex:  # pylint: disable=broad-exception-caught
            print("[Coworker] Translate: couldn't write the translation in: {!s}".format(ex))
    return None


def _editing_text_object(data: Any) -> dict | None:
    """A context override for the text object in Edit mode that uses *data*, else None."""
    if not isinstance(data, bpy.types.TextCurve):
        return None
    for window in bpy.context.window_manager.windows:
        layer = window.view_layer
        obj = layer.objects.active if layer else None
        if obj is None or obj.data != data or obj.mode != 'EDIT':
            continue
        area = next((a for a in window.screen.areas if a.type == 'VIEW_3D'), None)
        override = {"window": window, "screen": window.screen, "active_object": obj, "object": obj}
        if area is not None:
            override.update(area=area, region=next((r for r in area.regions if r.type == 'WINDOW'), None))
        return override
    return None


def apply_translation(path: str, span: str, text: str) -> None:
    """Write *text* to *path* (``owner.attr``, or ``text.as_string()`` with a *span*), one undo step."""
    namespace = {"bpy": bpy}
    if path.endswith(".as_string()"):
        block = eval(path[:-len(".as_string()")], namespace)          # noqa: S307 -- our own repr()
        if span:
            block.from_string(splice(block.as_string(), tuple(int(v) for v in span.split(",")), text))
        else:
            block.from_string(text)
    else:
        owner, attr = path.rsplit(".", 1)
        target = eval(owner, namespace)                                # noqa: S307 -- our own repr()
        editing = _editing_text_object(target)
        if editing is None:
            setattr(target, attr, text)
        else:
            # A text object in Edit mode keeps its typing in an edit buffer that overwrites
            # body when you leave: step out, set it, and step back in.
            with bpy.context.temp_override(**editing):
                bpy.ops.object.mode_set(mode='OBJECT')
                setattr(target, attr, text)
                bpy.ops.object.mode_set(mode='EDIT')
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            area.tag_redraw()
    try:
        bpy.ops.ed.undo_push(message="Translate")
    except RuntimeError:
        pass


def _translate_dialog(window_index: int, args: dict) -> None:
    wm = bpy.context.window_manager
    if window_index < len(wm.windows):
        window = wm.windows[window_index]
        with bpy.context.temp_override(window=window, screen=window.screen):
            bpy.ops.bfacw.translate('INVOKE_DEFAULT', **args)
    return None


class BFACW_OT_translate_swap(Operator):  # type: ignore[misc]
    """Swap the From and To languages"""
    bl_idname = "bfacw.translate_swap"
    bl_label = "Swap Languages"
    bl_options = {'INTERNAL'}

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        prefs = context.preferences.addons[__package__].preferences
        if prefs.translate_from == "auto":
            cls.poll_message_set("Pick the From language first (not Detect)")
            return False
        return True

    def execute(self, context: bpy.types.Context) -> set[str]:
        prefs = context.preferences.addons[__package__].preferences
        prefs.translate_from, prefs.translate_language = prefs.translate_language, prefs.translate_from
        return {'FINISHED'}


def _translate_op(layout: Any, path: str, current: str, what: str, label: str) -> None:
    layout.operator_context = 'INVOKE_DEFAULT'      # some menus run their items without a dialog
    op = layout.operator("bfacw.translate", text=label, icon='FONT_DATA')
    op.path, op.current, op.what = path, current[:2000], what


def _translate_entry(layout: Any, _context: Any, target: dict) -> None:
    """Any text field, file paths and names too (not passwords)."""
    if target["kind"] != "property" or target["type"] != "string":
        return
    if target.get("subtype") == "PASSWORD":
        return
    path = _python_path(target["pointer"], target["identifier"])
    full = getattr(target["pointer"], target["identifier"], target["value"])     # not the shortened one
    if path:
        _translate_op(layout, path, str(full), "the text \u201c{:s}\u201d".format(target["label"]),
                      "Translate \u201c{:s}\u201d...".format(target["label"][:40]))


def _draw_text_object_menu(self: Any, context: Any) -> None:
    obj = getattr(context, "active_object", None)
    if obj is None or obj.type != 'FONT':
        return
    self.layout.separator()
    _translate_op(self.layout, _python_path(obj.data, "body"), obj.data.body,
                  "the text object \u201c{:s}\u201d".format(obj.name), "Translate Text...")


def _draw_text_strip_menu(self: Any, context: Any) -> None:
    editor = getattr(context.scene, "sequence_editor", None)
    strip = getattr(editor, "active_strip", None)
    if strip is None or strip.type != 'TEXT':
        return
    self.layout.separator()
    _translate_op(self.layout, _python_path(strip, "text"), strip.text,
                  "the text strip \u201c{:s}\u201d".format(strip.name), "Translate Text Strip...")


def _draw_text_editor_menu(self: Any, context: Any) -> None:
    text = getattr(getattr(context, "space_data", None), "text", None)
    if text is None:
        return
    a = (text.current_line_index, text.current_character)
    b = (text.select_end_line_index, text.select_end_character)
    (l0, c0), (l1, c1) = sorted((a, b))
    lines = [line.body for line in text.lines]
    if (l0, c0) != (l1, c1):
        chosen = "\n".join(lines[l0:l1 + 1])
        chosen = chosen[c0:len(chosen) - (len(lines[l1]) - c1)] if lines else ""
        what = "the selected text (lines {:d}-{:d}) of the text \u201c{:s}\u201d".format(l0 + 1, l1 + 1, text.name)
        label = "Translate Selection..."
    else:
        chosen, what, label = text.as_string(), "the text \u201c{:s}\u201d".format(text.name), "Translate Text..."
    self.layout.separator()
    self.layout.operator_context = 'INVOKE_DEFAULT'
    op = self.layout.operator("bfacw.translate", text=label, icon='FONT_DATA')
    # A Text is edited as a whole: the instruction names the part, the path is the datablock.
    op.path, op.current, op.what = "{!r}".format(text) + ".as_string()", chosen[:2000], what
    if (l0, c0) != (l1, c1):
        op.span = "{:d},{:d},{:d},{:d}".format(l0, c0, l1, c1)


# -- the menus ------------------------------------------------------------------------

# Entries for the button menu: (draw(layout, context, target) -> None).  Translate adds itself.
BUTTON_ENTRIES: list[Callable[[Any, Any, dict], None]] = []


def _explain_entry(layout: Any, _context: Any, target: dict) -> None:
    layout.operator("bfacw.explain", text="Explain “{:s}”".format(target["label"][:40]),
                    icon='QUESTION').text = explain_text(target)


def _draw_button_menu(self: Any, context: Any) -> None:
    target = clicked(context)
    if target is None:      # a menu, popover or other button Python can't name: show it a picture
        self.layout.separator()
        self.layout.operator_context = 'INVOKE_DEFAULT'     # its invoke reads where you right-clicked
        self.layout.operator("bfacw.explain_spot", text="Explain This", icon='QUESTION')
        return
    layout = self.layout
    layout.separator()
    for entry in BUTTON_ENTRIES:
        try:
            entry(layout, context, target)
        except Exception as ex:  # pylint: disable=broad-exception-caught
            print("[Coworker] context menu entry failed: {!s}".format(ex))


def _draw_editor_menu(self: Any, context: Any) -> None:
    layout = self.layout
    layout.separator()
    layout.operator("bfacw.explain", text="Explain This Editor", icon='QUESTION').text = \
        explain_text(_editor_facts(context))


_HOOKED: list[tuple[type, Callable]] = []
_CLASSES = (BFACW_OT_explain, BFACW_OT_explain_spot, BFACW_OT_translate, BFACW_OT_translate_swap)
# Menus of a kind of thing, with their draw: text objects, text strips, the Text Editor (#42).
_THING_MENUS = (
    ("VIEW3D_MT_object_context_menu", _draw_text_object_menu),
    ("VIEW3D_MT_edit_font_context_menu", _draw_text_object_menu),
    ("SEQUENCER_MT_context_menu", _draw_text_strip_menu),
    ("TEXT_MT_context_menu", _draw_text_editor_menu),
)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    if _explain_entry not in BUTTON_ENTRIES:
        BUTTON_ENTRIES.insert(0, _explain_entry)
    if _translate_entry not in BUTTON_ENTRIES:
        BUTTON_ENTRIES.append(_translate_entry)
    for name, draw in _THING_MENUS:
        menu = getattr(bpy.types, name, None)
        if menu is not None:
            menu.append(draw)
            _HOOKED.append((menu, draw))
    menu = getattr(bpy.types, "UI_MT_button_context_menu", None)
    if menu is not None:
        menu.append(_draw_button_menu)
        _HOOKED.append((menu, _draw_button_menu))
    for name in _EDITOR_MENUS:
        menu = getattr(bpy.types, name, None)
        if menu is not None:
            menu.append(_draw_editor_menu)
            _HOOKED.append((menu, _draw_editor_menu))


def unregister() -> None:
    for menu, fn in _HOOKED:
        try:
            menu.remove(fn)
        except (ValueError, RuntimeError):
            pass
    _HOOKED.clear()
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
