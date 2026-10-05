# BFA Coworker - Tier 3k: Image Attachments to Chat

**Date**: 2026-10-04
**Status**: IMPLEMENTED - code + tests complete on this branch. Pending: in-Blender
manual verification, CI lint (ruff/mypy/pylint), commit / PR.
**Issue**: Draise14/bfa_coworker #88 - "Feat: Allow attaching/adding an image to chat"
**Branch**: `freebuff/i-need-to-do-this-for-the-chat-baa63f94-fc72-45af-b620-2f3185ddc275`
**Depends on**: the MCP screenshot toolcode (imbuf downscale pattern), a
vision-capable model (local llama-server mmproj or a remote vision model)
**Related**: Tier 3i (`_SCREENSHOT_TOKENS` fixed image cost in the token ledger),
Tier 3j tool discovery (unchanged)
**Scope**: 4 files - new `chat_attachments.py` (~370 LOC), `agent_controller.py`
(send path, +~110), `ui_chat.py` (operators/UI, +~220), new
`tests/test_image_attachments.py` (13 tests)

> **Scope decision (2026-10-04, user-approved):** deliver the plan AND the full
> implementation in one branch, with **one Blender-standard socket** (a
> `PointerProperty` to `bpy.types.Image`) rather than a free-floating file path,
> and a **sticky socket + "Send Once" toggle** (default OFF = sticky: the image is
> re-sent every turn until the user clears it or ticks Send Once).

---

## 1. Goal

Let the user put an image in front of the model from the chat panel:

- attach an image file (file browser),
- capture the current **Render Result**,
- capture a **screen** shot (3D Viewport, whole window as fallback),
- **drag-and-drop** an image onto the 3D Viewport or Text Editor,
- pick any loaded datablock via the standard image socket (`template_ID`:
  browse / open / new),

...and have the image actually reach the model on the next send, while the saved
chat history stays plain text.

## 2. Constraints (the rules the design had to honour)

| # | Rule | Why |
|---|---|---|
| C1 | **Encode on the MAIN thread only.** | The turn runs on a worker thread; touching `bpy` off-main races Blender's global Python context counter ("Python context internal state bug"). |
| C2 | **Stored history stays plain text.** | `_save_chat_history` dumps `conversation_history` to JSON; base64 there would bloat files and re-enter the context on load. |
| C3 | **Images must be budgeted.** | Injection happens BEFORE `_estimate_messages_tokens` / `_fit_history_to_budget`, at the pre-existing fixed cost `_SCREENSHOT_TOKENS = 1500` per image (a vision tower costs roughly fixed tokens, not `len(base64)`). |
| C4 | **The turn-start `_pending_image` reset must not clear user attachments.** | `_pending_image` is the MCP screenshot pipeline (set mid-turn, cleared at turn start, cleared after one use); attachments are the user's socket and behave differently. |
| C5 | **Blender-standard socket.** | Per the issue: image handling follows Blender's own patterns - a datablock picker, the file browser, `image.open`, drag-and-drop `FileHandler`. |
| C6 | **Existing callers/tests keep working.** | All new parameters default to `None`. |

## 3. Architecture (data flow)

```
 main thread (operator execute / send)           worker thread (turn)
 ------------------------------                  -------------------------
 chat_attachments.image_to_data_uri(img)          run_conversation_turn(
   -> data URI (~1 MiB cap, imbuf downscale)       attachments=[uri],
 _capture_chat_attachment(props)                   attachment_names=[name])
   -> (attachments, attachment_names)                |
   + Send Once consumes the socket                   v
        |                              turn start: install into AgentState
        v                              (REPLACES prev turn; before the
 enqueue_message(...) /                _pending_image reset - C4)
 _do_turn / _do_turn_from_queue           |
        |                                v
        +------------------>   _build_send_messages (per REQUEST):
                               target = LAST user msg flagged turn_start
                               (fallback: last user msg), dict COPY only,
                               prepend one {"type":"image_url"} block per
                               attachment + text block, BEFORE budgeting
                               - re-injected EVERY tool-loop request,
                                 never cleared after use
                               - stored history untouched (C2)
                               stored user message carries only
                               "[attached: a.png]" marker (plain text)
```

### 3.1 `chat_attachments.py` (new module)

