# 🖼️ BFA Coworker - Tier 3k: Image Attachments to Chat

**📅 Date**: 2026-10-04
**🚦 Status**: ✅ IMPLEMENTED - code + tests complete **and committed** (`d24d21d`).
Remaining: 🔲 in-Blender manual verification, 🔲 lint sweep (ruff/mypy/pylint/
vulture - available locally in `.lintvenv`, **not** CI-only), 🔲 PR.
**➕ Follow-up (uncommitted)**: the image row moved **above the chat input** and
gained a **drawn thumbnail preview** (`_draw_attachment_preview` +
`_ensure_attachment_preview`); tests 13 → 17.
**🐞 Bug fix (uncommitted, see 6.3)**: attaching a **BMP/JPEG/TIFF** file (any
non-PNG source) wrote the *source* format into `downscaled.png` - `imbuf.write`
uses the buffer's own `file_type`, not the file name - producing an oversized
scratch file, an `OSError` on a full scratch drive, and a traceback that killed
the send. Fixed by pinning the buffer to PNG, making encoding total, preferring
`bpy.app.tempdir`, and warning instead of failing silently; tests 17 → 25.
**🎫 Issue**: Draise14/bfa_coworker #88 - "Feat: Allow attaching/adding an image to chat"
**🌿 Branch**: `freebuff/i-need-to-do-this-for-the-chat-baa63f94-fc72-45af-b620-2f3185ddc275`
**🧩 Depends on**: the MCP screenshot tool code (imbuf downscale pattern), a
vision-capable model (local llama-server mmproj or a remote vision model)
**🔗 Related**: Tier 3i (`_SCREENSHOT_TOKENS` fixed image cost in the token ledger),
Tier 3j tool discovery (unchanged)
**📦 Scope**: 4 files - new `chat_attachments.py` (~370 LOC), `agent_controller.py`
(send path, +~110), `ui_chat.py` (operators/UI, +~220), new
`tests/test_image_attachments.py` (13 tests)

> **📋 TL;DR:** An image socket in both chat panels feeds the vision model a
> data URI on every turn, while saved history stays plain text. ✅ Shipped and
> green on tests; ⚠️ lint sweep never actually ran (no CI, tooling available
> locally); 🔲 manual in-Blender pass still open.

> 🧭 **Scope decision (2026-10-04, user-approved):** deliver the plan AND the full
> implementation in one branch, with **one Blender-standard socket** (a
> `PointerProperty` to `bpy.types.Image`) rather than a free-floating file path,
> and a **sticky socket + "Send Once" toggle** (default OFF = sticky: the image is
> re-sent every turn until the user clears it or ticks Send Once).

---

## 1. 🎯 Goal

Let the user put an image in front of the model from the chat panel:

- 📁 attach an image file (file browser),
- 🎞️ capture the current **Render Result**,
- 📸 capture a **screen** shot (3D Viewport, whole window as fallback),
- 🖱️ **drag-and-drop** an image onto the 3D Viewport or Text Editor,
- 🖼️ pick any loaded datablock via the standard image socket (`template_ID`:
  browse / open / new),

...and have the image actually reach the model on the next send, while the saved
chat history stays plain text. ✅

## 2. 🧱 Constraints (the rules the design had to honour)

