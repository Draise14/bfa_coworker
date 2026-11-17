# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Loop guard: the same tool call again and again (Tier 4, Runtime 1, #310).

A model stuck in a loop repeats one call (same tool, same input) and wastes
minutes, tokens and sometimes the scene.  One rule for every engine:

- the **3rd** identical call in a turn asks you first with the approval card
  ("It keeps doing the same thing: let it try again, or stop?");
- the **4th** is refused with a short tool error, without running;
- a read that changes nothing (``engine.PLAN_SAFE_TOOLS``) gets more room
  (asks on the 5th), and any other call in between starts its count again:
  reading the digest after every step is normal, reading it five times in a
  row without doing anything is not;
- a long job's status poll (``get_job_status``) is never counted;
- a tool whose answer also depends on state outside its input (settings,
  installed files: :data:`_CONTEXT_MODULES`) is counted with that state, so
  a call made after it changed is a new call -- transcribing a strip again
  after setting speech up or picking another model is not a loop (#499);
- a render that failed counts like any repeat when it is tried again with only
  another file name (``output_path``): the render is the same, and so is its
  cost (#527).  :meth:`LoopGuard.note_result` hears how each render ended; one
  that worked doesn't count toward a render to another file;
- in Auto mode nobody can answer: the 3rd call is declined at once;
- an External Harness client has its chat outside Blender, so nobody is at
  the card either: the 3rd call is refused at once with a plain text instead
  of waiting for an answer that never comes (#499).

The local loop (Local / Remote) keeps its own :class:`LoopGuard` per turn and
waits for the answer on its worker thread (:func:`wait_answer`).  External
Agent and External Harness calls are counted at the bridge
(:data:`BRIDGE`, :func:`bridge_wrap`), per session, and reset when a turn
starts; a Harness client has no turns in Blender, so a pause of
:data:`_BRIDGE_IDLE_RESET` seconds counts as a new one.

bpy-free and thread-safe.
"""

from __future__ import annotations

__all__ = (
    "ASK",
    "BRIDGE",
    "REFUSE",
    "RUN",
    "LoopGuard",
    "bridge_watch",
    "bridge_wrap",
    "decide",
    "repeat_request",
    "signature",
    "wait_answer",
)

import functools
import hashlib
import importlib
import json
import re
import threading
import time
from typing import Any, Callable

from .answer_envelope import answer_of, answers_in_full
from .approvals import ApprovalRequest, Approvals

RUN, ASK, REFUSE = "run", "ask", "refuse"

REPEAT_ASK = 3          # the 3rd identical call asks; the 4th is refused
READ_REPEAT_ASK = 5     # reads that change nothing get more room

# #591: a model that goes on polling get_job_status after being told "stuck" this many times in a
# row is waiting on a job that goes nowhere: the next poll is refused.  Only "stuck" answers count
# (a render quiet for a while but "running" is never refused).
STALE_POLLS = 3

_BRIDGE_IDLE_RESET = 60.0
_ORDINALS = {1: "1st", 2: "2nd", 3: "3rd"}


def _ordinal(n: int) -> str:
    return _ORDINALS.get(n, "{:d}th".format(n))


def signature(tool: str, args: Any) -> str:
    """Tool name + its input with sorted keys, hashed (identical calls match)."""
    try:
        text = json.dumps(args, sort_keys=True, default=str, ensure_ascii=False)
    except (TypeError, ValueError):
        text = repr(args)
    return hashlib.sha1("{:s}\0{:s}".format(str(tool or ""), text).encode("utf-8")).hexdigest()


def _is_read(tool: str) -> bool:
    from .engine import PLAN_SAFE_TOOLS
    return tool in PLAN_SAFE_TOOLS


def _is_exempt(tool: str) -> bool:
    from .long_jobs import is_status_poll
    return is_status_poll(tool)      # waiting for a render repeats its poll (Tools 6)


# Tools whose answer also depends on state outside their input: the module named here has
# ``repeat_context() -> str`` (cheap, bpy-safe on any thread).  A call made after that state
# changed is a new call, not a repeat; the same call with the same state still is (#499).
_CONTEXT_MODULES: dict[str, str] = {
    "transcribe_strip": "speech",     # the speech settings, the builds and models installed
}


def _context(tool: str) -> str:
    """The state *tool*'s answer depends on besides its input ("" for most tools)."""
    name = _CONTEXT_MODULES.get(tool)
    if not name:
        return ""
    try:
        return str(importlib.import_module("." + name, __package__).repeat_context() or "")
    except Exception:  # pylint: disable=broad-exception-caught  # count it as before
        return ""


# Render tools and the arguments that only name the file (#527): a render that failed, tried
# again under another name, is the same render.
_RENDER_TOOLS: dict[str, tuple[str, ...]] = {
    "render_viewport_to_path": ("output_path",),
    "render_thumbnail_to_path": ("output_path",),
}
# The bridge counts a tool's code, its arguments written in as ``Params(output_path='...', ...)``.
_PATH_IN_CODE = re.compile(r"""\boutput_path=(?:'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*")""")
_ERROR_IN_TEXT = re.compile(r'"status"\s*:\s*"error"')


def _render_identity(tool: str, args: Any) -> str:
    """A render's input without its file name ("" for other tools)."""
    names = _RENDER_TOOLS.get(tool)
    if not names or not isinstance(args, dict):
        return ""
    if set(args) == {"code"} and isinstance(args["code"], str):
        return signature(tool, {"code": _PATH_IN_CODE.sub("output_path=?", args["code"])})
    return signature(tool, {k: v for k, v in args.items() if k not in names})


def _failed(result: Any) -> bool:
    """Did the call fail?  *result* is the answer (the bridge's envelope or the tool's own
    dict), or its JSON text (the local loop's).  A stopped render is not a failure."""
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except ValueError:
            return bool(_ERROR_IN_TEXT.search(result[:600]))
    if not isinstance(result, dict):
        return False
    if result.get("status") == "error":
        return True
    inner = result.get("result")
    return isinstance(inner, dict) and inner.get("status") == "error"


def _poll_answer(result: Any) -> dict[str, Any] | None:
    """The job status inside a status poll's answer (the bridge's envelope, the tool's own
    dict, or their JSON text), or None when it is no status."""
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except ValueError:
            return None
    for _ in range(2):
        if not isinstance(result, dict):
            return None
        if "state" in result:
            return result
        result = result.get("result")
    return None


class LoopGuard:
    """Counts identical calls per key (a session) until the turn ends."""

    def __init__(self, ask_at: int = REPEAT_ASK, read_ask_at: int = READ_REPEAT_ASK,
                 idle_reset: float = 0.0, clock: Callable[[], float] = time.monotonic) -> None:
        self.ask_at = ask_at
        self.read_ask_at = read_ask_at
        self.idle_reset = idle_reset     # > 0: a pause this long starts a new turn (Harness)
        self._clock = clock
        self._lock = threading.Lock()
        self._counts: dict[str, dict[str, int]] = {}
        self._last: dict[str, float] = {}
        # key -> {a render's input without its file name -> how often it failed} (#527)
        self._failures: dict[str, dict[str, int]] = {}
        # key -> {job id -> (what its last status poll said, polls in a row that said it)} (#591)
        self._polls: dict[str, dict[str, tuple[str, int]]] = {}

    def new_turn(self, key: str | None = None) -> None:
        """A new turn: every count starts again (only *key*'s when given)."""
        with self._lock:
            if key is None:
                self._counts.clear()
                self._last.clear()
                self._failures.clear()
                self._polls.clear()
            else:
                self._counts.pop(key, None)
                self._last.pop(key, None)
                self._failures.pop(key, None)
                self._polls.pop(key, None)

    def note_result(self, tool: str, args: Any, result: Any, key: str = "") -> None:
        """How a call ended (#527): a render that failed makes the next render with the same
        input but another file name a repeat.  Other tools and answers change nothing."""
        if _is_exempt(str(tool or "")):
            self._note_poll(args, result, key)
            return
        ident = _render_identity(str(tool or ""), args)
        if not ident:
            return
        with self._lock:
            if not _failed(result):
                # It worked: the earlier failures no longer count against this render.
                self._failures.get(key, {}).pop(ident, None)
                return
            per = self._failures.setdefault(key, {})
            per[ident] = per.get(ident, 0) + 1

    def _note_poll(self, args: Any, result: Any, key: str) -> None:
        """A status poll's answer (#591): counts the polls in a row that already said "stuck" (the
        answer that tells the model to stop waiting).  A render that is merely quiet (kernel
        compile, BVH build, denoise, saving) says "running" and is never counted; any other answer
        starts the count again."""
        answer = _poll_answer(result)
        job_id = str((args or {}).get("job_id", "")) if isinstance(args, dict) else ""
        if answer is None or answer.get("kind") != "RENDER":
            return      # a bake shows no progress by design
        with self._lock:
            per = self._polls.setdefault(key, {})
            if answer.get("state") != "stuck":
                per.pop(job_id, None)
                return
            per[job_id] = ("stuck", per.get(job_id, ("", 0))[1] + 1)

    def stale_polls(self, args: Any, key: str = "") -> int:
        """How many status polls of this job in a row said the same (#591)."""
        job_id = str((args or {}).get("job_id", "")) if isinstance(args, dict) else ""
        with self._lock:
            return self._polls.get(key, {}).get(job_id, ("", 0))[1]

    def check(self, tool: str, args: Any, key: str = "") -> tuple[str, int]:
        """Count this call: ``(verdict, nth)``, *verdict* one of RUN / ASK / REFUSE."""
        tool = str(tool or "")
        if _is_exempt(tool):
            stale = self.stale_polls(_poll_args(args), key)
            return (REFUSE, stale) if stale >= STALE_POLLS else (RUN, 0)
        read = _is_read(tool)
        context = _context(tool)
        sig = signature(tool, {"input": args, "context": context} if context else args)
        ident = _render_identity(tool, args)
        with self._lock:
            now = self._clock()
            if self.idle_reset > 0 and now - self._last.get(key, now) > self.idle_reset:
                self._counts.pop(key, None)
                self._failures.pop(key, None)
            self._last[key] = now
            counts = self._counts.setdefault(key, {})
            if not read:
                # Something else ran: the scene may have changed, re-reading is fair again.
                for other in [s for s, (r, _n) in counts.items() if r]:
                    del counts[other]
                if not ident:
                    # ... and a render that failed for want of it (a camera) may work now.
                    self._failures.pop(key, None)
            nth = counts.get(sig, (read, 0))[1] + 1
            counts[sig] = (read, nth)
            if ident:
                # The same render after it failed, under another file name, is a repeat too.
                nth = max(nth, self._failures.get(key, {}).get(ident, 0) + 1)
        limit = self.read_ask_at if read else self.ask_at
        if nth < limit:
            return RUN, nth
        return (ASK if nth == limit else REFUSE), nth


# -- What the model and the card say -------------------------------------------------

def _error(message: str) -> dict[str, object]:
    return {"status": "error", "message": message}


def refused(tool: str, nth: int) -> dict[str, object]:
    if _is_exempt(tool):
        return _error("Refused: get_job_status has said \"stuck\" {:d} times in a row, so the "
                      "job is stuck or too slow. Stop polling and don't start another render or bake: tell the "
                      "user it looks stuck and that they can stop it with Esc or the X on the job bar in the "
                      "status bar. Looking at the scene with other tools still works.".format(nth))
    return _error("Refused: this is the {:s} call to {:s} with the same input in this turn, and it isn't "
                  "getting anywhere. Don't repeat it: change the approach, or stop and tell the user "
                  "what blocks you.".format(_ordinal(nth), tool or "this tool"))


def declined(tool: str) -> dict[str, object]:
    return _error("The user stopped this repeat ({:s} with the same input again). Don't call it again "
                  "with the same input; try another way or ask them.".format(tool or "this tool"))


def auto_declined(tool: str) -> dict[str, object]:
    return _error("Auto mode doesn't repeat a step a third time without the user ({:s} with the same "
                  "input). Change the approach, or finish and report what blocks you.".format(tool or "this tool"))


def unanswered(tool: str, nth: int) -> dict[str, object]:
    """No one can answer a card in Blender (External Harness: the chat is the client's)."""
    return _error("Refused: this is the {:s} call to {:s} with the same input in this turn, and nobody in "
                  "Blender can let a repeat through. Don't repeat it: change the approach, or stop and ask the "
                  "user in your chat what blocks you.".format(_ordinal(nth), tool or "this tool"))


def _kind(tool: str) -> str:
    return "repeat:" + (tool or "")


def repeat_request(tool: str, args: Any, nth: int, source: str = "", thread: str = "") -> ApprovalRequest:
    """The approval card for a repeat (asked in *thread*, #357)."""
    return ApprovalRequest(
        tool=_kind(tool), source=source, args=args, coworker_tool=True,
        reason="run {:s} a {:s} time with the same input".format(tool or "a tool", _ordinal(nth)),
        note="It keeps doing the same thing: let it try again, or stop?", thread=thread, name=tool)


def decide(verdict: str, tool: str, nth: int, approvals: Approvals, auto: bool,
           thread: str = "", can_ask: bool = True) -> tuple[str, dict[str, object] | None]:
    """What to do right now: ``(RUN, None)``, ``(REFUSE, error)`` or ``(ASK, None)`` (show the card).
    *can_ask* False: nobody can answer a card (External Harness), refuse instead of asking."""
    if verdict == RUN:
        return RUN, None
    if verdict == REFUSE:
        return REFUSE, refused(tool, nth)
    if auto:                                    # nobody is there to answer
        return REFUSE, auto_declined(tool)
    if approvals.allowed_always(_kind(tool), thread):   # "Allow this turn" was picked
        return RUN, None
    if not can_ask:
        return REFUSE, unanswered(tool, nth)
    return ASK, None


def wait_answer(approvals: Approvals, request: ApprovalRequest, stop: Any = None,
                poll: float = 0.1) -> str:
    """Ask *request* and wait (a worker thread): the choice, or "cancel" on Stop."""
    done = threading.Event()
    box: list[str] = []

    def respond(choice: str) -> None:
        box.append(choice)
        done.set()

    approvals.ask(request, respond)
    while not done.wait(poll):
        if stop is not None and stop.is_set():
            approvals.answer("deny", request.id)    # take the card down
            return "cancel"
    return box[0] if box else "cancel"


def bridge_wrap(guard: LoopGuard, approvals: Approvals, auto: bool, tool: str, args: Any, key: str,
                proceed: Callable[[], tuple[dict[str, object], Callable[[], dict[str, object] | None] | None]],
                source: str = "The agent", thread: str = "", shown: Any = None, can_ask: bool = True,
                ) -> tuple[dict[str, object], Callable[[], dict[str, object] | None] | None] | None:
    """The bridge's repeat check: None lets the call through, else ``(response, check_fn)``
    (a refusal, or the card's deferred answer; once allowed, *proceed* runs the call).
    Counted per *key* (session) and *thread* (#357).  *shown* (#500): what the card shows
    for the call (the tool's arguments), when it isn't *args*.  *can_ask* False (External
    Harness): no card, the repeat is refused at once (#499)."""
    verdict, nth = guard.check(tool, args, key + "/" + thread if thread else key)
    action, error = decide(verdict, tool, nth, approvals, auto, thread, can_ask)
    if action == RUN:
        return None
    if action == REFUSE:
        print("[Coworker] loop guard: refused {:s} ({:s} identical call)".format(tool, _ordinal(nth)))
        return error, None
    state: dict[str, Any] = {"answer": None, "inner": None, "extra": None}
    approvals.ask(repeat_request(tool, args if shown is None else shown, nth, source, thread),
                  lambda choice: state.__setitem__("answer", choice))

    # #487: the card's answer is the call's whole answer, envelope included (one envelope).
    @answers_in_full
    def check() -> dict[str, object] | None:
        if state["inner"] is not None:      # allowed, and the call itself is deferred
            return answer_of(state["inner"], state["inner"](), state["extra"])
        answer = state["answer"]
        if answer is None:
            return None
        if answer in ("deny", "cancel"):
            return declined(tool)
        response, inner = proceed()
        if inner is not None:
            state["inner"] = inner
            state["extra"] = {k: response.get(k) for k in ("stdout", "stderr")}
            return None
        return response
    return {}, check


_JOB_ID_IN_CODE = re.compile(r"""\bjob_id=(?:'([^'\\]*)'|"([^"\\]*)")""")


def _poll_args(args: Any) -> dict[str, Any]:
    """``{"job_id": ...}`` of a bridge status poll, whose *args* is ``{"code": "...Params(job_id='x', ...)"}``."""
    if isinstance(args, dict) and "job_id" in args:
        return {"job_id": args["job_id"]}
    code = str(args.get("code", "")) if isinstance(args, dict) else ""
    m = _JOB_ID_IN_CODE.search(code)
    return {"job_id": (m.group(1) or m.group(2)) if m else ""}


def bridge_watch(guard: LoopGuard, tool: str, args: Any, key: str, thread: str, response: dict[str, object],
                 check_fn: Callable[[], dict[str, object] | None] | None,
                 ) -> Callable[[], dict[str, object] | None] | None:
    """Tell *guard* how a render call that went through ended (#527), counted as in
    :func:`bridge_wrap`.  Returns the checker to use: *check_fn* itself for other tools,
    else one that notes the answer when it comes (with *check_fn*'s attributes: the job
    kind, label and end a long render needs)."""
    if _is_exempt(str(tool or "")):
        # #591: its answer is counted as a status poll; the bridge's args are the call's code, so the
        # job id comes out of it.
        poll_args = _poll_args(args)
        if check_fn is None:
            guard.note_result(tool, poll_args, response, key + "/" + thread if thread else key)
            return None
        inner_poll = check_fn

        @functools.wraps(inner_poll)
        def watched_poll() -> dict[str, object] | None:
            result = inner_poll()
            if result is not None:
                guard.note_result(tool, poll_args, result, key + "/" + thread if thread else key)
            return result
        return watched_poll
    if not _render_identity(str(tool or ""), args):
        return check_fn
    count_key = key + "/" + thread if thread else key
    if check_fn is None:
        guard.note_result(tool, args, response, count_key)
        return None
    inner = check_fn

    @functools.wraps(inner)
    def watched() -> dict[str, object] | None:
        result = inner()
        if result is not None:
            guard.note_result(tool, args, result, count_key)
        return result
    return watched


# External Agent / External Harness calls, counted at the bridge.
BRIDGE = LoopGuard(idle_reset=_BRIDGE_IDLE_RESET)
