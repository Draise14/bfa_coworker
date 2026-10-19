# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
How the chat history splits into turns (shared by the panel and the GPU chat).

Moved out of ``ui_chat.py`` unchanged (Tier 4, task "GPU 2") so the sidebar
panel and the GPU-drawn chat (``ui_gpu.chat_view``) always agree on what a
turn is: one real user message, the Workshop messages behind it, and the
assistant reply without tool calls.  ``ui_chat`` imports these under their
old private names.  Free of ``bpy``.
"""

from __future__ import annotations

__all__ = (
    "NOTE_TITLES",
    "group_turns",
    "is_system_note_msg",
    "split_turn",
)


# Friendly Workshop titles for injected notes, keyed by ``system_note`` kind
# (the icon is a Blender icon name; the GPU chat draws its own glyph for it).
NOTE_TITLES = {
    "followup": ("Coworker \u2192 itself: keep going", 'FORWARD'),
    "continue": ("Continued after the output limit", 'TRIA_RIGHT'),
    "nudge": ("Nudge: act, don't just describe", 'PLAY'),
    "entity": ("Scene context", 'OUTLINER_OB_MESH'),
    "scene_change": ("You changed the scene mid-turn", 'VIEW_PAN'),
    "spiral": ("Repeated-error guidance", 'ERROR'),
    "malformed": ("Re-emit a broken tool call", 'ERROR'),
    "wrapup": ("Step budget reached -- progress report", 'TIME'),
}


def is_system_note_msg(msg: dict) -> bool:
    """True for an agent-injected user-role note (never a real user turn).

    Mirrors ``session_memory.is_system_note`` (kept self-contained so the
    draw path never imports): the structured ``system_note`` flag first,
    then the ``[System:`` prefix, then the known legacy prompt -- the
    unprefixed "Continue." older builds could leave in the history, which
    rendered as a NEW user turn written by the agent itself.
    """
    if msg.get("role") != "user":
        return False
    if msg.get("system_note"):
        return True
    content = msg.get("content")
    if isinstance(content, list):
        content = " ".join(
            str(b.get("text", "")) for b in content if isinstance(b, dict))
    text = str(content or "").lstrip()
    return (text.startswith("[System:")
            or text.startswith("Continue. Keep this next step small"))


def group_turns(history: list) -> list[list[dict]]:
    """Group conversation history into turns (one real user send = one turn).

    A real user message always starts a turn; agent-injected messages begin
    with ``[System:`` and are excluded.  This deliberately does NOT rely on
    the ``turn_start`` flag: that flag can be lost when the history is sliced,
    compacted, or reloaded, and its loss dropped the user's own message into
    the collapsed Workshop (so it looked like it had disappeared).  UI-only
    greetings never anchor a turn -- otherwise the welcome created a phantom
    "Turn 1" with no user message.
    """
    turns: list[list[dict]] = []
    current_turn: list[dict] = []
    for msg in history:
        if msg.get("ui_only"):
            continue
        role = msg.get("role", "")
        if role == "user" and not is_system_note_msg(msg):
            if current_turn:
                turns.append(current_turn)
            current_turn = [msg]
        elif role in ("assistant", "tool", "reasoning", "user", "compaction"):
            current_turn.append(msg)
    if current_turn:
        turns.append(current_turn)
    return turns


def split_turn(turn: list[dict]) -> tuple[dict | None, list[dict], dict | None]:
    """Split a turn into (user message, process messages, conclusion message)."""
    user_msg: dict | None = None
    process_msgs: list[dict] = []
    conclusion_msg: dict | None = None
    for msg in turn:
        role = msg.get("role", "")
        if role == "user" and not is_system_note_msg(msg):
            user_msg = msg
        elif role in ("reasoning", "tool", "user", "compaction") or is_system_note_msg(msg):
            process_msgs.append(msg)
        elif role == "assistant":
            if not msg.get("tool_calls"):
                conclusion_msg = msg
    return user_msg, process_msgs, conclusion_msg
