"""
Tests for run-loop orchestration helpers in agent_controller.py.

These helpers keep the agent from spiraling on repeated errors and keep the
prompt inside a small local model's context window:

1. ``_trim_tool_result`` must keep the TAIL of an error message (Python
   tracebacks put the actual exception on the last lines). Head-only
   truncation left the model blind to the real error and it retried the
   same broken code forever.

2. The internally generated smart-undo / cleanup payloads
   (``_undo_code``, ``_build_cleanup_code``) must carry the
   ``# blmcp-toolcode-skip-preflight`` marker so the bridge runs them
   inline on the main thread -- otherwise ``bpy.ops.ed.undo()`` /
   undo_push / entity snapshot report "No window/area available" in the
   worker thread and the fallback cleanup is called with no snapshot
   data to work from.

3. ``_prompt_candidates`` / ``_get_system_prompt`` must pick the compact
   prompt for local models and find the deployed ``vendor/blmcp/data/``
   layout (issue #62 -- the auto-detection read a non-existent
   ``LLMConfig.local_llm_port``, so the compact prompt never loaded).

4. ``_repair_tool_call_pairs`` must drop half-finished tool-call exchanges
   in both directions; a slice that cuts one in half makes llama-server's
   Jinja template return 400 "Unexpected message role".

5. ``_fit_history_to_budget`` must trim the oldest exchanges to fit a token
   budget while always preserving the system prompt.

6. ``_flatten_for_plain_chat`` must not emit consecutive same-role messages
   after mapping ``tool`` results to ``user``.

7. ``_classify_llm_500`` must distinguish a template rejection from a
   resource/hardware fault (issue #63). Reshaping the request is only
   correct for the former; applying it to the latter silently downgrades
   the session to text-based tool calling and hides the real cause.

8. ``_flatten_for_plain_chat`` must merge same-role runs even when content
   is multimodal, and must fold a second ``system`` message into the first
   -- either shape otherwise still trips "Unexpected message role".

9. The benchmark step recorder must treat a step as FAILED when
   ``run_conversation_turn`` returns normally but sets
   ``_agent_state.error``. It does not raise on an LLM failure, so
   checking the exception alone reported every failed step as a success
   and the Diagnostics panel showed nothing but a timing.

Loaded from source (the module imports bpy, which is not available in
the unit-test environment).

Run with::

    python -m unittest tests.test_orchestration_helpers -v
"""

__all__ = ()

import ast
import contextlib
import io
import json
import os
import types
import unittest
from pathlib import Path

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_AC_PATH = os.path.join(_REPO, "addon", "bfa_coworker", "agent_controller.py")


def _load_source():
    with open(_AC_PATH, "r", encoding="utf-8") as fh:
        return fh.read()


def _extract_func(source: str, name: str, extra: dict | None = None) -> object:
    """Extract one top-level function from agent_controller.py source."""
    marker = "\ndef {:s}(".format(name)
    start = source.find(marker)
    if start < 0:
        raise ImportError("{:s} not found in source".format(name))
    start += 1  # skip the leading newline

    # End at the next top-level def/class/comment-divider at same indent.
    end = len(source)
    search_from = start + 100
    for m in ["\ndef _", "\ndef ", "\nclass ", "\n# ---"]:
        idx = source.find(m, search_from)
        if 0 <= idx < end:
            end = idx

    func_source = source[start:end]
    mod = types.ModuleType("_ac_extract")
    mod.__dict__["json"] = json
    # Signature annotations are evaluated at exec time; the helper test
    # module does not import the addon, so stub the annotation types.
    mod.__dict__["_EntityDiff"] = object
    mod.__dict__["Any"] = object
    # Functions that resolve paths relative to the module use ``__file__``.
    mod.__dict__["__file__"] = _AC_PATH
    if extra:
        mod.__dict__.update(extra)
    exec(compile(func_source, _AC_PATH, "exec"), mod.__dict__)
    return mod.__dict__[name]


_trim_tool_result = _extract_func(
    _load_source(), "_trim_tool_result", {"_MAX_TOOL_RESULT_CHARS": 2000},
)
_trim_history_tool_results = _extract_func(
    _load_source(), "_trim_history_tool_results",
    {"_trim_tool_result": _trim_tool_result, "_MAX_TOOL_RESULT_CHARS": 2000},
)
_undo_code = _extract_func(_load_source(), "_undo_code")
_build_cleanup_code = _extract_func(_load_source(), "_build_cleanup_code")

# Prompt-variant helpers.  ``_prompt_candidates`` needs ``Path`` in its
# namespace; ``_get_system_prompt`` additionally needs ``textwrap`` and the
# module-level cache/constants it closes over.
_prompt_candidates = _extract_func(
    _load_source(), "_prompt_candidates", {"Path": Path}
)
_get_system_prompt = _extract_func(
    _load_source(),
    "_get_system_prompt",
    {
        "Path": Path,
        "textwrap": __import__("textwrap"),
        "_system_prompt_cache": {},
        "_prompt_candidates": _prompt_candidates,
        "_use_compact_prompt": lambda: False,
    },
)

# Tool-call pair repair + token budget helpers.
_repair_tool_call_pairs = _extract_func(_load_source(), "_repair_tool_call_pairs")
_message_text_length = _extract_func(_load_source(), "_message_text_length")
_estimate_messages_tokens = _extract_func(
    _load_source(), "_estimate_messages_tokens",
    {"_message_text_length": _message_text_length, "_CHARS_PER_TOKEN": 3.5},
)
_fit_history_to_budget = _extract_func(
    _load_source(), "_fit_history_to_budget",
    {
        "_estimate_messages_tokens": _estimate_messages_tokens,
        "_repair_tool_call_pairs": _repair_tool_call_pairs,
    },
)
_flatten_for_plain_chat = _extract_func(_load_source(), "_flatten_for_plain_chat")
_content_as_text = _extract_func(_load_source(), "_content_as_text")
_describe_message_roles = _extract_func(_load_source(), "_describe_message_roles")
_strip_ui_only_from_history = _extract_func(
    _load_source(), "_strip_ui_only_from_history"
)
_sanitize_loaded_history = _extract_func(
    _load_source(), "_sanitize_loaded_history",
    {"_repair_tool_call_pairs": _repair_tool_call_pairs},
)
# ``_flatten_for_plain_chat`` calls ``_content_as_text``, so the extracted
# function needs it in its namespace.
_flatten_for_plain_chat = _extract_func(
    _load_source(), "_flatten_for_plain_chat",
    {"_content_as_text": _content_as_text},
)
# Request-shape diagnostics (Phase A).  ``_describe_history_for_log`` and
# ``_count_empty_content_messages`` both call ``_content_as_text``.
_count_empty_content_messages = _extract_func(
    _load_source(), "_count_empty_content_messages",
    {"_content_as_text": _content_as_text},
)
_describe_history_for_log = _extract_func(
    _load_source(), "_describe_history_for_log",
    {
        "_content_as_text": _content_as_text,
        "_describe_message_roles": _describe_message_roles,
        "_count_empty_content_messages": _count_empty_content_messages,
    },
)
_toolcall_fault_message = _extract_func(
    _load_source(), "_toolcall_fault_message",
)

