# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
The bridge reads everything that has arrived and sends answers in pieces (#547).

A tick of the bridge's timer read one 4 KB ``recv`` per client, and a partial
read did not count as work, so a 120 KB tool call took 30 ticks (30 s at the
idle interval).  Now a tick reads until the socket would block (at most a cap
per client per tick), a tick that read, sent or accepted anything brings the
next one at the active interval, and an answer goes out as the socket takes
it (``sendall`` on a non-blocking socket lost the rest of a large one).

Fake sockets, no Blender:

1. A 120 KB request in one tick; half a request counts as work; a request
   bigger than the per-tick cap over a few ticks; a flooding client can't hold
   the others.
2. Several clients at once, a client that disconnects mid-request, a request
   split across many small packets, two requests in one packet, over the size
   limit, a client that never finishes.
3. Answers: a large answer over several ticks, the per-tick send cap, a client
   gone while answering, one that never reads, a deferred answer.
4. The timer: idle stays slow when nothing happens; a chat's turn ticks fast.

Run with::

    python -m unittest tests.test_bridge_reads_547 -v
"""

__all__ = ()

import json
import os
import sys
import unittest
import unittest.mock

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from test_threads import _load  # noqa: E402

br, sbs, deferred = _load("mcp_to_blender_server", "side_by_side", "deferred_tool")

BLOCK = object()     # recv: nothing more has arrived (BlockingIOError)
EOF = b""            # recv: the peer closed


class FakeConn:
    """A non-blocking client socket.

    *incoming*: what ``recv`` returns, in order -- bytes (handed out at most *n* at a
    time), ``BLOCK`` (raises ``BlockingIOError`` once), ``EOF`` or an exception to raise.
    Once it is used up, ``recv`` would block.  *send_window*: bytes ``send`` takes per call
    (``None``: all); *sends_per_tick*: calls before it would block (``reset_tick``)."""

    def __init__(self, incoming=(), send_window=None, sends_per_tick=None, send_error=None):
        self.incoming = list(incoming)
        self.sent = bytearray()
        self.send_window = send_window
        self.sends_per_tick = sends_per_tick
        self.send_error = send_error
        self.sends_this_tick = 0
        self.closed = False
        self.recv_calls = 0

    def reset_tick(self):
        self.sends_this_tick = 0

    def recv(self, n):
        self.recv_calls += 1
        if self.closed:
            raise OSError("closed")
        if not self.incoming:
            raise BlockingIOError
        head = self.incoming[0]
        if head is BLOCK:
            self.incoming.pop(0)
            raise BlockingIOError
        if isinstance(head, BaseException):
            self.incoming.pop(0)
            raise head
        if head == EOF:
            return b""
        out, rest = head[:n], head[n:]
        if rest:
            self.incoming[0] = rest
        else:
            self.incoming.pop(0)
        return bytes(out)

    def send(self, data):
        if self.send_error is not None:
            raise self.send_error
        if self.sends_per_tick is not None and self.sends_this_tick >= self.sends_per_tick:
            raise BlockingIOError
        self.sends_this_tick += 1
        data = bytes(data)
        if self.send_window is not None:
            data = data[:self.send_window]
        self.sent.extend(data)
        return len(data)

    def sendall(self, _data):
        raise AssertionError("sendall on a non-blocking socket: the bridge must send in pieces (#547)")

    def close(self):
        self.closed = True

    def answer(self):
        assert self.sent.endswith(b"\0"), "a whole answer ends with its null byte"
        return json.loads(bytes(self.sent[:-1]).decode("utf-8"))


def request(size=0, thread="", tag=""):
    """A request of about *size* bytes (its code padded), with its null byte."""
    payload = {"type": "execute", "code": "", "strict_json": True, "thread": thread, "tag": tag}
    raw = json.dumps(payload)
    if size > len(raw):
        payload["code"] = "#" + "p" * (size - len(raw) - 1)
    return json.dumps(payload).encode("utf-8") + b"\0"


def pieces(data, size):
    return [data[i:i + size] for i in range(0, len(data), size)]


class _BridgeTest(unittest.TestCase):

    def setUp(self):
        self.ran = []
        self.answer_size = 10
        saved = list(br._state.clients), br._state.last_chat
        br._state.clients[:] = []
        br._state.last_chat = ""

        def restore():
            br._state.clients[:], br._state.last_chat = saved
        self.addCleanup(restore)

        def fake_execute(data):
            req = json.loads(data)
            self.ran.append((req.get("tag"), len(data)))
            return br._ExecResult({"status": "ok", "result": {"tag": req.get("tag"), "len": len(data),
                                                              "pad": "x" * self.answer_size}}), True
        patcher = unittest.mock.patch.object(br, "_execute_code_from_request", fake_execute)
        patcher.start()
        self.addCleanup(patcher.stop)
        saved_working = list(sbs.WORKING)
        sbs.WORKING[:] = []
        self.addCleanup(lambda: sbs.WORKING.__setitem__(slice(None), saved_working))

    def client(self, conn):
        c = br._Client(conn)
        br._state.clients.append(c)
        return c

    def tick(self, *conns):
        for conn in conns:
            conn.reset_tick()
        return br._service_clients()


class TestReads(_BridgeTest):

    def test_120_kb_request_in_one_tick(self):
        data = request(120_000, tag="big")
        conn = FakeConn(pieces(data, 4096))          # 30 packets, all arrived
        self.client(conn)
        self.assertTrue(self.tick(conn))
        self.assertEqual(self.ran, [("big", len(data) - 1)], "read whole and run in the same tick")
        self.assertEqual(conn.answer()["result"]["tag"], "big")
        self.assertTrue(conn.closed)
        self.assertEqual(br._state.clients, [])

    def test_half_a_request_counts_as_work(self):
        data = request(120_000, tag="big")
        half = len(data) // 2
        conn = FakeConn([data[:half], BLOCK, data[half:]])
        self.client(conn)
        self.assertTrue(self.tick(conn), "a tick that read something brings the next one soon")
        self.assertEqual(self.ran, [])
        self.assertFalse(conn.closed)
        self.assertTrue(self.tick(conn))
        self.assertEqual(self.ran, [("big", len(data) - 1)])

    def test_nothing_arrived_is_no_work(self):
        conn = FakeConn([])
        self.client(conn)
        self.assertFalse(self.tick(conn))
        self.assertFalse(conn.closed)

    def test_request_bigger_than_the_tick_cap(self):
        data = request(300_000, tag="huge")
        conn = FakeConn([data])
        self.client(conn)
        with unittest.mock.patch.object(br, "_RECV_MAX_BYTES_PER_TICK", 100_000):
            ticks = 0
            while not self.ran and ticks < 10:
                self.assertTrue(self.tick(conn))
                ticks += 1
        self.assertEqual(self.ran, [("huge", len(data) - 1)])
        self.assertEqual(ticks, 3, "100 KB per tick: three ticks for 300 KB")
        self.assertEqual(conn.answer()["status"], "ok")

    def test_flooding_client_does_not_hold_the_others(self):
        endless = FakeConn([b"z" * 65536] * 10_000)     # 640 MB, no null byte
        small = FakeConn([request(200, tag="small")])
        self.client(endless)
        self.client(small)
        self.assertTrue(self.tick(endless, small))
        self.assertEqual([t for t, _n in self.ran], ["small"], "the small call ran in the same tick")
        flood = br._state.clients[0]
        self.assertIs(flood.conn, endless)
        self.assertLessEqual(len(flood.buffer), br._RECV_MAX_BYTES_PER_TICK + br._RECV_BUFFER_SIZE)
        self.assertGreaterEqual(len(flood.buffer), br._RECV_MAX_BYTES_PER_TICK)

    def test_several_clients_at_once(self):
        conns = [FakeConn(pieces(request(n, thread="t%d" % i, tag=str(n)), 4096))
                 for i, n in enumerate((120_000, 18_000, 1_200))]
        for conn in conns:
            self.client(conn)
        self.assertTrue(self.tick(*conns))
        self.assertEqual(sorted(t for t, _n in self.ran), ["1200", "120000", "18000"])
        for conn, n in zip(conns, (120_000, 18_000, 1_200)):
            self.assertEqual(conn.answer()["result"]["tag"], str(n), "each answer to its own client")
            self.assertTrue(conn.closed)

    def test_several_calls_of_one_chat_take_turns(self):
        conns = [FakeConn([request(5_000, tag=str(i))]) for i in range(3)]      # one chat ("")
        for conn in conns:
            self.client(conn)
        for _ in range(3):
            self.assertTrue(self.tick(*conns))
        self.assertEqual([t for t, _n in self.ran], ["0", "1", "2"], "one call per chat per tick (#359)")

    def test_client_disconnects_mid_request(self):
        data = request(120_000, tag="cut")
        conn = FakeConn([data[:60_000], EOF])
        self.client(conn)
        self.assertTrue(self.tick(conn))
        self.assertEqual(self.ran, [])
        self.assertTrue(conn.closed)
        self.assertEqual(br._state.clients, [])
        self.assertEqual(bytes(conn.sent), b"", "no answer to a client that left")

    def test_connection_error_mid_request(self):
        conn = FakeConn([b'{"type": "exe', ConnectionResetError()])
        self.client(conn)
        self.tick(conn)
        self.assertEqual((self.ran, conn.closed, br._state.clients), ([], True, []))

    def test_request_split_across_many_small_packets(self):
        data = request(1_200, tag="split")
        incoming = []
        for i, piece in enumerate(pieces(data, 7)):
            incoming.append(piece)
            if i % 20 == 19:
                incoming.append(BLOCK)                  # the rest has not arrived yet
        conn = FakeConn(incoming)
        self.client(conn)
        ticks = 0
        while not self.ran and ticks < 50:
            self.assertTrue(self.tick(conn))
            ticks += 1
        self.assertEqual(self.ran, [("split", len(data) - 1)])
        self.assertEqual(ticks, -(-len(pieces(data, 7)) // 20), "one tick per burst that arrived")
        self.assertEqual(conn.answer()["result"]["tag"], "split")

    def test_one_byte_at_a_time(self):
        data = request(300, tag="bytes")
        conn = FakeConn(pieces(data, 1))
        self.client(conn)
        self.assertTrue(self.tick(conn))
        self.assertEqual(self.ran, [("bytes", len(data) - 1)])

    def test_two_requests_in_one_packet(self):
        first, second = request(500, tag="first"), request(500, tag="second")
        conn = FakeConn([first + second])
        self.client(conn)
        self.assertTrue(self.tick(conn))
        self.tick(conn)
        self.assertEqual(self.ran, [("first", len(first) - 1)], "one request per connection, run once")
        self.assertEqual(conn.answer()["result"]["tag"], "first")
        self.assertEqual(conn.sent.count(b"\0"), 1, "one answer")
        self.assertTrue(conn.closed)

    def test_reading_stops_at_the_null_byte(self):
        first = request(500, tag="first")
        conn = FakeConn([first, b"more that is not read"])
        self.client(conn)
        self.tick(conn)
        self.assertEqual(conn.incoming, [b"more that is not read"])

    def test_over_the_size_limit(self):
        conn = FakeConn([b"x" * 65536] * 4)
        self.client(conn)
        with unittest.mock.patch.object(br, "_MAX_REQUEST_BYTES", 100_000):
            self.tick(conn)
        self.assertEqual(self.ran, [])
        self.assertIn("byte limit", conn.answer()["message"])
        self.assertTrue(conn.closed)

    def test_a_client_that_never_finishes_times_out(self):
        conn = FakeConn([b'{"type": "execute"'])
        client = self.client(conn)
        for _ in range(client.timeout + 1):
            self.tick(conn)
        self.assertEqual(conn.answer()["message"], "Client timed out")
        self.assertTrue(conn.closed)
        self.assertEqual(br._state.clients, [])


class TestAnswers(_BridgeTest):

    def test_large_answer_over_several_ticks(self):
        self.answer_size = 1_000_000
        conn = FakeConn([request(300, tag="img")], send_window=65536, sends_per_tick=3)
        self.client(conn)
        ticks = 0
        while not conn.closed and ticks < 50:
            self.assertTrue(self.tick(conn), "a tick that sent something brings the next one soon")
            ticks += 1
        answer = conn.answer()
        self.assertEqual(len(answer["result"]["pad"]), 1_000_000, "the whole answer arrived")
        self.assertGreater(ticks, 3)
        self.assertEqual(br._state.clients, [])
        self.assertEqual(len(self.ran), 1, "run once")

    def test_send_cap_per_tick(self):
        self.answer_size = 300_000
        conn = FakeConn([request(300, tag="big answer")])
        self.client(conn)
        with unittest.mock.patch.object(br, "_SEND_MAX_BYTES_PER_TICK", 100_000):
            self.tick(conn)
            self.assertLessEqual(len(conn.sent), 100_000)
            self.assertFalse(conn.closed)
            for _ in range(5):
                self.tick(conn)
        self.assertEqual(len(conn.answer()["result"]["pad"]), 300_000)
        self.assertTrue(conn.closed)

    def test_client_gone_while_answering(self):
        self.answer_size = 1_000_000
        conn = FakeConn([request(300)], send_window=65536, sends_per_tick=1)
        self.client(conn)
        self.tick(conn)
        self.assertFalse(conn.closed)
        conn.send_error = ConnectionResetError()
        self.tick(conn)
        self.assertTrue(conn.closed)
        self.assertEqual(br._state.clients, [])

    def test_client_that_never_reads_its_answer(self):
        self.answer_size = 1_000_000
        conn = FakeConn([request(300)], send_window=65536, sends_per_tick=1)
        client = self.client(conn)
        self.tick(conn)
        conn.sends_per_tick = 0                        # its receive buffer is full from now on
        busy = [self.tick(conn) for _ in range(client.timeout + 1)]
        self.assertTrue(all(busy[:-1]), "still answering: the next tick soon")
        self.assertTrue(conn.closed, "given up after the time-out")
        self.assertEqual(br._state.clients, [])

    def test_answer_progress_resets_the_time_out(self):
        conn = FakeConn([request(300)], send_window=1024, sends_per_tick=1)
        client = self.client(conn)
        limit = client.timeout
        self.answer_size = limit * 2 * 1024             # twice as many ticks as the time-out
        ticks = 0
        while not conn.closed and ticks < limit * 4:   # slow, but it reads: not evicted
            self.tick(conn)
            ticks += 1
        self.assertGreater(ticks, limit)
        self.assertEqual(len(conn.answer()["result"]["pad"]), self.answer_size, "the whole answer")

    def test_deferred_answer_goes_through_the_bridge(self):
        conn = FakeConn([], send_window=65536, sends_per_tick=2)
        big = {"status": "ok", "result": {"image": "i" * 500_000}}
        br.send_answer(conn, big)
        self.assertFalse(conn.closed)
        self.assertEqual(len(br._state.clients), 1, "the rest goes at the next ticks")
        for _ in range(20):
            if conn.closed:
                break
            self.assertTrue(self.tick(conn))
        self.assertEqual(conn.answer(), big)
        self.assertEqual(br._state.clients, [])
        self.assertEqual(self.ran, [], "an answer, not a request")

    def test_small_deferred_answer_at_once(self):
        conn = FakeConn([])
        br.send_answer(conn, {"status": "ok"})
        self.assertEqual(conn.answer(), {"status": "ok"})
        self.assertTrue(conn.closed)
        self.assertEqual(br._state.clients, [])

    def test_deferred_tool_sends_with_the_bridge(self):
        conn = FakeConn([], send_window=4096, sends_per_tick=1)
        seen = []
        dc = deferred._DeferredClient(conn, lambda: None, True, "", "", on_answer=seen.append)
        deferred._deferred_clients.append(dc)
        self.addCleanup(deferred._deferred_clients.clear)
        deferred._send_and_close(dc, {"status": "ok", "result": "r" * 20_000})
        self.assertEqual(len(seen), 1)
        self.assertNotIn(dc, deferred._deferred_clients)
        for _ in range(20):
            if conn.closed:
                break
            self.tick(conn)
        self.assertEqual(conn.answer()["result"], "r" * 20_000)


class _Listener:
    def __init__(self, conns):
        self.conns = list(conns)

    def accept(self):
        if not self.conns:
            raise BlockingIOError
        return self.conns.pop(0), ("127.0.0.1", 1)


class _AcceptedConn(FakeConn):
    def setblocking(self, _flag):
        pass


class TestTimer(_BridgeTest):

    def setUp(self):
        super().setUp()
        t = br._timer
        saved = (t.interval_active, t.interval_idle, t.interval_idle_delay)
        self.addCleanup(lambda: br.timer_internal_vars_calc(*saved))
        br.timer_internal_vars_calc(active=0.25, idle=1.0, idle_delay=5.0)     # the preferences' defaults

    def _run_down(self):
        while br._timer.idle_countdown > 0:
            br.timer_idle_interval()

    def test_idle_stays_slow(self):
        self._run_down()
        self.assertFalse(self.tick())
        self.assertEqual(br.timer_idle_interval(), 1.0)

    def test_a_chat_turn_ticks_fast(self):
        self._run_down()
        sbs.WORKING[:] = [lambda: [""]]                 # the default chat's turn runs
        self.assertEqual(br.timer_idle_interval(), br._TIMER_INTERVAL_TURN)
        br.timer_internal_vars_calc(active=0.02)
        self.assertEqual(br.timer_idle_interval(), 0.02, "never slower than the active interval")
        sbs.WORKING[:] = []
        br.timer_internal_vars_calc(active=0.25)
        self._run_down()
        self.assertEqual(br.timer_idle_interval(), 1.0, "the turn ended: idle again")

    def test_accept_counts_as_work(self):
        conn = _AcceptedConn([])
        saved = br._state.sock
        br._state.sock = _Listener([conn])
        try:
            self.assertTrue(br._accept_clients())
            self.assertFalse(br._accept_clients())
        finally:
            br._state.sock = saved
        self.assertEqual([c.conn for c in br._state.clients], [conn])


if __name__ == "__main__":
    unittest.main()
