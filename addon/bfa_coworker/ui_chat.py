# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Bforartists Coworker Chat Panel -- provides an in-Blender chat interface to the MCP agent.

Registers a ``VIEW_3D`` sidebar panel with conversation history, multi-line
input, send/clear/stop buttons, and a status bar.

Also registers a Text Editor side panel for prompt-based interaction.
"""

__all__ = (
    "ChatHistoryProperties",
    "BFACW_PT_chat_panel",
    "BFACW_PT_chat_session",
    "BFACW_PT_chat_queue",
    "BFACW_PT_chat_status",
    "BFACW_PT_chat_text_editor",
    "BFACW_OT_chat_send",
    "BFACW_OT_chat_clear",
    "BFACW_OT_chat_stop",
    "BFACW_OT_chat_queue_send",
    "BFACW_OT_chat_capture_render",
    "BFACW_OT_chat_capture_screen",
    "BFACW_OT_chat_image_drop",
    "BFACW_FH_chat_drop",
    "BFACW_OT_export_session_log",
    "BFACW_OT_copy_session_log",
    "BFACW_OT_copy_status_error",
    "BFACW_OT_agent_start",
    "BFACW_OT_agent_stop",
    "BFACW_OT_agent_restart",
    "chat_timer_update",
    "register",
    "unregister",
)

import array
import json
import os
import random
import re
import threading
import time
from pathlib import Path

import bpy  # pylint: disable=import-error
from bpy.props import (  # pylint: disable=import-error
    BoolProperty,
    EnumProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)
from bpy.types import (  # pylint: disable=import-error
    Operator,
    Panel,
    PropertyGroup,
)

import textwrap

from . import agent_controller
from . import chat_attachments
from . import llm_manager
from . import mcp_to_blender_server
from .shared import effective_ports, CHAT_MODE_ITEMS


def _sync_prefs_to_config(prefs: bpy.types.AddonPreferences) -> None:
    """Copy all relevant preference fields into llm_manager._config."""
    llm_cfg = llm_manager.get_config()
    # Derive mode from operating_mode.
    if prefs.operating_mode == "LOCAL_LLM":
        llm_cfg.mode = "local"
    elif prefs.operating_mode == "REMOTE_API":
        llm_cfg.mode = "remote"
    else:
        llm_cfg.mode = "local"  # fallback for harness mode
    llm_cfg.llama_path = prefs.llama_path
    llm_cfg.model_repo_id = prefs.model_repo_id
    llm_cfg.model_filename = prefs.model_filename
    llm_cfg.downloaded_models_dir = prefs.downloaded_models_dir
    llm_cfg.local_ctx_size = prefs.local_ctx_size
    llm_cfg.local_max_tokens = prefs.local_max_tokens
    llm_cfg.thinking_budget_tokens = prefs.thinking_budget_tokens
    # getattr with a default: a Blender session that still has an older
    # _BFACW_Preferences class registered (e.g. before a restart after an
    # addon update) must not crash agent start over a newer preference.
    llm_cfg.lock_scene_while_working = getattr(prefs, "lock_scene_while_working", True)
    llm_cfg.auto_continue_rounds = int(getattr(prefs, "auto_continue_rounds", 3))
    llm_cfg.remote_api_url = prefs.remote_api_url
    llm_cfg.remote_api_key = prefs.remote_api_key
    llm_cfg.remote_model = prefs.remote_model
    llm_manager.set_config(llm_cfg)


_WRAP_WIDTH = 60

# Panel category both chat panels live under.  The drag-and-drop handler
# uses it to tell "over the Coworker panel" from "somewhere else in the
# sidebar" (a FileHandler poll gets no mouse position, only the region).
_CHAT_PANEL_CATEGORY = "Coworker"

# Thumbnail sizing for the attached-image preview.
# Everything here follows from one fact in the C code: a preview icon is
# drawn into a SQUARE.  template_icon() marks its button BUT_ICON_PREVIEW,
# so widget_draw_preview_icon() (interface_widgets.cc) takes
# min(button_w, button_h), subtracts PREVIEW_PAD and calls
# icon_draw_preview(..., aspect=1.0f, size) -> icon_draw_size() builds
# w = h = size/aspect -> icon_draw_rect() fits the image inside that
# square, centred, aspect preserved.  A wide image is therefore never
# drawn wider than the button is tall, and shrinking the button only
# shrinks the thumbnail with it (a short button draws the image as wide
# as the button is tall).
#
# So the thumbnail buffer is made SQUARE and the letterbox bands are
# filled with a dimmed, blurred, cover-scaled copy of the same image
# (_attachment_thumbnail_pixels).  That keeps the thumbnail panel-wide,
# aspect-correct and borderless -- no empty band above or below it --
# which a square draw rect cannot do on its own.
#
# The size is quantised so dragging the sidebar does not resample on
# every single pixel of width, and the pixels come from our own buffer
# resampled to exactly the size Blender will draw them at, because a
# plain template_icon draws the datablock's 32px icon buffer
# (interface_icons.cc icon_create_rect) and looks very pixely once the
# thumbnail is panel-wide.
_UI_UNIT_BASE_PX = 20.0
_ATTACHMENT_PREVIEW_MARGIN_PX = 40
_ATTACHMENT_PREVIEW_MIN_PX = 64
_ATTACHMENT_PREVIEW_MAX_PX = 512
_ATTACHMENT_PREVIEW_QUANTUM_PX = 16
# How much the cover-scaled backdrop is darkened (see
# _attachment_thumbnail_pixels): low enough to read as a backdrop for the
# sharp image in the middle, high enough that it is not a black box.
_ATTACHMENT_THUMBNAIL_DIM = 0.4
# Images bigger than this are left to Blender's own (small) preview icon:
# the thumbnail resample copies the pixels into a float buffer, so a huge
# source would cost two buffers of 16 bytes per pixel for a thumb.
_ATTACHMENT_THUMBNAIL_MAX_SOURCE_PX = 12000000


# -- Brand detection: Bforartists has a View menu in the 3D viewport header,
#    vanilla Blender does not.  Cache the result once at import time.
_is_bfa: bool = hasattr(bpy.types, "VIEW3D_MT_view")
_AGENT_ICON: str = "WIZARD" if _is_bfa else "GHOST_ENABLED"

# Animated "thinking" spinner frames, assembled from named phase groups: a
# braille dot grows into a full cell and shrinks back (diagonal and bar
# variants), the classic multi-dot "wormy" orbit, a travelling "wavy" ripple,
# and a "block" gap rotating through a full cell.  Rather than replaying the
# same order every loop, the groups are shuffled per cycle (see
# _ordered_phases) so a long wait keeps looking new.  Written as \u escapes so
# the source stays ASCII; rendered as glyphs by Blender's UI.
_EXPAND: tuple[str, ...] = (
    "\u2801", "\u2803", "\u2807", "\u280f", "\u281f", "\u283f",
)
_CONTRACT: tuple[str, ...] = (
    "\u281f", "\u280f", "\u2807", "\u2803", "\u2801",
)
# "Wormy": the classic multi-dot braille orbit -- dots chase each other around.
_WORMY: tuple[str, ...] = (
    "\u280b", "\u2819", "\u2839", "\u2838", "\u283c",
    "\u2834", "\u2826", "\u2827", "\u2807", "\u280f",
)
# "Wavy": a ripple travelling along the braille columns.
_WAVY: tuple[str, ...] = (
    "\u2809", "\u280b", "\u2819", "\u281a", "\u2812", "\u2802", "\u2802",
    "\u2812", "\u2832", "\u2834", "\u2826", "\u2816", "\u2812", "\u2810",
)
# "Growing bar": fills the left column top-to-bottom, then the right column
# bottom-to-top, then unwinds -- a bar that grows out and collapses back.
_GROW: tuple[str, ...] = (
    "\u2801", "\u2803", "\u2807", "\u2827", "\u2837",
    "\u283f", "\u2837", "\u2827", "\u2807", "\u2803",
)
# "Block": a full cell with a gap that rotates around it.
_BLOCK: tuple[str, ...] = (
    "\u28ff", "\u28f7", "\u28ef", "\u28df", "\u287f",
    "\u28bf", "\u28fb", "\u28fd", "\u28fe", "\u28ff",
)
# "Sweep": a diagonal blade that grows corner-to-corner, then melts away.
_SWEEP: tuple[str, ...] = (
    "\u2800", "\u2840", "\u28c0", "\u28c4", "\u28e4", "\u28e6",
    "\u28f6", "\u28f7", "\u28ff", "\u28bf", "\u283f", "\u283b",
    "\u281b", "\u2819", "\u2809", "\u2808", "\u2800",
)
_PHASES: tuple[tuple[str, ...], ...] = (
    _EXPAND, _CONTRACT, _WORMY, _WAVY, _GROW, _BLOCK, _SWEEP,
)
_PHASE_TOTAL = sum(len(_phase) for _phase in _PHASES)


def _ordered_phases(cycle: int) -> list[tuple[str, ...]]:
    """Phase play order for cycle number *cycle*, deterministically shuffled.

    Seeding on the cycle number gives every widget drawn in the same frame the
    same order, and a fresh shuffle each loop -- variety with no shared mutable
    state and no mid-frame flicker.
    """
    order = list(_PHASES)
    random.Random(cycle * 2654435761 & 0xFFFFFFFF).shuffle(order)
    return order


def _spinner_glyph(tick: int) -> str:
    """Return the spinner glyph for monotonic *tick* (wraps across all phases)."""
    cycle, pos = divmod(tick, _PHASE_TOTAL)
    for phase in _ordered_phases(cycle):
        if pos < len(phase):
            return phase[pos]
        pos -= len(phase)
    return _PHASES[0][0]  # Unreachable: pos < _PHASE_TOTAL always resolves.


def _spinner_char(state) -> str:
    """Return the current animated spinner glyph for *state*."""
    return _spinner_glyph(int(getattr(state, "thinking_dots", 0) or 0))


def _phase_text(state) -> str:
    """Human label for the pre-first-token activity phase.

    So a send does not read as "frozen": "Reading your message" while the
    prompt is assembled, "Dreaming, one moment" while a local server loads,
    then "Thinking" once tokens arrive.
    """
    phase = str(getattr(state, "turn_phase", "") or "")
    if phase == "reading":
        return "Reading your message"
    if phase == "warming":
        return "Dreaming, one moment"
    return "Thinking"


def _fmt_duration(seconds: float) -> str:
    """Format a duration for display, rolling up into minutes and hours.

    Long local turns run into the minutes, so raw seconds ("742s") read badly.
    Examples: ``45s``, ``2m 05s``, ``18m``, ``1h 02m``.  Sub-second values
    render as ``0s``; negatives are clamped to ``0s``.
    """
    try:
        total = int(seconds)
    except (TypeError, ValueError):
        return ""
    if total < 0:
        total = 0
    if total < 60:
        return "{:d}s".format(total)
    if total < 3600:
        mins, secs = divmod(total, 60)
        return "{:d}m {:02d}s".format(mins, secs) if secs else "{:d}m".format(mins)
    hours, rem = divmod(total, 3600)
    mins = rem // 60
    return "{:d}h {:02d}m".format(hours, mins)


def _is_system_note_msg(msg: dict) -> bool:
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


# Friendly Workshop titles for injected notes, keyed by ``system_note`` kind.
_NOTE_TITLES = {
    "followup": ("Coworker → itself: keep going", 'FORWARD'),
    "continue": ("Continued after the output limit", 'TRIA_RIGHT'),
    "nudge": ("Nudge: act, don't just describe", 'PLAY'),
    "entity": ("Scene context", 'OUTLINER_OB_MESH'),
    "scene_change": ("You changed the scene mid-turn", 'VIEW_PAN'),
    "spiral": ("Repeated-error guidance", 'ERROR'),
    "malformed": ("Re-emit a broken tool call", 'ERROR'),
    "wrapup": ("Step budget reached -- progress report", 'TIME'),
}


def _group_turns(history: list) -> list[list[dict]]:
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
        if role == "user" and not _is_system_note_msg(msg):
            if current_turn:
                turns.append(current_turn)
            current_turn = [msg]
        elif role in ("assistant", "tool", "reasoning", "user", "compaction"):
            current_turn.append(msg)
    if current_turn:
        turns.append(current_turn)
    return turns


def _split_turn(turn: list[dict]) -> tuple[dict | None, list[dict], dict | None]:
    """Split a turn into (user message, process messages, conclusion message)."""
    user_msg: dict | None = None
    process_msgs: list[dict] = []
    conclusion_msg: dict | None = None
    for msg in turn:
        role = msg.get("role", "")
        if role == "user" and not _is_system_note_msg(msg):
            user_msg = msg
        elif role in ("reasoning", "tool", "user", "compaction") or _is_system_note_msg(msg):
            process_msgs.append(msg)
        elif role == "assistant":
            if not msg.get("tool_calls"):
                conclusion_msg = msg
    return user_msg, process_msgs, conclusion_msg


def _hist_index(history: list, msg: dict) -> int:
    """Return *msg*'s index in *history*, or -1 when it is no longer present.

    The conversation history is mutated on the turn's worker thread
    (compaction replaces it, spiral recovery truncates it, auto-continue pops
    from it) while this panel draws on the main thread.  A message captured by
    ``_group_turns`` can therefore be gone by the time its index is needed --
    and ``list.index`` raises ``ValueError``, which aborts the whole Panel
    draw and blanked the chat history for the rest of a long turn.  The copy
    operators already treat a negative index as "no message" (see
    ``BFACW_OT_copy_message``), so returning -1 is the correct, safe result.

    Matching is by IDENTITY first (handles equal-valued messages), falling
    back to equality.
    """
    for i, m in enumerate(history):
        if m is msg:
            return i
    try:
        return history.index(msg)
    except ValueError:
        return -1


def _wrap_text(text: str, width: int = _WRAP_WIDTH) -> str:
    """Wrap text to a given width for display in Blender labels."""
    if not text:
        return ""
    return "\n".join(
        textwrap.fill(line, width=width)
        for line in text.split("\n")
    )


#
# Blender UI primitives can't render bold or italic, don't have a monospace
# label, and have no table widget -- but we can simulate most of it with
# row/column layouts, scale_y tricks for headings, boxes for code/quotes,
# and splitting pipe tables into aligned columns.
# ---------------------------------------------------------------------------

_INLINE_BOLD_RE  = re.compile(r"\*\*([^*]+?)\*\*")
_INLINE_BOLD2_RE = re.compile(r"__([^_]+?)__")
_INLINE_ITAL_RE  = re.compile(r"(?<!\*)\*([^*\s][^*]*?)\*(?!\*)")
_INLINE_ITAL2_RE = re.compile(r"(?<!_)_([^_\s][^_]*?)_(?!_)")
_INLINE_CODE_RE  = re.compile(r"`([^`]+?)`")
_INLINE_LINK_RE  = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_HEADING_RE      = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_LIST_RE         = re.compile(r"^(\s*)([-*+]|\d+\.)\s+(.+)$")
_HR_RE           = re.compile(r"^\s*(\*{3,}|-{3,}|_{3,})\s*$")
_QUOTE_RE        = re.compile(r"^\s*>\s?(.*)$")
_TABLE_SEP_RE    = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$")


def _strip_inline(text):
    """Flatten inline markdown that labels can't style."""
    text = _INLINE_BOLD_RE.sub(r"\1", text)
    text = _INLINE_BOLD2_RE.sub(r"\1", text)
    text = _INLINE_ITAL_RE.sub(r"\1", text)
    text = _INLINE_ITAL2_RE.sub(r"\1", text)
    text = _INLINE_CODE_RE.sub(r"\1", text)
    text = _INLINE_LINK_RE.sub(r"\1", text)
    return text


_PARA_SCALE_Y = 0.78  # tight vertical spacing for paragraph text

_CAN_MULTILINE = None

def _can_multiline() -> bool:
    """Whether the host build exposes ``UILayout.label_multiline``.

    The native multi-line label API (Blender PR #154351, merged into
    the workshop/ios-workshop builds) wraps text to the *actual* layout
    width with a tight 0.75 UI_UNIT_Y line height and supports an icon,
    alignment and max-lines cap.  Older stock builds lack it, in which
    case the addon keeps the manual character-chop renderer.  Checked
    once via RNA so ``hasattr`` on a live layout is not required.
    """
    global _CAN_MULTILINE
    if _CAN_MULTILINE is None:
        try:
            import bpy
            _CAN_MULTILINE = any(
                fn.identifier == "label_multiline"
                for fn in bpy.types.UILayout.bl_rna.functions
            )
        except Exception:  # pylint: disable=broad-exception-caught
            _CAN_MULTILINE = False
    return _CAN_MULTILINE

_CODE_SCALE_Y = 0.72
def _wrap_for_label(text, width=40):
    """Wrap text for UILabel -- returns a list of lines."""
    if not text:
        return [""]
    return textwrap.wrap(text, width=width) or [""]

  # tighter again for monospace-style code blocks


# --- LaTeX -> plain text -----------------------------------------------------
# The model occasionally emits $$...$$ blocks or \frac{a}{b}-style markup even
# though the system prompt says not to. Blender's UI labels can't render math,
# so we preprocess to readable ASCII / Unicode equivalents -- matches how a
# human would write the same equation in a chat message.

_LATEX_BLOCK_RE  = re.compile(r"\$\$\s*(.+?)\s*\$\$", re.DOTALL)
_LATEX_INLINE_RE = re.compile(r"(?<![\$\w])\$([^\$\n]+?)\$(?!\w)")
_LATEX_FRAC_RE   = re.compile(r"\\frac\s*\{([^{}]+)\}\s*\{([^{}]+)\}")
_LATEX_SQRT_RE   = re.compile(r"\\sqrt\s*\{([^{}]+)\}")
_LATEX_CMD_TAIL_RE = re.compile(r"\\([a-zA-Z]+)")

# Single-arg styling / wrapper commands. Strip them to just their content so
# nested cases like \frac{\mathbf{v}}{\|\mathbf{v}\|} can be parsed by the
# (deliberately non-recursive) frac/sqrt regexes after a few passes.
_LATEX_STRIP_CMD_RE = re.compile(
    r"\\(?:mathbf|mathrm|mathit|mathsf|mathtt|mathbb|mathfrak|mathcal|"
    r"text|textbf|textit|textrm|textsf|texttt|"
    r"boldsymbol|bm|vec|hat|tilde|bar|dot|ddot|underline|overline|"
    r"operatorname)\s*\{([^{}]*)\}"
)

_LATEX_SYMBOLS = {
    r"\cdot":   "\u00b7",
    r"\times":  "\u00d7",
    r"\div":    "\u00f7",
    r"\pm":     "\u00b1",
    r"\mp":     "\u2213",
    r"\approx": "\u2248",
    r"\neq":    "\u2260",
    r"\leq":    "\u2264",
    r"\geq":    "\u2265",
    r"\to":     "\u2192",
    r"\infty":  "\u221e",
    r"\sum":    "\u03a3",
    r"\prod":   "\u03a0",
    r"\int":    "\u222b",
    r"\partial": "\u2202",
    r"\nabla":  "\u2207",
    r"\langle": "\u27e8", r"\rangle": "\u27e9",
    r"\lceil":  "\u2308", r"\rceil":  "\u2309",
    r"\lfloor": "\u230a", r"\rfloor": "\u230b",
    r"\|":      "\u2016",  # double-bar (norm)
    r"\alpha":  "\u03b1", r"\beta":  "\u03b2", r"\gamma":   "\u03b3", r"\delta":  "\u03b4",
    r"\epsilon":"\u03b5", r"\zeta":  "\u03b6", r"\eta":     "\u03b7", r"\theta":  "\u03b8",
    r"\iota":   "\u03b9", r"\kappa": "\u03ba", r"\lambda":  "\u03bb", r"\mu":     "\u03bc",
    r"\nu":     "\u03bd", r"\xi":    "\u03be", r"\pi":      "\u03c0", r"\rho":    "\u03c1",
    r"\sigma":  "\u03c3", r"\tau":   "\u03c4", r"\phi":     "\u03c6", r"\chi":    "\u03c7",
    r"\psi":    "\u03c8", r"\omega": "\u03c9",
    r"\Gamma":  "\u0393", r"\Delta": "\u0394", r"\Theta":   "\u0398", r"\Lambda": "\u039b",
    r"\Xi":     "\u039e", r"\Pi":    "\u03a0", r"\Sigma":   "\u03a3", r"\Phi":    "\u03a6",
    r"\Psi":    "\u03a8", r"\Omega": "\u03a9",
    r"\left":   "",  r"\right": "",
    r"\,":      " ", r"\;":     " ", r"\!":       "",
    r"\\":      "\n",  # LaTeX line break inside an equation
}