# LLM 500 fault classification.  The marker tuples and the two result
# constants are module-level names, so the extracted function needs them in
# its namespace.  They are mirrored here, using the same private names as
# the source, rather than parsed out of it: if a marker were added to the
# source and read from there, a behaviour test could silently pass against
# a stale expectation.  ``TestClassifierMarkerSync`` below closes that gap
# by comparing this mirror against the real constants and failing on drift.
_FAULT_TEMPLATE = "template"
_FAULT_SERVER = "server"
_FAULT_TOOLCALL = "toolcall"

_SERVER_FAULT_MARKERS = (
    "out of memory",
    "outofmemory",
    "out of device memory",
    "outofdevicememory",
    "outofhostmemory",
    "cuda error",
    "cudamalloc",
    "ggml_assert",
    "ggml_vulkan",
    "allocatememory",
    "failed to allocate",
    "failed to allocate vulkan0 buffer",
    "failed to allocate buffer for kv cache",
    "failed to allocate gpu buffer",
)

_TEMPLATE_FAULT_MARKERS = (
    "unable to generate parser",
    "unexpected message role",
    "no user query found",
    "jinja",
    "chat template",
    "template",
)

_TOOLCALL_FAULT_MARKERS = (
    "failed to parse tool call",
    "parse tool call arguments",
    "tool call arguments as json",
    "invalid string: missing closing quote",
    "missing closing quote",
)

_classify_llm_500 = _extract_func(
    _load_source(), "_classify_llm_500",
    {
        "_FAULT_TEMPLATE": _FAULT_TEMPLATE,
        "_FAULT_SERVER": _FAULT_SERVER,
        "_FAULT_TOOLCALL": _FAULT_TOOLCALL,
        "_SERVER_FAULT_MARKERS": _SERVER_FAULT_MARKERS,
        "_TEMPLATE_FAULT_MARKERS": _TEMPLATE_FAULT_MARKERS,
        "_TOOLCALL_FAULT_MARKERS": _TOOLCALL_FAULT_MARKERS,
    },
)


class TestTrimToolResultErrorTail(unittest.TestCase):
    """Error results keep the tail (the actual exception) for the LLM."""

    def _mk_error(self, body: str, status: str = "error") -> str:
        return json.dumps({"status": status, "message": body})

    def test_error_tail_preserved(self):
        """Traceback's last line (the exception) survives the 500-char trim."""
        tb = (
            'Traceback (most recent call last):\n'
            '  File "C:\\...\\mcp_to_blender_server.py", line 750, in _execute_code\n'
            '    raise _exec_error[0]\n'
            '  File "C:\\...\\mcp_to_blender_server.py", line 771, in _run_code\n'
            '    exec(code, namespace)\n'
            '  File "<string>", line 14, in <module>\n'
            '    obj1 = bpy.context.active_object\n'
            'AttributeError: \'Context\' object has no attribute \'active_object\''
        )
        # Pad with stack-preamble noise so the message exceeds the budget.
        noisy = "Some repeated context line that adds tokens far from the error.\n" * 40 + tb
        trimmed = _trim_tool_result(self._mk_error(noisy), max_chars=500)
        # The exception type+message must still be visible to the model.
        self.assertIn("AttributeError", trimmed)
        self.assertIn("active_object", trimmed)
        self.assertIn("line 14", trimmed)
        self.assertIn("chars trimmed", trimmed)
        # Must stay within the token budget (plus a little slack).
        self.assertLessEqual(len(trimmed), 560)

    def test_error_tail_short_message_unchanged(self):
        """Messages within budget are returned whole."""
        msg = "simple error with a reason"
        result = _trim_tool_result(self._mk_error(msg))
        self.assertIn(msg, result)
        self.assertNotIn("chars trimmed", result)

    def test_success_result_still_head_trimmed(self):
        """Success results keep the (head) behavior -- only errors favor tail."""
        big = json.dumps({"status": "ok", "result": {"items": ["x"] * 300}})
        trimmed = _trim_tool_result(big, max_chars=200)
        self.assertLessEqual(len(trimmed), 240)
        self.assertIn('"items"', trimmed)

    def test_non_json_fallback(self):
        """Non-JSON output falls back to head truncation without crashing."""
        raw = "plain text " * 200
        trimmed = _trim_tool_result(raw, max_chars=100)
        self.assertIn("more chars", trimmed)


class TestPromptVariantSelection(unittest.TestCase):
    """Compact prompt is chosen for local mode; the vendor path is searched.

    Regression guard for issue #62: the auto-detection read a non-existent
    ``LLMConfig.local_llm_port``, so the compact prompt never loaded.
    """

    def test_compact_candidates_prefer_compact_file(self):
        """Compact variant tries prompts_compact.yml before prompts.yml."""
        names = [p.name for p in _prompt_candidates(True)]
        self.assertEqual(names[0], "prompts_compact.yml")
        self.assertIn("prompts.yml", names)

    def test_full_candidates_never_use_compact_file(self):
        """Remote/full variant must not fall back to the compact prompt."""
        names = [p.name for p in _prompt_candidates(False)]
        self.assertEqual(names, ["prompts.yml", "prompts.yml"])

    def test_candidates_include_deployed_vendor_layout(self):
        """The installed-addon layout (vendor/blmcp/data) must be searched."""
        paths = [str(p) for p in _prompt_candidates(True)]
        self.assertTrue(
            any("vendor" in p and "blmcp" in p for p in paths),
            "vendor/blmcp/data path missing from candidates: {:s}".format(str(paths)),
        )

    def test_compact_and_full_load_different_text(self):
        """The two variants resolve to different prompt text."""
        # The loader prints diagnostics containing emoji, which the Windows
        # console codec (cp1252) cannot encode -- capture to a StringIO.
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            compact = _get_system_prompt(use_compact=True)
            full = _get_system_prompt(use_compact=False)
        self.assertTrue(compact.strip())
        self.assertTrue(full.strip())
        self.assertNotEqual(compact, full)


