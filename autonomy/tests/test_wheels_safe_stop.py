#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# autonomy/tests/test_wheels_safe_stop.py: the wheel stop of the post-exit safe-stop command, offline.
#
# bin/pi/wheels_safe_stop.py runs against a FAKE feetech SDK (FAKE_SDK below): nine simulated STS servos on one bus, ids 1
# to 6 the arm and 7 to 9 the wheels, which records every packet it is handed and answers from its simulated registers.
# bin/templates/safe-stop.sh.in is rendered the way bin/pi/lekiwi-deploy.sh install renders it and RUN the way the Cake
# supervisor runs a safe-stop command, measured: argv [safe-stop.sh, LOG], environment exactly PATH=/usr/bin:/bin, a
# separate working directory. Its interpreter is a small wrapper that puts the fake SDK first on the import path.
#
# No serial device is ever opened here. The servo "device" is the slave side of a pseudo-terminal (a character device that
# nothing holds open), and the fake SDK never opens it either; a missing device is a path that does not exist, and a busy
# one is that pseudo-terminal held open by a second process. bin/templates/run-host.sh.in is rendered beside it with this
# machine's flock and the same lock file (HostStartLock), and run as Cake runs a child, with stub fuser and ss and a
# stand-in host: nothing it starts is a host.
# Run directly: python3 autonomy/tests/test_wheels_safe_stop.py
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HELPER = os.path.join(ROOT, "bin", "pi", "wheels_safe_stop.py")
TEMPLATE = os.path.join(ROOT, "bin", "templates", "safe-stop.sh.in")
HOST_TEMPLATE = os.path.join(ROOT, "bin", "templates", "run-host.sh.in")
CAKE_ENV = {"PATH": "/usr/bin:/bin"}
CAKE_POSITIONALS = ["3", "0", "900", "kc-lekiwi-run-tag"]
FLOCK = shutil.which("flock", path=CAKE_ENV["PATH"])
ARM_IDS = {1, 2, 3, 4, 5, 6}
WHEEL_IDS = {7, 8, 9}
SUPERVISOR_DEADLINE_S = 0.5  # the supervisor ends a safe-stop still running about 500 ms after its start (measured)

FAKE_SDK = r'''"""scservo_sdk stand-in for autonomy/tests/test_wheels_safe_stop.py.

Nine simulated STS servos on one bus: ids 1 to 6 the arm (position mode, torque on), 7 to 9 the wheels (velocity mode,
torque on, turning at the configured velocity). Every packet it is handed is recorded; nothing is ever opened. Configured
by configure() inside the test process, or, imported by a helper process, from the JSON file named by FAKE_SCS_CONFIG; a
helper process records one JSON line per packet into the file named by FAKE_SCS_RECORD, flushed at once, so a killed
helper leaves behind exactly what it sent.
"""
import json
import os
import time

COMM_SUCCESS = 0
COMM_PORT_BUSY = -1
COMM_TX_FAIL = -2
COMM_RX_FAIL = -3
COMM_TX_ERROR = -4
COMM_RX_WAITING = -5
COMM_RX_TIMEOUT = -6
COMM_RX_CORRUPT = -7
COMM_NOT_AVAILABLE = -9
BROADCAST_ID = 0xFE
ARM_IDS = (1, 2, 3, 4, 5, 6)
WHEEL_IDS = (7, 8, 9)


def _raw_velocity(value):
    return (abs(value) & 0x7FFF) | (0x8000 if value < 0 else 0)


def _locked(path):
    """Whether a fresh open of PATH finds an exclusive flock on it (one this process holds through another open counts)."""
    import fcntl
    fd = os.open(path, os.O_RDONLY | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    finally:
        os.close(fd)
    return False


class Bus:
    def __init__(self, config=None, record_path=None):
        self.config = dict(config or {})
        self.record_path = record_path
        self.records = []
        self.regs = {}
        for sid in ARM_IDS:
            self.regs[sid] = {33: 0, 40: 1, 41: 254, 42: 0x00, 43: 0x08, 46: 0, 47: 0, 56: 0x00, 57: 0x08, 58: 0, 59: 0}
        raw = _raw_velocity(int(self.config.get("wheel_velocity", 300)))
        for sid in WHEEL_IDS:
            self.regs[sid] = {33: 1, 40: 1, 41: 254, 46: raw & 0xFF, 47: raw >> 8, 58: raw & 0xFF, 59: raw >> 8}
        for sid, value in self.config.get("torque", {}).items():
            self.regs[int(sid)][40] = int(value)
        for sid, value in self.config.get("mode", {}).items():
            self.regs[int(sid)][33] = int(value)
        self.lag = {sid: 0 for sid in WHEEL_IDS}
        self.lock_seen = []  # with "lock_probe": PATH, per record: (op, whether another open of PATH found it locked)

    def record(self, entry):
        self.records.append(entry)
        if self.config.get("lock_probe"):
            self.lock_seen.append((entry["op"], _locked(self.config["lock_probe"])))
        if self.record_path:
            with open(self.record_path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry) + "\n")
                handle.flush()

    def dead(self, sid):
        return sid in self.config.get("dead_ids", ())

    def exchange(self, sid):
        """One request and its reply: a silent servo costs the configured timeout, a live one the configured delay."""
        wait = float(self.config.get("timeout_s", 0.05)) if self.dead(sid) else float(self.config.get("reply_delay_s", 0.0))
        if wait:
            time.sleep(wait)
        return not self.dead(sid)

    def apply(self, sid, address, data):
        if self.dead(sid):
            return
        if address == 46 and sid in WHEEL_IDS and self.config.get("goal_write_energizes"):
            self.regs[sid][40] = 1  # firmware that switches torque on when a goal is written (open on the real servos)
        if address == 46 and sid in self.config.get("sticky_goal_ids", ()):
            return  # acknowledges the write and keeps its old goal
        if address == 40 and sid in self.config.get("sticky_torque_ids", ()):
            return  # acknowledges the write and keeps its torque on
        for offset, byte in enumerate(data):
            self.regs[sid][address + offset] = byte & 0xFF
        if address == 46 and sid in WHEEL_IDS:
            self.lag[sid] = int(self.config.get("present_lag_reads", 0))
            if self.lag[sid] == 0:
                self.regs[sid][58], self.regs[sid][59] = self.regs[sid][46], self.regs[sid][47]

    def present(self, sid):
        if self.lag.get(sid):
            self.lag[sid] -= 1
            if self.lag[sid] == 0:
                self.regs[sid][58], self.regs[sid][59] = self.regs[sid][46], self.regs[sid][47]


bus = None


def configure(config=None, record_path=None):
    global bus
    bus = Bus(config, record_path)
    return bus


def _bus():
    global bus
    if bus is None:
        config = {}
        if os.environ.get("FAKE_SCS_CONFIG"):
            with open(os.environ["FAKE_SCS_CONFIG"], encoding="utf-8") as handle:
                config = json.load(handle)
        bus = Bus(config, os.environ.get("FAKE_SCS_RECORD"))
    return bus


if os.environ.get("FAKE_SCS_CONFIG") and _bus().config.get("import_error"):
    raise ImportError("stands in for an interpreter without the feetech SDK")


class PortHandler:
    def __init__(self, port_name):
        self.port_name = port_name
        self.is_open = False
        self.is_using = False
        self.baudrate = 1000000
        self.tx_time_per_byte = 0.01
        self.packet_start_time = 0.0
        self.packet_timeout = 0.0
        _bus().record({"op": "port", "port": port_name})

    def setBaudRate(self, baudrate):
        b = _bus()
        if b.config.get("hang_open_s"):
            time.sleep(float(b.config["hang_open_s"]))
        if b.config.get("open_fails"):
            raise OSError(5, "Input/output error")
        self.baudrate = baudrate
        self.is_open = True
        b.record({"op": "open", "baud": baudrate})
        return True

    def openPort(self):
        return self.setBaudRate(self.baudrate)

    def closePort(self):
        self.is_open = False
        _bus().record({"op": "close"})

    def clearPort(self):
        pass

    def getCurrentTime(self):
        return round(time.time() * 1000000000) / 1000000.0

    def setPacketTimeout(self, packet_length):
        self.packet_start_time = self.getCurrentTime()
        self.packet_timeout = 34.0


class _Packet:
    def _write(self, port, sid, address, data):
        b = _bus()
        b.record({"op": "write", "id": sid, "addr": address, "data": list(data)})
        port.setPacketTimeout(6)
        if not b.exchange(sid):
            return COMM_RX_TIMEOUT, 0
        b.apply(sid, address, data)
        return COMM_SUCCESS, int(b.config.get("error_byte", 0))

    def write1ByteTxRx(self, port, sid, address, data):
        return self._write(port, sid, address, [data & 0xFF])

    def write2ByteTxRx(self, port, sid, address, data):
        return self._write(port, sid, address, [data & 0xFF, (data >> 8) & 0xFF])

    def _read(self, port, sid, address, length):
        b = _bus()
        b.record({"op": "read", "id": sid, "addr": address, "len": length})
        port.setPacketTimeout(length + 6)
        if not b.exchange(sid):
            return [], COMM_RX_TIMEOUT, 0
        for slow_id, slow_address, seconds in b.config.get("slow_reads", ()):
            if (slow_id, slow_address) == (sid, address):
                time.sleep(float(seconds))  # this one read answers late: [id, address, seconds]
        if [sid, address] in [list(pair) for pair in b.config.get("corrupt_reads", ())]:
            return [], COMM_RX_CORRUPT, 0  # this one read gets a reply that does not check out: [id, address]
        if address == 58:
            b.present(sid)
        return [b.regs[sid].get(address + i, 0) for i in range(length)], COMM_SUCCESS, 0

    def read1ByteTxRx(self, port, sid, address):
        data, result, error = self._read(port, sid, address, 1)
        return (data[0] if result == COMM_SUCCESS else 0), result, error

    def read2ByteTxRx(self, port, sid, address):
        data, result, error = self._read(port, sid, address, 2)
        return ((data[0] | (data[1] << 8)) if result == COMM_SUCCESS else 0), result, error

    def getTxRxResult(self, result):
        return {COMM_SUCCESS: "[TxRxResult] Communication success!", COMM_RX_TIMEOUT: "[TxRxResult] There is no status packet!"}.get(result, "")

    def getRxPacketError(self, error):
        return ""


def PacketHandler(protocol_end):
    _bus().record({"op": "protocol", "end": protocol_end})
    return _Packet()


class GroupSyncWrite:
    def __init__(self, port, ph, start_address, data_length):
        self.port = port
        self.start_address = start_address
        self.data_length = data_length
        self.params = {}

    def addParam(self, sid, data):
        if sid in self.params or len(data) > self.data_length:
            return False
        self.params[sid] = list(data)
        return True

    def clearParam(self):
        self.params.clear()

    def txPacket(self):
        b = _bus()
        b.record({"op": "sync_write", "addr": self.start_address, "len": self.data_length,
                  "params": {str(sid): data for sid, data in self.params.items()}})
        if not b.config.get("sync_lost"):  # a lost broadcast reports nothing: a sync write has no reply
            for sid, data in self.params.items():
                b.apply(sid, self.start_address, data)
        return COMM_SUCCESS
'''


