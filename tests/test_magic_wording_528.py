# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for #528.

A. Redaction: the helper tab's first message (the ``delegate_task`` task) and the parent's
   "[Helpers]" message are written by the agent, not typed, so a key in them is ``***`` on
   screen, in a copy, in a selection and in a title; what you typed still shows as typed
   (#418); the history keeps the real text (the model gets it).
B. Magic wording: helpers are mages / wizards / magicians (a name chosen once and stored),
   the thinking status is a word about magic picked once per stretch, one preference turns
   it off, and nothing magic reaches the model.

Run with::

    python -m unittest tests.test_magic_wording_528 -v
"""

__all__ = ()

import importlib
import re
import sys
import types
import unittest
from pathlib import Path

_ADDON = Path(__file__).resolve().parents[1] / "addon" / "bfa_coworker"
_PKG = "bfacw_magic528_pkg"


def _load(module):
    if _PKG not in sys.modules:
        pkg = types.ModuleType(_PKG)
        pkg.__path__ = [str(_ADDON)]
        sys.modules[_PKG] = pkg
    return importlib.import_module(_PKG + "." + module)


log = _load("log")
mw = _load("magic_wording")
hp = _load("helpers")
cs = _load("chat_status")
cv = _load("ui_gpu.chat_view")
lay = _load("ui_gpu.layout")
st = _load("ui_gpu.style")

FAKE_KEY = "sk-or-v1-" + "FAKEdummy0" * 4


class _Case(unittest.TestCase):

    def setUp(self):
        mw.set_enabled_for_tests(True)
        self.addCleanup(mw.set_enabled_for_tests, None)
        hp.HELPERS.clear()
        self.addCleanup(hp.HELPERS.clear)

    def draw(self, hist, live=None, compact=False):
        canvas = lay.Canvas(st.Style(), lay.fixed_measure(0.55), lay.InteractionState())
        view = cv.ChatView()
        view.draw(canvas, lay.Rect(0, 0, 900, 5000), hist, live=live, compact=compact)
        return "\n".join(cmd.text for cmd in canvas.commands if cmd.kind == "text")


class TestRedactedOnScreen(_Case):

    def _helper_history(self):
        message = hp.framing("Build the scene", "Use the key " + FAKE_KEY + " for the lookup.", "")
        return [{"role": "user", "content": message, "turn_start": True},
                {"role": "assistant", "content": "Done."}], message

    def test_the_helpers_first_message_hides_a_key(self):
        for on in (True, False):
            mw.set_enabled_for_tests(on)
            hist, message = self._helper_history()
            texts = self.draw(hist)
            self.assertNotIn(FAKE_KEY[:12], texts, "wording on=%s" % on)
            self.assertIn("Use the key ***", texts)
            self.assertIn(FAKE_KEY, hist[0]["content"], "the history keeps it: the model gets the task")
            self.assertEqual(hist[0]["content"], message)

    def test_the_parent_s_helpers_message_hides_a_key(self):
        a = hp.Helper(id="h1", parent="p", title="Lookup", status=hp.DONE, answer="The key was " + FAKE_KEY)
        text = hp.results_text([a])
        hist = [{"role": "user", "content": text, "turn_start": True}, {"role": "assistant", "content": "ok"}]
        texts = self.draw(hist)
        self.assertNotIn(FAKE_KEY[:12], texts)
        self.assertIn("The key was ***", texts)
        self.assertIn(FAKE_KEY, hist[0]["content"])

    def test_copy_and_selection_texts_hide_it_too(self):
        hist, _message = self._helper_history()
        user_text, _reply = cv._copy_texts(hist)
        self.assertNotIn(FAKE_KEY[:12], user_text)
        self.assertNotIn(FAKE_KEY[:12], cv.transcript(hist))
        units = cv.unit_texts(hist)
        self.assertNotIn(FAKE_KEY[:12], "\n".join(text for _u, text, _k in units))

    def test_what_you_typed_still_shows_as_typed(self):
        hist = [{"role": "user", "content": "my key is " + FAKE_KEY, "turn_start": True},
                {"role": "assistant", "content": "ok"}]
        texts = self.draw(hist)
        self.assertIn(FAKE_KEY, texts, "the #418 rule: your own message is kept as you typed it")

    def test_a_key_set_later_is_hidden_from_the_next_frame(self):
        late = "late-fake-key-0000-xyz"
        message = hp.framing("Build", "Call with " + late, "")
        hist = [{"role": "user", "content": message, "turn_start": True}, {"role": "assistant", "content": "ok"}]
        view = cv.ChatView()
        def frame():
            canvas = lay.Canvas(st.Style(), lay.fixed_measure(0.55), lay.InteractionState())
            view.draw(canvas, lay.Rect(0, 0, 900, 5000), hist)
            return "\n".join(c.text for c in canvas.commands if c.kind == "text")
        self.assertIn(late, frame())
        log.set_secret("late", late)
        self.addCleanup(log.set_secret, "late", "")
        self.assertNotIn(late, frame())

    def test_a_title_from_an_agent_message_hides_a_key(self):
        ch = _load("chat_history")
        message = hp.framing("Build", "Use " + FAKE_KEY, "")
        title = ch.title_of([{"role": "user", "content": message}])
        self.assertNotIn(FAKE_KEY[:12], title)
        typed = ch.title_of([{"role": "user", "content": "use " + FAKE_KEY}])
        self.assertIn(FAKE_KEY[:12], typed, "typed text is not touched")
        self.assertNotIn(FAKE_KEY[:12], mw.shown_agent_text(message))


class TestHelperNames(_Case):

    def _three(self):
        return [hp.HELPERS.add("p1", "h%d" % n, title="Part %d" % n, task="do part %d" % n,
                               message=hp.framing("Build", "do part %d" % n, "")) for n in (1, 2, 3)]

    def test_each_helper_gets_a_name_once_and_keeps_it(self):
        three = self._three()
        names = [h.name for h in three]
        self.assertTrue(all(re.fullmatch(r"(Mage|Wizard|Magician) %d" % n, name)
                            for n, name in enumerate(names, 1)), names)
        for _ in range(5):                                      # redraws, lookups: the same
            self.assertEqual([hp.HELPERS.get("h%d" % n).name for n in (1, 2, 3)], names)
        self.assertEqual(hp.HELPERS.get("h2").name, three[1].name, "stored on the helper")
        again = hp.HELPERS.add("p1", "h9", title="x", task="x", message=three[0].message)
        self.assertEqual(again.name.split()[0], three[0].name.split()[0], "the word follows the message")
        self.assertEqual(again.name.split()[1], "4", "numbers count up per asking chat")

    def test_the_three_words_all_occur(self):
        words = {mw.helper_name("task %d" % n, 1).split()[0] for n in range(60)}
        self.assertEqual(words, set(mw.HELPER_WORDS))

    def test_the_first_message_heading_uses_the_stored_name(self):
        helper = hp.HELPERS.add("p1", "h1", title="T", task="Tidy", message=hp.framing("Build", "Tidy", ""))
        hist = [{"role": "user", "content": helper.message, "turn_start": True},
                {"role": "assistant", "content": "ok"}]
        texts = self.draw(hist)
        self.assertIn('[{:s} for "Build"]'.format(helper.name), texts)
        self.assertEqual(hist[0]["content"], helper.message, "the history is as it was sent")
        self.assertTrue(helper.message.startswith('[Helper for "Build"]'))

    def test_a_reopened_helper_chat_keeps_its_word(self):
        helper = hp.HELPERS.add("p1", "h1", title="T", task="Tidy", message=hp.framing("Build", "Tidy", ""))
        word = helper.name.split()[0]
        hp.HELPERS.clear()                  # a new session: no registry
        shown = mw.rewrite_agent_text(helper.message)
        self.assertTrue(shown.startswith('[{:s} for "Build"]'.format(word)), shown)
        cut = " ".join(helper.message.split())[:57]        # a title cut from it (History, the tab)
        self.assertTrue(mw.rewrite_agent_text(cut).startswith("[" + word + " for"))

    def test_the_results_heading_says_mages(self):
        a = hp.Helper(id="a", parent="p", title="A", status=hp.DONE, answer="ok")
        b = hp.Helper(id="b", parent="p", title="B", status=hp.DONE, answer="ok")
        self.assertTrue(mw.rewrite_agent_text(hp.results_text([a, b])).startswith("[Mages] Your 2 mages finished (2 done)"))
        self.assertTrue(mw.rewrite_agent_text(hp.results_text([a])).startswith("[Mages] Your 1 mage finished"))
        self.assertEqual(hp.waiting_text(2), "Waiting for 2 mages")
        self.assertEqual(hp.waiting_text(1), "Waiting for 1 mage")


class TestHelperChatsGlue(_Case):

    def _hc(self):
        ac = types.ModuleType(_PKG + ".agent_controller")
        ui = types.ModuleType(_PKG + ".ui_chat")
        ui._thread_status = lambda t: ("Thinking", "SORTTIME")
        stand_ins = {_PKG + ".agent_controller": ac, _PKG + ".ui_chat": ui, "bpy": types.ModuleType("bpy")}
        saved = {name: sys.modules.get(name) for name in list(stand_ins) + [_PKG + ".helper_chats"]}

        def restore():
            for name, module in saved.items():
                if module is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = module
        self.addCleanup(restore)
        sys.modules.update(stand_ins)
        sys.modules.pop(_PKG + ".helper_chats", None)
        ac.THREADS = types.SimpleNamespace(get=lambda _id: None)
        return importlib.import_module(_PKG + ".helper_chats")

    def test_card_tab_and_tooltip(self):
        hc = self._hc()
        h1 = hp.HELPERS.add("p1", "h1", title="Node tidy", task="t", message="[Helper for \"B\"] t", parent_title="Build")
        hp.HELPERS.add("p1", "h2", title="Strips", task="s", message="[Helper for \"B\"] s", parent_title="Build")
        card = hc.card(types.SimpleNamespace(id="p1"))
        self.assertEqual(card.title, "2 mages — 2 working")
        self.assertEqual(card.rows[0][1], h1.name + ": Node tidy")
        self.assertEqual(hc.tab_label(types.SimpleNamespace(id="h1")), h1.name + ": Node tidy")
        self.assertEqual(hc.tab_label(types.SimpleNamespace(id="h1"), named=False), "Node tidy")
        tip = hc.tab_tip(types.SimpleNamespace(id="h1"))
        self.assertIn("a helper chat that does part of the task", tip, "one plain sentence says what it is")
        self.assertTrue(tip.startswith(h1.name))
        self.assertEqual(hc.tab_tip(types.SimpleNamespace(id="nobody")), "")

    def test_with_the_preference_off_everything_is_plain(self):
        hc = self._hc()
        mw.set_enabled_for_tests(False)
        hp.HELPERS.add("p1", "h1", title="Node tidy", task="t", message="[Helper for \"B\"] t", parent_title="Build")
        card = hc.card(types.SimpleNamespace(id="p1"))
        self.assertEqual(card.title, "1 helper — 1 working")
        self.assertEqual(card.rows[0][1], "Node tidy")
        self.assertEqual(hc.tab_label(types.SimpleNamespace(id="h1")), "Node tidy")
        self.assertEqual(hc.tab_tip(types.SimpleNamespace(id="h1")), 'Helper for "Build": ')
        self.assertEqual(hp.waiting_text(2), "Waiting for 2 helpers")
        message = hp.framing("Build", "Tidy", "")
        self.assertEqual(mw.rewrite_agent_text(message), message)
        text = hp.results_text([hp.Helper(id="a", parent="p", title="A", status=hp.DONE)])
        self.assertEqual(mw.rewrite_agent_text(text), text)


class TestThinkingWord(_Case):

    def test_the_list(self):
        words = mw.THINKING_WORDS
        self.assertGreaterEqual(len(words), 20)
        self.assertEqual(len(set(words)), len(words))
        self.assertTrue(all(w and len(w) <= 24 and w == w.strip() and "." not in w for w in words))

    def test_one_word_per_stretch_another_for_the_next(self):
        start = 1763400000.123
        state = types.SimpleNamespace(turn_phase="", thinking_start_time=start)
        first = cs.phase_text(state)
        self.assertIn(first, mw.THINKING_WORDS)
        for _ in range(200):                        # every redraw of the stretch
            state.thinking_dots = getattr(state, "thinking_dots", 0) + 1
            self.assertEqual(cs.phase_text(state), first)
        seen = {cs.phase_text(types.SimpleNamespace(turn_phase="", thinking_start_time=start + n * 7.3))
                for n in range(40)}
        self.assertGreaterEqual(len(seen), 10, "the next stretches pick other words")

    def test_plain_with_the_preference_off_and_other_phases_unchanged(self):
        mw.set_enabled_for_tests(False)
        self.assertEqual(cs.phase_text(types.SimpleNamespace(turn_phase="", thinking_start_time=5.0)), "Thinking")
        mw.set_enabled_for_tests(True)
        self.assertEqual(cs.phase_text(types.SimpleNamespace(turn_phase="reading")), "Reading your message")
        self.assertEqual(cs.phase_text(types.SimpleNamespace(turn_phase="waiting")), "Waiting for the model")

    def test_the_chat_draws_the_same_word_each_frame_and_stores_none(self):
        class Live:
            is_thinking = True
            turn_phase = ""
            thinking_start_time = 1763400000.5
            streaming_text = ""
            reasoning_text = ""
            thinking_dots = 0
        hist = [{"role": "user", "content": "go", "turn_start": True}]
        live = Live()
        shown = set()
        for dots in range(30):
            live.thinking_dots = dots
            for compact in (False, True):               # chat editor and compact sidebar
                texts = self.draw(hist, live=live, compact=compact)
                found = [w for w in mw.THINKING_WORDS if re.search(r"^%s " % re.escape(w), texts, re.M)]
                self.assertEqual(len(found), 1, texts)
                shown.update(found)
        self.assertEqual(len(shown), 1, "no flicker over 60 frames")
        self.assertEqual(hist, [{"role": "user", "content": "go", "turn_start": True}], "nothing stored")
        mw.set_enabled_for_tests(False)
        self.assertRegex(self.draw(hist, live=live), r"(?m)^Thinking ")


class TestNothingMagicForTheModel(_Case):

    def test_what_the_model_gets_has_no_magic_words(self):
        message = hp.framing("Build the scene", "Tidy the nodes.", "NODE_EDITOR")
        results = hp.results_text([hp.Helper(id="a", parent="p", title="A", status=hp.DONE, answer="ok")])
        for text in (message, results):
            for word in mw.HELPER_WORDS + ("Mages", "mages") + mw.THINKING_WORDS:
                self.assertNotIn(word, text)
        self.assertTrue(message.startswith('[Helper for "Build the scene"]'))
        self.assertTrue(results.startswith("[Helpers] Your 1 helper finished"))

    def test_the_request_builders_do_not_use_the_wording(self):
        for name in ("llm_transport.py", "engine_loop.py", "engine.py", "engine_acp.py"):
            source = (_ADDON / name).read_text(encoding="utf-8")
            for token in ("magic_wording.helper_name", "rewrite_agent_text", "thinking_word", "shown_agent_text"):
                self.assertNotIn(token, source, "%s must not reword what is sent" % name)
        self.assertNotIn("magic", (_ADDON / "llm_transport.py").read_text(encoding="utf-8").lower())
        skills = "\n".join(p.read_text(encoding="utf-8") for p in (_ADDON / "skills").rglob("*.md"))
        for word in mw.THINKING_WORDS:
            self.assertNotIn(word + "...", skills)

    def test_the_sidebar_plan_goal_is_redacted(self):
        source = (_ADDON / "ui_chat.py").read_text(encoding="utf-8")
        self.assertIn("_draw_multiline(layout, _magic.shown_agent_text(goal)", source)
        self.assertIn("_log.redact(step.get(", source)

    def test_the_preference_exists_and_is_on_by_default(self):
        source = (_ADDON / "preferences.py").read_text(encoding="utf-8")
        match = re.search(r"magic_wording: BoolProperty\((.*?)\n    \)", source, re.S)
        self.assertIsNotNone(match)
        self.assertIn("default=True", match.group(1))
        self.assertIn('chat_box.prop(self, "magic_wording")', source)


if __name__ == "__main__":
    unittest.main()