class TestRepairToolCallPairs(unittest.TestCase):
    """Half-finished tool-call exchanges must be dropped in both directions."""

    @staticmethod
    def _assistant_call(call_id: str = "c1") -> dict:
        return {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": call_id, "type": "function",
                            "function": {"name": "t", "arguments": "{}"}}],
        }

    @staticmethod
    def _tool_result(call_id: str = "c1") -> dict:
        return {"role": "tool", "tool_call_id": call_id, "name": "t", "content": "ok"}

    def test_orphaned_tool_result_dropped(self):
        """A tool result whose assistant parent was sliced away is dropped."""
        messages = [
            {"role": "system", "content": "sys"},
            self._tool_result(),  # parent missing
            {"role": "user", "content": "hi"},
        ]
        repaired = _repair_tool_call_pairs(messages)
        self.assertEqual([m["role"] for m in repaired], ["system", "user"])

    def test_orphaned_assistant_tool_call_dropped(self):
        """An assistant tool_calls whose replies were sliced away is dropped.

        This is the case the old helper missed and the likely cause of the
        llama-server 400 'Unexpected message role' after 1-2 prompts.
        """
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
            self._assistant_call(),  # replies missing
        ]
        repaired = _repair_tool_call_pairs(messages)
        self.assertEqual([m["role"] for m in repaired], ["system", "user"])

    def test_complete_pair_preserved(self):
        """A well-formed exchange survives untouched."""
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
            self._assistant_call(),
            self._tool_result(),
        ]
        repaired = _repair_tool_call_pairs(messages)
        self.assertEqual(
            [m["role"] for m in repaired],
            ["system", "user", "assistant", "tool"],
        )

    def test_later_tool_result_does_not_satisfy_earlier_call(self):
        """Only the run of tool messages directly after the call counts."""
        messages = [
            self._assistant_call("c1"),  # no reply directly after
            {"role": "user", "content": "hi"},
            self._assistant_call("c2"),
            self._tool_result("c2"),
        ]
        repaired = _repair_tool_call_pairs(messages)
        self.assertEqual(
            [m["role"] for m in repaired],
            ["user", "assistant", "tool"],
        )


class TestTokenBudget(unittest.TestCase):
    """The prompt is trimmed to fit the model's context window."""

    def test_message_text_length_counts_tool_calls(self):
        """Tool-call names and arguments count toward the message size."""
        plain = {"role": "assistant", "content": "x" * 10}
        with_calls = {
            "role": "assistant",
            "content": "x" * 10,
            "tool_calls": [{"function": {"name": "execute_blender_code",
                                         "arguments": "y" * 50}}],
        }
        self.assertGreater(
            _message_text_length(with_calls), _message_text_length(plain)
        )

    def test_message_text_length_counts_image_blocks(self):
        """Multimodal image blocks count toward the message size."""
        message = {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": "data:image/png;base64," + "A" * 100}},
                {"type": "text", "text": "look"},
            ],
        }
        self.assertGreater(_message_text_length(message), 100)

    def test_fits_within_budget_returns_unchanged(self):
        """A prompt already inside budget is returned as-is."""
        messages = [{"role": "system", "content": "sys"},
                    {"role": "user", "content": "hi"}]
        self.assertIs(_fit_history_to_budget(messages, 100000), messages)

    def test_oversized_prompt_is_trimmed(self):
        """An oversized prompt is trimmed below the budget."""
        messages = [{"role": "system", "content": "sys"}]
        for i in range(50):
            messages.append({"role": "user", "content": "u{:d} ".format(i) * 200})
            messages.append({"role": "assistant", "content": "a{:d} ".format(i) * 200})
        trimmed = _fit_history_to_budget(messages, 500)
        self.assertLessEqual(_estimate_messages_tokens(trimmed), 500)
        self.assertLess(len(trimmed), len(messages))

    def test_system_prompt_always_kept(self):
        """The system prompt survives even an impossibly small budget.

        The last user message is pinned alongside it: trimming the user's
        question away leaves the model with nothing to answer, and it then
        invents a task.  A prompt with no user turn is never useful, so the
        budget is allowed to overflow rather than produce one.
        """
        messages = [{"role": "system", "content": "sys"}]
        for i in range(20):
            messages.append({"role": "user", "content": "u{:d} ".format(i) * 200})
        trimmed = _fit_history_to_budget(messages, 1)
        self.assertEqual(trimmed[0]["role"], "system")
        # The system prompt plus the pinned current user message.
        self.assertEqual(len(trimmed), 2)
        self.assertEqual(trimmed[-1]["role"], "user")
        self.assertIn("u19", trimmed[-1]["content"])

    def test_zero_budget_disables_trimming(self):
        """Budget 0 (remote path) leaves the prompt untouched."""
        messages = [{"role": "user", "content": "x" * 100000}]
        self.assertIs(_fit_history_to_budget(messages, 0), messages)


class TestFlattenRoleAlternation(unittest.TestCase):
    """Flattened conversations must not contain consecutive same-role runs."""

    def test_consecutive_user_messages_merged(self):
        """tool -> user mapping must not produce a user,user run."""
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "do it"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "c1"}]},
            {"role": "tool", "tool_call_id": "c1", "name": "t", "content": "ok"},
            {"role": "user", "content": "Please respond."},
        ]
        flat = _flatten_for_plain_chat(messages)
        roles = [m["role"] for m in flat]
        for a, b in zip(roles, roles[1:]):
            self.assertNotEqual(a, b, "consecutive same role: {:s}".format(str(roles)))
        self.assertIn("ok", flat[-1]["content"])
        self.assertIn("Please respond.", flat[-1]["content"])

    def test_tool_calls_and_ids_stripped(self):
        """tool_calls / tool_call_id must not survive flattening."""
        messages = [
            {"role": "assistant", "content": "x", "tool_calls": [{"id": "c1"}]},
            {"role": "tool", "tool_call_id": "c1", "name": "t", "content": "ok"},
        ]
        flat = _flatten_for_plain_chat(messages)
        for msg in flat:
            self.assertNotIn("tool_calls", msg)
            self.assertNotIn("tool_call_id", msg)

    def test_caller_messages_not_mutated(self):
        """Flattening must not mutate the caller's message dicts."""
        original = {"role": "assistant", "content": "x", "tool_calls": [{"id": "c1"}]}
        messages = [original]
        _flatten_for_plain_chat(messages)
        self.assertIn("tool_calls", original)

    def test_multimodal_content_does_not_block_merging(self):
        """A list-content message must still merge with a same-role neighbour.

        Previously the merge required both contents to be ``str``, so a
        multimodal (list) block left a ``user, user`` run that strict Jinja
        templates reject with "Unexpected message role".
        """
        messages = [
            {"role": "user", "content": [{"type": "text", "text": "look"}]},
            {"role": "user", "content": "and again"},
        ]
        flat = _flatten_for_plain_chat(messages)
        roles = [m["role"] for m in flat]
        self.assertEqual(roles, ["user"])
        self.assertIn("look", flat[0]["content"])
        self.assertIn("and again", flat[0]["content"])

    def test_adjacent_system_messages_merged(self):
        """A second system message must fold into the first.

        Injected tool text can add a system message mid-conversation, which
        many templates reject outright.
        """
        messages = [
            {"role": "system", "content": "rules"},
            {"role": "system", "content": "tools"},
            {"role": "user", "content": "go"},
        ]
        flat = _flatten_for_plain_chat(messages)
        roles = [m["role"] for m in flat]
        self.assertEqual(roles, ["system", "user"])
        self.assertIn("rules", flat[0]["content"])
        self.assertIn("tools", flat[0]["content"])

    def test_no_consecutive_same_role_after_flatten(self):
        """The flattened output must always alternate roles."""
        messages = [
            {"role": "system", "content": "s"},
            {"role": "system", "content": "s2"},
            {"role": "user", "content": "u"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "c1"}]},
            {"role": "tool", "tool_call_id": "c1", "name": "t", "content": "r1"},
            {"role": "tool", "tool_call_id": "c2", "name": "t", "content": "r2"},
            {"role": "user", "content": "u2"},
        ]
        flat = _flatten_for_plain_chat(messages)
        roles = [m["role"] for m in flat]
        for a, b in zip(roles, roles[1:]):
            self.assertNotEqual(a, b, "consecutive same role: {:s}".format(str(roles)))


