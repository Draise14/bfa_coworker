# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Helper chats in the add-on (Tier 4, #479): the glue between ``delegate_task``,
the chat tabs and the turn queue.  The bookkeeping is :mod:`helpers` (bpy-free).

Built only on the thread machinery's public entry points, so the thread runner
(Threads 3, #359) can replace the one-turn-at-a-time rule underneath:

* ``ui_chat._editor_thread`` opens the helper's tab (a new thread, not shown in
  any area: the user's view stays where it is);
* ``ui_chat._thread_engine(thread).submit / .enqueue`` start or queue a message in
  a thread (``agent_controller.turn_blocked`` decides, like Send does);
* our loop: ``LoopEngine.on_turn_finished`` (worker thread, before the next
  queued message starts) with ``last_stop_reason`` / ``last_error``; the results
  message goes into the parent's queue (``agent_controller.thread_queue``) there,
  so the normal queue starts the parent's turn;
* External Agent: ``AcpEngine.on_turn_end(thread, stop_reason, error)`` (main
  thread, from drain).

Delegation never blocks the asking turn: :func:`delegate` queues the task and
answers at once.
"""

from __future__ import annotations

__all__ = (
    "card",
    "delegate",
    "acp_turn_ended",
    "loop_turn_finished",
    "stop_helpers",
    "submit",
    "tab_closed",
    "tab_label",
    "tab_tip",
    "waiting",
)

from typing import Any

import bpy  # pylint: disable=import-error

from . import agent_controller, helpers, threads
from .helpers import HELPERS


def _ui():
    from . import ui_chat
    return ui_chat


def _prefs() -> Any:
    try:
        return bpy.context.preferences.addons[__package__].preferences
    except (AttributeError, KeyError):
        return None


def _thread(thread_id: str) -> Any:
    """The live thread *thread_id* ("" / "main" = the default one), or None."""
    return agent_controller.THREADS.get(thread_id or None)


def _later(fn, delay: float = 0.0) -> None:
    """*fn* on the main thread, soon (also from a worker: the add-on does so elsewhere)."""
    def run() -> None:
        try:
            fn()
        except Exception as ex:  # pylint: disable=broad-exception-caught
            print("[Coworker] helper chats: {:s}".format(str(ex)))
        return None
    bpy.app.timers.register(run, first_interval=delay)


def _redraw() -> None:
    try:
        _ui()._redraw_all_chats()
    except Exception:  # pylint: disable=broad-exception-caught
        pass


# -- starting or queueing a message in a thread (main thread) --------------------------
def send_config() -> tuple[str, Any]:
    """``(mode, LoopConfig)``: the engine settings a message sent now uses, read like Send
    does (main thread)."""
    from . import llm_manager
    from .engine_loop import LoopConfig
    from .shared import effective_ports
    prefs = _prefs()
    if prefs is not None:
        _ui()._sync_prefs_to_config(prefs)
    cfg = llm_manager.get_config()
    llm_url = api_key = model = None
    if cfg.mode == "remote":
        llm_url, api_key, model = cfg.remote_api_url, cfg.remote_api_key, cfg.remote_model or None
    mcp_port = 0
    if prefs is not None:
        _bridge, mcp_port, _llm = effective_ports(prefs)
    mcp_port = agent_controller._agent_state.mcp_port_actual or mcp_port
    return cfg.mode, LoopConfig(llm_url=llm_url or None, api_key=api_key or None, model=model, mcp_port=mcp_port)


def submit(thread: Any, text: str, config: Any = None) -> str:
    """Send *text* (a message Coworker writes itself) in *thread*: started at once when
    nothing runs, else queued like Send does.  Returns ``"started"`` or ``"queued"``.
    Unlike Send it never touches the attachment socket or the draft (the user's view)."""
    from . import coworker_api
    from .engine import Prompt
    ui = _ui()
    mode, fresh = send_config()
    config = config or fresh
    external = ui._is_external_agent()
    engine = ui._thread_engine(thread, mode)
    prompt = Prompt(text=text, chat_mode="AGENT", thread=threads.tag(thread))
    agent_controller.prefetch_main_thread_context(thread)
    busy = engine.is_busy() and not agent_controller.thread_stop(thread).is_set()
    if busy or (not external and agent_controller.turn_blocked(thread)):
        engine.enqueue(prompt, config)
        _redraw()
        return "queued"
    state = agent_controller.thread_state(thread)
    state.turn_phase = "reading"
    state.ui_status = "Reading your message..."
    coworker_api.turn_started(ui._turn_label(None, thread))
    ui._approvals_new_turn(thread)
    engine.submit(prompt, config)
    _redraw()
    return "started"


# -- delegate_task (main thread, the bridge) ------------------------------------------------
def _chat_title(thread: Any) -> str:
    """The asking chat's name for its helpers: its first message, else its tab's label."""
    from .chat_history import title_of
    try:
        return title_of(agent_controller.thread_state(thread).conversation_history) or _ui()._tab_label(thread)
    except Exception:  # pylint: disable=broad-exception-caught
        return _ui()._tab_label(thread)


def _turn_key(parent: Any) -> str:
    """Which of the parent's turns this is (the cap counts per turn)."""
    acp = _ui()._acp
    acp_turn = getattr(acp, "_turn_id", "") if acp is not None and acp.thread == threads.tag(parent) else ""
    return "{:d}:{:s}".format(int(getattr(agent_controller.thread_state(parent), "turn_token", 0)), acp_turn)


def delegate(caller: str, args: dict[str, Any]) -> dict[str, Any]:
    """``delegate_task`` for the thread tagged *caller*: open the helper's tab, queue its
    task there and answer at once."""
    def error(message: str) -> dict[str, Any]:
        return {"status": "error", "message": message}
    prefs = _prefs()
    mode = getattr(prefs, "operating_mode", "")
    if mode == "EXTERNAL_HARNESS":
        return error("In External Harness the chat runs in your own client: use its own sub-agents "
                     "(they can all call Coworker's tools) instead of delegate_task.")
    parent = _thread(caller)
    if parent is None:
        return error("The chat that asked is closed, so no helper was opened.")
    task = str(args.get("task") or "").strip()
    if not task:
        return error("Give the helper a task: what to do, complete on its own (names, values, where).")
    editor = helpers.parse_editor(args.get("editor"))
    if editor is None:
        return error("Unknown editor {!r}: use one of {:s}, or \"\" for All editors.".format(
            str(args.get("editor")), ", ".join(helpers.EDITOR_KINDS)))
    cap = int(getattr(prefs, "helpers_per_turn", helpers.DEFAULT_CAP) or helpers.DEFAULT_CAP)
    turn_key = _turn_key(parent)
    why = HELPERS.refusal(parent.id, turn_key, cap)
    if why:
        return error(why)
    ui = _ui()
    title = helpers.short_title(args.get("title") or task)
    parent_title = _chat_title(parent)
    helper = ui._editor_thread(editor)          # its own tab; no area shows it (your view stays)
    helper.title = title
    message = helpers.framing(parent_title, task, editor)
    record = HELPERS.add(parent.id, helper.id, title=title, editor=editor, task=task, turn_key=turn_key,
                         message=message, parent_title=parent_title)
    _mode, record.config = send_config()        # the results go back with these settings (worker)
    how = submit(helper, message, record.config)
    print("[Coworker] helper chat {:s} ({:s}) for {:s}: {:s}".format(helper.id, title, parent_title, how))
    ui._save_tabs()
    local = mode == "LOCAL_LLM"
    return {"status": "ok", "helper_id": helper.id, "title": title,
            "message": ("Helper \"{:s}\" opened in its own tab ({:s}) and {:s}. Its result comes back to this "
                        "chat as a message once all your helpers have finished: don't wait or poll for it. "
                        "Do any other independent part, then end your turn.{:s}").format(
                title, helpers.EDITOR_KINDS.get(editor, "All editors"),
                "is starting" if how == "started" else "is queued",
                " On Local the helpers run one after another, after this turn." if local else "")}


# -- a helper's turn ended -------------------------------------------------------------
def _ended(thread: Any, stop_reason: str, error: str) -> str:
    """Record the helper's end; its parent's id when the parent's batch just finished."""
    history = agent_controller.thread_state(thread).conversation_history
    return HELPERS.ended(thread.id, stop_reason, helpers.last_answer(history), error)


def loop_turn_finished(thread: Any) -> None:
    """Our loop, on the turn's WORKER thread, before the next queued message starts:
    when this finished a parent's helpers, their results go into the parent's queue now,
    so the normal queue runs the parent next."""
    helper = HELPERS.get(getattr(thread, "id", ""))
    if helper is None or helper.finished:
        return
    engine = thread.engine
    stop_reason = getattr(engine, "last_stop_reason", "") or "end_turn"
    history = agent_controller.thread_state(thread).conversation_history
    if stop_reason != "cancelled" and not helpers.asked(history, helper.message):
        # Its turn never ran (another tab's turn held the gate): send the task again.
        _later(lambda: _requeue(helper.id), 0.5)
        return
    parent_id = _ended(thread, stop_reason, getattr(engine, "last_error", ""))
    if not parent_id:
        return
    parent = _thread(parent_id)
    batch = HELPERS.take_results(parent_id)
    if parent is None or not batch:
        return
    config = helper.config or getattr(engine, "config", None)
    agent_controller.thread_queue(parent).enqueue(
        message=helpers.results_text(batch), chat_mode="AGENT",
        llm_url=getattr(config, "llm_url", None), api_key=getattr(config, "api_key", None),
        model=getattr(config, "model", None), mcp_port=getattr(config, "mcp_port", 0))
    print("[Coworker] helper chats: {:d} result(s) queued for {:s}".format(len(batch), parent_id))
    _later(_redraw)


def _requeue(helper_id: str) -> None:
    helper = HELPERS.get(helper_id)
    thread = _thread(helper_id)
    if helper is None or helper.finished or thread is None:
        return
    if agent_controller.thread_queue(thread).pending_count or agent_controller.thread_state(thread).turn_active:
        return
    submit(thread, helper.message)


def acp_turn_ended(tag: str, stop_reason: str, error: str) -> None:
    """External Agent, main thread (drain): the turn's events are in its chat now."""
    thread = _thread(tag)
    if thread is None or not HELPERS.is_helper(thread.id):
        return
    parent_id = _ended(thread, stop_reason, error)
    if parent_id:
        report(parent_id)


def report(parent_id: str) -> None:
    """Main thread: the parent's finished batch goes to it as one message."""
    parent = _thread(parent_id)
    batch = HELPERS.take_results(parent_id)
    if parent is None or not batch:
        return
    how = submit(parent, helpers.results_text(batch))
    print("[Coworker] helper chats: {:d} result(s) for {:s} ({:s})".format(len(batch), parent_id, how))


# -- Stop and closing tabs (main thread) ----------------------------------------------------
def _stop_thread(thread: Any) -> None:
    """Stop *thread*'s running turn and drop its waiting messages."""
    from . import coworker_api, risky_steps
    ui = _ui()
    tag = threads.tag(thread)
    coworker_api.cancel_questions(tag)
    risky_steps.APPROVALS.cancel_all(tag)
    acp = ui._acp
    if ui._is_external_agent() and acp is not None:
        if acp.thread == tag and acp.is_busy():
            acp.cancel()                    # its turn, and its waiting messages
        else:
            acp.drop_queued(tag)
        return
    agent_controller.thread_queue(thread).clear()
    if agent_controller.thread_state(thread).turn_active:
        ui._thread_engine(thread).cancel()


def stop_helpers(parent: Any) -> int:
    """Stop in the asking chat: its unfinished helpers stop too (no results message)."""
    stopped = HELPERS.stop_parent(parent.id)
    for helper in stopped:
        thread = _thread(helper.id)
        if thread is not None:
            _stop_thread(thread)
            agent_controller.thread_state(thread).status_text = "Stopped"
    if stopped:
        print("[Coworker] helper chats: stopped {:d} helper(s) of {:s}".format(len(stopped), parent.id))
        _redraw()
    return len(stopped)


def tab_closed(thread: Any) -> None:
    """A tab closes: a helper counts as stopped (its waiting task goes); a parent's
    helpers carry on as plain chats."""
    if HELPERS.is_helper(thread.id):
        helper = HELPERS.get(thread.id)
        if helper is not None and not helper.finished:
            _stop_thread(thread)
    parent_id = HELPERS.closed(thread.id)
    if parent_id:
        report(parent_id)


# -- what the chat shows ------------------------------------------------------------------
def waiting(thread: Any) -> int:
    """How many of *thread*'s helpers haven't finished."""
    return len(HELPERS.waiting(thread.id)) if thread is not None else 0


def _status_of(helper: helpers.Helper) -> tuple[str, str]:
    """``(word, icon)`` for a helper's row."""
    if helper.finished:
        return {helpers.DONE: ("done", "CHECKMARK"), helpers.FAILED: ("failed", "ERROR")}.get(
            helper.status, ("stopped", "CANCEL"))
    thread = _thread(helper.id)
    if thread is not None:
        word, _icon = _ui()._thread_status(thread)
        if word == "Waiting for you":
            return "waiting for you", "QUESTION"
        if word == "Thinking":
            return "running", "SORTTIME"
    return "queued", "TIME"


def card(thread: Any) -> Any:
    """The asking chat's Helpers card while any helper is unfinished, else None."""
    batch = HELPERS.batch(thread.id) if thread is not None else []
    if not batch or all(h.finished for h in batch):
        return None
    from .ui_gpu.surface import Helpers
    rows = []
    for helper in batch:
        word, icon = _status_of(helper)
        rows.append((helper.id, helper.title, word, icon))
    left = sum(1 for h in batch if not h.finished)
    title = "{:d} helper{:s} — {:d} working".format(len(batch), "" if len(batch) == 1 else "s", left)
    return Helpers(title=title, rows=tuple(rows))


def tab_label(thread: Any) -> str:
    """A helper tab's label (its title), or "" for any other tab."""
    helper = HELPERS.get(getattr(thread, "id", ""))
    return helper.title if helper is not None else ""


def tab_tip(thread: Any) -> str:
    """"Helper for <parent>: " for a helper tab's tooltip, else ""."""
    helper = HELPERS.get(getattr(thread, "id", ""))
    return "Helper for \"{:s}\": ".format(helper.parent_title) if helper is not None else ""
