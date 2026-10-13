#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# autonomy/tests/test_capability_pulse.py: autonomy/motion/capability_pulse.py, the relay that makes a forward base pulse
# out of the launched capability's own output, and autonomy/motion/capability_link.py, the socket and Cake-read helper it
# shares with bin/pi/approach_probe.py.
#   - UNDER ONE FAKE CLOCK, the relay's transport calls (connect, every send, every stop read, disconnect, each with its
#     time) equal those of the PROVEN forward primitive, run_hybrid_pick_place._make_move_base_sustained, when the
#     capability is the real ApproachService on contract v2: the only thing replaced is where the command comes from;
#   - the in-process MobileApproachController, patched to raise, is never constructed, and the relay's source builds no
#     velocity of its own;
#   - an inert slot, a slot on another capsule, a missing pid, an owned observation endpoint and a reverse pulse all
#     refuse before any transport exists, so the base receives nothing at all;
#   - a command of another pid, another target, another timestamp, or with vy, omega or a non-forward vx is dropped and
#     counted; a silent capability is zeroed by the lease; a generation, pid or capsule change is refused after the stop;
#   - THE STOP: a stop flag set before the Cake reads, during them or while the link waits builds no transport and sends
#     nothing; a flag that cannot be read is a stop; no relay can be built without one; a pulse already driving runs to
#     its bounded end, as the proven primitive's does;
#   - THE OBSERVATION: no relay exists without an observation source and a motion epoch; a source with nothing to give
#     (the LIVE one, target_observation_source, outside advance_base or without a perception budget) costs zero admin
#     reads, zero publications and no transport; another target, a frame that is not "anchor_reach", a base that was not
#     stationary, a changed epoch and a capture older than its own bound are each refused before any read;
#   - CONTRACT V2: every publication is the source's wire_v2 document (the eleven fields of
#     autonomy/schemas/target_observation.v2.schema.json) with published_ns the relay's clock at THAT publication, strictly
#     increasing across a pulse, and only an answer stamped with it is accepted (an answer stamped with the capture, the v1
#     shape, is dropped not_the_publication_just_made); across a ten-period pulse every accepted command is APPLIED by the
#     gateway, which v1's capture-stamped commands could not be (the gateway rejects a repeated stamp as replayed, shown
#     directly); a pulse whose commands the gateway did not apply is refused no_capability_output_applied; an observation
#     that stops qualifying mid-pulse (its own bound, not a module constant) ends the publishing, the stop sequence runs
#     and the pulse is refused. The stand-in observation these tests publish is built here, in the v2 shape, from a
#     stated remaining advance; it says nothing about any robot;
#   - the link's close() returns only once its observation endpoint is released (ZeroMQ releases it asynchronously), over
#     real Unix-domain ZeroMQ, so the endpoint checks below do not depend on the machine's load;
#   - END TO END over real Unix-domain ZeroMQ against the REAL capability process started through the rendered
#     run-capability.sh, the way Cake starts it (skipped, per method, without pyzmq or bash). Nothing here touches a device:
#     every transport is a transport.FakeBaseMotionTransport.
# Run directly: python3 autonomy/tests/test_capability_pulse.py
import ast
import inspect
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MOTION = os.path.join(ROOT, "autonomy", "motion")
PULSE_SOURCE = os.path.join(MOTION, "capability_pulse.py")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "bin", "pi"))
sys.path.insert(0, os.path.join(ROOT, "bin", "laptop", "manipulation"))
sys.path.insert(0, MOTION)

import approach_probe  # noqa: E402
import capability_link  # noqa: E402
import capability_pulse  # noqa: E402
import clock as clock_module  # noqa: E402
import lerobot_transport  # noqa: E402
import mobile_approach  # noqa: E402
import run_hybrid_pick_place  # noqa: E402
import target_observation_source as tos  # noqa: E402
from autonomy.motion import mobile_approach as packaged_mobile_approach  # noqa: E402
from autonomy.motion.mobile_approach_service import ApproachService  # noqa: E402
from clock import FakeClock, MonotonicClock  # noqa: E402
from stable_motion_gateway import StableMotionGateway  # noqa: E402
from transport import FakeBaseMotionTransport  # noqa: E402

SLOT = "5050543040508050000000000000cafe"
MOBILE, HOLDER = "b" * 64, "a" * 64
PID = 4321
TARGET = "cube_b"
OBSERVATIONS, COMMANDS = "ipc:///kc-test/approach-observations.ipc", "ipc:///kc-test/approach-commands.ipc"
START_NS = 1_000_000_000
VX = 0.05  # run_single_cube_cycle.BASE_VX_MPS: the commanded speed of the in-process primitive run on hardware; qualifies nothing about the relay
NEED_M = 0.065  # the stand-in's remaining advance (x_m): a stated number for these tests, beyond the executor's 8 mm tolerance and inside its 120 mm bound
DEADLINE_S = 60.0  # the stand-in's perception budget, from which the observation's own bound follows (target_observation_source.max_age_ns_for)
MAX_AGE_NS = tos.max_age_ns_for(DEADLINE_S)
OBSERVATION_ID = "obs-20260925T000000Z-wrist-0001"
try:
    import zmq  # noqa: F401
    HAVE_ZMQ = True
except ImportError:
    HAVE_ZMQ = False


def listing(generation=2, state="ACTIVE", capsule=MOBILE, pid=PID, slot=SLOT):
    slots = (f"status ok\nslot capsule={capsule} config={capsule} generation={generation} instance=7 name=mobile_approach "
             f"state={state} uuid={slot}\n")
    health = (f"status ok\nhealth arg0=6 arg1={pid} code=0 flags=1 sequence=2 slot={slot} state=1 state_name=HEALTHY "
              f"surface=reported\n")
    return {"list-slots": slots, "get-health": health}


class Admin:
    """admin-probe's two reads, scripted per snapshot: the Nth snapshot reads the Nth answer, then the last one."""

    def __init__(self, *answers):
        self.answers, self.reads = list(answers) or [listing()], []

    def read(self, op):
        self.reads.append(op)
        index = min(self.reads.count("list-slots") - 1, len(self.answers) - 1)
        return self.answers[max(index, 0)][op]


class FakeTime:
    """time.perf_counter and time.sleep over one FakeClock, so both primitives run on the same fake time."""

    def __init__(self, clock):
        self.clock = clock

    def perf_counter(self):
        return self.clock.now_ns() / 1e9

    def sleep(self, seconds):
        self.clock.advance(round(seconds * 1e9))


class ReadingClient:
    def __init__(self, transport, readings):
        self.transport, self.readings = transport, list(readings)

    def get_observation(self):
        self.transport.log.append((self.transport.clock.now_ns(), "read"))
        vx, vy, theta = self.readings.pop(0) if self.readings else (0.0, 0.0, 0.0)
        return {"x.vel": vx, "y.vel": vy, "theta.vel": theta}