class TestContentAsText(unittest.TestCase):
    """``_content_as_text`` normalises string and multimodal content."""

    def test_string_passthrough(self):
        self.assertEqual(_content_as_text("hello"), "hello")

    def test_none_is_empty(self):
        self.assertEqual(_content_as_text(None), "")

    def test_text_blocks_joined(self):
        content = [
            {"type": "text", "text": "one"},
            {"type": "text", "text": "two"},
        ]
        self.assertEqual(_content_as_text(content), "one\ntwo")

    def test_image_block_contributes_no_text(self):
        """A bare image block carries no text and must not crash."""
        content = [{"type": "image_url", "image_url": {"url": "data:..."}}]
        self.assertEqual(_content_as_text(content), "")

    def test_mixed_blocks_keep_text_only(self):
        content = [
            {"type": "text", "text": "caption"},
            {"type": "image_url", "image_url": {"url": "data:..."}},
        ]
        self.assertEqual(_content_as_text(content), "caption")


class TestDescribeMessageRoles(unittest.TestCase):
    """``_describe_message_roles`` renders the shape for diagnostics."""

    def test_renders_comma_separated_roles(self):
        messages = [
            {"role": "system"},
            {"role": "user"},
            {"role": "assistant"},
            {"role": "tool"},
        ]
        self.assertEqual(
            _describe_message_roles(messages), "system,user,assistant,tool"
        )

    def test_empty_list(self):
        self.assertEqual(_describe_message_roles([]), "")

    def test_missing_role_is_question_mark(self):
        self.assertEqual(_describe_message_roles([{}]), "?")


class TestStripUiOnlyFromHistory(unittest.TestCase):
    """UI-only entries must never reach the LLM.

    The startup greeting is a real history entry so the chat panel can render
    it, but sending it adds a phantom assistant turn ahead of the system
    prompt -- which made the model answer the greeting instead of the user's
    actual request.
    """

    def test_ui_only_message_removed(self):
        messages = [
            {"role": "system", "content": "rules"},
            {"role": "assistant", "content": "Ok, now we are ready!", "ui_only": True},
            {"role": "user", "content": "make a cube"},
        ]
        cleaned = _strip_ui_only_from_history(messages)
        roles = [m["role"] for m in cleaned]
        self.assertEqual(roles, ["system", "user"])
        self.assertNotIn("Ok, now we are ready!", str(cleaned))

    def test_ui_only_key_stripped_from_kept_messages(self):
        """The marker must not leak into the API payload."""
        messages = [{"role": "user", "content": "hi", "ui_only": False}]
        cleaned = _strip_ui_only_from_history(messages)
        self.assertNotIn("ui_only", cleaned[0])

    def test_normal_messages_untouched(self):
        messages = [
            {"role": "system", "content": "s"},
            {"role": "user", "content": "u"},
            {"role": "assistant", "content": "a"},
        ]
        self.assertEqual(_strip_ui_only_from_history(messages), messages)

    def test_empty_list(self):
        self.assertEqual(_strip_ui_only_from_history([]), [])

    def test_caller_messages_not_mutated(self):
        """Stripping the marker must not mutate the caller's dict."""
        original = {"role": "user", "content": "hi", "ui_only": False}
        _strip_ui_only_from_history([original])
        self.assertIn("ui_only", original)


class TestLeadingAssistantGuard(unittest.TestCase):
    """A conversation must not begin with a non-UI assistant message.

    Guards the contamination regression: the greeting was appended straight
    to the history, putting an assistant message ahead of the system prompt.
    """

    def test_guard_skips_ui_only_messages(self):
        """The guard must not drop the greeting (it is stripped later)."""
        src = _load_source()
        self.assertRegex(
            src,
            r'while history and history\[0\]\.get\("role"\) == "assistant"'
            r' and not history\[0\]\.get\("ui_only"\):',
        )

    def test_ui_only_strip_runs_before_send(self):
        """The strip must be wired into the per-request pipeline."""
        src = _load_source()
        self.assertIn("history_to_send = _strip_ui_only_from_history(history_to_send)", src)

    def test_warmup_marks_welcome_ui_only(self):
        """The greeting must be marked ui_only, not appended as a plain turn."""
        src = _load_source()
        start = src.find("def warmup_agent(")
        self.assertGreater(start, 0, "warmup_agent not found")
        end = src.find("\ndef ", start + 100)
        body = src[start:end if end > 0 else len(src)]
        self.assertIn('"ui_only": True', body)


