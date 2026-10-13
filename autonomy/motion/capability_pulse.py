#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""capability_pulse: ONE bounded FORWARD base pulse whose every velocity is the output of the mobile_approach capability
that Cake launched, relayed into the same StableMotionGateway the proven forward primitive uses.

WHY IT EXISTS. The proven forward primitive (run_hybrid_pick_place._make_move_base_sustained, physical pass 2026-09-18)
runs its OWN in-process MobileApproachController. For a target that needs the capability, that is exactly the path the
operator ruled out: "The Cake-managed capability must still produce the approach output consumed by the bounded
gateway. Activation cannot become a flag that lets an unrelated Python path perform the approach." So this relay keeps
the mechanics of that primitive (its 80 ms refresh, its 150 ms lease, its gateway limits, its stop sequence) and replaces
the controller: the observation is published to the capability process over autonomy/motion/capability_link.py, and what
the gateway receives is the command that process published back, or nothing. This module never constructs a
MobileApproachController and never builds a velocity of its own; the one DesiredBaseMotion it makes is the received one
with its expiry shortened. Those mechanics are the primitive's; what the primitive physically did with them does not
transfer: 0.05 m/s is the commanded speed of the in-process primitive run on hardware, and it qualifies nothing about the
relay. This relay's nonzero output has only ever reached RecordingTransport, a FakeBaseMotionTransport subclass in
autonomy/tests/test_capability_pulse.py. The nonzero fixture of the handoff's section 4 is not this relay's: it was
ApproachService inside the test process, x_m 0.95, clamped by the gateway to 0.05 and 0.05 in
FakeBaseMotionTransport.last_sent. Both are software behaviour, and neither is a validated hardware speed.

WHAT IT PUBLISHES. An ACTUAL observation of the selected target, from the required `observe` callable, never one of its
own: "Feed mobile_approach an actual observation of the selected unresolved target, with the coordinate frame, units,
capture identity and freshness required by its existing contract." The synthetic x 1.0 m this relay used to publish is a
rehearsal input. This relay's copy of it moved to autonomy/motion/rehearsal_observation.py, which this module does not
import; the in-process primitive keeps its own copy, which a LIVE run never imports (rehearsal_observation says how).
The LIVE source is autonomy/motion/target_observation_source.py: the executor's own remaining bounded advance, in contract
v2 (kc.target_observation/2, autonomy/schemas/target_observation.v2.schema.json), or a refusal naming every reason there
is none, in which case a pulse of this relay is refused before any Cake read. Every publication is
`observed.wire_v2(published_ns)`, the eleven fields of that schema and nothing else, with published_ns this relay's own
monotonic now at that publication and capture_ns the capture's stamp on the same clock.