def _convert_latex_expr(expr):
    """Convert a single LaTeX expression body to plain text."""
    # 1) Strip styling/wrapper commands first -- this collapses
    #    \mathbf{v}, \text{normalized}, \vec{x} etc. to their inner content,
    #    so frac/sqrt's flat-brace regex can see through them. Iterate to
    #    handle nesting like \mathbf{\hat{n}}.
    for _ in range(6):
        new = _LATEX_STRIP_CMD_RE.sub(r"\1", expr)
        if new == expr:
            break
        expr = new
    # 2) frac/sqrt -- also iterate because substitutions can expose new matches.
    for _ in range(6):
        new = _LATEX_FRAC_RE.sub(r"(\1)/(\2)", expr)
        new = _LATEX_SQRT_RE.sub(r"sqrt(\1)", new)
        if new == expr:
            break
        expr = new
    # 3) Symbols.
    for k, v in _LATEX_SYMBOLS.items():
        expr = expr.replace(k, v)
    # 4) Strip remaining \command tokens -- keep the name as a fallback so
    #    users can still see what was meant (e.g., \mathbb -> mathbb).
    expr = _LATEX_CMD_TAIL_RE.sub(r"\1", expr)
    # 5) Collapse braces left over from stripped commands.
    expr = expr.replace("{", "").replace("}", "")
    return expr.strip()


_FENCE_PROTECT_RE       = re.compile(r"(```.*?```)", re.DOTALL)
_INLINE_CODE_PROTECT_RE = re.compile(r"(`[^`\n]+`)")

# x^2 -> x^2, 10^{-3} -> 10^-^3. Only digits + sign chars are converted, so
# `2^k`, `^L` (control chars in docs), and code paths like `path^foo`
# are left alone.
_SUPERSCRIPT_TR = str.maketrans(
    '0123456789-+',
    '\u2070\u00b9\u00b2\u00b3\u2074\u2075\u2076\u2077\u2078\u2079\u207b\u207a')
_SUPERSCRIPT_RE = re.compile(r'\^(\{[\d\-+]+\}|[\d\-+]+)')


def _superscript_powers(text):
    """Convert ^N and ^{NN} to Unicode superscripts."""
    def repl(m):
        s = m.group(1)
        if s.startswith('{') and s.endswith('}'):
            s = s[1:-1]
        return s.translate(_SUPERSCRIPT_TR)
    return _SUPERSCRIPT_RE.sub(repl, text)


def _convert_latex(text):
    """Replace $$...$$ blocks and inline $...$ with readable plain text.
    Code fences and inline `code` are passed through untouched so $-syntax
    in shell snippets / variables doesn't get mangled."""
    def _block(m):
        return "\n" + _convert_latex_expr(m.group(1)) + "\n"

    def _convert_segment(seg):
        seg = _LATEX_BLOCK_RE.sub(_block, seg)
        seg = _LATEX_INLINE_RE.sub(
            lambda m: _convert_latex_expr(m.group(1)), seg,
        )
        # Catches both LaTeX-converted output (^2 left over from \frac/\sqrt
        # bodies) and plain-typed `mc^2`-style powers in normal prose.
        seg = _superscript_powers(seg)
        return seg

    out = []
    # Split on fenced code blocks (odd-index parts are fence content, kept verbatim)
    for i, part in enumerate(_FENCE_PROTECT_RE.split(text)):
        if i % 2 == 1:
            out.append(part)
            continue
        # Within non-fence text, also protect inline `code`
        sub_out = []
        for j, sub in enumerate(_INLINE_CODE_PROTECT_RE.split(part)):
            sub_out.append(sub if j % 2 == 1 else _convert_segment(sub))
        out.append("".join(sub_out))
    return "".join(out)


def _close_trailing_fence(text):
    """If the markdown text contains an odd number of triple-backtick
    fences, it ends with an open code block -- typically because the
    model hit max_tokens mid-snippet. Append a closing fence plus a
    one-line truncation note so the renderer doesn't treat everything
    that follows as code."""
    if text.count("```") % 2 == 0:
        return text
    sep = "" if text.endswith("\n") else "\n"
    return text + sep + "```\n_(response was cut off)_\n"



class BFACW_OT_copy_code_block(Operator):
    """Copy a code block from markdown to the clipboard."""
    bl_idname = "bfacw.copy_code_block"
    bl_label = "Copy Code"
    code_text: bpy.props.StringProperty(name="Code", default="")
    def execute(self, context):
        context.window_manager.clipboard = self.code_text
        self.report({"INFO"}, "Code copied to clipboard")
        return {"FINISHED"}

