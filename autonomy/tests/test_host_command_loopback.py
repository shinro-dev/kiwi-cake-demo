#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# autonomy/tests/test_host_command_loopback.py: the host command loopback block of bin/pi/lekiwi_host_safe_attach.py,
# RUN with real pyzmq. The shape and the pins are test_host_safe_attach_static.py's; this proves the behaviour.
#
# HOW, WITHOUT A ROBOT. Each case runs in a fresh interpreter whose sys.path starts with a scratch directory holding a
# stub `lerobot.robots.lekiwi` package: LeKiwi is an empty class and LeKiwiHost's constructor is the STOCK one line for
# line (lerobot b4e2d0b6, lekiwi_host.py:40-52: a PULL command socket with CONFLATE 1 bound on tcp://*:<cmd port>, a PUSH
# observation socket with CONFLATE 1 bound on tcp://*:<observation port>). The wrapper is then loaded as a module (never
# as __main__, so main() never runs) and a host is constructed on free ports. No lerobot, no bus, no device.
# What is checked, from the kernel's own table (/proc/net/tcp) rather than from zmq's answer alone: the command port
# listens on 127.0.0.1 and on no other address; the observation port keeps the stock binding; a command pushed to the
# loopback endpoint arrives on the socket the host reads; and every refusal ends the process before a host exists.
import os
import socket
import subprocess
import sys
import tempfile
import textwrap
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WRAPPER = os.path.join(ROOT, "bin", "pi", "lekiwi_host_safe_attach.py")

STUB_LEKIWI = "class LeKiwi:\n    pass\n"
STUB_HOST = textwrap.dedent('''
    import zmq


    def main(*args, **kwargs):
        raise AssertionError("main() must never run in this test")


    class LeKiwiHost:
        def __init__(self, config):
            self.zmq_context = zmq.Context()
            self.zmq_cmd_socket = self.zmq_context.socket(zmq.PULL)
            self.zmq_cmd_socket.setsockopt(zmq.CONFLATE, 1)
            self.zmq_cmd_socket.bind(f"tcp://*:{config.port_zmq_cmd}")

            self.zmq_observation_socket = self.zmq_context.socket(zmq.PUSH)
            self.zmq_observation_socket.setsockopt(zmq.CONFLATE, 1)
            self.zmq_observation_socket.bind(f"tcp://*:{config.port_zmq_observations}")

            self.connection_time_s = config.connection_time_s
            self.watchdog_timeout_ms = config.watchdog_timeout_ms
            self.max_loop_freq_hz = config.max_loop_freq_hz
''')

# The case body: load the wrapper, construct a host, report what the kernel says is listening.
CASE = textwrap.dedent('''
    import importlib.util, json, sys, time, types
    import zmq

    spec = importlib.util.spec_from_file_location("lekiwi_host_safe_attach", sys.argv[1])
    wrapper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(wrapper)
    from lerobot.robots.lekiwi.lekiwi_host import LeKiwiHost

    cmd, obs = int(sys.argv[2]), int(sys.argv[3])
    config = types.SimpleNamespace(port_zmq_cmd=cmd, port_zmq_observations=obs, connection_time_s=1, watchdog_timeout_ms=500, max_loop_freq_hz=30)

    def listeners(port):
        out = []
        for table in ("/proc/net/tcp", "/proc/net/tcp6"):
            try:
                rows = open(table).read().splitlines()[1:]
            except OSError:
                continue
            for row in rows:
                fields = row.split()
                address, hexport = fields[1].rsplit(":", 1)
                if int(hexport, 16) == port and fields[3] == "0A":  # 0A = LISTEN
                    out.append((table.rsplit("/", 1)[1], address))
        return out

    host = LeKiwiHost(config)
    report = {"cmd_endpoint": host.zmq_cmd_socket.getsockopt_string(zmq.LAST_ENDPOINT),
              "obs_endpoint": host.zmq_observation_socket.getsockopt_string(zmq.LAST_ENDPOINT),
              "cmd_listeners": listeners(cmd), "obs_listeners": listeners(obs)}
    push = host.zmq_context.socket(zmq.PUSH)
    push.connect(f"tcp://127.0.0.1:{cmd}")
    push.send_string('{"x.vel": 0.0}')
    deadline = time.monotonic() + 3.0
    received = None
    while received is None and time.monotonic() < deadline:
        try:
            received = host.zmq_cmd_socket.recv_string(zmq.NOBLOCK)
        except zmq.Again:
            time.sleep(0.02)
    report["received_on_loopback"] = received
    print(json.dumps(report))
''')


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def have_zmq():
    try:
        import importlib.util

        return importlib.util.find_spec("zmq") is not None
    except Exception:
        return False


