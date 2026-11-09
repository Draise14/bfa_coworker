# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Tests for Security 2 (#346): your API key, Hugging Face token and the session
token never end up in a log, an export or a Copy.

1. ``log.redact``: the secrets it was handed, ``Authorization: Bearer ...`` in
   any quoting, and ``sk-...`` / ``hf_...`` keys it was not told about, become
   ``***``; ordinary text is left alone.
2. A session with fake secrets: every place text leaves Coworker (console and
   log file through the print tee, ``operations.jsonl``, the session export and
   its clipboard version, the activity view's Copy Log, Copy Error and the
   chat's Copy buttons) is grepped for them.
3. ``llm_transport`` prints the URL, never the request headers.
4. Files that hold a secret are owner-only (POSIX): the harness ``install.json``
   and the settings backup, which keeps the keys on purpose in a separate
   ``secrets`` field.

Run with::

    python -m unittest tests.test_secrets_never_leak -v
"""

__all__ = ()

import contextlib
import importlib
import io
import json
import os
import sys
import tempfile
import threading
import types
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

_ADDON = Path(__file__).resolve().parents[1] / "addon" / "bfa_coworker"
_PKG = "bfacw_secrets_pkg"


def _import(name):
    if _PKG not in sys.modules:
        pkg = types.ModuleType(_PKG)
        pkg.__path__ = [str(_ADDON)]
        sys.modules[_PKG] = pkg
    return importlib.import_module(_PKG + "." + name)


log = _import("log")
activity = _import("activity")
ac = _import("agent_controller")
st = _import("settings_transfer")
si = _import("session_identity")
lt = _import("llm_transport")

# Fake secrets.  The gen key and the session token match no pattern: only the
# hand-over (set_secret) hides them.
API_KEY = "sk-or-v1-FAKEfake0123456789abcdefABCDEF"
HF_TOKEN = "hf_FAKEtoken0123456789abcdefXYZ"
GEN_KEY = "gen-Q2x9LmR7pZ4vN8sK"
SESSION_TOKEN = "Tq7Wm2Zr9Xk4Pv8Ln3Bs6Hd1"
BEARER = "unregistered-bearer-0123456789"         # only ever seen in a header
AUTH_LINE = "Authorization: Bearer " + BEARER
SECRETS = (API_KEY, HF_TOKEN, GEN_KEY, SESSION_TOKEN, BEARER)


def _hand_over():
    log.set_secret("remote_api_key", API_KEY)
    log.set_secret("hf_token", HF_TOKEN)
    log.set_secret("gen_remote_key", GEN_KEY)
    log.set_secret("session_token", SESSION_TOKEN)


def _clear():
    for name in ("remote_api_key", "hf_token", "gen_remote_key", "session_token"):
        log.set_secret(name, "")


class _Case(unittest.TestCase):

    def assertNoSecret(self, text, where):
        for secret in SECRETS:
            self.assertNotIn(secret, text, "{:s} leaks {:s}".format(where, secret))
        # Not even half of one (a cut after a few hundred characters).
        for secret in SECRETS:
            self.assertNotIn(secret[:12], text, "{:s} leaks the start of {:s}".format(where, secret))


class TestRedact(_Case):

    def setUp(self):
        _hand_over()
        self.addCleanup(_clear)

    def test_handed_over_secrets_become_stars(self):
        text = "key {:s}, hf {:s}, gen {:s}, token {:s}".format(API_KEY, HF_TOKEN, GEN_KEY, SESSION_TOKEN)
        self.assertEqual(log.redact(text), "key ***, hf ***, gen ***, token ***")

    def test_authorization_headers_in_any_quoting(self):
        for text in (AUTH_LINE,
                     "{'Authorization': 'Bearer " + BEARER + "'}",
                     '{"Authorization": "Bearer ' + BEARER + '"}',
                     "authorization=Basic " + BEARER,
                     "curl -H 'Authorization: token " + BEARER + "'"):
            out = log.redact(text)
            self.assertNotIn(BEARER, out, text)
            self.assertIn("***", out)
        self.assertEqual(log.redact(AUTH_LINE), "Authorization: Bearer ***")

    def test_unknown_keys_that_look_like_keys(self):
        _clear()
        out = log.redact("OPENAI_API_KEY=sk-proj-abcdefghijklmnopqrstuvwx and hf_abcdefghijklmnopqrstuvwxyz12")
        self.assertEqual(out, "OPENAI_API_KEY=*** and ***")

    def test_ordinary_text_is_left_alone(self):
        for text in ("prefs.hf_token is set", "risk-assessment-pipeline-step", "task-scheduler-for-the-renders",
                     "Bearer", "the bearer of bad news", "", "Authorization failed (401)"):
            self.assertEqual(log.redact(text), text)

    def test_short_values_are_not_hidden(self):
        log.set_secret("gen_remote_key", "abc")
        self.assertEqual(log.redact("abc abcdef"), "abc abcdef")

    def test_a_changed_key_replaces_the_old_one(self):
        log.set_secret("remote_api_key", "old-key-123456789")
        log.set_secret("remote_api_key", "new-key-987654321")
        self.assertEqual(log.redact("old-key-123456789 new-key-987654321"), "old-key-123456789 ***")

    def test_environment_tokens(self):
        with mock.patch.dict(os.environ, {"BFACW_TOKEN": "env-token-0123456789abc"}):
            log.set_secret("unused", "")
            self.assertEqual(log.redact("env-token-0123456789abc"), "***")
        log.set_secret("unused", "")

    def test_twice_is_the_same(self):
        once = log.redact(AUTH_LINE + " " + API_KEY)
        self.assertEqual(log.redact(once), once)


class _FakeTexts:
    def __init__(self):
        self.written = []

    def new(self, _name):
        return types.SimpleNamespace(write=self.written.append)


class TestASessionWithSecrets(_Case):
    """Everything a session writes or copies, grepped for the secrets."""

    def setUp(self):
        _hand_over()
        self.addCleanup(_clear)
        self.home = tempfile.TemporaryDirectory()
        self.addCleanup(self.home.cleanup)
        patcher = mock.patch("pathlib.Path.home", return_value=Path(self.home.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        state = ac._agent_state
        for name, value in (
                ("conversation_history", [
                    {"role": "user", "content": "use my key " + API_KEY + " please"},
                    {"role": "reasoning", "content": "the token is " + SESSION_TOKEN},
                    {"role": "assistant", "content": "ok", "tool_calls": [{"function": {
                        "name": "execute_blender_code",
                        "arguments": json.dumps({"code": "hf = '{:s}'".format(HF_TOKEN)})}}]},
                    # A long result: the export cuts it at 1000 characters, inside the key.
                    {"role": "tool", "content": "x" * 990 + GEN_KEY + " " + AUTH_LINE},
                ]),
                ("error", "LLM request failed"),
                ("error_full", "HTTP 401 with headers {'Authorization': 'Bearer " + BEARER + "'}"),
                ("llm_stderr", "download: hf token " + HF_TOKEN)):
            patcher = mock.patch.object(state, name, value, create=True)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(ac, "_get_system_prompt_with_rules",
                                    return_value="Rules: my key is " + API_KEY)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_console_and_log_file(self):
        sink = io.StringIO()
        tee = log._TeeStream(sink)
        with mock.patch.object(log, "_suppress_console", False):
            with contextlib.redirect_stdout(tee):
                print("[Coworker] remote key", API_KEY)
                print("[Coworker] headers:", {"Authorization": "Bearer " + BEARER})
                print("[Coworker] ERROR token", SESSION_TOKEN, "hf", HF_TOKEN, "gen", GEN_KEY)
        log.write("direct " + API_KEY)
        written = log.get_log_path().read_text(encoding="utf-8")
        self.assertNoSecret(sink.getvalue(), "the console")
        self.assertNoSecret(written, "coworker.log")
        self.assertIn("[Coworker] remote key ***", sink.getvalue())
        self.assertIn("direct ***", written)

    def test_operations_log(self):
        ac._log_operation("execute_blender_code", {"code": "key = '{:s}'".format(API_KEY)},
                          "x" * 495 + HF_TOKEN)
        path = Path(self.home.name) / ".cache" / "bfa_coworker" / "operations.jsonl"
        self.assertNoSecret(path.read_text(encoding="utf-8"), "operations.jsonl")

    def test_session_export_and_its_clipboard_copy(self):
        self.assertNoSecret(ac.export_session_log_to_clipboard(), "Copy Session Log")
        timers, texts = [], _FakeTexts()
        bpy = types.SimpleNamespace(
            app=types.SimpleNamespace(timers=types.SimpleNamespace(register=lambda fn, **_kw: timers.append(fn)),
                                      version_string="5.3"),
            data=types.SimpleNamespace(texts=texts),
            utils=types.SimpleNamespace(resource_path=lambda _kind: ""))
        with mock.patch.dict(sys.modules, {"bpy": bpy}):
            ac.export_session_log(auto_saved=True)
            for fn in timers:
                fn()
        self.assertEqual(len(texts.written), 1)
        exported = texts.written[0]
        self.assertNoSecret(exported, "Export Session Log")
        self.assertIn("Current error: HTTP 401 with headers {'Authorization': 'Bearer ***'}", exported)

    def test_activity_copy_log(self):
        activity.clear()
        self.addCleanup(activity.clear)
        # The view keeps 120 characters of the input: the key sits across that cut.
        activity.record("execute_blender_code", {"code": "#" * 100 + API_KEY}, {"result": HF_TOKEN}, 0.2)
        activity.record("execute_blender_code", AUTH_LINE, {"status": "error", "message": SESSION_TOKEN}, 0.1)
        self.assertNoSecret(activity.log_text(), "the activity Copy Log")
        for entry in activity.entries():
            self.assertNoSecret(entry.args + entry.preview, "the activity view")

    def test_copy_error_shows_stars(self):
        op = _ui_chat_class("BFACW_OT_copy_status_error")()
        op.report = lambda *_a: None
        wm = types.SimpleNamespace(clipboard="")
        self.assertEqual(op.execute(types.SimpleNamespace(window_manager=wm)), {"FINISHED"})
        self.assertEqual(wm.clipboard, "HTTP 401 with headers {'Authorization': 'Bearer ***'}")

    def test_chat_copy_buttons(self):
        wm = types.SimpleNamespace(clipboard="")
        do = _chat_editor_do(wm)
        do("copy", "here is the code:\napi_key = '{:s}'\n{:s}".format(API_KEY, AUTH_LINE), None)
        self.assertNoSecret(wm.clipboard, "a chat Copy button")
        self.assertIn("api_key = '***'", wm.clipboard)
        activity.clear()
        self.addCleanup(activity.clear)
        activity.record("code", {"token": SESSION_TOKEN}, {"result": "ok"}, 0.1)
        do("notice", "copy_log", None)
        self.assertNoSecret(wm.clipboard, "the External Harness card's Copy Log")
        self.assertIn("***", wm.clipboard)


def _ui_chat_class(name):
    src = (_ADDON / "ui_chat.py").read_text(encoding="utf-8")
    start = src.index("class {:s}(".format(name))
    end = src.index("\n\n\n", start)
    ns = {"__name__": _PKG + ".ui_chat", "__package__": _PKG, "Operator": object, "agent_controller": ac,
          "bpy": types.SimpleNamespace(types=types.SimpleNamespace(Context=object))}
    exec(compile(src[start:end], "ui_chat.py", "exec"), ns)      # noqa: S102
    return ns[name]


def _chat_editor_do(wm):
    src = (_ADDON / "chat_editor.py").read_text(encoding="utf-8")
    start = src.index("def _do(")
    end = src.index("\n\n\n", start)
    bpy = types.SimpleNamespace(context=types.SimpleNamespace(window_manager=wm),
                                ops=types.SimpleNamespace(bfacw=None))
    ns = {"__name__": _PKG + ".chat_editor", "__package__": _PKG, "__builtins__": __builtins__,
          "bpy": bpy, "_props": lambda: None, "_prefs": lambda _c: None,
          "agent_controller": ac, "mcp_to_blender_server": None}
    exec(compile("from __future__ import annotations\n" + src[start:end], "chat_editor.py", "exec"), ns)  # noqa: S102
    return ns["_do"]


class _LLM(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802 (stdlib name)
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        self.server.auth.append(self.headers.get("Authorization", ""))
        message = {"role": "assistant", "content": "hello"}
        if body.get("stream"):
            chunk = {"choices": [{"delta": message, "finish_reason": "stop"}]}
            data = "data: {:s}\n\ndata: [DONE]\n\n".format(json.dumps(chunk)).encode()
            ctype = "text/event-stream"
        else:
            data = json.dumps({"choices": [{"message": message, "finish_reason": "stop"}]}).encode()
            ctype = "application/json"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *_args):
        pass


class TestTransportPrintsOnlyTheUrl(_Case):

    def setUp(self):
        _clear()        # the transport's own output, not the tee's redaction
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _LLM)
        self.server.auth = []
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.url = "http://127.0.0.1:{:d}/v1/chat/completions".format(self.server.server_address[1])
        lt.bind(types.SimpleNamespace(error="", error_full="", error_kind="", warning=""), threading.Event())

    def _check(self, call):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            result = call(self.url, [{"role": "user", "content": "hi"}], [], api_key=API_KEY, model="m")
        self.assertIsNotNone(result)
        self.assertEqual(self.server.auth, ["Bearer " + API_KEY])     # the key was really sent
        self.assertIn(self.url, out.getvalue())
        self.assertNoSecret(out.getvalue(), call.__name__)
        self.assertNotIn("Authorization", out.getvalue())

    def test_plain_request(self):
        self._check(lt.openai_chat_completions)

    def test_streaming_request(self):
        self._check(lt.openai_chat_completions_stream)


class TestFilesThatHoldASecret(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)

    @unittest.skipIf(os.name == "nt", "Windows ignores the mode; the user profile is private")
    def test_install_json_is_owner_only(self):
        with mock.patch.object(ac, "_HARNESS_DIR", Path(self.dir.name) / "harness"), \
                mock.patch.object(si, "token_required", return_value=True), \
                mock.patch.object(si, "session_token", return_value=SESSION_TOKEN):
            path = ac._HARNESS_DIR / "install.json"
            ac._HARNESS_DIR.mkdir()
            ac.ensure_harness_launcher("")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            path.chmod(0o644)                   # written by an older Coworker, content unchanged
            ac.ensure_harness_launcher("")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["token"], SESSION_TOKEN)

    def test_install_json_is_written_through_write_private(self):
        # Every OS: install.json goes through the owner-only writer.
        with mock.patch.object(ac, "_HARNESS_DIR", Path(self.dir.name) / "harness"), \
                mock.patch.object(si, "write_private", wraps=si.write_private) as private:
            ac.ensure_harness_launcher("")
        self.assertEqual([Path(c.args[0]).name for c in private.call_args_list], ["install.json"])

    def test_settings_backup_keeps_secrets_apart_and_private(self):
        prefs = _Prefs(remote_api_key=API_KEY, hf_token=HF_TOKEN, gen_remote_key=GEN_KEY, local_ctx_size=8192)
        with mock.patch("builtins.print") as out, mock.patch.object(st.os, "open", wraps=os.open) as opened:
            self.assertTrue(st.save(prefs, self.dir.name, "1.1.42"))
        out.assert_not_called()                                 # never logs the file
        self.assertEqual(opened.call_args.args[2], 0o600)       # created owner-only
        payload = json.loads(Path(st.settings_path(self.dir.name)).read_text(encoding="utf-8"))
        self.assertEqual(payload["format"], 2)
        self.assertEqual(payload["secrets"], {"gen_remote_key": GEN_KEY, "hf_token": HF_TOKEN,
                                              "remote_api_key": API_KEY})
        self.assertEqual(payload["settings"], {"local_ctx_size": 8192})
        if os.name != "nt":
            self.assertEqual(os.stat(st.settings_path(self.dir.name)).st_mode & 0o777, 0o600)
        # ...and a fresh install gets them back.
        new = _Prefs(remote_api_key="", hf_token="", gen_remote_key="", local_ctx_size=4096)
        self.assertEqual(set(st.restore_once(new, self.dir.name)),
                         {"remote_api_key", "hf_token", "gen_remote_key", "local_ctx_size"})
        self.assertEqual((new.remote_api_key, new.hf_token, new.local_ctx_size), (API_KEY, HF_TOKEN, 8192))

    def test_a_secret_that_fails_to_restore_is_named_not_quoted(self):
        new = _Prefs(remote_api_key="", local_ctx_size=4096)
        new.refuse = "remote_api_key"
        with mock.patch("builtins.print") as out:
            st.apply(new, {"remote_api_key": API_KEY})
        self.assertIn("remote_api_key", out.call_args[0][0])
        self.assertNotIn(API_KEY, out.call_args[0][0])


class _Prefs:
    """Just enough of an AddonPreferences for settings_transfer."""

    def __init__(self, **values):
        props = [types.SimpleNamespace(identifier=k, type="INT" if isinstance(v, int) else "STRING",
                                       subtype="PASSWORD" if not isinstance(v, int) else "NONE",
                                       is_readonly=False, is_array=False, array_length=0)
                 for k, v in values.items()]
        object.__setattr__(self, "bl_rna", types.SimpleNamespace(properties=props))
        object.__setattr__(self, "refuse", "")
        object.__setattr__(self, "settings_restored", False)
        for k, v in values.items():
            object.__setattr__(self, k, v)

    def __setattr__(self, key, value):
        if key == self.refuse:
            raise TypeError("cannot set {!r}".format(value))
        object.__setattr__(self, key, value)


if __name__ == "__main__":
    unittest.main()