Lazy-`bpy`, stdlib-only at import time, no `mcp.blmcp` imports (the addon ships
standalone; the imbuf downscaler is a slimmed copy of the MCP screenshot helper).

- `ATTACHMENT_LIMIT_BYTES = 786432` (768 KiB raw -> ~1 MiB base64, the same
  budget the MCP screenshot tools use), `SUPPORTED_EXTENSIONS` (7),
  `_KEEP_ATTACHMENTS = 10`.
- `attachments_dir()` - `SCRIPTS/bfa_coworker_chat_history/attachments`.
- `load_image_file(path)` - `bpy.data.images.load(check_existing=True)` with an
  extension filter.
- `image_to_data_uri(img)` - fast path: unmodified file on disk read as-is
  (original mime); slow path: `img.save_render` to a temp PNG; both downscale
  with `imbuf` behind a capability guard (`hasattr(imbuf, "load"/"write")` -
  the stub imbuf in the test env has neither).
- `capture_render()` / `capture_screen(target="WINDOW"|"VIEW_3D")` - save a PNG
  into the attachments dir (unique name, pruned to 10); `None` when there is
  nothing to capture. Main thread only.
- `attachment_name(img)`, `attachment_marker(names)` -> `"[attached: a.png]"`,
  `prune_attachments()`.

### 3.2 Send path (`agent_controller.py`)

- `AgentState.user_attachments: list[str]` / `user_attachment_names: list[str]`
  - installed at turn start from `run_conversation_turn(attachments=...)`,
  REPLACING the previous turn's (no cross-turn leak), deliberately NOT touched
  by the `_pending_image = None` reset (C4), not cleared after injection.
- Stored user message: `user_message + "\n[attached: ...]"` (C2).
- Injection in `_build_send_messages`: backwards scan for the LAST
  `turn_start`-flagged user message (older turns keep their flag too), on a
  `dict()` copy, images ahead of the text block; then the pre-existing
  pending-screenshot block runs as before; then budgeting. Falls back to the
  last user message if the flag was ever stripped.
- `MessageQueue.enqueue` / `enqueue_message` gained
  `attachments` / `attachment_names`, **snapshotted (copied) at enqueue time**.

### 3.3 UI (`ui_chat.py`)

- Socket props on `ChatHistoryProperties` (WindowManager):
  `chat_image: PointerProperty(bpy.types.Image)`, `chat_image_send_once:
  BoolProperty(default=False)` = sticky.
- Operators (all main-thread, all reuse `chat_attachments`):
  - `BFACW_OT_chat_image_attach` - file browser (`filter_glob` = the 7
    extensions) -> `load_image_file` -> socket.
  - `BFACW_OT_chat_capture_render` - `capture_render()` -> load -> socket.
  - `BFACW_OT_chat_capture_screen` - `capture_screen("VIEW_3D")`, falling back
    to `capture_screen("WINDOW")` -> load -> socket.
  - `BFACW_OT_chat_image_drop` - receives `filepath` from the FileHandler.
  - `BFACW_FH_chat_drop(bpy.types.FileHandler)` - `bl_import_operator =
    "bfacw.chat_image_drop"`; `bl_file_extensions` lists all 7 extensions
    **semicolon-separated** (per `bpy.types.FileHandler.bl_file_extensions` -
    NOT space-separated), mirroring `chat_attachments.SUPPORTED_EXTENSIONS`;
    `poll_drop` accepts `VIEW_3D` and `TEXT_EDITOR`.
- `_capture_chat_attachment(props)` - the ONLY encode site: called by
  `BFACW_OT_chat_send` and `BFACW_OT_chat_queue_send` before any worker
  starts; feeds the queued `enqueue_message` (both queue entry points), the
  direct `run_conversation_turn`, and `_do_turn_from_queue` forwards the
  queue item's payload. Send Once consumes the socket only after a SUCCESSFUL
  encode; a failed encode keeps the socket and sends plain text.
- `_draw_attachment_row(layout, props)` - one helper drawn by BOTH panels
  (Viewport chat panel and Text Editor panel): `template_ID` socket
  (browse/open/new), Send Once toggle, and the File / Render / Screen buttons
  (icons verified against the `icon_items` enum dump: `IMAGE_DATA`,
  `FILEBROWSER`, `RENDER_STILL`, `FULLSCREEN_ENTER` - there is no
  `SCREENSHOT` icon).

