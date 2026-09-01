# BFA Coworker — Tier 3e: Chat UI Refinement

**Date**: 2026-08-25
**Status**: ✅ Implemented
**Depends on**: Existing chat panel infrastructure (`ui_chat.py`, `agent_controller.py`, `preferences.py`)

---

## Case

The chat history panel has two structural problems that make it hard for users to follow what the agent is doing:

1. **Fake turn boundaries** — The agent injects `role="user"` messages during processing (auto-continue, entity context, spiral correction, tool-result prompts). The UI treats every `role="user"` message as a new turn, so one user message can produce "Turn 5" in the display. Turns should be user-orientated: one user send = one turn.

2. **3-turn truncation** — `visible_turns = turns[-3:]` hardcodes showing only the last 3 turns. Users want all history visible until they explicitly clear it.

3. **Box hierarchy is flat** — Reasoning, tool calls, and agent messages are all siblings in the turn box. The user wants a clear nested hierarchy:
   - Turn box → user prompt → collapsible process sub-box → (reasoning | tool | agent message | reasoning | tool | ...) → agent conclusion

4. **Turns persist across Blender sessions** — History is saved to disk and reloaded, but turn boundaries should reset when Blender closes/opens (since the agent's context window resets anyway).

---

## Solution

### Phase 1: Tag Real User Messages (~5 LOC, 1 file)

The root cause of fake turn boundaries is that `run_conversation_turn` injects many `role="user"` messages during agent processing, and the UI treats every `role="user"` as a new turn.

**Step 1.1 — Add `turn_start` flag to real user messages**

In `agent_controller.py`, line ~2994, the **only** place where a real user-initiated message enters history:

```python
# Before:
history.append({"role": "user", "content": user_message})

# After:
history.append({"role": "user", "content": user_message, "turn_start": True})
```

All other `role="user"` appends (auto-continue "Continue.", entity context, spiral correction, tool-result prompts, max-iteration summary) stay untagged — they are agent-injected and should NOT start a new turn.

**Locations of agent-injected `role="user"` messages** (all in `agent_controller.py`):

| Line | Content | Purpose |
|------|---------|---------|
| ~3232 | `"Continue."` | Auto-continue on finish_reason=length |
| ~3510 | `ctx` (entity diff) | Entity tracking context |
| ~3552 | `corrective` | Spiral detection corrective message |
| ~3565 | `"The tool results are above..."` | Prompt LLM to respond after tool calls |
| ~3582 | `"[System: All tool calls are complete...]"` | Max-iteration summary prompt |

**File modified:** `addon/bfa_coworker/agent_controller.py`

**Verification:**
1. Send a message that triggers tool calls → verify only 1 turn appears in the UI
2. Check saved JSON history → the real user message has `"turn_start": true`
3. Agent-injected messages (entity context, spiral correction) appear inside the turn, not as separate turns

---

### Phase 2: Fix Turn Grouping in UI (~50 LOC, 1 file)

The turn-grouping loop in `BFACW_PT_chat_panel.draw()` needs to split on `turn_start` instead of `role == "user"`, with backward-compatible fallback for old saved sessions.

**Step 2.1 — Rewrite the turn-grouping loop**

Replace the current logic (lines ~1460-1475 in `ui_chat.py`):

```python
# Current (broken):
turns: list[list[dict]] = []
current_turn: list[dict] = []
for msg in history:
    role = msg.get("role", "")
    if role == "user":
        if current_turn:
            turns.append(current_turn)
        current_turn = [msg]
    elif role in ("assistant", "tool", "reasoning"):
        current_turn.append(msg)
if current_turn:
    turns.append(current_turn)
```

With:

```python
# New (correct):
turns: list[list[dict]] = []
current_turn: list[dict] = []
for msg in history:
    role = msg.get("role", "")
    is_turn_start = msg.get("turn_start", False)
    # Backward compat: old sessions won't have turn_start flag.
    # Treat any role="user" as a turn start ONLY if it has turn_start=True
    # OR if it's from an old session (no turn_start key at all).
    if role == "user" and (is_turn_start or "turn_start" not in msg):
        if current_turn:
            turns.append(current_turn)
        current_turn = [msg]
    elif role in ("assistant", "tool", "reasoning", "user"):
        current_turn.append(msg)
if current_turn:
    turns.append(current_turn)
```

Note: `"user"` is added to the `elif` so agent-injected user messages get included in the current turn rather than starting a new one.

**Step 2.2 — Fix `turn_num` calculation**

Replace the fragile `turns.index(turn) + 1` (O(n²)) with `enumerate`:

```python
# Before:
for turn_idx, turn in enumerate(turn_iter):
    ...
    turn_num = turns.index(turn) + 1  # Per user-message turn number

# After:
for turn_idx, (turn_num, turn) in enumerate(turn_iter, 1):
    ...
    # turn_num is now the 1-based turn number directly
```

**Step 2.3 — Fix the inner message classification**

The inner loop that classifies messages within a turn needs to handle agent-injected `role="user"` messages as process messages (not as the user message):

```python
# Current:
for msg in turn:
    role = msg.get("role", "")
    c2 = msg.get("content", "")
    is_sys = (role == "user" and isinstance(c2, str) and c2.startswith("[System:"))
    if role == "user" and not is_sys:
        user_msg = msg
    elif role in ("reasoning", "tool") or is_sys:
        process_msgs.append(msg)
    elif role == "assistant":
        if not msg.get("tool_calls"):
            conclusion_msg = msg

# New:
for msg in turn:
    role = msg.get("role", "")
    c2 = msg.get("content", "")
    is_turn_start = msg.get("turn_start", False) or "turn_start" not in msg
    is_sys = (role == "user" and isinstance(c2, str) and c2.startswith("[System:"))
    if role == "user" and not is_sys and is_turn_start:
        user_msg = msg
    elif role in ("reasoning", "tool", "user") or is_sys:
        process_msgs.append(msg)
    elif role == "assistant":
        if not msg.get("tool_calls"):
            conclusion_msg = msg
```

**File modified:** `addon/bfa_coworker/ui_chat.py`

**Verification:**
1. Send a message → verify "Turn 1" for the entire agent cycle
2. Send a second message → verify "Turn 2"
3. Check that agent-injected messages appear inside the turn's process box
4. Load an old saved session (no `turn_start` flag) → verify it still displays correctly

---

### Phase 3: Box Hierarchy Refinement (~80 LOC, 1 file)

The current layout has reasoning, tool calls, and agent messages as flat siblings. The user wants a clear nested hierarchy:

```
____ Turn Box: Status + Turn No _____
| The user Prompt (always show)
____ sub box, collapsible ____
| |- Reason 1 (sub-collapsible)
| |- Tool 1
| |- Reason 2 (sub-collapsible)
| |- Tool 2
| |- Agent message (inline)
| |- Reason 3 (sub-collapsible)
| |- Tool 3, error
| |- Reason 4 (sub-collapsible)
| |- Tool 4
| |- Result / Conclusion
| Agent Conclusion
___ Turn end __
```

**Step 3.1 — Restructure the process sub-box**

Replace the flat `pb` box with a structured layout that preserves message order:

```python
if process_msgs:
    tb2.separator()
    pb = tb2.box()
    pb.label(text="Process ({:d} steps)".format(len(process_msgs)), icon="SORTTIME")
    for pm in process_msgs:
        pr = pm.get("role", "")
        pc = pm.get("content", "")
        is_sm = (pr == "user" and isinstance(pc, str) and pc.startswith("[System:"))
        if is_sm:
            sb = pb.box()
            sb.label(text="System Context", icon="INFO")
            _draw_multiline(sb, pc)
        elif pr == "reasoning":
            _draw_reasoning(
                pb, pc, pm.get("label", "Thinking"),
                is_thinking=state.is_thinking,
                thinking_dots=state.thinking_dots,
                message_index=history.index(pm),
            )
        elif pr == "tool":
            tn = pm.get("name", "")
            ts = pm.get("summary", "")
            ie = (
                '"status": "error"' in (pc or "")
                or (pc or "").startswith("Error")
            )
            d = ts if ts else (pc or "")
            if not ts and len(d) > 200:
                d = d[:200] + "..."
            _draw_tool_inline(pb, tn, d, ie, message_index=history.index(pm))
        elif pr == "user":
            # Agent-injected user messages (entity context, spiral correction)
            sb = pb.box()
            sb.label(text="Agent Context", icon="INFO")
            _draw_multiline(sb, pc)
        elif pr == "assistant":
            # Intermediate assistant messages (e.g. from auto-continue)
            sb = pb.box()
            sb.label(text="Agent Note", icon="CONSOLE")
            _draw_multiline(sb, pc)
```

**Step 3.2 — Show live streaming text inside the process box**

When the agent is thinking and streaming text, show it inside the process box (not outside):

```python
# After the process_msgs loop, inside the `if tb2:` block:
if state.is_thinking and state.streaming_text and turn_idx == 0:
    pb.separator()
    sb = pb.box()
    sb.label(text="Coworker (live):", icon="CONSOLE")
    _draw_multiline(sb, state.streaming_text[:300] + "...")
```

**Step 3.3 — Ensure conclusion is always visible**

The agent conclusion should appear after the process box, not inside it:

```python
# After the process box (pb) block:
if conclusion_msg:
    turn_box.separator()
    cr = turn_box.row()
    cr.label(text="Coworker:", icon="CONSOLE")
    op = cr.operator("bfacw.copy_message", text="", icon="COPYDOWN")
    op.message_index = history.index(conclusion_msg)
    _draw_multiline(turn_box, conclusion_msg.get("content", ""))
```

**File modified:** `addon/bfa_coworker/ui_chat.py`

**Verification:**
1. Send a message that triggers reasoning + tool calls → verify the hierarchy:
   - Turn box → user prompt → collapsible "Process" box → reasoning → tool → reasoning → tool → agent conclusion
2. Send a message with an error tool call → verify error icon in the tool box
3. Send a message with entity context injection → verify "Agent Context" box inside process
4. Verify the agent conclusion appears after the process box, not inside it

---

### Phase 4: Remove 3-Turn Cap, Add Preference Toggle (~30 LOC, 2 files)

**Step 4.1 — Add `chat_max_visible_turns` to preferences**

In `preferences.py`, add a new IntProperty to `_BFACW_Preferences`:

```python
chat_max_visible_turns: IntProperty(
    name="Max Visible Turns",
    description=(
        "Maximum number of conversation turns shown in the chat panel.\n"
        "0 = show all turns (no limit). Higher values may slow the UI\n"
        "with very long conversations."
    ),
    default=0,
    min=0,
    max=100,
)
```

**Step 4.2 — Add UI in Advanced tab**

In `_draw_tab_advanced()`, add a row in the Advanced Options section:

```python
# ── Chat Display ──────────────────────────────────────────────
chat_box = layout.box()
chat_box.label(text="Chat Display", icon='SORTTIME')
chat_box.prop(self, "chat_max_visible_turns")
chat_box.label(
    text="0 = show all turns. Higher values limit history shown.",
    icon='INFO',
)
```

**Step 4.3 — Wire into the turn display logic**

In `ui_chat.py`, replace the hardcoded `visible_turns = turns[-3:]`:

```python
# Before:
visible_turns = turns[-3:]

# After:
max_turns = prefs.chat_max_visible_turns
if max_turns > 0:
    visible_turns = turns[-max_turns:]
else:
    visible_turns = turns
```

**Files modified:**
- `addon/bfa_coworker/preferences.py` — add `chat_max_visible_turns` property + UI
- `addon/bfa_coworker/ui_chat.py` — wire preference into turn display

**Verification:**
1. Set `chat_max_visible_turns` to 2 → only 2 turns shown
2. Set to 0 → all turns shown
3. Set to 50 → up to 50 turns shown
4. Verify the preference persists across Blender restarts

---

### Phase 5: Reset Turns on Blender Restart (~15 LOC, 2 files)

Turns should reset when Blender closes and opens, since the agent's context window resets anyway. The conversation history is saved to disk, but the `turn_start` flags from the previous session should not carry over — the new session starts fresh.

**Step 5.1 — Strip `turn_start` flags when loading history**

In `_load_chat_history()` in `ui_chat.py`, strip `turn_start` flags from loaded messages so old turns don't create fake boundaries in the new session:

```python
def _load_chat_history() -> list[dict]:
    """Load conversation history from disk."""
    path = _chat_history_path()
    if path.exists():
        try:
            with open(str(path), "r", encoding="utf-8") as fh:
                history = json.load(fh)
            # Strip turn_start flags from loaded history — turns reset
            # on Blender restart since the agent's context resets.
            for msg in history:
                msg.pop("turn_start", None)
            return history
        except (json.JSONDecodeError, OSError):
            pass
    return []
```

This means:
- Old sessions display correctly (backward compat fallback treats all `role="user"` as turn starts)
- New sessions start fresh — the first user message in the new session creates Turn 1

**Step 5.2 — Clear history on agent stop (optional)**

In `BFACW_OT_agent_stop.execute()`, optionally clear the conversation history so the next start is clean:

```python
# Optional: clear history on stop so next start is fresh
agent_controller._agent_state.conversation_history.clear()
```

This is optional — the user may want to keep history across stop/start cycles. The `turn_start` stripping in `_load_chat_history()` is the primary mechanism.

**Files modified:**
- `addon/bfa_coworker/ui_chat.py` — strip `turn_start` in `_load_chat_history()`

**Verification:**
1. Send messages, close Blender, reopen → verify turns reset to 0
2. Old saved sessions (no `turn_start` flag) still display correctly
3. New session starts with Turn 1 on first message

---

### Phase 6: Remove "You:" Prefix (Already Done)

The `"You: "` prefix was removed from user message display in the turn box. The turn box header already shows "Turn N" with a message preview, making the prefix redundant.

---

## Summary of All Changes

| Phase | What | Files Changed | Files New | LOC |
|-------|------|:-------------:|:---------:|:---:|
| 1 | Tag real user messages with `turn_start` | 1 | 0 | ~5 |
| 2 | Fix turn grouping in UI | 1 | 0 | ~50 |
| 3 | Box hierarchy refinement | 1 | 0 | ~80 |
| 4 | Remove 3-turn cap, add preference toggle | 2 | 0 | ~30 |
| 5 | Reset turns on Blender restart | 1 | 0 | ~15 |
| **Total** | | **3** | **0** | **~180** |

## Files Modified

| File | Changes |
|------|---------|
| `addon/bfa_coworker/agent_controller.py` | Add `"turn_start": True` to real user message (line ~2994) |
| `addon/bfa_coworker/ui_chat.py` | Rewrite turn grouping logic; restructure process box hierarchy; wire preference; strip `turn_start` on load |
| `addon/bfa_coworker/preferences.py` | Add `chat_max_visible_turns` IntProperty + Chat Display UI in Advanced tab |

## Key Decisions

| Decision | Rationale |
|----------|-----------|
| **Tag-based turn boundaries** | A `turn_start` flag on the message dict is self-contained, survives JSON serialization, and doesn't require a separate data structure |
| **Backward compat via fallback** | Old sessions without `turn_start` fall back to `role == "user"` behavior — no migration needed |
| **Strip `turn_start` on load** | Turns reset on Blender restart since the agent's context window resets anyway |
| **Preference default is 0 (unlimited)** | User explicitly wants all history visible by default |
| **Agent-injected messages inside turn** | Entity context, spiral correction, etc. belong inside the turn's process box — they provide useful context |
| **Conclusion outside process box** | The agent's final answer should always be visible, not buried inside a collapsible section |

## Testing Guide

### Phase 1-2: Turn Grouping

| Step | Expected Result |
|------|----------------|
| Send "create a cube and add a material" | Single "Turn 1" for the entire agent cycle |
| Send "now rotate it 45 degrees" | "Turn 2" appears |
| Check saved JSON | Real user message has `"turn_start": true` |
| Load old session file | Displays correctly with backward compat |

### Phase 3: Box Hierarchy

| Step | Expected Result |
|------|----------------|
| Send message with reasoning + tools | Process box shows reasoning → tool → reasoning → tool in order |
| Send message with error | Error tool shows warning icon |
| Send message with entity context | "Agent Context" box inside process |
| Agent conclusion | Always visible after process box |

### Phase 4: Preference Toggle

| Step | Expected Result |
|------|----------------|
| Set Max Visible Turns to 2 | Only 2 turns shown |
| Set to 0 | All turns shown |
| Restart Blender | Preference persists |

### Phase 5: Turn Reset

| Step | Expected Result |
|------|----------------|
| Send messages, close Blender, reopen | Turns reset, first message is Turn 1 |
| Old session file loads | Displays correctly |