def _render_markdown(layout, md, width=40):
    """Walk a markdown string and render into the given layout.

    Paragraph text is emitted into a rolling compact column (align=True +
    reduced scale_y) so consecutive wrapped lines don't look double-spaced.
    Special blocks (code, table, heading, rule, quote) break that column
    and render in their own layouts so they aren't squished.
    """
    md = _convert_latex(md)
    # Auto-close an unterminated fenced block -- common when the model
    # hits max_tokens mid-code and the last ``` got truncated. Without
    # this, the markdown renderer treats everything after the opening
    # fence as one giant code block and the conversation layout breaks.
    md = _close_trailing_fence(md)
    lines = md.splitlines()
    n = len(lines)

    # Rolling paragraph column -- lazily created so each block break gets
    # its own column (which keeps tight spacing within but separates
    # visually from whatever comes next).
    para_col = [None]

    def _break_para():
        para_col[0] = None

    def _para_col():
        if para_col[0] is None:
            col = layout.column(align=True)
            col.scale_y = _PARA_SCALE_Y
            para_col[0] = col
        return para_col[0]

    def _emit_para(text, indent=""):
        if not text.strip():
            return
        col = _para_col()
        full = indent + text
        if _can_multiline():
            # Native multiline already has tight 0.75 UI_UNIT_Y leading;
            # keep the column at full scale so it does not over-squish.
            col.scale_y = 1.0
            col.label_multiline(text=full)
        else:
            for chunk in _wrap_for_label(full, width=width):
                col.label(text=chunk if chunk else " ")

    i = 0
    while i < n:
        raw = lines[i]
        stripped = raw.strip()

        # ```fenced code block
        if stripped.startswith("```"):
            _break_para()
            lang = stripped[3:].strip() or "code"

            # Harvest the code body first so the copy button can receive it.
            i += 1
            code_body_lines = []
            while i < n and not lines[i].lstrip().startswith("```"):
                code_body_lines.append(lines[i])
                i += 1
            if i < n and lines[i].lstrip().startswith("```"):
                i += 1  # skip closing fence
            code_body = "\n".join(code_body_lines)

            box = layout.box()
            # Header row: language label on the left, [Copy][Run] on the
            # right. Run is Python-only -- executing a shell/JSON/etc fence
            # doesn't make sense and would just error.
            hrow = box.row(align=False)
            left = hrow.row()
            left.label(text=lang, icon='SCRIPT')
            right = hrow.row(align=True)
            right.alignment = 'RIGHT'
            cop = right.operator(
                "bfacw.copy_code_block", text="", icon='COPYDOWN',
            )
            cop.code_text = code_body


            col = box.column(align=True)
            col.scale_y = _CODE_SCALE_Y
            for code_line in code_body_lines:
                for chunk in _wrap_for_label(code_line, width=max(10, width - 2)):
                    col.label(text=chunk if chunk else " ")
            _break_para()
            continue

        # Pipe table (header + separator)
        if "|" in raw and i + 1 < n and _TABLE_SEP_RE.match(lines[i + 1]):
            _break_para()
            header_cells = [c.strip() for c in raw.strip().strip("|").split("|")]
            table_start = i
            i += 2
            body_rows = []
            while i < n and "|" in lines[i] and lines[i].strip():
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                body_rows.append(cells)
                i += 1

            try:
                tbox = layout.box()
                hrow = tbox.row(align=True)
                for cell in header_cells:
                    c = hrow.column(align=True)
                    c.label(text=_strip_inline(cell), icon='DOT')
                for row in body_rows:
                    while len(row) < len(header_cells):
                        row.append("")
                    brow = tbox.row(align=True)
                    for cell in row[: len(header_cells)]:
                        c = brow.column(align=True)
                        c.scale_y = _PARA_SCALE_Y
                        wrapped = _wrap_for_label(
                            _strip_inline(cell),
                            width=max(8, width // max(1, len(header_cells))),
                        )
                        for t in wrapped[:3]:
                            c.label(text=t if t else " ")
            except Exception as e:
                # Table data was malformed enough to break the renderer
                # (ragged rows, empty header, mismatched separator row).
                # Fall back to rendering the raw table block as plain
                # text so the content isn't lost.
                print(f"[Blender Buddy] table render fallback: {e}")
                fallback = layout.column(align=True)
                fallback.scale_y = _PARA_SCALE_Y
                for ln in lines[table_start:i]:
                    for chunk in _wrap_for_label(ln, width=width):
                        fallback.label(text=chunk if chunk else " ")
            _break_para()
            continue

        # Heading
        m = _HEADING_RE.match(stripped)
        if m:
            _break_para()
            level = len(m.group(1))
            text = _strip_inline(m.group(2))
            # Keyframe dots: size scales with heading level.
            # EXTREME=big, MOVING_HOLD=mid, JITTER/BREAKDOWN/GENERATED=small
            icon = {1: 'KEYTYPE_EXTREME_VEC',
                    2: 'KEYTYPE_MOVING_HOLD_VEC',
                    3: 'KEYTYPE_JITTER_VEC',
                    4: 'KEYTYPE_BREAKDOWN_VEC'}.get(level, 'KEYTYPE_GENERATED_VEC')
            if level == 1:
                # H1: separator + large scaled row + uppercase
                layout.separator()
                hrow = layout.row()
                hrow.scale_y = 1.6
                hrow.label(text=text.upper(), icon=icon)
            elif level == 2:
                # H2: slightly larger row, strong icon
                layout.separator(factor=0.5)
                hrow = layout.row()
                hrow.scale_y = 1.35
                hrow.label(text=text, icon=icon)
            elif level == 3:
                # H3: modest boost, distinct icon
                hrow = layout.row()
                hrow.scale_y = 1.2
                hrow.label(text=text, icon=icon)
            else:
                # H4-H6: subtle differentiation
                hrow = layout.row()
                hrow.scale_y = 1.1
                hrow.label(text=text, icon=icon)
            i += 1
            _break_para()
            continue

        # Horizontal rule
        if _HR_RE.match(raw):
            _break_para()
            layout.separator()
            i += 1
            continue

        # Blockquote
        mq = _QUOTE_RE.match(raw)
        if mq:
            _break_para()
            qbox = layout.box()
            qcol = qbox.column(align=True)
            qcol.scale_y = _PARA_SCALE_Y
            qtext = _strip_inline(mq.group(1) or "")
            for chunk in _wrap_for_label("| " + qtext, width=width):
                qcol.label(text=chunk)
            i += 1
            _break_para()
            continue

        # List item -- rendered into the rolling para column so successive
        # items share spacing.
        ml = _LIST_RE.match(raw)
        if ml:
            indent = len(ml.group(1))
            bullet = ml.group(2)
            text = _strip_inline(ml.group(3))
            prefix = "  " * (indent // 2)
            marker = "* " if bullet in ("-", "*", "+") else f"{bullet} "
            _emit_para(text, indent=prefix + marker)
            i += 1
            continue

        # Blank line -- break paragraph, no separator (keeps spacing tight).
        if not stripped:
            _break_para()
            i += 1
            continue

        # Regular paragraph line
        _emit_para(_strip_inline(stripped))
        i += 1

def _draw_multiline(
    layout: bpy.types.UILayout,
    text: str,
    width: int = _WRAP_WIDTH,
    icon: str = 'NONE',
) -> None:
    """Draw multi-line text in a layout, optionally with a leading icon.

    Prefers the host build's native ``label_multiline`` (Blender PR
    #154351, workshop builds) which wraps to the real layout width with
    a tight 0.75 UI_UNIT_Y line height - no manual chopping, no tall
    full-height rows per wrap chunk, so chat messages condense
    vertically. Falls back to character-based wrapping on builds that
    lack the API.  When *icon* is given it is placed on the first line
    (the fallback cannot attach an icon to a whole wrapped block).
    """
    if not text:
        return
    if _can_multiline():
        try:
            if icon and icon != 'NONE':
                layout.label_multiline(text=text, icon=icon)
            else:
                layout.label_multiline(text=text)
            return
        except Exception:  # pylint: disable=broad-exception-caught
            pass
    lines = _wrap_text(text, width=width).split("\n")
    for i, line in enumerate(lines):
        layout.label(text=line, icon=icon if i == 0 else 'NONE')


def _draw_reasoning(
    layout: bpy.types.UILayout,
    text: str,
    label: str = "Thinking",
    is_thinking: bool = False,
    thinking_dots: int = 0,
    message_index: int = -1,
    archived: bool = False,
) -> None:
    """Draw reasoning (chain-of-thought) content in a collapsible panel.

    The entire reasoning section is collapsible.  While *is_thinking* is
    True the label animates with dots.  The *label* is stored when the
    reasoning was first captured so it doesn't flicker on every redraw.
    """
    if not text:
        return

    reasoning_lines = text.strip().split("\n")

    # Animate the label with Unicode spinner while thinking.
    if is_thinking:
        display_label = "{:s} {:s}".format(label, _spinner_glyph(thinking_dots))
        icon = _AGENT_ICON
    else:
        display_label = label
        icon = 'CHECKMARK'

    # Collapsible panel for the entire reasoning section.
    header, body = layout.panel("reasoning_{:d}".format(message_index), default_closed=True)
    header.label(text="{:s} ({:d} lines)".format(display_label, len(reasoning_lines)), icon=icon)
    if message_index >= 0:
        op = header.operator("bfacw.copy_message", text="", icon='COPYDOWN')
        op.message_index = message_index
        op.archived = archived

    if body:
        body.separator()
        for line in reasoning_lines:
            _draw_multiline(body, line)


def _draw_tool_summary(layout: bpy.types.UILayout, content: str, summary: str) -> None:
    """Draw a tool result with a human-readable summary.

    Shows the summary prominently.  If the full content differs from the
    summary (e.g., contains a traceback), show a collapsed detail section.
    """
    if not summary and not content:
        return

    display = summary if summary else content
    _draw_multiline(layout, display)

    # If there's a summary different from raw content, show the raw version
    # collapsed as a detail section.
    if summary and summary != content and len(content) > len(summary):
        detail_box = layout.box()
        detail_row = detail_box.row()
        detail_row.label(text="Details:", icon='TEXT')
        # Show the full raw content -- the user asked to see it all, and the
        # panel scrolls.
        _draw_multiline(detail_box, content, width=_WRAP_WIDTH)


def _draw_archive_node(
    layout: bpy.types.UILayout,
    retired_count: int,
    summary: str,
) -> None:
    """Draw a 'Checkpoint' node in the Workshop timeline.

    Marks where older messages were summarized out of the model's context
    (a checkpoint is saved first, so it can be restored from the Session
    panel).  The summary is the memory note written at that point.
    Display-only -- never sent to the model.
    """
    box = layout.box()
    row = box.row()
    row.label(
        text="Checkpoint \u2014 {:d} earlier message(s) summarized".format(retired_count),
        icon='BOOKMARKS',
    )
    body = str(summary or "").strip()
    if body:
        _draw_multiline(box, body)


def _draw_tool_inline(
    layout: bpy.types.UILayout,
    tool_name: str,
    display: str,
    is_error: bool,
    message_index: int = -1,
    archived: bool = False,
) -> None:
    """Draw a tool result as a sub-box inside the agent's message box."""
    tool_box = layout.box()
    row = tool_box.row()
    row.label(
        text="\u2699 {:s}".format(tool_name),
        icon='WARNING' if is_error else 'TOOL_SETTINGS',
    )
    if message_index >= 0:
        op = row.operator("bfacw.copy_message", text="", icon='COPYDOWN')
        op.message_index = message_index
        op.archived = archived
    _draw_multiline(tool_box, display)


# ---------------------------------------------------------------------------
# Properties

def _chat_history_dir() -> Path:
    """Return the directory where chat history JSON files are stored."""
    base = Path(bpy.utils.user_resource("SCRIPTS")) / "bfa_coworker_chat_history"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _chat_history_path() -> Path:
    """Return the path to the chat history JSON file for the current blend file."""
    session_name = "default"
    if bpy.data.filepath:
        session_name = Path(bpy.data.filepath).stem
    return _chat_history_dir() / "{:s}.json".format(session_name)


class ChatHistoryProperties(PropertyGroup):  # type: ignore[misc]
    """Persistent chat history properties stored on the WindowManager."""

    chat_input: StringProperty(  # type: ignore[valid-type]
        name="Input",
        description="Type your message for the Coworker (AI agent)",
        default="",
    )

    chat_status: StringProperty(  # type: ignore[valid-type]
        name="Status",
        default="Idle",
    )

    chat_mode: EnumProperty(  # type: ignore[valid-type]
        name="Mode",
        description="Coworker mode: the agent can execute tools. Ask mode: read-only Q&A",
        items=CHAT_MODE_ITEMS,
        default="AGENT",
    )

    chat_newest_first: BoolProperty(  # type: ignore[valid-type]
        name="Newest First",
        description="Show the most recent messages at the top of the chat history",
        default=True,
    )

    # -- Session memory & checkpoints (Tier 3) ----------------------
    session_show_checkpoints: BoolProperty(  # type: ignore[valid-type]
        name="Show Checkpoints",
        description="Expand the checkpoint list in the Session section",
        default=False,
    )

    session_checkpoint_index: IntProperty(  # type: ignore[valid-type]
        name="Checkpoint",
        description="Index of the selected checkpoint",
        default=0,
        min=0,
    )

    session_memory_edit: StringProperty(  # type: ignore[valid-type]
        name="Session Memory",
        description="Editable session memory block (empty = unchanged)",
        default="",
    )

    # -- Image socket (issue #88, Tier 3k) --------------------------
    # One Blender-standard socket that every attach source feeds:
    # file attach, render/screen capture, drag-and-drop, and the
    # template_ID datablock browser below.  Encoded to a data URI on
    # the MAIN thread at send time (see chat_attachments).
    chat_image: PointerProperty(  # type: ignore[valid-type]
        name="Image",
        description="Image attached to the chat (drag-and-drop, attach, capture, or pick a datablock)",
        type=bpy.types.Image,
    )

    chat_image_send_once: BoolProperty(  # type: ignore[valid-type]
        name="Send Once",
        description=(
            "Send the attached image with the next message only, then "
            "detach it (off = sticky: re-sent every turn until cleared)"
        ),
        default=False,
    )

    chat_image_expanded: BoolProperty(  # type: ignore[valid-type]
        name="Show Image Panel",
        description=(
            "Expand the image socket, thumbnail and capture buttons; "
            "collapse them to give the chat the panel back"
        ),
        default=True,
    )


def _load_chat_history() -> list[dict]:
    """Load conversation history from disk."""
    path = _chat_history_path()
    if path.exists():
        try:
            with open(str(path), "r", encoding="utf-8") as fh:
                history = json.load(fh)
            # Detect old sessions (no turn_start flags anywhere) for backward compat.
            # New sessions keep their turn_start flags so only real user messages
            # create turns -- agent-injected messages won't inflate turn count.
            return history
        except (json.JSONDecodeError, OSError):
            pass
    return []


# Thread lock for history serialization -- prevents concurrent threads
# from writing partial dumps when a turn finishes while another is active.
_history_save_lock = threading.Lock()


def _session_memory_archive_path() -> Path:
    """Return the path of the session-memory archive (next to chat history)."""
    return _chat_history_dir() / "archive.jsonl"


def _session_memory_state_path() -> Path:
    """Return the path of the session-memory sidecar JSON (checkpoints etc.)."""
    return _chat_history_dir() / "default_session_memory.json"


def _save_session_memory_state() -> None:
    """Persist the session-memory store (memory note + checkpoints)."""
    from . import session_memory as _sm
    st = _sm.store
    st.archive_path = _session_memory_archive_path()
    try:
        with _sm.store_lock:
            payload = st.to_payload()
            payload["archive_path"] = str(st.archive_path)
        with open(str(_session_memory_state_path()), "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
    except OSError:
        pass


def _load_session_memory_state() -> None:
    """Restore the session-memory store from its sidecar JSON, if present."""
    from . import session_memory as _sm
    st = _sm.store
    st.archive_path = _session_memory_archive_path()
    path = _session_memory_state_path()
    if not path.exists():
        return
    try:
        with open(str(path), "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        with _sm.store_lock:
            st.load_payload(payload)
    except (json.JSONDecodeError, OSError):
        pass


def _save_chat_history() -> None:
    """Save conversation history to disk (thread-safe) with versioned copies."""
    import time as _time
    base_dir = _chat_history_path().parent
    with _history_save_lock:
        try:
            # Save timestamped copy.
            ts = _time.strftime("%Y-%m-%d_%H-%M-%S", _time.localtime())
            versioned_path = base_dir / "default_{:s}.json".format(ts)
            with open(str(versioned_path), "w", encoding="utf-8") as fh:
                json.dump(agent_controller._agent_state.conversation_history, fh, indent=2)
            # Also save to default.json (latest).
            with open(str(_chat_history_path()), "w", encoding="utf-8") as fh:
                json.dump(agent_controller._agent_state.conversation_history, fh, indent=2)
            # Prune old versions: keep last 10.
            _prune_old_sessions(base_dir)
            # Persist the session-memory store alongside (Tier 3).
            _save_session_memory_state()
        except OSError:
            pass


def _prune_old_sessions(base_dir) -> None:
    """Keep at most 10 versioned session files, remove oldest."""
    import re as _re
    pattern = _re.compile(r"^default_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}\.json$")
    files = []
    for f in base_dir.iterdir():
        if f.is_file() and pattern.match(f.name):
            files.append(f)
    files.sort(key=lambda x: x.stat().st_mtime, reverse=True)
    for old_file in files[10:]:
        try:
            old_file.unlink()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Operators


def _capture_chat_attachment(props) -> tuple[list[str] | None, list[str] | None]:
    """Encode the image socket for a send -- MAIN THREAD ONLY.

    Returns ``(attachments, attachment_names)`` ready for
    ``run_conversation_turn`` / ``enqueue_message``, or ``(None, None)``
    for a plain-text send (empty socket, or an image that cannot be
    encoded).  Called by the send operators BEFORE any worker thread
    starts: encoding reads bpy image pixels, which the turn worker must
    never touch.  A successful capture consumes the socket when
    "Send Once" is ticked; the default sticky socket keeps the image
    for every following send.
    """
    img = props.chat_image
    if img is None:
        return None, None
    uri = chat_attachments.image_to_data_uri(img)
    if not uri:
        # Oversized or broken: keep the socket (so the user can fix it)
        # and send this message as plain text.
        return None, None
    name = chat_attachments.attachment_name(img)
    if props.chat_image_send_once:
        props.chat_image = None
    return [uri], [name]


def _warn_failed_attachment(op, props) -> None:
    """Warn when the socket holds an image that could not be encoded.

    ``_capture_chat_attachment`` returns no attachments for both an
    empty socket and a failed encode, and deliberately keeps the socket
    on failure so the user can retry -- so a set socket plus an empty
    result means the encode failed and the send is silently plain text.
    Say so instead.
    """
    if props.chat_image is None:
        return
    op.report(
        {"WARNING"},
        "Could not prepare the attached image; sending without it. "
        "Free space in the temp folder, or save/pack the image, then retry.",
    )


def _attach_to_socket(context, op, img) -> None:
    """Store *img* in the chat socket and confirm on *op* (main thread)."""
    props = context.window_manager.bfacw_chat_props  # type: ignore[attr-defined]
    props.chat_image = img
    # Warm the preview now so the panel can draw the thumbnail on its
    # first redraw instead of one frame later (the pixels are filled by
    # Blender's preview thread after ``preview_ensure()``).
    _ensure_attachment_preview(img)
    op.report({"INFO"},
              f"Attached {chat_attachments.attachment_name(img)}")


class BFACW_OT_chat_send(Operator):  # type: ignore[misc]
    """Send the current input to the Coworker agent (or queue if busy)."""
    bl_idname = "bfacw.chat_send"
    bl_label = "Send"
    bl_description = "Send your message to the Coworker agent"

    def execute(self, context: bpy.types.Context) -> set[str]:
        wm = context.window_manager
        props = wm.bfacw_chat_props  # type: ignore[attr-defined]
        message = props.chat_input.strip()
        if not message:
            return {"CANCELLED"}

        if not agent_controller._agent_state.mcp_server_running:
            self.report({"WARNING"}, "Coworker is not running. Start it from Preferences or the Chat panel.")
            return {"CANCELLED"}

        # Sync preferences to config, then read LLM config.
        prefs = context.preferences.addons[__package__].preferences
        _sync_prefs_to_config(prefs)
        llm_cfg = llm_manager.get_config()
        llm_url = None
        api_key = None
        model = None
        if llm_cfg.mode == "remote":
            llm_url = llm_cfg.remote_api_url
            api_key = llm_cfg.remote_api_key
            model = llm_cfg.remote_model or None

        # Get effective ports from preferences.
        _bridge_port, _mcp_port, _llm_port = effective_ports(prefs)
        actual_mcp = agent_controller._agent_state.mcp_port_actual
        send_mcp_port = actual_mcp if actual_mcp else _mcp_port

        # Encode the image socket HERE, on the main thread, before any
        # worker thread starts (Tier 3k): the turn worker never touches
        # bpy, and both the queued and direct paths below need the payload.
        attachments, attachment_names = _capture_chat_attachment(props)
        if attachments is None:
            _warn_failed_attachment(self, props)

        # If a turn is already active, queue the message -- UNLESS the user has
        # pressed Stop.  After a Stop the old worker may still be unwinding
        # (a blocking read or tool call cannot be interrupted instantly); if we
        # queued here, the message would sit behind a worker that may not drain
        # the queue for a while ("frozen").  Instead we start a new turn, which
        # clears the stale guard (see run_conversation_turn) and supersedes the
        # old worker via the turn token.
        if (agent_controller._agent_state.turn_active
                and not agent_controller._stop_event.is_set()):
            pos = agent_controller.enqueue_message(
                message=message,
                chat_mode=props.chat_mode,
                llm_url=llm_url or None,
                api_key=api_key or None,
                model=model,
                mcp_port=send_mcp_port,
                attachments=attachments,
                attachment_names=attachment_names,
            )
            props.chat_input = ""
            self.report({"INFO"}, "Message queued (position {:d})".format(pos))
            _redraw_areas(context)
            return {"FINISHED"}

        # Clear input and start processing.  Show the "reading" phase at once,
        # before the worker thread even starts, so the send never looks frozen.
        props.chat_input = ""
        agent_controller._agent_state.turn_phase = "reading"
        props.chat_status = "Reading your message..."
        agent_controller._agent_state.ui_status = "Reading your message..."
        # Refresh bpy-derived values (system prompt, prefs) HERE, on the main
        # thread, so the turn worker never reads bpy itself -- an off-main
        # bpy read races Blender's context counter and spams the console.
        agent_controller.prefetch_main_thread_context()

        def _do_turn():
            try:
                agent_controller.run_conversation_turn(
                    user_message=message,
                    on_text=None,
                    on_reasoning=lambda r: _update_streaming(r),
                    on_status=lambda s: _update_status(s),
                    llm_url=llm_url or None,
                    api_key=api_key or None,
                    model=model,
                    mcp_port=send_mcp_port,
                    chat_mode=props.chat_mode,
                    on_stream_text=lambda t: _update_streaming(t),
                    on_stream_reasoning=lambda r: _update_streaming(r),
                    attachments=attachments,
                    attachment_names=attachment_names,
                )
            except Exception as ex:  # pylint: disable=broad-exception-caught
                agent_controller._agent_state.error = str(ex)
            finally:
                _save_chat_history()
                # Auto-dequeue next message if queue is not empty.
                _try_dequeue_next()
                _update_status("Idle")
                _redraw_areas_safe()

        def _try_dequeue_next():
            """Try to process the next queued message."""
            next_msg = agent_controller.dequeue_message()
            if next_msg:
                # Runs on the turn worker thread: record the status as plain
                # Python state; the main-thread timer mirrors it to the UI.
                agent_controller._agent_state.ui_status = "Processing queued message..."
                _redraw_areas_safe()
                # Start processing the next message in a new thread.
                import threading as _threading
                _threading.Thread(target=_do_turn_from_queue, args=(next_msg,), daemon=True).start()

        def _do_turn_from_queue(item: dict):
            """Process a message from the queue."""
            try:
                agent_controller.run_conversation_turn(
                    user_message=item["message"],
                    on_text=None,
                    on_reasoning=lambda r: _update_streaming(r),
                    on_status=lambda s: _update_status(s),
                    llm_url=item.get("llm_url"),
                    api_key=item.get("api_key"),
                    model=item.get("model"),
                    mcp_port=item.get("mcp_port", 0),
                    chat_mode=item.get("chat_mode", "AGENT"),
                    on_stream_text=lambda t: _update_streaming(t),
                    on_stream_reasoning=lambda r: _update_streaming(r),
                    attachments=item.get("attachments"),
                    attachment_names=item.get("attachment_names"),
                )
            except Exception as ex:  # pylint: disable=broad-exception-caught
                agent_controller._agent_state.error = str(ex)
            finally:
                _save_chat_history()
                _try_dequeue_next()
                _update_status("Idle")
                _redraw_areas_safe()

        def _update_status(text: str) -> None:
            # Called from the turn WORKER thread.  Do NOT write the bpy chat
            # property here: an off-main RNA write races Blender's global
            # Python context counter and spams "Python context internal state
            # bug".  Record plain Python state; the main-thread timer mirrors
            # it into props.chat_status.
            agent_controller._agent_state.ui_status = text
            _redraw_areas_safe()

        def _update_streaming(text: str) -> None:
            """Called when reasoning or streaming text arrives -- refresh UI."""
            _redraw_areas_safe()

        import threading
        thread = threading.Thread(target=_do_turn, daemon=True)
        thread.start()

        return {"FINISHED"}


class BFACW_OT_chat_clear(Operator):  # type: ignore[misc]
    """Clear the conversation history and start a fresh thread."""
    bl_idname = "bfacw.chat_clear"
    bl_label = "New Thread"
    bl_description = "Clear conversation history and start a fresh thread (system prompt stays)"

    def execute(self, context: bpy.types.Context) -> set[str]:
        from . import session_memory as _sm
        agent_controller._agent_state.conversation_history.clear()
        agent_controller._agent_state.streaming_text = ""
        agent_controller._agent_state.reasoning_text = ""
        agent_controller._agent_state.thinking_dots = 0
        # Reset token accounting so the context bar returns to
        # "No usage recorded yet" for the new thread.
        agent_controller._agent_state.reset_usage()
        # Reset session memory + checkpoints so the new thread does not inherit
        # the previous thread's memory block or turn counter.
        with _sm.store_lock:
            _sm.store.reset()
        agent_controller._session_turn_count = 0
        agent_controller._reset_session_domains()
        # Fresh thread -> forget which datablocks the coworker created so a
        # later turn does not re-lock the old thread's objects.
        agent_controller.clear_session_scene_lock()
        # Clear Coworker_* text datablocks from the text editor.
        agent_controller._clear_coworker_text_blocks()
        # Clear cached system prompt so project rules are reloaded on next turn.
        agent_controller._clear_system_prompt_cache()
        _save_chat_history()
        _redraw_areas(context)
        return {"FINISHED"}


class BFACW_OT_queue_clear(Operator):  # type: ignore[misc]
    """Clear all queued messages."""
    bl_idname = "bfacw.queue_clear"
    bl_label = "Clear Queue"
    bl_description = "Remove all queued messages from the message queue"

    def execute(self, context: bpy.types.Context) -> set[str]:
        agent_controller._message_queue.clear()
        self.report({"INFO"}, "Message queue cleared")
        _redraw_areas(context)
        return {"FINISHED"}


class BFACW_OT_queue_show(Operator):  # type: ignore[misc]
    """Show queued messages in a popup."""
    bl_idname = "bfacw.queue_show"
    bl_label = "Show Queue"
    bl_description = "Display all queued messages in a popup menu"

    def execute(self, context: bpy.types.Context) -> set[str]:
        queue_items = agent_controller._message_queue.get_all()
        if not queue_items:
            self.report({"INFO"}, "Queue is empty")
            return {"CANCELLED"}

        def _draw_menu(menu, _context):
            layout = menu.layout
            layout.label(
                text="Queued Messages ({:d})".format(len(queue_items)),
                icon='FORWARD',
            )
            for idx, item in enumerate(queue_items):
                msg = item.get("message", "")
                mode = item.get("chat_mode", "AGENT")
                preview = msg[:60] + ("..." if len(msg) > 60 else "")
                row = layout.row()
                row.label(text="[{:d}] [{:s}] {:s}".format(idx + 1, mode, preview))

        context.window_manager.popup_menu(
            _draw_menu,
            title="Message Queue",
            icon='FORWARD',
        )
        return {"FINISHED"}


class BFACW_OT_export_session_log(Operator):  # type: ignore[misc]
    """Export the current session to a Blender text datablock."""
    bl_idname = "bfacw.export_session_log"
    bl_label = "Export Session Log"
    bl_description = "Export full session history, system prompt, and version info to a text block"

    def execute(self, context: bpy.types.Context) -> set[str]:
        agent_controller.export_session_log()
        self.report({"INFO"}, "Session log exported to text block")
        _redraw_areas(context)
        return {"FINISHED"}


class BFACW_OT_copy_session_log(Operator):  # type: ignore[misc]
    """Copy the session log to the clipboard."""
    bl_idname = "bfacw.copy_session_log"
    bl_label = "Copy Session Log"
    bl_description = "Copy full session history to clipboard"

    def execute(self, context: bpy.types.Context) -> set[str]:
        log_text = agent_controller.export_session_log_to_clipboard()
        context.window_manager.clipboard = log_text
        self.report({"INFO"}, "Session log copied to clipboard")
        return {"FINISHED"}


class BFACW_OT_copy_status_error(Operator):  # type: ignore[misc]
    """Copy the full error/status text to the clipboard for troubleshooting."""
    bl_idname = "bfacw.copy_status_error"
    bl_label = "Copy Error"
    bl_description = "Copy the full error text to the clipboard for troubleshooting"

    def execute(self, context: bpy.types.Context) -> set[str]:
        state = agent_controller._agent_state
        text = state.error_full or state.error or ""
        if not text.strip():
            self.report({"WARNING"}, "No error text to copy")
            return {"CANCELLED"}
        context.window_manager.clipboard = text
        self.report({"INFO"}, "Error text copied to clipboard")
        return {"FINISHED"}


class BFACW_OT_chat_stop(Operator):  # type: ignore[misc]
    """Stop the current generation."""
    bl_idname = "bfacw.chat_stop"
    bl_label = "Stop"
    bl_description = "Stop the current generation"

    def execute(self, context: bpy.types.Context) -> set[str]:
        agent_controller.request_stop()
        agent_controller._agent_state.status_text = "Stopped"
        wm = context.window_manager
        props = wm.bfacw_chat_props  # type: ignore[attr-defined]
        props.chat_status = "Stopped"
        _redraw_areas(context)
        return {"FINISHED"}


class BFACW_OT_chat_queue_send(Operator):  # type: ignore[misc]
    """Queue the current input message for later processing."""
    bl_idname = "bfacw.chat_queue_send"
    bl_label = "Queue Message"
    bl_description = "Add the current message to the queue for processing after the current turn"

    def execute(self, context: bpy.types.Context) -> set[str]:
        wm = context.window_manager
        props = wm.bfacw_chat_props  # type: ignore[attr-defined]
        message = props.chat_input.strip()
        if not message:
            self.report({"WARNING"}, "Nothing to queue")
            return {"CANCELLED"}

        prefs = context.preferences.addons[__package__].preferences
        _sync_prefs_to_config(prefs)
        llm_cfg = llm_manager.get_config()
        llm_url = None
        api_key = None
        model = None
        if llm_cfg.mode == "remote":
            llm_url = llm_cfg.remote_api_url
            api_key = llm_cfg.remote_api_key
            model = llm_cfg.remote_model or None

        _bridge_port, _mcp_port, _llm_port = effective_ports(prefs)
        actual_mcp = agent_controller._agent_state.mcp_port_actual
        send_mcp_port = actual_mcp if actual_mcp else _mcp_port

        # Main-thread capture of the image socket (Tier 3k), same as the
        # direct send path -- the encoded payload travels with the queue item.
        attachments, attachment_names = _capture_chat_attachment(props)
        if attachments is None:
            _warn_failed_attachment(self, props)

        pos = agent_controller.enqueue_message(
            message=message,
            chat_mode=props.chat_mode,
            llm_url=llm_url or None,
            api_key=api_key or None,
            model=model,
            mcp_port=send_mcp_port,
            attachments=attachments,
            attachment_names=attachment_names,
        )
        props.chat_input = ""
        self.report({"INFO"}, "Queued (position {:d})".format(pos))
        _redraw_areas(context)
        return {"FINISHED"}


class BFACW_OT_chat_capture_render(Operator):  # type: ignore[misc]
    """Render the current view and attach the result (main thread only)."""
    bl_idname = "bfacw.chat_capture_render"
    bl_label = "Render View"
    bl_description = (
        "Render the current 3D Viewport view through a temporary camera "
        "(your own camera is never moved) and attach the result"
    )

    def execute(self, context: bpy.types.Context) -> set[str]:
        # Render the current view.  A throwaway camera is aligned to the
        # viewport and removed again, so the scene is left untouched.
        path = chat_attachments.capture_render_from_view()
        if not path:
            # No 3D Viewport to render from (e.g. a Text-Editor-only
            # layout): fall back to the last Render Result, if any.
            path = chat_attachments.capture_render()
        if not path:
            self.report({"WARNING"}, "Nothing to render -- open a 3D Viewport")
            return {"CANCELLED"}
        img = chat_attachments.load_image_file(path)
        if img is None:
            self.report({"ERROR"}, "Could not load the rendered image")
            return {"CANCELLED"}
        _attach_to_socket(context, self, img)
        return {"FINISHED"}


class BFACW_OT_chat_capture_screen(Operator):  # type: ignore[misc]
    """Attach a screenshot to the chat socket (main thread only)."""
    bl_idname = "bfacw.chat_capture_screen"
    bl_label = "Capture Screen"
    bl_description = "Screenshot the 3D Viewport (or whole window) and attach it"

    def execute(self, context: bpy.types.Context) -> set[str]:
        # Prefer the 3D Viewport (what the model needs to see); fall
        # back to the whole window when no viewport exists.  Operators
        # always run on the main thread, which these captures require.
        path = chat_attachments.capture_screen(target="VIEW_3D")
        if not path:
            path = chat_attachments.capture_screen(target="WINDOW")
        if not path:
            self.report({"WARNING"}, "Screenshot unavailable (background mode?)")
            return {"CANCELLED"}
        img = chat_attachments.load_image_file(path)
        if img is None:
            self.report({"ERROR"}, "Could not load the screenshot")
            return {"CANCELLED"}
        _attach_to_socket(context, self, img)
        return {"FINISHED"}


class BFACW_OT_chat_image_drop(Operator):  # type: ignore[misc]
    """Receive an image file dragged onto the Coworker chat panel."""
    bl_idname = "bfacw.chat_image_drop"
    bl_label = "Attach Image to Coworker Chat"
    bl_description = "Attach the dropped image file to the chat"

    # Blender's file-handler drop writes these onto the import operator:
    # ``filepath`` is the first supported path, while ``directory`` +
    # ``files`` describe the whole drop (multi-file drops included).
    filepath: bpy.props.StringProperty(  # type: ignore[valid-type]
        subtype='FILE_PATH', options={'SKIP_SAVE'})
    directory: bpy.props.StringProperty(  # type: ignore[valid-type]
        subtype='DIR_PATH', options={'SKIP_SAVE'})
    files: bpy.props.CollectionProperty(  # type: ignore[valid-type]
        type=bpy.types.OperatorFileListElement, options={'SKIP_SAVE'})

    def execute(self, context: bpy.types.Context) -> set[str]:
        img = chat_attachments.load_image_file(self.filepath)
        if img is None:
            self.report({"WARNING"}, "Not a supported image file")
            return {"CANCELLED"}
        _attach_to_socket(context, self, img)
        return {"FINISHED"}


def _image_data_icon_value() -> int:
    """``FileHandler.bl_icon`` for the image icon (a Bforartists addition).

    Bforartists gives FileHandler a plain INT ``bl_icon`` ("Icon to
    display for the file handler", rna_ui.cc) and passes it to the entry
    it adds to its "multiple file handlers" drop menu, so the icon has to
    be looked up as the enum's integer value.  Returns 0 ("no icon") when
    the build has no such property or the enum cannot be read: stock
    Blender simply ignores the attribute.
    """
    try:
        items = bpy.types.UILayout.bl_rna.functions["label"].parameters[
            "icon"].enum_items
        return int(items["IMAGE_DATA"].value)
    except Exception:  # noqa: BLE001  # pylint: disable=broad-exception-caught
        return 0


_IMAGE_DATA_ICON = _image_data_icon_value()


class BFACW_FH_chat_drop(bpy.types.FileHandler):  # type: ignore[misc]
    """Drag-and-drop image files onto the Coworker chat panel.

    ``poll_drop`` gets no mouse position, so the drop is scoped by region
    + panel category: the Text Editor chat panel, or the 3D Viewport
    sidebar while the "Coworker" tab is active.

    In the 3D Viewport Blender's own image drop handlers match the same
    extensions, so several handlers match and Blender lists them -- our
    entry is "Attach Image to Coworker Chat".  In the Text Editor ours is
    the only match, so the drop attaches directly.  We deliberately do not
    patch Blender's handlers (see the note above
    ``BFACW_OT_copy_message``).
    """
    bl_idname = "BFACW_FH_chat_drop"
    bl_label = "Attach Image to Coworker Chat"
    bl_import_operator = "bfacw.chat_image_drop"
    # Semicolon-separated per bpy.types.FileHandler.bl_file_extensions;
    # mirrors chat_attachments.SUPPORTED_EXTENSIONS.
    bl_file_extensions = ".png;.jpg;.jpeg;.webp;.bmp;.tif;.tiff"
    # Bforartists shows this icon on our entry in its "multiple file
    # handlers" drop menu (see _image_data_icon_value).
    bl_icon = _IMAGE_DATA_ICON

    @classmethod
    def poll_drop(cls, context) -> bool:
        # Blender calls this mid-drag from C, so it must never raise or
        # return a non-bool: anything unexpected means "not ours".
        try:
            area = context.area
            if area is None:
                return False
            if area.type == "TEXT_EDITOR":
                return True
            return _is_coworker_panel_region(context)
        except (AttributeError, ReferenceError, TypeError, ValueError):
            return False


def _is_coworker_panel_region(context) -> bool:
    """True for the 3D Viewport sidebar while the Coworker tab is active."""
    try:
        area = context.area
        region = context.region
    except AttributeError:
        return False
    return (area is not None
            and area.type == "VIEW_3D"
            and region is not None
            and region.type == "UI"
            and getattr(region, "active_panel_category", "") == _CHAT_PANEL_CATEGORY)


# NOTE: we deliberately do NOT monkey-patch Blender's own image FileHandlers
# (VIEW3D_FH_empty_image / VIEW3D_FH_camera_background_image) to make them
# yield inside our panel.  Doing so replaced ``poll_drop`` on classes that
# Blender registers from a startup module, and a wrapper left behind by an
# add-on reload segfaulted Blender inside ``bpy_class_call`` on the next drag
# (EXCEPTION_ACCESS_VIOLATION via file_handler_poll_drop).  The trade-off is
# that a drop in the 3D Viewport matches several handlers, so Blender shows
# its usual "multiple file handlers" menu -- with our "Attach Image to
# Coworker Chat" entry in it.  That is stock, safe behaviour: the Text Editor
# chat panel still attaches directly, and the socket, the two capture buttons
# and the menu entry cover the rest.


class BFACW_OT_copy_message(Operator):  # type: ignore[misc]
    """Copy a message from the conversation history to the clipboard."""
    bl_idname = "bfacw.copy_message"
    bl_label = "Copy Message"
    bl_description = "Copy this message\'s content to the clipboard"

    message_index: bpy.props.IntProperty(  # type: ignore[valid-type]
        name="Message Index",
        description="Index into conversation_history",
        default=-1,
    )

    archived: bpy.props.BoolProperty(  # type: ignore[valid-type]
        name="Archived",
        description="Read the message from the retired-history archive",
        default=False,
    )

    def execute(self, context: bpy.types.Context) -> set[str]:
        from . import session_memory as _sm
        history = (
            _sm.store.retired_history if self.archived
            else agent_controller._agent_state.conversation_history
        )
        if self.message_index < 0 or self.message_index >= len(history):
            self.report({"ERROR"}, "Message not found (stale index)")
            return {"CANCELLED"}
        msg = history[self.message_index]
        role = msg.get("role", "")
        content = msg.get("content", "") or ""

        # Build clipboard text with context.
        parts = []
        if role == "tool":
            name = msg.get("name", "")
            summary = msg.get("summary", "")
            if name:
                parts.append("[Tool: {:s}]".format(name))
            if summary and summary != content:
                parts.append(summary)
            if content:
                parts.append("--- Full output ---")
                parts.append(content)
        elif role == "reasoning":
            label = msg.get("label", "Thinking")
            parts.append("[{:s}]".format(label))
            parts.append(content)
        else:
            if content:
                parts.append(content)

        context.window_manager.clipboard = "\n\n".join(parts)
        self.report({"INFO"}, "Message copied to clipboard")
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# @Mention System (Tier 2+)

# Mention categories with their data sources and icons.
_MENTION_CATEGORIES = {
    "object": {
        "label": "Objects",
        "icon": 'OUTLINER_OB_MESH',
        "data": lambda: [
            {"name": obj.name, "type": obj.type, "category": "object"}
            for obj in bpy.data.objects
        ],
    },
    "material": {
        "label": "Materials",
        "icon": 'MATERIAL',
        "data": lambda: [
            {"name": mat.name, "type": "MAT", "category": "material"}
            for mat in bpy.data.materials
        ],
    },
    "collection": {
        "label": "Collections",
        "icon": 'OUTLINER_COLLECTION',
        "data": lambda: [
            {"name": col.name, "type": "COL", "category": "collection"}
            for col in bpy.data.collections
        ],
    },
    "nodegroup": {
        "label": "Node Groups",
        "icon": 'NODETREE',
        "data": lambda: [
            {"name": ng.name, "type": ng.type or "NODE", "category": "nodegroup"}
            for ng in bpy.data.node_groups
        ],
    },
    "world": {
        "label": "Worlds",
        "icon": 'WORLD',
        "data": lambda: [
            {"name": w.name, "type": "WORLD", "category": "world"}
            for w in bpy.data.worlds
        ],
    },
    "action": {
        "label": "Actions",
        "icon": 'ACTION',
        "data": lambda: [
            {"name": a.name, "type": "ACT", "category": "action"}
            for a in bpy.data.actions
        ],
    },
}


def _collect_all_mentionables() -> list[dict]:
    """Collect all mentionable items from all categories."""
    items = []
    for cat_key, cat_info in _MENTION_CATEGORIES.items():
        try:
            items.extend(cat_info["data"]())
        except Exception:
            pass
    return items


def _filter_mentionables(
    items: list[dict],
    filter_text: str = "",
    category: str = "",
) -> list[dict]:
    """Filter mentionable items by text and category."""
    filtered = items
    if category and category in _MENTION_CATEGORIES:
        filtered = [i for i in filtered if i.get("category") == category]
    if filter_text:
        filter_lower = filter_text.lower()
        filtered = [i for i in filtered if filter_lower in i["name"].lower()]
    return filtered


class BFACW_OT_mention_search(Operator):  # type: ignore[misc]
    """Search for scene items by name and insert @mention into chat."""
    bl_idname = "bfacw.mention_search"
    bl_label = "@ Mention"
    bl_description = "Search objects, materials, collections, and more to insert @mention"

    filter_text: StringProperty(  # type: ignore[valid-type]
        name="Filter",
        default="",
    )
    category: StringProperty(  # type: ignore[valid-type]
        name="Category",
        default="",
    )

    def execute(self, context: bpy.types.Context) -> set[str]:
        wm = context.window_manager
        props = wm.bfacw_chat_props  # type: ignore[attr-defined]

        # Auto-detect filter from input: if user typed @word, use word as filter.
        current_input = props.chat_input or ""
        if not self.filter_text and "@" in current_input:
            # Find the last @ and extract text after it.
            last_at = current_input.rfind("@")
            after_at = current_input[last_at + 1:]
            # If there's text after @ without a space, use it as filter.
            if after_at and not after_at.startswith(" "):
                self.filter_text = after_at.split()[-1] if after_at.split() else ""

        # Collect and filter items.
        all_items = _collect_all_mentionables()
        filtered = _filter_mentionables(all_items, self.filter_text, self.category)

        if not filtered:
            self.report({"INFO"}, "No matches found.")
            return {"CANCELLED"}

        def _draw_menu(menu, _context):
            layout = menu.layout

            # Category filter buttons.
            row = layout.row(align=True)
            row.label(text="", icon='VIEWZOOM')
            op = row.operator("bfacw.mention_search", text="All", icon='NONE')
            op.category = ""
            op.filter_text = self.filter_text
            for cat_key, cat_info in _MENTION_CATEGORIES.items():
                op = row.operator(
                    "bfacw.mention_search",
                    text="",
                    icon=cat_info["icon"],
                )
                op.category = cat_key
                op.filter_text = self.filter_text

            layout.separator()

            # Filtered results.
            display_items = filtered[:50]  # Limit to 50.
            if self.filter_text:
                layout.label(
                    text="{:d} matches for '{:s}'".format(len(display_items), self.filter_text),
                    icon='SORTBYEXT',
                )
            else:
                layout.label(
                    text="{:d} items".format(len(display_items)),
                    icon='INFO',
                )

            for item in display_items:
                cat = item.get("category", "object")
                cat_info = _MENTION_CATEGORIES.get(cat, _MENTION_CATEGORIES["object"])
                op = layout.operator(
                    "bfacw.mention_insert",
                    text="[{:s}] {:s}".format(item["type"], item["name"]),
                    icon=cat_info["icon"],
                )
                op.object_name = item["name"]
                op.category = cat

        wm.popup_menu(_draw_menu, title="@ Mention", icon='OUTLINER_OB_MESH')
        return {"FINISHED"}


class BFACW_OT_mention_insert(Operator):  # type: ignore[misc]
    """Insert an @mentioned item name into the chat input."""
    bl_idname = "bfacw.mention_insert"
    bl_label = "Insert @mention"
    bl_description = "Insert the selected item name as an @mention in the chat input"

    object_name: StringProperty(  # type: ignore[valid-type]
        name="Item Name",
        default="",
    )
    category: StringProperty(  # type: ignore[valid-type]
        name="Category",
        default="object",
    )

    def execute(self, context: bpy.types.Context) -> set[str]:
        wm = context.window_manager
        props = wm.bfacw_chat_props  # type: ignore[attr-defined]
        current = props.chat_input or ""

        # Remove any partial @mention that was being typed.
        # Find the last @ and remove everything after it.
        if "@" in current:
            last_at = current.rfind("@")
            before_at = current[:last_at]
            after_at = current[last_at + 1:]
            # If there's text after @ without a space, it's a partial mention.
            if after_at and not after_at.startswith(" "):
                current = before_at

        # Insert the mention.
        mention = "@{:s}".format(self.object_name)
        if current and not current.endswith(" "):
            mention = " " + mention
        props.chat_input = current + mention + " "
        _redraw_areas(context)
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# Project Rules (Tier 2)

def _rules_dir() -> Path:
    """Return the directory where project rules are stored."""
    base = Path(bpy.utils.user_resource("SCRIPTS")) / "bfa_coworker_rules"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _global_rules_path() -> Path:
    """Return the path to the global rules file."""
    return _rules_dir() / "global.md"


def _blend_rules_path() -> Path:
    """Return the path to the blend-file-specific rules file."""
    if bpy.data.filepath:
        stem = Path(bpy.data.filepath).stem
        return _rules_dir() / "{:s}.md".format(stem)
    return _rules_dir() / "default.md"


def _load_rules() -> str:
    """Load project rules, merging global and blend-specific files."""
    parts = []
    global_path = _global_rules_path()
    if global_path.exists():
        try:
            parts.append(global_path.read_text(encoding="utf-8"))
        except OSError:
            pass
    blend_path = _blend_rules_path()
    if blend_path.exists():
        try:
            parts.append(blend_path.read_text(encoding="utf-8"))
        except OSError:
            pass
    return "\n\n".join(parts)


class BFACW_OT_edit_rules(Operator):  # type: ignore[misc]
    """Open the project rules file in the Blender Text Editor."""
    bl_idname = "bfacw.edit_rules"
    bl_label = "Edit Rules"
    bl_description = "Open the project rules file for editing"

    def execute(self, context: bpy.types.Context) -> set[str]:
        rules_path = _blend_rules_path()

        # Create default rules file if it doesn't exist.
        if not rules_path.exists():
            try:
                rules_path.write_text(
                    "# Project Rules for {:s}\n"
                    "# Write instructions for the agent below.\n"
                    "# Each line starting with # is a comment.\n"
                    "\n"
                    "- Be concise and specific.\n"
                    "- Use Blender 5.2+ API conventions.\n".format(
                        Path(bpy.data.filepath).stem if bpy.data.filepath else "this scene"
                    ),
                    encoding="utf-8",
                )
            except OSError as ex:
                self.report({"ERROR"}, "Failed to create rules file: {:s}".format(str(ex)))
                return {"CANCELLED"}

        # Open in Text Editor.
        try:
            text = bpy.data.texts.load(str(rules_path), internal=False)
        except (OSError, RuntimeError) as ex:
            self.report({"ERROR"}, "Failed to open rules file: {:s}".format(str(ex)))
            return {"CANCELLED"}

        # Switch to Text Editor workspace.
        for area in context.screen.areas:
            if area.type == 'TEXT_EDITOR':
                area.spaces[0].text = text
                area.tag_redraw()
                break

        self.report({"INFO"}, "Opened rules file: {:s}".format(str(rules_path)))
        return {"FINISHED"}


class BFACW_OT_reload_rules(Operator):  # type: ignore[misc]
    """Reload project rules into the agent's system prompt."""
    bl_idname = "bfacw.reload_rules"
    bl_label = "Reload Rules"
    bl_description = "Reload project rules into the Coworker's system prompt"

    def execute(self, context: bpy.types.Context) -> set[str]:
        # Clear cached system prompt so it's rebuilt on next turn.
        agent_controller._clear_system_prompt_cache()
        self.report({"INFO"}, "Project rules reloaded")
        return {"FINISHED"}


class BFACW_OT_agent_start(Operator):  # type: ignore[misc]
    """Start the Coworker agent: MCP bridge, MCP server, and LLM backend."""
    bl_idname = "bfacw.agent_start"
    bl_label = "Start Coworker"
    bl_description = "Start the Coworker agent: MCP bridge, MCP server, and LLM backend"

    def execute(self, context: bpy.types.Context) -> set[str]:
        prefs = context.preferences.addons[__package__].preferences

        # In External Harness mode, only start the bridge server.
        if prefs.operating_mode == "EXTERNAL_HARNESS":
            return self._start_bridge_only(context)

        return self._start_full_agent(context)

    def _start_bridge_only(self, context: bpy.types.Context) -> set[str]:
        """Start only the bridge server (External Harness mode)."""
        wm = context.window_manager
        props = wm.bfacw_chat_props  # type: ignore[attr-defined]

        if mcp_to_blender_server.is_running():
            self.report({"INFO"}, "Bridge server already running")
            actual = mcp_to_blender_server.get_actual_port()
            if actual:
                props.chat_status = "External Harness -- Bridge on port {:d}".format(actual)
            else:
                props.chat_status = "External Harness -- Bridge running"
            return {"FINISHED"}

        if bpy.app.background:
            self.report({"ERROR"}, "Cannot start in background mode")
            return {"CANCELLED"}

        prefs = context.preferences.addons[__package__].preferences
        _bridge_port, _, _ = effective_ports(prefs)
        try:
            mcp_to_blender_server.start(prefs.host, _bridge_port)
        except Exception as ex:  # pylint: disable=broad-exception-caught
            self.report({"ERROR"}, "Bridge server failed: {:s}".format(str(ex)))
            return {"CANCELLED"}

        from . import execute_interactive
        bpy.app.timers.register(
            execute_interactive.run,
            first_interval=mcp_to_blender_server.TIMER_INTERVAL_ACTIVE,
            persistent=True,
        )

        actual = mcp_to_blender_server.get_actual_port()
        if actual:
            props.chat_status = "External Harness -- Bridge on port {:d}".format(actual)
            self.report({"INFO"}, "Bridge server started on port {:d}".format(actual))
        else:
            props.chat_status = "External Harness -- Bridge running"
            self.report({"INFO"}, "Bridge server started")
        _redraw_areas(context)
        return {"FINISHED"}

    def _start_full_agent(self, context: bpy.types.Context) -> set[str]:
        wm = context.window_manager
        props = wm.bfacw_chat_props  # type: ignore[attr-defined]

        # Local mode: never let llama-server trigger an implicit multi-GB
        # download.  If the configured model is not on disk (and not cached),
        # stall the launch and ask the user to confirm the download first.
        _prefs = context.preferences.addons[__package__].preferences
        if _prefs.operating_mode == "LOCAL_LLM" and not llm_manager.get_state().is_running:
            _sync_prefs_to_config(_prefs)
            _existing = _prefs.existing_model_path
            _ready, _info = llm_manager.check_local_model_ready(
                _existing if _existing and os.path.isfile(_existing) else None)
            if not _ready:
                llm_manager.set_pending_model_download(_info)
                props.chat_status = "Waiting for download confirmation..."
                _redraw_areas(context)
                _result = bpy.ops.bfacw.confirm_model_download('INVOKE_DEFAULT')
                return {"CANCELLED"} if _result == {"CANCELLED"} else {"FINISHED"}

        # Warm the main-thread bpy-derived cache (system prompt, prefs) now, on
        # the main thread, so the first turn worker never reads bpy itself.
        agent_controller.prefetch_main_thread_context()

        # Step 1: Start the MCP bridge server (inside Blender).
        if not mcp_to_blender_server.is_running():
            if bpy.app.background:
                self.report({"ERROR"}, "Cannot start in background mode")
                return {"CANCELLED"}
            prefs = context.preferences.addons[__package__].preferences
            _bridge_port, _mcp_port, _llm_port = effective_ports(prefs)
            try:
                mcp_to_blender_server.start(prefs.host, _bridge_port)
            except Exception as ex:  # pylint: disable=broad-exception-caught
                self.report({"ERROR"}, "Bridge server failed: {:s}".format(str(ex)))
                return {"CANCELLED"}
            # Register timer.
            from . import execute_interactive
            bpy.app.timers.register(
                execute_interactive.run,
                first_interval=mcp_to_blender_server.TIMER_INTERVAL_ACTIVE,
                persistent=True,
            )
            actual_bridge = mcp_to_blender_server.get_actual_port()
            if actual_bridge:
                self.report({"INFO"}, "Bridge server started on port {:d}".format(actual_bridge))
            else:
                self.report({"INFO"}, "Bridge server started")

        # Step 2: Start the MCP HTTP server.
        if not agent_controller._agent_state.mcp_server_running:
            prefs = context.preferences.addons[__package__].preferences
            _bridge_port, _mcp_port, _llm_port = effective_ports(prefs)
            proc = agent_controller.start_mcp_server(port=_mcp_port, blender_port=_bridge_port)
            if proc is None:
                self.report({"ERROR"}, agent_controller._agent_state.error)
                return {"CANCELLED"}
            actual_mcp = agent_controller._agent_state.mcp_port_actual
            if actual_mcp:
                self.report({"INFO"}, "MCP server started on port {:d}".format(actual_mcp))
            else:
                self.report({"INFO"}, "MCP server started on port {:d}".format(_mcp_port))

        # Step 3: Start the LLM backend (only in local mode).
        # This can be slow (model download or server startup), so it runs
        # on a background thread to avoid freezing Blender's UI.
        prefs = context.preferences.addons[__package__].preferences
        # Sync preferences to llm_manager config before starting.
        _sync_prefs_to_config(prefs)
        llm_cfg = llm_manager.get_config()
        _bridge_port, _mcp_port, _llm_port = effective_ports(prefs)
        llm_cfg.local_port = _llm_port
        llm_manager.set_config(llm_cfg)

        if llm_cfg.mode == "local":
            llm_state = llm_manager.get_state()

            def _set_chat_status(msg: str) -> None:
                bpy.app.timers.register(
                    lambda m=msg: setattr(props, "chat_status", m) or _redraw_areas_safe(),
                    first_interval=0.0,
                )

            if not llm_state.is_running:

                def _start_llm_backend():
                    existing_path = prefs.existing_model_path
                    if existing_path and os.path.isfile(existing_path):
                        proc = llm_manager.start_local_llama(model_path=existing_path)
                    else:
                        proc = llm_manager.start_local_llama()
                    if proc is None:
                        _err = llm_manager.get_state().error or "llama-server failed to start"
                        agent_controller._agent_state.error = _err
                        _set_chat_status("Error: " + _err)
                        return
                    # Wait for the model to actually load before claiming
                    # readiness.  Posting the welcome right after Popen makes
                    # it appear even when llama-server crashes at startup
                    # ("welcome message happens, then closes") and the first
                    # real turn then hangs 120s on a dead port.
                    _set_chat_status("Loading model... (large models can take a few minutes)")
                    if not llm_manager.wait_until_ready(timeout=300.0, proc=proc):
                        _err = llm_manager.get_state().error or "llama-server did not become ready"
                        agent_controller._agent_state.error = _err
                        _set_chat_status("Error: " + _err)
                        return
                    # Warm up tools + post welcome message (background thread).
                    _bridge_port, _mcp_port, _llm_port = effective_ports(prefs)
                    actual_mcp = agent_controller._agent_state.mcp_port_actual
                    warmup_mcp = actual_mcp if actual_mcp else _mcp_port
                    agent_controller.warmup_agent(
                        on_status=lambda s: bpy.app.timers.register(
                            lambda s=s: setattr(props, "chat_status", s) or _redraw_areas_safe(),
                            first_interval=0.0,
                        ),
                        mcp_port=warmup_mcp,
                    )
                    # Mark connected on the main thread after warmup completes.
                    _set_chat_status("Connected")

                thread = threading.Thread(target=_start_llm_backend, daemon=True)
                thread.start()
                props.chat_status = "Starting LLM backend..."
            else:
                # Already running -- warmup in background thread, but only
                # after the model has actually finished loading.
                def _warmup_existing():
                    if llm_manager.get_config().mode == "local":
                        _set_chat_status("Loading model... (large models can take a few minutes)")
                        if not llm_manager.wait_until_ready(
                            timeout=300.0, proc=llm_manager.get_llama_process()
                        ):
                            _err = llm_manager.get_state().error or "llama-server did not become ready"
                            agent_controller._agent_state.error = _err
                            _set_chat_status("Error: " + _err)
                            return
                    _bridge_port, _mcp_port, _llm_port = effective_ports(prefs)
                    actual_mcp = agent_controller._agent_state.mcp_port_actual
                    warmup_mcp = actual_mcp if actual_mcp else _mcp_port
                    agent_controller.warmup_agent(
                        on_status=lambda s: bpy.app.timers.register(
                            lambda s=s: setattr(props, "chat_status", s) or _redraw_areas_safe(),
                            first_interval=0.0,
                        ),
                        mcp_port=warmup_mcp,
                    )
                    _set_chat_status("Connected")
                threading.Thread(target=_warmup_existing, daemon=True).start()
                props.chat_status = "Warming up..."
        else:
            # In remote mode, no LLM backend is started.
            def _warmup_remote():
                _bridge_port, _mcp_port, _llm_port = effective_ports(prefs)
                actual_mcp = agent_controller._agent_state.mcp_port_actual
                warmup_mcp = actual_mcp if actual_mcp else _mcp_port
                agent_controller.warmup_agent(
                    on_status=lambda s: bpy.app.timers.register(
                        lambda s=s: setattr(props, "chat_status", s) or _redraw_areas_safe(),
                        first_interval=0.0,
                    ),
                    mcp_port=warmup_mcp,
                )
                bpy.app.timers.register(
                    lambda: setattr(props, "chat_status", "Connected") or _redraw_areas_safe(),
                    first_interval=0.0,
                )
            threading.Thread(target=_warmup_remote, daemon=True).start()
            props.chat_status = "Warming up..."

        # Load chat history.
        history = _load_chat_history()
        # Restore the session-memory store (memory note + checkpoints).
        _load_session_memory_state()
        if history:
            # -- Loaded-history diagnostic -----------------------------
            # A persisted history is restored verbatim, so a stale prompt or
            # a bad shape from an earlier session is sent to the model as-is.
            # Log what was loaded -- the first user message in particular
            # reveals whether the model is answering a stale prompt.
            print("[Coworker] _load_chat_history: loaded {:d} messages from {:s}".format(
                len(history), str(_chat_history_path())))
            print(agent_controller._describe_history_for_log(history))
            # Sanitize before use: a history written by an older build may
            # contain ui_only greetings, a leading assistant message,
            # reasoning entries, or half-finished tool-call exchanges -- all
            # of which trip strict Jinja chat templates.
            history = agent_controller._sanitize_loaded_history(history)
            if history:
                print("[Coworker] _load_chat_history: after sanitize:")
                print(agent_controller._describe_history_for_log(history))
            agent_controller._agent_state.conversation_history = history

        # Local-mode status is driven by the background thread (Starting ->
        # Loading -> Connected / Error: ...); only remote mode marks
        # "Connected" here.
        if llm_cfg.mode != "local":
            props.chat_status = "Connected"

        _redraw_areas(context)
        return {"FINISHED"}


class BFACW_OT_agent_stop(Operator):  # type: ignore[misc]
    """Stop the Coworker agent and all subprocesses."""
    bl_idname = "bfacw.agent_stop"
    bl_label = "Stop Coworker"
    bl_description = "Stop the Coworker agent and all subprocesses"

    def execute(self, context: bpy.types.Context) -> set[str]:
        wm = context.window_manager
        props = wm.bfacw_chat_props  # type: ignore[attr-defined]

        # Stop LLM.
        llm_manager.stop_local_llama()

        # Stop MCP server.
        agent_controller.stop_mcp_server()

        # Stop bridge server.
        if mcp_to_blender_server.is_running():
            from . import execute_interactive
            mcp_to_blender_server.stop()
            if bpy.app.timers.is_registered(execute_interactive.run):
                bpy.app.timers.unregister(execute_interactive.run)

        props.chat_status = "Stopped"
        agent_controller._agent_state.mcp_server_running = False
        # Stopping the agent ends the working session -- clear the token
        # accounting so the context bar does not stay pinned at its old value.
        agent_controller._agent_state.reset_usage()
        agent_controller.clear_session_scene_lock()
        _redraw_areas(context)
        return {"FINISHED"}


class BFACW_OT_agent_restart(Operator):  # type: ignore[misc]
    """Restart the Coworker agent (stop then start)."""
    bl_idname = "bfacw.agent_restart"
    bl_label = "Restart Coworker"
    bl_description = "Stop all components and restart the Coworker agent"

    def execute(self, context: bpy.types.Context) -> set[str]:
        wm = context.window_manager
        props = wm.bfacw_chat_props  # type: ignore[attr-defined]

        # Stop first.
        props.chat_status = "Stopping..."
        _redraw_areas(context)

        llm_manager.stop_local_llama()
        agent_controller.stop_mcp_server()
        if mcp_to_blender_server.is_running():
            from . import execute_interactive
            mcp_to_blender_server.stop()
            if bpy.app.timers.is_registered(execute_interactive.run):
                bpy.app.timers.unregister(execute_interactive.run)

        agent_controller._agent_state.mcp_server_running = False
        # Fresh agent session -- clear stale token accounting and the
        # session-created entity set.
        agent_controller._agent_state.reset_usage()
        agent_controller.clear_session_scene_lock()

        # Start again after a brief delay.
        def _deferred_start():
            props.chat_status = "Starting..."
            _redraw_areas_safe()
            # Re-register the bridge timer.
            if not mcp_to_blender_server.is_running():
                from . import execute_interactive
                prefs = context.preferences.addons[__package__].preferences
                _bridge_port, _, _ = effective_ports(prefs)
                mcp_to_blender_server.start(prefs.host, _bridge_port)
                bpy.app.timers.register(
                    execute_interactive.run,
                    first_interval=mcp_to_blender_server.TIMER_INTERVAL_ACTIVE,
                    persistent=True)
            # Start MCP server.
            agent_controller.start_mcp_server()
            props.chat_status = "Connected"
            _redraw_areas_safe()
            return None  # Don't repeat timer.

        bpy.app.timers.register(_deferred_start, first_interval=0.5)
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# Timer for UI updates

# Track whether we already opened the mention popup for the current @
_mention_popup_open = False

# Last status mirrored from the turn worker into the UI property.  The worker
# records ``_agent_state.ui_status`` (plain Python); the main-thread timer
# applies it to ``props.chat_status``.  The marker avoids re-applying an
# unchanged value (which would clobber status text set directly by the
# start/stop operators on the main thread).
_last_applied_ui_status = ""


def chat_timer_update() -> float | None:
    """
    Timer callback that periodically redraws chat areas, animates
    the "Thinking..." indicator, and auto-opens the mention popup
    when the user types @ in the input field.

    Registered when the add-on starts, runs while Blender is alive.
    """
    global _mention_popup_open
    global _last_applied_ui_status
    from . import agent_controller as _ac

    # Animate thinking dots.
    if _ac._agent_state.is_thinking:
        _ac._agent_state.thinking_dots += 1

    # Mirror the turn worker's status into the UI property.  The worker must
    # not touch a bpy property itself (that races Blender's context counter);
    # it records plain Python state and we apply it here on the main thread.
    # Apply only when it CHANGED, so status text set directly by the start/stop
    # operators is not clobbered on the next tick.
    _ui_status = _ac._agent_state.ui_status
    if _ui_status and _ui_status != _last_applied_ui_status:
        for wm in bpy.data.window_managers:
            _props = getattr(wm, "bfacw_chat_props", None)
            if _props is not None:
                _props.chat_status = _ui_status
        _last_applied_ui_status = _ui_status

    # Auto-open @mention popup when user types @ in input.
    try:
        for wm in bpy.data.window_managers:
            props = getattr(wm, "bfacw_chat_props", None)
            if props is None:
                continue
            text = props.chat_input or ""
            if "@" in text and not _ac._agent_state.is_thinking:
                last_at = text.rfind("@")
                after = text[last_at + 1:]
                # Only trigger if @ is at end or followed by text (not space).
                if not after or (not after.startswith(" ") and len(after) <= 30):
                    if not _mention_popup_open:
                        _mention_popup_open = True
                        # Defer popup to avoid timer re-entrancy.
                        bpy.app.timers.register(
                            lambda: _open_mention_for_at(text),
                            first_interval=0.0,
                        )
            else:
                _mention_popup_open = False
    except Exception:
        pass

    # Mirror the pinned goal & plan to/from its editable text block.
    try:
        _sync_plan_text()
    except Exception as _plan_ex:  # pylint: disable=broad-exception-caught
        print("[Coworker] plan text sync skipped -- {:s}".format(str(_plan_ex)))

    # Redraw all chat panels.
    for wm in bpy.data.window_managers:
        for win in wm.windows:
            for area in win.screen.areas:
                if area.type == 'VIEW_3D':
                    area.tag_redraw()
                if area.type == 'TEXT_EDITOR':
                    area.tag_redraw()
    # Tick faster while a turn is running so the spinner reads as motion;
    # otherwise keep the light idle cadence.
    return 0.1 if _ac._agent_state.is_thinking else 0.5


def _open_mention_for_at(text: str) -> float | None:
    """Open the mention popup filtered by the text after @."""
    global _mention_popup_open
    try:
        last_at = text.rfind("@")
        after = text[last_at + 1:] if last_at >= 0 else ""
        # Don't open if user already inserted a mention (space after @).
        if after.startswith(" "):
            _mention_popup_open = False
            return None
        bpy.ops.bfacw.mention_search(filter_text=after)
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Panels

class BFACW_PT_chat_panel(Panel):  # type: ignore[misc]
    """Main chat panel in the 3D Viewport sidebar -- input and messages."""
    bl_label = "Coworker"
    bl_idname = "BFACW_PT_chat_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = _CHAT_PANEL_CATEGORY

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        return not bpy.app.background

    def draw(self, context: bpy.types.Context) -> None:
        layout = self.layout
        wm = context.window_manager
        props = wm.bfacw_chat_props  # type: ignore[attr-defined]
        state = agent_controller._agent_state
        prefs = context.preferences.addons[__package__].preferences
        is_harness = (prefs.operating_mode == "EXTERNAL_HARNESS")

        # -- Mode info + Settings button --
        mode_row = layout.row(align=True)
        mode_row.scale_y = 0.9
        if is_harness:
            mode_row.label(text="External MCP", icon='WORLD')
        elif prefs.operating_mode == "LOCAL_LLM":
            mode_row.label(text="Local LLM", icon='CONSOLE')
        else:
            mode_row.label(text="Remote API", icon='URL')
        # NOTE: The model name is intentionally NOT shown here (issue #70).
        # It lives only in the Status & Diagnostics panel, which shows the
        # mode-correct model (local llama-server model or remote API model).
        mode_row.separator(factor=0.3)
        mode_row.operator("bfacw.open_addon_prefs", icon="PREFERENCES", text="")

        # -- Agent control buttons (compact) --
        row = layout.row(align=True)
        row.scale_y = 1.8
        if is_harness:
            if mcp_to_blender_server.is_running():
                actual = mcp_to_blender_server.get_actual_port()
                tip = "Bridge running on port {:d}".format(actual) if actual else "Stop Bridge"
                row.operator("bfacw.agent_stop", icon="X", text="Stop Bridge")
            else:
                row.operator("bfacw.agent_start", icon="PLAY", text="Start Bridge")
        else:
            if state.mcp_server_running:
                row.operator("bfacw.agent_stop", icon="X", text="Stop")
            else:
                row.operator("bfacw.agent_start", icon="PLAY", text="Start")

        # -- Compact status line --
        if is_harness:
            status = "Bridge Running" if mcp_to_blender_server.is_running() else "Bridge Offline"
            is_ok = mcp_to_blender_server.is_running()
        else:
            status = props.chat_status
            if state.is_thinking:
                elapsed = time.time() - state.thinking_start_time if state.thinking_start_time else 0.0
                status = "{:s} {:s} ({:s})".format(
                    _phase_text(state), _spinner_char(state),
                    _fmt_duration(elapsed))
            elif not state.mcp_server_running:
                status = "Offline"
            elif state.error:
                status = "Error: {:s}".format(state.error)
            is_ok = state.mcp_server_running

        status_icon = (
            'CHECKMARK' if is_ok and not state.is_thinking and not state.error else
            'SORTTIME' if state.is_thinking else
            'ERROR' if state.error else 'CANCEL'
        )
        status_row = layout.row()
        status_row.label(text="", icon=status_icon)
        _draw_multiline(status_row, status)
        if state.error:
            err_row = layout.row(align=True)
            err_row.scale_y = 0.8
            err_row.operator(
                "bfacw.copy_status_error",
                icon="COPYDOWN",
                text="Copy Error",
            )
        elif state.warning:
            # Non-fatal notice (e.g. tool-calling downgrade).  Shown when
            # there is no error, so it never masks a real failure.
            warn_row = layout.row()
            warn_row.scale_y = 0.9
            warn_row.label(text="", icon='INFO')
            _draw_multiline(warn_row, state.warning)

        # -- Co-work scene protection notice (scene safety Phase 1) -----
        # While the coworker works it briefly makes the objects it created
        # un-selectable (the soft lock), so the user cannot re-target them
        # mid-turn.  Say so -- otherwise the "I can't click this" moment
        # looks like a bug.  Everything is released when the turn ends.
        if not is_harness:
            try:
                from . import co_work_guard as _cwg
                if _cwg.is_locked():
                    _lo, _lc = _cwg.managed_names()
                    lock_row = layout.row()
                    lock_row.scale_y = 0.8
                    lock_row.label(
                        text="Scene protection: {:d} object(s) temporarily "
                             "unselectable while working".format(len(_lo) + len(_lc)),
                        icon='LOCKED',
                    )
            except Exception:  # pylint: disable=broad-exception-caught
                pass

        # -- External Harness mode --
        if is_harness:
            if mcp_to_blender_server.is_running():
                box = layout.box()
                box.label(text="External MCP Client", icon='WORLD')
                row = box.row(align=True)
                row.scale_y = 1.2
                row.operator("bfacw.open_harness_prefs", icon="PREFERENCES", text="Configure")
                row = box.row(align=True)
                row.prop(prefs, "harness_preset", text="")
                op = row.operator("bfacw.copy_mcp_config", icon="COPYDOWN", text="Copy")
                op.client_type = prefs.harness_preset
            layout.label(text="Chat handled by external client.", icon='INFO')
            return

        # -- Mode toggle --
        row = layout.row(align=True)
        row.prop(props, "chat_mode", expand=True)
        _draw_reasoning_effort_row(layout, prefs)

        layout.separator()

        # -- Image attachment socket (issue #88 / Tier 3k) ----------
        # Above the chat input: set the image BEFORE typing, so what is
        # attached is visible right next to the message it will ride with.
        _draw_attachment_row(layout, props, context)

        # -- Input area --
        layout.textbox(props, "chat_input")

        # @mention button.
        row = layout.row(align=True)
        row.operator("bfacw.mention_search", icon="OUTLINER_OB_MESH", text="@ Mention")

        # -- Action buttons --
        if state.is_thinking:
            # During thinking: Stop + Queue side by side.
            btn_row = layout.row(align=True)
            btn_row.scale_y = 1.5
            btn_row.operator("bfacw.chat_queue_send", icon="ADD", text="Queue")
            btn_row.operator("bfacw.chat_stop", icon="PAUSE", text="Stop")
        else:
            # Idle: Send + New Thread.
            btn_row = layout.row(align=True)
            btn_row.scale_y = 1.5
            btn_row.operator("bfacw.chat_send", icon="PLAY", text="Send")
            btn_row.operator("bfacw.chat_clear", icon="X", text="New Thread")

        layout.separator()

        # -- Conversation history --------------------------------------
        # Drawn here, directly under the input and action buttons, so the
        # messages read as one continuous chat instead of a detached panel.
        self._draw_chat_history(context)

    def _draw_chat_history(self, context: bpy.types.Context) -> None:
        """Draw the conversation history (turns) into this panel.

        Kept as a method so it renders with the chat input, Send button and
        agent controls (the History ``Panel`` was removed; the section now
        lives inside the Coworker panel).
        """
        layout = self.layout
        wm = context.window_manager
        props = wm.bfacw_chat_props  # type: ignore[attr-defined]
        state = agent_controller._agent_state
        prefs = context.preferences.addons[__package__].preferences
        # Snapshot the history ONCE per frame.  The turn loop runs on a worker
        # thread and MUTATES this same list mid-draw (compaction reassigns it,
        # spiral recovery truncates it, auto-continue pops from it).  Iterating
        # the live list raised "list changed size during iteration", which
        # aborted the Panel draw and made the whole chat history disappear for
        # the rest of a long turn.  ``list()`` copies atomically under the GIL,
        # so grouping, lookup and rendering all see one consistent view.
        live = list(state.conversation_history)
        # Cumulative display: retired turns (kept in memory by the session
        # store) are rendered as ORDINARY turns above the live window in ONE
        # continuous conversation, so a compaction never looks like the chat
        # vanished.  Grouping the COMBINED list also recombines a turn whose
        # prompt was retired while its reply stayed live (an earlier compaction
        # could split one) back into a single bubble.  The model's context is
        # unaffected -- retired turns are never re-sent.
        from . import session_memory as _sm
        archived = list(_sm.store.retired_history)
        combined = archived + live
        archived_ids = {id(m) for m in archived}

        if combined:
            # Display order toggle + message count.
            hist_box = layout.box()
            toggle_row = hist_box.row(align=True)
            toggle_row.prop(
                props, "chat_newest_first",
                icon='SORTTIME', text="Newest First",
            )
            # Count displayable messages (exclude system/internal).
            displayable = sum(1 for m in combined if m.get("role") != "system")
            _draw_multiline(
                hist_box,
                "({:d} messages)".format(displayable),
            )
            # The UI-only welcome greeting no longer forms a turn, so show it
            # once here -- otherwise it would never be visible.  Keep the
            # agent hat/icon header so it reads as a Coworker message, not a
            # bare line of text.
            for _g in combined:
                if (_g.get("ui_only") and _g.get("role") == "assistant"
                        and _g.get("content")):
                    _gr = hist_box.row()
                    _gr.label(text="* Coworker:", icon=_AGENT_ICON)
                    _draw_multiline(hist_box, _g.get("content", ""))
                    break

            # Group the FULL conversation (retired + live) into turns.  Each
            # question renders as ONE bubble: your prompt, the reasoning
            # (surface), the Workshop (deeper), then the conclusion -- whether
            # or not the turn has since been retired from the model's context.
            turns = _group_turns(combined)

            # Determine display order and turn limit.
            max_turns = prefs.chat_max_visible_turns
            visible_turns = turns[-max_turns:] if max_turns > 0 else turns
            turn_iter = (
                reversed(visible_turns) if props.chat_newest_first
                else visible_turns
            )

            for _display_idx, turn in enumerate(turn_iter):
                # Absolute turn number by IDENTITY (not list.index, which
                # compares by value and could collide on equal turns).
                _turn_num = next(
                    (i + 1 for i, t in enumerate(turns) if t is turn), 0)
                try:
                    self._draw_turn(
                        hist_box, archived, live, archived_ids, turn,
                        _turn_num, _display_idx, len(visible_turns),
                        props, state,
                    )
                except Exception as _draw_ex:  # pylint: disable=broad-exception-caught
                    # One unexpected turn must never blank the WHOLE history
                    # (a single raising lookup/draw used to vanish the panel
                    # for the rest of a long turn).  Skip just this turn.
                    print("[Coworker] chat history: skipped a turn that failed "
                          "to draw -- {:s}".format(str(_draw_ex)))

        else:
            _draw_multiline(
                layout,
                "Start a conversation by typing a message and clicking Send.",
                icon='INFO',
            )

    def _draw_turn(
        self,
        hist_box,
        archived: list,
        live: list,
        archived_ids: set,
        turn: list,
        turn_num: int,
        display_idx: int,
        visible_count: int,
        props,
        state,
    ) -> None:
        """Draw one conversation turn (user input, Workshop, reply).

        Split out of ``_draw_chat_history`` so a single bad turn can be skipped
        in isolation (see the caller's try/except) instead of aborting the
        whole panel -- the failure mode that hid the chat history during a
        long turn.

        A turn's messages may live in the retired store, the live history, or
        (for a turn split by an earlier compaction) both; each copy action is
        pointed at the list its message actually belongs to.
        """
        def _idx(msg: dict) -> tuple[int, bool]:
            """(index, archived) for *msg* from whichever list holds it."""
            if id(msg) in archived_ids:
                return _hist_index(archived, msg), True
            return _hist_index(live, msg), False

        def _copy(op, msg: dict) -> None:
            """Point a copy operator at the right source list + index."""
            op.message_index, op.archived = _idx(msg)

        user_msg, process_msgs, conclusion_msg = _split_turn(turn)
        if not user_msg:
            if conclusion_msg:
                tb = hist_box.box()
                cr = tb.row()
                cr.label(text="Turn {:d} -- Coworker:".format(turn_num), icon=_AGENT_ICON)
                _copy(cr.operator("bfacw.copy_message", text="", icon="COPYDOWN"),
                      conclusion_msg)
                _draw_multiline(tb, conclusion_msg.get("content", ""))
            return
        has_proc = bool(process_msgs)
        turn_box = hist_box.box()

        # Only the active (newest) turn animates while thinking -- past turns
        # keep a static label.  Computed before the header so the header can
        # show the live elapsed time for the running turn.
        is_active_turn = state.is_thinking and (
            (props.chat_newest_first and display_idx == 0)
            or (not props.chat_newest_first
                and display_idx == visible_count - 1)
        )

        # --- Turn header (always visible) ---
        has_err = has_proc and any(
            p.get("role") == "tool"
            and ('"status": "error"' in (p.get("content") or "")
                 or (p.get("content") or "").startswith("Error"))
            for p in process_msgs
        )
        tic = "CHECKMARK" if conclusion_msg else "USER"
        hr = turn_box.row(align=True)
        hr.label(text="", icon=tic)
        sub = hr.row(align=True)
        sub.scale_x = 0.5
        sub.label(text="Turn {:d}".format(turn_num))
        # Turn duration: the finished turn carries ``turn_seconds`` (stamped by
        # the turn loop on its last message); the active turn shows elapsed so
        # far.  Formatted with minutes (long local turns run into the minutes).
        _turn_seconds = None
        for _m in reversed(turn):
            if isinstance(_m.get("turn_seconds"), (int, float)):
                _turn_seconds = float(_m["turn_seconds"])
                break
        _dur_text = ""
        if is_active_turn and getattr(state, "thinking_start_time", 0.0):
            _dur_text = _fmt_duration(time.time() - state.thinking_start_time)
        elif _turn_seconds is not None:
            _dur_text = _fmt_duration(_turn_seconds)
        if _dur_text:
            sub.label(text="| {:s}".format(_dur_text))
        _copy(hr.operator("bfacw.copy_message", text="", icon="COPYDOWN"),
              user_msg)

        # --- User message (always visible) ---
        # Labelled so every turn clearly shows what the user typed, mirroring
        # the "* Coworker:" label on the reply below.
        urow = turn_box.row()
        urow.label(text="You:", icon='USER')
        _draw_multiline(turn_box, user_msg.get("content", ""))

        # --- Workshop (collapsible: only the internals collapse) ---
        if has_proc:
            ph, pb = turn_box.panel(
                "turn_proc_{:d}".format(turn_num),
                default_closed=True,
            )
            pb_icon = "WARNING" if has_err else "PACKAGE"
            if is_active_turn:
                ws_label = "Workshop {:s}".format(_spinner_char(state))
            else:
                ws_label = "Workshop"
            ph.label(text=ws_label, icon=pb_icon)
            if pb:
                work_box = pb.box()
                for pm in process_msgs:
                    pr = pm.get("role", "")
                    pc = pm.get("content", "")
                    is_sm = _is_system_note_msg(pm)
                    if is_sm:
                        # Agent-to-itself guidance: a compact, titled note --
                        # never a user bubble.
                        _title, _icon = _NOTE_TITLES.get(
                            str(pm.get("system_note") or ""),
                            ("System context", 'INFO'))
                        sb = work_box.box()
                        sb.scale_y = 0.85
                        sb.label(text=_title, icon=_icon)
                        _body = pc if isinstance(pc, str) else ""
                        if _body.startswith("[System:") and _body.endswith("]"):
                            _body = _body[len("[System:"):-1].strip()
                        _draw_multiline(sb, _body)
                    elif pr == "reasoning":
                        _pm_idx, _pm_arch = _idx(pm)
                        _draw_reasoning(
                            work_box, pc, pm.get("label", "Thinking"),
                            is_thinking=False,
                            thinking_dots=0,
                            message_index=_pm_idx,
                            archived=_pm_arch,
                        )
                    elif pr == "tool":
                        tn = pm.get("name", "")
                        ts = pm.get("summary", "")
                        ie = (
                            '"status": "error"' in (pc or "")
                            or (pc or "").startswith("Error")
                        )
                        # Show the full result -- the user asked to see it all,
                        # and the panel scrolls.
                        d = ts if ts else (pc or "")
                        _pm_idx, _pm_arch = _idx(pm)
                        _draw_tool_inline(
                            work_box, tn, d, ie,
                            message_index=_pm_idx,
                            archived=_pm_arch,
                        )
                    elif pr == "compaction":
                        # Timeline marker: context was compressed here.
                        try:
                            _retired_n = int(pm.get("retired", 0) or 0)
                        except (TypeError, ValueError):
                            _retired_n = 0
                        _draw_archive_node(work_box, _retired_n, pc)
                    elif pr == "user":
                        work_box.label(text="Agent Context", icon="INFO")
                        _draw_multiline(work_box, pc)
                    elif pr == "assistant":
                        work_box.label(text="Self Prompt", icon="CONSOLE")
                        _draw_multiline(work_box, pc)

        # --- Coworker reply: live while thinking, then the final message ---
        # While THIS turn is still being generated, show the live readout so
        # the user watches the answer form; only when the turn finishes is it
        # replaced by the final message.  Ordering matters: the active-turn
        # check comes FIRST so a mid-turn "final-looking" message (e.g. one
        # that triggers the end-of-turn execution nudge) cannot show a
        # conclusion while the turn is plainly still running.
        if is_active_turn:
            turn_box.separator()
            if not state.streaming_text and getattr(state, "turn_phase", ""):
                # Pre-first-token: show the activity phase ("Reading your
                # message" / "Dreaming, one moment") in the turn just sent.
                turn_box.label(
                    text="{:s} {:s}".format(_phase_text(state), _spinner_char(state)),
                    icon=_AGENT_ICON)
            else:
                # Live readout lives inside the ACTIVE turn box, visible even
                # while the Workshop is collapsed.
                turn_box.label(
                    text="Coworker (live) {:s}".format(_spinner_char(state)),
                    icon=_AGENT_ICON)
                _draw_multiline(turn_box, state.streaming_text)
        elif conclusion_msg:
            turn_box.separator()
            cr = turn_box.row()
            cr.label(text="* Coworker:", icon=_AGENT_ICON)
            _copy(cr.operator("bfacw.copy_message", text="", icon="COPYDOWN"),
                  conclusion_msg)
            _render_markdown(turn_box, conclusion_msg.get("content", ""))


class BFACW_PT_chat_session(Panel):  # type: ignore[misc]
    """Session panel -- context usage, memory, compaction, and checkpoints."""
    bl_label = "Session"
    bl_idname = "BFACW_PT_chat_session"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = _CHAT_PANEL_CATEGORY
    bl_order = 1

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        # Hidden in External Harness mode: session memory, compaction, and
        # checkpoints are part of the in-Blender chat, which is handled
        # entirely by the external MCP client in that mode.
        if bpy.app.background:
            return False
        prefs = context.preferences.addons[__package__].preferences
        return prefs.operating_mode != "EXTERNAL_HARNESS"

    def draw(self, context: bpy.types.Context) -> None:
        layout = self.layout
        wm = context.window_manager
        props = wm.bfacw_chat_props  # type: ignore[attr-defined]
        state = agent_controller._agent_state
        _draw_session_section(layout, context, props, state)


class BFACW_PT_chat_session_goal(Panel):  # type: ignore[misc]
    """Session > Goal & Plan -- the pinned goal and step plan."""
    bl_label = "Goal & Plan"
    bl_idname = "BFACW_PT_chat_session_goal"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Coworker"
    bl_parent_id = "BFACW_PT_chat_session"

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        return BFACW_PT_chat_session.poll(context)

    def draw_header(self, context: bpy.types.Context) -> None:
        self.layout.label(text="", icon='PINNED')

    def draw(self, context: bpy.types.Context) -> None:
        _draw_goal_plan_section(self.layout)


class BFACW_PT_chat_queue(Panel):  # type: ignore[misc]
    """Top-level queue panel -- shows pending queued messages."""
    bl_label = "Queue"
    bl_idname = "BFACW_PT_chat_queue"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = _CHAT_PANEL_CATEGORY
    bl_order = 2

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        # Hidden in External Harness mode: chat (and thus the message
        # queue) is handled entirely by the external MCP client.
        if bpy.app.background:
            return False
        prefs = context.preferences.addons[__package__].preferences
        return prefs.operating_mode != "EXTERNAL_HARNESS"

    def draw(self, context: bpy.types.Context) -> None:
        layout = self.layout
        state = agent_controller._agent_state
        queue_items = agent_controller._message_queue.get_all()

        if not queue_items:
            layout.label(text="Queue empty", icon='CHECKMARK')
            return

        layout.label(
            text="{:d} message(s) queued".format(len(queue_items)),
            icon='FORWARD',
        )

        for idx, item in enumerate(queue_items):
            msg = item.get("message", "")
            mode = item.get("chat_mode", "AGENT")
            preview = msg[:60] + ("..." if len(msg) > 60 else "")
            box = layout.box()
            hdr = box.row()
            hdr.label(text="[{:d}] {:s}".format(idx + 1, mode), icon='SORTTIME')
            _draw_multiline(box, preview)

        # Clear button.
        row = layout.row(align=True)
        row.scale_y = 1.0
        row.operator("bfacw.queue_clear", icon="TRASH", text="Clear Queue")


class BFACW_PT_chat_status(Panel):  # type: ignore[misc]
    """Status sub-panel -- health dots, model info, tools, advanced diagnostics."""
    bl_label = "Status & Diagnostics"
    bl_idname = "BFACW_PT_chat_status"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = _CHAT_PANEL_CATEGORY
    bl_options = {'DEFAULT_CLOSED'}
    bl_order = 3

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        return not bpy.app.background

    def draw(self, context: bpy.types.Context) -> None:
        layout = self.layout
        wm = context.window_manager
        props = wm.bfacw_chat_props  # type: ignore[attr-defined]
        state = agent_controller._agent_state
        prefs = context.preferences.addons[__package__].preferences
        is_harness = (prefs.operating_mode == "EXTERNAL_HARNESS")

        # -- Liveness dots --
        if not is_harness and state.mcp_server_running:
            agent_controller._check_liveness()
            liveness_row = layout.row(align=True)
            liveness_row.label(
                text="Bridge: {:s}".format("\u25cf" if state.bridge_live else "\u25cb"),
                icon='NETWORK_DRIVE',
            )
            liveness_row.label(
                text="MCP: {:s}".format("\u25cf" if state.mcp_live else "\u25cb"),
                icon='SETTINGS',
            )
            liveness_row.label(
                text="LLM: {:s}".format("\u25cf" if state.llm_live else "\u25cb"),
                icon='CONSOLE',
            )
            layout.separator()

        # -- Restart button --
        if state.mcp_server_running or (is_harness and mcp_to_blender_server.is_running()):
            restart_row = layout.row()
            restart_row.scale_y = 0.8
            restart_row.operator("bfacw.agent_restart", icon="LOOP_BACK", text="Restart Coworker")

        # -- Mode indicator --
        if not is_harness:
            mode_row = layout.row(align=True)
            if prefs.operating_mode == "REMOTE_API":
                mode_row.label(text="Mode: Remote API", icon='URL')
            else:
                mode_row.label(text="Mode: Local LLM", icon='CONSOLE')

        # -- Tool count --
        if not is_harness and state.mcp_server_running:
            if state.tool_count > 0:
                layout.label(text="Tools: {:d} loaded".format(state.tool_count), icon='MODIFIER')
            else:
                layout.label(
                    text="Tools: none loaded",
                    icon='WARNING',
                )

        # -- LLM info --
        if not is_harness:
            llm_state = llm_manager.get_state()
            if llm_state.is_running:
                _draw_multiline(layout, "Model: {:s}".format(llm_state.model_name or "Local LLM"))
            elif llm_state.download_active and llm_state.download_kind == "model":
                prog_row = layout.row()
                prog_row.scale_y = 0.6
                pct = llm_state.download_progress_pct
                if pct > 0:
                    prog_row.progress(factor=pct / 100.0, type='BAR')
                else:
                    prog_row.label(text="Loading model...", icon='SORTTIME')
            llm_cfg = llm_manager.get_config()
            if llm_cfg.mode == "remote" and llm_cfg.remote_model:
                _draw_multiline(layout, "Model: {:s}".format(llm_cfg.remote_model))

            # -- Token usage (issue #69) --
            # Both llama-server and OpenAI-compatible remote APIs report a
            # usage object per request.  Show turn + session totals so the
            # user can see what the chat is consuming.
            _usage = agent_controller._agent_state.session_usage
            if _usage:
                _turn_usage = agent_controller._agent_state.turn_usage
                _turn_tot = _turn_usage.get("total_tokens", 0)
                _sess_p = _usage.get("prompt_tokens", 0)
                _sess_c = _usage.get("completion_tokens", 0)
                _sess_tot = _usage.get("total_tokens", 0)
                _usage_text = "Tokens: {:d}+{:d}={:d}".format(_sess_p, _sess_c, _sess_tot)
                if state.is_thinking and _turn_tot > 0:
                    _usage_text += " (turn: {:d})".format(_turn_tot)
                _draw_multiline(layout, _usage_text)

            # -- Speed (llama-server timings) --
            # Per-request prompt-eval vs generation throughput.  Prompt tok/s
            # collapsing means the prompt is too big; gen tok/s collapsing
            # means the model is over-reasoning.  Only llama-server reports it.
            _timings = agent_controller._agent_state.last_timings
            if _timings:
                _pp = float(_timings.get("prompt_per_second", 0.0) or 0.0)
                _tp = float(_timings.get("predicted_per_second", 0.0) or 0.0)
                if _pp > 0 or _tp > 0:
                    _draw_multiline(
                        layout,
                        "Speed: {:.0f} tok/s prompt, {:.0f} tok/s gen".format(_pp, _tp),
                    )

            # -- Last turn cost (Tier 3i) --
            # Prefill vs generation vs tool/loop overhead for the previous
            # turn, so a slow turn can be attributed at a glance.
            _cost = agent_controller._agent_state.last_turn_cost
            if _cost and _cost.get("requests"):
                _wall = float(_cost.get("wall", 0.0) or 0.0)
                _p_s = float(_cost.get("prompt_ms", 0.0)) / 1000.0
                _g_s = float(_cost.get("predicted_ms", 0.0)) / 1000.0
                _draw_multiline(
                    layout,
                    "Last turn: {:s} wall | gen {:d} tok/{:s}, "
                    "prefill {:d} tok/{:s} | {:d} tools".format(
                        _fmt_duration(_wall),
                        int(_cost.get("predicted_n", 0)), _fmt_duration(_g_s),
                        int(_cost.get("prompt_n", 0)), _fmt_duration(_p_s),
                        int(_cost.get("tools", 0)),
                    ),
                )

        # -- Export/Copy Log (advanced) --
        if not is_harness:
            layout.separator()
            row = layout.row(align=True)
            row.scale_y = 1.2
            row.operator("bfacw.export_session_log", icon="EXPORT", text="Export Log")
            row.operator("bfacw.copy_session_log", icon="COPYDOWN", text="Copy Log")

        # -- External Harness MCP server controls --
        if is_harness and mcp_to_blender_server.is_running():
            box = layout.box()
            box.label(text="MCP Server Mode:", icon='SETTINGS')
            box.prop(prefs, "mcp_server_mode", expand=True)

            if prefs.mcp_server_mode == "NETWORK":
                box.prop(prefs, "mcp_server_host")
                row = box.row(align=True)
                row.prop(prefs, "mcp_server_port_override")
                if prefs.mcp_server_host not in ("127.0.0.1", "localhost", "::1"):
                    box.label(
                        text="\u26a0 Non-localhost exposes MCP to network!",
                        icon='ERROR',
                    )
                row = box.row(align=True)
                if agent_controller._agent_state.mcp_server_running:
                    row.operator("bfacw.mcp_server_stop", icon="CANCEL", text="Stop MCP Server")
                else:
                    row.operator("bfacw.mcp_server_start", icon="PLAY", text="Start MCP Server")


class BFACW_PT_chat_text_editor(Panel):  # type: ignore[misc]
    """Chat panel in the Text Editor sidebar."""
    bl_label = "Coworker Chat"
    bl_idname = "BFACW_PT_chat_text_editor"
    bl_space_type = 'TEXT_EDITOR'
    bl_region_type = 'UI'
    bl_category = _CHAT_PANEL_CATEGORY
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        return not bpy.app.background

    def draw(self, context: bpy.types.Context) -> None:
        layout = self.layout
        wm = context.window_manager
        props = wm.bfacw_chat_props  # type: ignore[attr-defined]
        state = agent_controller._agent_state
        prefs = context.preferences.addons[__package__].preferences
        is_harness = (prefs.operating_mode == "EXTERNAL_HARNESS")

        # Status bar.
        row = layout.row(align=True)
        if is_harness:
            if mcp_to_blender_server.is_running():
                row.operator("bfacw.agent_stop", icon="X", text="Stop Bridge")
                row.label(text="Bridge Running", icon='CHECKMARK')
            else:
                row.operator("bfacw.agent_start", icon="PLAY", text="Start Bridge")
                row.label(text="Bridge Stopped", icon='X')
        else:
            if state.mcp_server_running:
                row.operator("bfacw.agent_stop", icon="X", text="Stop")
                row.label(text="Running", icon='CHECKMARK')
            else:
                row.operator("bfacw.agent_start", icon="PLAY", text="Start")
                row.label(text="Stopped", icon='X')

        layout.separator()

        if not is_harness:
            # Agent/Ask mode toggle (Tier 1).
            row = layout.row(align=True)
            row.prop(props, "chat_mode", expand=True)
            _draw_reasoning_effort_row(layout, prefs)

            # Project Rules button (Tier 2).
            row = layout.row(align=True)
            row.operator("bfacw.edit_rules", icon="TEXT", text="Edit Rules")

            layout.separator()

            # -- Image attachment socket (issue #88 / Tier 3k) ------
            # Above the chat input (same order as the Viewport panel).
            _draw_attachment_row(layout, props, context)

            # Input (multi-line textbox).
            layout.textbox(props, "chat_input")

            row = layout.row(align=True)
            row.scale_y = 1.5
            if state.is_thinking:
                row.operator("bfacw.chat_stop", icon="PAUSE", text="Stop")
            else:
                row.operator("bfacw.chat_send", icon="PLAY", text="Send")
            row.operator("bfacw.chat_clear", icon="X", text="New Thread")
        else:
            layout.label(text="Chat handled by external MCP client.", icon='INFO')

        layout.separator()

        # Conversation summary -- latest message first.
        history = state.conversation_history
        # Prefix retired turns so the count reflects the WHOLE conversation;
        # the last-10 preview still draws from the tail.  Mirrors the main
        # panel's "Archived context" so nothing appears to vanish.
        try:
            from . import session_memory as _sm
            _combined = list(_sm.store.retired_history) + list(history)
        except Exception:  # pylint: disable=broad-exception-caught
            _combined = list(history)
        display_history = [m for m in _combined if m.get("role") != "system"]
        if display_history:
            box = layout.box()
            box.label(text="History ({:d} messages)".format(len(display_history)), icon='TEXT')
            for msg in reversed(display_history[-10:]):
                role = msg.get("role", "")
                content = msg.get("content", "")
                summary = msg.get("summary", "")
                if role == "reasoning":
                    preview = "Thinking... ({:d} chars)".format(len(content or ""))
                elif role == "tool":
                    display = summary if summary else (content or "")
                    preview = display[:80] + "..." if display and len(display) > 80 else (display or "")
                elif role == "compaction":
                    try:
                        _rn = int(msg.get("retired", 0) or 0)
                    except (TypeError, ValueError):
                        _rn = 0
                    preview = "checkpoint: {:d} message(s) summarized".format(_rn)
                else:
                    preview = content if content else ""
                _draw_multiline(box, "[{:s}] {:s}".format(role, preview))
        else:
            layout.label(text="No conversation yet.", icon='INFO')


# ---------------------------------------------------------------------------
# Helpers


def _draw_attachment_row(layout, props, context) -> None:
    """Draw the chat image socket + capture buttons (issue #88 / Tier 3k).

    Shared by the 3D Viewport chat panel and the Text Editor panel:
    one Blender-standard socket (``template_ID``: browse / open / new)
    fed by every attach source -- the socket's own file browser, render
    capture, screen capture, and drag-and-drop -- plus the Send Once
    toggle and a thumbnail preview of what is attached.  The socket is
    encoded on the main thread at send time (``_capture_chat_attachment``),
    never during drawing.

    The section collapses from its own header with the same
    ``DOWNARROW_HLT`` / ``TRIA_RIGHT`` arrow pair Blender uses for its
    sub-panels, so the image can be tucked away while you read the chat.
    Collapsed, the header still names the attached datablock, so what will
    be sent stays visible.
    """
    expanded = getattr(props, "chat_image_expanded", True)
    img = props.chat_image
    box = layout.box()
    head = box.row(align=True)
    head.prop(props, "chat_image_expanded",
              icon='DOWNARROW_HLT' if expanded else 'TRIA_RIGHT',
              text="Image")
    if img is not None and not expanded:
        head.label(text=img.name, icon='IMAGE_DATA')
    if not expanded:
        return
    head.prop(props, "chat_image_send_once", text="Send once")
    box.template_ID(props, "chat_image", open="image.open", new="image.new")
    _draw_attachment_preview(box, img, context)
    row = box.row(align=True)
    row.operator("bfacw.chat_capture_render", icon='RENDER_STILL', text="Render")
    row.operator("bfacw.chat_capture_screen", icon='FULLSCREEN_ENTER', text="Screen")


def _ensure_attachment_preview(img) -> None:
    """Start generating *img*'s preview so the thumbnail can be drawn.

    ``preview_ensure()`` only *creates* the preview; its pixels are
    filled by Blender's preview thread a moment later, which is why
    this is called when the image is ATTACHED rather than only from the
    panel draw -- by the first redraw the thumbnail is usually ready.
    Best-effort: an image without pixels must not break attaching, and
    in ``--background`` mode no preview icon is produced at all (the
    draw helper simply draws nothing).
    """
    if img is None:
        return
    try:
        img.preview_ensure()
    except Exception:  # noqa: BLE001  # pylint: disable=broad-exception-caught
        return


# Our own preview buffer for the attachment thumbnail.  Module-level
# because a preview collection has to outlive a single panel draw; the
# pixels are only recomposed when the target size (or image) changes.
#
# A private bpy.utils.previews collection is used rather than the
# datablock's own preview: Blender regenerates an image's preview from
# the file (128 x 128) and would throw our composite away, and writing to
# the datablock also perturbs every other UI that shows its icon.
_ATTACHMENT_THUMB = {"collection": None, "preview": None, "key": None,
                     "icon": 0}


def _thumbnail_collection():
    """Lazily create the private ``bpy.utils.previews`` collection."""
    if _ATTACHMENT_THUMB["collection"] is None:
        try:
            from bpy.utils import previews as _previews
            _ATTACHMENT_THUMB["collection"] = _previews.new()
        except Exception:  # noqa: BLE001  # pylint: disable=broad-exception-caught
            _ATTACHMENT_THUMB["collection"] = None
    return _ATTACHMENT_THUMB["collection"]


def free_attachment_thumbnail() -> None:
    """Release the private preview buffer (called from unregister)."""
    state = _ATTACHMENT_THUMB
    collection = state["collection"]
    state.update({"collection": None, "preview": None, "key": None, "icon": 0})
    if collection is None:
        return
    try:
        from bpy.utils import previews as _previews
        _previews.remove(collection)
    except Exception:  # noqa: BLE001  # pylint: disable=broad-exception-caught
        pass


def _attachment_preview_px(img, context) -> tuple[int, int]:
    """Edge length of the square thumbnail buffer, as ``(side, side)``.

    Sized from the panel's live width so the thumbnail spans it (see the
    sizing note near ``_ATTACHMENT_PREVIEW_MARGIN_PX`` for why the buffer
    is square rather than the image's own shape).  Falls back to
    ``(0, 0)`` when the image has no usable pixel size.
    """
    try:
        src_w, src_h = int(img.size[0]), int(img.size[1])
    except (AttributeError, IndexError, TypeError, ValueError):
        return 0, 0
    if src_w <= 0 or src_h <= 0:
        return 0, 0
    try:
        region_width = int(getattr(context.region, "width", 0) or 0)
    except (AttributeError, TypeError, ValueError):
        region_width = 0
    available = max(region_width - _ATTACHMENT_PREVIEW_MARGIN_PX,
                    _ATTACHMENT_PREVIEW_MIN_PX)
    side = min(available, _ATTACHMENT_PREVIEW_MAX_PX)
    quantum = _ATTACHMENT_PREVIEW_QUANTUM_PX
    if quantum > 1:
        side = max(_ATTACHMENT_PREVIEW_MIN_PX,
                   round(float(side) / quantum) * quantum)
    return side, side


def _attachment_fit_size(src_w, src_h, side) -> tuple[int, int]:
    """Pixel size that FITS an image into a ``side`` square (aspect kept)."""
    if src_w >= src_h:
        return side, max(1, round(side * src_h / src_w))
    return max(1, round(side * src_w / src_h)), side


def _attachment_cover_size(src_w, src_h, side) -> tuple[int, int]:
    """Pixel size that COVERS a ``side`` square (the other axis overflows)."""
    if src_w >= src_h:
        return max(side, round(side * src_w / src_h)), side
    return side, max(side, round(side * src_h / src_w))


def _attachment_source_floats(img):
    """``(floats, width, height)`` for *img*'s pixels, or None.

    Read through ``foreach_get`` into a byte-packed ``array('f')`` so a
    large image does not become millions of Python float objects.
    Images above ``_ATTACHMENT_THUMBNAIL_MAX_SOURCE_PX`` are refused: the
    copy needs two float buffers, and a thumbnail is not worth hundreds
    of megabytes (those fall back to Blender's own small preview icon).
    """
    try:
        src_w, src_h = int(img.size[0]), int(img.size[1])
    except (AttributeError, IndexError, TypeError, ValueError):
        return None
    if src_w <= 0 or src_h <= 0:
        return None
    if src_w * src_h > _ATTACHMENT_THUMBNAIL_MAX_SOURCE_PX:
        return None
    try:
        buf = array.array("f", [0.0]) * (src_w * src_h * 4)
        img.pixels.foreach_get(buf)
    except Exception:  # noqa: BLE001  # pylint: disable=broad-exception-caught
        return None
    return buf, src_w, src_h


def _attachment_scale_pixels(img, width, height, *, blur=False, dim=None):
    """*img* resampled to ``width`` x ``height``; its RGBA floats or None.

    The source pixels are copied INTO a scratch image and resampled
    there.  The copy is explicit -- ``Image.copy()`` comes back all black
    for a generated image, because the copy is regenerated rather than
    duplicated -- and the user's datablock is never modified.  ``blur``
    softens the result with a down/up resample round trip and ``dim``
    multiplies the colour channels and forces the copy opaque; together
    they make the muted backdrop of ``_attachment_thumbnail_pixels``.
    Returns ``None``, never raising (this runs from a panel draw), when
    the pixels cannot be read.
    """
    source = _attachment_source_floats(img)
    if source is None:
        return None
    buf, src_w, src_h = source
    scratch = None
    try:
        scratch = bpy.data.images.new("bfacw_thumbnail_scratch", src_w, src_h,
                                      alpha=True)
        scratch.pixels.foreach_set(buf)
        del buf
        scratch.scale(width, height)
        if blur and width > 8 and height > 8:
            scratch.scale(max(1, width // 8), max(1, height // 8))
            scratch.scale(width, height)
        pixels = list(scratch.pixels)
    except Exception:  # noqa: BLE001  # pylint: disable=broad-exception-caught
        return None
    finally:
        if scratch is not None:
            try:
                bpy.data.images.remove(scratch)
            except Exception:  # noqa: BLE001
                pass
    needed = width * height * 4
    if len(pixels) < needed:
        return None
    pixels = pixels[:needed]
    if dim is not None:
        for channel in (0, 1, 2):
            pixels[channel::4] = [value * dim for value in pixels[channel::4]]
        pixels[3::4] = [1.0] * (width * height)
    return pixels


def _attachment_thumbnail_pixels(img, side):
    """A ``side`` x ``side`` RGBA thumbnail of *img*, or None if unreadable.

    The whole image is fitted into the middle of the square, over a
    dimmed, blurred, cover-scaled copy of itself.  Blender draws a preview
    icon into a square rect, so a wide image fitted on its own would leave
    empty bands above and below it; filling them with a muted version of
    the same image keeps the thumbnail panel-wide and borderless while
    still showing the whole image.
    """
    try:
        src_w, src_h = int(img.size[0]), int(img.size[1])
    except (AttributeError, IndexError, TypeError, ValueError):
        return None
    if src_w <= 0 or src_h <= 0 or side <= 0:
        return None
    cover_w, cover_h = _attachment_cover_size(src_w, src_h, side)
    fit_w, fit_h = _attachment_fit_size(src_w, src_h, side)
    backdrop = _attachment_scale_pixels(
        img, cover_w, cover_h, blur=True, dim=_ATTACHMENT_THUMBNAIL_DIM)
    front = _attachment_scale_pixels(img, fit_w, fit_h)
    if backdrop is None or front is None:
        return None
    x_off = (cover_w - side) // 2
    y_off = (cover_h - side) // 2
    fit_y0 = (side - fit_h) // 2
    fit_x0 = (side - fit_w) // 2
    row_floats = side * 4
    out = []
    for y in range(side):
        start = ((y + y_off) * cover_w + x_off) * 4
        row = backdrop[start:start + row_floats]
        if fit_y0 <= y < fit_y0 + fit_h:
            fy = y - fit_y0
            row = list(row)
            row[fit_x0 * 4:(fit_x0 + fit_w) * 4] = \
                front[fy * fit_w * 4:(fy + 1) * fit_w * 4]
        out.extend(row)
    return out


def _attachment_datablock_icon(img) -> int:
    """The image datablock's own preview icon (small, but always valid)."""
    try:
        preview = img.preview_ensure()
        return int(getattr(preview, "icon_id", 0) or 0)
    except Exception:  # noqa: BLE001  # pylint: disable=broad-exception-caught
        return 0


def _attachment_preview_icon(img, context) -> tuple[int, int, int]:
    """``(icon_value, width, height)`` for the attachment thumbnail.

    Prefers a buffer we compose ourselves -- the image at full width over
    a muted backdrop, so the thumbnail is panel-wide and has no empty band
    above or below it -- because the datablock's stock icon is only 32px
    and looks very pixely once the thumbnail spans the panel.  Falls back
    to that stock icon, which Blender then scales, when the buffer cannot
    be built, and to ``(0, 0, 0)`` when there is nothing to draw.
    """
    width, height = _attachment_preview_px(img, context)
    if width <= 0 or height <= 0:
        return 0, 0, 0

    preview_state = _ATTACHMENT_THUMB
    key = None
    try:
        key = (img.name, width, height, tuple(img.size))
    except Exception:  # noqa: BLE001  # pylint: disable=broad-exception-caught
        key = None
    if key is not None and preview_state["key"] == key and preview_state["icon"]:
        return preview_state["icon"], width, height

    collection = _thumbnail_collection()
    if collection is not None:
        try:
            preview = preview_state["preview"]
            if preview is None:
                preview = collection.new("attachment")
                preview_state["preview"] = preview
            pixels = _attachment_thumbnail_pixels(img, width)
            if pixels is not None:
                # Size first: clearing a buffer is what lets the dynamic
                # pixel array take our new dimensions.  Both buffers get
                # the same pixels because template_icon asks for
                # ICON_SIZE_ICON and only falls back to the big preview
                # when the small buffer is missing (interface_icons.cc
                # icon_draw_size) -- filling both means the thumbnail is
                # sharp whichever buffer Blender reaches for.
                preview.image_size = (width, height)
                preview.image_pixels_float[:] = pixels
                preview.icon_size = (width, height)
                preview.icon_pixels_float[:] = pixels
                icon = int(getattr(preview, "icon_id", 0) or 0)
                if icon:
                    preview_state.update({"key": key, "icon": icon})
                    return icon, width, height
        except Exception:  # noqa: BLE001  # pylint: disable=broad-exception-caught
            pass

    return _attachment_datablock_icon(img), width, height


def _draw_attachment_preview(layout, img, context=None) -> None:
    """Draw a thumbnail of the attached image (issue #88 / Tier 3k).

    A filename alone is easy to misread, so the user can see WHAT is
    attached before sending.  The button is sized from the panel's live
    width so the thumbnail spans it (see the sizing note near
    ``_ATTACHMENT_PREVIEW_MARGIN_PX``), and the pixels are our own
    resample of the image at exactly the size Blender draws them --
    without that, a template_icon shows the datablock's 32px icon and the
    thumbnail is very pixely.  Best-effort: a datablock with no pixels
    (or a stub image in tests) has no preview to make, and a panel draw
    must never raise.
    """
    if img is None:
        return
    icon, width, height = _attachment_preview_icon(img, context)
    if not icon or width <= 0 or height <= 0:
        return
    try:
        ui_scale = float(context.preferences.system.ui_scale) or 1.0
    except (AttributeError, TypeError, ValueError):
        ui_scale = 1.0
    unit = _UI_UNIT_BASE_PX * ui_scale
    # Drawn straight onto the panel layout, not inside a layout.row():
    # a row wrapper made the (deliberately large) icon button vanish --
    # measured in a real session, where the same call without the row
    # drew a 162x40 thumbnail and with it drew nothing at all.
    layout.template_icon(icon_value=icon, scale=max(1.0, width / unit))


def _redraw_areas(context: bpy.types.Context | None) -> None:
    """Force redraw of all panels."""
    if context and context.area:
        context.area.tag_redraw()


def _redraw_areas_safe() -> None:
    """Defer a panel redraw to the main thread via a timer.

    Safe to call from background threads where the operator's
    ``context`` may have been invalidated after ``execute()``
    returned.  Uses ``bpy.app.timers`` to access ``bpy.context``
    on the main thread where it is always valid.
    """
    bpy.app.timers.register(
        lambda: _redraw_areas(bpy.context),
        first_interval=0.0,
    )


# ---------------------------------------------------------------------------
# Session memory & checkpoint operators (Tier 3 Phase 5)

def _draw_session_section(layout, context, props, state) -> None:
    """Draw the Session panel: context usage, memory preview, checkpoints.

    Shown in the dedicated ``BFACW_PT_chat_session`` panel, separate from the
    chat panel (which holds the message history, input and controls).
    """
    from . import session_memory as _sm
    st = _sm.store

    # -- Context usage ----------------------------------------------
    # Use the LATEST request's prompt tokens (current occupancy), not the
    # cumulative session sum -- a running sum only grows, so the bar could
    # never fall after compaction and would pin at 100%.
    ctx_size = getattr(state, "ctx_size_used", 0) or 0
    last_prompt = getattr(state, "last_prompt_tokens", 0) or 0
    ctx_box = layout.box()
    ctx_box.label(text="Context Window", icon='MEMORY')
    if ctx_size > 0 and last_prompt > 0:
        pct = min(int(last_prompt * 100 / ctx_size), 100)
        row = ctx_box.row(align=True)
        row.label(text="{:d}% used".format(pct))
        row.progress(factor=pct / 100.0, type='BAR')
        if pct >= int(_sm.COMPACTION_TRIGGER_RATIO * 100):
            ctx_box.label(
                text="Near the limit -- older turns will be summarized (checkpoint)",
                icon='INFO')
    else:
        ctx_box.label(text="No usage recorded yet", icon='INFO')

    # -- Memory note (compaction summary) ---------------------------
    mem_box = layout.box()
    mem_box.label(text="Memory", icon='BOOKMARKS')
    if st.memory_block:
        mem_lines = st.memory_block.splitlines()
        _draw_multiline(mem_box, mem_lines[0] if mem_lines else "")
    else:
        mem_box.label(text="Nothing remembered yet", icon='INFO')
    # Bound multiline editor: the textbox shows and edits the memory block;
    # the Apply button writes it back (an empty box reloads the current one).
    # Initialise the editor from the store while it is untouched so the user
    # can see what is remembered.
    if not props.session_memory_edit and st.memory_block:
        props.session_memory_edit = st.memory_block
    mem_box.textbox(props, "session_memory_edit",
                    placeholder="Session memory (empty = reload current)")
    row = mem_box.row(align=True)
    row.operator("bfacw.session_memory_view_edit", icon='TEXT', text="Apply Memory")
    row.operator("bfacw.session_compact_now", icon='BOOKMARKS', text="Checkpoint Now")

    # -- Checkpoints (Restore / Branch) -----------------------------
    cp_box = layout.box()
    checkpoints = st.list_checkpoints()
    row = cp_box.row(align=True)
    row.prop(props, "session_show_checkpoints",
             icon='TRIA_DOWN' if props.session_show_checkpoints else 'TRIA_RIGHT',
             text="Checkpoints ({:d})".format(len(checkpoints)))
    if props.session_show_checkpoints and checkpoints:
        # Default the selection to the newest checkpoint.
        if props.session_checkpoint_index >= len(checkpoints):
            props.session_checkpoint_index = len(checkpoints) - 1
        # Make clear what the radio index selects -- without this the number
        # reads as an unexplained "0".
        _sel = props.session_checkpoint_index
        if 0 <= _sel < len(checkpoints):
            cp_box.label(text="Restore target: #{:d}  {:s}".format(
                _sel, checkpoints[_sel].get("reason", "?")))
        for i in range(len(checkpoints) - 1, -1, -1):
            cp = checkpoints[i]
            ts = cp.get("timestamp", "?")
            # The timestamp is a full date+time; only the time-of-day is
            # shown in the compact list (the full stamp is too long and
            # pushed the reason out of the row).
            ts_short = ts.split(" ", 1)[1] if " " in ts else ts
            # Primary row: a radio button (not a bare index number) so it is
            # obvious at a glance which checkpoint is the restore target.
            row = cp_box.row(align=True)
            row.operator(
                "bfacw.session_checkpoint_select",
                text="",
                icon='RADIOBUT_ON' if i == _sel else 'RADIOBUT_OFF',
                emboss=False,
            ).index = i
            row.label(text="#{:d}  {:s}".format(i, cp.get("reason", "?")))
            # Detail row: smaller, indented timestamp + message count.
            detail = cp_box.row()
            detail.scale_y = 0.8
            detail.label(text="       {:s}, {:d} messages".format(
                ts_short, cp.get("message_count", 0)))
        row = cp_box.row(align=True)
        row.operator("bfacw.session_checkpoint_restore", icon='LOOP_BACK', text="Restore")


class BFACW_OT_session_checkpoint_restore(Operator):  # type: ignore[misc]
    """Restore the selected checkpoint (the current session is auto-saved first)"""
    bl_idname = "bfacw.session_checkpoint_restore"
    bl_label = "Restore"
    bl_description = (
        "Restore this checkpoint. The current conversation is saved as a "
        "checkpoint first, so nothing is lost."
    )

    def execute(self, context: bpy.types.Context) -> set[str]:
        from . import session_memory as _sm
        st = _sm.store
        index = context.window_manager.bfacw_chat_props.session_checkpoint_index  # type: ignore[attr-defined]
        try:
            with _sm.store_lock:
                restored = st.restore(
                    index,
                    agent_controller._agent_state.conversation_history,
                )
                agent_controller._agent_state.conversation_history[:] = restored
        except IndexError:
            self.report({"WARNING"}, "Checkpoint no longer exists")
            return {"CANCELLED"}
        _save_chat_history()
        self.report({"INFO"}, "Checkpoint restored (current session was saved as 'pre-restore')")
        return {"FINISHED"}


class BFACW_OT_session_checkpoint_select(Operator):  # type: ignore[misc]
    """Choose a checkpoint as the restore target (radio button)"""
    bl_idname = "bfacw.session_checkpoint_select"
    bl_label = "Select Checkpoint"
    bl_description = "Choose this checkpoint as the restore target"

    index: IntProperty(default=0, min=0)  # type: ignore[valid-type]

    def execute(self, context: bpy.types.Context) -> set[str]:
        context.window_manager.bfacw_chat_props.session_checkpoint_index = self.index  # type: ignore[attr-defined]
        return {"FINISHED"}


class BFACW_OT_session_memory_view_edit(Operator):  # type: ignore[misc]
    """View or edit the session memory block"""
    bl_idname = "bfacw.session_memory_view_edit"
    bl_label = "Apply Memory"
    bl_description = ("Write the edited session memory block to the store, or "
                      "reload it into the editor when the box is left empty")

    def execute(self, context: bpy.types.Context) -> set[str]:
        from . import session_memory as _sm
        props = context.window_manager.bfacw_chat_props  # type: ignore[attr-defined]
        text = props.session_memory_edit
        with _sm.store_lock:
            if text.strip():
                _sm.store.memory_block = text.strip()
                message = "Session memory updated"
            else:
                props.session_memory_edit = _sm.store.memory_block
                message = "Loaded current session memory into the editor"
        _save_chat_history()
        self.report({"INFO"}, message)
        return {"FINISHED"}


# Manual "Compact Now" keeps a larger recent window than the forced-overflow
# path but smaller than the automatic window, and always snapshots the
# pre-compaction state so the action is reversible.
_MANUAL_COMPACT_KEEP_RECENT = 8


class BFACW_OT_session_compact_now(Operator):  # type: ignore[misc]
    """Checkpoint now: save the session, then summarize older turns into memory"""
    bl_idname = "bfacw.session_compact_now"
    bl_label = "Checkpoint Now"
    bl_description = (
        "Save a checkpoint of the whole session, then summarize the oldest "
        "turns into the session memory to free context. The goal and plan "
        "are pinned and never summarized away. Restore the checkpoint from "
        "the list below at any time."
    )

    def execute(self, context: bpy.types.Context) -> set[str]:
        from . import session_memory as _sm
        st = _sm.store
        history = agent_controller._agent_state.conversation_history
        # Only compact when there is genuinely something safe to retire: never
        # let a manual compaction reduce a young conversation to the system
        # prompt (find_retire_boundary returns len(history) when nothing is
        # retirable).
        boundary = _sm.find_retire_boundary(history, _MANUAL_COMPACT_KEEP_RECENT)
        if boundary >= len(history):
            self.report({"INFO"}, "Nothing to compact yet")
            return {"CANCELLED"}
        st.archive_path = _session_memory_archive_path()
        with _sm.store_lock:
            # Snapshot the PRE-compaction state so "Compact Now" is reversible.
            st.snapshot(history, reason="manual-compaction")
            kept, memory_block, retired = _sm.compact_history(
                history, prior_memory=st.memory_block,
                keep_recent=_MANUAL_COMPACT_KEEP_RECENT)
            st.memory_block = memory_block
            st.append_archive(retired)
            st.append_retired(retired)
            history[:] = kept
        _save_chat_history()
        self.report({"INFO"}, "Checkpoint saved -- {:d} older messages summarized".format(len(retired)))
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# Goal & plan (Tier 3 hardening)

def _draw_reasoning_effort_row(layout, prefs) -> None:
    """Compact Reasoning Effort selector for the chat panels (local mode).

    The thinking budget is sent with every request, so it is safe to change
    at any time -- including mid-session; it applies from the next message.
    Remote providers ignore it, so the row is shown for the local model only.
    """
    if getattr(prefs, "operating_mode", "") != "LOCAL_LLM":
        return
    if not hasattr(prefs, "reasoning_effort"):
        return
    row = layout.row(align=True)
    row.scale_y = 0.9
    row.label(text="Thinking", icon='SOLO_ON')
    row.prop(prefs, "reasoning_effort", text="")


def _draw_goal_plan_section(layout) -> None:
    """Draw the pinned goal and plan (Session > Goal & Plan sub-panel)."""
    from . import session_memory as _sm
    with _sm.store_lock:
        g = _sm.store.goal
        session_goal = g.session_goal
        turn_goal = g.turn_goal
        steps = [dict(s) for s in g.steps]
        notes = g.user_notes
        done, total = g.progress()
    if not (session_goal or turn_goal or steps or notes):
        _draw_multiline(
            layout,
            "Set automatically from your first request. It is pinned to every "
            "request, so it survives checkpoints and long turns.",
            icon='INFO')
    else:
        box = layout.box()
        box.label(text="Session goal", icon='PINNED')
        _draw_multiline(box, session_goal or "(none)")
        if turn_goal and turn_goal != session_goal:
            box.label(text="Current request", icon='USER')
            _draw_multiline(box, turn_goal)
        if steps:
            pbox = layout.box()
            row = pbox.row()
            row.label(text="Plan  {:d}/{:d} done".format(done, total), icon='PRESET')
            if total:
                row.progress(factor=done / float(total), type='BAR', text="")
            _icons = {"done": 'CHECKBOX_HLT', "doing": 'PLAY', "todo": 'CHECKBOX_DEHLT'}
            for i, s in enumerate(steps, 1):
                srow = pbox.row()
                srow.label(text="", icon=_icons.get(s.get("status"), 'CHECKBOX_DEHLT'))
                _draw_multiline(srow, "{:d}. {:s}".format(i, s.get("text", "")))
        if notes:
            nbox = layout.box()
            nbox.label(text="Your notes", icon='TEXT')
            _draw_multiline(nbox, notes)
    row = layout.row(align=True)
    row.operator("bfacw.plan_open", icon='TEXT', text="Edit in Text Editor")
    row.operator("bfacw.plan_clear", icon='X', text="Clear Plan")


# Last text written to (or adopted from) the plan text block.  A difference
# means the USER edited it -> parse back into the store.  ``None`` until the
# block is first seen this session.
_plan_last_written: str | None = None


def _sync_plan_text() -> None:
    """Keep the ``Coworker Plan.md`` text block and the pinned plan in sync.

    Main thread only (called from the chat timer).  The block exists only
    after the user opened it once ("Edit in Text Editor"), so the add-on
    never adds a datablock to the user's file uninvited.  User edits win:
    when the text differs from what we last wrote, it is parsed into the
    store; otherwise store changes (new goal, plan progress) are written out.
    """
    global _plan_last_written
    from . import session_memory as _sm
    name = _sm.goal_plan.PLAN_TEXT_NAME
    text = bpy.data.texts.get(name)
    if text is None:
        _plan_last_written = None
        return
    current = text.as_string()
    with _sm.store_lock:
        if _plan_last_written is not None and current != _plan_last_written:
            # The user edited the file: adopt it.
            if _sm.store.goal.parse_markdown(current):
                print("[Coworker] plan: applied your edits from '{:s}'".format(name))
            _plan_last_written = current
            return
        rendered = _sm.store.goal.render_markdown()
    if rendered != current:
        text.clear()
        text.write(rendered)
        current = rendered
    _plan_last_written = current


class BFACW_OT_plan_open(Operator):  # type: ignore[misc]
    """Open the goal & plan in the Text Editor to read or edit it"""
    bl_idname = "bfacw.plan_open"
    bl_label = "Edit Plan"
    bl_description = (
        "Open the pinned goal & plan as an editable text ('Coworker Plan.md'). "
        "Edit the goal, steps ([ ] / [>] / [x]) or notes -- your changes are "
        "sent to the Coworker with its next request"
    )

    def execute(self, context: bpy.types.Context) -> set[str]:
        global _plan_last_written
        from . import session_memory as _sm
        name = _sm.goal_plan.PLAN_TEXT_NAME
        text = bpy.data.texts.get(name) or bpy.data.texts.new(name)
        with _sm.store_lock:
            rendered = _sm.store.goal.render_markdown()
        text.clear()
        text.write(rendered)
        _plan_last_written = rendered
        # Show it: reuse an open Text Editor, else open one in a new window.
        for win in context.window_manager.windows:
            for area in win.screen.areas:
                if area.type == 'TEXT_EDITOR':
                    area.spaces.active.text = text
                    area.tag_redraw()
                    self.report({"INFO"}, "Plan opened in the Text Editor")
                    return {"FINISHED"}
        try:
            bpy.ops.wm.window_new()
            new_win = context.window_manager.windows[-1]
            area = new_win.screen.areas[0]
            area.ui_type = 'TEXT_EDITOR'
            area.spaces.active.text = text
            self.report({"INFO"}, "Plan opened in a new Text Editor window")
        except Exception:  # pylint: disable=broad-exception-caught
            self.report({"INFO"}, "Plan saved as text '{:s}' -- open it in any "
                                  "Text Editor".format(name))
        return {"FINISHED"}


class BFACW_OT_plan_clear(Operator):  # type: ignore[misc]
    """Clear the step plan (the goals are kept)"""
    bl_idname = "bfacw.plan_clear"
    bl_label = "Clear Plan"
    bl_description = (
        "Remove the current step plan and your notes. The session goal and "
        "current request stay pinned"
    )

    def execute(self, context: bpy.types.Context) -> set[str]:
        from . import session_memory as _sm
        with _sm.store_lock:
            g = _sm.store.goal
            g.steps = []
            g.user_notes = ""
            g.revision += 1
        _save_chat_history()
        self.report({"INFO"}, "Plan cleared")
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# Registration helpers

_classes = (
    ChatHistoryProperties,
    BFACW_OT_session_checkpoint_select,
    BFACW_OT_session_checkpoint_restore,
    BFACW_OT_session_memory_view_edit,
    BFACW_OT_session_compact_now,
    BFACW_OT_plan_open,
    BFACW_OT_plan_clear,
    BFACW_OT_chat_send,
    BFACW_OT_chat_clear,
    BFACW_OT_chat_stop,
    BFACW_OT_chat_queue_send,
    BFACW_OT_chat_capture_render,
    BFACW_OT_chat_capture_screen,
    BFACW_OT_chat_image_drop,
    BFACW_FH_chat_drop,
    BFACW_OT_export_session_log,
    BFACW_OT_copy_session_log,
    BFACW_OT_copy_status_error,
    BFACW_OT_queue_clear,
    BFACW_OT_queue_show,
    BFACW_OT_mention_search,
    BFACW_OT_mention_insert,
    BFACW_OT_edit_rules,
    BFACW_OT_reload_rules,
    BFACW_OT_agent_start,
    BFACW_OT_agent_stop,
    BFACW_OT_agent_restart,
    BFACW_OT_copy_message,
    BFACW_OT_copy_code_block,

    BFACW_PT_chat_queue,
    BFACW_PT_chat_panel,
    BFACW_PT_chat_session,
    BFACW_PT_chat_session_goal,
    BFACW_PT_chat_status,
    BFACW_PT_chat_text_editor,
)


def register() -> None:
    # Idempotent registration -- unregister old classes first if re-enabling.
    if hasattr(bpy.types.WindowManager, "bfacw_chat_props"):
        unregister()

    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.WindowManager.bfacw_chat_props = bpy.props.PointerProperty(type=ChatHistoryProperties)  # type: ignore[attr-defined]

    # Register the chat UI update timer.
    if not bpy.app.background:
        bpy.app.timers.register(chat_timer_update, first_interval=1.0, persistent=True)


def unregister() -> None:
    # Save history.
    _save_chat_history()

    # Drop the thumbnail's private preview buffer.
    free_attachment_thumbnail()

    if bpy.app.timers.is_registered(chat_timer_update):
        bpy.app.timers.unregister(chat_timer_update)

    if hasattr(bpy.types.WindowManager, "bfacw_chat_props"):
        del bpy.types.WindowManager.bfacw_chat_props  # type: ignore[attr-defined]
    for cls in reversed(_classes):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
