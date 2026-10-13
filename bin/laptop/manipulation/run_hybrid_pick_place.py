#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""Real-hardware wiring for autonomy/manipulation/hybrid_pick_place.py.

This is ONLY the dependency-injection wiring -- every actual physical
behavior (bounded interpolation, wrist_roll freeze, gripper values,
max_relative_target clamp, refreshed-lease sustained base motion) is the
same code already proven physically
(docs/autonomous-demo/physical-pass-2026-09-18.md):
  - move_arm(): canonical_handoff.run_canonical_handoff() +
    action_adapter.freeze_wrist_roll() + action_adapter.
    manipulation_action_to_robot_command(), the same primitive
    .local-dev/move_to_target.py used during development.
  - move_base_sustained(): MobileApproachController -> StableMotionGateway
    -> LeKiwiTransport, the same refreshed-lease pattern validated in
    .local-dev/sustained_pulse_test.py (TTL=watchdog, not TTL=duration).
None of those primitives are reimplemented here.

Also wires the real autonomy/motion/capability_registry.py CapabilityRegistry
into PickPlaceDeps.capability_available/wait_for_capability_change -- this is
the tracked entry point that reproduces the CAKE MOBILE_CAPABILITY boundary
(WAITING_FOR_CAPABILITY -> invalid-candidate-rejected -> valid-candidate-
admitted -> MOBILE_APPROACH) from a normal terminal. One CapabilityRegistry
instance is created per run; mobile_approach starts unavailable. When the
state machine reaches WAITING_FOR_CAPABILITY it prompts `CAKE> ` in this same
terminal (dispatch_cake_command()): `INVALID` submits the known unadmitted
capsule identity, `VALID` submits the known admitted one, both through
registry.submit_candidate() -- never a direct state or availability change.
Control returns to the state machine's own re-check loop either way. See
docs/autonomous-demo/physical-pass-2026-09-18.md for the exact capsule
identities this reproduces and mission_id continuity.

This is the real single-terminal entry point: confirm() and the CAKE>
prompt both use plain input(), so this script expects a live TTY. For a
chat-driven operator session without a live terminal, see
.local-dev/interactive_hybrid_pick_place.py (gitignored scratch, not part of
the tracked demo, and no longer required to reproduce the capability
boundary -- this script now does that on its own).

Mandatory before running: the standing fresh-host/torque/Present-vs-Goal
safety check documented in the reproduction checklist
(docs/autonomous-demo/physical-pass-2026-09-18.md) -- this script assumes
an already-connected, already-torque-safe robot.

Usage:
  KC_REMOTE_IP=<robot-ip> KC_ROBOT_ID=<robot-id> \\
    python3 bin/laptop/manipulation/run_hybrid_pick_place.py