WHAT ONE PULSE DOES, in order (move_base):
  1. refuses anything but a forward pulse: vx <= 0 is the proven reverse primitive's, which restores the base and is
     not the approach;
  2. reads the controller's motion epoch and asks `observe()` for the observation of this target; refuses, before any
     admin read, publication or transport, when there is none (target_observation_unavailable, with the source's own
     reasons) or when it fails target_observation_source.check_for_pulse: another target, a frame that is not
     "anchor_reach", a base that was not stationary at the capture, a capture taken before the last commanded motion
     ended (the epoch changed), or older than the observation's OWN max_age_ns (the perception budget plus this relay's
     bounded waits, derived by the source) on this relay's clock;
  3. reads list-slots and get-health ONCE (the snapshot) and refuses, before anything is published or any transport is
     built, unless the capability slot is ACTIVE on the mobile capsule and get-health names its launched pid; refuses
     when another process already listens on the observation endpoint (binding it would take it from its owner) and
     when the capability's command endpoint does not exist;
  4. opens the link, which waits for the capability's own subscription rather than sleeping; the executor's stop flag
     is read before step 3's reads, right after them and once the link is open (THE STOP, below);
  5. only then builds the injected transport, connects it and runs the proven loop: every REFRESH_INTERVAL_S it asks
     `observe()` again, applies the same check (the re-qualification stays a mechanism; with the v2 bound of tens of
     seconds it ends a pulse only when the source or the scene changed), and publishes that observation's
     TargetObservationV2 with published_ns = this relay's now. An observation that fails the check mid-pulse ends the
     publishing at once: the stop sequence below runs, and the pulse is refused after it
     (target_observation_lost_during_pulse). A command is ACCEPTED only when ALL hold:
       - bind_command_to_launched_process accepts its command_id against THE SNAPSHOT (slot ACTIVE on the capsule, the
         pid Cake reports). A label check, not authentication, and said so where it is defined;
       - its target_id is this pulse's target;
       - its timestamp EQUALS the published_ns of the publication just made (not_the_publication_just_made otherwise).
         The capability judges the capture's age against the observation's own max_age_ns and steps its controller with
         published_ns as "now", so its command's timestamp and expiry are anchored to the publication; binding the
         answer to that publication, and checking the capture's age here, is the freshness check that remains. Because
         published_ns advances every period, successive commands are strictly newer for the gateway, which v1's
         capture-stamped commands were not (the gateway rejected every command after the first as replayed);
       - vx > 0, and vy and omega are exactly 0: the only motion this relay can forward is straight ahead.
     An accepted command's expiry becomes min(its own, now + COMMAND_TTL_MS), and it is submitted to a gateway built
     exactly as the proven primitive builds it, GatewayLimits(|vx|, 0.05, 0.01). The gateway's outcome is counted, and
     a command it APPLIED is counted again as applied: accepted says the relay submitted it, applied says the base was
     sent its velocity. tick() runs on every loop, so a capability that goes silent is zeroed by the lease, never by
     this module's judgment;
  6. ends with the proven stop sequence, copied from the reverse primitive of run_single_cube_cycle.make_move_base
     (disable, wait 0.3 s, up to six reads 0.15 s apart, True at the first zero read), then operator stop and
     disconnect whatever happened, and the link is closed only once its observation endpoint is released (or its
     bound ran out, recorded as observation_endpoint_released false);
  7. reads the snapshot AGAIN. A change of admin readability, slot state, capsule, generation or pid while the pulse
     ran is a refusal, and so are an observation lost mid-pulse and a pulse in which not one command was APPLIED by the
     gateway (no_capability_output_applied: a pulse whose every command expired on arrival or was replayed sent the base
     nothing the capability produced, however many were accepted).

THE STOP. The caller's executor checks its stop flag before it calls a pulse. The proven primitive, once called,
connects its transport and drives. This relay first makes two admin reads and waits for the capability's subscription,
each bounded by the caller (5 s each in run_single_cube_cycle). "Stopping the mission must prevent a late perception or
activation result from starting further execution", so `should_stop` (the executor's own flag, a required argument) is
read again before the Cake reads, right after them (a stop takes precedence over what they said) and once the link is
open, which is the last point before a transport exists. A stop seen at any of them is the refusal
STOPPED_BEFORE_MOTION: nothing is commanded, the link is closed, and the return is False. A flag that cannot be read is
taken as set (STOP_FLAG_UNREADABLE). Once the transport is built the pulse runs to its bounded end exactly as the proven
primitive's does (it is never cut or split); the executor's re-observation then measures where the base stopped. The
admin reads and the link wait themselves are not interrupted: a stop during them is honoured when they return, with
nothing moved.

HOW A REFUSAL IS EXPOSED. `last_refusal` is None or a reason code, set by every call; `last_evidence` holds the ids and
numbers of the last pulse (the contract word, generation, pid, the identity and the evidence figures of the observation
it started from, observations published and the distinct stamps they were published with, commands accepted, applied
and dropped by reason, gateway outcomes, stop confirmation). The return value is the proven
primitive's: whether the stop sequence confirmed a zero. A refusal BEFORE motion returns False, because this call then
confirmed no stop at all: a caller that ignored `last_refusal` would still fail closed. A refusal AFTER the stop returns
the stop confirmation it really read, and the caller's own zero and drift checks come first; `last_refusal` is what it
then refuses on.

THE TRANSPORT IS INJECTED as a factory, called once per pulse, because the proven primitive builds, connects and
disconnects one transport per pulse and reads its stop confirmation through that transport's own client
(`transport._client.get_observation()`). This module imports no lerobot and no lerobot_transport: the real
LeKiwiTransport to the robot's own host is the caller's to build. Bounds on the pulse itself (its speed, its duration,
that it is never split) are the executor's (run_single_cube_cycle.base_pulse), which calls this once per pulse.