## 4. Phases (how it shipped)

1. **Foundation** - `chat_attachments.py`; socket props; `AgentState` fields.
   Verified: py_compile, 201 unit tests, namespace/license/ascii baseline.
2. **Send path** - params threaded through `run_conversation_turn` ->
   `_run_conversation_turn_inner`; turn-start install + marker; request-copy
   injection before budgeting; queue fields. Verified: 393-test suite +
   `tests/test_image_attachments.py` (turn-loop tests against the fake
   llama-server harness: all 3 requests of an AGENT tool-loop turn carry the
   blocks; cross-turn targeting; exact 1500-token delta; queue snapshot).
3. **Operators + UI** - the five classes above, `_classes`/`__all__`
   registration, both panel draws, both send paths wired. Verified: 399-test
   suite + 6 register/draw/send-path tests that run without Blender.
4. **Docs** - this plan + the CHANGELOG entry.

## 5. Tests

`tests/test_image_attachments.py` (13):

- **Turn loop (real HTTP, scripted server):** image blocks injected into the
  turn_start message on EVERY request of a 3-request tool loop; exactly one
  message per request carries images; exact text = prompt + marker; stored
  history `json` has no `data:` URI; attachments survive the turn; a later
  plain send carries no images (no cross-turn leak); a later attach targets
  the CURRENT turn's message, not an older `turn_start`; the stale
  `_pending_image` reset is independent; token delta is exactly 1500/image;
  source guard: injection index < budget index + `turn_start` targeting;
  queue snapshot-at-enqueue semantics; `enqueue_message` forwarding.
- **Register/draw (no Blender):** all 5 classes in `_classes` AND `__all__`;
  FileHandler extensions == `";".join(SUPPORTED_EXTENSIONS)` and
  `bl_import_operator`/`poll_drop` contract; `_capture_chat_attachment`
  sticky / send-once / failed-encode semantics; fake-layout draw test
  (socket drawn once with open/new, 3 buttons, send-once toggle); wiring
  counts (2 captures, 3 `attachments=`, 2 `item.get(...)` forwards); both
  panels call `_draw_attachment_row`.

## 6. Verification status

Ran green locally (Python 3.11, no Blender):

- 399 unit/integration tests (11 modules: image_attachments, chat_turns,
  orchestration_helpers, context_budget, ask_mode, addon_imports,
  turn_loop_integration, streaming_llm, turn_cost, session_memory,
  llm_transport_errors).
- `py_compile` / `ast.parse` on all touched files.
- `check_ascii` clean; `check_license` = only the 2 pre-existing SPDX misses
  (`autofix.py`, `blender_templates.py`); `check_namespace` = 195 repo-wide /
  20 touched files = exact HEAD baseline (0 new).

CI-only (not installed locally): ruff, mypy, pylint, vulture.

**Manual in-Blender checklist (no `BLENDER_BIN` in this environment):**
register/unregister is clean on add-on reload; the attachment row renders in
both panels; file browser filters to the 7 extensions; attach -> sticky send
-> model sees the image; Send Once detaches after one send; drag-and-drop onto
VIEW_3D and TEXT_EDITOR; render capture with/without a render; screen capture
(3D Viewport and window fallback); saved chat JSON contains the marker and no
base64; an oversized image downscales instead of failing.

## 7. Known limitations / pinned follow-ups

- **Vision gating**: the buttons are not hidden for non-vision models.
  `llm_manager.supports_vision()` (preset `vision` flag / mmproj presence)
  with a disabled-state tooltip is a planned follow-up - out of scope here.
- **One image per send**: the plumbing carries a LIST (`attachments`), the
  socket holds one datablock (Blender-standard). Multi-image sockets would be
  a UI change only.
- **Auto-continue / flatten rescue paths** rebuild the payload from raw
  history, so they re-send WITHOUT the image blocks (same fate as the
  pre-existing pending screenshot; the marker text still tells the model an
  image was attached to the turn).
- **Downscale fidelity**: `imbuf` downscaling is capability-guarded; in an
  environment with a stub imbuf an oversized payload falls back to raw bytes
  (the MCP screenshot tools share this behaviour).
