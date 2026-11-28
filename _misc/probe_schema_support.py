# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Probe (#612): does a running llama-server (or any chat endpoint) enforce a JSON schema?

Usage (stdlib only; it sends four tiny requests to a server YOU already started)::

    python _misc/probe_schema_support.py --port 8081
    python _misc/probe_schema_support.py --url https://host/v1/chat/completions --key KEY --model M

For each of three prompts that tempt a model to answer in prose it sends the request with
``response_format: json_schema`` and reports: HTTP status, whether the reply parses, and
whether it matches the schema.  A server that enforces the schema gives 3 of 3 even for
the prompt that asks for a poem.  It also sends one request with a ``response_format`` the
server cannot know, to show how a refusing server answers (the add-on then asks again
without the field).  Paste the output into #612.
"""

__all__ = ()

import argparse
import importlib
import json
import sys
import types
import urllib.error
import urllib.request
from pathlib import Path


def _thinking_off():
    """The add-on's own thinking-off request fields (``llm_transport.thinking_fields(0)``)."""
    name = "bfacw_probe_pkg"
    pkg = types.ModuleType(name)
    pkg.__path__ = [str(Path(__file__).resolve().parents[1] / "addon" / "bfa_coworker")]
    sys.modules[name] = pkg
    return importlib.import_module(name + ".llm_transport").thinking_fields(0)

SCHEMA = {"type": "object", "properties": {"first": {"type": "integer", "minimum": 1},
                                           "last": {"type": "integer", "minimum": 1}},
          "required": ["first", "last"], "additionalProperties": False}
PROMPTS = ("Name the sentence range of the best moment: from sentence 4 to sentence 9.",
           "Write a two-line poem about the sea.",
           "Explain in prose why the sky is blue. Do not use JSON.")


def post(url, key, model, body):
    if model:
        body["model"] = model
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST", headers={
        "Content-Type": "application/json", **({"Authorization": "Bearer " + key} if key else {})})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as ex:
        return ex.code, ex.read().decode("utf-8", "replace")[:300]
    except (urllib.error.URLError, OSError) as ex:
        sys.exit("Could not reach {} ({}). Is the server started, and is the port right?".format(url, ex))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--url", default="")
    ap.add_argument("--key", default="")
    ap.add_argument("--model", default="")
    a = ap.parse_args()
    url = a.url or "http://127.0.0.1:{:d}/v1/chat/completions".format(a.port)
    if not a.url and not a.port:
        sys.exit("give --port or --url")
    good = 0
    local = not a.url or "127.0.0.1" in a.url or "localhost" in a.url
    extra = _thinking_off() if local else {}            # what the add-on sends
    only = ("\n\nAnswer with JSON only, no other text, matching this JSON Schema:\n"
            + json.dumps(SCHEMA, separators=(",", ":")))
    for prompt in PROMPTS:
        status, resp = post(url, a.key, a.model, {
            **extra,
            "messages": [{"role": "user", "content": prompt + only}], "max_tokens": 128, "temperature": 0,
            "response_format": {"type": "json_schema",
                                "json_schema": {"name": "range", "strict": True, "schema": SCHEMA}}})
        ok = False
        text = ""
        if status == 200:
            text = resp["choices"][0]["message"].get("content") or ""
            try:
                v = json.loads(text)
                ok = (isinstance(v, dict) and set(v) == {"first", "last"}
                      and all(isinstance(v[k], int) and v[k] >= 1 for k in v))
            except ValueError:
                pass
        good += ok
        print("HTTP {} schema-valid={} reply={!r}".format(status, ok, text[:120] if status == 200 else resp))
    status, resp = post(url, a.key, a.model, {"messages": [{"role": "user", "content": "hi"}], "max_tokens": 8,
                                              "response_format": {"type": "no_such_format"}})
    print("unknown response_format ->", status, str(resp)[:150])
    print("enforced" if good == len(PROMPTS) else "NOT enforced ({}/{})".format(good, len(PROMPTS)))


if __name__ == "__main__":
    main()