| # | 🚧 Rule | 💡 Why |
|---|---|---|
| 🔒 C1 | **Encode on the MAIN thread only.** | The turn runs on a worker thread; touching `bpy` off-main races Blender's global Python context counter ("Python context internal state bug"). |
| 🔒 C2 | **Stored history stays plain text.** | `_save_chat_history` dumps `conversation_history` to JSON; base64 there would bloat files and re-enter the context on load. |
| 🔒 C3 | **Images must be budgeted.** | Injection happens BEFORE `_estimate_messages_tokens` / `_fit_history_to_budget`, at the pre-existing fixed cost `_SCREENSHOT_TOKENS = 1500` per image (a vision tower costs roughly fixed tokens, not `len(base64)`). |
| 🔒 C4 | **The turn-start `_pending_image` reset must not clear user attachments.** | `_pending_image` is the MCP screenshot pipeline (set mid-turn, cleared at turn start, cleared after one use); attachments are the user's socket and behave differently. |
| 🔒 C5 | **Blender-standard socket.** | Per the issue: image handling follows Blender's own patterns - a datablock picker, the file browser, `image.open`, drag-and-drop `FileHandler`. |
| 🔒 C6 | **Existing callers/tests keep working.** | All new parameters default to `None`. |

## 3. 🏗️ Architecture (data flow)

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

> 🔄 **Main thread** does the `bpy`-touching encode; the **worker thread** only
> ever receives plain data URIs and never touches `bpy` (constraint C1).

### 3.1 📦 `chat_attachments.py` (new module)

Lazy-`bpy`, stdlib-only at import time, no `mcp.blmcp` imports (the addon ships
standalone; the imbuf downscaler is a slimmed copy of the MCP screenshot helper).

- 📏 `ATTACHMENT_LIMIT_BYTES = 786432` (768 KiB raw -> ~1 MiB base64, the same
  budget the MCP screenshot tools use), `SUPPORTED_EXTENSIONS` (7),
  `_KEEP_ATTACHMENTS = 10`.
- 🗂️ `attachments_dir()` - `SCRIPTS/bfa_coworker_chat_history/attachments`.
- 📥 `load_image_file(path)` - `bpy.data.images.load(check_existing=True)` with an
  extension filter.
- 🔐 `image_to_data_uri(img)` - fast path: unmodified file on disk read as-is
  (original mime); slow path: `img.save_render` to a temp PNG; both downscale
  with `imbuf` behind a capability guard (`hasattr(imbuf, "load"/"write")` -
  the stub imbuf in the test env has neither).
- 📸 `capture_render()` / `capture_screen(target="WINDOW"|"VIEW_3D")` - save a PNG
  into the attachments dir (unique name, pruned to 10); `None` when there is
  nothing to capture. Main thread only.
- 🏷️ `attachment_name(img)`, `attachment_marker(names)` -> `"[attached: a.png]"`,
  `prune_attachments()`.

### 3.2 🚚 Send path (`agent_controller.py`)

- 🧷 `AgentState.user_attachments: list[str]` / `user_attachment_names: list[str]`
  - installed at turn start from `run_conversation_turn(attachments=...)`,
  REPLACING the previous turn's (no cross-turn leak), deliberately NOT touched
  by the `_pending_image = None` reset (C4), not cleared after injection.
- 📝 Stored user message: `user_message + "\n[attached: ...]"` (C2).
- 💉 Injection in `_build_send_messages`: backwards scan for the LAST
  `turn_start`-flagged user message (older turns keep their flag too), on a
  `dict()` copy, images ahead of the text block; then the pre-existing
  pending-screenshot block runs as before; then budgeting. Falls back to the
  last user message if the flag was ever stripped.
- 📬 `MessageQueue.enqueue` / `enqueue_message` gained
  `attachments` / `attachment_names`, **snapshotted (copied) at enqueue time**.

### 3.3 🎨 UI (`ui_chat.py`)

- 🔌 Socket props on `ChatHistoryProperties` (WindowManager):
  `chat_image: PointerProperty(bpy.types.Image)`, `chat_image_send_once:
  BoolProperty(default=False)` = sticky.
