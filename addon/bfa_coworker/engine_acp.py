# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
An external agent as the chat's engine (Tier 4, task "Agent 4").

:class:`AcpEngine` runs an agent app on this computer (OpenCode, Claude Code,
Codex, Gemini, Copilot -- see :mod:`acp_presets`) over the Agent Client
Protocol (:mod:`acp_client`).  You chat in Coworker's own chat; the agent
plans and works with **Coworker's Blender tools** -- our MCP server is handed
to its session -- and its turn streams back into the same chat state as a
local turn (:mod:`chat_state`), so the panel and the GPU chat draw it the same:
text, thinking, Workshop rows, plan, Stop, Queue.

Permissions (Agent 5): Coworker's own tools run (in Blender, with a
checkpoint to revert to) unless *ask_coworker_tools* is on.  The agent's own
tools (its shell, file edits) are refused while *only_coworker_tools* is on;
otherwise you're asked in the chat (:mod:`approvals`): Allow once, Always (for
this session) or Deny.

Resume (Agent 6): :meth:`connect` with *resume* continues an earlier session
(``session/load``) when the agent can, else opens a new one; New Thread calls
:meth:`new_session`.  *on_session* hears each session id, to keep it.

Threads: the agent's messages arrive on the client's reader thread and are put
on an :class:`engine.EventBus`; :meth:`drain` applies them on the main thread.