class TestRequestShapeDiagnostics(unittest.TestCase):
    """Phase A diagnostics must report the shape actually sent to the model.

    These logs are the evidence base for the history-contamination and 400
    investigations: the role sequence shows the offending run, the empty
    count flags assistant turns left blank after tool_calls are stripped, and
    the first user message reveals whether the model is answering a stale
    prompt.
    """

    def test_counts_empty_content_messages(self):
        messages = [
            {"role": "system", "content": "rules"},
            {"role": "assistant", "content": ""},
            {"role": "user", "content": "hi"},
        ]
        self.assertEqual(_count_empty_content_messages(messages), 1)

    def test_message_with_tool_calls_is_not_empty(self):
        """A tool-call message carries information even with no content."""
        messages = [{"role": "assistant", "content": "", "tool_calls": [{"id": "c1"}]}]
        self.assertEqual(_count_empty_content_messages(messages), 0)

    def test_whitespace_only_counts_as_empty(self):
        messages = [{"role": "assistant", "content": "   \n  "}]
        self.assertEqual(_count_empty_content_messages(messages), 1)

    def test_multimodal_text_counts_as_content(self):
        messages = [{"role": "user", "content": [{"type": "text", "text": "look"}]}]
        self.assertEqual(_count_empty_content_messages(messages), 0)

    def test_empty_list(self):
        self.assertEqual(_count_empty_content_messages([]), 0)

    def test_log_reports_count_roles_and_first_user(self):
        messages = [
            {"role": "system", "content": "rules"},
            {"role": "assistant", "content": "Ok, now we are ready!"},
            {"role": "user", "content": "make a cube"},
        ]
        text = _describe_history_for_log(messages)
        self.assertIn("messages   = 3", text)
        self.assertIn("roles      = system,assistant,user", text)
        # All three carry content, so nothing is empty.
        self.assertIn("empty      = 0", text)
        self.assertIn("first user = make a cube", text)

    def test_log_counts_blank_assistant_turn(self):
        """A blank assistant turn (post tool_calls strip) is reported."""
        messages = [
            {"role": "system", "content": "rules"},
            {"role": "assistant", "content": ""},
            {"role": "user", "content": "make a cube"},
        ]
        text = _describe_history_for_log(messages)
        self.assertIn("empty      = 1", text)

    def test_log_reports_first_user_not_last(self):
        """The FIRST user message is the one that reveals a stale prompt."""
        messages = [
            {"role": "system", "content": "s"},
            {"role": "user", "content": "stale living room prompt"},
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": "new request"},
        ]
        text = _describe_history_for_log(messages)
        self.assertIn("stale living room prompt", text)
        self.assertNotIn("new request", text)

    def test_log_reports_none_when_no_user_message(self):
        text = _describe_history_for_log([{"role": "system", "content": "s"}])
        self.assertIn("first user = (none)", text)

    def test_log_truncates_long_user_message(self):
        messages = [{"role": "user", "content": "x" * 500}]
        text = _describe_history_for_log(messages)
        line = [ln for ln in text.splitlines() if "first user" in ln][0]
        # 160 chars of payload plus the label.
        self.assertLess(len(line), 200)

    def test_log_handles_empty_list(self):
        text = _describe_history_for_log([])
        self.assertIn("messages   = 0", text)
        self.assertIn("first user = (none)", text)

    def test_request_shape_log_is_wired_in(self):
        """The per-request log must run before the LLM call."""
        src = _load_source()
        self.assertIn("run_conversation_turn: request shape:", src)
        self.assertIn("_describe_history_for_log(history_to_send)", src)

    def test_flatten_log_is_wired_in(self):
        """The 400 fallback must log the flattened shape."""
        src = _load_source()
        self.assertIn("flattened shape:", src)


class TestFlattenDropsNonStandardRoles(unittest.TestCase):
    """Flattened output must contain ONLY system/user/assistant.

    This is the confirmed root cause of the persistent 400: the flatten
    fallback preserved ``reasoning`` entries, whose non-standard role falls
    through the chat template's ``else`` branch and raises "Unexpected
    message role" -- so the retry failed identically to the request it was
    meant to rescue.
    """

    def test_reasoning_entries_dropped(self):
        messages = [
            {"role": "system", "content": "rules"},
            {"role": "user", "content": "make a cube"},
            {"role": "reasoning", "content": "let me think about this"},
            {"role": "assistant", "content": "ok"},
        ]
        flat = _flatten_for_plain_chat(messages)
        roles = [m["role"] for m in flat]
        self.assertNotIn("reasoning", roles)
        self.assertNotIn("let me think about this", str(flat))

    def test_unknown_role_mapped_to_user(self):
        """An unrecognised role keeps its content but becomes ``user``."""
        messages = [
            {"role": "system", "content": "s"},
            {"role": "weird_role", "content": "keep me"},
        ]
        flat = _flatten_for_plain_chat(messages)
        roles = [m["role"] for m in flat]
        self.assertNotIn("weird_role", roles)
        self.assertIn("keep me", str(flat))

    def test_output_roles_are_always_standard(self):
        """The whole point: no role outside the standard set survives."""
        messages = [
            {"role": "system", "content": "s"},
            {"role": "reasoning", "content": "r"},
            {"role": "user", "content": "u"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "c1"}]},
            {"role": "tool", "tool_call_id": "c1", "name": "t", "content": "res"},
            {"role": "weird", "content": "w"},
            {"role": "assistant", "content": "done"},
        ]
        flat = _flatten_for_plain_chat(messages)
        for msg in flat:
            self.assertIn(msg["role"], ("system", "user", "assistant"),
                          "non-standard role survived: {:s}".format(msg["role"]))

    def test_empty_assistant_turn_dropped(self):
        """A blank assistant turn (post tool_calls strip) carries nothing."""
        messages = [
            {"role": "system", "content": "s"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "c1"}]},
            {"role": "user", "content": "u"},
        ]
        flat = _flatten_for_plain_chat(messages)
        for msg in flat:
            if msg["role"] == "assistant":
                self.assertTrue(msg["content"].strip(),
                                "blank assistant turn survived")

    def test_ui_only_marker_stripped(self):
        messages = [{"role": "user", "content": "hi", "ui_only": True}]
        flat = _flatten_for_plain_chat(messages)
        for msg in flat:
            self.assertNotIn("ui_only", msg)

    def test_reasoning_only_conversation_yields_no_reasoning(self):
        messages = [{"role": "reasoning", "content": "only thinking"}]
        flat = _flatten_for_plain_chat(messages)
        self.assertEqual(flat, [])


class TestFitHistoryKeepsCurrentUserMessage(unittest.TestCase):
    """Trimming must never remove the user's current request.

    This is the confirmed root cause of the hallucinated task: with
    ``max_tokens == ctx_size`` the budget went negative, the floor was too
    small, and the user message was trimmed away entirely -- leaving the
    model with no question, so it invented one.
    """

    def test_last_user_message_survives_tiny_budget(self):
        messages = [
            {"role": "system", "content": "s" * 400},
            {"role": "user", "content": "old request"},
            {"role": "assistant", "content": "old reply"},
            {"role": "user", "content": "CURRENT REQUEST"},
        ]
        fitted = _fit_history_to_budget(messages, budget_tokens=10)
        self.assertIn("CURRENT REQUEST", str(fitted))

    def test_system_prompt_survives(self):
        messages = [
            {"role": "system", "content": "SYSTEM RULES"},
            {"role": "user", "content": "CURRENT REQUEST"},
        ]
        fitted = _fit_history_to_budget(messages, budget_tokens=10)
        self.assertEqual(fitted[0]["role"], "system")
        self.assertIn("SYSTEM RULES", str(fitted))

    def test_older_history_dropped_first(self):
        messages = [
            {"role": "system", "content": "s"},
            {"role": "user", "content": "ancient"},
            {"role": "assistant", "content": "a" * 2000},
            {"role": "user", "content": "CURRENT"},
        ]
        fitted = _fit_history_to_budget(messages, budget_tokens=50)
        self.assertNotIn("ancient", str(fitted))
        self.assertIn("CURRENT", str(fitted))

    def test_unchanged_when_it_fits(self):
        messages = [
            {"role": "system", "content": "s"},
            {"role": "user", "content": "u"},
        ]
        self.assertEqual(_fit_history_to_budget(messages, 100000), messages)

    def test_no_user_message_falls_back_to_oldest_first(self):
        """The forced-summary path has no user turn; must not crash."""
        messages = [
            {"role": "system", "content": "s"},
            {"role": "assistant", "content": "a" * 2000},
        ]
        fitted = _fit_history_to_budget(messages, budget_tokens=10)
        self.assertEqual(fitted[0]["role"], "system")

    def test_result_never_empty_when_user_present(self):
        """A prompt with no user turn is never useful."""
        messages = [
            {"role": "system", "content": "s" * 5000},
            {"role": "user", "content": "CURRENT"},
        ]
        fitted = _fit_history_to_budget(messages, budget_tokens=1)
        self.assertTrue(any(m.get("role") == "user" for m in fitted))


