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

import json
import os
import types
import unittest

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
    if extra:
        mod.__dict__.update(extra)
    exec(compile(func_source, _AC_PATH, "exec"), mod.__dict__)
    return mod.__dict__[name]


_trim_tool_result = _extract_func(_load_source(), "_trim_tool_result")
_undo_code = _extract_func(_load_source(), "_undo_code")
_build_cleanup_code = _extract_func(_load_source(), "_build_cleanup_code")


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