Chats (Threads 1, #357; Threads 3, #359): every chat has its own agent session on
the one agent process.  Up to :attr:`AcpEngine.cap` chats' turns run at once (an
agent measured to run sessions concurrently -- Claude Code -- gets the "Chats
running at once" cap; others take turns, one after another); each running turn
has a *slot* (its turn id, session, chat applier, mode).  The agent's updates
and permission requests carry their ``sessionId`` and are routed to that
session's chat; Stop cancels only that chat's session.  Free of ``bpy``.
"""

from __future__ import annotations

import base64
import os
import threading
from collections import deque
import re
from typing import Any, Callable

from . import store_paths
from .acp_client import MCP_SERVER_NAME, AcpClient, AcpError, _tool_name, stdio_mcp_server, update_to_events
from .approvals import ApprovalRequest, Approvals
from .chat_state import ChatStateApplier
from .engine import PLAN_NOTE, PLAN_SAFE_TOOLS, EngineCaps, EventBus, Prompt, new_turn_id

# Words agents use when you aren't logged in (ACP's "auth required" shares its
# code, -32000, with our "agent exited", so the code alone can't tell).
_LOGIN_WORDS = re.compile(r"\bauth(entication|orization)?\b|\blog ?in\b|not logged|unauthori[sz]ed|"
                          r"\b401\b|api[ _-]?key|credential", re.IGNORECASE)
# The agent's options that get a picker in the chat, in this order.
PICKER_CATEGORIES = ("mode", "model", "thought_level")
# Modes that stop the agent asking before its tools (so Coworker couldn't refuse them).
_SKIPS_QUESTIONS = re.compile(r"bypass|accept|\bauto\b|handles permission", re.IGNORECASE)
STOP_GRACE = 10.0     # seconds an agent gets to finish after Stop before Coworker closes it

__all__ = (
    "AcpEngine",
)


class AcpEngine:
    """One external agent session, driven from the chat."""

    name = "External Agent"

    def __init__(self, state: Any, client_factory: Callable[..., AcpClient] = AcpClient) -> None:
        self.state = state                      # the add-on's agent state (chat history...)
        self.caps = EngineCaps(label="External Agent", images=False, cancel=True, own_loop=True)
        self.bus = EventBus()
        self.applier = ChatStateApplier(state)
        self.approvals = Approvals()            # the agent's "may I?" questions (Agent 5)
        self.ask_coworker_tools = False         # on: ask before Coworker's tools too (approval Ask)
        self.never_ask = False                  # approval Auto (#240): allow without asking
        self.client: AcpClient | None = None
        self.session_id = ""
        self.agent_label = ""
        self._named = False
        self.status = "Not connected"
        self.error = ""
        self.connected = False
        self.connecting = False
        self.login_hint = ""             # the preset's "how to log in", for login errors
        self.auto_approve = False        # Auto mode (#177): allow what the settings allow
        self.auto_threads: set[str] = set()   # ...for these threads only (#417)
        # Each running chat's turn (#359): thread tag -> {"turn", "session", "applier", "mode",
        # "forced", "abandoned"}.  Set first: ``_turn_id`` / ``_turn_mode`` read the current one.
        self.thread = ""                     # the thread whose session is current ("" = default)
        self._running: dict[str, dict[str, Any]] = {}
        self._mode_idle = "AGENT"            # the chat mode while no turn of the current thread runs
        self.config_options: list[dict[str, Any]] = []     # the agent's own (mode, model...)
        self.preferred: dict[str, str] = {}                # your picks, re-applied per session
        self.on_option: Callable[[str, str], None] | None = None
        self.stop_grace = STOP_GRACE
        self._client_factory = client_factory
        self._lock = threading.RLock()
        self._queue: deque[Prompt] = deque()
        self.on_session: Callable[[str], None] | None = None   # each session id (to resume later)
        # #431: a tab's chat continues its own agent session.  *resume_for(thread)* is the
        # session saved for that thread's chat ("" = none); *on_thread_session(session, thread)*
        # hears each tab's session, to file it under its chat.
        self.resume_for: Callable[[str], str] | None = None
        self.preset_id = ""                 # which agent (its sessions are filed per agent)
        self.on_thread_session: Callable[[str, str], None] | None = None
        self.resumed = self.resume_failed = False
        self._cwd, self._servers = "", []
        self.on_change: Callable[[], None] | None = None     # redraw hook (any thread)
        self.on_request: Callable[[str], None] | None = None  # each turn's message (Goal & Plan)
        self.only_coworker_tools = True      # off: the agent may also use its own tools
        self.bridge_port = 0                 # the Blender bridge port its tools were given
        # Threads 1 (#357): one agent session per thread on the same process.  A prompt's
        # ``thread`` picks the session and the chat it writes to.
        self.state_for: Callable[[str], Any] | None = None   # a thread's chat state
        self._threads: dict[str, tuple[str, Any, ChatStateApplier]] = {}
        self._turn_appliers: dict[str, ChatStateApplier] = {}
        self.cap = 1                         # chats at once (#359; set from the preference)
        self._turn_threads: dict[str, str] = {}    # turn id -> its thread's tag (#359)
        # Each chat's own Goal & Plan (#359): *on_thread_request(thread, text)* and
        # *on_thread_plan(thread, steps)*; unset, :attr:`on_request` / the applier's plan hook.
        self.on_thread_request: Callable[[str, str], None] | None = None
        self.on_thread_plan: Callable[[str, list], None] | None = None

    # -- the running turns (#359) ------------------------------------------------------
    @property
    def _turn_id(self) -> str:
        """The current thread's running turn ("" = none)."""
        return str(self._running.get(self.thread, {}).get("turn", ""))

    @_turn_id.setter
    def _turn_id(self, value: str) -> None:
        with self._lock:
            if value:
                slot = self._running.setdefault(self.thread, {"session": self.session_id, "applier": self.applier,
                                                              "mode": self._mode_idle, "forced": False})
                slot["turn"] = value
            else:
                self._running.pop(self.thread, None)

    @property
    def _turn_mode(self) -> str:
        slot = self._running.get(self.thread)
        return str(slot.get("mode", "AGENT")) if slot is not None else self._mode_idle

    @_turn_mode.setter
    def _turn_mode(self, value: str) -> None:
        slot = self._running.get(self.thread)
        if slot is not None:
            slot["mode"] = value
        else:
            self._mode_idle = value

    def _slot_for_session(self, session: str) -> tuple[str, dict[str, Any] | None]:
        """The running chat whose session is *session*: ``(tag, slot)``, or ``("", None)``
        for a session no turn runs in (a cancelled turn's late chunks).  Without a
        session id (an agent that doesn't send it): the current thread's turn, or the
        only one running."""
        with self._lock:
            slots = list(self._running.items())
        if session:
            for tag, slot in slots:
                if slot.get("session") == session:
                    return tag, slot
            if len(slots) == 1 and not slots[0][1].get("session"):
                return slots[0]             # its session isn't known yet
            return "", None
        mine = dict(slots).get(self.thread)
        if mine is not None:
            return self.thread, mine
        return slots[0] if len(slots) == 1 else ("", None)

    def _set_session(self, thread: str, session_id: str) -> None:
        """*thread*'s session is *session_id* now (its running turn's, its saved one)."""
        with self._lock:
            slot = self._running.get(thread)
            if slot is not None:
                slot["session"] = session_id
            if thread == self.thread:
                self.session_id = session_id
            elif thread in self._threads:
                _old, state, applier = self._threads[thread]
                self._threads[thread] = (session_id, state, applier)

    # -- connection ----------------------------------------------------------------
    def connect(self, argv: list[str], mcp_entry: dict[str, Any], cwd: str,
                env: dict[str, str] | None = None, label: str = "", wait: bool = False,
                resume: str = "") -> None:
        """Start the agent and open a session (in the background unless *wait*).

        *resume*: an earlier session id to continue, when the agent supports it."""
        if self.connected or self.connecting:
            return
        self.connecting, self.error = True, ""
        self.agent_label = label or (os.path.basename(argv[0]) if argv else "agent")
        self._named = bool(label)
        self.status = "Starting {:s}...".format(self.agent_label)
        self._changed()
        worker = threading.Thread(target=self._connect, args=(argv, mcp_entry, cwd, env, resume),
                                  daemon=True, name="acp-connect")
        worker.start()
        if wait:
            worker.join()

    def _connect(self, argv: list[str], mcp_entry: dict[str, Any], cwd: str,
                 env: dict[str, str] | None, resume: str = "") -> None:
        try:
            os.makedirs(cwd, exist_ok=True)
            # The agent runs outside a Store copy's package: it needs the folder as it
            # really is on disk (#390).
            cwd = store_paths.real_path(cwd)
            client = self._client_factory(argv, cwd=cwd, env=env, on_update=self._on_update,
                                          on_permission=self._on_permission)
            client.start()
            info = client.initialize()
            server = stdio_mcp_server(store_paths.real_path(mcp_entry["command"]),
                                      [store_paths.real_path(a) for a in mcp_entry.get("args") or []],
                                      dict(mcp_entry.get("env") or {}))
            self._cwd, self._servers = cwd, [server]
            self.session_id = self._open_session(client, info.load_session, resume, cwd, [server])
            if not self.session_id:
                raise AcpError("the agent did not open a session")
            self.client = client
            self.caps = EngineCaps(label="External Agent", images=info.image, cancel=True,
                                   load_session=info.load_session, own_loop=True)
            # The preset's name ("Claude Code"), not the adapter's package name.
            name = self.agent_label if self._named else (info.name or self.agent_label)
            self.applier.agent_name = name          # its replies say who wrote them
            self.status = "Connected to {:s}{:s}".format(name, " " + info.version if info.version else "")
            self.connected = True
        except (AcpError, OSError, KeyError) as ex:
            self.error = self._explain(ex)
            self.status = "Couldn't connect: {:s}".format(self.error.splitlines()[0])
            try:
                if "client" in locals():
                    client.close()      # type: ignore[possibly-undefined]
            except Exception:  # pylint: disable=broad-exception-caught
                pass
        finally:
            self.connecting = False
            self._changed()

    def _open_session(self, client: AcpClient, can_load: bool, resume: str, cwd: str,
                      servers: list[dict[str, Any]], thread: str | None = None) -> str:
        """Continue *resume* (``session/load``) when the agent can, else open a new session
        (for *thread*, default: the current one)."""
        thread = self.thread if thread is None else thread
        self.resumed = self.resume_failed = False
        session_id, result = "", {}
        if resume and can_load:
            try:
                result = client.load_session(resume, cwd, servers)    # its replay is ignored: no turn runs
                session_id, self.resumed = resume, True
            except AcpError:
                self.resume_failed = True     # gone or too old: a fresh session instead
        if not session_id:
            result = client.new_session(cwd, servers)
            session_id = str(result.get("sessionId", ""))
        self.config_options = list(result.get("configOptions") or [])
        if session_id:
            self._apply_preferred(client, session_id)
        if session_id and self.on_session is not None and not thread:
            self.on_session(session_id)     # the default thread's session, filed under its chat
        elif session_id and self.on_thread_session is not None and thread:
            self.on_thread_session(session_id, thread)      # a tab's, under its chat (#431)
        return session_id

    # -- threads (#357) ----------------------------------------------------------------
    def use_thread(self, thread: str) -> None:
        """Turns from now on run in *thread*'s own session and write its chat (between turns)."""
        with self._lock:
            if thread == self.thread:
                return
            self._threads[self.thread] = (self.session_id, self.state, self.applier)
            session_id, state, applier = self._threads.get(thread, ("", None, None))
            if state is None:
                state = self.state_for(thread) if self.state_for is not None else self.state
                applier = ChatStateApplier(state, on_plan=self._plan_hook(thread))
                applier.agent_name = self.applier.agent_name
            self.thread, self.session_id, self.state, self.applier = thread, session_id, state, applier

    def _plan_hook(self, thread: str) -> Callable[[list], None] | None:
        """The agent's plan for *thread*'s turn goes to that chat's Goal & Plan (#359)."""
        if self.on_thread_plan is None:
            return self.applier.on_plan
        return lambda steps, _t=thread: self.on_thread_plan(_t, steps)

    def drop_threads(self) -> None:
        """Another .blend opened (#431): its tabs are new threads, so the old tabs' sessions
        and chats are let go (the default thread's stays until :meth:`switch_session`)."""
        if self.thread and not self.busy_for(self.thread):
            self.use_thread("")
        self._threads = {k: v for k, v in self._threads.items() if not k or k == self.thread
                         or k in self._running}

    def forget_thread(self, thread: str) -> None:
        """New Thread in an editor's thread (#358): its next turn opens a fresh session."""
        if self.busy_for(thread):
            return                  # its running turn keeps its session
        if thread == self.thread:
            self.session_id = ""
        else:
            self._threads.pop(thread, None)

    def drop_queued(self, thread: str) -> int:
        """Stop in a thread whose turn isn't the running one: its waiting messages go."""
        kept = [p for p in self._queue if p.thread != thread]
        dropped = len(self._queue) - len(kept)
        self._queue.clear()
        self._queue.extend(kept)
        if dropped:
            self._changed()
        return dropped

    def _servers_for(self, thread: str) -> list[dict[str, Any]]:
        """The MCP server entries for *thread*'s session: its id in their env, so the
        bridge knows who calls (``BFACW_THREAD_ID``)."""
        if not thread:
            return self._servers
        out = []
        for server in self._servers:
            server = dict(server)
            env = [e for e in server.get("env") or [] if e.get("name") != "BFACW_THREAD_ID"]
            server["env"] = env + [{"name": "BFACW_THREAD_ID", "value": thread}]
            out.append(server)
        return out

    # -- the agent's own options (Agent 8): mode, model, effort -----------------------
    def pickers(self) -> list[dict[str, Any]]:
        """The options worth a picker in the chat, with the choices that are safe now.

        While *only_coworker_tools* is on, modes that skip the agent's permission
        questions (Claude Code's Accept edits / Auto / Bypass) are left out: Coworker
        refuses the agent's own tools when it asks, so it must keep asking."""
        out = []
        for option in self.config_options:
            if option.get("type", "select") != "select" or option.get("category") not in PICKER_CATEGORIES:
                continue
            choices = [(str(c.get("value", "")), str(c.get("name") or c.get("value", "")), str(c.get("description", "")))
                       for c in option.get("options") or [] if c.get("value") is not None]
            if option.get("category") == "mode" and self.only_coworker_tools:
                choices = [c for c in choices if not _SKIPS_QUESTIONS.search(" ".join(c))]
            if len(choices) < 2:
                continue
            current = str(option.get("currentValue", ""))
            out.append({"id": str(option.get("id", "")), "name": str(option.get("name") or option.get("id", "")),
                        "category": option.get("category"), "current": current,
                        "current_name": next((n for v, n, _d in choices if v == current), current),
                        "choices": choices})
        out.sort(key=lambda p: PICKER_CATEGORIES.index(p["category"]))
        return out

    def set_option(self, config_id: str, value: str, wait: bool = False) -> None:
        """Change one of the agent's options (in the background); *on_option* hears it."""
        client, session_id = self.client, self.session_id
        if client is None or not self.connected:
            return

        def work() -> None:
            try:
                result = client.set_config_option(session_id, config_id, value)
                if result.get("configOptions"):
                    self.config_options = list(result["configOptions"])
                else:
                    for option in self.config_options:
                        if option.get("id") == config_id:
                            option["currentValue"] = value
                self.preferred[config_id] = value
                if self.on_option is not None:
                    self.on_option(config_id, value)
            except AcpError as ex:
                self.status = "Couldn't change {:s}: {:s}".format(config_id, str(ex).splitlines()[0])
            self._changed()
        worker = threading.Thread(target=work, daemon=True, name="acp-option")
        worker.start()
        if wait:
            worker.join(timeout=5.0)        # never hang Bforartists on a stuck agent

    def _apply_preferred(self, client: AcpClient, session_id: str) -> None:
        """Your earlier picks (model, mode...) again in a new session, where still offered."""
        allowed = {p["id"]: {v for v, _n, _d in p["choices"]} for p in self.pickers()}
        for config_id, value in list(self.preferred.items()):
            current = next((str(o.get("currentValue", "")) for o in self.config_options if o.get("id") == config_id), None)
            if current is None or current == value or value not in allowed.get(config_id, ()):
                continue
            try:
                result = client.set_config_option(session_id, config_id, value)
                self.config_options = list(result.get("configOptions") or self.config_options)
            except AcpError:
                pass     # not offered any more: keep the agent's default

    def new_session(self, wait: bool = False) -> None:
        """New Thread in All editors: the agent forgets the conversation too (same agent, a
        new session for the default thread; #359: another chat's turn may be current)."""
        client = self.client
        if client is None or not self.connected:
            return
        self.cancel_thread("")
        if not self.busy_for(self.thread):
            self.use_thread("")

        def work() -> None:
            try:
                self._set_session("", self._open_session(client, False, "", self._cwd, self._servers_for(""),
                                                         thread=""))
            except AcpError as ex:
                self.status = "Couldn't start a fresh session: {:s}".format(str(ex).splitlines()[0])
            self._changed()
        worker = threading.Thread(target=work, daemon=True, name="acp-new-session")
        worker.start()
        if wait:
            worker.join()

    def switch_session(self, resume: str = "", wait: bool = False) -> None:
        """The default thread shows another chat (Chat 7; a .blend opened, #431): continue
        that chat's own agent session, or a fresh one."""
        client = self.client
        if client is None or not self.connected:
            return
        self.cancel()
        self.use_thread("")

        def work() -> None:
            try:
                self.session_id = self._open_session(client, self.caps.load_session, resume, self._cwd,
                                                     self._servers_for(self.thread))
            except AcpError as ex:
                self.status = "Couldn't switch the conversation: {:s}".format(str(ex).splitlines()[0])
            self._changed()
        worker = threading.Thread(target=work, daemon=True, name="acp-switch-session")
        worker.start()
        if wait:
            worker.join()

    def disconnect(self) -> None:
        client, self.client = self.client, None
        self.connected, self.session_id = False, ""
        self._queue.clear()
        self.approvals.end_session()
        with self._lock:
            sessions = [slot.get("session", "") for slot in self._running.values()]
        if client is not None:
            for session in sessions:
                client.cancel(session)
            client.close()
        self.status = "Not connected"
        self._changed()

    # -- the chat's engine interface (like LoopEngine) ------------------------------
    def is_busy(self) -> bool:
        """Some chat's turn runs."""
        return bool(self._running)

    def running_threads(self) -> list[str]:
        """The threads (tags) whose turn runs now, in the order they started (#359)."""
        with self._lock:
            return list(self._running)

    def busy_for(self, thread: str) -> bool:
        """*thread*'s own turn runs now."""
        return thread in self._running

    def blocked_for(self, thread: str) -> bool:
        """*thread*'s message has to wait for a free slot (other chats' turns hold them)."""
        with self._lock:
            return thread not in self._running and len(self._running) >= max(1, int(self.cap or 1))

    def submit(self, prompt: Prompt, _config: Any = None) -> tuple[str | None, int]:
        """Start a turn, or queue it while its chat's turn runs or every slot is taken:
        ``(turn_id, 0)`` / ``(None, position)``."""
        with self._lock:
            if self.busy_for(prompt.thread) or self.blocked_for(prompt.thread):
                return None, self.enqueue(prompt)
            return self._start(prompt), 0

    def enqueue(self, prompt: Prompt, _config: Any = None) -> int:
        self._queue.append(prompt)
        self._changed()
        return len(self._queue)

    def queue_items(self) -> list[dict[str, Any]]:
        """The queued messages, in the turn loop's queue shape (for the Queue panel)."""
        return [{"message": p.text, "chat_mode": p.chat_mode, "attachments": list(p.images)}
                for p in list(self._queue)]

    def clear_queue(self) -> None:
        self._queue.clear()
        self._changed()

    def remove_queued(self, index: int) -> Prompt | None:
        """Take queued message *index* out (#197)."""
        if not 0 <= index < len(self._queue):
            return None
        items = list(self._queue)
        prompt = items.pop(index)
        self._queue.clear()
        self._queue.extend(items)
        self._changed()
        return prompt

    def queue_to_front(self, index: int) -> bool:
        """Queued message *index* goes next (#197)."""
        prompt = self.remove_queued(index)
        if prompt is None:
            return False
        self._queue.appendleft(prompt)
        self._changed()
        return True

    def cancel(self) -> None:
        """Stop in the current thread's chat."""
        self.cancel_thread(self.thread)

    def cancel_thread(self, thread: str) -> None:
        """Stop in *thread*'s chat (#359): its waiting messages go, its questions are
        declined, and its running turn's session -- only that one -- is cancelled."""
        self.drop_queued(thread)                # this thread's waiting messages only (#358)
        self.approvals.cancel_all(thread)
        with self._lock:
            slot = self._running.get(thread)
        if self.client is not None and slot is not None and slot.get("turn"):
            self.client.cancel(str(slot.get("session") or self.session_id))
            # An agent stuck in a tool may never answer Stop: then Coworker lets it go.
            timer = threading.Timer(self.stop_grace, self._stop_overdue, (slot["turn"], self.client, thread))
            timer.daemon = True
            timer.start()

    def _stop_overdue(self, turn_id: str, client: AcpClient, thread: str = "") -> None:
        with self._lock:
            slot = self._running.get(thread)
            if slot is None or slot.get("turn") != turn_id or self.client is not client:
                return                  # it stopped in time
            others = [t for t in self._running if t != thread]
            slot["forced"] = True
            if others:
                # Closing the agent would end the other chats' turns too: this chat's turn
                # ends here instead, and its late answer is ignored (#359).
                slot["abandoned"] = True
                self._running.pop(thread, None)
        if others:
            from . import claims
            claims.TABLE.release(thread)
            self.bus.emit("error", turn_id, message=self._forced_message(closed=False))
            self.bus.emit("turn_end", turn_id, stop_reason="error")     # the chat shows why
            self._changed()
            self._start_next()
            return
        client.close(timeout=2.0)      # the turn's prompt then fails and ends the turn

    def _forced_message(self, closed: bool = True) -> str:
        who = self.agent_label or "The agent"
        if closed:
            return "{:s} didn't stop when asked, so Coworker closed it. Press Start to carry on.".format(who)
        return ("{:s} didn't stop this chat's turn when asked, so Coworker let it go; the other chats "
                "keep working.".format(who))

    def stop(self) -> None:
        self.cancel()

    def drain(self) -> int:
        """Apply the agent's events to the chat state (main thread): each turn's to the
        chat of the thread it ran in (#357)."""
        return self.bus.drain_to(self._apply)

    def _apply(self, event: Any) -> None:
        applier = self._turn_appliers.get(event.turn_id, self.applier)
        applier.apply(event)
        if event.kind == "turn_end":
            self._turn_appliers.pop(event.turn_id, None)
            self._turn_threads.pop(event.turn_id, None)

    def turn_thread(self, turn_id: str) -> str:
        """The thread (tag) a turn ran in (#359: several chats' turns run at once, so the
        current :attr:`thread` may be another's).  Valid until its ``turn_end`` is applied."""
        return self._turn_threads.get(turn_id, self.thread)

    # -- turns ------------------------------------------------------------------------
    def _start(self, prompt: Prompt) -> str:
        turn_id = new_turn_id()
        tag = prompt.thread
        with self._lock:
            self.use_thread(tag)
            slot = {"turn": turn_id, "session": self.session_id, "applier": self.applier,
                    "mode": prompt.chat_mode, "forced": False}
            self._running[tag] = slot
        self._turn_appliers[turn_id] = slot["applier"]
        self._turn_threads[turn_id] = tag
        # Like a local turn, the message in the chat keeps an "[attached: ...]"
        # marker for its images, which the chat draws as thumbnails.
        names = [n for n in prompt.image_names if n]
        shown = prompt.text + ("\n[attached: {:s}]".format(", ".join(names)) if names and prompt.images else "")
        self.bus.emit("turn_start", turn_id, text=shown, mode=prompt.chat_mode)
        if prompt.chat_mode != "ASK":     # a question is no goal
            if self.on_thread_request is not None:
                self.on_thread_request(tag, prompt.text)
            elif self.on_request is not None:
                self.on_request(prompt.text)
        threading.Thread(target=self._run, args=(turn_id, prompt, slot), daemon=True, name="acp-turn").start()
        return turn_id

    def _start_next(self) -> None:
        """Start queued messages while slots are free: each chat's oldest, in queue order,
        skipping chats whose turn runs (#359)."""
        while self.connected:
            with self._lock:
                if len(self._running) >= max(1, int(self.cap or 1)):
                    return
                nxt = next((p for p in self._queue if p.thread not in self._running), None)
                if nxt is None:
                    return
                self._queue.remove(nxt)
                self._start(nxt)

    def _blocks(self, prompt: Prompt) -> list[dict[str, Any]]:
        text = prompt.text
        if self.is_command(text):
            # The agent's own slash command goes as typed (Agent 8b): nothing appended.
            return [{"type": "text", "text": text}]
        if prompt.context:
            text = "{:s}\n\n[Blender: {:s}]".format(text, prompt.context)
        if prompt.chat_mode == "ASK":
            text += "\n\n[Answer only; don't change the Blender scene.]"
        elif prompt.chat_mode == "PLAN":
            text += "\n\n" + PLAN_NOTE
        blocks: list[dict[str, Any]] = [{"type": "text", "text": text}]
        if self.caps.images:
            for uri in prompt.images:
                head, _, data = str(uri).partition(",")
                if head.startswith("data:") and ";base64" in head and data:
                    mime = head[5:].split(";")[0] or "image/png"
                    try:
                        base64.b64decode(data[:64] + "=" * (-len(data[:64]) % 4), validate=False)
                    except ValueError:
                        continue
                    blocks.append({"type": "image", "mimeType": mime, "data": data})
        return blocks

    def _run(self, turn_id: str, prompt: Prompt, slot: dict[str, Any] | None = None) -> None:
        tag = prompt.thread
        slot = slot if slot is not None else self._running.get(tag, {})
        stop_reason = "end_turn"
        try:
            if self.client is None or not self.connected:
                raise AcpError("not connected -- press Start first")
            session = str(slot.get("session") or "")
            if not session:         # this thread's first turn: its own session (#357)
                # ... continuing the session its chat had, when there was one (#431).
                resume = self.resume_for(tag) if self.resume_for is not None else ""
                session = self._open_session(self.client, self.caps.load_session, resume, self._cwd,
                                             self._servers_for(tag), thread=tag)
                self._set_session(tag, session)
            result = self.client.prompt(session, self._blocks(prompt), timeout=None)
            stop_reason = str(result.get("stopReason") or "end_turn")
        except AcpError as ex:
            stop_reason = "error"
            if slot.get("abandoned"):
                pass
            elif slot.get("forced"):
                self.bus.emit("error", turn_id, message=self._forced_message())
            else:
                self.bus.emit("error", turn_id, message=self._explain(ex))
            if self.client is not None and not self.client.alive:
                self.connected = False
                self.status = "The agent stopped -- press Start to reconnect"
        finally:
            if not slot.get("abandoned"):   # an abandoned turn already ended (#359)
                from . import claims
                claims.TABLE.release(tag)   # what it was changing is free again (#359)
                self.approvals.cancel_all(tag)   # a question left from this turn has no one to answer
                self.bus.emit("turn_end", turn_id, stop_reason=stop_reason)
                with self._lock:
                    if self._running.get(tag) is slot or self._running.get(tag, {}).get("turn") == turn_id:
                        self._running.pop(tag, None)
                self._changed()
        self._start_next()

    # -- from the agent (reader thread) -----------------------------------------------
    def _on_update(self, params: dict[str, Any]) -> None:
        """One ``session/update``: *params* (with its ``sessionId``, #359), or the bare
        update (the session then is the client's ``update_session``)."""
        has_params = isinstance(params.get("update"), dict)
        update = params["update"] if has_params else params
        if update.get("sessionUpdate") == "config_option_update" and update.get("configOptions"):
            self.config_options = list(update["configOptions"])     # the agent changed one itself
            self._changed()
        client = self.client
        session = str(params.get("sessionId") or "") if has_params else ""
        if not session and client is not None:
            session = str(getattr(client, "update_session", "") or "")
        _tag, slot = self._slot_for_session(session)
        if slot is None or not slot.get("turn") or slot.get("abandoned"):
            return      # no turn runs in that session (a cancelled turn's late chunks) (#357)
        for kind, data in update_to_events(update):
            self.bus.emit(kind, slot["turn"], **data)
        self._changed()

    def commands(self) -> list[dict[str, str]]:
        """The agent's own slash commands (ACP ``available_commands_update``)."""
        client = self.client
        if client is None or not self.connected:
            return []
        out = []
        for command in client.commands:
            name = str(command.get("name", "")).lstrip("/")
            if name:
                out.append({"name": name, "description": str(command.get("description", "")),
                            "hint": str((command.get("input") or {}).get("hint", ""))})
        return out

    def is_command(self, text: str) -> bool:
        """*text* starts with one of the agent's slash commands."""
        if not text.startswith("/"):
            return False
        word = text[1:].split(None, 1)[0] if text[1:].strip() else ""
        return any(c["name"] == word for c in self.commands())

    def _explain(self, ex: Exception) -> str:
        """What went wrong, for you: a login hint when it's about logging in; else the
        message, with the agent's last output lines (if any) as a code block."""
        text = str(ex).strip() or "error"
        first, _, rest = text.partition("\n")
        if _LOGIN_WORDS.search(text):
            return "{:s} isn't logged in. {:s}".format(
                self.agent_label or "The agent", self.login_hint or "Log in to it once in a terminal.")
        return first + ("\n\n```\n{:s}\n```".format(rest.strip()) if rest.strip() else "")

    def _on_permission(self, params: dict[str, Any], respond: Callable[[str | None], None]) -> None:
        """The agent asks before a tool: answer by the settings, or ask you in the chat."""
        call = params.get("toolCall") or {}
        title = str(call.get("title") or call.get("kind") or "")
        options = params.get("options") or []

        def answer(choice: str) -> None:
            # Our choice -> the agent's option ids.  Never the agent's own "allow_always":
            # Claude Code would save it for good (.claude/settings.local.json) and stop
            # asking; Coworker remembers "Always" itself, for this session only.
            wanted = {"once": ("allow_once",), "always": ("allow_once",),
                      "deny": ("reject_once", "reject_always")}.get(choice, ())
            pick = next((o for kind in wanted for o in options if o.get("kind") == kind), None)
            respond(str(pick.get("optionId")) if pick else None)

        # Whose turn asks (#359): the request's session's chat; without one, the current.
        found, slot = self._slot_for_session(str(params.get("sessionId") or ""))
        thread = found if slot is not None else self.thread
        turn = str(slot.get("turn", "")) if slot is not None else ""
        mode = str(slot.get("mode", "AGENT")) if slot is not None else self._turn_mode
        # OpenCode: "bfa-coworker_<tool>"; Claude Code: "mcp__bfa-coworker__<tool>".
        ours = MCP_SERVER_NAME in title or "blender" in title.lower()
        tool = _tool_name(title) if ours else title
        if not ours and self.only_coworker_tools:
            answer("deny")
            if turn:
                self.bus.emit("status", turn, text="Refused {:s}: the agent can only use "
                              "Coworker's Blender tools".format(title or "a tool"))
            return
        if ours and mode == "PLAN" and tool not in PLAN_SAFE_TOOLS:
            answer("deny")              # Plan mode (#194): looking is fine, changing isn't
            if turn:
                self.bus.emit("status", turn, text="Plan mode: {:s} not run (it would change "
                              "the scene)".format(tool))
            return
        if (ours and not self.ask_coworker_tools) or self.approvals.allowed_always(tool, thread)                 or self.never_ask:
            answer("once")
            return
        if self.auto_approve or thread in self.auto_threads:     # Auto mode (#177, #417)
            answer("once")
            if turn:
                self.bus.emit("status", turn, text="Auto mode allowed {:s}".format(tool or "a tool"))
            return
        request = ApprovalRequest(tool=tool or "a tool", source="Coworker", args=call.get("rawInput"),
                                  coworker_tool=ours, thread=thread)
        if turn:
            self.bus.emit("status", turn, text="Waiting for you: allow {:s}?".format(request.tool))
        self.approvals.ask(request, answer)
        self._changed()

    def _changed(self) -> None:
        if self.on_change is not None:
            try:
                self.on_change()
            except Exception:  # pylint: disable=broad-exception-caught
                pass