class RecordingTransport(FakeBaseMotionTransport):
    """FakeBaseMotionTransport with the lifecycle of LeKiwiTransport (connect, disconnect, the client the stop reads go
    through), every call logged in order with the fake time it happened at."""

    def __init__(self, log, clock, readings=()):
        super().__init__(connected=False)
        self.log, self.clock = log, clock
        self._client = ReadingClient(self, readings)

    def connect(self):
        self.log.append((self.clock.now_ns(), "connect"))
        self.set_connected(True)

    def disconnect(self):
        self.log.append((self.clock.now_ns(), "disconnect"))
        self.set_connected(False)

    def send_velocity(self, vx_mps, vy_mps, omega_rps):
        super().send_velocity(vx_mps, vy_mps, omega_rps)
        self.log.append((self.clock.now_ns(), "velocity", vx_mps, vy_mps, omega_rps))

    def send_zero(self):
        super().send_zero()
        self.log.append((self.clock.now_ns(), "zero"))


class ServiceCapability:
    """The REAL capability loop, mobile_approach_service.ApproachService on contract v2 (as bin/templates/run-capability.sh.in
    launches it), labelled as the wrapper Cake runs labels it."""

    def __init__(self, pid=PID):
        self.inbox = []
        self.outbox = []
        self.service = ApproachService(lambda _timeout: self.inbox.pop(0) if self.inbox else None, self.outbox.append,
                                       identity=f"mobile_approach.pid{pid}", contract="v2")

    def __call__(self, payload):
        self.inbox.append(payload)
        self.service.step()
        answers, self.outbox[:] = list(self.outbox), []
        return answers


class ScriptedCapability:
    """Answers observation N with answer(observation, N): a document, or None for silence. No controller anywhere."""

    def __init__(self, answer):
        self.answer, self.count = answer, 0

    def __call__(self, payload):
        observation = json.loads(payload.decode("utf-8"))
        document = self.answer(observation, self.count)
        self.count += 1
        return [] if document is None else [document if isinstance(document, bytes) else json.dumps(document).encode("utf-8")]


def forward_command(observation, n, **overrides):
    """The v2 capability's answer shape: timestamp_ns == the observation's published_ns, expiry 250 ms after it."""
    stamp = observation["published_ns"]
    document = {"command_id": f"mobile_approach.pid{PID}.cmd_{n + 1}", "target_id": observation["target_id"],
                "timestamp_ns": stamp, "vx_mps": 0.10, "vy_mps": 0.0, "omega_rps": 0.0, "expiry_ns": stamp + 250_000_000}
    document.update(overrides)
    return document


def v2_source(target_id, clock, motion_epoch, *, capture_ns=None, max_age_ns=MAX_AGE_NS):
    """observe() -> a v2 stand-in observation of `target_id`: captured at `capture_ns` (the clock's now at the first call
    when None), carrying the caller's epoch and the stated bound. A documentation stand-in for the executor's source: it
    states a remaining advance, it measures nothing."""
    stamps = {"capture": capture_ns}

    def observe():
        if stamps["capture"] is None:
            stamps["capture"] = clock.now_ns()
        return tos.ObservedTarget(target_id=target_id, x_m=NEED_M, capture_monotonic_ns=stamps["capture"], motion_epoch=motion_epoch(),
                                  observation_id=OBSERVATION_ID, max_age_ns=max_age_ns, stationary=True)

    return observe


class FakeLink:
    def __init__(self, capability):
        self.capability, self.queue, self.published, self.closed = capability, [], [], False

    def publish(self, payload):
        self.published.append(json.loads(payload.decode("utf-8")))
        self.queue.extend(self.capability(payload))

    def receive(self, timeout_ms):
        return self.queue.pop(0) if self.queue and timeout_ms > 0 else None

    def close(self):
        self.closed = True
        return True  # capability_link.CapabilityLink.close: the observation endpoint was released


class Rig:
    """One relay over one fake clock, one scripted admin, one fake link and recording transports. Unless a test hands it
    another `observe`, the relay publishes the v2 stand-in observation (v2_source), captured at the Rig's start."""

    def __init__(self, capability, admin=None, readings=(), listens=False, exists=True, target_id=TARGET, observe=None):
        self.clock = FakeClock(START_NS)
        self.time = FakeTime(self.clock)
        self.log, self.transports, self.links = [], [], []
        self.admin = admin or Admin()
        self.capability = capability
        self.readings = readings
        self.stop = {"set": False}  # the executor's stop flag, as run_single_cube_cycle hands its own to the relay
        self.epoch = {"value": 0}  # the executor's motion epoch, as run_single_cube_cycle hands its own to the relay
        self.observed = []  # every call the relay made to its source, and what it got
        source = observe or v2_source(target_id, self.clock, lambda: self.epoch["value"])

        def observing():
            self.observed.append(self.clock.now_ns())
            return source()

        def transport_factory():
            transport = RecordingTransport(self.log, self.clock, self.readings)
            self.transports.append(transport)
            return transport

        def open_link(observations, commands, timeout_s):
            link = FakeLink(self.capability)
            self.links.append(link)
            return link

        self.pulse = capability_pulse.CapabilityPulse(
            read=self.admin.read, slot_uuid=SLOT, capsule=MOBILE, observations=OBSERVATIONS, commands=COMMANDS,
            transport_factory=transport_factory, target_id=target_id, should_stop=lambda: self.stop["set"], observe=observing,
            motion_epoch=lambda: self.epoch["value"], clock=self.clock, sleep=self.time.sleep, open_link=open_link,
            listens=lambda path: listens, exists=lambda path: exists)

    def velocities(self):
        return [entry for entry in self.log if entry[1] == "velocity"]


def proven_forward(duration_s, readings=()):
    """The PROVEN forward primitive, unchanged, with only its transport, its clock and its time module replaced."""
    clock = FakeClock(START_NS)
    fake_time, log = FakeTime(clock), []
    with mock.patch.object(lerobot_transport, "LeKiwiTransport", lambda remote_ip, robot_id: RecordingTransport(log, clock, readings)), \
            mock.patch.object(clock_module, "MonotonicClock", lambda: clock), \
            mock.patch.object(run_hybrid_pick_place, "time", fake_time):
        stopped = run_hybrid_pick_place._make_move_base_sustained("synthetic-remote", "synthetic-unit")(VX, duration_s)
    return stopped, log


class TheSameTransportCallsAsTheProvenPrimitive(unittest.TestCase):
    READINGS = ((0.05, 0.0, 0.0), (0.02, 0.0, 0.0), (0.0, 0.0, 0.0))

    def test_under_one_fake_clock_every_transport_call_and_its_time_are_the_proven_primitives(self):
        for duration_s in (0.2, 0.5, 0.8):  # run_single_cube_cycle.BASE_PULSE_S and the 0.5 s the in-process primitive ran on hardware
            with self.subTest(duration_s=duration_s):
                stopped, proven = proven_forward(duration_s, self.READINGS)
                rig = Rig(ServiceCapability(), readings=self.READINGS)
                self.assertTrue(stopped)
                self.assertTrue(rig.pulse.move_base(VX, duration_s))
                self.assertIsNone(rig.pulse.last_refusal, rig.pulse.last_evidence)
                self.assertEqual(rig.log, proven)
                velocities = [entry for entry in proven if entry[1] == "velocity"]
                self.assertGreaterEqual(len(velocities), 3, "the proven pulse sent almost nothing: nothing was compared")
                self.assertEqual({entry[2:] for entry in velocities}, {(VX, 0.0, 0.0)})
                self.assertEqual([entry[1] for entry in proven[-6:]], ["zero", "read", "read", "read", "zero", "disconnect"])
                evidence = rig.pulse.last_evidence
                self.assertEqual(evidence["commands_accepted"], len(velocities))
                self.assertEqual(evidence["commands_applied"], len(velocities), "the real v2 service's commands were accepted but not applied")
                self.assertEqual(evidence["observations_published"], len(velocities))
                self.assertEqual(evidence["published_stamps"], len(velocities), "two publications shared a stamp")
                self.assertEqual(evidence["gateway_outcomes"], {"APPLIED": len(velocities)})
                self.assertEqual(evidence["commands_dropped"], {})
                self.assertEqual((evidence["slot_generation"], evidence["producer_pid"], evidence["contract"]), (2, PID, "kc.target_observation/2"))

    def test_an_unconfirmed_stop_is_reported_as_the_proven_primitive_reports_it(self):
        moving = [(0.05, 0.0, 0.0)] * 6
        stopped, proven = proven_forward(0.2, moving)
        rig = Rig(ServiceCapability(), readings=moving)
        self.assertFalse(stopped)
        self.assertFalse(rig.pulse.move_base(VX, 0.2))
        self.assertEqual(rig.log, proven)
        self.assertEqual(sum(1 for entry in rig.log if entry[1] == "read"), 6)