def load_helper():
    spec = importlib.util.spec_from_file_location("wheels_safe_stop_under_test", HELPER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fake_sdk(config=None):
    module = types.ModuleType("fake_scservo_sdk")
    exec(compile(FAKE_SDK, "fake_scservo_sdk", "exec"), module.__dict__)
    module.configure(config or {})
    return module


def ids_named(records):
    """Every servo id a record names: the target of a read or a write, and each id a sync write carries data for."""
    named = set()
    for r in records:
        if "id" in r:
            named.add(r["id"])
        for sid in r.get("params", {}):
            named.add(int(sid))
    return named


def writes(records):
    return [(r["id"], r["addr"], r["data"]) for r in records if r["op"] == "write"]


def velocity(regs, sid, address=46):
    raw = regs[sid][address] | (regs[sid][address + 1] << 8)
    return -(raw & 0x7FFF) if raw & 0x8000 else raw & 0x7FFF


class UnwritableLog:
    """A log stream every write and flush of which fails the way a full disk fails (ENOSPC); counts the write attempts."""

    def __init__(self):
        self.attempts = 0

    def write(self, text):
        self.attempts += 1
        raise OSError(28, "No space left on device")

    def flush(self):
        raise OSError(28, "No space left on device")


class Holder:
    """A second process holding PATH open (the device is then busy), or holding an exclusive flock on PATH (the lock is
    then held), for HOLD_S seconds; started() returns once it holds it."""

    def __init__(self, path, mode, hold_s=30.0):
        code = ("import fcntl, os, sys, time\n"
                "fd = os.open(sys.argv[1], os.O_RDWR | os.O_NOCTTY | (os.O_CREAT if sys.argv[2] == 'lock' else 0), 0o600)\n"
                "if sys.argv[2] == 'lock':\n    fcntl.flock(fd, fcntl.LOCK_EX)\n"
                "print('holding', flush=True)\n"
                "time.sleep(float(sys.argv[3]))\n")
        self.process = subprocess.Popen([sys.executable, "-c", code, path, mode, str(hold_s)], stdout=subprocess.PIPE, text=True)
        line = self.process.stdout.readline()
        assert line.strip() == "holding", line

    def stop(self):
        if self.process.poll() is None:
            self.process.kill()
        self.process.wait()
        self.process.stdout.close()


class DeviceFixture(unittest.TestCase):
    """A pseudo-terminal slave as the servo device (a character device nothing holds open), a lock path, a scratch dir,
    and a stand-in /proc (proc_root) that lists this process and every holder the test starts, each by a link to its real
    /proc entry: the owner check scans it the way it scans /proc, and sees exactly those processes."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        master, slave = os.openpty()
        self.device = os.ttyname(slave)
        os.close(slave)
        self.addCleanup(os.close, master)
        self.lock = os.path.join(self.tmp.name, "wheels-safe-stop.lock")
        self.proc_root = os.path.join(self.tmp.name, "proc")
        os.mkdir(self.proc_root)
        self.list_process(os.getpid())

    def list_process(self, pid):
        os.symlink(f"/proc/{pid}", os.path.join(self.proc_root, str(pid)))

    def hold(self, path, mode, hold_s=30.0):
        holder = Holder(path, mode, hold_s)
        self.addCleanup(holder.stop)
        self.list_process(holder.process.pid)
        return holder


@unittest.skipUnless(sys.platform.startswith("linux") and os.path.isdir("/proc/self/fd"), "needs Linux /proc")
class Helper(DeviceFixture):
    """bin/pi/wheels_safe_stop.py in this process, against the fake SDK, with the test's own clock (the helper's default
    clock is its process age, which in a test runner is already far past any budget).

    Its owner check scans the stand-in /proc (proc_root) unless a test passes another: a scan of the real /proc walks
    every process of this account, which on a busy machine takes more than a hundred milliseconds, and that time would
    come out of the budget a test asserts on. The tests that name proc_root='/proc' run it against the real /proc, and so
    does every run of the rendered safe-stop (SafeStopScript)."""

    def setUp(self):
        super().setUp()
        self.helper = load_helper()

    def run_helper(self, sdk, *extra, argv=None, budget_ms=None, loader=None, **kwargs):
        stream = io.StringIO()
        if argv is None:
            argv = ["--port", self.device, "--lock", self.lock, *extra]
            if budget_ms is not None:
                argv += ["--budget-ms", str(budget_ms)]
        kwargs.setdefault("proc_root", self.proc_root)
        start = time.monotonic()
        rc = self.helper.main(argv, sdk_loader=loader or (lambda: sdk), clock=lambda: time.monotonic() - start,
                              stream=stream, **kwargs)
        return rc, stream.getvalue(), time.monotonic() - start

    def test_a_healthy_run_writes_goal_velocity_zero_to_the_three_wheels_and_nothing_else(self):
        sdk = fake_sdk()
        arm_before = {sid: dict(sdk.bus.regs[sid]) for sid in ARM_IDS}
        rc, out, _ = self.run_helper(sdk)
        self.assertEqual(rc, 0, out)
        records = sdk.bus.records
        syncs = [r for r in records if r["op"] == "sync_write"]
        self.assertEqual(syncs, [{"op": "sync_write", "addr": 46, "len": 2, "params": {"7": [0, 0], "8": [0, 0], "9": [0, 0]}}])
        self.assertEqual(writes(records), [(7, 46, [0, 0]), (8, 46, [0, 0]), (9, 46, [0, 0])])
        self.assertEqual(ids_named(records), WHEEL_IDS, "a servo other than the three wheels was addressed")
        self.assertIn({"op": "protocol", "end": 0}, records)
        self.assertIn({"op": "open", "baud": 1000000}, records)
        self.assertEqual(records[-1], {"op": "close"}, "the port is closed last")
        for sid in WHEEL_IDS:
            self.assertEqual(velocity(sdk.bus.regs, sid), 0)
            self.assertEqual(velocity(sdk.bus.regs, sid, 58), 0)
            self.assertEqual(sdk.bus.regs[sid][40], 1, "the wheel torque was changed on a verified run")
        self.assertEqual({sid: sdk.bus.regs[sid] for sid in ARM_IDS}, arm_before)
        for want in ("WROTE Goal_Velocity 0 to wheels 7 8 9", "wheel 7 base_left_wheel: WROTE Goal_Velocity 0, acknowledged",
                     "wheel 8 base_back_wheel: READ BACK goal velocity 0", "present velocity after zeroing (wheels 7 8 9): 0 0 0",
                     "DONE"):
            self.assertIn(want, out)
        self.assertNotIn("FALLBACK", out)
        for line in out.splitlines():
            self.assertRegex(line, r"^wheels-safe-stop: \+\d+ ms ")
        # The start line carries the wall-clock time to the millisecond, so a kill time can be set against it.
        self.assertRegex(out.splitlines()[0], r"^wheels-safe-stop: \+\d+ ms start at \d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z: pid \d+, ")

    def test_a_log_that_cannot_be_written_does_not_stop_the_writes(self):
        # The log is evidence, never a precondition: a full disk (every line fails with ENOSPC) or a closed stream
        # (ValueError) drops the lines, and the zero is still written to the three wheels and verified.
        full = UnwritableLog()
        closed = io.StringIO()
        closed.close()
        for name, stream in (("full disk", full), ("closed stream", closed)):
            with self.subTest(log=name):
                sdk = fake_sdk()
                start = time.monotonic()
                try:
                    rc = self.helper.main(["--port", self.device, "--lock", self.lock], sdk_loader=lambda: sdk,
                                          clock=lambda: time.monotonic() - start, stream=stream, proc_root=self.proc_root)
                except (OSError, ValueError) as error:
                    self.fail(f"the log's failure stopped the wheel stop: {type(error).__name__}: {error}")
                self.assertEqual(rc, 0)
                self.assertEqual([r for r in sdk.bus.records if r["op"] == "sync_write"],
                                 [{"op": "sync_write", "addr": 46, "len": 2, "params": {"7": [0, 0], "8": [0, 0], "9": [0, 0]}}])
                self.assertEqual(writes(sdk.bus.records), [(7, 46, [0, 0]), (8, 46, [0, 0]), (9, 46, [0, 0])])
                for sid in WHEEL_IDS:
                    self.assertEqual(velocity(sdk.bus.regs, sid), 0)
        self.assertGreater(full.attempts, 1, "the helper never tried to log, so no failing write was exercised")

    def test_the_zero_is_written_before_anything_is_read(self):
        sdk = fake_sdk()
        rc, out, _ = self.run_helper(sdk)
        self.assertEqual(rc, 0, out)
        bus_ops = [r["op"] for r in sdk.bus.records if r["op"] in ("sync_write", "write", "read")]
        self.assertEqual(bus_ops[:4], ["sync_write", "write", "write", "write"], bus_ops)

    def test_present_velocity_is_read_again_until_the_wheels_stand(self):
        # The per-wheel readback is the first present read after the writes; the wheels stand from the third on.
        sdk = fake_sdk({"present_lag_reads": 3})
        rc, out, _ = self.run_helper(sdk)
        self.assertEqual(rc, 0, out)
        samples = re.findall(r"present velocity after zeroing \(wheels 7 8 9\): (-?\d+|\?) (-?\d+|\?) (-?\d+|\?)", out)
        self.assertGreaterEqual(len(samples), 2, out)
        self.assertEqual(samples[-1], ("0", "0", "0"), out)
        self.assertNotEqual(samples[0], ("0", "0", "0"), out)

    def test_a_lost_sync_write_is_made_good_by_the_acknowledged_writes(self):
        sdk = fake_sdk({"sync_lost": True})
        rc, out, _ = self.run_helper(sdk)
        self.assertEqual(rc, 0, out)
        for sid in WHEEL_IDS:
            self.assertEqual(velocity(sdk.bus.regs, sid), 0)

    def test_a_wheel_that_keeps_its_goal_gets_torque_off_and_no_other_servo_does(self):
        sdk = fake_sdk({"sticky_goal_ids": [8]})
        rc, out, _ = self.run_helper(sdk)
        self.assertEqual(rc, 1, out)
        torque_writes = [w for w in writes(sdk.bus.records) if w[1] == 40]
        self.assertEqual(torque_writes, [(8, 40, [0])])
        self.assertEqual(sdk.bus.regs[8][40], 0)
        self.assertEqual((sdk.bus.regs[7][40], sdk.bus.regs[9][40]), (1, 1))
        self.assertEqual(ids_named(sdk.bus.records), WHEEL_IDS)
        self.assertIn("wheel 8 base_back_wheel: NOT VERIFIED: its goal velocity read back as 300", out)
        self.assertIn("wheel 8 base_back_wheel: FALLBACK: WROTE Torque_Enable 0 so that it stops being driven, acknowledged; "
                      "its torque reads 0: it is no longer driven and turns freely", out)
        self.assertIn("NOT VERIFIED: wheel 8", out)

    def test_the_mode_is_read_with_the_goal_velocity_before_any_present_velocity(self):
        # A zero goal velocity stops a wheel only in velocity mode, so the mode belongs to the verification: it is read in
        # the same pass as the goal velocity, right after it, before the present-velocity sampling.
        sdk = fake_sdk()
        rc, out, _ = self.run_helper(sdk)
        self.assertEqual(rc, 0, out)
        reads = [(r["id"], r["addr"]) for r in sdk.bus.records if r["op"] == "read"]
        self.assertEqual(reads[:6], [(7, 46), (7, 33), (8, 46), (8, 33), (9, 46), (9, 33)], reads)

    def test_a_wheel_not_in_velocity_mode_is_not_verified_and_its_torque_is_switched_off(self):
        # A replaced wheel servo comes in position mode (0) with its torque off. On firmware that switches torque on at a
        # goal write (open on the real servos), the stop's own zero switches it on, and a zero goal velocity does not stop
        # a wheel in position mode. The run must end NOT VERIFIED with that wheel's torque switched off: exactly one write
        # more than a verified run, Torque_Enable 0 to wheel 8, and nothing else.
        sdk = fake_sdk({"wheel_velocity": 0, "torque": {"7": 0, "8": 0, "9": 0}, "mode": {"8": 0}, "goal_write_energizes": True})
        arm_before = {sid: dict(sdk.bus.regs[sid]) for sid in ARM_IDS}
        rc, out, _ = self.run_helper(sdk)
        self.assertEqual(rc, 1, out)
        self.assertEqual(writes(sdk.bus.records), [(7, 46, [0, 0]), (8, 46, [0, 0]), (9, 46, [0, 0]), (8, 40, [0])])
        self.assertEqual(sdk.bus.regs[8][40], 0, "the wheel that is not in velocity mode was left with its torque on")
        self.assertEqual((sdk.bus.regs[7][40], sdk.bus.regs[9][40]), (1, 1))
        self.assertEqual(sdk.bus.regs[8][33], 0, "the operating mode was written")
        self.assertEqual({sid: sdk.bus.regs[sid] for sid in ARM_IDS}, arm_before)
        self.assertEqual(ids_named(sdk.bus.records), WHEEL_IDS)
        self.assertIn("wheel 8 base_back_wheel: NOT VERIFIED: its goal velocity read back 0, but its operating mode reads 0, "
                      "not 1 (velocity mode): a zero goal velocity does not stop a wheel in another mode", out)
        self.assertIn("wheel 8 base_back_wheel: FALLBACK: WROTE Torque_Enable 0 so that it stops being driven, acknowledged; "
                      "its torque reads 0: it is no longer driven and turns freely", out)
        self.assertIn("NOT VERIFIED: wheel 8 did not read back goal velocity 0 in velocity mode", out)
        self.assertNotIn("DONE", out)
        # The zero is still the first thing sent, and the fallback comes before the present-velocity sampling.
        sent = [(r["op"], r.get("id"), r.get("addr")) for r in sdk.bus.records if r["op"] in ("sync_write", "write", "read")]
        self.assertEqual(sent[0], ("sync_write", None, 46))
        self.assertLess(sent.index(("write", 8, 40)), sent.index(("read", 7, 58)), sent)

    def test_a_wheel_whose_mode_cannot_be_read_is_not_verified_and_its_torque_is_switched_off(self):
        sdk = fake_sdk({"corrupt_reads": [[9, 33]]})
        rc, out, _ = self.run_helper(sdk)
        self.assertEqual(rc, 1, out)
        self.assertEqual([w for w in writes(sdk.bus.records) if w[1] == 40], [(9, 40, [0])])
        self.assertEqual(sdk.bus.regs[9][40], 0)
        self.assertEqual((sdk.bus.regs[7][40], sdk.bus.regs[8][40]), (1, 1))
        self.assertIn("wheel 9 base_right_wheel: NOT VERIFIED: its goal velocity read back 0, but its operating mode could "
                      "not be read back (corrupt status packet)", out)
        self.assertIn("wheel 9 base_right_wheel: FALLBACK: WROTE Torque_Enable 0 so that it stops being driven, acknowledged; "
                      "its torque reads 0: it is no longer driven and turns freely", out)
        self.assertIn("NOT VERIFIED: wheel 9 ", out)

    def test_a_mode_left_unread_for_want_of_time_is_not_verified(self):
        # Wheel 9's goal velocity reads 0, but that read answers so late that no exchange fits the budget after it: its
        # mode is not read, so it is not verified, and there is no time left for its fallback either.
        sdk = fake_sdk({"slow_reads": [[9, 46, 0.95]]})
        rc, out, elapsed = self.run_helper(sdk, budget_ms=1000)
        self.assertEqual(rc, 1, out)
        reads = [(r["id"], r["addr"]) for r in sdk.bus.records if r["op"] == "read"]
        self.assertEqual(reads, [(7, 46), (7, 33), (8, 46), (8, 33), (9, 46)], reads)
        self.assertEqual([w for w in writes(sdk.bus.records) if w[1] == 40], [])
        self.assertIn("wheel 9 base_right_wheel: NOT VERIFIED: its goal velocity read back 0, but its operating mode was not "
                      "read back: the time budget was spent", out)
        self.assertIn("wheel 9 base_right_wheel: FALLBACK NOT WRITTEN: the time budget was spent", out)
        self.assertIn("NOT VERIFIED: wheel 9 ", out)
        self.assertLess(elapsed, 1.0 + 0.1, out)

    def test_a_fallback_that_does_not_take_is_said_to_have_failed(self):
        sdk = fake_sdk({"sticky_goal_ids": [7], "sticky_torque_ids": [7]})
        rc, out, _ = self.run_helper(sdk)
        self.assertEqual(rc, 1, out)
        self.assertIn("wheel 7 base_left_wheel: FALLBACK: WROTE Torque_Enable 0 so that it stops being driven, acknowledged; "
                      "its torque still reads 1: the fallback did NOT take", out)
        self.assertNotIn("turns freely", out)
        self.assertIn("If a wheel still turns, cut power.", out)

    def test_a_silent_wheel_is_reported_and_the_run_ends_inside_its_budget(self):
        sdk = fake_sdk({"dead_ids": [9], "timeout_s": 0.05})
        rc, out, elapsed = self.run_helper(sdk, budget_ms=360)
        self.assertEqual(rc, 1, out)
        self.assertLessEqual(elapsed, 0.36 + 0.03, out)
        self.assertEqual(ids_named(sdk.bus.records), WHEEL_IDS)
        for sid in (7, 8):
            self.assertEqual(velocity(sdk.bus.regs, sid), 0)
        self.assertIn("wheel 9 base_right_wheel: WROTE Goal_Velocity 0, NOT acknowledged", out)
        self.assertIn("wheel 9 base_right_wheel: NOT VERIFIED: its goal velocity could not be read back (no status packet)", out)
        self.assertIn("wheel 9 base_right_wheel: FALLBACK: WROTE Torque_Enable 0, NOT acknowledged (no status packet); "
                      "whether it took is unknown", out)
        self.assertIn("wheel 9 base_right_wheel: it answered no request; nothing more is asked of it", out)
        for sid, name in ((7, "base_left_wheel"), (8, "base_back_wheel")):
            self.assertIn(f"wheel {sid} {name}: READ BACK goal velocity 0, present velocity 0", out)
        self.assertNotIn("turns freely", out)
        reads_of_9 = [r for r in sdk.bus.records if r.get("id") == 9 and r["op"] == "read"]
        self.assertEqual(len(reads_of_9), 1, "a wheel that answered nothing was asked more than its goal velocity")

    def test_a_silent_bus_ends_at_the_budget_after_trying_every_wheel(self):
        sdk = fake_sdk({"dead_ids": [7, 8, 9], "timeout_s": 0.05})
        rc, out, elapsed = self.run_helper(sdk, budget_ms=300)
        self.assertEqual(rc, 1, out)
        self.assertLessEqual(elapsed, 0.30 + 0.03, out)
        sent = [r for r in sdk.bus.records if r["op"] in ("sync_write", "write")]
        self.assertEqual(sent[0]["op"], "sync_write")
        self.assertEqual([(r["id"], r["addr"]) for r in sent[1:4]], [(7, 46), (8, 46), (9, 46)],
                         "each wheel gets its first acknowledged write before any wheel is written again")
        self.assertIn("budget", out)

    def test_two_runs_write_the_same_and_leave_the_same_state(self):
        sdk = fake_sdk()
        rc1, out1, _ = self.run_helper(sdk)
        first = [r for r in sdk.bus.records if r["op"] in ("sync_write", "write")]
        regs1 = json.dumps(sdk.bus.regs, sort_keys=True)
        del sdk.bus.records[:]
        rc2, out2, _ = self.run_helper(sdk)
        second = [r for r in sdk.bus.records if r["op"] in ("sync_write", "write")]
        self.assertEqual((rc1, rc2), (0, 0), out1 + out2)
        self.assertEqual(first, second)
        self.assertEqual(json.dumps(sdk.bus.regs, sort_keys=True), regs1)

    def test_a_missing_device_writes_nothing(self):
        sdk = fake_sdk()
        missing = os.path.join(self.tmp.name, "no-such-device")
        rc, out, _ = self.run_helper(sdk, argv=["--port", missing, "--lock", self.lock])
        self.assertEqual(rc, 3, out)
        self.assertIn("NOT RUN: the servo device is missing", out)
        self.assertEqual(sdk.bus.records, [])

    def test_a_path_that_is_not_a_character_device_writes_nothing(self):
        sdk = fake_sdk()
        plain = os.path.join(self.tmp.name, "plain-file")
        with open(plain, "w", encoding="utf-8"):
            pass
        rc, out, _ = self.run_helper(sdk, argv=["--port", plain, "--lock", self.lock])
        self.assertEqual(rc, 3, out)
        self.assertIn("is not a character device", out)
        self.assertEqual(sdk.bus.records, [])

    def test_a_device_another_process_holds_open_is_not_written(self):
        sdk = fake_sdk()
        holder = self.hold(self.device, "device")
        rc, out, _ = self.run_helper(sdk, proc_root="/proc")
        self.assertEqual(rc, 3, out)
        self.assertIn(f"NOT RUN: the servo device has an owner (pid {holder.process.pid} ", out)
        self.assertEqual(sdk.bus.records, [])

    def test_the_owner_line_names_five_owners_and_counts_the_rest(self):
        sdk = fake_sdk()
        holders = [self.hold(self.device, "device") for _ in range(7)]
        rc, out, _ = self.run_helper(sdk, proc_root="/proc")
        self.assertEqual(rc, 3, out)
        line = [l for l in out.splitlines() if "NOT RUN: the servo device has an owner" in l][0]
        self.assertEqual(len(re.findall(r"pid \d+ ", line)), 5, line)
        self.assertIn(", and 2 more)", line)
        self.assertTrue(any(f"pid {h.process.pid} " in line for h in holders))
        self.assertEqual(sdk.bus.records, [])

    def test_a_lock_held_for_the_whole_wait_is_skipped_and_nothing_is_written(self):
        # The holder may be another wheel stop, but also a host that run-host.sh started, which holds the lock from before
        # its owner check until it exits: the line must not say it is always another wheel stop.
        sdk = fake_sdk()
        self.hold(self.lock, "lock")
        rc, out, elapsed = self.run_helper(sdk, budget_ms=200)
        self.assertEqual(rc, 3, out)
        self.assertIn("NOT RUN: the wheel-stop lock was held for the whole wait: by another wheel stop or a --status run, or "
                      "by a host that run-host.sh started, which holds it from before its owner check until it exits; nothing "
                      "was written", out)
        self.assertNotIn("it is the one writing the wheels", out)
        self.assertLess(elapsed, 0.2, out)
        self.assertEqual(sdk.bus.records, [])

    def test_a_lock_released_during_the_wait_is_taken_and_the_wheels_are_written(self):
        sdk = fake_sdk()
        self.hold(self.lock, "lock", hold_s=0.05)
        rc, out, _ = self.run_helper(sdk)
        self.assertEqual(rc, 0, out)
        self.assertEqual(writes(sdk.bus.records), [(7, 46, [0, 0]), (8, 46, [0, 0]), (9, 46, [0, 0])])

    def test_a_lock_file_that_cannot_be_opened_writes_nothing(self):
        # The lock is what keeps a wheel stop off a bus that a host start is opening, so a stop writes only while it holds
        # it: a lock that cannot be opened, like one held for the whole wait, is a stop that writes nothing.
        sdk = fake_sdk()
        rc, out, _ = self.run_helper(sdk, argv=["--port", self.device, "--lock", os.path.join(self.tmp.name, "no-dir", "x.lock")])
        self.assertEqual(rc, 3, out)
        self.assertIn("NOT RUN: the wheel-stop lock could not be taken (the lock file could not be opened: No such file or "
                      "directory)", out)
        self.assertNotIn("continuing without it", out)
        self.assertEqual(sdk.bus.records, [])

    def test_a_lock_file_this_account_may_only_read_is_still_taken(self):
        # flock needs no write access, so the lock file is opened read-only: a file left read-only (a read-only file system
        # does the same to a file that exists) must not keep the stop from writing the wheels.
        with open(self.lock, "w", encoding="utf-8"):
            pass
        os.chmod(self.lock, 0o400)
        sdk = fake_sdk({"lock_probe": self.lock})
        rc, out, _ = self.run_helper(sdk)
        self.assertEqual(rc, 0, out)
        self.assertEqual(writes(sdk.bus.records), [(7, 46, [0, 0]), (8, 46, [0, 0]), (9, 46, [0, 0])])
        self.assertTrue(sdk.bus.lock_seen and all(held for _, held in sdk.bus.lock_seen), sdk.bus.lock_seen)

    def test_every_bus_exchange_and_the_port_close_happen_while_the_lock_is_held(self):
        for config in ({}, {"dead_ids": [9]}, {"sticky_goal_ids": [8]}):
            with self.subTest(config=config):
                sdk = fake_sdk({"lock_probe": self.lock, **config})
                self.run_helper(sdk)
                ops = [op for op, _ in sdk.bus.lock_seen]
                self.assertEqual(ops, [r["op"] for r in sdk.bus.records])
                self.assertIn("sync_write", ops)
                self.assertEqual(ops[-1], "close")
                self.assertEqual([op for op, held in sdk.bus.lock_seen if not held], [], "the bus was used without the lock")
        rc, out, _ = self.status(fake_sdk({"lock_probe": self.lock}))
        self.assertEqual(rc, 0, out)

    def test_an_sdk_that_cannot_be_imported_writes_nothing(self):
        def missing():
            raise ModuleNotFoundError("No module named 'scservo_sdk'")
        rc, out, _ = self.run_helper(None, loader=missing)
        self.assertEqual(rc, 4, out)
        self.assertIn("NOT RUN: the feetech SDK (scservo_sdk) could not be imported", out)

    def test_a_device_that_cannot_be_opened_writes_nothing(self):
        sdk = fake_sdk({"open_fails": True})
        rc, out, _ = self.run_helper(sdk)
        self.assertEqual(rc, 4, out)
        self.assertIn("NOT RUN: the servo device could not be opened", out)
        self.assertEqual([r for r in sdk.bus.records if r["op"] in ("sync_write", "write", "read")], [])

    def test_on_the_real_proc_the_owner_check_sees_this_process_and_the_stop_writes(self):
        # The stand-in /proc keeps the other tests' budgets free of a real scan's time. This one scans the real /proc,
        # with a budget no scan spends, so its positive control and its verdict are the real ones.
        sdk = fake_sdk()
        rc, out, _ = self.run_helper(sdk, budget_ms=5000, proc_root="/proc")
        self.assertEqual(rc, 0, out)
        self.assertEqual(writes(sdk.bus.records), [(7, 46, [0, 0]), (8, 46, [0, 0]), (9, 46, [0, 0])])
        self.assertIn("DONE", out)

    def test_the_owner_check_fails_closed_when_it_cannot_see_this_process(self):
        sdk = fake_sdk()
        empty_proc = os.path.join(self.tmp.name, "empty-proc")
        os.mkdir(empty_proc)
        rc, out, _ = self.run_helper(sdk, proc_root=empty_proc)
        self.assertEqual(rc, 3, out)
        self.assertIn("NOT RUN: the owner check did not see this process", out)
        self.assertEqual(sdk.bus.records, [])

    def test_no_scenario_addresses_an_arm_servo_or_writes_anything_but_zero(self):
        for config in ({}, {"sticky_goal_ids": [7, 9]}, {"dead_ids": [8]}, {"present_lag_reads": 3}, {"sync_lost": True},
                       {"mode": {"8": 0}}, {"torque": {"7": 0}}, {"error_byte": 0x20}, {"dead_ids": [7, 8, 9]},
                       {"sticky_goal_ids": [8], "sticky_torque_ids": [8]},
                       {"torque": {"7": 0, "8": 0, "9": 0}, "goal_write_energizes": True}):
            with self.subTest(config=config):
                sdk = fake_sdk(config)
                self.run_helper(sdk)
                self.assertEqual(ids_named(sdk.bus.records) & ARM_IDS, set())
                for sid, address, data in writes(sdk.bus.records):
                    self.assertIn(sid, WHEEL_IDS)
                    self.assertIn(address, (40, 46))
                    self.assertEqual(set(data), {0}, "a write carried a nonzero value")

    def test_a_verified_run_writes_no_torque_register_and_reports_the_torque_it_read_after(self):
        sdk = fake_sdk({"torque": {"7": 0}})
        rc, out, _ = self.run_helper(sdk)
        self.assertEqual(rc, 0, out)
        self.assertEqual([w for w in writes(sdk.bus.records) if w[1] == 40], [])
        self.assertEqual(sdk.bus.regs[7][40], 0)
        self.assertIn("DONE: wheels 7 8 9 read back goal velocity 0 in velocity mode; present velocity 0 0 0; no torque "
                      "register was written; torque read after the writes: 0 1 1; no other servo was read or written", out)

    def test_torque_a_goal_write_switched_on_is_reported_as_read_never_as_kept(self):
        # Whether a goal write switches on a torque-off STS3215 is open on the real servos. With firmware that does, the
        # wheels end at torque 1 although the helper wrote no torque register: the log must say what it read after the
        # writes, and never that the torque was left as it was.
        sdk = fake_sdk({"torque": {"7": 0, "8": 0, "9": 0}, "goal_write_energizes": True})
        rc, out, _ = self.run_helper(sdk)
        self.assertEqual(rc, 0, out)
        self.assertEqual([w for w in writes(sdk.bus.records) if w[1] == 40], [])
        self.assertEqual([sdk.bus.regs[sid][40] for sid in (7, 8, 9)], [1, 1, 1])
        self.assertIn("no torque register was written; torque read after the writes: 1 1 1;", out)
        self.assertNotIn("as it was", out)

    def status(self, sdk, **kwargs):
        return self.run_helper(sdk, argv=["--status", "--port", self.device, "--lock", self.lock], **kwargs)

    def test_status_reads_the_three_wheels_and_writes_nothing(self):
        sdk = fake_sdk({"torque": {"7": 0, "8": 1, "9": 0}, "mode": {"8": 0}, "wheel_velocity": -120})
        regs_before = json.dumps(sdk.bus.regs, sort_keys=True)
        rc, out, _ = self.status(sdk)
        self.assertEqual(rc, 0, out)
        self.assertEqual([r for r in sdk.bus.records if r["op"] in ("write", "sync_write")], [], "status wrote to the bus")
        self.assertEqual(json.dumps(sdk.bus.regs, sort_keys=True), regs_before)
        self.assertEqual(ids_named(sdk.bus.records), WHEEL_IDS)
        self.assertEqual({r["addr"] for r in sdk.bus.records if r["op"] == "read"}, {33, 40, 46, 58})
        self.assertEqual(sdk.bus.records[-1], {"op": "close"})
        self.assertIn("wheel 7 base_left_wheel: STATUS torque 0, mode 1, goal velocity -120, present velocity -120", out)
        self.assertIn("wheel 8 base_back_wheel: STATUS torque 1, mode 0, goal velocity -120, present velocity -120", out)
        self.assertIn("STATUS (read only, nothing was written; wheels 7 8 9): torque 0 1 0; mode 1 0 1; "
                      "goal velocity -120 -120 -120; present velocity -120 -120 -120", out)
        self.assertRegex(out.splitlines()[0], r"^wheels-safe-stop: \+\d+ ms status at \d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z: "
                                              r"pid \d+, servo device ")
        self.assertNotIn("WROTE", out)

    def test_status_of_a_silent_wheel_is_incomplete_and_asks_it_nothing_more(self):
        sdk = fake_sdk({"dead_ids": [8], "timeout_s": 0.01})
        rc, out, _ = self.status(sdk)
        self.assertEqual(rc, 1, out)
        self.assertEqual([r for r in sdk.bus.records if r["op"] in ("write", "sync_write")], [])
        self.assertEqual(len([r for r in sdk.bus.records if r.get("id") == 8]), 1, "a silent wheel was asked more than once")
        self.assertIn("wheel 8 base_back_wheel: STATUS NOT READ (no status packet)", out)
        self.assertIn("STATUS (read only, nothing was written; wheels 7 8 9): torque 1 ? 1; mode 1 ? 1;", out)

    def test_status_of_a_device_another_process_holds_reads_nothing(self):
        sdk = fake_sdk()
        holder = self.hold(self.device, "device")
        rc, out, _ = self.status(sdk, proc_root="/proc")
        self.assertEqual(rc, 3, out)
        self.assertIn(f"NOT RUN: the servo device has an owner (pid {holder.process.pid} ", out)
        self.assertEqual(sdk.bus.records, [])

    def test_status_before_and_after_a_stop_shows_whether_the_goal_write_switched_torque_on(self):
        # The attended probe, offline: wheels at torque 0 (a power cycle), status, the stop, status again. Only firmware
        # that switches torque on when a goal is written moves the torque from 0 to 1.
        for energizes, after in ((False, "torque 0 0 0;"), (True, "torque 1 1 1;")):
            with self.subTest(goal_write_energizes=energizes):
                sdk = fake_sdk({"torque": {"7": 0, "8": 0, "9": 0}, "wheel_velocity": 0, "goal_write_energizes": energizes})
                rc, before_out, _ = self.status(sdk)
                self.assertEqual(rc, 0, before_out)
                self.assertIn("): torque 0 0 0;", before_out)
                rc, stop_out, _ = self.run_helper(sdk)
                self.assertEqual(rc, 0, stop_out)
                rc, after_out, _ = self.status(sdk)
                self.assertEqual(rc, 0, after_out)
                self.assertIn(f"): {after}", after_out)

    def test_self_check_imports_the_sdk_and_opens_nothing(self):
        sdk = fake_sdk()
        rc, out, _ = self.run_helper(sdk, argv=["--self-check"])
        self.assertEqual(rc, 0, out)
        self.assertIn("SELF-CHECK ok", out)
        self.assertIn("no device was opened", out)
        self.assertEqual(sdk.bus.records, [])

        def missing():
            raise ModuleNotFoundError("No module named 'scservo_sdk'")
        rc, out, _ = self.run_helper(None, argv=["--self-check"], loader=missing)
        self.assertEqual(rc, 4, out)
        self.assertIn("SELF-CHECK FAILED", out)

    def self_check_at(self, age_s, sdk=None, proc_root="/proc"):
        """--self-check with a clock that reads AGE_S of this process's age throughout."""
        stream = io.StringIO()
        sdk = sdk or fake_sdk()
        rc = self.helper.main(["--self-check"], sdk_loader=lambda: sdk, clock=lambda: age_s, stream=stream, proc_root=proc_root)
        return rc, stream.getvalue(), sdk

    def test_self_check_fails_when_this_process_is_ready_past_the_limit(self):
        # The limit is the last moment the stop's budget lets a readback exchange begin. A stop whose process is ready to
        # open the bus later could not read back what it writes, and one later still is ended by the safe-stop's hard
        # limit before it writes at all. So an interpreter that slow must fail the self-check (and so the install).
        helper = self.helper
        self.assertEqual(helper.SELF_CHECK_READY_LIMIT_MS, round((helper.DEFAULT_BUDGET_S - helper.EXCHANGE_S) * 1000))
        self.assertLess(helper.SELF_CHECK_READY_LIMIT_MS, hard_limit_s() * 1000)
        for age_s, want_rc, verdict in ((0.05, 0, "SELF-CHECK ok"), (0.3, 0, "SELF-CHECK ok"), (0.31, 1, "SELF-CHECK TOO SLOW"),
                                        (0.635, 1, "SELF-CHECK TOO SLOW")):
            with self.subTest(age_s=age_s):
                rc, out, sdk = self.self_check_at(age_s)
                self.assertEqual(rc, want_rc, out)
                self.assertIn(verdict, out)
                self.assertIn(f"ready to open the bus at +{round(age_s * 1000)} ms of its age", out)
                self.assertIn("limit of 300 ms", out)
                self.assertIn("no device was opened", out)
                self.assertEqual(sdk.bus.records, [])
                if want_rc:
                    self.assertNotIn("SELF-CHECK ok", out)

    def test_self_check_fails_when_the_owner_check_cannot_see_this_process(self):
        # A real stop runs the owner check before it opens the bus and fails closed when the check cannot see this process
        # holding its own file: every stop would then write nothing. The self-check runs the same scan and says so.
        empty_proc = os.path.join(self.tmp.name, "empty-proc")
        os.mkdir(empty_proc)
        rc, out, sdk = self.self_check_at(0.05, proc_root=empty_proc)
        self.assertEqual(rc, 3, out)
        self.assertIn("SELF-CHECK FAILED: the owner check did not see this process holding its own file open", out)
        self.assertNotIn("SELF-CHECK ok", out)
        self.assertEqual(sdk.bus.records, [])

    def test_usage_errors_exit_2_and_write_nothing(self):
        sdk = fake_sdk()
        for argv in (["--port", self.device], ["--lock", self.lock], ["--port", self.device, "--lock", self.lock, "--arm"],
                     ["--port", self.device, "--lock", self.lock, "--budget-ms", "lots"], [], ["--status"],
                     ["--status", "--status", "--port", self.device, "--lock", self.lock], ["--self-check", "--status"],
                     ["--status", "--self-check"]):
            with self.subTest(argv=argv):
                rc, out, _ = self.run_helper(sdk, argv=argv)
                self.assertEqual(rc, 2, out)
        self.assertEqual(sdk.bus.records, [])

    def test_the_default_budget_leaves_room_under_the_scripts_hard_limit(self):
        limit = hard_limit_s()
        self.assertLess(limit, SUPERVISOR_DEADLINE_S)
        self.assertLessEqual(self.helper.DEFAULT_BUDGET_S + 0.04, limit)
        self.assertEqual(self.helper.WHEEL_IDS, (7, 8, 9))


