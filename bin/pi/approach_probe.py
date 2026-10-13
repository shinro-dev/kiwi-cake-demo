#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""approach_probe: a NON-MOTION probe of the mobile_approach capability that Cake launched on the robot computer.

RUN ON DEMAND, ON THE ROBOT COMPUTER, over ssh (bin/pi/lekiwi-deploy.sh approach-probe [args...]). It is NOT a Cake child
and nothing supervises it. It moves nothing and can move nothing: it opens no servo bus and no camera, it imports no
lerobot (asserted before it exits), and the only motion transport it constructs is transport.FakeBaseMotionTransport,
an in-memory recorder. lerobot_transport is never imported, so LeKiwiMotionTransport is never constructed.

WHAT IT DOES, in order:
  1. under v2 only, imports the proven executor from --executor-dir and, through it, the observation source
     (target_observation_source from the executor's checkout, the file the relay packages with in a run); a checkout
     that cannot be imported refuses here, before Cake is read at all. Then reads list-slots and get-health through the
     robot's admin-probe and refuses, before publishing anything, unless the mobile_approach slot is ACTIVE on the
     expected mobile capsule and its health row names a launched pid;
  2. binds a ZeroMQ publisher at the capability's observation endpoint (an XPUB, so that it can wait for the
     capability's own subscription instead of sleeping and hoping), subscribes to the capability's command endpoint,
     and publishes ONE observation document, stamped from clock.MonotonicClock, the same clock the gateway below reads:
       --contract v1 (the default): the LABELLED FIXTURE TargetObservation cube_far, x_m 0.95, y_m 0.10, quality 0.92.
                     It is a fixture, not a camera observation;
       --contract v2: a TargetObservationV2 (autonomy/schemas/target_observation.v2.schema.json) DERIVED FROM A REAL
                     STORED PERCEPTION RECORD (--record PATH, the JSON perceive() writes beside a capture) with the
                     proven executor's own functions: the anchor is cube_by_template.cube_center_pad_m, the reach axis
                     is reach_direction_pad at cube.contact_px through pad_by_template's homography, and x_m is
                     base_advance_needed_mm / 1000, packaged by target_observation_source.resolve exactly as the relay
                     does it in a run. It is a stored record, not a live capture: capture_ns is a stamp read before the
                     record is read, motion_epoch is 0 and stationary is asserted by the probe, and the evidence says so;
  3. receives the capability's DesiredBaseMotion and ACCEPTS it only when cake_capability_adapter's
     bind_command_to_launched_process holds: the slot as read in step 1 is ACTIVE on the mobile capsule AND the
     command_id starts with mobile_approach.pid<P>. where P is get-health arg1 for that slot, the process Cake
     launched. The Cake reads happen BEFORE the document is published because the command expires 250 ms after the
     stamp it answers; they are read again afterwards (step 5) and must not have changed. Under v2 the command must
     also echo the publication: timestamp_ns == published_ns, expiry_ns == published_ns + 250 ms, vx_mps == the
     executor's pulse speed BASE_VX_MPS (0.05), vy_mps 0, omega 0;
  4. submits the accepted command to a StableMotionGateway constructed DIRECTLY over FakeBaseMotionTransport, the
     supported seam for a directly constructed gateway: first with autonomous DISABLED (expect
     ZERO_AUTONOMOUS_DISABLED, no velocity forwarded; a zero IS forwarded, which is what a disabled gateway does), then
     on a SEPARATE gateway with autonomous ENABLED (expect APPLIED, bounded by the per-update slew limit);
  5. re-reads list-slots and get-health and requires the same slot, generation, capsule and pid.
It prints ONE JSON evidence record on stdout. GATEWAY ACCEPTANCE IS NOT PHYSICAL EXECUTION: the record names the
recording destination, transport.FakeBaseMotionTransport, and says physical_execution false. FakeBaseMotionTransport is
not the fake-client seam that controller code uses (lerobot_transport's client_factory_scope) and is not presented as
interchangeable with it.

THE FAIL-CLOSED NEGATIVE, under v2: a capability launched on another contract drops a v2 document (it counts it and
publishes nothing), so the record's `contract_answered` is false and the verdict FAIL. That is the proof that the two
contracts do not mix, not an error of this probe. The v1 fixture against a v2 capability is dropped the same way
(contract_mismatch on the capability's side) and reads here as "no command within the timeout".

Exit status: 0 every expectation held; 1 an expectation did not hold (the record says which); 3 refused before
anything was published (capability not active, an endpoint missing or owned by another process, or under v2 no
observation resolves from the record: the record names every reason); 5 usage.

Usage:
  approach_probe.py --admin-probe PATH --admin-socket PATH (--manifest PATH | --slot-uuid HEX --capsule HEX)
                    --observations ipc://PATH --commands ipc://PATH [--timeout-s SECONDS]
                    [--contract v1|v2] [--record PATH] [--target-id ID] [--observation-id ID]
                    [--perception-deadline-s SECONDS] [--executor-dir PATH]
Under v2 the interpreter must import the executor (numpy, cv2 and what bin/laptop/perception/run_stage1_scan pulls):
on the robot computer that is the benchmark venv python (KC_PI_PYTHON), and --executor-dir is the directory holding
run_single_cube_cycle.py there when it is not this checkout's bin/laptop/manipulation; that checkout also supplies
autonomy/motion/target_observation_source.py, which the deployed app does not carry. A leading '~' in --record and
--executor-dir is expanded here, and the evidence writes paths with the home directory as '~'.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "autonomy", "motion"))

# Module scope imports ONLY what bin/pi/lekiwi-deploy.sh's APP_FILES deploys beside this file (a test derives that set
# from the script): the executor and its observation source are imported inside the v2 path (load_executor, load_source).
from cake_capability_adapter import (  # noqa: E402
    ACTIVE,
    bind_command_to_launched_process,
    parse_health,
    parse_slots,
)
from clock import MonotonicClock  # noqa: E402
from mobile_approach import ControllerConfig, DesiredBaseMotion  # noqa: E402
from stable_motion_gateway import GatewayLimits, GatewayOutcome, StableMotionGateway  # noqa: E402
from transport import FakeBaseMotionTransport  # noqa: E402

CAPABILITY = "mobile_approach"
#: The labelled fixture. Inside the trusted localization envelope and OUTSIDE the handoff region, so the controller
#: must drive: the capability answers vx 0.10, vy 0.06 and the enabled gateway applies 0.05, 0.05.
FIXTURE = {"target_id": "cube_far", "frame": "base", "x_m": 0.95, "y_m": 0.10, "quality": 0.92}
RECORDING_DESTINATION = f"{FakeBaseMotionTransport.__module__}.{FakeBaseMotionTransport.__name__}"
HEX64 = re.compile(r"^[0-9a-f]{64}$")
HEX32 = re.compile(r"^[0-9a-f]{32}$")
#: The two contracts this probe can publish on; v1 is the default so the fixture mode is what it always was.
CONTRACT_V1, CONTRACT_V2 = "v1", "v2"
#: Where the proven executor lives in this checkout; on the robot computer the caller names its directory.
EXECUTOR_DIR = os.path.join(ROOT, "bin", "laptop", "manipulation")
#: The v2 target id when the caller names none: the probe's own label, not a mission's selection.
PROBE_TARGET_ID = "probe_target"
#: The v2 freshness budget when the caller names none: the desktop perception deadline a LIVE run is given, in seconds.
DEFAULT_PERCEPTION_DEADLINE_S = 60.0
#: The command TTL the v2 configuration fixes; the command's expiry must be the publication plus this.
V2_COMMAND_TTL_NS = ControllerConfig.contract_v2().command_ttl_ns


class Refused(Exception):
    """A refusal before anything was published."""

    def __init__(self, message: str, reasons=()):
        super().__init__(message)
        self.reasons = tuple(reasons)


def lerobot_modules() -> list:
    return sorted(name for name in sys.modules if name == "lerobot" or name.startswith("lerobot."))


def admin_reader(probe: str, admin_socket: str, timeout_s: float):
    """read(op) -> the text admin-probe printed. Only the two read operations this probe needs are ever run."""

    def read(op: str) -> str:
        if op not in ("list-slots", "get-health"):
            raise ValueError(f"{op} is not a read this probe performs")
        done = subprocess.run([probe, "request", admin_socket, op], capture_output=True, text=True, timeout=timeout_s)
        return done.stdout

    return read


def snapshot(read, slot_uuid: str) -> dict:
    """The capability slot and its health row, as the resident reported them at one moment."""
    listing, health = read("list-slots"), read("get-health")
    ok = "status ok" in listing.splitlines() and "status ok" in health.splitlines()
    slot = parse_slots(listing).get(slot_uuid) if ok else None
    row = parse_health(health).get(slot_uuid) if ok else None
    return {"ok": ok, "slot": slot, "health": row}


def describe(snap: dict) -> dict:
    slot, row = snap["slot"], snap["health"]
    return {
        "admin_reads_ok": snap["ok"],
        "slot": None if slot is None else {"uuid": slot.uuid, "name": slot.name, "state": slot.state,
                                           "capsule": slot.capsule, "generation": slot.generation},
        "health": None if row is None else {"surface": row.surface, "state_name": row.state_name, "arg1": row.arg1},
    }


def read_manifest(path: str) -> tuple:
    values = {}
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            name, _, value = line.rstrip("\n").partition(" ")
            values.setdefault(name, value)
    return values.get("mobile_slot_uuid", ""), values.get("mobile_capsule_digest", "")


def ipc_path(endpoint: str) -> str:
    if not endpoint.startswith("ipc:///"):
        raise Refused(f"{endpoint.split('://')[0]} endpoint given; only an absolute ipc:// endpoint is supported")
    return endpoint[len("ipc://"):]


def someone_listens(path: str) -> bool:
    """True when a process accepts connections on the Unix socket at PATH (a stale file is not a listener)."""
    if not os.path.exists(path):
        return False
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    probe.settimeout(1.0)
    try:
        probe.connect(path)
        return True
    except OSError:
        return False
    finally:
        probe.close()


def fixture_document(now_ns: int) -> dict:
    """The v1 fixture, stamped now: what the probe always published."""
    return dict(FIXTURE, timestamp_ns=now_ns)


def load_executor(executor_dir: str):
    """The proven executor module, imported from `executor_dir` the way its tests import it (the directory on sys.path;
    the module puts its own siblings on the path). Imported only under v2, because it needs numpy and cv2."""
    path = os.path.join(executor_dir, "run_single_cube_cycle.py")
    if not os.path.isfile(path):
        raise Refused(f"no run_single_cube_cycle.py in the executor directory; name it with --executor-dir ({path})")
    if executor_dir not in sys.path:
        sys.path.insert(0, executor_dir)
    try:
        import run_single_cube_cycle as executor
    except ImportError as error:
        raise Refused(f"the executor could not be imported under this interpreter ({error}); run under the benchmark venv python")
    return executor


#: What the v2 observation source must offer for this probe to package with it: the v1 source (which raised on every
#: LIVE target) had none of these, and a checkout carrying it is refused by name rather than failing in the middle.
SOURCE_V2_NAMES = ("CONTRACT", "ObservationUnavailable", "max_age_ns_for", "resolve")


def load_source(executor):
    """target_observation_source, the module the relay packages the observation with in a run, imported the way the
    executor imports it (executor._motion_module: the autonomy/motion directory of the EXECUTOR'S checkout, first on the
    path), so that the probe's observation is built by the same file a run would use and the deployed app need not carry
    it. Imported only under v2, after the executor."""
    try:
        source = executor._motion_module("target_observation_source")
    except ImportError as error:
        raise Refused(f"the executor's checkout has no importable target_observation_source ({error}); name a complete checkout with --executor-dir")
    missing = [name for name in SOURCE_V2_NAMES if not hasattr(source, name)]
    if missing:
        raise Refused("the executor's target_observation_source is not the contract v2 source (it lacks " + ", ".join(missing) + ")")
    return source


def home_relative(path: str) -> str:
    """`path`, absolute, with this account's home directory written as '~': the evidence names no home directory (the
    deploy script's redaction does the same to everything the robot prints, and the record is pasted into documents)."""
    absolute = os.path.abspath(path)
    home = os.path.expanduser("~").rstrip(os.sep)
    if home and home != "~" and (absolute == home or absolute.startswith(home + os.sep)):
        return "~" + absolute[len(home):]
    return absolute


def load_record(path: str) -> dict:
    """The stored perception record: the JSON perceive() wrote for one capture, read as it is."""
    try:
        with open(path, encoding="utf-8") as handle:
            record = json.load(handle)
    except OSError as error:
        raise Refused(f"the perception record could not be read: {error.strerror}")
    except ValueError:
        raise Refused("the perception record is not JSON")
    if not isinstance(record, dict):
        raise Refused("the perception record is not an object")
    return record


def record_observation_id(path: str) -> str:
    """The stored capture's id as the executor names the record: <observation_id>.json in the session's perception directory."""
    name = os.path.basename(path)
    return name[:-len(".json")] if name.endswith(".json") else name


def observation_from_record(executor, record: dict, *, observation_id: str, target_id: str, capture_ns: int, now_ns: int,
                            perception_deadline_s: float):
    """(the ObservedTarget, the approach state) derived from a stored perception record with the executor's own functions,
    or Refused naming what the record lacks or every reason target_observation_source.resolve refuses it for.

    The anchor is the record's cube_by_template.cube_center_pad_m (PAD_FRAME metres); the reach axis is
    executor.reach_direction_pad at the record's cube.contact_px through pad_by_template.homography_image_to_pad; the
    approach state is executor.Cycle._approach_state (the same builder advance_base hands the relay, so the key set is
    the executor's, need_mm included); the capture is stamped with `capture_ns`, motion epoch 0 and stationary True,
    which for a stored record the PROBE asserts (the record was taken from a base the executor does not read here).
    The packaging is target_observation_source.resolve from the executor's checkout (load_source)."""
    source = load_source(executor)
    pad = record.get("pad_by_template") if isinstance(record.get("pad_by_template"), dict) else {}
    cube = record.get("cube") if isinstance(record.get("cube"), dict) else {}
    template = record.get("cube_by_template") if isinstance(record.get("cube_by_template"), dict) else {}
    anchor, contact, homography = template.get("cube_center_pad_m"), cube.get("contact_px"), pad.get("homography_image_to_pad")
    missing = [name for name, value in (("pad_by_template.homography_image_to_pad", homography), ("cube.contact_px", contact),
                                        ("cube_by_template.cube_center_pad_m", anchor)) if not value]
    if missing:
        raise Refused("the perception record has no " + ", no ".join(missing) + "; no observation derives from it", missing)
    if not pad.get("pad_frame_valid"):
        raise Refused("the perception record's pad frame is not valid (pad_frame_valid); no observation derives from it", ["pad_frame_invalid"])
    axis = executor.reach_direction_pad(homography, contact)
    view = {"perception": [{"observation_id": observation_id, **record}]}
    approach = executor.Cycle._approach_state(view, anchor, axis)
    capture = {"observation_id": observation_id, "capture_monotonic_ns": capture_ns, "motion_epoch": 0, "stationary": True}
    selection = {"target_id": target_id, "selected_by": "approach_probe --target-id"}
    try:
        observed = source.resolve(approach, capture, selection, now_ns, perception_deadline_s=perception_deadline_s, target_id=target_id)
    except source.ObservationUnavailable as unavailable:
        raise Refused("no v2 observation resolves from the record: " + ",".join(unavailable.reasons), unavailable.reasons)
    return observed, approach


def exchange(observations: str, commands: str, clock, timeout_s: float, document=fixture_document) -> tuple:
    """Publish document(now_ns) once the capability has subscribed, and return (what was published, the command document
    or None)."""
    import zmq

    context = zmq.Context.instance()
    inbound = context.socket(zmq.SUB)
    inbound.setsockopt(zmq.LINGER, 0)
    inbound.setsockopt(zmq.SUBSCRIBE, b"")
    monitor = inbound.get_monitor_socket(zmq.EVENT_HANDSHAKE_SUCCEEDED)
    outbound = context.socket(zmq.XPUB)
    outbound.setsockopt(zmq.LINGER, 0)
    try:
        inbound.connect(commands)
        deadline = time.monotonic() + timeout_s
        # Our subscription reaches the capability's publisher after the handshake; wait for it rather than guess.
        if not monitor.poll(int(timeout_s * 1000)):
            raise Refused("the capability's command endpoint did not complete a handshake: is the capability running?")
        monitor.recv_multipart()
        outbound.bind(observations)
        # The capability's subscriber reconnects to the endpoint we just bound; its subscription arrives here.
        while True:
            left = int((deadline - time.monotonic()) * 1000)
            if left <= 0 or not outbound.poll(left):
                raise Refused("the capability never subscribed to the observation endpoint within the timeout")
            if outbound.recv()[:1] == b"\x01":
                break
        time.sleep(0.05)  # a settle for our own subscription at the capability's publisher, not a measurement
        published = document(clock.now_ns())
        outbound.send(json.dumps(published, separators=(",", ":")).encode("utf-8"))
        left = int((deadline - time.monotonic()) * 1000)
        if left <= 0 or not inbound.poll(left):
            return published, None
        return published, json.loads(inbound.recv().decode("utf-8"))
    finally:
        inbound.close()
        outbound.close()
        monitor.close()


def expected_applied(command, limits) -> tuple:
    """What an enabled gateway with nothing applied yet must apply: clamp to the limit, then one slew step from zero."""

    def one(value, limit, step):
        return max(-step, min(step, max(-limit, min(limit, value))))

    return (one(command.vx_mps, limits.vx_max_mps, limits.max_dvx_per_update_mps),
            one(command.vy_mps, limits.vy_max_mps, limits.max_dvy_per_update_mps),
            one(command.omega_rps, limits.omega_max_rps, limits.max_domega_per_update_rps))


def through_gateways(command, clock) -> dict:
    """The accepted command, submitted to two gateways built directly over in-memory recorders."""
    limits = GatewayLimits()
    disabled_recorder = FakeBaseMotionTransport()
    disabled = StableMotionGateway(disabled_recorder, clock, limits)
    off = disabled.submit_command(command)
    enabled_recorder = FakeBaseMotionTransport()
    enabled = StableMotionGateway(enabled_recorder, clock, limits)
    enabled.enable_autonomous()
    on = enabled.submit_command(command)
    return {
        "limits": {"vx_max_mps": limits.vx_max_mps, "vy_max_mps": limits.vy_max_mps,
                   "max_dvx_per_update_mps": limits.max_dvx_per_update_mps,
                   "max_dvy_per_update_mps": limits.max_dvy_per_update_mps},
        "expected_applied": list(expected_applied(command, limits)),
        "disabled": {"outcome": off[3].value, "reason": off[4], "applied": list(off[:3]),
                     "recorded_calls": [list(call) for call in disabled_recorder.calls]},
        "enabled": {"outcome": on[3].value, "reason": on[4], "applied": list(on[:3]),
                    "recorded_calls": [list(call) for call in enabled_recorder.calls],
                    "recorder_last_sent": list(enabled_recorder.last_sent)},
    }


def judge(gateways: dict) -> list:
    """Every expectation the two gateways must meet, as the failures that did not hold."""
    failures = []
    off, on = gateways["disabled"], gateways["enabled"]
    if off["outcome"] != GatewayOutcome.ZERO_AUTONOMOUS_DISABLED.value:
        failures.append(f"disabled gateway answered {off['outcome']}, not ZERO_AUTONOMOUS_DISABLED")
    if any(call[0] == "velocity" for call in off["recorded_calls"]):
        failures.append("the disabled gateway forwarded a velocity")
    if off["recorded_calls"] != [["zero"]]:
        failures.append(f"the disabled gateway forwarded {off['recorded_calls']}, not exactly one zero")
    if on["outcome"] != GatewayOutcome.APPLIED.value:
        failures.append(f"enabled gateway answered {on['outcome']}, not APPLIED")
    elif on["applied"] != gateways["expected_applied"]:
        failures.append(f"enabled gateway applied {on['applied']}, not the slew-bounded {gateways['expected_applied']}")
    elif on["applied"] == [0.0, 0.0, 0.0]:
        failures.append("the enabled gateway applied a zero vector; the fixture requires an approach")
    limits = gateways["limits"]
    if abs(on["applied"][0]) > limits["max_dvx_per_update_mps"] or abs(on["applied"][1]) > limits["max_dvy_per_update_mps"]:
        failures.append("the applied vector exceeds the per-update slew limit")
    return failures


def judge_v2(command, observation: dict, pulse_vx_mps: float, gateways=None) -> list:
    """Every expectation a v2 answer must meet, as the failures that did not hold: it answers THIS publication
    (timestamp_ns == published_ns, expiry_ns == published_ns + the v2 TTL), for this target, at exactly the executor's
    pulse speed straight ahead (vx == pulse_vx_mps, vy 0, omega 0), and when the gateways ran, the enabled one applied
    exactly that speed (the v2 command is at the slew limit, so nothing is cut)."""
    failures = []
    if command.target_id != observation["target_id"]:
        failures.append(f"the command names target {command.target_id!r}, not the observation's {observation['target_id']!r}")
    if command.timestamp_ns != observation["published_ns"]:
        failures.append(f"the command's timestamp_ns {command.timestamp_ns} is not the publication's published_ns {observation['published_ns']}")
    if command.expiry_ns != observation["published_ns"] + V2_COMMAND_TTL_NS:
        failures.append(f"the command's expiry_ns is {command.expiry_ns - observation['published_ns']} ns after the publication, not {V2_COMMAND_TTL_NS}")
    if command.vx_mps != pulse_vx_mps:
        failures.append(f"the command's vx_mps {command.vx_mps} is not the executor's pulse speed {pulse_vx_mps}")
    if command.vy_mps != 0.0 or command.omega_rps != 0.0:
        failures.append(f"the command is not straight ahead (vy {command.vy_mps}, omega {command.omega_rps})")
    if gateways is not None and gateways["enabled"]["outcome"] == GatewayOutcome.APPLIED.value and gateways["enabled"]["applied"] != [pulse_vx_mps, 0.0, 0.0]:
        failures.append(f"the enabled gateway applied {gateways['enabled']['applied']}, not the pulse [{pulse_vx_mps}, 0.0, 0.0]")
    return failures


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="approach_probe", description=__doc__.splitlines()[0])
    parser.add_argument("--admin-probe", required=True, help="absolute path of the robot's admin-probe")
    parser.add_argument("--admin-socket", required=True, help="the deployment resident's admin socket")
    parser.add_argument("--manifest", help="the deployment manifest (reads mobile_slot_uuid and mobile_capsule_digest)")
    parser.add_argument("--slot-uuid", help="the mobile_approach slot uuid, instead of the manifest's")
    parser.add_argument("--capsule", help="the mobile capsule digest, instead of the manifest's")
    parser.add_argument("--observations", required=True, help="the capability's observation endpoint, ipc://PATH")
    parser.add_argument("--commands", required=True, help="the capability's command endpoint, ipc://PATH")
    parser.add_argument("--timeout-s", type=float, default=5.0, help="how long each wait may take")
    parser.add_argument("--contract", choices=(CONTRACT_V1, CONTRACT_V2), default=CONTRACT_V1,
                        help="what to publish: the v1 labelled fixture (the default) or a v2 observation derived from --record")
    parser.add_argument("--record", help="v2: the stored perception record (the JSON perceive() wrote for one capture)")
    parser.add_argument("--target-id", default=PROBE_TARGET_ID, help=f"v2: the target id the observation names (default {PROBE_TARGET_ID})")
    parser.add_argument("--observation-id", help="v2: the stored capture's id (default: the record file's name without .json, as the executor names it)")
    parser.add_argument("--perception-deadline-s", type=float, default=DEFAULT_PERCEPTION_DEADLINE_S,
                        help=f"v2: the perception budget max_age_ns is derived from (default {DEFAULT_PERCEPTION_DEADLINE_S:g})")
    parser.add_argument("--executor-dir", default=EXECUTOR_DIR, help="v2: the directory holding run_single_cube_cycle.py")
    args = parser.parse_args(argv)

    slot_uuid, capsule = ("", "")
    if args.manifest:
        slot_uuid, capsule = read_manifest(args.manifest)
    slot_uuid, capsule = (args.slot_uuid or slot_uuid), (args.capsule or capsule)
    if not HEX32.match(slot_uuid or "") or not HEX64.match(capsule or ""):
        print("approach_probe: USAGE: a 32-hex slot uuid and a 64-hex capsule digest are needed (--manifest, or "
              "--slot-uuid and --capsule)", file=sys.stderr)
        return 5
    v2 = args.contract == CONTRACT_V2
    if v2 and not args.record:
        print("approach_probe: USAGE: --contract v2 needs --record PATH, the stored perception record", file=sys.stderr)
        return 5
    if not v2 and args.record:
        print("approach_probe: USAGE: --record is read only under --contract v2", file=sys.stderr)
        return 5
    # A '~' the caller wrote (the deploy script hands the arguments over quoted, so the remote shell expands nothing).
    args.executor_dir = os.path.expanduser(args.executor_dir)
    args.record = os.path.expanduser(args.record) if args.record else args.record

    clock = MonotonicClock()
    read = admin_reader(args.admin_probe, args.admin_socket, max(args.timeout_s, 1.0))
    if v2:
        record = {"evidence": "approach_probe", "capability": CAPABILITY, "verdict": "FAIL", "failures": [],
                  "fixture_label": "DERIVED FROM A STORED PERCEPTION RECORD, not a live capture",
                  "recording_destination": RECORDING_DESTINATION, "physical_execution": False,
                  "note": "gateway acceptance is not physical execution; no motion transport to the robot was constructed"}
    else:
        record = {"evidence": "approach_probe", "capability": CAPABILITY, "verdict": "FAIL", "failures": [],
                  "fixture_label": "LABELLED FIXTURE, not a camera observation",
                  "recording_destination": RECORDING_DESTINATION, "physical_execution": False,
                  "note": "gateway acceptance is not physical execution; no motion transport to the robot was constructed"}
    document, pulse_vx_mps = fixture_document, None
    try:
        if v2:
            # The probe's own prerequisites first: a checkout that cannot be imported refuses before Cake is read at all.
            executor = load_executor(args.executor_dir)
            source = load_source(executor)
            record["contract"] = source.CONTRACT
        before = snapshot(read, slot_uuid)
        record["cake_before"] = describe(before)
        slot = before["slot"]
        if slot is None or slot.state != ACTIVE or slot.capsule != capsule:
            raise Refused("the mobile_approach slot is not ACTIVE on the expected mobile capsule; nothing was published")
        if before["health"] is None or not before["health"].arg1:
            raise Refused("get-health names no launched pid for the mobile_approach slot; nothing was published")
        if v2:
            observation_id = args.observation_id or record_observation_id(args.record)
            capture_ns = clock.now_ns()  # BEFORE the record is read: the stamp the observation carries as its capture
            stored = load_record(args.record)
            observed, approach = observation_from_record(executor, stored, observation_id=observation_id, target_id=args.target_id,
                                                         capture_ns=capture_ns, now_ns=clock.now_ns(), perception_deadline_s=args.perception_deadline_s)
            document, pulse_vx_mps = observed.wire_v2, float(executor.BASE_VX_MPS)
            record["observation_evidence"] = {**observed.identity(), **observed.evidence(), "approach_state": approach,
                                              "record_path": home_relative(args.record), "executor_module": home_relative(executor.__file__),
                                              "source_module": home_relative(source.__file__),
                                              "stationary_asserted_by_probe": True, "motion_epoch_asserted_by_probe": True,
                                              "perception_deadline_s": args.perception_deadline_s, "expected_vx_mps": pulse_vx_mps}
        if someone_listens(ipc_path(args.observations)):
            raise Refused("another process already listens on the observation endpoint; it was left alone")
        if not os.path.exists(ipc_path(args.commands)):
            raise Refused("the capability's command endpoint does not exist: the capability process is not running")
        published, answer = exchange(args.observations, args.commands, clock, args.timeout_s, document)
    except Refused as refusal:
        record["refused"] = str(refusal)
        if refusal.reasons:
            record["observation_unavailable"] = list(refusal.reasons)
        record["lerobot_modules_loaded"] = lerobot_modules()
        print(json.dumps(record, sort_keys=True))
        return 3
    if v2:
        record["observation"] = published
        record["contract_answered"] = answer is not None
        stamp_ns, stamp_name = published["published_ns"], "publication"
    else:
        record["fixture"] = published
        stamp_ns, stamp_name = published["timestamp_ns"], "fixture"
    if answer is None:
        record["failures"].append("the capability published no command within the timeout" + (
            " (contract_answered false: a capability on another contract drops a v2 document and answers nothing, fail closed)" if v2 else ""))
    else:
        command = DesiredBaseMotion(**answer)
        binding = bind_command_to_launched_process(command.command_id, capability=CAPABILITY, slot=before["slot"],
                                                   active_capsule=capsule, health=before["health"])
        record["command"] = answer
        record["producer_pid"] = binding.pid
        record["binding"] = {"accepted": binding.accepted, "reason": binding.reason,
                             "expected_prefix": binding.expected_prefix}
        if not binding.accepted:
            record["failures"].append(f"the command was not accepted: {binding.reason}")
        else:
            # Measured on the gateway's own clock, from the published stamp to the submission, against the command's
            # expiry: a command that expired on the way here would be judged below as not APPLIED.
            submitted_ns = clock.now_ns()
            record[f"submitted_ms_after_{stamp_name}"] = round((submitted_ns - stamp_ns) / 1e6, 3)
            record["ttl_left_ms_at_submission"] = round((command.expiry_ns - submitted_ns) / 1e6, 3)
            record["gateways"] = through_gateways(command, clock)
            record["failures"].extend(judge(record["gateways"]))
            if v2:
                record["failures"].extend(judge_v2(command, published, pulse_vx_mps, record["gateways"]))
    after = snapshot(read, slot_uuid)
    record["cake_after"] = describe(after)
    if describe(after) != record["cake_before"]:
        record["failures"].append("the capability slot or its launched pid changed while the probe ran")
    record["slot_generation"] = before["slot"].generation
    record["slot_capsule"] = before["slot"].capsule
    record["lerobot_modules_loaded"] = lerobot_modules()
    if record["lerobot_modules_loaded"]:
        record["failures"].append("a lerobot module was imported")
    record["verdict"] = "PASS" if not record["failures"] else "FAIL"
    print(json.dumps(record, sort_keys=True))
    return 0 if record["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
