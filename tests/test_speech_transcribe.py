# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Local transcription with whisper.cpp (Tier 4, task "Video 1", #375).

1. The media key, the cache paths and the lookup #376 (captions) uses.
2. The ``bfa_coworker.transcript/1`` format: validate, write, read.
3. whisper-server ``verbose_json`` -> words, on a trimmed real answer of the spike (#374:
   large-v3-turbo, CUDA, the 125 s test clip) against its own word list and the
   synthesizer's word starts; and an OpenAI-style answer (Remote).
4. Release assets by pattern, on the real asset lists of b5454 and b5130.
5. Zip-slip: an archive with a member outside its folder is refused whole.
6. The server: the start line, ports, start / ready / transcribe / stop against a stand-in
   server (a small Python HTTP server in place of whisper-server.exe).
7. Downloads from a local HTTP server: progress, install layout, cancel, disk space.
8. Remote ``/v1/audio/transcriptions`` against a fake HTTP server (no real API call).
9. The tool's results: frames for a strip, paging, plan and setup messages, the
   tool-code's Params / Result.

Run with::

    python -m unittest tests.test_speech_transcribe -v
"""

__all__ = ()

import hashlib
import importlib
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

_REPO = Path(__file__).resolve().parents[1]
_ADDON = _REPO / "addon" / "bfa_coworker"
_DATA = Path(__file__).resolve().parent / "data"
_PKG = "bfacw_speech_pkg"


def _load(name):
    if _PKG not in sys.modules:
        pkg = types.ModuleType(_PKG)
        pkg.__path__ = [str(_ADDON)]
        sys.modules[_PKG] = pkg
    return importlib.import_module(_PKG + "." + name)


tr = _load("transcripts")
wm = _load("whisper_manager")
speech = _load("speech")
shared = _load("shared")
pg = _load("process_guard")


def _toolcode():
    sys.path.insert(0, str(_REPO / "mcp"))
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "transcribe_strip_toolcode_under_test", _REPO / "mcp" / "blmcp" / "tools" / "transcribe_strip_toolcode.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path.remove(str(_REPO / "mcp"))


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _TempData(unittest.TestCase):
    """Each test gets its own whisper data folder and process registry."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        wm.set_data_dir(self.tmp / "whisper")
        self._lad = os.environ.get("LOCALAPPDATA")
        os.environ["LOCALAPPDATA"] = str(self.tmp / "lad")

    def tearDown(self):
        wm.stop_server()
        wm.set_data_dir(None)
        if self._lad is None:
            os.environ.pop("LOCALAPPDATA", None)
        else:
            os.environ["LOCALAPPDATA"] = self._lad
        self._tmp.cleanup()


def _doc(words=None, key="0123456789abcdef", model="small", language="en"):
    return {"schema": tr.SCHEMA,
            "media": {"path": "C:/clips/a.mp4", "key": key, "size": 10, "duration_s": 3.0},
            "engine": {"name": "whisper.cpp", "model": model, "timing": "dtw"},
            "language": language, "created": "2026-11-14T00:00:00+00:00",
            "words": words if words is not None else [
                {"w": "Hello,", "start": 0.5, "end": 0.9, "p": 0.9},
                {"w": "world.", "start": 0.9, "end": 1.9, "p": 0.8},
                {"w": "Again", "start": 2.5, "end": 3.0, "p": 0.7}]}


# ---------------------------------------------------------------------------
# 1. Media key and cache

class TestMediaKeyAndCache(_TempData):

    def test_key_is_sha1_of_size_first_and_last_mib(self):
        small = self.tmp / "small.wav"
        small.write_bytes(b"abc" * 1000)
        key, size = tr.media_key(small)
        self.assertEqual(size, 3000)
        self.assertEqual(key, hashlib.sha1(b"3000" + b"abc" * 1000).hexdigest()[:16])
        big = self.tmp / "big.mp4"
        data = os.urandom(3 << 20)
        big.write_bytes(data)
        key, size = tr.media_key(big)
        want = hashlib.sha1(str(len(data)).encode() + data[:1 << 20] + data[-(1 << 20):]).hexdigest()[:16]
        self.assertEqual((key, size), (want, len(data)))
        self.assertRegex(key, r"^[0-9a-f]{16}$")
        moved = self.tmp / "renamed elsewhere.mp4"
        shutil.copy(big, moved)
        self.assertEqual(tr.media_key(moved)[0], key)          # stable across renames / moves

    def test_cache_path_next_to_the_blend_or_in_the_cache_folder(self):
        blend = self.tmp / "proj" / "edit.blend"
        p = tr.cache_path("0123456789abcdef", "small", "en", str(blend), self.tmp / "fb")
        self.assertEqual(p, (self.tmp / "proj").resolve() / "coworker_transcripts" / "0123456789abcdef.small.en.json")
        p = tr.cache_path("0123456789abcdef", "large-v3-turbo", "auto", "", self.tmp / "fb")
        self.assertEqual(p, self.tmp / "fb" / "coworker_transcripts" / "0123456789abcdef.large-v3-turbo.auto.json")

    def test_find_cached_and_load_for_media(self):
        media = self.tmp / "clip.wav"
        media.write_bytes(b"RIFF" + b"\0" * 100)
        key, _ = tr.media_key(media)
        blend = self.tmp / "proj" / "edit.blend"
        self.assertIsNone(tr.load_for_media(str(media), blend_path=str(blend), fallback_dir=self.tmp / "fb"))
        tr.write(tr.cache_path(key, "small", "en", str(blend)), _doc(key=key))
        self.assertIsNotNone(tr.find_cached(key, "small", "en", str(blend)))
        self.assertIsNotNone(tr.find_cached(key, None, None, str(blend)))          # any model / language
        self.assertIsNotNone(tr.find_cached(key, "small", "auto", str(blend)))     # auto = any language
        self.assertIsNone(tr.find_cached(key, "base", None, str(blend)))
        self.assertIsNone(tr.find_cached(key, "small", "de", str(blend)))
        got = tr.load_for_media(str(media), blend_path=str(blend), fallback_dir=self.tmp / "fb")
        self.assertEqual(tr.words(got)[0]["w"], "Hello,")
        self.assertIsNone(tr.load_for_media(str(self.tmp / "gone.wav"), blend_path=str(blend)))

    def test_transcribed_before_the_blend_was_saved(self):
        media = self.tmp / "clip.wav"
        media.write_bytes(b"x" * 50)
        key, _ = tr.media_key(media)
        tr.write(tr.cache_path(key, "small", "en", "", self.tmp / "fb"), _doc(key=key))
        got = tr.load_for_media(str(media), blend_path=str(self.tmp / "later.blend"), fallback_dir=self.tmp / "fb")
        self.assertIsNotNone(got)

    def test_names_for_captions(self):
        # #376 imports these lazily by name.
        self.assertEqual(tr.SCHEMA, "bfa_coworker.transcript/1")
        self.assertTrue(callable(tr.load_for_media) and callable(tr.words))
        self.assertEqual(tr.words(None), [])
        self.assertEqual([w["w"] for w in tr.words(_doc())], ["Hello,", "world.", "Again"])