RESIDUAL, stated rather than hidden: the pid label is not authentication, and any process of the robot account can
publish on the command endpoint or push to the host's loopback command port.
"""
from __future__ import annotations

import json
import math
import os
import sys
import time
from dataclasses import replace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "manipulation"))

from base_rest import reads_at_rest  # noqa: E402
from cake_capability_adapter import ACTIVE, bind_command_to_launched_process  # noqa: E402
from capability_link import CAPABILITY, CapabilityLink, Refused, describe, ipc_path, snapshot, someone_listens  # noqa: E402
from clock import MonotonicClock  # noqa: E402
from mobile_approach import DesiredBaseMotion  # noqa: E402
from stable_motion_gateway import GatewayLimits, GatewayOutcome, StableMotionGateway  # noqa: E402
from target_observation_source import CONTRACT, ObservationUnavailable, check_for_pulse  # noqa: E402

#: The proven forward primitive's cadence and lease (run_hybrid_pick_place._make_move_base_sustained): the lease is a
#: watchdog, never the intended motion duration.
REFRESH_INTERVAL_S = 0.08
COMMAND_TTL_MS = 150
#: The gateway limits beside |vx|, as that primitive sets them.
VY_MAX_MPS, OMEGA_MAX_RPS = 0.05, 0.01

#: last_refusal when the caller's stop flag was set before this pulse commanded anything, and when it could not be read.
STOPPED_BEFORE_MOTION = "operator_abort_before_motion"
STOP_FLAG_UNREADABLE = "stop_flag_unreadable"
#: last_refusal when `observe()` had no observation to give (it raised), before any admin read; the evidence carries the
#: source's own reasons. check_for_pulse's codes are the refusal when an observation was given and does not qualify.
OBSERVATION_UNAVAILABLE = "target_observation_unavailable"
#: last_refusal when the controller's motion epoch could not be read: an unreadable epoch is not a fresh scene.
MOTION_EPOCH_UNREADABLE = "motion_epoch_unreadable"
#: last_refusal, after the stop sequence, when an observation stopped qualifying while the pulse ran.
OBSERVATION_LOST = "target_observation_lost_during_pulse"

_NS_PER_S = 1_000_000_000


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _identity(snap: dict) -> tuple:
    """What must not change while a pulse runs: readability, and the slot's state, capsule, generation and pid."""
    slot, row = snap["slot"], snap["health"]
    return (snap["ok"], None if slot is None else (slot.uuid, slot.state, slot.capsule, slot.generation),
            None if row is None else row.arg1)


