# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Follow subject for Reframe (#333): ask the vision model where the subject is.

Each sampled frame (a small PNG of the whole shot) goes to the model the way chat
attachments do -- an OpenAI-style ``image_url`` block with a base64 ``data:`` URI
(``chat_attachments._encode_file``), sent through ``ask_json`` (#612: the answer is checked against a
schema, one retry) to the engine Coworker is set to (Local LLM: llama-server; Remote API: its URL, key
and model).  The answer is one number: the subject's horizontal centre, 0 = the
image's left edge, 1 = its right edge.

External Agent and External Harness engines run their own model loop, so there is no
direct request to make there: Follow subject says so instead.  :data:`STUB` replaces
the model with a function (tests, and the scripted live check).

Free of ``bpy``: the asker runs on a worker thread.
"""

from __future__ import annotations

__all__ = (
    "PROMPT",
    "STUB",
    "Unavailable",
    "X_SCHEMA",
    "build_messages",
    "parse_x",
    "resolve_asker",
    "run",
    "vision_target",
)

import base64
import json
import re
import threading
import types
from typing import Any, Callable, Iterable

from . import ask_json

PROMPT = (
    "This is one frame of a video shot. Find the main subject (the person, or else the thing the shot is "
    "about) and say where its centre is horizontally, as a number from 0 (the image's left edge) to 1 "
    "(its right edge). Answer with JSON only, like {\"x\": 0.42}. If there is no clear subject, answer "
    "{\"x\": null}."
)
# The answer's shape; ``ask_json`` checks it and asks once more when it is wrong.
X_SCHEMA = {"type": "object", "properties": {"x": {"type": ["number", "null"], "minimum": 0, "maximum": 1}},
            "required": ["x"],
            "additionalProperties": False}
_LIMIT_BYTES = 2_000_000
# Replace the model: ``STUB(png_path, strip_name, frame) -> x | None``.
STUB: Callable[[str, str, int], float | None] | None = None


# No vision-capable engine to ask (the message says what to do).
Unavailable = ask_json.Unavailable


def parse_x(text: Any) -> float | None:
    """The subject's x (0..1) from the model's answer, or ``None``."""
    if text is None:
        return None
    if isinstance(text, (int, float)):
        value = float(text)
    else:
        s = str(text).strip()
        value = None
        for m in re.finditer(r"\{[^{}]*\}", s):
            try:
                obj = json.loads(m.group(0))
            except ValueError:
                continue
            if isinstance(obj, dict) and "x" in obj:
                if obj["x"] is None:
                    return None
                try:
                    value = float(obj["x"])
                except (TypeError, ValueError):
                    return None
                break
        if value is None:
            if re.search(r"\bnull\b|\bnone\b", s, re.I):
                return None
            m = re.search(r"-?\d+(?:\.\d+)?\s*%?", s)
            if not m:
                return None
            token = m.group(0).strip()
            value = float(token.rstrip("%").strip()) / (100.0 if token.endswith("%") else 1.0)
    if value > 1.0 and value <= 100.0:
        value /= 100.0                     # "42" meaning 42 %
    if value != value or value < 0.0 or value > 1.0:
        return None
    return value


def build_messages(data_uri: str) -> list[dict[str, Any]]:
    """One user message: the frame and the question (like a chat attachment)."""
    return [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": data_uri}},
        {"type": "text", "text": PROMPT},
    ]}]


def vision_target(operating_mode: str, cfg: Any, running: bool,
                  fetch: Callable[[str], dict[str, Any]] | None = None) -> tuple[str, str, str]:
    """``(url, api_key, model)`` to ask, or :class:`Unavailable` with what to do."""
    return ask_json.engine_target(operating_mode, cfg, running, purpose="Follow subject", vision=True,
                                  fetch=fetch)


def _data_uri(path: str) -> str:
    try:
        from . import chat_attachments
        data, mime = chat_attachments._encode_file(path, _LIMIT_BYTES)  # pylint: disable=protected-access
    except ImportError:
        with open(path, "rb") as fh:
            data, mime = fh.read(), "image/png"
    return "data:{:s};base64,{:s}".format(mime, base64.b64encode(data).decode("ascii"))


def make_asker(target: tuple[str, str, str], transport: Callable[..., Any]) -> Callable[[str, str, int], float | None]:
    """An asker that sends one frame and parses the answer (through :func:`ask_json.ask_json`)."""
    ask_checked = ask_json.make_asker(target, transport)

    def ask(path: str, _strip: str, _frame: int) -> float | None:
        try:
            return ask_checked(build_messages(_data_uri(path)), X_SCHEMA, name="subject_x", max_tokens=256).value["x"]
        except ask_json.AskFailed as ex:
            return parse_x(ex.text)        # a frame the model can't answer cleanly counts as "no subject"
    return ask


def resolve_asker() -> Callable[[str, str, int], float | None]:
    """The asker for Coworker's current engine (or :data:`STUB`); raises :class:`Unavailable`."""
    if STUB is not None:
        return STUB
    import bpy  # pylint: disable=import-error
    from . import llm_manager, llm_transport
    try:
        prefs = bpy.context.preferences.addons[__package__].preferences
        mode = str(prefs.operating_mode)
    except (KeyError, AttributeError):
        mode = ""
    target = vision_target(mode, llm_manager.get_config(), bool(llm_manager.get_state().is_running))
    # Errors go to a throwaway state, so a failed frame never shows as the chat's error.
    scratch = types.SimpleNamespace(error="", error_full="", error_kind="", is_thinking=False)

    def transport(*args: Any, **kwargs: Any) -> Any:
        with llm_transport.using(scratch, threading.Event()):
            return llm_transport.openai_chat_completions(*args, **kwargs)
    transport.error_kind = lambda: str(getattr(scratch, "error_kind", "") or "")    # type: ignore[attr-defined]
    return make_asker(target, transport)


def run(jobs: Iterable[tuple[str, int, str]],
        ask: Callable[[str, str, int], float | None]) -> dict[str, list[tuple[int, float | None]]]:
    """Ask for every ``(strip, frame, png)``; returns ``{strip: [(frame, x or None), ...]}``.

    A frame the model can't place counts as ``None`` (smoothing skips it); a failing
    engine stops the run with its message.
    """
    out: dict[str, list[tuple[int, float | None]]] = {}
    for strip, frame, path in jobs:
        out.setdefault(strip, []).append((int(frame), ask(path, strip, int(frame))))
    return out
