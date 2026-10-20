# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Your chats in one list, not tied to the .blend (Tier 4, task "Chat 7", #156).

Like the chat list in VS Code or Claude: every chat has its own file
(``chats/<id>.json``, its memory next to it as ``<id>.memory.json``) and an
entry in ``chats/index.json`` -- title (your first message, renamable), when
it was last used, which engine / agent, the .blend it started in, pinned.
Opening a .blend still continues the chat you last used there
(``last_for_blend``); any chat can be continued from any file.

The old per-.blend files (``<blend name>.json`` next to ``chats/``) are moved
in once (copied; the originals stay as a backup), with their memory and
External Agent sessions.

bpy-free: the add-on passes the folder in.
"""

from __future__ import annotations

__all__ = (
    "ChatIndex",
    "relative_time",
)

import json
import re
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any

_TITLE_CHARS = 60
# Files in the history folder that aren't per-.blend chats.
_NOT_CHATS = re.compile(r"^(agent_sessions|default_session_memory|.*_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})\.json$")


def relative_time(when: float, now: float | None = None) -> str:
    """ "just now", "5 min ago", "3 h ago", "yesterday", "12 Oct"."""
    now = time.time() if now is None else now
    age = max(0.0, now - when)
    if age < 60:
        return "just now"
    if age < 3600:
        return "{:d} min ago".format(int(age // 60))
    if age < 86400:
        return "{:d} h ago".format(int(age // 3600))
    if age < 2 * 86400:
        return "yesterday"
    return time.strftime("%d %b", time.localtime(when)).lstrip("0")


def title_of(history: list[dict[str, Any]]) -> str:
    """The first thing you asked, short; "" for a chat without a message yet."""
    for msg in history:
        if msg.get("role") == "user" and not msg.get("ui_only"):
            content = msg.get("content")
            if isinstance(content, list):
                content = " ".join(str(p.get("text", "")) for p in content if isinstance(p, dict))
            text = re.sub(r"\[attached:[^\]]*\]", "", str(content or ""))
            text = " ".join(text.split())
            if text:
                return text if len(text) <= _TITLE_CHARS else text[:_TITLE_CHARS - 3].rstrip() + "..."
    return ""


class ChatIndex:
    """The chats in *base* (the add-on's chat history folder)."""

    def __init__(self, base: Path) -> None:
        self.base = Path(base)
        self.folder = self.base / "chats"
        self._lock = threading.Lock()

    # -- paths -----------------------------------------------------------------------
    def chat_path(self, chat_id: str) -> Path:
        return self.folder / "{:s}.json".format(chat_id)

    def memory_path(self, chat_id: str) -> Path:
        return self.folder / "{:s}.memory.json".format(chat_id)

    def _index_path(self) -> Path:
        return self.folder / "index.json"

    # -- the index -------------------------------------------------------------------
    def _read(self) -> dict[str, Any]:
        try:
            with open(str(self._index_path()), "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                data.setdefault("chats", {})
                data.setdefault("last_for_blend", {})
                return data
        except (OSError, ValueError):
            pass
        return {"chats": {}, "last_for_blend": {}}

    def _write(self, data: dict[str, Any]) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        tmp = self._index_path().with_suffix(".tmp")
        with open(str(tmp), "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=1)
        tmp.replace(self._index_path())

    def ensure(self) -> None:
        """Create the folder; move the old per-.blend chats in, once."""
        with self._lock:
            if self._index_path().exists():
                return
            data = {"chats": {}, "last_for_blend": {}}
            sessions = self._read_json(self.base / "agent_sessions.json")
            moved_sessions: dict[str, Any] = {}
            self.folder.mkdir(parents=True, exist_ok=True)
            for old in sorted(self.base.glob("*.json")):
                if _NOT_CHATS.match(old.name):
                    continue
                history = self._read_json(old)
                if not isinstance(history, list) or not any(m.get("role") == "user" for m in history
                                                            if isinstance(m, dict)):
                    continue
                stem = old.stem
                chat_id = "blend-" + re.sub(r"[^A-Za-z0-9_-]", "_", stem)[:40]
                shutil.copy2(str(old), str(self.chat_path(chat_id)))
                if stem == "default" and (self.base / "default_session_memory.json").exists():
                    shutil.copy2(str(self.base / "default_session_memory.json"), str(self.memory_path(chat_id)))
                if old.name in sessions:
                    moved_sessions[self.chat_path(chat_id).name] = sessions[old.name]
                data["chats"][chat_id] = {
                    "title": title_of(history) or stem, "updated": old.stat().st_mtime,
                    "created": old.stat().st_mtime, "blend": "" if stem == "default" else stem,
                    "engine": "", "messages": len(history), "pinned": False}
                data["last_for_blend"][stem] = chat_id
            if moved_sessions:
                sessions.update(moved_sessions)
                try:
                    with open(str(self.base / "agent_sessions.json"), "w", encoding="utf-8") as fh:
                        json.dump(sessions, fh, indent=2)
                except OSError:
                    pass
            self._write(data)

    @staticmethod
    def _read_json(path: Path) -> Any:
        try:
            with open(str(path), "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return {}

    # -- reading ---------------------------------------------------------------------
    def entries(self, query: str = "") -> list[dict[str, Any]]:
        """The chats, pinned first, then the newest; *query* filters titles."""
        q = query.lower().strip()
        out = []
        for chat_id, entry in self._read()["chats"].items():
            if q and q not in str(entry.get("title", "")).lower():
                continue
            out.append(dict(entry, id=chat_id))
        out.sort(key=lambda e: (not e.get("pinned"), -float(e.get("updated", 0))))
        return out

    def get(self, chat_id: str) -> dict[str, Any] | None:
        entry = self._read()["chats"].get(chat_id)
        return dict(entry, id=chat_id) if entry else None

    def last_for_blend(self, blend: str) -> str:
        chat_id = self._read()["last_for_blend"].get(blend or "default", "")
        return chat_id if chat_id and self.chat_path(chat_id).exists() else ""

    @staticmethod
    def new_id() -> str:
        return time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]

    # -- writing ---------------------------------------------------------------------
    def touch(self, chat_id: str, history: list[dict[str, Any]], blend: str = "", engine: str = "") -> None:
        """The chat was saved: keep its entry current (a chat with no message isn't listed)."""
        title = title_of(history)
        with self._lock:
            data = self._read()
            entry = data["chats"].get(chat_id)
            if entry is None:
                if not title:
                    return
                entry = data["chats"][chat_id] = {"title": title, "created": time.time(), "blend": blend,
                                                  "pinned": False, "renamed": False}
            if not entry.get("renamed") and title:
                entry["title"] = title
            entry["updated"] = time.time()
            entry["messages"] = len(history)
            if engine:
                entry["engine"] = engine
            data["last_for_blend"][blend or "default"] = chat_id
            self._write(data)

    def set_last_for_blend(self, blend: str, chat_id: str) -> None:
        with self._lock:
            data = self._read()
            data["last_for_blend"][blend or "default"] = chat_id
            self._write(data)

    def rename(self, chat_id: str, title: str) -> bool:
        with self._lock:
            data = self._read()
            entry = data["chats"].get(chat_id)
            if entry is None or not title.strip():
                return False
            entry["title"], entry["renamed"] = title.strip()[:120], True
            self._write(data)
            return True

    def set_pinned(self, chat_id: str, pinned: bool) -> None:
        with self._lock:
            data = self._read()
            if chat_id in data["chats"]:
                data["chats"][chat_id]["pinned"] = bool(pinned)
                self._write(data)

    def delete(self, chat_id: str) -> None:
        """Forget the chat and remove its files."""
        with self._lock:
            data = self._read()
            data["chats"].pop(chat_id, None)
            data["last_for_blend"] = {k: v for k, v in data["last_for_blend"].items() if v != chat_id}
            self._write(data)
        for path in (self.chat_path(chat_id), self.memory_path(chat_id)):
            try:
                path.unlink()
            except OSError:
                pass