# ---------------------------------------------------------------------------
# 2. The format

class TestSchema(_TempData):

    def test_round_trip(self):
        doc = _doc()
        path = tr.write(self.tmp / "t" / "x.json", doc)
        self.assertEqual(tr.read(path), doc)
        self.assertEqual(tr.validate(doc), [])

    def test_sample_of_the_spike_is_valid(self):
        vj = json.loads((_DATA / "whisper_verbose_json_turbo_trimmed.json").read_text(encoding="utf-8"))
        doc = tr.from_verbose_json(vj, "C:/m.wav", "156ea4f55a7c24d9", 4002126, "large-v3-turbo", build="CUDA 12")
        self.assertEqual(tr.validate(doc), [])

    def test_invalid_documents(self):
        self.assertIn("missing words", tr.validate({k: v for k, v in _doc().items() if k != "words"}))
        bad = _doc()
        bad["media"]["key"] = "XYZ"
        self.assertTrue(any("key" in e for e in tr.validate(bad)))
        bad = _doc()
        bad["schema"] = "other/2"
        self.assertTrue(tr.validate(bad))
        bad = _doc(words=[{"w": "a", "start": "0"}])
        self.assertTrue(tr.validate(bad))
        bad = _doc()
        bad["engine"]["timing"] = "guess"
        self.assertTrue(tr.validate(bad))
        with self.assertRaises(ValueError):
            tr.write(self.tmp / "bad.json", bad)
        self.assertFalse((self.tmp / "bad.json").exists())


# ---------------------------------------------------------------------------
# 3. verbose_json -> words

def _truth():
    rows = []
    with open(_DATA / "whisper_speech_clip_truth.tsv", encoding="utf-8-sig") as fh:
        for line in fh:
            t, w = line.rstrip("\n").split("\t", 1)
            rows.append((float(t), w))
    return rows


