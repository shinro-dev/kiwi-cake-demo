#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""mobile_approach_service: the capability process Cake supervises.

WHAT THIS IS FOR. `autonomy/motion/mobile_approach.py` is pure compute: it has no process, no socket and no main,
so nothing about it can be activated. This is the process wrapper that turns it into the capability the
mobile-approach deployment's slot spawns, which is what makes a Cake activation load-bearing rather than
decorative. Until the capability slot is replaced onto the mobile build, this process is not running, no
DesiredBaseMotion is published, and a mission that needs one waits. That is the whole mechanism.

THE TRANSPORT IS THE ONE THE SPEC ALREADY CHOSE, not a new one. `docs/autonomous-demo/mobile-approach-spec.md`
settles it: Unix-domain ZeroMQ, one socket carrying TargetObservation in and one carrying DesiredBaseMotion out,
on the pyzmq that LeRobot already pins. That document also rejects two shortcuts by name, and this module takes
neither: Cake's admin socket is NOT a data channel ("its introspection surface is not designed or sized for a
continuous control loop"), and an in-process call is rejected because it "would make Cake decorative, not
load-bearing". Cake never sees a control message; it owns only whether this process exists.

WHAT IT DOES NOT DO. It enforces no safety bound. The advisory clamps live in the controller and the real ones
are the stable motion gateway's, unconditionally, which is the only thing this output is ever connected to. It
owns no mission and selects no target: it reacts to the TargetObservation it is handed and says so in its own
`target_id`. It opens no camera, no servo bus and no host observation socket.

STDOUT IS DIAGNOSTICS, NEVER DATA. The spec is explicit that the supervisor captures stdout into a bounded log
inside the slot's granted data directory, so this process keeps it quiet and treats it as diagnostics only. Every
control value leaves by the publication socket and by nothing else.

IDENTITY. `--identity` is written into every published command's `command_id` prefix so that a command can be
attributed to the module generation that produced it. The supervising configuration supplies it; this process
never invents one and never reads Cake.

THE CONTRACT IS CHOSEN AT LAUNCH, NEVER GUESSED FROM A MESSAGE. `--contract v1` (the default, what the deployed
robot runs until the next install) reads TargetObservation (autonomy/schemas/target_observation.schema.json);
`--contract v2` (what bin/templates/run-capability.sh.in now launches) reads TargetObservationV2
(autonomy/schemas/target_observation.v2.schema.json) and nothing else. A document of the other contract is counted
and dropped on both sides: under v2 a v1 document has no contract word (contract_mismatch); under v1 a v2 document
has no quality field (missing_fields). The ready line names the contract so the slot's log says which one is running.

WHAT V2 CHANGES HERE. The observation carries two stamps on the robot's monotonic clock: capture_ns, read before
the capture was asked for, and published_ns, the relay's now at this publication. This process checks
0 <= published_ns - capture_ns <= max_age_ns (the observation's OWN bound, derived by its source from the perception
deadline and the relay's bounded waits) and drops the observation otherwise; it steps the controller with
published_ns as now, so the command's timestamp_ns EQUALS published_ns (the relay binds the answer on that) and
its expiry_ns is published_ns + the command TTL. Because the relay publishes with a new published_ns every period,
successive commands are strictly newer for the stable motion gateway, which v1's capture-stamped commands were not.
v2 has no graded quality: the pipeline's validity predicate is binary and an observation that fails it is never
published, so the controller is handed quality 1.0 against a v2 minimum of 0.0.

Usage:
  mobile_approach_service.py --observations ipc://PATH --commands ipc://PATH [--contract v1|v2] [--identity TEXT] [--once]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import signal
import sys
from dataclasses import asdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from autonomy.motion.mobile_approach import (  # noqa: E402
    CONTRACT_V2,
    FRAME_ANCHOR_REACH,
    V2_MAX_ADVANCE_M,
    ControllerConfig,
    MobileApproachController,
    TargetObservation,
    TargetObservationV2,
)

#: How long a receive waits before the loop checks whether it was asked to stop. A wait, not a measurement.
POLL_TIMEOUT_MS = 200
#: The identity used when the supervising configuration named none. It is reported as unknown, never invented.
UNKNOWN_IDENTITY = "unidentified"
#: The two contracts this process can be launched on. v1 is the default so the deployed v1 stays as it is.
CONTRACT_V1_NAME = "v1"
CONTRACT_V2_NAME = "v2"
CONTRACTS = (CONTRACT_V1_NAME, CONTRACT_V2_NAME)

#: The eleven v2 fields, as the schema requires them; nothing else is accepted (additionalProperties false).
V2_FIELDS = ("contract", "target_id", "capture_ns", "published_ns", "motion_epoch", "frame", "x_m", "y_m",
             "max_age_ns", "stationary", "observation_id")
_TARGET_ID = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_OBSERVATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,159}$")