- 🎛️ Operators (all main-thread, all reuse `chat_attachments`):
  - 🎞️ `BFACW_OT_chat_capture_render` - `capture_render_from_view()` (a
    throwaway camera aligned to the viewport view), falling back to
    `capture_render()` for the last Render Result -> load -> socket.
  - 📸 `BFACW_OT_chat_capture_screen` - `capture_screen("VIEW_3D")`, falling back
    to `capture_screen("WINDOW")` -> load -> socket.
  - 🖱️ `BFACW_OT_chat_image_drop` - receives `directory` + `files` from the
    FileHandler.
  - 🪝 `BFACW_FH_chat_drop(bpy.types.FileHandler)` - `bl_import_operator =
    "bfacw.chat_image_drop"`; `bl_file_extensions` lists all 7 extensions
    **semicolon-separated** (per `bpy.types.FileHandler.bl_file_extensions` -
    NOT space-separated), mirroring `chat_attachments.SUPPORTED_EXTENSIONS`;
    `poll_drop` accepts `TEXT_EDITOR` and the `VIEW_3D` sidebar **while the
    Coworker tab is active** (`_is_coworker_panel_region`) - see 6.5.
- 🔐 `_capture_chat_attachment(props)` - the ONLY encode site: called by
  `BFACW_OT_chat_send` and `BFACW_OT_chat_queue_send` before any worker
  starts; feeds the queued `enqueue_message` (both queue entry points), the
  direct `run_conversation_turn`, and `_do_turn_from_queue` forwards the
  queue item's payload. Send Once consumes the socket only after a SUCCESSFUL
  encode; a failed encode keeps the socket and sends plain text.
- 🧩 `_draw_attachment_row(layout, props)` - one helper drawn by BOTH panels
  (Viewport chat panel and Text Editor panel) **above the chat input**, so the
  attachment is set before typing: the `template_ID` socket
  (browse/open/new), Send Once toggle, a thumbnail **preview** of the attached
  image, and the File / Render / Screen buttons
  (icons verified against the `icon_items` enum dump: `IMAGE_DATA`,
  `FILEBROWSER`, `RENDER_STILL`, `FULLSCREEN_ENTER` - there is no
  `SCREENSHOT` icon).
- 🖼️ `_draw_attachment_preview(layout, img, context)` - draws a thumbnail of the
  attached image (`_attachment_preview_icon` -> `UILayout.template_icon`) so
  the user can see WHAT is attached instead of trusting a filename.  The
  pixels are our own resample of the image at exactly the size the widget is
  drawn at, written into the datablock's own preview (`img.preview_ensure()`)
  because `template_icon` otherwise draws Blender's stock 32px icon and the
  thumbnail is very pixely.  The widget is sized from the panel's live width,
  so a landscape image gets a wide, SHORT thumbnail instead of a square button
  with large empty margins above and below it.  Best-effort: nothing attached,
  no preview, or a datablock without pixels all draw nothing (or fall back to
  the stock icon) and a panel draw never raises.
- 🔥 `_ensure_attachment_preview(img)` - called when an image is ATTACHED
  (`_attach_to_socket`), not only from the draw.  `preview_ensure()` only
  *creates* the preview; Blender's preview thread fills its pixels a moment
  later, so warming it at attach time makes the thumbnail ready on the FIRST
  redraw instead of one frame later.  Best-effort: no image, or a datablock
  without pixels, never raises.

## 4. 🚀 Phases (how it shipped)

1. 🏗️ **Foundation** - `chat_attachments.py`; socket props; `AgentState` fields.
   ✅ Verified: py_compile, 201 unit tests, namespace/license/ascii baseline.
2. 🚚 **Send path** - params threaded through `run_conversation_turn` ->
   `_run_conversation_turn_inner`; turn-start install + marker; request-copy
   injection before budgeting; queue fields. ✅ Verified: 393-test suite +
   `tests/test_image_attachments.py` (turn-loop tests against the fake
   llama-server harness: all 3 requests of an AGENT tool-loop turn carry the
   blocks; cross-turn targeting; exact 1500-token delta; queue snapshot).
