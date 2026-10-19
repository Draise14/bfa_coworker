# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
A fake ACP agent that replays a recorded session (Tier 4h, "Agent 2" tests).

Usage::

    python tests/fake_acp_agent.py <fixture.jsonl> [--exit-after N]

The fixture (written by ``_misc/acp_record.py``) is replayed strictly in
order: for each recorded client->agent message the fake reads the next line
the client sends (and checks it is the same method, or a response); for each
recorded agent->client message it writes that message.  Response ids are
remapped to the ids the live client used, so any ACP client can talk to it.

``--exit-after N`` exits with code 3 after writing N agent messages, to
simulate a crash mid-turn (a line is written to stderr first).
"""

__all__ = ()

import json
import sys


def main() -> int:
    args = sys.argv[1:]
    exit_after = -1
    if "--exit-after" in args:
        i = args.index("--exit-after")
        exit_after = int(args[i + 1])
        del args[i:i + 2]
    with open(args[0], encoding="utf-8") as fh:
        records = [json.loads(line) for line in fh if line.strip()]

    id_map: dict = {}  # recorded request id -> live request id
    written = 0
    for rec in records:
        msg = rec["msg"]
        if rec["dir"] == "client->agent":
            line = sys.stdin.readline()
            if not line:
                return 0  # client closed stdin
            live = json.loads(line)
            want = msg.get("method")
            if want is not None and live.get("method") != want:
                sys.stderr.write("fake agent: expected {} got {}\n".format(want, live.get("method")))
                sys.stderr.flush()
                return 2
            if want is not None and "id" in msg:
                id_map[msg["id"]] = live.get("id")
            continue
        out = dict(msg)
        if "id" in out and "method" not in out and out["id"] in id_map:
            out["id"] = id_map[out["id"]]  # a response to the live client's request
        sys.stdout.write(json.dumps(out) + "\n")
        sys.stdout.flush()
        written += 1
        if exit_after >= 0 and written >= exit_after:
            sys.stderr.write("fake agent: simulated crash\n")
            sys.stderr.flush()
            return 3
    # Recording finished: stay alive until the client closes stdin.
    for _ in sys.stdin:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