@unittest.skipUnless(have_zmq(), "pyzmq is not installed")
@unittest.skipUnless(os.path.exists("/proc/net/tcp"), "no /proc/net/tcp to read the listeners from")
class CommandLoopback(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        package = os.path.join(self.scratch.name, "lerobot", "robots", "lekiwi")
        os.makedirs(package)
        for directory in ("lerobot", os.path.join("lerobot", "robots"), os.path.join("lerobot", "robots", "lekiwi")):
            open(os.path.join(self.scratch.name, directory, "__init__.py"), "w").close()
        with open(os.path.join(package, "lekiwi.py"), "w") as handle:
            handle.write(STUB_LEKIWI)
        with open(os.path.join(package, "lekiwi_host.py"), "w") as handle:
            handle.write(STUB_HOST)
        self.case = os.path.join(self.scratch.name, "case.py")
        with open(self.case, "w") as handle:
            handle.write(CASE)

    def tearDown(self):
        self.scratch.cleanup()

    def run_case(self, env_value, cmd, obs, extra=""):
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PYTHONPATH": self.scratch.name}
        if env_value is not None:
            env["KC_HOST_COMMAND_LOOPBACK"] = env_value
        if extra:
            with open(self.case) as handle:
                body = handle.read()
            with open(self.case, "w") as handle:
                handle.write(body.replace("host = LeKiwiHost(config)", extra + "\nhost = LeKiwiHost(config)"))
        return subprocess.run([sys.executable, self.case, WRAPPER, str(cmd), str(obs)], env=env, capture_output=True, text=True, timeout=30)

    def report(self, done):
        self.assertEqual(done.returncode, 0, done.stderr)
        import json

        report = json.loads(done.stdout.strip().splitlines()[-1])
        for key in ("cmd_listeners", "obs_listeners"):
            report[key] = [tuple(entry) for entry in report[key]]
        return report

    def test_set_the_command_socket_listens_on_loopback_only_and_still_receives(self):
        cmd, obs = free_port(), free_port()
        done = self.run_case("1", cmd, obs)
        report = self.report(done)
        self.assertEqual(report["cmd_endpoint"], f"tcp://127.0.0.1:{cmd}")
        self.assertEqual(report["cmd_listeners"], [("tcp", "0100007F")], "the kernel lists the command port on 127.0.0.1 and on nothing else")
        self.assertEqual(report["received_on_loopback"], '{"x.vel": 0.0}', "a command pushed to the loopback endpoint reaches the socket the host reads")
        self.assertIn(f"HOST COMMAND SOCKET: bound to tcp://127.0.0.1:{cmd} only (loopback)", done.stderr)

    def test_set_the_observation_socket_keeps_the_stock_binding(self):
        cmd, obs = free_port(), free_port()
        report = self.report(self.run_case("1", cmd, obs))
        self.assertEqual(report["obs_endpoint"], f"tcp://0.0.0.0:{obs}")
        self.assertIn(("tcp", "00000000"), report["obs_listeners"])

    def test_unset_or_any_other_value_leaves_the_stock_binding_on_every_interface(self):
        for value in (None, "0", "yes", "1 "):
            cmd, obs = free_port(), free_port()
            done = self.run_case(value, cmd, obs)
            report = self.report(done)
            self.assertEqual(report["cmd_endpoint"], f"tcp://0.0.0.0:{cmd}", value)
            self.assertIn(("tcp", "00000000"), report["cmd_listeners"], value)
            self.assertNotIn("HOST COMMAND SOCKET", done.stderr, value)

    def test_a_loopback_port_already_taken_stops_the_process_before_a_host_exists(self):
        cmd, obs = free_port(), free_port()
        blocker = socket.socket()
        blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
        blocker.bind(("127.0.0.1", cmd))
        blocker.listen(1)
        try:
            done = self.run_case("1", cmd, obs)
        finally:
            blocker.close()
        # zmq's own bind of tcp://*:<cmd> may already refuse (the stock constructor raises), or the loopback rebind does:
        # either way the process ends without a host and prints no success line.
        self.assertNotEqual(done.returncode, 0, done.stdout)
        self.assertNotIn("HOST COMMAND SOCKET: bound", done.stderr)

    def test_a_command_socket_already_replaced_is_refused_not_combined(self):
        cmd, obs = free_port(), free_port()
        replace = textwrap.dedent('''
            _loopback_init = LeKiwiHost.__init__
            _stock_init = wrapper._stock_host_init

            class Gate:
                pass

            def _replacing_init(self, config):
                _stock_init(self, config)
                self.zmq_cmd_socket = Gate()
            wrapper._stock_host_init = _replacing_init
            _loopback_init.__globals__["_stock_host_init"] = _replacing_init
        ''')
        done = self.run_case("1", cmd, obs, extra=replace)
        self.assertEqual(done.returncode, 1, done.stderr)
        self.assertIn("refusing to start: the command socket was already replaced", done.stderr)


if __name__ == "__main__":
    unittest.main()
