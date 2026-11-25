"""#643: no message names a "Strip > Coworker" menu; the Strip menu has no such submenu."""
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent / "addon" / "bfa_coworker"
ARROWS = (">", chr(0x2192), "->")


class MenuPathTest(unittest.TestCase):
    def test_no_strip_coworker_path(self):
        bad = []
        for path in ROOT.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for arrow in ARROWS:
                for gap in (" ", ""):
                    needle = "Strip" + gap + arrow + gap + "Coworker"
                    if needle in text:
                        bad.append((path.name, needle))
        self.assertEqual(bad, [])

    def test_real_path_is_named(self):
        text = (ROOT / "reframe.py").read_text(encoding="utf-8")
        self.assertIn("Strip > Reframe Layout", text)


if __name__ == "__main__":
    unittest.main()
