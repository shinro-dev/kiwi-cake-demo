#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# packages/demo-runtime/tests/test_rpc_mission_retry.py: the mission_retry op over the socket. It is a mutating op (a
# connection that has not said hello is refused by the transport before the daemon sees it), and RuntimeClient carries
# it with hello, naming at most the mission id the caller saw. The op's own guards are test_daemon_mission_retry.py's.
# Run directly: python3 packages/demo-runtime/tests/test_rpc_mission_retry.py
from __future__ import annotations

import json
import socket
import sys
import unittest
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from demo_runtime import daemon, rpc  # noqa: E402
from demo_runtime.rpc import ReadOnlyClient, RuntimeClient  # noqa: E402
from test_rpc import Runtime  # noqa: E402


class Recording(RuntimeClient):
    """A client whose transport records the request instead of sending it."""

    def __init__(self, *, read_only=False):
        super().__init__(Path("/nonexistent/runtime.sock"))
        self.read_only = read_only
        self.sent = []

    def _call(self, request, *, hello=False):
        self.sent.append((dict(request), hello))
        return {"ok": True, "mission_id": request.get("mission_id")}


class TheOpIsMutating(unittest.TestCase):
    def test_it_is_listed_with_the_other_mission_commands(self):
        self.assertIn("mission_retry", rpc.MUTATING_OPS)

    def test_a_connection_without_hello_is_refused_by_the_transport(self):
        runtime = Runtime(self)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as raw:
            raw.settimeout(10)
            raw.connect(str(runtime.data / daemon.SOCKET_NAME))
            lines = raw.makefile("rb")
            raw.sendall((json.dumps({"op": "mission_retry"}) + "\n").encode())
            self.assertEqual(json.loads(lines.readline()), {"ok": False, "reason_code": "hello_required"})


class TheClientMethod(unittest.TestCase):
    def test_it_says_hello_and_names_nothing_when_given_nothing(self):
        client = Recording()
        client.mission_retry()
        self.assertEqual(client.sent, [({"op": "mission_retry"}, True)])

    def test_it_carries_the_mission_id_it_is_given(self):
        client = Recording()
        client.mission_retry("mission-0123456789ab")
        self.assertEqual(client.sent, [({"op": "mission_retry", "mission_id": "mission-0123456789ab"}, True)])

    def test_a_read_only_client_refuses_before_sending(self):
        client = Recording(read_only=True)
        with self.assertRaises(ReadOnlyClient):
            client.mission_retry("mission-0123456789ab")
        self.assertEqual(client.sent, [])

    def test_over_the_socket_the_daemon_answers_it(self):
        runtime = Runtime(self)
        answer = runtime.client.mission_retry()
        self.assertIs(answer["ok"], False)
        self.assertIsInstance(answer["reason_code"], str)
        self.assertNotIn(answer["reason_code"], ("unknown_op", "hello_required"))


if __name__ == "__main__":
    unittest.main()
