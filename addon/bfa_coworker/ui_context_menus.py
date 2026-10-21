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

from typing import Any, Callable

import bpy  # pylint: disable=import-error
from bpy.props import StringProperty  # pylint: disable=import-error
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


# -- Translate (#42) -----------------------------------------------------------------

def _python_path(pointer: Any, identifier: str) -> str:
    try:
        return "{!r}.{:s}".format(pointer, identifier)
    except (TypeError, ValueError):
        return ""


def translate_text(path: str, current: str, language: str, what: str) -> str:
    """The instruction for the chat: exact, so a small model gets it right too."""
    if path.endswith(".as_string()"):          # a Text datablock: edited as a whole
        text = path[:-len(".as_string()")]
        return ("Translate {:s} into {:s}: “{:s}”. Edit the text {:s} with execute_blender_code: "
                "replace just that part with its translation and write it back with {:s}.from_string(...); keep "
                "line breaks, code, names and placeholders, and change nothing else. Then reply with just the "
                "translation.").format(what, language, current, text, text)
    return ("Translate {:s} into {:s}. It is the Python property {:s}, now: \u201c{:s}\u201d. Set it to the "
            "translation with execute_blender_code ({:s} = \"...\"); keep line breaks, names, numbers and "
            "placeholders as they are, and change nothing else. Then reply with just the translation.").format(
        what, language, path, current, path)


class BFACW_OT_translate(Operator):  # type: ignore[misc]
    """Translate this text with Coworker: pick the language (remembered), and it sets the text for you"""
    bl_idname = "bfacw.translate"
    bl_label = "Translate"
    bl_options = {'INTERNAL'}

    path: StringProperty(options={'SKIP_SAVE', 'HIDDEN'})  # type: ignore[valid-type]
    current: StringProperty(options={'SKIP_SAVE', 'HIDDEN'})  # type: ignore[valid-type]
    what: StringProperty(default="this text", options={'SKIP_SAVE', 'HIDDEN'})  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        if not chat_ready():
            cls.poll_message_set("Start Coworker first (its own chat: not in External Harness)")
            return False
        return True

    def invoke(self, context: bpy.types.Context, _event) -> set[str]:
        return context.window_manager.invoke_props_dialog(self, width=320, title="Translate", confirm_text="Translate")

    def draw(self, context: bpy.types.Context) -> None:
        prefs = context.preferences.addons[__package__].preferences
        layout = self.layout
        layout.label(text=self.what[:60], icon='FONT_DATA')
        if self.current:
            sub = layout.row()
            sub.scale_y = 0.8
            sub.label(text="\u201c{:s}\u201d".format(self.current[:60] + ("..." if len(self.current) > 60 else "")))
        layout.prop(prefs, "translate_language")

    def execute(self, context: bpy.types.Context) -> set[str]:
        if not self.path:
            return {'CANCELLED'}
        prefs = context.preferences.addons[__package__].preferences
        name = prefs.bl_rna.properties["translate_language"].enum_items[prefs.translate_language].name
        if not _ask(context, translate_text(self.path, self.current, name, self.what), "AGENT"):
            return {'CANCELLED'}
        self.report({'INFO'}, "Translating into {:s} -- see the chat".format(name))
        return {'FINISHED'}


def _translate_op(layout: Any, path: str, current: str, what: str, label: str) -> None:
    op = layout.operator("bfacw.translate", text=label, icon='FONT_DATA')
    op.path, op.current, op.what = path, current[:2000], what


def _translate_entry(layout: Any, _context: Any, target: dict) -> None:
    """Any text field (a string property that isn't a file path)."""
    if target["kind"] != "property" or target["type"] != "string":
        return
    if target.get("subtype") in ("FILE_PATH", "DIR_PATH", "FILE_NAME", "PASSWORD"):
        return
    path = _python_path(target["pointer"], target["identifier"])
    if path:
        _translate_op(layout, path, target["value"], "the text \u201c{:s}\u201d".format(target["label"]),
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
    op = self.layout.operator("bfacw.translate", text=label, icon='FONT_DATA')
    # A Text is edited as a whole: the instruction names the part, the path is the datablock.
    op.path, op.current, op.what = "{!r}".format(text) + ".as_string()", chosen[:2000], what


# -- the menus ------------------------------------------------------------------------

# Entries for the button menu: (draw(layout, context, target) -> None).  Translate adds itself.
BUTTON_ENTRIES: list[Callable[[Any, Any, dict], None]] = []


def _explain_entry(layout: Any, _context: Any, target: dict) -> None:
    layout.operator("bfacw.explain", text="Explain “{:s}”".format(target["label"][:40]),
                    icon='QUESTION').text = explain_text(target)


def _draw_button_menu(self: Any, context: Any) -> None:
    target = clicked(context)
    if target is None:
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
_CLASSES = (BFACW_OT_explain, BFACW_OT_translate)
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
