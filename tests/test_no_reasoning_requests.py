# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
#460: no prompt Coworker sends asks the model to write out its reasoning.

Claude Code (Opus 5.5) blocked normal requests with ``[reasoning_extraction]``
("... reverse engineering or duplicating model outputs").  The MCP server's
instructions said "Think aloud in full paragraphs. Explain your reasoning step by
step ... follow your thought process", and Claude Code adds them to every request.
Progress updates ("say what you're about to do, then what changed") keep the user
posted without asking for the model's reasoning.

Run with::

    python -m unittest tests.test_no_reasoning_requests -v
"""

__all__ = ()

import ast
import re
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]

# Asking for the model's reasoning, in the words prompts use.
_ASKS = re.compile(
    r"think(ing)? (aloud|out loud)"
    r"|(explain|show|share|describe|write out|walk through|narrate) (your|the|its) (reasoning|thinking|thoughts?)"
    r"|(your|the model's) (thought process|chain[- ]of[- ]thought|inner monologue)"
    r"|reasoning step by step",
    re.IGNORECASE,
)


def _prompt_files() -> list[Path]:
    """The instruction files the MCP server and the add-on load."""
    return sorted((_ROOT / "mcp" / "blmcp" / "data").glob("prompts*.yml"))


def _string_constants(path: Path) -> list[tuple[int, str]]:
    """Every string literal in *path* (adjacent literals joined, as Python does)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [(node.lineno, node.value) for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)]


class TestNoReasoningRequests(unittest.TestCase):

    def test_the_mcp_server_instructions(self):
        files = _prompt_files()
        self.assertTrue(files, "the prompts moved: point this test at them")
        for path in files:
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                self.assertIsNone(_ASKS.search(line), "{:s}:{:d}: {:s}".format(path.name, number, line.strip()))

    def test_the_text_across_line_breaks(self):
        # "Explain your reasoning" can be split over two lines of the YAML.
        for path in _prompt_files():
            text = " ".join(path.read_text(encoding="utf-8").split())
            match = _ASKS.search(text)
            self.assertIsNone(match, "{:s}: {!r}".format(path.name, match and match.group(0)))

    def test_the_add_ons_prompts(self):
        # The STYLE line and the built-in fallback prompt (any provider: OpenRouter can be Claude).
        for name in ("agent_controller.py", "engine.py", "engine_acp.py", "auto_mode.py"):
            path = _ROOT / "addon" / "bfa_coworker" / name
            for lineno, value in _string_constants(path):
                match = _ASKS.search(" ".join(value.split()))
                self.assertIsNone(match, "{:s}:{:d}: {!r}".format(name, lineno, match and match.group(0)))

    def test_the_check_catches_the_old_wording(self):
        old = ("Think aloud in full paragraphs. Explain your reasoning step by step -- what "
               "you observe, what you plan to do, and why. The user should be able to follow "
               "your thought process.")
        self.assertIsNotNone(_ASKS.search(old))
        self.assertIsNone(_ASKS.search("Before a step, say in a sentence what you are about to do; "
                                       "after it, say what changed."))


if __name__ == "__main__":
    unittest.main()