3. 🎨 **Operators + UI** - the five classes above, `_classes`/`__all__`
   registration, both panel draws, both send paths wired. ✅ Verified: 399-test
   suite + 6 register/draw/send-path tests that run without Blender (the §6.3
   follow-up - row moved above the input, drawn preview - lifted this to
   403 + 10 UI tests).
4. 📚 **Docs** - this plan + the CHANGELOG entry. ✅

## 5. 🧪 Tests

`tests/test_image_attachments.py` (17 ✅):

- 🌀 **Turn loop (real HTTP, scripted server):** image blocks injected into the
  turn_start message on EVERY request of a 3-request tool loop; exactly one
  message per request carries images; exact text = prompt + marker; stored
  history `json` has no `data:` URI; attachments survive the turn; a later
  plain send carries no images (no cross-turn leak); a later attach targets
  the CURRENT turn's message, not an older `turn_start`; the stale
  `_pending_image` reset is independent; token delta is exactly 1500/image;
  source guard: injection index < budget index + `turn_start` targeting;
  queue snapshot-at-enqueue semantics; `enqueue_message` forwarding.
- 🎨 **Register/draw (no Blender):** all 5 classes in `_classes` AND `__all__`;
  FileHandler extensions == `";".join(SUPPORTED_EXTENSIONS)` and
  `bl_import_operator`/`poll_drop` contract; `_capture_chat_attachment`
  sticky / send-once / failed-encode semantics; fake-layout draw test
  (socket drawn once with open/new, 3 buttons, send-once toggle); wiring
  counts (2 captures, 3 `attachments=`, 2 `item.get(...)` forwards); both
  panels call `_draw_attachment_row`; the preview thumbnail is drawn once and
  skipped (without raising) when there is no image, no preview, or `icon_id`
  0; the row is drawn ABOVE the chat input in both panels; and
  `_attach_to_socket` sets the socket + warms the preview + reports while
  `_ensure_attachment_preview` stays best-effort (no image, or a datablock
  whose `preview_ensure` raises, never propagates).

## 6. ✅ Verification status

Ran green locally (Python 3.11; plus real Blender API probes - see 6.1):

- ✅ 403 unit/integration tests (11 modules: image_attachments, chat_turns,
  orchestration_helpers, context_budget, ask_mode, addon_imports,
  turn_loop_integration, streaming_llm, turn_cost, session_memory,
  llm_transport_errors).
- ✅ `py_compile` / `ast.parse` on all touched files.
- ✅ `check_ascii` clean; `check_license` = only the 2 pre-existing SPDX misses
  (`autofix.py`, `blender_templates.py`); `check_namespace` = 195 repo-wide /
  20 touched files = exact HEAD baseline (0 new).

- ⚠️ **The lint sweep was deferred as "CI-only (not installed locally)" - that
  note is wrong on both counts** (see 6.1): ruff/mypy/pylint/vulture *are*
  installed in `.lintvenv`, and this repo has **no CI at all**
  (`.github/workflows` does not exist), so nothing would ever have run them.

### 6.1 🔁 Independent re-verification (2026-10-04, post-commit)

Re-ran everything reproducible in this environment against commit `d24d21d`:

| ✅/⚠️ | Check | Result |
|---|---|---|
| ✅ | 🧪 `pytest tests/test_image_attachments.py` | 17 passed |
| ✅ | 🧪 11-module suite | 403 passed, 40 subtests |
| ✅ | 🔤 `check_ascii.py` | clean (exit 0) |
| ✅ | 📜 `check_license.py` | only non-vendor misses are `autofix.py` + `blender_templates.py` (the other 1,948 are vendored deps, out of scope) |
| ✅ | 🧭 `check_namespace.py` on `chat_attachments.py` | 0 errors |
| ⚠️ | 🧭 `check_namespace.py` repo-wide baseline | the documented "195" figure did **not** reproduce here (vendor deps inflate it; with `--skip addon/bfa_coworker/vendor`: 227 files / 156 errors). The meaningful claim - **0 new** namespace errors from this change - holds. |
| ⚡ | `ruff check` - `chat_attachments.py` | clean |
| ✅ | ⚡ `ruff check` - `agent_controller.py` / `ui_chat.py` | **no new violations** (per-rule stats identical to `HEAD~1`: 338 / 91) |
| ⚠️ | ⚡ `ruff check` - `tests/test_image_attachments.py` | 9 findings, all auto-fixable (8 × UP032 f-string, 1 × I001 import sort) - consistent with existing repo style |
| ✅ | 🧠 `mypy --ignore-missing-imports` - `chat_attachments.py` | no errors in the new module |
| ✅ | 🔍 `pylint` - `chat_attachments.py` | 9.51/10 (existing test files sit at 8.11–8.48) |
| ✅ | 🧹 `vulture` - `chat_attachments.py` | clean (exit 0) |
| ✅ | 🧾 CHANGELOG entry for #88 | present (line 30) |
| ✅ | 📌 Code ↔ plan fidelity | constants, injection order, `turn_start` targeting, queue snapshot, icons, FileHandler contract all match |
| ✅ | 🖼️ Blender 5.2.0 LTS - `ID.preview_ensure()` | returns a real `ImagePreview`; **`icon_id = 1112` in GUI**, `0` in `--background` (the thumbnail is a GUI-only affordance that degrades to nothing) |
| ✅ | 🖼️ Blender 5.2.0 LTS - draw calls | `row.alignment = 'CENTER'` + `row.template_icon(icon_value=1112, scale=8.0)` executed inside a real `UILayout` (menu `draw`) with **no error** |
| ✅ | 🧩 Blender 5.2.0 LTS - `UILayout` RNA | `template_ID`, `template_ID_preview`, `template_icon`, `template_preview`, `template_image` all present |
| ✅ | ⚡ `ruff check` - `ui_chat.py` | **no new violations** (`HEAD~1` baseline 91 preserved; the single S110 the preview helper first introduced was fixed) |

**Net:** the implementation is faithful to the plan and the test claims hold.
The only real gap is process: the lint sweep was never executed (and cannot be
"left to CI", since there is no CI), plus the manual Blender pass below.

### 6.2 🔲 Manual in-Blender checklist

> **Correction:** `BLENDER_BIN` *is* set in this environment (it pointed at a
> stale path), and Blender **5.2.0 LTS** is installed at
> `D:\Software\Blender\stable\blender-5.2.0-lts.fbe6228777e7\blender.exe`.  The
> preview/draw path was therefore verified programmatically (6.1); the items
> below still need a human inside the running add-on.

- 🔲 register/unregister is clean on add-on reload;
- 🔲 the attachment row renders in both panels;
- 🔲 file browser filters to the 7 extensions;
- 🔲 attach -> sticky send -> model sees the image;
- 🔲 Send Once detaches after one send;
- 🔲 drag-and-drop onto VIEW_3D and TEXT_EDITOR;
- 🔲 render capture with/without a render;
- 🔲 screen capture (3D Viewport and window fallback);
- 🔲 saved chat JSON contains the marker and no base64;
- 🔲 an oversized image downscales instead of failing.

### 6.3 🐞 Follow-up fix: non-PNG attachments crashed the send

**Symptom** (reported 2026-10-04): attaching `Wallpaper.bmp` (2560×1080,
8,294,454 bytes) raised `OSError: write: Unable to write image file (No error)`
from `chat_attachments._downscale_to_limit`, which escaped
`BFACW_OT_chat_send.execute` as `bpy.rna ERROR Python script error`.

**Root cause (reproduced, not inferred):** `imbuf.write()` picks the output format
from the ImBuf itself, **not** from the filepath extension. `_downscale_to_limit`
loaded the BMP, wrote it to `downscaled.png`, and got **BMP bytes back**:

