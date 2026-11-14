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

**Side by side** (Threads 3, #359): the registry is the thread runner.  Each
thread's turn takes a *slot*; how many slots there are depends on the engine
(:func:`slots_for`): online engines (Remote API, External Agent) run up to
*cap* chats at once (the "Chats running at once" preference, 3 by default);
Local runs them **chained**, one after another, because one machine has one
GPU's worth of capacity (an opt-in lets Local use the same cap on llama-server's
unified KV slots).  A thread's message waits in its own Queue while every slot
is taken, and starts when one frees (oldest waiting message first).

The turn worker marks which thread it works for (:func:`use` / :func:`current`),
so code deep in a turn (the MCP request's thread header, the transport) finds
the thread without passing it through every call.

bpy-free and thread-safe.
"""

from __future__ import annotations

__all__ = (
    "DEFAULT_ID",
    "AttachmentSocket",
    "Thread",
    "ThreadRegistry",
    "current",
    "label",
    "close_refused",
    "pick",
    "reopen",
    "tab_modes",
    "tab_title",
    "short_name",
    "slots_for",
    "stop_all",
    "tag",
    "use",
    "wait_message",
)

import contextlib
import itertools
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

DEFAULT_ID = "main"
RUNNING_CAP = 3          # chats an online engine runs at once (the preference's default, #359)
CAP_MAX = 8              # the preference's upper bound
LOCAL_SLOTS_MAX = 4      # llama-server's unified KV slots (auto = 4; never split the KV, #399)

# Engine kinds (``operating_mode`` in a word): which run chats at once.
ENGINE_KINDS = ("local", "remote", "agent", "harness")


def slots_for(kind: str, cap: int = RUNNING_CAP, local_side_by_side: bool = False) -> int:
    """How many chats may run a turn at once on engine *kind* (#359).

    Online engines (``remote``, ``agent``) run up to *cap* side by side.  ``local`` is
    chained (1) unless *local_side_by_side* is on: then up to *cap*, at most the
    server's :data:`LOCAL_SLOTS_MAX` unified slots.  The External Harness's outside
    client is one chat (1)."""
    cap = max(1, min(int(cap or 1), CAP_MAX))
    if kind in ("remote", "agent"):
        return cap
    if kind == "local" and local_side_by_side:
        return min(cap, LOCAL_SLOTS_MAX)
    return 1


def wait_message(slots: int, running: "list[Thread]", title: Callable[["Thread"], str]) -> str:
    """What a message that has to wait says: whose turn it waits for when chats are
    chained, else that it waits for a free slot."""
    if slots <= 1 and running:
        return "Waits for the {:s} chat's turn".format(title(running[0]))
    return "Waits for a free slot ({:d} of {:d} chats running)".format(len(running), slots)

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
    # Threads 2b (#417): what the chat holds for it, kept when you switch threads.
    draft: str = ""               # the half-typed message
    attachment: str = ""          # the attached image's name (the socket shows the focused thread's)
    attachment_once: bool = True  # send it with the next message only (off: pinned)
    sticky: dict[str, Any] = field(default_factory=dict)    # the pinned image's re-link record
    auto: dict[str, Any] = field(default_factory=lambda: {"run": None, "last": "", "user_stop": False,
                                                          "err_before": ""})     # its Auto mode
    plan_dismissed: str = ""      # the plan the Plan card was answered for

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
    """The live threads, the default one, and the thread runner's slots (#359)."""

    def __init__(self, make_state: Callable[[], Any] | None = None,
                 make_queue: Callable[[], Any] | None = None,
                 make_store: Callable[[], Any] | None = None,
                 cap: int = RUNNING_CAP, default_objects: bool = False) -> None:
        """*make_\\** build a new thread's objects.  With *default_objects* the default
        thread gets its own too (tests); otherwise its are the controller's globals."""
        self._make_state = make_state
        self._make_queue = make_queue
        self._make_store = make_store
        self.cap = cap                   # online engines: chats at once (the preference)
        self.kind = "local"              # the engine running turns now (set at each send)
        self.local_side_by_side = False  # opt-in: Local uses the cap too (unified KV slots)
        self._lock = threading.RLock()
        self._running: dict[str, float] = {}     # thread id -> when its turn took a slot (in order)
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
            if thread_id == DEFAULT_ID or thread_id in self._running or thread_id not in self._threads:
                return False
            del self._threads[thread_id]
            return True

    # -- the thread runner (#359) ------------------------------------------------------
    def slots(self) -> int:
        """How many turns may run at once now (by the engine kind, :func:`slots_for`)."""
        return slots_for(self.kind, self.cap, self.local_side_by_side)

    def try_begin(self, thread: Thread) -> bool:
        """Take a slot for *thread*'s turn; False while every slot is taken.  A thread
        that holds one keeps it (its stopped turn still unwinding, or a reserved start)."""
        with self._lock:
            if thread.id in self._running:
                return True
            if len(self._running) >= self.slots():
                return False
            self._running[thread.id] = time.monotonic()
            return True

    def end(self, thread: Thread) -> None:
        """*thread*'s turn is over: its slot is free (only its owner frees it)."""
        with self._lock:
            self._running.pop(thread.id, None)

    def running(self) -> Thread | None:
        """The thread whose turn started first of those running now, or None."""
        with self._lock:
            for thread_id in self._running:
                if thread_id in self._threads:
                    return self._threads[thread_id]
            return None

    def running_threads(self) -> list[Thread]:
        """Every thread whose turn runs now, in the order they started."""
        with self._lock:
            return [self._threads[i] for i in self._running if i in self._threads]

    def is_running(self, thread: Thread | None) -> bool:
        with self._lock:
            return thread is not None and thread.id in self._running

    def blocked(self, thread: Thread) -> bool:
        """True when *thread* holds no slot and every slot is taken (it has to wait)."""
        with self._lock:
            return thread.id not in self._running and len(self._running) >= self.slots()

    def next_waiting(self, queue_of: Callable[[Thread], Any] | None = None) -> Thread | None:
        """The thread whose queued message waited longest (it goes next), or None.
        A thread whose turn runs is skipped: its message waits for that turn.
        *queue_of* finds a thread's queue (the default's is the controller's)."""
        best, best_at = None, None
        for thread in self.live():
            if self.is_running(thread):
                continue
            queue = queue_of(thread) if queue_of is not None else thread.queue
            head = queue.peek() if queue is not None else None
            if not head:
                continue
            at = float(head.get("queued_at", 0.0) or 0.0)
            if best_at is None or at < best_at:
                best, best_at = thread, at
        return best


def stop_all(registry: "ThreadRegistry", stop: Callable[[Thread], bool]) -> int:
    """Stop All (#359): *stop* each live thread (it returns whether that thread was
    working or had messages waiting); how many were."""
    return sum(1 for thread in registry.live() if stop(thread))


# -- Threads 2b (#417): the window's one image socket -------------------------------
class AttachmentSocket:
    """The window has one attached-image socket (a datablock pointer the panels edit), but
    every thread has its own attachment.  The socket holds the image of the thread whose
    chat was used last; every thread keeps its image's name, and its pinned-image record,
    so switching back brings it again.

    ``thread.attachment`` is kept current by :meth:`set_by` for every change of the
    socket, so a switch never reads a socket that Blender emptied (undo) as "no image".
    """

    def __init__(self, focus: str = DEFAULT_ID) -> None:
        self.focus = focus          # the thread whose image the socket holds
        self.sticky: dict[str, Any] = {}    # the pinned-image record of that thread

    def use(self, registry: "ThreadRegistry", thread: Thread, socket_image: str = "") -> str | None:
        """*thread*'s chat is used: ``None`` when the socket already holds its image, else
        the image name to put in the socket ("" = empty).  *socket_image* is what the socket
        holds now (adopted by the thread in use if it has no record yet)."""
        if thread.id == self.focus:
            return None
        old = registry.get(self.focus)
        if old is not None:
            if not old.attachment and socket_image:
                old.attachment = socket_image
            old.sticky = dict(self.sticky)
        self.sticky = dict(thread.sticky)
        self.focus = thread.id
        return thread.attachment

    def set_by(self, registry: "ThreadRegistry", thread: Thread, image: str) -> None:
        """The socket was set from *thread*'s chat to *image* ("" = cleared): it is that
        thread's now; the thread that held the socket keeps the image it had."""
        if thread.id != self.focus:
            old = registry.get(self.focus)
            if old is not None:
                old.sticky = dict(self.sticky)
            self.sticky = dict(thread.sticky)
            self.focus = thread.id
        thread.attachment = image

    def image_of(self, thread: Thread, socket_image: str) -> str:
        """*thread*'s image name: the socket's for the thread in use, else its record."""
        return socket_image if thread.id == self.focus else thread.attachment


# -- Threads 2c (#427): chat tabs ------------------------------------------------------
# Every live thread is an open tab, in the order they were opened, the default (All
# editors) first.  Closing a tab lets its thread go; its chat stays in History.

TAB_TITLE_CHARS = 18


def tab_title(thread: Thread, first_message: str = "") -> str:
    """A tab's short title: the first thing asked in it, else its editor's name."""
    text = " ".join(str(first_message or "").split())
    if text:
        return text if len(text) <= TAB_TITLE_CHARS else text[:TAB_TITLE_CHARS - 1].rstrip() + "\u2026"
    if thread.is_default:
        return "All editors"
    return thread.title or short_name(thread.editor) or "Chat"


def close_refused(registry: "ThreadRegistry", thread: Thread, busy: bool = False) -> str:
    """Why *thread*'s tab can't close now ("" when it can): its turn runs.  The default
    tab never goes away: closing it saves its chat to History and starts it fresh."""
    if busy or registry.is_running(thread):
        return "Its turn is running: stop it first"
    return ""


def reopen(registry: "ThreadRegistry", chat_id: str, editor: str = "",
           make: Callable[[str, str], Thread] | None = None) -> Thread:
    """The tab for History's chat *chat_id*: the open one, else a new thread for it
    (``make(editor, chat_id)`` loads its messages)."""
    for thread in registry.live():
        if thread.chat_id == chat_id:
            return thread
    if make is not None:
        return make(editor, chat_id)
    thread = registry.create(editor=editor, chat_id=chat_id)
    return thread


def tab_modes(title_widths: list[float], shown: int, room: float, icon_w: float,
              tail_w: float) -> list[str]:
    """How each tab fits in *room* (UI units): ``"full"`` (icon + title), ``"icon"`` or
    ``"hidden"`` (reached through the strip's menu).  A titled tab costs its title's width
    plus its icon (*icon_w*), an icon tab *icon_w*; *tail_w* is the tools to the right
    (close, +, the menu) and the gap before them.

    All titles when they fit; else the shown tab keeps its title and the others are
    icons; then icons drop from the far end until it fits (never the shown one)."""
    n = len(title_widths)
    room -= tail_w
    if sum(w + icon_w for w in title_widths) <= room:
        return ["full"] * n
    modes = ["icon"] * n
    if 0 <= shown < n:
        modes[shown] = "full"

    def width() -> float:
        return sum((title_widths[i] + icon_w) if m == "full" else icon_w if m == "icon" else 0.0
                   for i, m in enumerate(modes))
    for i in range(n - 1, -1, -1):
        if width() <= room:
            break
        if i != shown:
            modes[i] = "hidden"
    return modes


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