def _integer(value, *, minimum: int = 0, exclusive: bool = False) -> bool:
    """An integer of the declared kind: a JSON integer (a bool is not one), at or above the schema's minimum."""
    if isinstance(value, bool) or not isinstance(value, int):
        return False
    return value > minimum if exclusive else value >= minimum


def _number(value) -> bool:
    """A finite JSON number (a bool is not one; NaN and the infinities are not finite)."""
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


def _identifier(value, pattern) -> bool:
    """A string of the schema's declared pattern, whole (the two id fields; anything else, a number included, is not one).
    fullmatch, not match: a '$' in a Python pattern also matches before a trailing newline, and an id with one would
    otherwise be answered with a target_id outside the frozen DesiredBaseMotion schema."""
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def parse_observation_v2(payload: bytes):
    """One TargetObservationV2 from the wire, or None with a reason, STRICTLY: every field present, no other
    field, every type the schema's, every const the schema's, every range the schema's, and the capture fresh
    against the observation's own bound. The reasons, in the order they are judged:

      not_json, not_an_object, contract_mismatch (the contract word absent or another: a v1 document lands here),
      missing_fields, unknown_fields, field_not_a_number (a field whose value is not the kind the schema declares:
      an integer where an integer is declared, at or above the declared minimum; a finite number where a number is
      declared, so NaN and the infinities are refused here; and, under the same fixed reason code, the two id
      fields when they are not a string of the schema's pattern, since a target_id or observation_id outside its
      pattern would make the answering DesiredBaseMotion violate its own frozen schema), frame_not_anchor_reach,
      y_not_zero, x_out_of_bounds (outside 0..0.120 m), capture_after_publication (published_ns < capture_ns: two
      stamps on one clock cannot read that way), capture_older_than_bound (published_ns - capture_ns > max_age_ns),
      not_stationary.

    The same rule as v1: a message this process cannot read is COUNTED AND DROPPED, never guessed at.
    """
    try:
        document = json.loads(payload.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None, "not_json"
    if not isinstance(document, dict):
        return None, "not_an_object"
    if document.get("contract") != CONTRACT_V2:
        return None, "contract_mismatch"
    if any(name not in document for name in V2_FIELDS):
        return None, "missing_fields"
    if any(name not in V2_FIELDS for name in document):
        return None, "unknown_fields"
    if not (_integer(document["capture_ns"]) and _integer(document["published_ns"]) and _integer(document["motion_epoch"])
            and _integer(document["max_age_ns"], exclusive=True) and _number(document["x_m"]) and _number(document["y_m"])
            and _identifier(document["target_id"], _TARGET_ID)
            and _identifier(document["observation_id"], _OBSERVATION_ID)):
        return None, "field_not_a_number"
    if document["frame"] != FRAME_ANCHOR_REACH:
        return None, "frame_not_anchor_reach"
    if document["y_m"] != 0.0:
        return None, "y_not_zero"
    if not 0.0 <= document["x_m"] <= V2_MAX_ADVANCE_M:
        return None, "x_out_of_bounds"
    age_ns = document["published_ns"] - document["capture_ns"]
    if age_ns < 0:
        return None, "capture_after_publication"
    if age_ns > document["max_age_ns"]:
        return None, "capture_older_than_bound"
    if document["stationary"] is not True:
        return None, "not_stationary"
    return TargetObservationV2(
        target_id=document["target_id"],
        capture_ns=document["capture_ns"],
        published_ns=document["published_ns"],
        motion_epoch=document["motion_epoch"],
        x_m=float(document["x_m"]),
        max_age_ns=document["max_age_ns"],
        observation_id=document["observation_id"],
    ), None


def parse_observation(payload: bytes):
    """One TargetObservation from the wire, or None with a reason.

    A message this process cannot read is COUNTED AND DROPPED, never guessed at: an approach computed from a
    half-understood observation is worse than no approach, and the mission that is waiting for this capability
    is better told nothing arrived than handed a command built on a field that was not there.
    """
    try:
        document = json.loads(payload.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None, "not_json"
    if not isinstance(document, dict):
        return None, "not_an_object"
    missing = [name for name in ("target_id", "timestamp_ns", "x_m", "y_m", "quality") if name not in document]
    if missing:
        return None, "missing_fields"
    if document.get("frame", "base") != "base":
        return None, "frame_not_base"
    try:
        observation = TargetObservation(
            target_id=str(document["target_id"]),
            timestamp_ns=int(document["timestamp_ns"]),
            x_m=float(document["x_m"]),
            y_m=float(document["y_m"]),
            quality=float(document["quality"]),
        )
    except (TypeError, ValueError):
        return None, "field_not_a_number"
    return observation, None


def command_document(command, identity: str) -> dict:
    """A DesiredBaseMotion as the committed schema declares it, plus nothing.

    The identity travels inside `command_id`, which the schema already bounds, rather than as an added field: the
    schema sets `additionalProperties: false` and a capability is not a reason to widen a frozen contract.
    """
    document = asdict(command)
    # The fallback is applied HERE and not only where a service is constructed, so a command built by any caller
    # still carries an identity that says it is unknown rather than an empty one that reads as a name.
    document["command_id"] = f"{identity or UNKNOWN_IDENTITY}.{document['command_id']}"
    return document


class ApproachService:
    """The loop, with its sockets injected so it can be driven without a transport in a test."""

    def __init__(self, receive, publish, *, identity: str = UNKNOWN_IDENTITY, config=None, contract: str = CONTRACT_V1_NAME):
        if contract not in CONTRACTS:
            raise ValueError(f"contract must be one of {CONTRACTS}, not {contract!r}")
        self._receive, self._publish = receive, publish
        self.identity = identity or UNKNOWN_IDENTITY
        self.contract = contract
        if config is None:
            config = ControllerConfig.contract_v2() if contract == CONTRACT_V2_NAME else ControllerConfig()
        self.controller = MobileApproachController(config)
        self.published = 0
        self.dropped: dict = {}
        self.stopping = False
        self.last_state = None
        self.last_reason = None

    def stop(self) -> None:
        self.stopping = True

    def step(self) -> bool:
        """One observe-compute-publish cycle. True when a command was published."""
        payload = self._receive(POLL_TIMEOUT_MS)
        if payload is None:
            return False
        if self.contract == CONTRACT_V2_NAME:
            received, reason = parse_observation_v2(payload)
            # The freshness of the capture was judged above against the observation's own bound; what the
            # controller steps on is the projection stamped with the publication, so that the command it answers
            # with carries timestamp_ns == published_ns and expiry_ns == published_ns + TTL.
            observation = None if received is None else received.controller_input()
        else:
            observation, reason = parse_observation(payload)
        if observation is None:
            self.dropped[reason] = self.dropped.get(reason, 0) + 1
            return False
        # The controller answers (command, state, reason). The state and the reason are DIAGNOSTICS and are kept
        # here rather than published: the committed DesiredBaseMotion schema sets additionalProperties false, and a
        # capability is not a reason to widen a frozen contract. A cycle that produced no command is not a failure;
        # it is the controller declining to command, which is exactly what TARGET_LOST and the deadband mean.
        # Under v1 the observation's stamp is the capture's; under v2 it is the publication's (see above).
        command, state, reason = self.controller.step(observation, observation.timestamp_ns)
        self.last_state, self.last_reason = state, reason
        if command is None:
            return False
        self._publish(json.dumps(command_document(command, self.identity), separators=(",", ":")).encode("utf-8"))
        self.published += 1
        return True

    def run(self, *, once: bool = False) -> int:
        while not self.stopping:
            published = self.step()
            if once and published:
                break
        return self.published


def zmq_endpoints(observations: str, commands: str):
    """The two sockets, bound and connected the way the spec's recommendation describes. Imported late so that
    every unit of this module can be exercised without pyzmq present."""
    import zmq

    context = zmq.Context.instance()
    inbound = context.socket(zmq.SUB)
    inbound.setsockopt(zmq.SUBSCRIBE, b"")
    inbound.setsockopt(zmq.LINGER, 0)
    inbound.connect(observations)
    outbound = context.socket(zmq.PUB)
    outbound.setsockopt(zmq.LINGER, 0)
    outbound.bind(commands)

    def receive(timeout_ms: int):
        if inbound.poll(timeout_ms) == 0:
            return None
        return inbound.recv()

    return receive, outbound.send, lambda: (inbound.close(), outbound.close())


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="mobile_approach_service", description=__doc__.splitlines()[0])
    parser.add_argument("--observations", required=True, help="the endpoint TargetObservation is published on")
    parser.add_argument("--commands", required=True, help="the endpoint this process publishes DesiredBaseMotion on")
    parser.add_argument("--contract", choices=CONTRACTS, default=CONTRACT_V1_NAME,
                        help="the observation contract this process reads: v1 TargetObservation (the default) or v2 TargetObservationV2")
    parser.add_argument("--identity", default=os.environ.get("KC_APPROACH_IDENTITY", UNKNOWN_IDENTITY),
                        help="the module identity attributed to every command this process publishes")
    parser.add_argument("--once", action="store_true", help="publish one command and exit, for a rehearsal")
    args = parser.parse_args(argv)

    receive, publish, close = zmq_endpoints(args.observations, args.commands)
    service = ApproachService(receive, publish, identity=args.identity, contract=args.contract)
    for received in (signal.SIGTERM, signal.SIGINT):
        signal.signal(received, lambda *_: service.stop())
    # The one line this process prints, into the bounded diagnostics log the slot's grant holds. No control value
    # is ever printed here: the publication socket carries all of them.
    print(f"mobile_approach_service: ready (identity {service.identity}, contract {service.contract})", flush=True)
    try:
        service.run(once=args.once)
    finally:
        close()
    print(f"mobile_approach_service: stopped after {service.published} command(s)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