def hard_limit_s():
    with open(TEMPLATE, encoding="utf-8") as handle:
        found = re.search(r"(?m)^timeout -s KILL ([0-9.]+) ", handle.read())
    assert found, "safe-stop.sh.in has no 'timeout -s KILL <seconds>' line"
    return float(found.group(1))


def render_safe_stop(path, python, helper, port, lock):
    """bin/templates/safe-stop.sh.in with its four wheel settings filled in, line for line as lekiwi-deploy.sh fills them."""
    with open(TEMPLATE, encoding="utf-8") as handle:
        text = handle.read()
    for name, value in (("WHEELS_PYTHON", python), ("WHEELS_HELPER", helper), ("WHEELS_PORT", port), ("WHEELS_LOCK", lock)):
        text, n = re.subn(rf"(?m)^{name}=''$", f"{name}='{value}'", text)
        assert n == 1, f"the template has no single empty {name} line"
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.chmod(path, 0o755)


class SafeStopFixture(DeviceFixture):
    """The rendered bin/templates/safe-stop.sh.in, its interpreter (a wrapper that puts the fake SDK first on the import
    path and names its configuration and record files), and how the supervisor runs it. It has no tests of its own."""

    def setUp(self):
        super().setUp()
        t = self.tmp.name
        self.cwd = os.path.join(t, "child-cwd")
        self.logs = os.path.join(t, "logs")
        self.sdk_dir = os.path.join(t, "fake-sdk")
        for d in (self.cwd, self.logs, os.path.join(self.sdk_dir, "scservo_sdk")):
            os.makedirs(d)
        with open(os.path.join(self.sdk_dir, "scservo_sdk", "__init__.py"), "w", encoding="utf-8") as handle:
            handle.write(FAKE_SDK)
        self.config = os.path.join(t, "fake-config.json")
        self.record = os.path.join(t, "fake-record.jsonl")
        self.calls = os.path.join(t, "python-calls")
        self.python = os.path.join(t, "python")
        with open(self.python, "w", encoding="utf-8") as handle:
            handle.write("#!/bin/sh\n"
                         f"printf '%s\\n' \"$*\" >>'{self.calls}'\n"
                         f"export PYTHONPATH='{self.sdk_dir}' FAKE_SCS_CONFIG='{self.config}' FAKE_SCS_RECORD='{self.record}'\n"
                         f"exec '{sys.executable}' \"$@\"\n")
        os.chmod(self.python, 0o755)
        self.script = os.path.join(t, "safe-stop.sh")
        self.log = os.path.join(self.logs, "safe-stop-host.log")

    def configure(self, **config):
        with open(self.config, "w", encoding="utf-8") as handle:
            json.dump(config, handle)

    def render(self, port=None, python=None):
        render_safe_stop(self.script, python or self.python, HELPER, port or self.device, self.lock)

    def run_script(self, log=None):
        start = time.monotonic()
        result = subprocess.run([self.script, log or self.log], env=CAKE_ENV, cwd=self.cwd, stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=20)
        return result, time.monotonic() - start

    def log_text(self):
        with open(self.log, encoding="utf-8") as handle:
            return handle.read()

    def records(self):
        if not os.path.exists(self.record):
            return []
        with open(self.record, encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]

    def python_calls(self):
        if not os.path.exists(self.calls):
            return []
        with open(self.calls, encoding="utf-8") as handle:
            return handle.read().splitlines()


