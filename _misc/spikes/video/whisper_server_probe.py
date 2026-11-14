"""Video 0 spike (#374): whisper-server start-up, readiness and one request per build/model.

  python -I whisper_server_probe.py <whisper_root> <wav> <out_dir> build:model:dtwpreset ...
Starts whisper-server on 127.0.0.1:18911 with -nfa --dtw <preset>, polls GET /health,
POSTs the WAV to /inference (verbose_json, max_len=1, split_on_word=true), stops it.
"""
import json, os, subprocess, sys, time, urllib.error, urllib.request

ROOT, WAV, OUT = sys.argv[1:4]
res = {}
for cfg in sys.argv[4:]:
    build, model, dtw = cfg.split(":")
    exe = os.path.join(ROOT, build, "Release", "whisper-server.exe")
    cmd = [exe, "-m", os.path.join(ROOT, "models", "ggml-%s.bin" % model), "--port", "18911",
           "-nfa", "--dtw", dtw, "-t", "8"]
    t0 = time.perf_counter()
    p = subprocess.Popen(cmd, stdout=open(os.path.join(OUT, "server_%s_%s.log" % (build, model)), "w"),
                         stderr=subprocess.STDOUT, cwd=OUT)
    states = []
    while time.perf_counter() - t0 < 180:
        try:
            r = urllib.request.urlopen("http://127.0.0.1:18911/health", timeout=1)
            states.append(("200", round(time.perf_counter() - t0, 2), r.read().decode()))
            break
        except urllib.error.HTTPError as e:
            st = "http %d" % e.code
            if not states or states[-1][0] != st:
                states.append((st, round(time.perf_counter() - t0, 2), e.read().decode()[:80]))
        except Exception as e:
            if not states or states[-1][0] != "refused":
                states.append(("refused", round(time.perf_counter() - t0, 2), str(e)[:60]))
        time.sleep(0.05)
    times = []
    for i in range(2):
        out = subprocess.run(["curl", "-s", "-o", os.path.join(OUT, "req_%s_%s.json" % (build, model)), "-w", "%{time_total}",
                              "http://127.0.0.1:18911/inference", "-F", "file=@" + WAV,
                              "--form-string", "response_format=verbose_json", "--form-string", "max_len=1",
                              "--form-string", "split_on_word=true", "--form-string", "language=en"],
                             capture_output=True, text=True).stdout
        times.append(out)
    res[cfg] = {"health": states, "request_s": times}
    p.kill()
    p.wait()
    time.sleep(1)
print(json.dumps(res, indent=1))