class TestSanitizeLoadedHistory(unittest.TestCase):
    """A history restored from disk must be cleaned before use."""

    def test_drops_ui_only_greeting(self):
        messages = [
            {"role": "system", "content": "s"},
            {"role": "assistant", "content": "Ok, now we are ready!", "ui_only": True},
            {"role": "user", "content": "hi"},
        ]
        cleaned = _sanitize_loaded_history(messages)
        self.assertNotIn("Ok, now we are ready!", str(cleaned))

    def test_drops_reasoning_entries(self):
        messages = [
            {"role": "system", "content": "s"},
            {"role": "reasoning", "content": "thinking"},
            {"role": "user", "content": "hi"},
        ]
        cleaned = _sanitize_loaded_history(messages)
        self.assertNotIn("reasoning", [m["role"] for m in cleaned])

    def test_drops_leading_assistant_message(self):
        messages = [
            {"role": "assistant", "content": "greeting"},
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "reply"},
        ]
        cleaned = _sanitize_loaded_history(messages)
        self.assertEqual(cleaned[0]["role"], "user")
        self.assertNotIn("greeting", str(cleaned))

    def test_drops_trailing_unanswered_user_message(self):
        """An interrupted turn would create a user,user run."""
        messages = [
            {"role": "system", "content": "s"},
            {"role": "assistant", "content": "a"},
            {"role": "user", "content": "interrupted"},
        ]
        cleaned = _sanitize_loaded_history(messages)
        self.assertNotIn("interrupted", str(cleaned))

    def test_drops_orphaned_tool_result(self):
        messages = [
            {"role": "system", "content": "s"},
            {"role": "tool", "tool_call_id": "c1", "name": "t", "content": "orphan"},
            {"role": "user", "content": "hi"},
        ]
        cleaned = _sanitize_loaded_history(messages)
        self.assertNotIn("orphan", str(cleaned))

    def test_ui_only_key_stripped(self):
        messages = [{"role": "user", "content": "hi", "ui_only": False}]
        cleaned = _sanitize_loaded_history(messages)
        for msg in cleaned:
            self.assertNotIn("ui_only", msg)

    def test_empty_input(self):
        self.assertEqual(_sanitize_loaded_history([]), [])

    def test_caller_not_mutated(self):
        original = {"role": "user", "content": "hi", "ui_only": False}
        _sanitize_loaded_history([original])
        self.assertIn("ui_only", original)

    def test_clean_history_passes_through(self):
        messages = [
            {"role": "system", "content": "s"},
            {"role": "user", "content": "u"},
            {"role": "assistant", "content": "a"},
        ]
        self.assertEqual(_sanitize_loaded_history(messages), messages)


class TestBudgetClamp(unittest.TestCase):
    """max_tokens must not consume the whole context window."""

    def test_clamp_is_wired_in(self):
        """The budget must clamp max_tokens, not just floor the budget."""
        src = _load_source()
        self.assertIn("exceeds", src)
        self.assertIn("clamping to", src)

    def test_floor_is_at_least_half_the_context(self):
        """The old floor (ctx//4) could not hold a system prompt."""
        src = _load_source()
        start = src.find("prompt_budget = _ctx_size - max_tokens")
        self.assertGreater(start, 0)
        window = src[max(0, start - 900):start + 400]
        self.assertIn("_ctx_size // 2", window)


class TestTrimHistoryToolResults(unittest.TestCase):
    """Tool results are stored in full but trimmed for the request.

    The chat panel renders from conversation_history, so truncating at store
    time made the user see a 500-char stub instead of the real output.
    Trimming now happens only when the request is built.
    """

    def test_oversized_tool_result_trimmed(self):
        messages = [{"role": "tool", "name": "t", "content": "x" * 5000}]
        trimmed = _trim_history_tool_results(messages, max_chars=100)
        self.assertLess(len(trimmed[0]["content"]), 5000)

    def test_small_tool_result_untouched(self):
        messages = [{"role": "tool", "name": "t", "content": "short"}]
        trimmed = _trim_history_tool_results(messages, max_chars=100)
        self.assertEqual(trimmed[0]["content"], "short")

    def test_non_tool_messages_untouched(self):
        messages = [{"role": "user", "content": "x" * 5000}]
        trimmed = _trim_history_tool_results(messages, max_chars=100)
        self.assertEqual(len(trimmed[0]["content"]), 5000)

    def test_caller_history_not_mutated(self):
        """The stored history must keep the full text for display."""
        original = {"role": "tool", "name": "t", "content": "x" * 5000}
        _trim_history_tool_results([original], max_chars=100)
        self.assertEqual(len(original["content"]), 5000)

    def test_empty_list(self):
        self.assertEqual(_trim_history_tool_results([]), [])

    def test_history_stores_full_result(self):
        """The store site must not truncate -- that is the reported bug."""
        src = _load_source()
        start = src.find('"content": result_text,')
        self.assertGreater(start, 0)
        window = src[max(0, start - 700):start + 300]
        self.assertIn('"role": "tool",', window)
        self.assertNotIn("truncated", window)

    def test_request_pipeline_trims(self):
        """The trim must be wired into the per-request pipeline."""
        src = _load_source()
        self.assertIn("history_to_send = _trim_history_tool_results(history_to_send)", src)