class TestVerboseJson(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.vj = json.loads((_DATA / "whisper_verbose_json_turbo_trimmed.json").read_text(encoding="utf-8"))
        cls.words, cls.timing = tr.verbose_json_words(cls.vj)

    def test_words_of_the_real_answer(self):
        self.assertEqual(self.timing, "dtw")
        texts = [w["w"] for w in self.words]
        nonempty = [s for s in self.vj["segments"] if s.get("text", "").strip()]
        self.assertEqual(len(texts), len(nonempty))             # one word per non-empty segment
        self.assertEqual(texts[:5], ["Hello,", "and", "welcome", "to", "this"])
        self.assertIn("sequencer.", texts)                      # punctuation stays with its word
        # The spike's own converter on the full answer (sample_transcript.json).
        self.assertEqual(self.words[0], {"w": "Hello,", "start": 1.44, "end": 2.24, "p": 0.884})
        self.assertEqual(self.words[1], {"w": "and", "start": 2.24, "end": 2.58, "p": 0.958})
        starts = [w["start"] for w in self.words]
        self.assertEqual(starts, sorted(starts))
        for a, b in zip(self.words, self.words[1:]):
            self.assertEqual(a["end"], min(b["start"], round(a["start"] + tr.MAX_WORD_S, 3)))

    def test_start_error_against_the_synthesizer(self):
        import difflib
        import re

        def norm(w):
            return re.sub(r"[^a-z0-9']", "", w.lower())
        ref = _truth()
        a = [norm(w["w"]) for w in self.words]
        b = [norm(r[1]) for r in ref]
        errs = []
        for blk in difflib.SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks():
            for k in range(blk.size):
                errs.append(abs(self.words[blk.a + k]["start"] - ref[blk.b + k][0]))
        self.assertGreater(len(errs), 35)
        self.assertLess(sum(errs) / len(errs), 0.15)            # spike: mean 0.10 s for turbo
        self.assertLess(max(errs), 0.5)

    def test_punctuation_segment_joins_its_word_and_specials_are_skipped(self):
        vj = {"duration": 3.0, "segments": [
            {"text": " Yes", "start": 0.0, "end": 0.5, "words": [
                {"word": "[_BEG_]", "t_dtw": 1, "probability": 0.01},
                {"word": " Yes", "t_dtw": 74, "probability": 0.9}]},
            {"text": "!", "start": 0.5, "end": 0.6, "words": [{"word": "!", "t_dtw": 90, "probability": 0.5}]},
            {"text": "", "start": 1.0, "end": 1.0},
            {"text": " no", "start": 1.0, "end": 1.5, "words": [{"word": " no", "t_dtw": 124, "probability": 0.8}]},
        ]}
        words, timing = tr.verbose_json_words(vj)
        self.assertEqual(timing, "dtw")
        self.assertEqual(words, [{"w": "Yes!", "start": 0.5, "end": 1.0, "p": 0.5},
                                 {"w": "no", "start": 1.0, "end": 2.0, "p": 0.8}])

    def test_without_dtw_the_token_times_are_used(self):
        vj = {"duration": 2.0, "segments": [
            {"text": " a", "start": 0.3, "end": 0.6, "words": [{"word": " a", "t_dtw": -1, "probability": 1.0}]},
            {"text": " b", "start": 0.2, "end": 0.9, "words": [{"word": " b", "t_dtw": -1, "probability": 1.0}]}]}
        words, timing = tr.verbose_json_words(vj)
        self.assertEqual(timing, "token")
        self.assertEqual([w["start"] for w in words], [0.3, 0.31])     # kept monotonic

    def test_language_and_engine(self):
        doc = tr.from_verbose_json(self.vj, "C:\\m\\a.wav", "156ea4f55a7c24d9", 4002126, "large-v3-turbo",
                                   build="CUDA 12", version="b5454")
        self.assertEqual(doc["language"], "en")
        self.assertEqual(doc["media"]["path"], "C:/m/a.wav")
        self.assertEqual(doc["engine"]["dtw_offset_s"], -0.24)
        self.assertEqual(doc["engine"]["version"], "b5454")
        self.assertEqual(tr.language_code("German"), "de")

    def test_openai_answer(self):
        resp = {"task": "transcribe", "language": "english", "duration": 2.0, "text": "Hi there",
                "words": [{"word": "Hi", "start": 0.1, "end": 0.4}, {"word": "there", "start": 0.4, "end": 0.9},
                          {"word": "", "start": 1.0, "end": 1.1}]}
        doc = tr.from_openai(resp, "a.wav", "0123456789abcdef", 5, "whisper-1", language="auto")
        self.assertEqual(tr.validate(doc), [])
        self.assertEqual(doc["engine"]["timing"], "remote")
        self.assertEqual(doc["language"], "en")
        self.assertEqual(doc["words"], [{"w": "Hi", "start": 0.1, "end": 0.4}, {"w": "there", "start": 0.4, "end": 0.9}])


# ---------------------------------------------------------------------------
# 4. Release assets

class TestReleaseAssets(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.releases = json.loads((_DATA / "whisper_releases_b5454_b5130.json").read_text(encoding="utf-8"))

    def names(self, tag):
        return [a["name"] for r in self.releases if r["tag_name"] == tag for a in r["assets"]]

    def test_patterns_on_b5454_and_b5130(self):
        self.assertEqual(wm.match_asset(self.names("b5454"), "cpu"), "whisper-bin-x64.zip")
        self.assertEqual(wm.match_asset(self.names("b5454"), "cuda12"), "whisper-bin-win-cuda-12.4.0-x64.zip")
        self.assertEqual(wm.match_asset(self.names("b5130"), "cpu"), "whisper-bin-x64.zip")
        self.assertEqual(wm.match_asset(self.names("b5130"), "cuda12"), "whisper-cublas-12.4.0-bin-x64.zip")
        # Not ours: BLAS, 32-bit, ARM, CUDA 11.8, Linux.
        for name in ("whisper-blas-bin-x64.zip", "whisper-bin-Win32.zip", "whisper-bin-win-cuda-13.4-arm64.zip",
                     "whisper-bin-win-cuda-11.8.0-x64.zip", "whisper-cublas-11.8.0-bin-x64.zip",
                     "whisper-bin-ubuntu-x64.tar.gz"):
            self.assertIsNone(wm.match_asset([name], "cpu"), name)
            self.assertIsNone(wm.match_asset([name], "cuda12"), name)

    def test_newest_release_with_assets_not_the_tagged_one(self):
        self.assertEqual(self.names("v1.9.5"), [])          # what releases/latest gives
        rel, asset = wm.pick_release(self.releases, "cuda12")
        self.assertEqual((rel["tag_name"], asset["name"], asset["size"]),
                         ("b5454", "whisper-bin-win-cuda-12.4.0-x64.zip", 684913404))
        rel, asset = wm.pick_release(self.releases, "cpu")
        self.assertEqual((rel["tag_name"], asset["size"]), ("b5454", 8928640))
        older = [r for r in self.releases if r["tag_name"] != "b5454"]
        self.assertEqual(wm.pick_release(older, "cuda12")[0]["tag_name"], "b5130")
        self.assertIsNone(wm.pick_release([{"tag_name": "v9", "assets": []}], "cpu"))


# ---------------------------------------------------------------------------
# 5. Zip-slip

class TestZipSlip(_TempData):

    def _zip(self, members):
        path = self.tmp / "a.zip"
        with zipfile.ZipFile(path, "w") as zf:
            for name, data in members:
                zf.writestr(zipfile.ZipInfo(name), data)
        return path

    def test_member_outside_is_refused_whole(self):
        for evil in ("../evil.txt", "Release/../../evil.txt", "/abs/evil.txt", "C:/evil.txt", "..\\evil.txt"):
            dest = self.tmp / "out"
            shutil.rmtree(dest, ignore_errors=True)
            path = self._zip([("Release/whisper-server.exe", b"MZ"), (evil, b"x")])
            with self.assertRaises(wm.UnsafeArchive, msg=evil):
                wm.safe_extract(path, dest)
            self.assertFalse((self.tmp / "evil.txt").exists())
            self.assertFalse((dest / "Release" / "whisper-server.exe").exists(), evil)   # nothing written

    def test_normal_archive(self):
        path = self._zip([("Release/whisper-server.exe", b"MZ"), ("Release/ggml.dll", b"d")])
        self.assertEqual(wm.safe_extract(path, self.tmp / "out"), 2)
        self.assertEqual((self.tmp / "out" / "Release" / "whisper-server.exe").read_bytes(), b"MZ")


# ---------------------------------------------------------------------------
# 6. The server

_FAKE_SERVER = r'''
import json, sys, time
from http.server import BaseHTTPRequestHandler, HTTPServer
port = int(sys.argv[sys.argv.index("--port") + 1])
time.sleep(0.3)        # loading the model: connection refused meanwhile
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        body = b'{"status":"ok"}'
        self.send_response(200 if self.path == "/health" else 404)
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0); raw = self.rfile.read(n)
        ok = b'name="max_len"\r\n\r\n1' in raw and b'name="split_on_word"\r\n\r\ntrue' in raw and b"RIFF" in raw
        vj = {"duration": 1.0, "language": "english", "language_probabilities": {"en": 0.99},
              "segments": [{"text": " Hi" if ok else " BAD", "start": 0.0, "end": 0.5,
                            "words": [{"word": " Hi", "t_dtw": 50, "probability": 0.9}]}],
              "argv": sys.argv[1:]}
        body = json.dumps(vj).encode()
        self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers()
        self.wfile.write(body)
HTTPServer(("127.0.0.1", port), H).serve_forever()
'''


class TestServer(_TempData):

    def _install_fake(self, build="cpu", model="base"):
        exe = wm.build_dir(build) / "Release" / "whisper-server.exe"
        exe.parent.mkdir(parents=True)
        exe.write_bytes(b"MZ")
        preset = wm.get_preset(model)
        with open(wm.model_file(model), "wb") as fh:
            fh.truncate(preset.size)                 # a sparse file of the right size
        script = self.tmp / "fake_whisper_server.py"
        script.write_text(_FAKE_SERVER, encoding="utf-8")
        real = wm.server_command

        def cmd(exe_, model_path, port, threads, dtw):
            line = real(exe_, model_path, port, threads, dtw)
            return [sys.executable, str(script)] + line[1:]
        return mock.patch.object(wm, "server_command", cmd)

    def test_start_line(self):
        self.assertEqual(wm.server_command("C:/w/Release/whisper-server.exe", "C:/m/ggml-small.bin", 8481, 8, "small"),
                         ["C:/w/Release/whisper-server.exe", "-m", "C:/m/ggml-small.bin", "--host", "127.0.0.1",
                          "--port", "8481", "-t", "8", "-nfa", "--dtw", "small"])
        self.assertEqual(wm.default_threads(16), 8)
        self.assertEqual(wm.default_threads(4), 4)
        self.assertEqual({p.identifier: p.dtw for p in wm.MODEL_PRESETS},
                         {"tiny": "tiny", "base": "base", "small": "small", "medium": "medium",
                          "large-v3-turbo": "large.v3.turbo"})
        self.assertEqual([p.identifier for p in wm.MODEL_PRESETS if not p.measured], ["tiny", "medium"])
        self.assertEqual(wm.inference_fields("de", "Bforartists"), [
            ("response_format", "verbose_json"), ("max_len", "1"), ("split_on_word", "true"),
            ("language", "de"), ("prompt", "Bforartists")])
        self.assertEqual(wm.inference_fields("")[-1], ("language", "auto"))

    def test_builds_and_models(self):
        with mock.patch.object(wm, "_nvidia", [True]):
            self.assertEqual(wm.builds_to_download("AUTO"), ["cpu", "cuda12"])
            self.assertEqual(wm.resolve_build("AUTO"), "cuda12")
            self.assertEqual(wm.resolve_model("AUTO", "cuda12"), "large-v3-turbo")
        with mock.patch.object(wm, "_nvidia", [False]):
            self.assertEqual(wm.builds_to_download("AUTO"), ["cpu"])
            self.assertEqual(wm.resolve_build("AUTO"), "cpu")
            self.assertEqual(wm.resolve_model("AUTO", "cpu"), "small")
            self.assertEqual(wm.resolve_model("base", "cpu"), "base")
            self.assertEqual(wm.builds_to_download("CUDA"), ["cuda12"])
        self.assertEqual(wm.model_status("small"), "missing")
        wm.model_file("small").write_bytes(b"partial")
        self.assertEqual(wm.model_status("small"), "incomplete")

    def test_existing_files_are_used(self):
        # Files put there by hand (no download marker) count as installed.
        exe = wm.build_dir("cuda12") / "Release" / "whisper-server.exe"
        exe.parent.mkdir(parents=True)
        exe.write_bytes(b"MZ")
        self.assertEqual(wm.server_exe("cuda12"), exe)
        self.assertEqual(wm.installed_builds(), ["cuda12"])
        self.assertEqual(wm.build_info("cuda12"), {})

    def test_refuses_clearly_when_not_downloaded(self):
        if wm.platform_problem():
            self.skipTest("local speech is Windows only")
        msg = wm.start_server("small", "cpu", _free_port())
        self.assertIn("isn't downloaded", msg)
        self.assertIn("Speech > Download", msg)

    def test_other_platforms_say_so(self):
        with mock.patch.object(wm.sys, "platform", "darwin"):
            self.assertIn("isn't available on this platform yet", wm.platform_problem())
            self.assertIn("isn't available", wm.start_server("small", "cpu", 1))
            self.assertFalse(wm.start_download("server", "AUTO"))

    def test_start_ready_transcribe_stop(self):
        if wm.platform_problem():
            self.skipTest("local speech is Windows only")
        port = _free_port()
        with self._install_fake():
            self.assertEqual(wm.start_server("base", "cpu", port), "")
            st = wm.get_state()
            self.assertTrue(st.starting)
            self.assertIn("Starting whisper-server", st.status_text)
            self.assertTrue(wm.wait_ready(30))
            self.assertEqual(wm.running_port(), port)
            self.assertTrue(wm.health(port))
            pid = wm.get_state().proc.pid
            self.assertIn(pid, [e["pid"] for e in pg._load() if e["kind"] == "whisper-server"])
            self.assertEqual(wm.start_server("base", "cpu", port), "")       # already running: no-op
            self.assertEqual(wm.get_state().proc.pid, pid)
            wav = self.tmp / "a.wav"
            wav.write_bytes(b"RIFF" + b"\0" * 64)
            vj = wm.transcribe_wav(wav, "en", "Bforartists")
            self.assertEqual(vj["segments"][0]["text"], " Hi")
            self.assertEqual(vj["argv"][-4:], ["8", "-nfa", "--dtw", "base"])
            wm.stop_server()
            self.assertEqual(wm.running_port(), 0)
            self.assertFalse(wm.health(port))
            self.assertNotIn(pid, [e["pid"] for e in pg._load()])

    def test_port_taken_moves_up(self):
        if wm.platform_problem():
            self.skipTest("local speech is Windows only")
        hold = socket.socket()
        hold.bind(("127.0.0.1", 0))
        hold.listen(1)
        port = hold.getsockname()[1]
        try:
            with self._install_fake():
                self.assertEqual(wm.start_server("base", "cpu", port), "")
                self.assertNotEqual(wm.get_state().port, port)
                self.assertTrue(wm.wait_ready(30))
                prefs = types.SimpleNamespace(port_offset=0, whisper_port=port)
                self.assertEqual(shared.effective_whisper_port(prefs), wm.running_port())
        finally:
            hold.close()

    def test_exit_during_start_shows_the_log(self):
        if wm.platform_problem():
            self.skipTest("local speech is Windows only")
        exe = wm.build_dir("cpu") / "Release" / "whisper-server.exe"
        exe.parent.mkdir(parents=True)
        exe.write_bytes(b"MZ")
        with open(wm.model_file("base"), "wb") as fh:
            fh.truncate(wm.get_preset("base").size)
        cmd = lambda *a: [sys.executable, "-c", "print('error: failed to load model'); raise SystemExit(3)"]  # noqa: E731
        with mock.patch.object(wm, "server_command", cmd):
            self.assertEqual(wm.start_server("base", "cpu", _free_port()), "")
            self.assertFalse(wm.wait_ready(30))
        st = wm.get_state()
        self.assertIn("exit code 3", st.error)
        self.assertIn("failed to load model", st.error)


class TestPorts(unittest.TestCase):

    def test_effective_whisper_port(self):
        p = types.SimpleNamespace(port_offset=0, whisper_port=0)
        self.assertEqual(shared.effective_whisper_port(p), 8481)
        p.port_offset = 3
        self.assertEqual(shared.effective_whisper_port(p), 8484)
        p.whisper_port = 9555
        self.assertEqual(shared.effective_whisper_port(p), 9555)       # an override ignores the offset
        self.assertEqual(shared.effective_whisper_port(types.SimpleNamespace()), shared.DEFAULT_WHISPER_PORT)

    def test_clear_of_the_other_ports_and_their_offsets(self):
        others = {shared.DEFAULT_BRIDGE_PORT, shared.DEFAULT_MCP_PORT, shared.DEFAULT_LLM_PORT}
        for off in range(0, 101):
            self.assertNotIn(shared.DEFAULT_WHISPER_PORT + off, {o + k for o in others for k in range(101)})

    def test_process_guard_knows_whisper_server(self):
        self.assertIn("whisper-server", pg._KIND_MARKERS["whisper-server"])
        self.assertEqual(wm.KIND, "whisper-server")


# ---------------------------------------------------------------------------
# 7. Downloads

class _Files(BaseHTTPRequestHandler):
    files: dict = {}
    slow: set = set()

    def log_message(self, *a):
        pass

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/releases":
            body = json.dumps(self.server.releases).encode()
        elif path in self.files:
            body = self.files[path]
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if path in self.slow:
            for i in range(0, len(body), 64 * 1024):
                try:
                    self.wfile.write(body[i:i + 64 * 1024])
                except OSError:
                    return
                time.sleep(0.05)
        else:
            self.wfile.write(body)


class TestDownloads(_TempData):

    def setUp(self):
        super().setUp()
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("Release/whisper-server.exe", b"MZ" * 100)
            zf.writestr("Release/ggml-cpu-x64.dll", b"d")
        self.zip = buf.getvalue()
        self.model = os.urandom(300 * 1024)
        _Files.files = {"/dl/whisper-bin-x64.zip": self.zip, "/m/ggml-tiny.bin": self.model,
                        "/m/ggml-base.bin": b"\0" * (2 << 20)}
        _Files.slow = {"/m/ggml-base.bin"}
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), _Files)
        base = "http://127.0.0.1:{:d}".format(self.srv.server_address[1])
        self.srv.releases = [
            {"tag_name": "v9.9.9", "published_at": "2026-11-02T00:00:00Z", "assets": []},
            {"tag_name": "b9999", "published_at": "2026-11-01T00:00:00Z", "prerelease": True, "assets": [
                {"name": "whisper-bin-x64.zip", "size": len(self.zip),
                 "browser_download_url": base + "/dl/whisper-bin-x64.zip"}]}]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        presets = tuple(
            wm.ModelPreset(p.identifier, p.label,
                           {"tiny": len(self.model), "base": 2 << 20}.get(p.identifier, p.size),
                           {"tiny": hashlib.sha256(self.model).hexdigest()}.get(p.identifier, ""),
                           p.dtw, p.measured, p.note)
            for p in wm.MODEL_PRESETS)
        self._patches = [mock.patch.object(wm, "RELEASES_API", base + "/releases"),
                         mock.patch.object(wm, "MODEL_URL", base + "/m/ggml-{:s}.bin"),
                         mock.patch.object(wm, "MODEL_PRESETS", presets),
                         mock.patch.object(wm, "_nvidia", [False])]
        for p in self._patches:
            p.start()

    def tearDown(self):
        wm.cancel_download()
        for p in self._patches:
            p.stop()
        self.srv.shutdown()
        self.srv.server_close()
        super().tearDown()

    def test_server_download_installs_its_own_folder(self):
        if wm.platform_problem():
            self.skipTest("local speech is Windows only")
        done = []
        self.assertTrue(wm.start_download("server", "AUTO", done_cb=done.append, wait=True))
        self.assertEqual(done, ["Installed whisper-server CPU (b9999)"])
        exe = wm.data_dir() / "cpu" / "Release" / "whisper-server.exe"
        self.assertEqual(wm.server_exe("cpu"), exe)
        self.assertEqual(wm.build_info("cpu")["tag"], "b9999")
        self.assertFalse(list((wm.data_dir() / "downloads").glob("*")))      # the zip is gone
        self.assertFalse(wm.get_state().download_active)
        done.clear()
        wm.start_download("server", "AUTO", done_cb=done.append, wait=True)
        self.assertEqual(done, ["whisper-server is already downloaded"])

    def test_model_download_with_checksum(self):
        done = []
        wm.start_download("model", "tiny", done_cb=done.append, wait=True)
        self.assertEqual(wm.model_status("tiny"), "ready")
        self.assertEqual(wm.model_file("tiny").read_bytes(), self.model)
        self.assertIn("Downloaded the Tiny model", done[0])

    def test_cancel_mid_download_leaves_nothing(self):
        done = []
        self.assertTrue(wm.start_download("model", "base", done_cb=done.append))
        self.assertFalse(wm.start_download("model", "tiny"))          # one at a time
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and wm.get_state().download_pct <= 0:
            time.sleep(0.05)
        self.assertGreater(wm.get_state().download_pct, 0)
        self.assertIn("Downloading the Base model", wm.get_state().download_text)
        wm.cancel_download()
        while time.monotonic() < deadline and not done:
            time.sleep(0.05)
        self.assertEqual(done, ["Download cancelled"])
        self.assertEqual(wm.model_status("base"), "missing")
        self.assertEqual(list(wm.models_dir().glob("*.part")), [])

    def test_disk_space_is_checked_first(self):
        usage = types.SimpleNamespace(total=1, used=1, free=1000)
        done = []
        with mock.patch.object(wm.shutil, "disk_usage", lambda _p: usage):
            wm.start_download("model", "tiny", done_cb=done.append, wait=True)
        self.assertIn("Not enough disk space", done[0])
        self.assertEqual(wm.model_status("tiny"), "missing")

    def test_damaged_download_is_refused(self):
        _Files.files["/m/ggml-tiny.bin"] = b"y" * len(self.model)
        done = []
        wm.start_download("model", "tiny", done_cb=done.append, wait=True)
        self.assertIn("checksum mismatch", done[0])
        self.assertEqual(wm.model_status("tiny"), "missing")


