# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for the operating-mode update callback (#489).

Setting ``operating_mode`` while the agent was running wrote the previous mode
back with ``self["operating_mode"] = ...``; on Blender 5.x the add-on
preferences hold no ID properties, so that raised "id properties not supported
for this type", the refusal never happened and the mode changed anyway.  The
callback is run here against a stand-in for the preferences that behaves like
Blender 5.x: a write runs the property's update at once, and ``self[key] = ...``
raises the same TypeError.

Run with::

    python -m unittest tests.test_operating_mode_update -v
"""

__all__ = ()

import contextlib
import io
import sys
import textwrap
import types
import unittest
from pathlib import Path

_ADDON = Path(__file__).resolve().parents[1] / "addon" / "bfa_coworker"
_PKG = "_bfacw_prefs_489"

# The add-on's operating modes (shared.OPERATING_MODE_ITEMS): id, label, description, icon, number.
_ITEMS = [("LOCAL_LLM", "", "", "", 0), ("REMOTE_API", "", "", "", 1),
          ("EXTERNAL_HARNESS", "", "", "", 2), ("EXTERNAL_AGENT", "", "", "", 3)]


def _load():
    """The module-level mode state and ``_update_operating_mode`` from preferences.py."""
    src = (_ADDON / "preferences.py").read_text(encoding="utf-8")
    start = src.index("_PREVIOUS_MODE: dict")
    state = src[start:src.index("\n\n", start)]
    start = src.index("    def _update_operating_mode(")
    method = textwrap.dedent(src[start:src.index("\n\n    operating_mode:", start)])
    agent_state = types.SimpleNamespace(mcp_server_running=False)
    sys.modules[_PKG] = types.ModuleType(_PKG)
    sys.modules[_PKG].__path__ = []
    sys.modules[_PKG + ".agent_controller"] = types.SimpleNamespace(_agent_state=agent_state)
    stub_bpy = types.SimpleNamespace(types=types.SimpleNamespace(Context=object))
    ns = {"__name__": _PKG + ".preferences", "__package__": _PKG, "bpy": stub_bpy,
          "OPERATING_MODE_ITEMS": _ITEMS}
    exec(compile(state + "\n" + method, "preferences.py", "exec"), ns)      # noqa: S102
    return ns, agent_state


class _Prefs:
    """Blender 5.x-like add-on preferences: writes run the update, no ID properties."""

    def __init__(self, update):
        object.__setattr__(self, "_update", update)
        object.__setattr__(self, "calls", 0)
        for name, value in (("operating_mode", "LOCAL_LLM"), ("agent_mode", "SELF_CONTAINED"),
                            ("llm_mode", "local"), ("pref_tab", "LOCAL_LLM")):
            object.__setattr__(self, name, value)

    def __setattr__(self, name, value):
        object.__setattr__(self, name, value)
        if name == "operating_mode":
            object.__setattr__(self, "calls", self.calls + 1)
            if self.calls > 10:
                raise RecursionError("the update keeps re-running itself")
            self._update(self, None)

    def __setitem__(self, key, value):
        raise TypeError("bpy_struct[key] = val: id properties not supported for this type")

    def state(self):
        return (self.operating_mode, self.agent_mode, self.llm_mode, self.pref_tab)


class TestOperatingModeUpdate(unittest.TestCase):

    def setUp(self):
        self.ns, self.agent = _load()
        self.prefs = _Prefs(self.ns["_update_operating_mode"])
        quiet = contextlib.redirect_stdout(io.StringIO())     # the "change rejected" line
        quiet.__enter__()
        self.addCleanup(quiet.__exit__, None, None, None)

    def tearDown(self):
        sys.modules.pop(_PKG, None)
        sys.modules.pop(_PKG + ".agent_controller", None)

    def test_each_mode_syncs_the_rest_when_idle(self):
        expect = {"REMOTE_API": ("REMOTE_API", "SELF_CONTAINED", "remote", "REMOTE_API"),
                  "EXTERNAL_HARNESS": ("EXTERNAL_HARNESS", "EXTERNAL_HARNESS", "remote", "ADVANCED"),
                  "EXTERNAL_AGENT": ("EXTERNAL_AGENT", "SELF_CONTAINED", "remote", "ADVANCED"),
                  "LOCAL_LLM": ("LOCAL_LLM", "SELF_CONTAINED", "local", "LOCAL_LLM")}
        for mode, state in expect.items():
            self.prefs.operating_mode = mode
            self.assertEqual(self.prefs.state(), state, mode)
            self.assertEqual(self.ns["_PREVIOUS_MODE"]["mode"], mode)

    def test_a_switch_while_running_is_refused_without_an_error(self):
        self.prefs.operating_mode = "REMOTE_API"
        before = self.prefs.state()
        self.agent.mcp_server_running = True
        for mode in ("LOCAL_LLM", "EXTERNAL_HARNESS", "EXTERNAL_AGENT"):
            self.prefs.operating_mode = mode            # raised a TypeError before #489
            self.assertEqual(self.prefs.state(), before, mode)
        self.assertFalse(self.ns["_MODE_REVERTING"]["on"])
        self.agent.mcp_server_running = False
        self.prefs.operating_mode = "EXTERNAL_HARNESS"  # once stopped, switching works again
        self.assertEqual(self.prefs.state()[:2], ("EXTERNAL_HARNESS", "EXTERNAL_HARNESS"))

    def test_refused_with_no_switch_yet_falls_back_to_the_synced_modes(self):
        self.prefs.agent_mode = "EXTERNAL_HARNESS"      # a session started in harness mode
        self.agent.mcp_server_running = True
        self.prefs.operating_mode = "REMOTE_API"
        self.assertEqual(self.prefs.operating_mode, "EXTERNAL_HARNESS")
        self.assertEqual(self.prefs.agent_mode, "EXTERNAL_HARNESS")

    def test_the_callback_never_writes_an_id_property(self):
        src = (_ADDON / "preferences.py").read_text(encoding="utf-8")
        start = src.index("    def _update_operating_mode(")
        body = src[start:src.index("\n\n    operating_mode:", start)]
        code = "\n".join(line.split("#", 1)[0] for line in body.splitlines())
        self.assertFalse('self["' in code, "the update writes self[...] (an ID property)")


if __name__ == "__main__":
    unittest.main()
