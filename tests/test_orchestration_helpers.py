"""
Tests for run-loop orchestration helpers in agent_controller.py.

Covers two behaviors that keep the agent from spiraling on repeated
errors:

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

Loaded from source (the module imports bpy, which is not available in
the unit-test environment).

Run with::

    python -m unittest tests.test_orchestration_helpers -v
"""

__all__ = ()

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


_trim_tool_result = _extract_func(_load_source(), "_trim_tool_result")
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
        """The system prompt survives even an impossibly small budget."""
        messages = [{"role": "system", "content": "sys"}]
        for i in range(20):
            messages.append({"role": "user", "content": "u{:d} ".format(i) * 200})
        trimmed = _fit_history_to_budget(messages, 1)
        self.assertEqual(len(trimmed), 1)
        self.assertEqual(trimmed[0]["role"], "system")

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