# ---------------------------------------------------------------------------
# 8. Remote

class _Remote(BaseHTTPRequestHandler):
    seen: list = []
    status = 200

    def log_message(self, *a):
        pass

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        _Remote.seen.append({"path": self.path, "auth": self.headers.get("Authorization"), "body": raw})
        if _Remote.status != 200:
            self.send_response(_Remote.status)
            self.end_headers()
            self.wfile.write(b'{"error": "nope"}')
            return
        body = json.dumps({"language": "english", "duration": 1.0, "text": "Hi",
                           "words": [{"word": "Hi", "start": 0.2, "end": 0.5}]}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class TestRemote(_TempData):
    """Against a fake server only: the real endpoint was not called."""

    def setUp(self):
        super().setUp()
        _Remote.seen = []
        _Remote.status = 200
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), _Remote)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = "http://127.0.0.1:{:d}".format(self.srv.server_address[1])
        self.wav = self.tmp / "a.wav"
        self.wav.write_bytes(b"RIFF" + b"\0" * 64)

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        super().tearDown()

    def test_request_and_answer(self):
        data = wm.transcribe_remote(self.wav, self.base + "/", "sk-test-123", "whisper-1", "en", "Bforartists")
        self.assertEqual(data["words"][0]["word"], "Hi")
        seen = _Remote.seen[0]
        self.assertEqual(seen["path"], "/v1/audio/transcriptions")
        self.assertEqual(seen["auth"], "Bearer sk-test-123")
        for field, value in (("model", b"whisper-1"), ("response_format", b"verbose_json"),
                             ("timestamp_granularities[]", b"word"), ("language", b"en"),
                             ("prompt", b"Bforartists")):
            self.assertIn('name="{:s}"\r\n\r\n'.format(field).encode() + value, seen["body"])
        self.assertIn(b"RIFF", seen["body"])
        self.assertEqual(wm.remote_url("https://api.example.com/v1"), "https://api.example.com/v1/audio/transcriptions")
        self.assertNotIn(("language", "auto"), wm.remote_fields("whisper-1", "auto"))

    def test_provider_without_the_endpoint(self):
        _Remote.status = 404
        with self.assertRaises(wm.RemoteUnsupported) as cm:
            wm.transcribe_remote(self.wav, self.base, "sk-secret-key", "whisper-1")
        self.assertIn("/v1/audio/transcriptions", str(cm.exception))
        self.assertNotIn("sk-secret-key", str(cm.exception))
        _Remote.status = 401
        with self.assertRaises(RuntimeError) as cm:
            wm.transcribe_remote(self.wav, self.base, "sk-secret-key", "whisper-1")
        self.assertNotIn("sk-secret-key", str(cm.exception))

    def test_limits(self):
        with self.assertRaises(RuntimeError):
            wm.transcribe_remote(self.wav, self.base, "", "whisper-1")
        with mock.patch.object(wm, "REMOTE_MAX_BYTES", 10):
            with self.assertRaises(RuntimeError) as cm:
                wm.transcribe_remote(self.wav, self.base, "k", "whisper-1")
            self.assertIn("25 MB", str(cm.exception))
        self.assertEqual(_Remote.seen, [])