| Probe (`C:\3D_Stuff\Devbuild\bforartists.exe --background`, 5.3.0 Alpha) | Result |
|---|---|
| `imbuf.write(im, filepath="probe.png")` for the BMP source | `8_294_454` bytes, magic `BM` |
| error position + short write | `8_286_774 + 7_680 == 8_294_454` (exactly that file) |
| `imbuf.new((32, 32))` → same call | magic `\x89PNG` (no source format → extension used) |
| `im.file_type = "PNG"` then write | magic `\x89PNG` |

So a BMP/TIFF/JPEG attachment was written back in its own (often uncompressed)
format while being advertised as `image/png`; the full-size attempt created an
8.29 MB scratch file for a 768 KiB budget; and its failure propagated because only
the `save_render` branch of `image_to_data_uri` was guarded. The `.blend` being
unsaved was **not** a factor - the image had a valid on-disk path.

**Fix** (`chat_attachments.py`, `ui_chat.py`, `tests/test_image_attachments.py`):
the buffer's `file_type` is pinned to `PNG` before any write (via `getattr`/
`setattr` - the runtime attribute is missing from the bundled `imbuf` stubs, so
a direct access is a hard mypy error); copies inherit it; `_write`,
`_downscale_to_limit`, `_encode_file` and `image_to_data_uri` can no longer raise
(they degrade to the smallest encode, the original bytes, or `None`); scratch dirs
prefer `bpy.app.tempdir` (honours *Preferences > File Paths > Temporary Files*);
both send operators now report a `WARNING` when a set socket could not be encoded
and send as plain text.

**Verified:** 25/25 `tests/test_image_attachments.py` tests pass; `ruff` shows no
new findings vs the 6.1 baseline (ui_chat 91, tests 9, chat_attachments 0); an
in-Blender 5.3.0 Alpha probe of the patched module returns `image/png`, 478,076
bytes, magic `\x89PNG` from `_downscale_to_limit`, and a
`data:image/png;base64,iVBORw0K…` URI from `image_to_data_uri`.

### 6.4 ➕ Follow-up: row above the input + drawn preview

Two UX changes on top of `d24d21d`, both verified:

1. **The image row moved ABOVE the chat input** in both panels, so the image is
   chosen before typing and reads as part of the message being composed.
   `test_attachment_row_sits_above_the_chat_input` locks the ordering in.
2. **The selection is now drawn.**  `_draw_attachment_preview` renders the
   attached datablock's own preview as a centred thumbnail
   (`ID.preview_ensure()` -> `UILayout.template_icon(icon_value=..., scale=8.0)`),
   and `_ensure_attachment_preview` warms that preview when the image is
   attached so it is ready on the first redraw.

Why this is safe: no pixels / no preview / `--background` all draw nothing and
never raise, so the socket, the Send-Once toggle and the whole send path are
untouched.  `ui_chat.py` is back to **zero new ruff violations**; the attachment
tests went 13 -> 15 -> 17.

