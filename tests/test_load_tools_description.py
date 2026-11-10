# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
``load_tools``' description names every tool each domain adds (#422).

With tools on demand, an External Agent only sees the surface tools.  Its own
tool search looks at names and descriptions, so "bake" or "render" has to land
on ``load_tools``: the description lists each domain's tools and plain words.

    python -m unittest tests.test_load_tools_description -v
"""

import ast
import importlib.util
import re
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_LOAD_TOOLS = _REPO / "mcp" / "blmcp" / "tools" / "load_tools.py"
_DOMAINS = _REPO / "mcp" / "blmcp" / "tool_domains.py"


def _domains() -> dict:
    spec = importlib.util.spec_from_file_location("tool_domains_desc_test", _DOMAINS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.DOMAINS


def _description() -> str:
    tree = ast.parse(_LOAD_TOOLS.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "load_tools":
            return ast.get_docstring(node) or ""
    raise AssertionError("load_tools not found")


def _sections(text: str) -> dict[str, str]:
    """Each ``- domain: ...`` entry, up to the next one or a blank line."""
    found = {}
    for match in re.finditer(r"^- (\w+):(.*?)(?=^- \w+:|^\s*$)", text, re.M | re.S):
        found[match.group(1)] = match.group(2)
    return found


class TestLoadToolsDescription(unittest.TestCase):

    def test_every_domain_tool_is_named_under_its_domain(self):
        sections = _sections(_description())
        for domain, tools in _domains().items():
            self.assertIn(domain, sections, "load_tools doesn't describe domain {!r}".format(domain))
            for tool in tools:
                self.assertRegex(sections[domain], r"\b{:s}\b".format(re.escape(tool)),
                                 "{!r} isn't named under {!r} in load_tools".format(tool, domain))

    def test_no_unknown_domain_or_tool(self):
        domains = _domains()
        for domain, text in _sections(_description()).items():
            self.assertIn(domain, domains, "load_tools describes an unknown domain {!r}".format(domain))
            for name in re.findall(r"\b[a-z]+(?:_[a-z0-9]+)+\b", text):
                self.assertIn(name, domains[domain], "{!r} named under {!r} isn't in it".format(name, domain))

    def test_plain_words_a_tool_search_matches(self):
        text = _description().lower()
        for word in ("bake", "render", "material", "keyframe", "asset", "light", "node"):
            self.assertIn(word, text)


if __name__ == "__main__":
    unittest.main()