class TestClassifyLlm500(unittest.TestCase):
    """A 500 must be classified before choosing a recovery (issue #63).

    Reshaping the request (tools-as-text) is only correct for a template
    fault.  Applying it to a server fault silently downgrades the session
    and hides the real cause, so the classifier must never mistake one for
    the other.
    """

    def test_template_markers_classify_as_template(self):
        """Known llama-server template rejections classify as template."""
        for body in (
            "Unable to generate parser for this template.",
            "Error: Jinja Exception: Unexpected message role.",
            "No user query found in messages.",
            "Jinja template error while rendering.",
        ):
            with self.subTest(body=body):
                self.assertEqual(_classify_llm_500(body), _FAULT_TEMPLATE)

    def test_oom_markers_classify_as_server(self):
        """Resource/hardware failures classify as server faults."""
        for body in (
            "CUDA error: out of memory",
            "failed to allocate buffer for kv cache",
            "ggml_vulkan: ErrorOutOfDeviceMemory",
            "GGML_ASSERT: cudaMalloc failed",
        ):
            with self.subTest(body=body):
                self.assertEqual(_classify_llm_500(body), _FAULT_SERVER)

    def test_empty_body_classifies_as_server(self):
        """An empty body is ambiguous -> safe default is server (no reshape)."""
        for body in ("", None, "   ", "{}"):
            with self.subTest(body=body):
                self.assertEqual(_classify_llm_500(body), _FAULT_SERVER)

    def test_unrecognised_body_classifies_as_server(self):
        """An unknown error must not trigger the template downgrade."""
        self.assertEqual(
            _classify_llm_500("Internal server error occurred."), _FAULT_SERVER
        )

    def test_server_markers_win_over_generic_template_marker(self):
        """An OOM mentioning 'template' is still a server fault.

        This is the exact ambiguity that caused the original bug: the
        generic marker 'template' appears in unrelated server messages, so
        the specific server markers must be checked first.
        """
        body = "Failed to load template: CUDA error: out of memory"
        self.assertEqual(_classify_llm_500(body), _FAULT_SERVER)

    def test_classification_is_case_insensitive(self):
        """Marker matching must not depend on casing."""
        self.assertEqual(
            _classify_llm_500("UNABLE TO GENERATE PARSER FOR THIS TEMPLATE"),
            _FAULT_TEMPLATE,
        )
        self.assertEqual(
            _classify_llm_500("OUT OF MEMORY"), _FAULT_SERVER
        )

    def test_malformed_tool_call_classifies_as_toolcall(self):
        """A model-generated bad tool call is its own fault class.

        The request was valid; the model's own output was not parseable
        JSON.  Reshaping the request cannot help, so it must not be treated
        as a template fault (which would downgrade the session) nor as a
        server fault (which would hide the real cause).
        """
        for body in (
            "Failed to parse tool call arguments as JSON: invalid string: "
            "missing closing quote",
            "failed to parse tool call",
            "Error: tool call arguments as json",
        ):
            with self.subTest(body=body):
                self.assertEqual(_classify_llm_500(body), _FAULT_TOOLCALL)

    def test_toolcall_marker_wins_over_generic_template_marker(self):
        """The real llama-server message also contains 'template'.

        llama-server wraps the parse failure in a message that mentions the
        chat template, so the generic 'template' marker would otherwise
        misclassify it as a template fault and trigger the tools-as-text
        downgrade.
        """
        body = (
            "Failed to parse tool call arguments as JSON for this template: "
            "invalid string: missing closing quote"
        )
        self.assertEqual(_classify_llm_500(body), _FAULT_TOOLCALL)

    def test_server_marker_wins_over_toolcall_marker(self):
        """An OOM that mentions a tool call is still a server fault."""
        body = "Failed to parse tool call: CUDA error: out of memory"
        self.assertEqual(_classify_llm_500(body), _FAULT_SERVER)


class TestToolcallFaultMessage(unittest.TestCase):
    """The malformed-tool-call error must name the cause and the fix."""

    def test_message_names_the_cause(self):
        msg = _toolcall_fault_message(
            "Failed to parse tool call arguments as JSON: invalid string: "
            "missing closing quote"
        )
        self.assertIn("not valid JSON", msg)
        self.assertIn("model generation failure", msg)

    def test_message_includes_server_body(self):
        msg = _toolcall_fault_message("some server detail")
        self.assertIn("some server detail", msg)

    def test_message_survives_empty_body(self):
        msg = _toolcall_fault_message("")
        self.assertIn("not valid JSON", msg)

    def test_message_suggests_a_remedy(self):
        msg = _toolcall_fault_message("x")
        self.assertIn("Reasoning Effort", msg)


class TestToolcallRetryWiring(unittest.TestCase):
    """The tool-call fault must retry once with a nudge, then surface."""

    def test_retry_guard_exists(self):
        src = _load_source()
        self.assertIn("_toolcall_nudged = False", src)

    def test_retry_branch_is_guarded(self):
        src = _load_source()
        self.assertIn(
            "_fault == _FAULT_TOOLCALL and not _toolcall_nudged", src
        )

    def test_nudge_instructs_well_formed_json(self):
        src = _load_source()
        self.assertIn("well-formed JSON arguments", src)

    def test_final_failure_surfaces_toolcall_message(self):
        src = _load_source()
        self.assertIn("_toolcall_fault_message(_500_body)", src)

    def test_log_crosscheck_exempts_specific_faults(self):
        """A specific match must not be overridden by a stale log tail."""
        src = _load_source()
        self.assertIn(
            "_fault not in (_FAULT_TEMPLATE, _FAULT_TOOLCALL)", src
        )


class TestClassifierMarkerSync(unittest.TestCase):
    """Guard against the mirrored marker tuples drifting from the source.

    The classifier tests above use lists mirrored in this module so that a
    marker addition cannot silently turn a test green.  This test closes the
    other half: it parses the real constants out of ``agent_controller.py``
    and fails if the mirror no longer matches.
    """

    @staticmethod
    def _source_tuple(name: str) -> tuple:
        """Extract a top-level tuple-of-strings constant from the source."""
        tree = ast.parse(_load_source())
        for node in tree.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                target = node.targets[0]
                if isinstance(target, ast.Name) and target.id == name:
                    return tuple(ast.literal_eval(node.value))
        raise AssertionError("{:s} not found in agent_controller.py".format(name))

    def test_server_markers_match_source(self):
        self.assertEqual(self._source_tuple("_SERVER_FAULT_MARKERS"), _SERVER_FAULT_MARKERS)

    def test_template_markers_match_source(self):
        self.assertEqual(self._source_tuple("_TEMPLATE_FAULT_MARKERS"), _TEMPLATE_FAULT_MARKERS)

    def test_toolcall_markers_match_source(self):
        self.assertEqual(self._source_tuple("_TOOLCALL_FAULT_MARKERS"), _TOOLCALL_FAULT_MARKERS)

    def test_fault_constants_match_source(self):
        """FAULT_TEMPLATE / FAULT_SERVER / FAULT_TOOLCALL must match source."""
        tree = ast.parse(_load_source())
        found = {}
        for node in tree.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                target = node.targets[0]
                if isinstance(target, ast.Name) and target.id in (
                    "_FAULT_TEMPLATE", "_FAULT_SERVER", "_FAULT_TOOLCALL"
                ):
                    found[target.id] = ast.literal_eval(node.value)
        self.assertEqual(
            found,
            {
                "_FAULT_TEMPLATE": _FAULT_TEMPLATE,
                "_FAULT_SERVER": _FAULT_SERVER,
                "_FAULT_TOOLCALL": _FAULT_TOOLCALL,
            },
        )