> ⚠️ **Concurrent edits:** another process is also working in this worktree
> (hardening `chat_attachments.py`'s encoder and adding its own tests), so the
> attachment test FILE currently holds more than the 17 added here, and the
> working tree carries changes this plan does not describe.  Numbers above are
> this change's own delta.

### 6.5 🧹 Follow-up: lighter row, real drops, view-following render (2026-10-05)

Four user-reported caveats, plus two bugs that only a real-Blender pass exposed.

1. **The redundant File button is gone.**  The Blender-standard image socket
   already opens the file browser, so `BFACW_OT_chat_image_attach` and its button
   were removed; the socket, a drop and the two capture buttons cover every case.
   `__all__`, `_classes`, the tests and the changelog were updated with it.
2. **Render now renders the CURRENT VIEW.**  `capture_render_from_view()` links a
   temporary camera, copies the viewport lens, runs `view3d.camera_to_view()`
   (falling back to `matrix_world = region_3d.view_matrix.inverted()`), renders,
   then restores `scene.camera` and removes the temporary object **and** the
   temporary datablock in a `finally`.  Putting a render in the chat therefore
   never moves the user's camera and never leaves anything behind.
   The capture is also **bounded**: `_clamp_render_settings()` caps the longest
   output edge (`_RENDER_MAX_EDGE` = 1024, never upscaling), the sample count
   (`_RENDER_MAX_SAMPLES` = 32, Cycles and EEVEE) and sets a Cycles time limit
   (`_RENDER_TIME_LIMIT_S` = 30) before rendering, and `_restore_render_settings()`
   puts the user's own values back in the same `finally` - so a heavy scene can
   never block Blender (and the chat) for minutes, and a later F12 render is
   unaffected.  A 1920x1080 / 4096-sample scene captures in under two seconds.
3. **Drag-and-drop lands on the panel** - without touching Blender's classes.
   Blender's built-in `VIEW3D_FH_empty_image` / `VIEW3D_FH_camera_background_image`
   also match image extensions in a 3D Viewport - the sidebar included - so a drop
   there opened Blender's "multiple file handlers" chooser instead of attaching.
   The first attempt wrapped both `poll_drop`s to return False **only** inside the
   Coworker panel (matched on `Region.active_panel_category`) and to hand the
   originals back on unregister.  A drag on Bforartists 5.3 then died with
   `EXCEPTION_ACCESS_VIOLATION` inside `bpy_class_call` -> `file_handler_poll_drop`,
   on every drag and whether or not the socket held an image.  Replacing a method
   on a class Blender registers from a *startup* module is not a mechanism we can
   keep, so the wrapper was **removed**: we now register only our own
   `BFACW_FH_chat_drop`, which claims the Text Editor and the Coworker panel
   region (`_is_coworker_panel_region()`), and Blender's handler classes are
   exactly as Blender registered them.  The 3D Viewport therefore keeps its stock
   "multiple file handlers" menu - with our entry in it - while the Text Editor
   attaches directly.  `poll_drop` and `_is_coworker_panel_region()` are
   additionally wrapped so that a drop poll can never raise.  The crash could
   **not** be reproduced on Blender 5.2 (the poll path was driven directly, with
   and without the wrapper, across a simulated add-on reload); the wrapper is
   removed regardless.
4. **The preview is responsive, sharp, and has no empty bands.**  Blender draws
   a preview icon into a SQUARE: `template_icon()` marks its button
   `BUT_ICON_PREVIEW`, so `widget_draw_preview_icon()` (interface_widgets.cc)
   takes `min(button_w, button_h) - PREVIEW_PAD` and calls
   `icon_draw_preview(..., aspect=1.0f, size)`, and `icon_draw_size()` builds
   `w = h = size / aspect` before `icon_draw_rect()` fits the image inside it.
   A wide image is therefore never drawn wider than the button is tall, and
   `UILayout.scale_x/scale_y` only makes the thumbnail smaller - measured, a
   4:1 image in a short button draws a 38x9 blob inside a 176x44 bar - so it
   cannot produce a rectangle filled by the image.  A preview built from
   several square tiles would follow the image's shape (a row for a wide
   image), but every Python-reachable icon button carries a 6px preview
   padding: `BUT_NO_PREVIEW_PADDING` is only set by `uiDefIconPreviewBut`,
   which only the File Browser uses.
   So the buffer is a panel-wide SQUARE and `_attachment_thumbnail_pixels()`
   composes it - the whole image fitted at full width over a dimmed, blurred,
   cover-scaled copy of itself - which keeps the thumbnail panel-wide,
   aspect-correct and borderless.  `_attachment_preview_px()` quantises the
   side to 16px and clamps it; the pixels are read with `foreach_get` into an
   `array('f')`, copied into a scratch image and resampled there, and the
   result is written to a private `bpy.utils.previews` collection because
   Blender regenerates an image datablock's own preview from the file.
   Measured in a real Bforartists 5.3 GUI on a 400x100px attachment in a 220px
   sidebar: a 176x176 buffer drawn as a 162x162 square whose sharp band is
   160x42 (the source's 4:1 aspect, centred) with the muted backdrop filling
   the rest - a tinted/bright pixel ratio of 4.08 against the 4.0 the geometry
   predicts.

   That run also uncovered two bugs worth recording: `Image.copy()` returns an
   all-black buffer for a generated image (the copy is regenerated, not
   duplicated), so the earlier "sharp" resample was never what the panel drew -
   it had been showing the datablock's 32px stock preview, which is both the
   "very pixely" look and the reason a private-collection buffer seemed not to
   draw at all.

5. **The image row collapses from its own header.**  `_draw_attachment_row()` draws
   a `chat_image_expanded` toggle (a `BoolProperty` on `ChatHistoryProperties`,
   so the state is per window and the collapse is discoverable) using the
   `DOWNARROW_HLT` / `TRIA_RIGHT` pair - the highlighted arrows Blender's own
   sub-panels use - and returns early when collapsed, so the socket, thumbnail
   and capture buttons cost no panel height.  While collapsed the header shows
   the attached datablock's name, so what will be sent stays visible; the drop
   handler targets the panel region rather than the row, so dragging still
   attaches with the row closed.  Verified in a real Bforartists 5.3 GUI: the
   property registers, the thumbnail's magenta pixels are 5396 while expanded
   and 0 while collapsed.

Two bugs found by running the code in a real Blender 5.2 GUI:

- `_save_render_result()` gated on `img.size`, but Blender 5.2 reports
  `Render Result.size` as `(0, 0)` even after a render that produced pixels - so
  the Render button never saved anything.  The write is now the test
  (`save_render` raises `RuntimeError` for a result that was never rendered), and
  a zero-byte write is unlinked instead of attached.
- `capture_render_from_view()` read `Area.spacedata`; the real attribute is
  `Area.spaces`, so it bailed out before rendering at all.  The unit test's fake
  had been written to match the bug - which is exactly why only the Blender run
  caught it.  The fake now uses `spaces`, so the tests guard the real name.

Verification: a real-Blender 5.2 GUI pass ran 27 checks, all passing - our own
handler claims the Coworker panel and the Text Editor while Blender's classes are
left untouched, so stock drop behaviour elsewhere is unchanged; the render writes
a real PNG at the scene resolution, follows the viewport view, restores
`scene.camera` and the user's own render settings, and leaves no object or camera
datablock behind - a 1920x1080 / 4096-sample scene is captured at 1024x576 in
under two seconds.  (The three checks that asserted the built-in handlers were
wrapped were retired with the wrapper; on Blender 5.2 they passed either way,
which is part of why the crash is not reproducible here.)
Unit tests: the new regression tests cover the zero-reported-size, empty-write and
settings-restore cases.

## 7. ⚠️ Known limitations / pinned follow-ups

- 👁️ **Vision gating**: the buttons are not hidden for non-vision models.
  `llm_manager.supports_vision()` (preset `vision` flag / mmproj presence)
  with a disabled-state tooltip is a planned follow-up - out of scope here.
- 1️⃣ **One image per send**: the plumbing carries a LIST (`attachments`), the
  socket holds one datablock (Blender-standard). Multi-image sockets would be
  a UI change only.
- 🔁 **Auto-continue / flatten rescue paths** rebuild the payload from raw
  history, so they re-send WITHOUT the image blocks (same fate as the
  pre-existing pending screenshot; the marker text still tells the model an
  image was attached to the turn).
- 🪄 **Downscale fidelity**: `imbuf` downscaling is capability-guarded; in an
  environment with a stub imbuf an oversized payload falls back to raw bytes
  (the MCP screenshot tools share this behaviour).