class NoInProcessController(unittest.TestCase):
    def test_the_controller_patched_to_raise_is_never_constructed(self):
        def refuse(*_args, **_kwargs):
            raise AssertionError("an in-process MobileApproachController was constructed")

        rig = Rig(ScriptedCapability(forward_command))
        with mock.patch.object(mobile_approach, "MobileApproachController", refuse), \
                mock.patch.object(packaged_mobile_approach, "MobileApproachController", refuse):
            self.assertTrue(rig.pulse.move_base(VX, 0.5))
        self.assertIsNone(rig.pulse.last_refusal)
        self.assertEqual({entry[2:] for entry in rig.velocities()}, {(VX, 0.0, 0.0)})
        self.assertEqual(rig.pulse.last_evidence["commands_accepted"], len(rig.velocities()))

    def test_its_source_names_no_controller_and_builds_no_velocity_of_its_own(self):
        with open(PULSE_SOURCE, encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} | {
            alias.name for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom)) for alias in node.names}
        modules = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
        self.assertNotIn("MobileApproachController", names)
        imported = modules | {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
        self.assertFalse({"run_hybrid_pick_place", "lerobot_transport", "transport"} & imported, imported)
        built = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "DesiredBaseMotion"]
        self.assertEqual(len(built), 1, "the relay constructs a DesiredBaseMotion other than the one it received")
        self.assertEqual([keyword.arg for keyword in built[0].keywords], [None], "that one is not built from the received document alone")
        replaced = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "replace"]
        self.assertEqual([[keyword.arg for keyword in node.keywords] for node in replaced], [["expiry_ns"]])

    def test_importing_the_relay_loads_no_lerobot_and_no_real_transport(self):
        code = ("import sys; sys.path.insert(0, sys.argv[1]); import capability_pulse; "
                "bad = sorted(m for m in sys.modules if m == 'lerobot' or m.startswith('lerobot.') or m in "
                "('lerobot_transport', 'run_hybrid_pick_place', 'zmq')); print(repr(bad)); sys.exit(1 if bad else 0)")
        done = subprocess.run([sys.executable, "-c", code, MOTION], capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(done.stdout.strip(), "[]")


class RefusedBeforeAnyTransportExists(unittest.TestCase):
    def assert_nothing_moved(self, rig, code):
        self.assertIs(rig.pulse.move_base(VX, 0.5), False, "a refusal before motion confirmed a stop it never read")
        self.assertEqual(rig.pulse.last_refusal, code)
        self.assertEqual(rig.pulse.last_evidence["refusal"], code)
        self.assertEqual(rig.transports, [], "a transport was built")
        self.assertEqual(rig.log, [])
        self.assertEqual(rig.links, [], "the observation endpoint was bound")
        self.assertFalse(rig.pulse.last_evidence["commanded"])

    def test_an_inert_slot_gives_zero_velocity(self):
        for name, answer, code in (
                ("inert", listing(state="INERT"), "capability_not_active"),
                ("on the holder capsule", listing(capsule=HOLDER), "capability_not_active"),
                ("absent", listing(slot="f" * 32), "capability_not_active"),
                ("no launched pid", listing(pid=0), "capability_pid_absent"),
                ("admin read not ok", {"list-slots": "status refused\n", "get-health": "status refused\n"}, "capability_not_active")):
            with self.subTest(name):
                self.assert_nothing_moved(Rig(ScriptedCapability(forward_command), admin=Admin(answer)), code)

    def test_an_admin_read_that_raises_is_a_refusal(self):
        rig = Rig(ScriptedCapability(forward_command))
        rig.pulse._read = mock.Mock(side_effect=subprocess.TimeoutExpired("admin-probe", 1.0))
        self.assert_nothing_moved(rig, "admin_read_failed")
        self.assertEqual(rig.pulse.last_evidence["refusal_detail"], "TimeoutExpired")

    def test_an_owned_observation_endpoint_and_a_missing_command_endpoint_are_left_alone(self):
        self.assert_nothing_moved(Rig(ScriptedCapability(forward_command), listens=True), "observation_endpoint_owned")
        self.assert_nothing_moved(Rig(ScriptedCapability(forward_command), exists=False), "capability_command_endpoint_absent")

    def test_a_capability_that_never_subscribes_is_a_refusal(self):
        rig = Rig(ScriptedCapability(forward_command))

        def never(*_):
            raise capability_link.Refused("the capability never subscribed to the observation endpoint within the timeout")

        rig.pulse._open_link = never
        self.assert_nothing_moved(rig, "capability_link_refused")

    def test_a_reverse_or_zero_pulse_is_not_this_relays_and_reads_nothing(self):
        for vx in (-VX, 0.0, float("nan"), True):
            with self.subTest(vx=vx):
                rig = Rig(ScriptedCapability(forward_command))
                self.assertIs(rig.pulse.move_base(vx, 0.5), False)
                self.assertEqual(rig.pulse.last_refusal, "not_a_forward_pulse")
                self.assertEqual((rig.admin.reads, rig.transports, rig.links), ([], [], []))

    def test_a_tcp_endpoint_is_refused(self):
        rig = Rig(ScriptedCapability(forward_command))
        rig.pulse.observations = "tcp://*:5599"
        self.assert_nothing_moved(rig, "endpoint_not_ipc")


class TheRelayRefusesEveryYaw(unittest.TestCase):
    """T6 step 3c (base yaw, designed and not built): pinned by name. The v2 relay drops a command with omega != 0 and with
    vy != 0 (not_straight_ahead), so no yaw and no lateral can reach the base through the capability path without a
    contract v3; the ruling on the command path comes first."""

    def test_omega_and_vy_are_refused_and_the_limits_clamp_them_at_the_gateway(self):
        for name in ("a yaw omega", "a lateral vy"):
            self.assertEqual("not_straight_ahead", DroppedAnswers.CASES[name][1])
        self.assertEqual(0.01, capability_pulse.OMEGA_MAX_RPS)
        self.assertEqual(0.05, capability_pulse.VY_MAX_MPS)


class DroppedAnswers(unittest.TestCase):
    CASES = {
        "another pid": (lambda o, n: forward_command(o, n, command_id=f"mobile_approach.pid{PID + 1}.cmd_{n}"), "command_not_from_launched_process"),
        "a pid prefix of this one": (lambda o, n: forward_command(o, n, command_id=f"mobile_approach.pid{PID // 10}.cmd_{n}"), "command_not_from_launched_process"),
        "another capability's label": (lambda o, n: forward_command(o, n, command_id=f"hybrid_pick_place.pid{PID}.cmd_{n}"), "command_not_from_launched_process"),
        "another target": (lambda o, n: forward_command(o, n, target_id="cube_a"), "target_id_mismatch"),
        "an earlier timestamp": (lambda o, n: forward_command(o, n, timestamp_ns=o["published_ns"] - 1), "not_the_publication_just_made"),
        "a later timestamp": (lambda o, n: forward_command(o, n, timestamp_ns=o["published_ns"] + 1), "not_the_publication_just_made"),
        "a lateral vy": (lambda o, n: forward_command(o, n, vy_mps=0.01), "not_straight_ahead"),
        "a yaw omega": (lambda o, n: forward_command(o, n, omega_rps=0.01), "not_straight_ahead"),
        "a zero vx": (lambda o, n: forward_command(o, n, vx_mps=0.0), "vx_not_forward"),
        "a reverse vx": (lambda o, n: forward_command(o, n, vx_mps=-0.10), "vx_not_forward"),
        "a vx that is not a number": (lambda o, n: forward_command(o, n, vx_mps="0.1"), "not_finite"),
        "an added field": (lambda o, n: forward_command(o, n, frame="base"), "not_a_desired_base_motion"),
        "not json": (lambda o, n: b"\xff not json", "not_json"),
    }

    def test_every_defect_is_dropped_counted_and_the_pulse_is_refused_after_its_stop(self):
        for name, (answer, reason) in self.CASES.items():
            with self.subTest(name):
                rig = Rig(ScriptedCapability(answer))
                stopped = rig.pulse.move_base(VX, 0.5)
                self.assertEqual(rig.velocities(), [], "a dropped answer reached the base")
                evidence = rig.pulse.last_evidence
                self.assertEqual(evidence["commands_dropped"], {reason: evidence["observations_published"]})
                self.assertEqual((evidence["commands_accepted"], evidence["commands_applied"]), (0, 0))
                self.assertEqual(rig.pulse.last_refusal, "no_capability_output_applied")
                self.assertTrue(stopped, "the stop sequence still runs and reads zero")
                self.assertEqual([entry[1] for entry in rig.log], ["connect", "zero", "read", "zero", "disconnect"])

    def test_one_bad_answer_among_good_ones_is_dropped_and_the_rest_drive(self):
        rig = Rig(ScriptedCapability(lambda o, n: forward_command(o, n, target_id="cube_a") if n == 2 else forward_command(o, n)))
        self.assertTrue(rig.pulse.move_base(VX, 0.5))
        evidence = rig.pulse.last_evidence
        self.assertEqual(evidence["commands_dropped"], {"target_id_mismatch": 1})
        self.assertEqual(evidence["commands_accepted"], evidence["observations_published"] - 1)
        self.assertIsNone(rig.pulse.last_refusal)

    def test_the_expiry_is_the_earlier_of_the_capabilitys_and_the_proven_lease(self):
        submitted = []

        class Spy(StableMotionGateway):
            def submit_command(self, command):
                submitted.append(command)
                return super().submit_command(command)

        for ttl_ms, expected_ms in ((250, 150), (100, 100)):
            with self.subTest(ttl_ms=ttl_ms):
                submitted.clear()
                rig = Rig(ScriptedCapability(lambda o, n: forward_command(o, n, expiry_ns=o["published_ns"] + ttl_ms * 1_000_000)))
                with mock.patch.object(capability_pulse, "StableMotionGateway", Spy):
                    self.assertTrue(rig.pulse.move_base(VX, 0.2))
                self.assertTrue(submitted)
                self.assertEqual({c.expiry_ns - c.timestamp_ns for c in submitted}, {expected_ms * 1_000_000})


class SilenceAndChange(unittest.TestCase):
    def test_a_silent_capability_is_zeroed_by_the_lease(self):
        rig = Rig(ScriptedCapability(lambda o, n: forward_command(o, n) if n < 3 else None))
        self.assertTrue(rig.pulse.move_base(VX, 0.8))
        velocities = rig.velocities()
        self.assertEqual(len(velocities), 3)
        last_velocity_ns = velocities[-1][0]
        after = [entry for entry in rig.log if entry[0] > last_velocity_ns or (entry[0] == last_velocity_ns and entry[1] != "velocity")]
        self.assertEqual(after[0][1], "zero", "the base was not zeroed after the capability went silent")
        lease_ns = capability_pulse.COMMAND_TTL_MS * 1_000_000
        self.assertGreaterEqual(after[0][0], last_velocity_ns + lease_ns, "zeroed before its lease ran out: not the lease")
        self.assertLessEqual(after[0][0], last_velocity_ns + lease_ns + round(capability_pulse.REFRESH_INTERVAL_S * 1e9))
        stop_ns = START_NS + 10 * round(capability_pulse.REFRESH_INTERVAL_S * 1e9)  # 0.8 s: ten periods
        self.assertLess(after[0][0], stop_ns, "the zero came from the stop sequence, not from the lease")
        self.assertEqual(rig.pulse.last_evidence["gateway_outcomes"], {"APPLIED": 3})
        self.assertIsNone(rig.pulse.last_refusal, "a capability that answered and then stopped answering was used and is not a refusal")

    def test_a_change_of_generation_pid_or_capsule_is_refused_after_the_stop(self):
        for name, after in (("generation", listing(generation=3)), ("pid", listing(pid=PID + 7)),
                            ("capsule", listing(capsule=HOLDER)), ("state", listing(state="INERT"))):
            with self.subTest(name):
                rig = Rig(ScriptedCapability(forward_command), admin=Admin(listing(), after))
                stopped = rig.pulse.move_base(VX, 0.5)
                self.assertTrue(stopped, "the refusal replaced the stop confirmation that was really read")
                self.assertEqual(rig.pulse.last_refusal, "capability_changed_during_pulse")
                self.assertEqual([entry[1] for entry in rig.log[-4:]], ["zero", "read", "zero", "disconnect"],
                                 "the stop sequence did not complete before the refusal")
                self.assertEqual(rig.admin.reads, ["list-slots", "get-health", "list-slots", "get-health"])
                self.assertTrue(rig.links[0].closed)

    def test_an_unchanged_capability_is_not_a_refusal_and_the_evidence_holds_only_ids_and_numbers(self):
        rig = Rig(ScriptedCapability(forward_command))
        self.assertTrue(rig.pulse.move_base(VX, 0.5))
        self.assertIsNone(rig.pulse.last_refusal)
        evidence = rig.pulse.last_evidence
        self.assertEqual(evidence["cake_before"], evidence["cake_after"])
        self.assertEqual(rig.pulse.pulses, [evidence])
        self.assertEqual(evidence["contract"], tos.CONTRACT)
        self.assertEqual(set(evidence["observation_evidence"]), {"anchor_pad_m", "axis_pad", "need_mm", "landmark_inliers", "reprojection_rms_px", "max_age_ns", "stationary"})
        text = json.dumps(evidence).replace(tos.CONTRACT, "")  # the contract word is the one slash the evidence carries
        self.assertNotIn("/", text, "the evidence names a path")

    def test_every_publication_is_the_sources_observation_for_the_mission_target(self):
        """What is published is the source's v2 document, one per period, each with the relay's clock as published_ns: the
        relay adds nothing of its own to what the source gave but that stamp."""
        rig = Rig(ScriptedCapability(forward_command))
        rig.pulse.move_base(VX, 0.5)
        published = rig.links[0].published
        self.assertTrue(published)
        stamps = [p.pop("published_ns") for p in published]
        self.assertEqual(stamps, sorted(set(stamps)))
        self.assertEqual([START_NS + n * round(capability_pulse.REFRESH_INTERVAL_S * 1e9) for n in range(len(stamps))], stamps, "not the relay's clock at each period")
        expected = tos.ObservedTarget(target_id=TARGET, x_m=NEED_M, capture_monotonic_ns=START_NS, motion_epoch=0, observation_id=OBSERVATION_ID,
                                      max_age_ns=MAX_AGE_NS, stationary=True).wire_v2(0)
        expected.pop("published_ns")
        self.assertEqual({json.dumps(p, sort_keys=True) for p in published}, {json.dumps(expected, sort_keys=True)})
        self.assertEqual(rig.pulse.last_evidence["observation"]["observation_id"], OBSERVATION_ID)
        self.assertEqual(rig.pulse.last_evidence["published_stamps"], len(stamps))

    def test_an_error_mid_pulse_still_zeroes_disconnects_and_closes_the_link(self):
        rig = Rig(ScriptedCapability(lambda o, n: forward_command(o, n) if n < 2 else b""))

        def broken(payload):
            if len(rig.links[0].published) >= 3:
                raise OSError("the link broke")
            FakeLink.publish(rig.links[0], payload)

        original = rig.pulse._open_link

        def open_link(*args):
            link = original(*args)
            link.publish = broken
            return link

        rig.pulse._open_link = open_link
        with self.assertRaises(OSError):
            rig.pulse.move_base(VX, 0.5)
        self.assertEqual([entry[1] for entry in rig.log[-2:]], ["zero", "disconnect"])
        self.assertTrue(rig.links[0].closed)


class TheStopBeforeMotion(unittest.TestCase):
    """The relay reads the caller's stop flag again at every point where its pulse could still start without motion: the
    admin reads and the link's wait take seconds on the robot, and a stop that arrived during them must start nothing."""

    def assert_stopped_before_motion(self, rig, code=capability_pulse.STOPPED_BEFORE_MOTION):
        self.assertIs(rig.pulse.move_base(VX, 0.5), False, "a pulse refused on the stop confirmed a stop it never read")
        self.assertEqual((rig.pulse.last_refusal, rig.pulse.last_evidence["refusal"]), (code, code))
        self.assertEqual(rig.transports, [], "a transport was built after the stop")
        self.assertEqual(rig.log, [])
        self.assertFalse(rig.pulse.last_evidence["commanded"])
        self.assertTrue(all(link.closed and not link.published for link in rig.links), "an observation was published, or the link was left open")

    def test_a_stop_before_the_call_reads_nothing_and_opens_nothing(self):
        rig = Rig(ScriptedCapability(forward_command))
        rig.stop["set"] = True
        self.assert_stopped_before_motion(rig)
        self.assertEqual((rig.admin.reads, rig.links), ([], []))

    def test_a_stop_during_the_cake_reads_opens_no_link(self):
        rig = Rig(ScriptedCapability(forward_command))
        read = rig.admin.read

        def slow_read(op):  # the operator's stop arrives while admin-probe answers
            if op == "get-health":
                rig.stop["set"] = True
            return read(op)

        rig.pulse._read = slow_read
        self.assert_stopped_before_motion(rig)
        self.assertEqual((rig.admin.reads, rig.links), (["list-slots", "get-health"], []))

    def test_a_stop_while_the_link_waits_builds_no_transport_and_closes_the_link(self):
        rig = Rig(ScriptedCapability(forward_command))
        opened = rig.pulse._open_link

        def open_link(*args):  # the operator's stop arrives while the link waits for the capability's subscription
            link = opened(*args)
            rig.stop["set"] = True
            return link

        rig.pulse._open_link = open_link
        self.assert_stopped_before_motion(rig)
        self.assertEqual(1, len(rig.links))
        self.assertTrue(rig.pulse.last_evidence["observation_endpoint_released"])

    def test_a_stop_flag_that_cannot_be_read_is_a_stop(self):
        rig = Rig(ScriptedCapability(forward_command))
        rig.pulse._should_stop = mock.Mock(side_effect=NameError("the executor's flag does not exist yet"))
        self.assert_stopped_before_motion(rig, capability_pulse.STOP_FLAG_UNREADABLE)
        self.assertEqual(rig.admin.reads, [])

    def test_no_relay_exists_without_a_stop_flag(self):
        arguments = dict(read=Admin().read, slot_uuid=SLOT, capsule=MOBILE, observations=OBSERVATIONS, commands=COMMANDS,
                         transport_factory=lambda: None, target_id=TARGET, observe=lambda: None, motion_epoch=lambda: 0)
        with self.assertRaises(TypeError):
            capability_pulse.CapabilityPulse(**arguments)
        with self.assertRaises(ValueError):
            capability_pulse.CapabilityPulse(**arguments, should_stop=None)

    def test_a_pulse_already_driving_runs_to_its_bounded_end_as_the_proven_primitive_does(self):
        """Not cut and not split: once a transport exists, a stop changes nothing in the pulse. The executor's re-observation
        then measures where the base stopped, and its own flag check refuses the next pulse."""
        undisturbed = Rig(ServiceCapability(), readings=TheSameTransportCallsAsTheProvenPrimitive.READINGS)
        self.assertTrue(undisturbed.pulse.move_base(VX, 0.5))
        rig = Rig(ServiceCapability(), readings=TheSameTransportCallsAsTheProvenPrimitive.READINGS)
        factory = rig.pulse._transport_factory

        def building():
            transport = factory()
            rig.stop["set"] = True
            return transport

        rig.pulse._transport_factory = building
        self.assertTrue(rig.pulse.move_base(VX, 0.5))
        self.assertIsNone(rig.pulse.last_refusal)
        self.assertEqual(undisturbed.log, rig.log)
        self.assertIs(rig.pulse.move_base(VX, 0.5), False)
        self.assertEqual(rig.pulse.last_refusal, capability_pulse.STOPPED_BEFORE_MOTION)
        self.assertEqual(1, len(rig.transports))

    def test_an_endpoint_the_link_did_not_release_is_recorded_and_is_not_a_refusal_of_this_pulse(self):
        rig = Rig(ScriptedCapability(forward_command))
        opened = rig.pulse._open_link

        def open_link(*args):
            link = opened(*args)
            link.close = lambda: False
            return link

        rig.pulse._open_link = open_link
        self.assertTrue(rig.pulse.move_base(VX, 0.5))
        self.assertIsNone(rig.pulse.last_refusal)
        self.assertIs(rig.pulse.last_evidence["observation_endpoint_released"], False)


def observed(**changes):
    """A v2 ObservedTarget of TARGET captured at the Rig's start at epoch 0, with `changes` (a documentation stand-in: a stated
    remaining advance, measured by nothing)."""
    fields = dict(target_id=TARGET, x_m=NEED_M, capture_monotonic_ns=START_NS, motion_epoch=0, observation_id=OBSERVATION_ID,
                  max_age_ns=MAX_AGE_NS, stationary=True, run_id="console-20260925T000000Z-00000000")
    fields.update(changes)
    return tos.ObservedTarget(**fields)


class TheObservation(unittest.TestCase):
    """What the relay publishes is the source's, and only an observation that qualifies NOW is published at all."""

    def assert_refused_before_any_read(self, rig, code):
        self.assertIs(rig.pulse.move_base(VX, 0.5), False)
        self.assertEqual((rig.pulse.last_refusal, rig.pulse.last_evidence["refusal"]), (code, code))
        self.assertEqual(rig.admin.reads, [], "the capability was read for a pulse that had nothing to publish")
        self.assertEqual((rig.transports, rig.links, rig.log), ([], [], []), "a transport or a link was made")
        self.assertEqual(rig.pulse.last_evidence["observations_published"], 0)
        self.assertFalse(rig.pulse.last_evidence["commanded"])

    def test_no_relay_exists_without_an_observation_source_or_a_motion_epoch(self):
        arguments = dict(read=Admin().read, slot_uuid=SLOT, capsule=MOBILE, observations=OBSERVATIONS, commands=COMMANDS,
                         transport_factory=lambda: None, target_id=TARGET, should_stop=lambda: False)
        with self.assertRaises(TypeError):
            capability_pulse.CapabilityPulse(**arguments, motion_epoch=lambda: 0)
        with self.assertRaises(TypeError):
            capability_pulse.CapabilityPulse(**arguments, observe=lambda: None)
        for name in ("observe", "motion_epoch"):
            with self.subTest(name), self.assertRaises(ValueError):
                capability_pulse.CapabilityPulse(**{**arguments, "observe": lambda: None, "motion_epoch": lambda: 0, name: None})

    def test_the_live_source_outside_an_advance_and_without_a_budget_gives_nothing_and_the_pulse_reads_publishes_and_builds_nothing(self):
        """target_observation_source.resolve on a capture exactly as the controller stores it (stamped, stationary), fresh on
        the relay's clock, but with no approach state (the controller is not inside advance_base) and no perception budget
        (a run whose perception runs on this computer): no observation, and every reason is named."""
        rig = Rig(ScriptedCapability(forward_command))
        record = {"observation_id": "obs-1", "capture_monotonic_ns": START_NS, "motion_epoch": 0, "stationary": True, "camera_pose": {"status": "UNAVAILABLE"}}
        rig.pulse._observe = lambda: tos.resolve(None, record, object(), rig.clock.now_ns(), perception_deadline_s=None, target_id=TARGET)
        self.assert_refused_before_any_read(rig, capability_pulse.OBSERVATION_UNAVAILABLE)
        self.assertEqual(rig.pulse.last_evidence["refusal_detail"].split(","), [tos.REASON_APPROACH_NOT_MEASURED, tos.REASON_PERCEPTION_BUDGET_UNKNOWN])

    def test_a_source_that_breaks_is_no_observation(self):
        rig = Rig(ScriptedCapability(forward_command), observe=mock.Mock(side_effect=KeyError("broken")))
        self.assert_refused_before_any_read(rig, capability_pulse.OBSERVATION_UNAVAILABLE)
        self.assertEqual(rig.pulse.last_evidence["refusal_detail"], "KeyError")

    def test_another_target_another_frame_a_moving_base_a_changed_epoch_and_a_stale_capture_are_each_refused_before_any_read(self):
        bound = 200_000_000  # a short bound of the observation's own, so that "older than its bound" fits before the Rig's start
        for name, source, code in (
                ("another target", lambda: observed(target_id="cube_a"), tos.PULSE_TARGET_MISMATCH),
                ("the v1 frame", lambda: observed(frame="base"), tos.PULSE_FRAME_NOT_ANCHOR_REACH),
                ("a base that was not stationary", lambda: observed(stationary=False), tos.PULSE_NOT_STATIONARY),
                ("a changed epoch", lambda: observed(motion_epoch=1), tos.PULSE_EPOCH_CHANGED),
                ("a capture older than its own bound", lambda: observed(max_age_ns=bound, capture_monotonic_ns=START_NS - bound - 1), tos.PULSE_STALE),
                ("a capture from the future", lambda: observed(capture_monotonic_ns=START_NS + 1), tos.PULSE_STALE),
                ("not an observation", lambda: observed().wire_v2(START_NS), tos.PULSE_NOT_AN_OBSERVATION)):
            with self.subTest(name):
                self.assert_refused_before_any_read(Rig(ScriptedCapability(forward_command), observe=source), code)
        # exactly at its own bound it still qualifies (<=), not a stricter one
        self.assertIsNone(tos.check_for_pulse(observed(max_age_ns=bound, capture_monotonic_ns=START_NS - bound), target_id=TARGET, motion_epoch=0, now_ns=START_NS))

    def test_an_epoch_that_cannot_be_read_is_no_fresh_scene(self):
        rig = Rig(ScriptedCapability(forward_command))
        rig.pulse._motion_epoch = mock.Mock(side_effect=AttributeError("no cycle yet"))
        self.assert_refused_before_any_read(rig, capability_pulse.MOTION_EPOCH_UNREADABLE)
        self.assertEqual(rig.observed, [], "the source was asked before the epoch it must match was known")

    def test_the_published_stamp_is_the_relays_clock_and_only_an_answer_to_it_is_accepted(self):
        capture = START_NS - 800_000_000  # 0.8 s old when the pulse starts: older than v1's 200 ms, inside its own bound, and never a publication stamp
        rig = Rig(ScriptedCapability(forward_command), observe=lambda: observed(capture_monotonic_ns=capture))
        self.assertTrue(rig.pulse.move_base(VX, 0.8))  # ten periods
        self.assertIsNone(rig.pulse.last_refusal, rig.pulse.last_evidence)
        published = rig.links[0].published
        self.assertEqual(10, len(published))
        self.assertEqual({p["capture_ns"] for p in published}, {capture}, "a publication carried a capture stamp that is not the capture's")
        stamps = [p["published_ns"] for p in published]
        self.assertEqual(stamps, [START_NS + n * 80_000_000 for n in range(10)], "published_ns is not the relay's clock at each publication")
        self.assertEqual(rig.pulse.last_evidence["observation"]["capture_monotonic_ns"], capture)
        evidence = rig.pulse.last_evidence
        # THIS is what v1 could not do: every command of a multi-period pulse is strictly newer than the last, so the gateway
        # APPLIES all ten; under v1 every command carried the capture stamp and all but the first were replayed for it.
        self.assertEqual((evidence["observations_published"], evidence["published_stamps"], evidence["commands_accepted"], evidence["commands_applied"]), (10, 10, 10, 10))
        self.assertEqual(evidence["gateway_outcomes"], {"APPLIED": 10})
        self.assertEqual(len(rig.velocities()), 10)
        capture_answers = Rig(ScriptedCapability(lambda o, n: forward_command(o, n, timestamp_ns=o["capture_ns"], expiry_ns=o["capture_ns"] + 250_000_000)),
                              observe=lambda: observed(capture_monotonic_ns=capture))
        capture_answers.pulse.move_base(VX, 0.2)
        self.assertEqual(capture_answers.velocities(), [], "an answer to the capture rather than to the publication reached the base")
        self.assertEqual(set(capture_answers.pulse.last_evidence["commands_dropped"]), {"not_the_publication_just_made"})
        self.assertEqual(capture_answers.pulse.last_refusal, "no_capability_output_applied")

    def test_the_gateway_itself_replays_a_repeated_stamp_which_is_why_the_publication_stamp_is_the_binding(self):
        """Read directly off the gateway the relay submits to: two commands with one timestamp are the v1 shape (every command
        of a pulse stamped with the capture), and the second is REJECTED_STALE_OR_REPLAYED; the same two with strictly
        increasing stamps are both APPLIED."""
        clock = FakeClock(START_NS)
        for name, stamps, outcomes in (("one stamp", (START_NS, START_NS), ["APPLIED", "REJECTED_STALE_OR_REPLAYED"]),
                                       ("increasing stamps", (START_NS, START_NS + 80_000_000), ["APPLIED", "APPLIED"])):
            with self.subTest(name):
                transport = FakeBaseMotionTransport()
                gateway = StableMotionGateway(transport, clock, capability_pulse.GatewayLimits(vx_max_mps=VX, vy_max_mps=0.05, omega_max_rps=0.01))
                gateway.enable_autonomous()
                seen = []
                for n, stamp in enumerate(stamps):
                    command = mobile_approach.DesiredBaseMotion(command_id=f"mobile_approach.pid{PID}.cmd_{n}", target_id=TARGET, timestamp_ns=stamp,
                                                                vx_mps=VX, vy_mps=0.0, omega_rps=0.0, expiry_ns=clock.now_ns() + 150_000_000)
                    seen.append(gateway.submit_command(command)[3].value)
                    clock.advance(80_000_000)
                self.assertEqual(outcomes, seen)

    def test_an_expired_answer_is_accepted_by_the_relay_but_not_applied_and_the_pulse_is_refused(self):
        """accepted counts what the relay submitted; applied counts what the gateway sent the base. An answer whose expiry the
        gateway sees as already passed is submitted (it passed every relay check) and rejected there, and a pulse of such
        answers is refused no_capability_output_applied (v1's refusal counted it as accepted output)."""
        rig = Rig(ScriptedCapability(lambda o, n: forward_command(o, n, expiry_ns=o["published_ns"])))  # expires at the instant it was asked for
        stopped = rig.pulse.move_base(VX, 0.5)
        self.assertTrue(stopped)
        evidence = rig.pulse.last_evidence
        self.assertEqual(rig.velocities(), [], "an expired command reached the base")
        self.assertGreater(evidence["commands_accepted"], 0)
        self.assertEqual(evidence["commands_applied"], 0)
        self.assertEqual(set(evidence["gateway_outcomes"]), {"REJECTED_EXPIRED_ON_ARRIVAL"})
        self.assertEqual(rig.pulse.last_refusal, "no_capability_output_applied")

    def test_an_observation_that_ages_past_its_own_bound_mid_pulse_ends_the_publishing(self):
        """C6: the re-qualification every period stays a mechanism; the bound it applies is the observation's own. A source
        whose bound is 200 ms and whose capture is 50 ms old at the start qualifies for two publications of a 0.2 s pulse
        and not for the third (160 ms in, 210 ms old)."""
        capture, bound = START_NS - 50_000_000, 200_000_000
        rig = Rig(ScriptedCapability(forward_command), observe=lambda: observed(max_age_ns=bound, capture_monotonic_ns=capture))
        self.assertTrue(rig.pulse.move_base(VX, 0.2))
        self.assertEqual(rig.pulse.last_evidence["observations_published"], 2)
        self.assertEqual(rig.pulse.last_evidence["observation_lost"], tos.PULSE_STALE)
        self.assertEqual(rig.pulse.last_refusal, capability_pulse.OBSERVATION_LOST)
        long_lived = Rig(ScriptedCapability(forward_command), observe=lambda: observed(capture_monotonic_ns=capture))  # the same capture under a 75 s bound
        self.assertTrue(long_lived.pulse.move_base(VX, 0.2))
        self.assertIsNone(long_lived.pulse.last_refusal)
        self.assertEqual(long_lived.pulse.last_evidence["observations_published"], 3)

    def test_an_observation_that_stops_qualifying_mid_pulse_ends_the_publishing_and_the_pulse_is_refused_after_its_stop(self):
        calls = []

        def source():  # fresh for the check before the Cake reads and for two publications, then nothing
            calls.append(1)
            if len(calls) > 3:
                raise tos.ObservationUnavailable([tos.REASON_NO_OBSERVATION])
            return observed(capture_monotonic_ns=rig.clock.now_ns())

        rig = Rig(ScriptedCapability(forward_command), observe=source)
        stopped = rig.pulse.move_base(VX, 0.8)
        self.assertTrue(stopped, "the stop sequence ran and read zero")
        self.assertEqual(rig.pulse.last_evidence["observations_published"], 2)
        self.assertEqual(rig.pulse.last_evidence["observation_lost"], capability_pulse.OBSERVATION_UNAVAILABLE)
        self.assertEqual(rig.pulse.last_refusal, capability_pulse.OBSERVATION_LOST)
        self.assertEqual([entry[1] for entry in rig.log if entry[1] != "velocity"][-4:], ["zero", "read", "zero", "disconnect"])
        last_velocity = max(entry[0] for entry in rig.log if entry[1] == "velocity")
        first_zero = min(entry[0] for entry in rig.log if entry[1] == "zero" and entry[0] >= last_velocity)
        self.assertLess(first_zero - last_velocity, capability_pulse.COMMAND_TTL_MS * 1_000_000,
                        "the base was zeroed by the lease, not by the end of the publishing")

    def test_the_relay_takes_no_observation_of_its_own(self):
        """Its source file builds no TargetObservation and names no coordinate: every x_m it publishes came from observe()."""
        with open(PULSE_SOURCE, encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        calls = {getattr(node.func, "id", getattr(node.func, "attr", None)) for node in ast.walk(tree) if isinstance(node, ast.Call)}
        self.assertNotIn("TargetObservation", calls)
        self.assertFalse([node for node in ast.walk(tree) if isinstance(node, ast.keyword) and node.arg in ("x_m", "y_m", "quality")])
        self.assertFalse([node for node in ast.walk(tree) if isinstance(node, ast.Name) and node.id.startswith("SYNTHETIC")])


class TheLinkIsTheProbesOwn(unittest.TestCase):
    def test_the_extracted_cake_reads_are_the_probes_text_for_text(self):
        for name in ("admin_reader", "snapshot", "describe", "read_manifest", "ipc_path", "someone_listens"):
            with self.subTest(name):
                self.assertEqual(inspect.getsource(getattr(capability_link, name)), inspect.getsource(getattr(approach_probe, name)))

    def test_someone_listens_tells_a_listener_from_a_stale_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "endpoint.ipc")
            self.assertFalse(capability_link.someone_listens(path))
            import socket

            server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            server.bind(path)
            server.listen(1)
            self.assertTrue(capability_link.someone_listens(path))
            server.close()
            self.assertTrue(os.path.exists(path))
            self.assertFalse(capability_link.someone_listens(path), "a stale socket file was taken for a listener")


@unittest.skipUnless(HAVE_ZMQ, "pyzmq is not importable by this interpreter")
class TheLinkReleasesItsEndpoint(unittest.TestCase):
    """capability_link.CapabilityLink over REAL Unix-domain ZeroMQ, against an in-process stand-in for the capability's two
    sockets (a PUB bound at the command endpoint, a SUB connecting to the observation endpoint). ZeroMQ closes a bound
    listener in its I/O thread after close() has returned; close() must return only once it is gone."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        context = zmq.Context.instance()
        self.commands = "ipc://" + os.path.join(self.tmp.name, "commands.ipc")
        self.publisher = context.socket(zmq.PUB)
        self.publisher.setsockopt(zmq.LINGER, 0)
        self.publisher.bind(self.commands)
        self.addCleanup(self.publisher.close)

    def open(self, number):
        path = os.path.join(self.tmp.name, f"observations-{number}.ipc")
        subscriber = zmq.Context.instance().socket(zmq.SUB)
        subscriber.setsockopt(zmq.LINGER, 0)
        subscriber.setsockopt(zmq.RECONNECT_IVL, 10)
        subscriber.setsockopt(zmq.SUBSCRIBE, b"")
        subscriber.connect("ipc://" + path)
        self.addCleanup(subscriber.close)
        return path, capability_link.CapabilityLink("ipc://" + path, self.commands, 5.0)

    def test_close_returns_only_once_nothing_listens_on_the_observation_endpoint(self):
        for number in range(5):  # without the wait, most single trials still find the listener (the measurement in capability_link)
            with self.subTest(trial=number):
                path, link = self.open(number)
                self.assertTrue(capability_link.someone_listens(path), "not vacuous: the link had bound the endpoint")
                self.assertIs(link.close(), True)
                self.assertFalse(capability_link.someone_listens(path), "close() returned while the endpoint still accepted connections")
                self.assertIs(link.close(), True, "a second close() waits for nothing and changes nothing")

    def test_a_listener_that_outlives_the_bound_is_reported_not_waited_for(self):
        path, link = self.open("held")
        with mock.patch.object(capability_link, "RELEASE_TIMEOUT_S", 0.05), mock.patch.object(capability_link, "someone_listens", lambda _path: True):
            started = time.monotonic()
            self.assertIs(link.close(), False)
            self.assertLess(time.monotonic() - started, 1.0)


@unittest.skipUnless(shutil.which("bash") and os.path.isdir("/usr/bin"), "bash is not installed")
class EndToEnd(unittest.TestCase):
    """The relay over REAL Unix-domain ZeroMQ, against the REAL capability process as Cake starts it."""

    def setUp(self):
        if not HAVE_ZMQ:
            self.skipTest("pyzmq is not importable by this interpreter")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        t = self.tmp.name
        self.run_dir = os.path.join(t, "run")
        os.mkdir(self.run_dir)
        os.mkdir(os.path.join(t, "child-cwd"))
        with open(os.path.join(ROOT, "bin", "templates", "run-capability.sh.in"), encoding="utf-8") as handle:
            text = handle.read()
        for name, value in (("PYTHON", sys.executable), ("APP_DIR", ROOT), ("RUN_DIR", self.run_dir)):
            text = text.replace(f"@@{name}@@", value)
        wrapper = os.path.join(t, "run-capability.sh")
        with open(wrapper, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.chmod(wrapper, 0o755)
        self.service = subprocess.Popen([wrapper, "3", "0", "900", "kc-test-run-tag"], env={"PATH": "/usr/bin:/bin"},
                                        cwd=os.path.join(t, "child-cwd"), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        self.addCleanup(self.stop_service)
        self.commands = os.path.join(self.run_dir, "approach-commands.ipc")
        deadline = time.monotonic() + 20
        while not os.path.exists(self.commands):
            if self.service.poll() is not None or time.monotonic() > deadline:
                self.fail("the capability process did not bind its command endpoint: " + (self.service.stdout.read() or ""))
            time.sleep(0.05)

    def stop_service(self):
        if self.service.poll() is None:
            self.service.send_signal(signal.SIGINT)
            try:
                self.service.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.service.kill()
                self.service.wait()
        self.service.stdout.close()

    def pulse(self, pid):
        clock, log = MonotonicClock(), []
        transports = []

        def transport_factory():
            transports.append(RecordingTransport(log, clock))
            return transports[-1]

        relay = capability_pulse.CapabilityPulse(
            read=Admin(listing(pid=pid)).read, slot_uuid=SLOT, capsule=MOBILE, transport_factory=transport_factory,
            observations="ipc://" + os.path.join(self.run_dir, "approach-observations.ipc"),
            commands="ipc://" + self.commands, target_id=TARGET, should_stop=lambda: False,
            observe=v2_source(TARGET, clock, lambda: 0), motion_epoch=lambda: 0, clock=clock)
        return relay, relay.move_base(VX, 0.5), log

    def test_the_launched_process_drives_the_gateway_and_nothing_else_does(self):
        relay, stopped, log = self.pulse(self.service.pid)
        evidence = relay.last_evidence
        self.assertTrue(stopped)
        self.assertIsNone(relay.last_refusal, json.dumps(evidence, indent=1))
        self.assertEqual(evidence["producer_pid"], self.service.pid)
        self.assertGreaterEqual(evidence["commands_accepted"], evidence["observations_published"] - 1,
                                "the capability's answers did not arrive within the period they were asked in")
        self.assertGreaterEqual(evidence["commands_accepted"], 3)
        self.assertEqual(evidence["commands_applied"], evidence["commands_accepted"], "the real v2 process's commands were accepted and not applied")
        self.assertEqual(evidence["gateway_outcomes"], {"APPLIED": evidence["commands_accepted"]})
        velocities = [entry[2:] for entry in log if entry[1] == "velocity"]
        self.assertEqual(len(velocities), evidence["commands_accepted"])
        self.assertEqual(set(velocities), {(VX, 0.0, 0.0)})
        self.assertEqual([entry[1] for entry in log[-4:]], ["zero", "read", "zero", "disconnect"])
        self.assertFalse(os.path.exists(os.path.join(self.run_dir, "approach-observations.ipc")) and
                         capability_link.someone_listens(os.path.join(self.run_dir, "approach-observations.ipc")),
                         "the relay left the observation endpoint bound")

    def test_a_command_of_another_pid_never_reaches_the_base(self):
        relay, stopped, log = self.pulse(4242)
        self.assertTrue(stopped)
        self.assertEqual([entry for entry in log if entry[1] == "velocity"], [])
        self.assertEqual(relay.last_refusal, "no_capability_output_applied")
        self.assertEqual(set(relay.last_evidence["commands_dropped"]), {"command_not_from_launched_process"})


if __name__ == "__main__":
    unittest.main()