@unittest.skipUnless(sys.platform.startswith("linux") and os.path.isdir("/proc/self/fd") and shutil.which("timeout", path=CAKE_ENV["PATH"]),
                     "needs Linux /proc and timeout in /usr/bin:/bin")
class SafeStopScript(SafeStopFixture):
    """The rendered bin/templates/safe-stop.sh.in, run as the supervisor runs it."""

    def test_the_template_as_segment_2_copies_it_only_logs(self):
        shutil.copy(TEMPLATE, self.script)
        os.chmod(self.script, 0o755)
        result, _ = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = self.log_text().splitlines()
        self.assertEqual(len(lines), 1, lines)
        self.assertRegex(lines[0], r"^safe-stop ran at \d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertEqual(self.python_calls(), [])

    def test_rendered_it_zeroes_the_wheels_and_logs_the_readback_inside_the_supervisor_deadline(self):
        self.configure()
        self.render()
        result, elapsed = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout + result.stderr, "", "the script printed outside its log")
        text = self.log_text()
        self.assertRegex(text.splitlines()[0], r"^safe-stop ran at ")
        for want in ("WROTE Goal_Velocity 0 to wheels 7 8 9", "READ BACK goal velocity 0",
                     "present velocity after zeroing (wheels 7 8 9): 0 0 0", "DONE",
                     "safe-stop: wheel stop exited 0 (every wheel read back goal velocity 0 in velocity mode)"):
            self.assertIn(want, text)
        self.assertLess(elapsed, SUPERVISOR_DEADLINE_S, text)
        self.assertEqual(ids_named(self.records()), WHEEL_IDS)
        self.assertEqual(writes(self.records()), [(7, 46, [0, 0]), (8, 46, [0, 0]), (9, 46, [0, 0])])
        self.assertEqual(self.python_calls(), [f"-u {HELPER} --port {self.device} --lock {self.lock}"])

    def test_a_wheel_not_in_velocity_mode_is_logged_not_verified_and_the_script_exits_0(self):
        self.configure(wheel_velocity=0, torque={"7": 0, "8": 0, "9": 0}, mode={"8": 0}, goal_write_energizes=True)
        self.render()
        result, _ = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        text = self.log_text()
        self.assertIn("wheel 8 base_back_wheel: NOT VERIFIED: its goal velocity read back 0, but its operating mode reads 0", text)
        self.assertIn("wheel 8 base_back_wheel: FALLBACK: WROTE Torque_Enable 0 so that it stops being driven, acknowledged", text)
        self.assertIn("safe-stop: wheel stop exited 1 (NOT VERIFIED: see the wheel lines above)", text)
        self.assertNotIn("DONE", text)
        self.assertEqual(writes(self.records()), [(7, 46, [0, 0]), (8, 46, [0, 0]), (9, 46, [0, 0]), (8, 40, [0])])
        self.assertEqual(ids_named(self.records()), WHEEL_IDS)

    def test_a_missing_device_is_logged_and_the_script_exits_0(self):
        self.configure()
        self.render(port="/dev/kc-no-such-servo-device")
        result, _ = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        text = self.log_text()
        self.assertIn("NOT RUN: the servo device is missing", text)
        self.assertIn("safe-stop: wheel stop exited 3", text)
        self.assertEqual(self.records(), [])

    def test_a_busy_device_is_logged_and_the_script_exits_0(self):
        self.configure()
        self.render()
        holder = self.hold(self.device, "device")
        result, _ = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        text = self.log_text()
        self.assertIn(f"NOT RUN: the servo device has an owner (pid {holder.process.pid} ", text)
        self.assertIn("safe-stop: wheel stop exited 3", text)
        self.assertEqual(self.records(), [])

    def test_a_hung_helper_is_killed_at_the_hard_limit_and_the_script_still_exits_0(self):
        self.configure(hang_open_s=5)
        self.render()
        result, elapsed = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertGreaterEqual(elapsed, hard_limit_s())
        self.assertLess(elapsed, 1.0, "the hard limit did not end the helper")
        text = self.log_text()
        self.assertIn("safe-stop: wheel stop exited 137 (ended at the hard limit", text)
        self.assertEqual([r for r in self.records() if r["op"] in ("sync_write", "write")], [])

    def test_an_interpreter_without_the_sdk_is_logged_and_the_script_exits_0(self):
        self.configure(import_error=True)
        self.render()
        result, _ = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        text = self.log_text()
        self.assertIn("NOT RUN: the feetech SDK (scservo_sdk) could not be imported", text)
        self.assertIn("safe-stop: wheel stop exited 4", text)

    def test_settings_that_are_not_absolute_paths_run_nothing(self):
        self.configure()
        render_safe_stop(self.script, self.python, HELPER, "relative/device", self.lock)
        result, _ = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("safe-stop: wheel stop NOT RUN: its four settings must all be absolute paths", self.log_text())
        self.assertEqual(self.python_calls(), [])

    def test_a_log_that_cannot_be_opened_does_not_stop_the_wheel_stop(self):
        self.configure()
        self.render()
        result, _ = self.run_script(log=os.path.join(self.tmp.name, "no-such-dir", "safe-stop.log"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(writes(self.records()), [(7, 46, [0, 0]), (8, 46, [0, 0]), (9, 46, [0, 0])])

    @unittest.skipUnless(os.path.exists("/dev/full"), "needs /dev/full")
    def test_a_log_that_opens_but_cannot_be_written_does_not_stop_the_wheel_stop(self):
        # /dev/full opens like any file and fails every write with ENOSPC: a full disk. The script's probe cannot see
        # that (it writes nothing), so the helper itself must drop its log lines and still write the wheels.
        self.configure()
        self.render()
        result, _ = self.run_script(log="/dev/full")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout + result.stderr, "", "the script printed outside its log")
        records = self.records()
        self.assertEqual([r for r in records if r["op"] == "sync_write"],
                         [{"op": "sync_write", "addr": 46, "len": 2, "params": {"7": [0, 0], "8": [0, 0], "9": [0, 0]}}])
        self.assertEqual(writes(records), [(7, 46, [0, 0]), (8, 46, [0, 0]), (9, 46, [0, 0])])
        self.assertEqual(ids_named(records), WHEEL_IDS)
        self.assertEqual(self.python_calls(), [f"-u {HELPER} --port {self.device} --lock {self.lock}"])

    @unittest.skipUnless(os.path.exists("/dev/full"), "needs /dev/full")
    def test_on_a_full_disk_the_helper_ends_with_its_own_status(self):
        # Run the way safe-stop.sh runs it (python -u, stdout and stderr into the log), with the log on /dev/full: no
        # traceback and no interpreter exit status in place of its own (0: every wheel read back goal velocity 0 in
        # velocity mode).
        self.configure()
        with open("/dev/full", "w", encoding="utf-8") as full:
            run = subprocess.run([self.python, "-u", HELPER, "--port", self.device, "--lock", self.lock], env=CAKE_ENV,
                                 cwd=self.cwd, stdin=subprocess.DEVNULL, stdout=full, stderr=full, timeout=20)
        self.assertEqual(run.returncode, 0)
        self.assertEqual(writes(self.records()), [(7, 46, [0, 0]), (8, 46, [0, 0]), (9, 46, [0, 0])])

    def test_two_runs_back_to_back_both_zero_and_both_verify(self):
        self.configure()
        self.render()
        for _ in range(2):
            result, _ = self.run_script()
            self.assertEqual(result.returncode, 0, result.stderr)
        text = self.log_text()
        self.assertEqual(text.count("safe-stop ran at "), 2)
        self.assertEqual(text.count("safe-stop: wheel stop exited 0 "), 2, text)
        self.assertEqual(len(writes(self.records())), 6)


# A stand-in for a host that has started and runs on: it records its pid, then stays up until the file $0.exit appears
# (at most 30 s). What it holds is what the wrapper's exec handed it.
STAND_IN_HOST = """#!/bin/sh
printf '%s\\n' "$$" >"$0.pid"
n=0
while [ ! -e "$0.exit" ] && [ "$n" -lt 300 ]; do sleep 0.1; n=$((n + 1)); done
exit 0
"""


@unittest.skipUnless(sys.platform.startswith("linux") and os.path.isdir("/proc/self/fd") and shutil.which("bash") and FLOCK
                     and shutil.which("timeout", path=CAKE_ENV["PATH"]), "needs Linux /proc, bash, and flock and timeout in /usr/bin:/bin")
class HostStartLock(SafeStopFixture):
    """The wheel stop against a host start. bin/templates/run-host.sh.in is rendered with this machine's flock and the
    wheel stop's own lock file, and run as Cake runs a child, armed, with stub fuser and ss that find the bus free and a
    stand-in for the host. The owner check sees only who holds the bus at one moment, and a host opens it seconds after
    its wrapper's check: the lock is what keeps a wheel stop off a bus a starting host is about to open."""

    def setUp(self):
        super().setUp()
        t = self.tmp.name
        self.token = os.path.join(t, "arm-token")
        self.probe = os.path.join(t, "at-the-owner-check")
        self.fuser, self.ss = os.path.join(t, "fuser"), os.path.join(t, "ss")
        # fuser finds no owner of the bus, and writes down whether the wheel stop had closed the port by then (its fake
        # SDK records the close); for any other path, the wrapper's own file among them, it reports an owner, as fuser does
        # for a file its caller holds open.
        with open(self.fuser, "w", encoding="utf-8") as handle:
            handle.write("#!/bin/sh\n"
                         f'case "$1" in "{self.device}") if grep -q \'"op": "close"\' "{self.record}" 2>/dev/null; then echo closed; '
                         f'else echo open; fi >"{self.probe}"; exit 1 ;; esac\nexit 0\n')
        with open(self.ss, "w", encoding="utf-8") as handle:
            handle.write("#!/bin/sh\nprintf 'LISTEN 0 4096 0.0.0.0:22 0.0.0.0:*\\n'\n")
        self.host = os.path.join(t, "stand-in-host")
        with open(self.host, "w", encoding="utf-8") as handle:
            handle.write(STAND_IN_HOST)
        for path in (self.fuser, self.ss, self.host):
            os.chmod(path, 0o755)
        self.wrapper = os.path.join(t, "run-host.sh")
        with open(HOST_TEMPLATE, encoding="utf-8") as handle:
            text = handle.read()
        values = {"PYTHON": self.host, "HOST_ENTRY": "/opt/example/lekiwi_host_safe_attach.py", "ROBOT_ID": "example_robot",
                  "ROBOT_PORT": self.device, "ARM_TOKEN": self.token, "FUSER": self.fuser, "SS": self.ss, "FLOCK": FLOCK,
                  "WHEELS_LOCK": self.lock}
        for name, value in values.items():
            text = text.replace(f"@@{name}@@", value)
        with open(self.wrapper, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.chmod(self.wrapper, 0o755)

    def arm(self):
        with open(self.token, "w", encoding="utf-8") as handle:
            handle.write("armed\n")
        os.chmod(self.token, 0o600)

    def start_host(self):
        """The armed wrapper, started as Cake starts a child and left running until the stand-in host is told to exit."""
        self.arm()
        process = subprocess.Popen([self.wrapper, *CAKE_POSITIONALS], env=CAKE_ENV, cwd=self.cwd, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        def stop():
            if process.poll() is None:
                process.kill()
            process.communicate()
        self.addCleanup(stop)
        return process

    def wait_for(self, check, what, timeout_s=10.0):
        end = time.monotonic() + timeout_s
        while not check():
            self.assertLess(time.monotonic(), end, f"{what} never happened")
            time.sleep(0.005)

    def run_stop_helper(self, budget_ms):
        """The helper itself, through the fake-SDK interpreter, with a budget of its own (no 0.42 s hard limit), left
        running; the test collects it."""
        process = subprocess.Popen([self.python, "-u", HELPER, "--port", self.device, "--lock", self.lock, "--budget-ms",
                                    str(budget_ms)], env=CAKE_ENV, cwd=self.cwd, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

        def stop():
            if process.poll() is None:
                process.kill()
            process.communicate()
        self.addCleanup(stop)
        return process

    def test_a_wheel_stop_while_a_host_that_run_host_started_lives_writes_nothing_and_writes_once_it_has_exited(self):
        # The window of verify r3: the host passed its wrapper's owner check and has not opened the bus yet (the stand-in
        # never does), so the owner check of a wheel stop that starts now sees no owner. The lock the host holds keeps it
        # off the bus all the same.
        self.configure()
        self.render()
        host = self.start_host()
        self.wait_for(lambda: os.path.exists(self.host + ".pid") and os.path.getsize(self.host + ".pid"), "the host's start")
        with open(self.host + ".pid", encoding="utf-8") as handle:
            self.assertEqual(int(handle.read()), host.pid, "the wrapper did not exec the host")
        result, _ = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        text = self.log_text()
        self.assertIn("NOT RUN: the wheel-stop lock was held for the whole wait", text)
        self.assertIn("safe-stop: wheel stop exited 3 ", text)
        self.assertEqual(self.records(), [], "the wheel stop reached the bus while a starting host held the lock")
        with open(self.host + ".exit", "w", encoding="utf-8"):
            pass
        _, err = host.communicate(timeout=10)
        self.assertEqual(host.returncode, 0, err)
        stop = self.run_stop_helper(3000)
        out, _ = stop.communicate(timeout=30)
        self.assertEqual(stop.returncode, 0, out)
        self.assertIn("DONE", out)
        self.assertEqual(writes(self.records()), [(7, 46, [0, 0]), (8, 46, [0, 0]), (9, 46, [0, 0])])

    def test_a_host_start_during_a_wheel_stop_waits_and_checks_the_bus_only_after_the_stop_closed_it(self):
        # Each bus exchange of this wheel stop takes 50 ms, so it holds the bus (and the lock) for most of a second. The
        # host start is launched while it runs; the wrapper's owner check must come after the stop closed the port.
        self.configure(reply_delay_s=0.05)
        stop = self.run_stop_helper(3000)
        self.wait_for(lambda: self.records(), "the wheel stop's first packet")
        self.assertNotIn("close", [r["op"] for r in self.records()], "the wheel stop ended before the host start was launched")
        host = self.start_host()
        self.wait_for(lambda: os.path.exists(self.host + ".pid") and os.path.getsize(self.host + ".pid"), "the host's start")
        with open(self.probe, encoding="utf-8") as handle:
            self.assertEqual(handle.read().strip(), "closed", "the wrapper checked the bus while the wheel stop still had it open")
        out, _ = stop.communicate(timeout=30)
        self.assertEqual(stop.returncode, 0, out)
        self.assertIn("DONE", out)
        self.assertEqual(writes(self.records()), [(7, 46, [0, 0]), (8, 46, [0, 0]), (9, 46, [0, 0])])
        with open(self.host + ".exit", "w", encoding="utf-8"):
            pass
        _, err = host.communicate(timeout=10)
        self.assertEqual(host.returncode, 0, err)


@unittest.skipUnless(sys.platform.startswith("linux") and os.path.isdir("/proc/self/fd"), "needs Linux /proc")
class SelfCheckProcess(unittest.TestCase):
    """--self-check run the way bin/pi/lekiwi-deploy.sh install runs it (env -i PATH=/usr/bin:/bin python -u HELPER
    --self-check), with the process's own age as its clock, against a stand-in SDK that imports quickly or slowly."""

    QUICK_SDK = "PortHandler = PacketHandler = GroupSyncWrite = object\nCOMM_SUCCESS = 0\n"

    def run_self_check(self, sdk_text):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "scservo_sdk"))
            with open(os.path.join(tmp, "scservo_sdk", "__init__.py"), "w", encoding="utf-8") as handle:
                handle.write(sdk_text)
            python = os.path.join(tmp, "python")
            with open(python, "w", encoding="utf-8") as handle:
                handle.write(f"#!/bin/sh\nexport PYTHONPATH='{tmp}'\nexec '{sys.executable}' \"$@\"\n")
            os.chmod(python, 0o755)
            run = subprocess.run([python, "-u", HELPER, "--self-check"], env=CAKE_ENV, cwd=tmp, stdin=subprocess.DEVNULL,
                                 capture_output=True, text=True, timeout=20)
        found = re.search(r"ready to open the bus at \+(\d+) ms of its age", run.stdout)
        return run, int(found.group(1)) if found else None

    def test_an_sdk_that_imports_too_slowly_for_the_budget_fails_the_self_check(self):
        # The verifier's case: an SDK import of 600 ms passed as "ok". 400 ms is past the limit whatever the machine.
        run, ready_ms = self.run_self_check("import time\ntime.sleep(0.4)\n" + self.QUICK_SDK)
        self.assertEqual(run.returncode, 1, run.stdout + run.stderr)
        self.assertIn("SELF-CHECK TOO SLOW", run.stdout)
        self.assertNotIn("SELF-CHECK ok", run.stdout)
        self.assertIsNotNone(ready_ms, run.stdout)
        self.assertGreaterEqual(ready_ms, 400, "the ready time is not this process's age")

    def test_an_sdk_that_imports_quickly_passes_the_self_check(self):
        run, ready_ms = self.run_self_check(self.QUICK_SDK)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn("SELF-CHECK ok", run.stdout)
        self.assertIsNotNone(ready_ms, run.stdout)
        self.assertEqual(run.stderr, "")


@unittest.skipUnless(shutil.which("sh"), "needs sh")
class TemplateContract(unittest.TestCase):
    def test_segment_2_needs_nothing_filled_in_and_the_wheel_settings_are_empty(self):
        with open(TEMPLATE, encoding="utf-8") as handle:
            text = handle.read()
        code = [line for line in text.splitlines() if not line.lstrip().startswith("#")]
        self.assertFalse([line for line in code if re.search(r"@@[A-Z_]+@@", line)])
        for name in ("WHEELS_PYTHON", "WHEELS_HELPER", "WHEELS_PORT", "WHEELS_LOCK"):
            self.assertEqual([line for line in code if line.startswith(name + "=")], [f"{name}=''"])
        self.assertEqual(text.splitlines()[0], "#!/bin/sh")
        self.assertEqual(code[-1], "exit 0")
        self.assertEqual(subprocess.run(["sh", "-n", TEMPLATE]).returncode, 0)


if __name__ == "__main__":
    unittest.main()