"""

import argparse
import os
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "autonomy" / "manipulation"))
sys.path.insert(0, str(ROOT / "autonomy" / "motion"))

from action_adapter import (  # noqa: E402
    freeze_wrist_roll,
    manipulation_action_to_robot_command,
)
from base_rest import reads_at_rest  # noqa: E402
from canonical_handoff import INTERP_JOINTS, run_canonical_handoff  # noqa: E402
from capability_registry import Candidate, CapabilityRegistry  # noqa: E402
from hybrid_pick_place import (  # noqa: E402
    JOINT_ORDER,
    MOBILE_APPROACH_CAPABILITY,
    PickPlaceDeps,
    run_hybrid_pick_place,
)

FPS = 30

# The exact capsule identities proven live on 2026-09-18
# (docs/autonomous-demo/physical-pass-2026-09-18.md, mission 72e9c281fe41):
# CAKE_VALID_CANDIDATE_IDENTITY is the only member of the admitted set,
# CAKE_INVALID_CANDIDATE_IDENTITY is deliberately outside it.
CAKE_VALID_CANDIDATE_IDENTITY = "mobile-approach-real-build"
CAKE_INVALID_CANDIDATE_IDENTITY = "mobile-approach-invalid-candidate"
BARE_TO_ARM_KEY = {name: f"arm_{name}.pos" for name in JOINT_ORDER}
BARE_TO_ARM_KEY["gripper"] = "arm_gripper.pos"
INTERP_JOINTS_BARE = {k.removeprefix("arm_").removesuffix(".pos") for k in INTERP_JOINTS}


def build_parser():
    p = argparse.ArgumentParser(prog="run_hybrid_pick_place.py", description="Real-hardware run of the packaged hybrid pick-place state machine.")
    p.add_argument("--remote-ip", default=os.environ.get("KC_REMOTE_IP"))
    p.add_argument("--robot-id", default=os.environ.get("KC_ROBOT_ID"))
    p.add_argument("--base-vx-mps", type=float, default=0.05)
    p.add_argument("--base-move-duration-s", type=float, default=1.5)
    return p


def _read_pose_bare(robot) -> dict:
    obs = robot.get_observation()
    pose = {name: float(obs[f"arm_{name}.pos"]) for name in JOINT_ORDER}
    pose["gripper"] = float(obs["arm_gripper.pos"])
    return pose


def _read_base(robot) -> dict:
    obs = robot.get_observation()
    return {"x_vel": obs["x.vel"], "y_vel": obs["y.vel"], "theta_vel": obs["theta.vel"]}


def _make_move_arm(robot):
    def move_arm(overrides: dict, duration_s: float, hold_s: float = 0.0) -> dict:
        current_bare = _read_pose_bare(robot)
        wrist_roll_hold = current_bare["wrist_roll"]

        current_pose = {BARE_TO_ARM_KEY[k]: v for k, v in current_bare.items() if k in INTERP_JOINTS_BARE}
        target_pose = dict(current_pose)
        for name, value in overrides.items():
            target_pose[BARE_TO_ARM_KEY[name]] = value

        n_steps = max(1, round(duration_s * FPS))
        control_interval = 1.0 / FPS

        def send_tick(waypoint):
            t0 = time.perf_counter()
            action = freeze_wrist_roll(waypoint, wrist_roll_hold)
            robot_command = manipulation_action_to_robot_command(action)
            robot.send_action(robot_command)
            time.sleep(max(control_interval - (time.perf_counter() - t0), 0.0))

        status = run_canonical_handoff(current_pose, target_pose, send_tick, lambda: False, n_steps=n_steps)
        if status != "COMPLETED":
            raise RuntimeError(f"move_arm: canonical handoff did not complete (status={status})")

        if hold_s > 0:
            time.sleep(hold_s)

        return _read_pose_bare(robot)

    return move_arm


def _make_move_base_sustained(remote_ip, robot_id):
    def move_base_sustained(vx_mps: float, duration_s: float) -> bool:
        # Exact refreshed-lease pattern validated in
        # .local-dev/sustained_pulse_test.py: TTL is a watchdog, never the
        # intended motion-duration primitive.
        from clock import MonotonicClock
        from lerobot_transport import LeKiwiTransport
        from mobile_approach import MobileApproachController, TargetObservation
        from stable_motion_gateway import GatewayLimits, StableMotionGateway

        REFRESH_INTERVAL_S = 0.08
        COMMAND_TTL_MS = 150

        transport = LeKiwiTransport(remote_ip=remote_ip, robot_id=robot_id)
        transport.connect()
        clock = MonotonicClock()
        limits = GatewayLimits(vx_max_mps=abs(vx_mps), vy_max_mps=0.05, omega_max_rps=0.01)
        gateway = StableMotionGateway(transport, clock, limits)
        gateway.enable_autonomous()
        controller = MobileApproachController()

        try:
            t0 = time.perf_counter()
            target_x = 1.0 if vx_mps >= 0 else None
            while (time.perf_counter() - t0) < duration_s:
                now_ns = clock.now_ns()
                if target_x is not None:
                    obs = TargetObservation(target_id="hybrid_pick_place", timestamp_ns=now_ns, x_m=target_x, y_m=0.0, quality=0.9)
                    command, _, _ = controller.step(obs, now_ns=now_ns)
                    command = command.__class__(**{**command.__dict__, "expiry_ns": now_ns + COMMAND_TTL_MS * 1_000_000})
                    gateway.submit_command(command)
                else:
                    # Reverse motion bypasses the controller's positive-only
                    # envelope, same as .local-dev/sustained_reverse_test.py --
                    # the gateway safety layer is still not bypassed.
                    from transport import DesiredBaseMotion

                    gateway.submit_command(
                        DesiredBaseMotion(
                            command_id="hybrid_pick_place",
                            target_id="hybrid_pick_place",
                            timestamp_ns=now_ns,
                            vx_mps=vx_mps,
                            vy_mps=0.0,
                            omega_rps=0.0,
                            expiry_ns=now_ns + COMMAND_TTL_MS * 1_000_000,
                        )
                    )
                time.sleep(REFRESH_INTERVAL_S)

            gateway.disable_autonomous()
            time.sleep(0.3)

            for _ in range(6):
                obs = transport._client.get_observation()
                if reads_at_rest(obs):  # formerly exact zero on all three axes (operator ruling 2026-09-27)
                    return True
                time.sleep(0.15)
            return False
        finally:
            gateway.operator_stop()
            transport.disconnect()

    return move_base_sustained


def _confirm(prompt: str) -> str:
    return input(prompt)


def _log(event: str, **fields):
    ts = time.strftime("%H:%M:%S")
    if event in ("STATE_ENTER", "STATE_PASS", "STATE_ABORT"):
        print(f"[{ts}] {event}: {fields.get('state', '')}")
        extra = {k: v for k, v in fields.items() if k not in ("state",)}
        if extra:
            print(f"    {extra}")
    else:
        print(f"[{ts}] {event}: {fields}")


def _make_mission_aware_log(base_log, mission_id: str):
    """Wraps base_log so every event carries the same mission_id -- proving
    one mission identity spans WAITING_FOR_CAPABILITY -> capability
    activation -> MOBILE_APPROACH, the same pattern
    docs/autonomous-demo/physical-pass-2026-09-18.md documents live. Never
    regenerates the id; mission_id is fixed once by the caller."""

    def log(event: str, **fields):
        if event == "STATE_ENTER":
            print(f"MISSION_ID: {mission_id}")
        base_log(event, mission_id=mission_id, **fields)

    return log


def _make_capability_registry(log) -> CapabilityRegistry:
    """The single CapabilityRegistry instance for a run: mobile_approach
    starts unavailable, and the admitted set contains exactly the capsule
    identity proven live on 2026-09-18 -- the same Path A semantics
    documented in autonomy/motion/capability_registry.py's own docstring.
    No admission logic lives here; this only configures the existing
    registry."""
    return CapabilityRegistry(admitted_identities=frozenset({CAKE_VALID_CANDIDATE_IDENTITY}), log=log)


def dispatch_cake_command(command: str, registry: CapabilityRegistry, capability_name: str = MOBILE_APPROACH_CAPABILITY):
    """Pure command -> registry-submission mapping for the CAKE> terminal
    prompt: INVALID submits the known unadmitted candidate, VALID submits
    the known admitted one, anything else submits nothing. Always goes
    through registry.submit_candidate() -- never sets availability directly
    and never touches the state machine. Returns ADMITTED/REJECTED (from
    capability_registry), or None if `command` was not recognized."""
    word = command.strip().upper()
    if word == "INVALID":
        return registry.submit_candidate(capability_name, Candidate(CAKE_INVALID_CANDIDATE_IDENTITY))
    if word == "VALID":
        return registry.submit_candidate(capability_name, Candidate(CAKE_VALID_CANDIDATE_IDENTITY))
    return None


def _make_wait_for_capability_change(registry: CapabilityRegistry):
    """Terminal-backed replacement for CapabilityRegistry.wait_for_change():
    prompts once in the SAME terminal used for every other operator gate,
    dispatches the typed command through the registry, and returns control
    to the state machine's own WAITING_FOR_CAPABILITY loop (autonomy/
    manipulation/hybrid_pick_place.py::_state_waiting_for_capability), which
    re-checks capability_available() itself -- this function never changes
    state directly."""

    def wait_for_capability_change():
        command = input("CAKE> ")
        if dispatch_cake_command(command, registry) is None:
            print(f"CAKE: unrecognized command {command!r} (expected INVALID or VALID)")

    return wait_for_capability_change


def main(argv=None):
    args = build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    missing = [n for n, v in (("--remote-ip", args.remote_ip), ("--robot-id", args.robot_id)) if not v]
    if missing:
        sys.exit("run_hybrid_pick_place: missing " + ", ".join(missing) + " (or the matching KC_* environment variables)")

    from lerobot.robots.lekiwi import LeKiwiClient, LeKiwiClientConfig

    mission_id = uuid.uuid4().hex[:12]
    mission_log = _make_mission_aware_log(_log, mission_id)
    registry = _make_capability_registry(mission_log)

    robot = LeKiwiClient(LeKiwiClientConfig(remote_ip=args.remote_ip, id=args.robot_id))
    robot.connect()
    try:
        deps = PickPlaceDeps(
            read_pose=lambda: _read_pose_bare(robot),
            read_base=lambda: _read_base(robot),
            move_arm=_make_move_arm(robot),
            move_base_sustained=_make_move_base_sustained(args.remote_ip, args.robot_id),
            confirm=_confirm,
            log=mission_log,
            capability_available=registry.has_capability,
            wait_for_capability_change=_make_wait_for_capability_change(registry),
        )
        result = run_hybrid_pick_place(deps, base_vx_mps=args.base_vx_mps, base_move_duration_s=args.base_move_duration_s)
        print()
        print(f"MISSION_ID: {mission_id}")
        print(f"FINAL STATE: {result.final_state}")
        print(f"abort_reason: {result.abort_reason}")
        print(f"final_pose: {result.final_pose}")
    finally:
        robot.disconnect()


if __name__ == "__main__":
    main()
