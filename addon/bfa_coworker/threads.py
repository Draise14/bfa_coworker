# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Threads: one conversation per job (Tier 4, Threads 1, #357).

A :class:`Thread` is one conversation that can live next to the others: its own
history and live readout (``state``), Queue, Stop, plan (``store``), engine
session, loaded tool domains, ``Coworker_*`` text blocks and status.  The
:class:`ThreadRegistry` holds the live threads and one **default** thread, which
is today's single conversation: the controller's module-level state
(``agent_controller._agent_state``, ``_stop_event``, ``_message_queue``,
``session_memory.store``) *is* the default thread's, so one thread behaves
exactly as before.

**One turn at a time across threads** (1.1.42): the registry's turn gate lets one
thread run a turn; another thread's message waits in its own Queue and starts
when the gate frees (oldest waiting message first).  Undo, Revert and the scene
lock stay as they are.  Running side by side is Threads 3 (#359).

The turn worker marks which thread it works for (:func:`use` / :func:`current`),
so code deep in a turn (the MCP request's thread header, the transport) finds
the thread without passing it through every call.

bpy-free and thread-safe.
"""

from __future__ import annotations

__all__ = (
    "DEFAULT_ID",
    "Thread",
    "ThreadRegistry",
    "current",
    "label",
    "pick",
    "short_name",
    "tag",
    "use",
)

import contextlib
import itertools
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

DEFAULT_ID = "main"
RUNNING_CAP = 3          # live threads that may run at once (Threads 3; one at a time here)

# Editor kinds in plain words, for undo step names and status (#357, Threads 2 names them).
_EDITOR_NAMES = {
    "VIEW_3D": "3D", "NODE_EDITOR": "Node", "SEQUENCE_EDITOR": "Sequencer", "TEXT_EDITOR": "Text",
    "IMAGE_EDITOR": "Image", "GRAPH_EDITOR": "Graph", "DOPESHEET_EDITOR": "Dope Sheet",
    "NLA_EDITOR": "NLA", "CLIP_EDITOR": "Clip", "OUTLINER": "Outliner", "PROPERTIES": "Properties",
}
_ids = itertools.count(1)


@dataclass(eq=False)
class Thread:
    """One conversation.  The default thread's state, stop, queue and store are
    the controller's module-level objects (``None`` here: the controller resolves them)."""

    id: str = ""
    editor: str = ""              # the editor kind it belongs to ("" = all editors)
    chat_id: str = ""             # its chat in History (Chat 7)
    title: str = ""
    state: Any = None             # AgentState: history, live readout, errors, usage, turn guard
    queue: Any = None             # MessageQueue
    stop: threading.Event = field(default_factory=threading.Event)
    store: Any = None             # session_memory.CheckpointStore: memory block, plan
    engine: Any = None            # the engine that runs its turns (LoopEngine / AcpEngine)
    session: str = ""             # its External Agent session id
    domains: set[str] = field(default_factory=set)       # Engine 7 domains loaded this chat
    turn_count: int = 0           # memory-block "last updated" stamps
    text_blocks: set[str] = field(default_factory=set)   # its Coworker_* text blocks
    unread: bool = False          # finished while nobody looked (Threads 2 draws the dot)
    created: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        if not self.id:
            self.id = "t{:d}".format(next(_ids))

    @property
    def is_default(self) -> bool:
        return self.id == DEFAULT_ID


def tag(thread: "Thread | str | None") -> str:
    """The thread's tag on shared records (questions, approvals, bridge calls):
    "" for the default thread, so one thread looks exactly as before."""
    thread_id = thread if isinstance(thread, str) or thread is None else thread.id
    return "" if not thread_id or thread_id == DEFAULT_ID else thread_id


def short_name(editor: str) -> str:
    """The editor kind in a word or two ("3D", "Node", "Sequencer"); "" for no editor."""
    return _EDITOR_NAMES.get(editor, editor.replace("_", " ").title()) if editor else ""


def pick(registry: "ThreadRegistry", editor: str, pinned: str = "", focus: bool = False,
         create: Callable[[str], Thread] | None = None) -> Thread:
    """The thread a chat in an *editor* kind shows (Threads 2, #358): the area's pinned
    thread (*New thread here*) while it lives; else, with editor *focus* on, that editor
    kind's thread (made with *create*, or the registry's); else the shared default
    thread ("All editors", today's one conversation)."""
    if pinned:
        thread = registry.get(pinned)
        if thread is not None:
            return thread
    if not focus or not editor:
        return registry.default
    found = registry.for_editor(editor, create=False)
    if found is not None:
        return found
    return create(editor) if create is not None else registry.for_editor(editor)


def label(thread: Thread | None) -> str:
    """Who did it, for undo step names: "Coworker" for the default thread,
    "Coworker (3D)" for an editor's."""
    if thread is None or thread.is_default or not thread.editor:
        return "Coworker"
    return "Coworker ({:s})".format(_EDITOR_NAMES.get(thread.editor, thread.editor.title()))


class ThreadRegistry:
    """The live threads, the default one, and the one-turn-at-a-time gate."""

    def __init__(self, make_state: Callable[[], Any] | None = None,
                 make_queue: Callable[[], Any] | None = None,
                 make_store: Callable[[], Any] | None = None,
                 cap: int = RUNNING_CAP, default_objects: bool = False) -> None:
        """*make_\\** build a new thread's objects.  With *default_objects* the default
        thread gets its own too (tests); otherwise its are the controller's globals."""
        self._make_state = make_state
        self._make_queue = make_queue
        self._make_store = make_store
        self.cap = cap
        self._lock = threading.RLock()
        self._running: str = ""          # the thread whose turn holds the gate
        self.default = Thread(id=DEFAULT_ID)
        if default_objects:
            self._fill(self.default)
        self._threads: dict[str, Thread] = {DEFAULT_ID: self.default}

    def _fill(self, thread: Thread) -> None:
        if thread.state is None and self._make_state is not None:
            thread.state = self._make_state()
        if thread.queue is None and self._make_queue is not None:
            thread.queue = self._make_queue()
        if thread.store is None and self._make_store is not None:
            thread.store = self._make_store()

    # -- the threads ---------------------------------------------------------------
    def create(self, editor: str = "", chat_id: str = "", title: str = "") -> Thread:
        thread = Thread(editor=editor, chat_id=chat_id, title=title)
        self._fill(thread)
        with self._lock:
            self._threads[thread.id] = thread
        return thread

    def get(self, thread_id: str | None) -> Thread | None:
        """The thread *thread_id*; "" / None is the default one."""
        if not thread_id:
            return self.default
        with self._lock:
            return self._threads.get(thread_id)

    def for_editor(self, editor: str, create: bool = True) -> Thread | None:
        """The thread of editor kind *editor* (the oldest one, if a second was pinned)."""
        if not editor:
            return self.default
        with self._lock:
            found = [t for t in self._threads.values() if t.editor == editor]
        if found:
            return min(found, key=lambda t: t.created)
        return self.create(editor=editor) if create else None

    def live(self) -> list[Thread]:
        """Every live thread, the default first."""
        with self._lock:
            others = sorted((t for t in self._threads.values() if not t.is_default), key=lambda t: t.created)
        return [self.default] + others

    def remove(self, thread_id: str) -> bool:
        """Close a thread (never the default, never while its turn runs)."""
        with self._lock:
            if thread_id == DEFAULT_ID or thread_id == self._running or thread_id not in self._threads:
                return False
            del self._threads[thread_id]
            return True

    # -- one turn at a time (1.1.42) -------------------------------------------------
    def try_begin(self, thread: Thread) -> bool:
        """Take the turn gate for *thread*; False while another thread's turn runs.
        The same thread may take it again (its stopped turn still unwinding)."""
        with self._lock:
            if self._running and self._running != thread.id:
                return False
            self._running = thread.id
            return True

    def end(self, thread: Thread) -> None:
        """*thread*'s turn is over: the gate is free (only its owner frees it)."""
        with self._lock:
            if self._running == thread.id:
                self._running = ""

    def running(self) -> Thread | None:
        """The thread whose turn runs now, or None."""
        with self._lock:
            return self._threads.get(self._running) if self._running else None

    def blocked(self, thread: Thread) -> bool:
        """True when another thread's turn holds the gate (*thread* has to wait)."""
        with self._lock:
            return bool(self._running) and self._running != thread.id

    def next_waiting(self, queue_of: Callable[[Thread], Any] | None = None) -> Thread | None:
        """The thread whose queued message waited longest (it goes next), or None.
        *queue_of* finds a thread's queue (the default's is the controller's)."""
        best, best_at = None, None
        for thread in self.live():
            queue = queue_of(thread) if queue_of is not None else thread.queue
            head = queue.peek() if queue is not None else None
            if not head:
                continue
            at = float(head.get("queued_at", 0.0) or 0.0)
            if best_at is None or at < best_at:
                best, best_at = thread, at
        return best


# -- which thread a worker works for ------------------------------------------------
_local = threading.local()


@contextlib.contextmanager
def use(thread: Thread | None) -> Iterator[Thread | None]:
    """Mark the calling (worker) thread as working for *thread* until the block ends."""
    previous = getattr(_local, "thread", None)
    _local.thread = thread
    try:
        yield thread
    finally:
        _local.thread = previous


def current() -> Thread | None:
    """The thread the calling worker works for (None: the default / the UI)."""
    return getattr(_local, "thread", None)
