"""#643: no message names a "Strip > Coworker" menu; the Strip menu has no such submenu.

The real path is built from the ``bl_label`` of the layouts menu (read with ``ast``, no bpy).
"""
import ast
import pathlib
import re
import sys
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
ADDON = REPO / "addon" / "bfa_coworker"
SCAN_DIRS = [ADDON, REPO / "mcp"]
SEPARATORS = ">" + chr(0x2192) + chr(0x203A) + chr(0xBB)
BAD = re.compile(r"Strip\s*(?:[" + re.escape(SEPARATORS) + r"]|-+>)\s*Coworker")


def _label(path, class_name):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for stmt in node.body:
                if (isinstance(stmt, ast.Assign) and stmt.targets[0].id == "bl_label"
                        and isinstance(stmt.value, ast.Constant)):
                    return stmt.value.value
    raise AssertionError("no bl_label for " + class_name)


def _constants(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def wrong_paths(dirs):
    """Hits of the wrong path, in string constants (joined literals included) and in raw text."""
    hits = []
    for folder in dirs:
        for path in sorted(pathlib.Path(folder).rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            found = BAD.findall(text)
            try:
                for const in _constants(path):
                    found += BAD.findall(const)
            except SyntaxError:
                pass
            if found:
                hits.append((path.name, found[0]))
    return hits


class MenuPathTest(unittest.TestCase):
    def test_no_strip_coworker_path(self):
        self.assertEqual(wrong_paths(SCAN_DIRS), [])

    def test_real_path_is_named(self):
        label = _label(ADDON / "reframe_layouts.py", "BFACW_MT_reframe_layout")
        want = "Strip > " + label
        hits = [c for c in _constants(ADDON / "reframe.py") if "part of a split" in c or "(Strip >" in c]
        self.assertGreaterEqual(len(hits), 3)
        self.assertTrue(any(want in c for c in hits))
        text = "\n".join(_constants(ADDON / "reframe.py"))
        self.assertGreaterEqual(text.count(want), 3)

    def test_scan_catches_splits_and_variants(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            f = pathlib.Path(tmp) / "bad.py"
            f.write_text('x = ("(Strip > "\n     "Coworker > Reframe Layout)")\n'
                         '# Strip' + chr(0x2192) + 'Coworker\ny = "Strip >Coworker"\n', encoding="utf-8")
            self.assertEqual(wrong_paths([tmp])[0][0], "bad.py")


if __name__ == "__main__":
    unittest.main()