class TestBenchmarkStepOutcome(unittest.TestCase):
    """Benchmark steps must record failures, not just timings.

    ``run_conversation_turn`` does NOT raise on an LLM failure -- it returns
    normally and records the reason in ``_agent_state.error``.  The step
    runner only caught exceptions, so every failed step was recorded as a
    success and the Diagnostics panel showed nothing but a fast timing.
    """

    @staticmethod
    def _load_operators_module():
        """Load the benchmark outcome helpers from operators_agent.py.

        The module imports bpy, so only the pure helpers are extracted.
        """
        path = os.path.join(_REPO, "addon", "bfa_coworker", "operators_agent.py")
        with open(path, "r", encoding="utf-8") as fh:
            source = fh.read()

        mod = types.ModuleType("_oa_extract")
        mod.__dict__["Any"] = object
        # The stores the helpers close over.
        mod.__dict__["_test_suite_timings"] = {}
        mod.__dict__["_test_suite_status"] = {}
        mod.__dict__["_test_suite_errors"] = {}
        for name in ("_record_step_outcome", "_suite_failure_count"):
            marker = "\ndef {:s}(".format(name)
            start = source.find(marker)
            if start < 0:
                raise ImportError("{:s} not found in operators_agent.py".format(name))
            start += 1
            end = len(source)
            for m in ["\ndef _", "\ndef ", "\nclass ", "\n# ---"]:
                idx = source.find(m, start + 100)
                if 0 <= idx < end:
                    end = idx
            exec(compile(source[start:end], path, "exec"), mod.__dict__)
        return mod

    def setUp(self):
        self.mod = self._load_operators_module()

    def test_success_records_ok_status(self):
        self.mod._record_step_outcome("suite", 1, 1.5)
        self.assertEqual(self.mod._test_suite_status[("suite", 1)], "ok")
        self.assertEqual(self.mod._test_suite_timings[("suite", 1)], 1.5)
        self.assertNotIn(("suite", 1), self.mod._test_suite_errors)

    def test_error_records_failed_status_and_message(self):
        self.mod._record_step_outcome("suite", 2, 0.1, "No response from LLM")
        self.assertEqual(self.mod._test_suite_status[("suite", 2)], "failed")
        self.assertEqual(
            self.mod._test_suite_errors[("suite", 2)], "No response from LLM"
        )

    def test_rerun_clears_previous_error(self):
        """A step that succeeds on re-run must not keep its old error."""
        self.mod._record_step_outcome("suite", 3, 0.1, "boom")
        self.mod._record_step_outcome("suite", 3, 2.0)
        self.assertEqual(self.mod._test_suite_status[("suite", 3)], "ok")
        self.assertNotIn(("suite", 3), self.mod._test_suite_errors)

    def test_failure_count_is_per_suite(self):
        self.mod._record_step_outcome("a", 1, 0.1, "x")
        self.mod._record_step_outcome("a", 2, 0.1, "y")
        self.mod._record_step_outcome("a", 3, 0.1)
        self.mod._record_step_outcome("b", 1, 0.1, "z")
        self.assertEqual(self.mod._suite_failure_count("a"), 2)
        self.assertEqual(self.mod._suite_failure_count("b"), 1)
        self.assertEqual(self.mod._suite_failure_count("c"), 0)

    def test_failure_count_zero_when_all_pass(self):
        self.mod._record_step_outcome("s", 1, 0.1)
        self.mod._record_step_outcome("s", 2, 0.1)
        self.assertEqual(self.mod._suite_failure_count("s"), 0)

    def test_step_runner_checks_agent_state_error(self):
        """The step runner must inspect _agent_state.error, not just exceptions.

        Guards the actual regression: ``run_conversation_turn`` returns
        normally on failure, so an exception-only check reports success.
        """
        path = os.path.join(_REPO, "addon", "bfa_coworker", "operators_agent.py")
        with open(path, "r", encoding="utf-8") as fh:
            source = fh.read()
        start = source.find("def _run_test_step(")
        self.assertGreater(start, 0, "_run_test_step not found")
        end = source.find("\ndef ", start + 100)
        body = source[start:end if end > 0 else len(source)]
        self.assertIn("_agent_state.error", body)
        self.assertIn("_record_step_outcome", body)

    def test_step_runner_does_not_clear_error_up_front(self):
        """The runner must not wipe _agent_state.error before the call.

        Clearing it destroyed the evidence the session log exports, which is
        why a failed run showed an empty ``--- Error Info ---`` section.
        """
        path = os.path.join(_REPO, "addon", "bfa_coworker", "operators_agent.py")
        with open(path, "r", encoding="utf-8") as fh:
            source = fh.read()
        start = source.find("def _run_test_step(")
        end = source.find("\ndef ", start + 100)
        body = source[start:end if end > 0 else len(source)]
        self.assertNotIn('_agent_state.error = ""', body)
        # It must snapshot instead, so a stale error is not misattributed.
        self.assertIn("_prev_error", body)


class TestInternalCodeMainThreadMarker(unittest.TestCase):
    """Smart-undo/cleanup payloads must run on the main thread."""
    def test_undo_code_has_toolcode_marker(self):
        code = _undo_code("undo")
        self.assertIn("# blmcp-toolcode-skip-preflight", code)

    def test_undo_push_code_has_toolcode_marker(self):
        code = _undo_code("push", "bfa_coworker_pre_script")
        self.assertIn("# blmcp-toolcode-skip-preflight", code)

    def test_cleanup_code_has_toolcode_marker(self):
        code = _build_cleanup_code(
            types.SimpleNamespace(
                object_names={"Torus"}, mesh_names=set(),
                material_names=set(), light_names=set(),
                camera_names=set(), collection_names=set(),
                curve_names=set(), grease_pencil_names=set(),
                armature_names=set(), node_group_names=set(),
                image_names=set(), text_names=set(),
            )
        )
        self.assertIn("# blmcp-toolcode-skip-preflight", code)
        self.assertIn("bpy.data.objects.remove", code)

    def test_cleanup_code_empty_diff(self):
        """Empty diff produces no-op code but still carries the marker."""
        empty = types.SimpleNamespace(**{
            f: set() for f in (
                "object_names mesh_names material_names light_names "
                "camera_names collection_names curve_names "
                "grease_pencil_names armature_names node_group_names "
                "image_names text_names".split()
            )
        })
        code = _build_cleanup_code(empty)
        self.assertIn("# blmcp-toolcode-skip-preflight", code)


if __name__ == "__main__":
    unittest.main()