class CapabilityPulse:
    """move_base(vx_mps, duration_s) -> stop confirmed, forward only, every velocity the capability's own output.

    `read(op)` answers the admin reads (capability_link.admin_reader on the robot). `transport_factory()` returns a
    fresh, unconnected transport per pulse with connect(), disconnect() and the client its stop reads go through.
    `should_stop()` is the executor's stop flag (THE STOP, in the module docstring); it is required, so that no relay
    exists that could not see a stop. `observe()` returns the ObservedTarget to publish or raises (the LIVE source is
    target_observation_source; a rehearsal passes rehearsal_observation.synthetic_source), and `motion_epoch()` is the
    executor's count of commanded motions that have ended; both are required, so that no relay exists that could publish
    an observation of its own making or one taken before the scene last changed. `clock` and `sleep` are one time source
    (MonotonicClock and time.sleep on the robot); the capture stamps `observe()` returns are read on that same clock.
    `open_link`, `listens` and `exists` are the socket seams; their defaults are capability_link's own.
    """

    def __init__(self, *, read, slot_uuid: str, capsule: str, observations: str, commands: str, transport_factory,
                 target_id: str, should_stop, observe, motion_epoch, clock=None, sleep=time.sleep, open_link=CapabilityLink,
                 listens=someone_listens, exists=os.path.exists, link_timeout_s: float = 5.0, consumed_reads=None):
        if not isinstance(target_id, str) or not target_id:
            raise ValueError("a capability pulse needs the mission target it approaches")
        if not callable(transport_factory):
            raise ValueError("a capability pulse needs a transport factory")
        if not callable(should_stop):
            raise ValueError("a capability pulse needs the stop flag it must honour before any motion")
        if not callable(observe):
            raise ValueError("a capability pulse needs the source of the target observation it publishes")
        if not callable(motion_epoch):
            raise ValueError("a capability pulse needs the executor's motion epoch, to tell a current observation "
                             "from a stale one")
        self._should_stop, self._observe, self._motion_epoch = should_stop, observe, motion_epoch
        self._read, self.slot_uuid, self.capsule = read, slot_uuid, capsule
        self.observations, self.commands = observations, commands
        self._transport_factory, self.target_id = transport_factory, target_id
        self._clock = clock if clock is not None else MonotonicClock()
        self._sleep, self._open_link, self._listens, self._exists = sleep, open_link, listens, exists
        self._link_timeout_s = link_timeout_s
        # The attribute the executor's events read off a base primitive; None unless the caller's client stamps its reads.
        self.consumed_reads = consumed_reads
        self.last_refusal = None
        self.last_evidence = None
        self.pulses = []

    def __call__(self, vx_mps: float, duration_s: float) -> bool:
        return self.move_base(vx_mps, duration_s)

    # ---- one pulse --------------------------------------------------------------------------------------------------

    def move_base(self, vx_mps: float, duration_s: float) -> bool:
        evidence = {"contract": CONTRACT, "target_id": self.target_id, "vx_mps": vx_mps, "duration_s": duration_s, "slot_generation": None,
                    "producer_pid": None, "observation": None, "observation_evidence": None, "observation_lost": None, "observations_published": 0,
                    "published_stamps": 0, "commands_accepted": 0, "commands_applied": 0, "commands_dropped": {}, "gateway_outcomes": {},
                    "commanded": False, "stop_confirmed": None, "refusal": None}
        self.last_refusal, self.last_evidence = None, evidence
        self.pulses.append(evidence)
        if not (_number(vx_mps) and vx_mps > 0):
            return self._refuse(evidence, "not_a_forward_pulse", "the capability relay drives forward only; the reverse "
                                "restores the base through the proven primitive")
        if not (_number(duration_s) and duration_s > 0):
            return self._refuse(evidence, "pulse_duration_not_positive", "a pulse needs a positive, finite duration")
        try:
            observation_path, command_path = ipc_path(self.observations), ipc_path(self.commands)
        except Refused as refusal:
            return self._refuse(evidence, "endpoint_not_ipc", str(refusal))
        stop = self._stop_refusal()
        if stop is not None:
            return self._refuse(evidence, stop, "the stop was requested before this pulse read the capability; nothing "
                                "was read, published or commanded")
        # THE OBSERVATION FIRST, before any Cake read: a pulse with nothing true to publish reads nothing, binds nothing
        # and builds no transport. The epoch is read once, here: every observation this pulse publishes must carry it.
        try:
            epoch = self._motion_epoch()
        except Exception as error:  # noqa: BLE001 -- the class only
            return self._refuse(evidence, MOTION_EPOCH_UNREADABLE, type(error).__name__)
        observed, code, detail = self._qualified(epoch)
        if code is not None:
            return self._refuse(evidence, code, detail)
        evidence["observation"], evidence["observation_evidence"] = observed.identity(), observed.evidence()
        try:
            before = snapshot(self._read, self.slot_uuid)
        except Exception as error:  # noqa: BLE001 -- the class only: a message can name a path
            return self._refuse(evidence, "admin_read_failed", type(error).__name__)
        evidence["cake_before"] = describe(before)
        stop = self._stop_refusal()  # the admin reads above can take seconds: a stop during them outranks what they said
        if stop is not None:
            return self._refuse(evidence, stop, "the stop was requested while this pulse read the capability; nothing was "
                                "published or commanded")
        slot, row = before["slot"], before["health"]
        if slot is None or slot.state != ACTIVE or slot.capsule != self.capsule:
            return self._refuse(evidence, "capability_not_active", "the mobile_approach slot is not ACTIVE on the mobile "
                                "capsule; nothing was published")
        if row is None or not row.arg1:
            return self._refuse(evidence, "capability_pid_absent", "get-health names no launched pid for the "
                                "mobile_approach slot; nothing was published")
        evidence["slot_generation"], evidence["producer_pid"] = slot.generation, row.arg1
        if self._listens(observation_path):
            return self._refuse(evidence, "observation_endpoint_owned", "another process already listens on the "
                                "observation endpoint; it was left alone")
        if not self._exists(command_path):
            return self._refuse(evidence, "capability_command_endpoint_absent", "the capability's command endpoint does "
                                "not exist: the capability process is not running")
        try:
            link = self._open_link(self.observations, self.commands, self._link_timeout_s)
        except Refused as refusal:
            return self._refuse(evidence, "capability_link_refused", str(refusal))
        try:
            stop = self._stop_refusal()  # the link's wait can take seconds; the last point before a transport exists
            if stop is not None:
                return self._refuse(evidence, stop, "the stop was requested while the link waited for the capability; the "
                                    "link was closed and nothing was commanded")
            stopped = self._drive(link, before, vx_mps, duration_s, evidence, epoch)
        finally:
            # The observation endpoint released, or not within the link's bound: evidence only. A link that still holds
            # it makes the NEXT pulse refuse (observation_endpoint_owned); this one has already stopped or never started.
            evidence["observation_endpoint_released"] = bool(link.close())
        evidence["stop_confirmed"] = stopped
        try:
            after = snapshot(self._read, self.slot_uuid)
        except Exception as error:  # noqa: BLE001
            self._refuse(evidence, "capability_unverified_after_pulse", type(error).__name__)
            return stopped
        evidence["cake_after"] = describe(after)
        if _identity(after) != _identity(before):
            self._refuse(evidence, "capability_changed_during_pulse", "the capability slot, its generation, capsule or "
                         "launched pid changed while the pulse ran")
        elif evidence.get("observation_lost") is not None:
            self._refuse(evidence, OBSERVATION_LOST, "the target observation stopped qualifying while the pulse ran "
                         f"({evidence['observation_lost']}); nothing further was published and the stop sequence ran")
        elif evidence["commands_applied"] == 0:
            self._refuse(evidence, "no_capability_output_applied", "not one command of the launched capability was "
                         "applied by the gateway during the pulse; the base was sent no velocity it produced")
        return stopped

    def _qualified(self, epoch: int):
        """(the observation, None, None) when `observe()` gave one that may feed this pulse now, else (None, the refusal
        code, the detail): OBSERVATION_UNAVAILABLE with the source's own reasons when it raised, check_for_pulse's code
        when what it gave does not qualify."""
        try:
            observed = self._observe()
        except ObservationUnavailable as unavailable:
            return None, OBSERVATION_UNAVAILABLE, ",".join(unavailable.reasons)
        except Exception as error:  # noqa: BLE001 -- a source that broke has no observation either; the class only
            return None, OBSERVATION_UNAVAILABLE, type(error).__name__
        code = check_for_pulse(observed, target_id=self.target_id, motion_epoch=epoch, now_ns=self._clock.now_ns())
        if code is not None:
            return None, code, "the observation the source gave does not qualify for this pulse; nothing was published from it"
        return observed, None, None

    def _stop_refusal(self):
        """STOPPED_BEFORE_MOTION when the executor's stop flag is set, STOP_FLAG_UNREADABLE when reading it raised (an
        unreadable stop is not a permission to move), else None."""
        try:
            return STOPPED_BEFORE_MOTION if self._should_stop() else None
        except Exception:  # noqa: BLE001 -- whatever broke, the side that moves nothing is the one taken
            return STOP_FLAG_UNREADABLE

    def _refuse(self, evidence: dict, code: str, detail: str) -> bool:
        self.last_refusal = code
        evidence["refusal"], evidence["refusal_detail"] = code, detail
        return False

    def _drive(self, link, before: dict, vx_mps: float, duration_s: float, evidence: dict, epoch: int) -> bool:
        """The proven loop with the controller replaced by the capability's answers, then the proven stop sequence. Every
        period, the first included, asks the source again: the observation that qualified before the Cake reads may have
        aged past its own bound during them and the link wait, or the source may have nothing any more. Each publication
        carries a fresh published_ns (this relay's now), and only an answer stamped with it is accepted."""
        transport = self._transport_factory()
        transport.connect()
        evidence["commanded"] = True
        gateway = StableMotionGateway(transport, self._clock, GatewayLimits(vx_max_mps=abs(vx_mps), vy_max_mps=VY_MAX_MPS,
                                                                             omega_max_rps=OMEGA_MAX_RPS))
        gateway.enable_autonomous()
        try:
            period_ns, duration_ns = round(REFRESH_INTERVAL_S * _NS_PER_S), round(duration_s * _NS_PER_S)
            t0, last_stamp = self._clock.now_ns(), None
            while self._clock.now_ns() - t0 < duration_ns:
                period_start = self._clock.now_ns()
                observed, code, _detail = self._qualified(epoch)
                if code is not None:
                    # Nothing true to publish any more: publishing ENDS here, and the stop sequence below zeroes the base
                    # now rather than at the lease. The pulse is refused after that sequence (OBSERVATION_LOST).
                    evidence["observation_lost"] = code
                    break
                stamp = self._clock.now_ns()  # published_ns: this publication's own stamp, what the answer must echo
                link.publish(json.dumps(observed.wire_v2(stamp), separators=(",", ":")).encode("utf-8"))
                evidence["observations_published"] += 1
                if stamp != last_stamp:
                    evidence["published_stamps"], last_stamp = evidence["published_stamps"] + 1, stamp
                period_end = period_start + period_ns
                while True:
                    left_ms = int((period_end - self._clock.now_ns()) / 1_000_000)
                    payload = link.receive(left_ms) if left_ms > 0 else None
                    if payload is None or self._consider(payload, stamp, before, gateway, evidence):
                        break
                gateway.tick()  # a capability that went silent is zeroed here by the lease of its last command
                left_ns = period_end - self._clock.now_ns()
                if left_ns > 0:
                    self._sleep(left_ns / _NS_PER_S)
            # The proven stop sequence, as run_single_cube_cycle.make_move_base's reverse runs it: disable (an explicit
            # zero), let the base settle, then up to six reads, True at the first that reads at rest (base_rest.reads_at_rest,
            # no wheel above one velocity count; formerly exact zero on all three axes, operator ruling 2026-09-27).
            gateway.disable_autonomous()
            self._sleep(0.3)
            for _ in range(6):
                observation = transport._client.get_observation()
                if reads_at_rest(observation):
                    return True
                self._sleep(0.15)
            return False
        finally:
            gateway.operator_stop()
            transport.disconnect()

    def _consider(self, payload: bytes, stamp: int, before: dict, gateway, evidence: dict) -> bool:
        """True when this answer was accepted and submitted; every other answer is counted by why it was dropped."""
        command, reason = self._judge(payload, stamp, before)
        if command is None:
            dropped = evidence["commands_dropped"]
            dropped[reason] = dropped.get(reason, 0) + 1
            return False
        expiry_ns = min(command.expiry_ns, self._clock.now_ns() + COMMAND_TTL_MS * 1_000_000)
        outcome = gateway.submit_command(replace(command, expiry_ns=expiry_ns))[3]
        evidence["commands_accepted"] += 1
        if outcome is GatewayOutcome.APPLIED:  # the base was sent this velocity; anything else (expired, replayed, disabled) was not
            evidence["commands_applied"] += 1
        evidence["gateway_outcomes"][outcome.value] = evidence["gateway_outcomes"].get(outcome.value, 0) + 1
        return True

    def _judge(self, payload: bytes, stamp: int, before: dict):
        """(the command, None) when every acceptance condition holds, else (None, the first one that did not)."""
        try:
            document = json.loads(payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None, "not_json"
        if not isinstance(document, dict):
            return None, "not_an_object"
        try:
            command = DesiredBaseMotion(**document)
        except TypeError:
            return None, "not_a_desired_base_motion"
        binding = bind_command_to_launched_process(command.command_id, capability=CAPABILITY, slot=before["slot"],
                                                   active_capsule=self.capsule, health=before["health"])
        if not binding.accepted:
            return None, binding.reason
        if command.target_id != self.target_id:
            return None, "target_id_mismatch"
        if isinstance(command.timestamp_ns, bool) or not isinstance(command.timestamp_ns, int) or command.timestamp_ns != stamp:
            return None, "not_the_publication_just_made"
        if not all(_number(value) for value in (command.vx_mps, command.vy_mps, command.omega_rps, command.expiry_ns)):
            return None, "not_finite"
        if command.vx_mps <= 0:
            return None, "vx_not_forward"
        if command.vy_mps != 0 or command.omega_rps != 0:
            return None, "not_straight_ahead"
        return command, None