# ---------------------------------------------------------------------------
# 9. The tool

class TestLongJobHandOff(unittest.TestCase):

    def test_the_bridge_keeps_the_checker_attributes(self):
        # The bridge wraps a deferred checker to run it as the calling thread (#357); the
        # wrapper must keep job_kind / job_label / job_on_finish, or a long transcription
        # (or render) is never handed to long_jobs after 45 s (found live in #375).
        src = (_ADDON / "mcp_to_blender_server.py").read_text(encoding="utf-8")
        start = src.index("def _for_thread(")
        body = src[start:src.index("return run", start)]
        self.assertIn("@functools.wraps(fn)", body)


class TestTool(_TempData):

    TIMING = {"content_start": 100.0, "left": 110.0, "right": 160.0, "fps": 24.0}

    def test_frames_for_the_strip(self):
        out = speech.shape_result(_doc(), self.TIMING, "talk", cached=True, path="x.json")
        # 0.5 s -> frame 112 (visible); 0.9 s -> 121.6; 2.5 s -> 160 (right handle: not visible)
        self.assertEqual([w[0] for w in out["words"]], ["Hello,", "world."])
        self.assertEqual(out["columns"], ["w", "start", "end", "frame_start", "frame_end"])
        self.assertEqual(out["words"][0], ["Hello,", 0.5, 0.9, 112, 122])
        self.assertLess(len(json.dumps(out["words"][0])), 40)          # short rows, not dicts
        self.assertEqual((out["word_count"], out["words_in_media"], out["cached"]), (2, 3, True))
        self.assertNotIn("next_offset", out)
        self.assertIn("from the cache", out["message"])

    def test_paging_and_plain_text(self):
        words = [{"w": "w{:d}".format(i), "start": i * 0.1, "end": i * 0.1 + 0.1} for i in range(30)]
        timing = {"content_start": 1.0, "left": 1.0, "right": 1000.0, "fps": 24.0}
        out = speech.shape_result(_doc(words), timing, "talk", limit=10, offset=0)
        self.assertEqual((len(out["words"]), out["next_offset"]), (10, 10))
        out = speech.shape_result(_doc(words), timing, "talk", limit=10, offset=25)
        self.assertEqual(([w[0] for w in out["words"]], out.get("next_offset")), (["w25", "w26", "w27", "w28", "w29"], None))
        out = speech.shape_result(_doc(words), timing, "talk", limit=0)
        self.assertEqual(out["words"], [])
        self.assertNotIn("next_offset", out)
        self.assertTrue(out["text"].startswith("w0 w1 w2"))
        self.assertEqual(speech.shape_result(_doc(words), timing, "t", limit=99999)["word_count"], 30)

    def test_plan_and_setup_messages(self):
        settings = {"device": "CPU", "model": "AUTO", "language": "auto", "use_remote": False, "port": 1}
        p = speech.plan(settings, language="DE")
        self.assertEqual((p["route"], p["build"], p["model"], p["language"]), ("local", "cpu", "small", "de"))
        self.assertEqual(speech.plan(settings, model="base")["model"], "base")
        if not wm.platform_problem():
            msg = speech.setup_problem(p, settings)
            self.assertIn("isn't downloaded", msg)
            self.assertIn("Speech", msg)
            exe = wm.build_dir("cpu") / "Release" / "whisper-server.exe"
            exe.parent.mkdir(parents=True)
            exe.write_bytes(b"MZ")
            self.assertIn("small speech model isn't downloaded", speech.setup_problem(p, settings))
        remote = dict(settings, use_remote=True, remote_model="whisper-1", remote_url="", remote_key="")
        rp = speech.plan(remote)
        self.assertEqual((rp["route"], rp["cache_model"]), ("remote", "remote-whisper-1"))
        self.assertIn("Remote API URL / key", speech.setup_problem(rp, remote))
        self.assertEqual(speech.setup_problem(rp, dict(remote, remote_url="u", remote_key="k")), "")

    def test_settings_from_prefs(self):
        prefs = types.SimpleNamespace(speech_device="CUDA", speech_model="base", speech_language="fr",
                                      speech_prompt="Bforartists", speech_use_remote=False,
                                      speech_remote_model="whisper-1", remote_api_url="u", remote_api_key="k",
                                      port_offset=2, whisper_port=0)
        s = speech.settings_from_prefs(prefs)
        self.assertEqual((s["device"], s["model"], s["language"], s["port"]), ("CUDA", "base", "fr", 8483))
        self.assertEqual(speech.settings_from_prefs(None)["port"], shared.DEFAULT_WHISPER_PORT)

    def test_strip_media_errors(self):
        text = types.SimpleNamespace(type="TEXT", name="Title")
        self.assertIn("has no sound", speech.strip_media(text)[1])
        gone = types.SimpleNamespace(type="SOUND", name="voice",
                                     sound=types.SimpleNamespace(filepath=str(self.tmp / "gone.wav"), library=None))
        self.assertIn("missing", speech.strip_media(gone)[1])
        ok_path = self.tmp / "here.wav"
        ok_path.write_bytes(b"RIFF")
        movie = types.SimpleNamespace(type="MOVIE", name="clip", filepath=str(ok_path))
        self.assertEqual(speech.strip_media(movie), (os.path.normpath(str(ok_path)), ""))

    def test_strip_timing_new_and_old_names(self):
        scene = types.SimpleNamespace(render=types.SimpleNamespace(fps=30, fps_base=1.001))
        new = types.SimpleNamespace(content_start=10.0, left_handle=12, right_handle=50)
        t = speech.strip_timing(scene, new)
        self.assertEqual((t["content_start"], t["left"], t["right"]), (10.0, 12.0, 50.0))
        self.assertAlmostEqual(t["fps"], 29.97, places=2)
        old = types.SimpleNamespace(frame_start=5.0, frame_final_start=6, frame_final_end=9)
        self.assertEqual(speech.strip_timing(scene, old)["right"], 9.0)

    def test_default_page_fits_the_mcp_result_limit(self):
        # The MCP server shortens answers over 8000 characters (bounded_output.LIMIT):
        # a default page of typical words must pass whole.
        words = [{"w": "word{:d},".format(i % 10), "start": round(i * 0.61, 2), "end": round(i * 0.61 + 0.4, 2)}
                 for i in range(300)]
        timing = {"content_start": 1.0, "left": 1.0, "right": 100000.0, "fps": 24.0}
        out = speech.shape_result(_doc(words), timing, "talk_sound", cached=True,
                                  path="C:/Users/someone/projects/edit/coworker_transcripts/0123456789abcdef.small.en.json")
        self.assertEqual(len(out["words"]), 150)
        self.assertLess(len(json.dumps(out, ensure_ascii=False)), 8000)     # as bounded_output.shorten measures

    def test_toolcode_params_and_result(self):
        tc = _toolcode()
        self.assertEqual(tc.Params(strip="voice"), ("voice", "", 150, 0, "", ""))
        out = speech.shape_result(_doc(), self.TIMING, "talk", limit=1)
        self.assertEqual(set(out) - set(tc.Result._fields), set())         # nothing dropped
        self.assertEqual(set({"status": 1, "message": 1}) - set(tc.Result._fields), set())


if __name__ == "__main__":
    unittest.main()
