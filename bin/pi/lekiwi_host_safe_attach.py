#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""Verify-first attach of the STOCK LeKiwi host to an arm that is ALREADY HOLDING
its pose (acquired beforehand, see docs/autonomous-demo/robot-host-setup.md), run
ON THE ROBOT COMPUTER in the FOREGROUND of a real terminal, with --robot.cameras={}.
bin/pi/start-host.sh is the launcher that copies this file over and starts it.

The stock LeKiwi.configure() switches torque OFF on all nine motors, rewrites
the configuration and switches torque back ON: on a holding arm that is a limp
interval followed by a jerk. This wrapper replaces two methods, and a third one
in scan mode only:

  LeKiwi.configure  ->  VERIFIES FIRST, BY READS ONLY. Requires: all six arm
                        motors torque ON in POSITION mode with |goal - present|
                        small, arm PID as configured, Phase bit 0x10 clear on all
                        nine motors; base motors in VELOCITY mode with goal
                        velocity 0 and a present velocity of at most ONE
                        velocity count (50 raw, either sign: the dither of a
                        wheel at rest). If any check fails the port is
                        closed with torque UNTOUCHED, nothing having been
                        written, and the process exits: it never falls back to
                        the stock configure.
                        When every check holds, NO ARM REGISTER IS WRITTEN, but
                        the startup is NOT read-only: it DOES WRITE TO THE BASE.
                        For each wheel whose torque is OFF it writes
                        Goal_Velocity 0, reads that back, then writes
                        Torque_Enable 1 (the zero-speed wheel hold), and finally
                        reads mode / goal velocity / torque of every wheel back.
                        A wheel that is already holding is not written. Should
                        one of those two readbacks fail, the process exits the
                        same way, and its "nothing was written" line is then
                        inexact: the base writes made up to that point stand.
  LeKiwi.calibrate  ->  refuses (never rewrites Homing_Offset under a holding arm).
  LeKiwi.send_action -> scan mode only (KC_SCAN_MODE=1): the gripper key is
                        removed from every incoming action before the stock
                        send_action sees it; normal mode keeps the stock method.

HOST BOUNDARY, OFF unless KC_HOST_BOUNDARY=1. With the variable unset nothing below is imported and nothing more is
replaced. With it set, bin/pi/host_boundary.py (it must sit next to this file) wraps two more things, and adds no
motor access of any kind:

  LeKiwiHost.__init__    -> after the stock one, the conflating command socket is replaced, on the same port, by a
                            gate that hands the loop only commands carrying the live lease; anything else is counted
                            and dropped as if the socket were empty, so it never refreshes the base watchdog.
  LeKiwi.get_observation -> the stock reading, plus kc.session / kc.seq / kc.t_mono_ns, plus a read-only tap.

Its control and tap sockets are bound when this file is loaded, before main() and so before the bus is opened: a busy
port ends the process with the robot untouched. See docs/autonomous-demo/host-boundary.md.

HOST COMMAND LOOPBACK, OFF unless KC_HOST_COMMAND_LOOPBACK=1, which the Cake-supervised launch (run-host.sh, rendered
from bin/templates/run-host.sh.in) sets and nothing else does. The stock host binds its command socket on EVERY
interface and has no setting for the address, so any process that can reach the robot could push a velocity command.
With the variable set, one more thing is wrapped, and no motor access of any kind is added:

  LeKiwiHost.__init__    -> after the stock one, the command socket is replaced by the same kind of socket with the
                            same option (PULL, CONFLATE 1) on the same port, bound to 127.0.0.1 ONLY. The endpoint
                            actually bound is read back, and anything else stops the host before its loop starts.

The observation socket is left as the stock host binds it. This is a NETWORK restriction only: it decides who can
reach the command socket (processes on the robot, and whatever an ssh forward on the robot's side hands to it), not
who may command: the host checks nothing itself, and the session, ownership and admission checks apply only to commands
routed through the robot-side service. It is not combined with the host boundary, which replaces the same socket
itself: the constructor refuses when it finds the command socket already replaced.

Everything else is the stock host main(). Start it with
--robot.disable_torque_on_disconnect=false so that stopping the host leaves the
arm HOLDING (release it later, deliberately, with the arm supported by hand).
Written against lerobot at commit b4e2d0b6 as installed on the robot computer.
"""
import os
import sys

from lerobot.robots.lekiwi.lekiwi import LeKiwi

# SCAN MODE (KC_SCAN_MODE=1, a temporary demo rule): the five arm joints hold; the gripper is limp (torque OFF, open by
# friction, possibly past its encoder seam) and must NEVER be written: the verify requires its torque OFF and every
# incoming action has its gripper key removed before the stock send_action sees it. Normal mode is unchanged.
SCAN_MODE = os.environ.get("KC_SCAN_MODE") == "1"
GRIPPER = "arm_gripper"
ATTACH_TOLERANCE_TICKS = 12  # about 1 deg between goal and present on a holding joint
POSITION_MODE, VELOCITY_MODE = 0, 1
# A wheel at rest reads 0 or ONE velocity count of either sign. Present_Velocity moves in steps of 50 (one encoder step per
# 20 ms window), and a wheel standing still, held or free, dithers by one step, on one wheel or on two at once
# (autonomy/manipulation/base_rest.py, operator ruling 2026-09-27). The attach takes that much as rest and no more; the goal
# velocity, a command register, must still read exactly 0.
WHEEL_REST_MAX_RAW = 50


def _refuse(robot, problems) -> None:
    print("SAFE ATTACH REFUSED, nothing was written:", flush=True)
    for line in problems:
        print(f"  - {line}", flush=True)
    try:
        robot.bus.disconnect(disable_torque=False)  # so that no destructor can drop torque behind our back
    finally:
        os._exit(3)  # skip every destructor: the arm keeps whatever state it had


def _verify_only_configure(self) -> None:
    bus, arm, base = self.bus, list(self.arm_motors), list(self.base_motors)
    if self.config.disable_torque_on_disconnect or self.cameras:  # the PARSED values: every exit path depends on them
        _refuse(self, ["the parsed configuration would drop torque on disconnect or owns a camera"])
    torque = bus.sync_read("Torque_Enable", arm + base, normalize=False)
    mode = bus.sync_read("Operating_Mode", arm + base, normalize=False)
    present = bus.sync_read("Present_Position", arm, normalize=False)
    goal = bus.sync_read("Goal_Position", arm, normalize=False)
    velocity = bus.sync_read("Present_Velocity", base, normalize=False)
    goal_velocity = bus.sync_read("Goal_Velocity", base, normalize=False)
    problems = []
    if SCAN_MODE:
        if int(torque[GRIPPER]) != 0:
            problems.append("scan mode: the gripper torque is ON; it must be OFF (limp) and is never written by this host")
        arm = [m for m in arm if m != GRIPPER]
    for m in arm:
        if int(torque[m]) != 1:
            problems.append(f"{m}: torque is not ON (the arm was not acquired)")
        if int(mode[m]) != POSITION_MODE:
            problems.append(f"{m}: operating mode {int(mode[m])}, expected POSITION")
        if abs(int(goal[m]) - int(present[m])) > ATTACH_TOLERANCE_TICKS:
            problems.append(f"{m}: goal-present = {int(goal[m]) - int(present[m])} ticks")
        for register, setting in (("P_Coefficient", self.config.arm_p_coefficient), ("I_Coefficient", self.config.arm_i_coefficient), ("D_Coefficient", self.config.arm_d_coefficient)):
            expected = setting[m] if isinstance(setting, dict) else setting
            actual = int(bus.read(register, m, normalize=False))
            if actual != int(expected):
                problems.append(f"{m}: {register} is {actual}, expected {expected}")
    for m in list(self.arm_motors) + base:
        if int(bus.read("Phase", m, normalize=False)) & 0x10:
            problems.append(f"{m}: Phase bit 0x10 is set (position feedback mode differs from what the host expects)")
    for m in base:
        if int(mode[m]) != VELOCITY_MODE:
            problems.append(f"{m}: operating mode {int(mode[m])}, expected VELOCITY")
        if abs(int(velocity[m])) > WHEEL_REST_MAX_RAW or int(goal_velocity[m]) != 0:
            problems.append(f"{m}: present/goal velocity {int(velocity[m])}/{int(goal_velocity[m])}, expected at most one count (|present| <= {WHEEL_REST_MAX_RAW})/0")
    if problems:
        _refuse(self, problems)
    # BASE ZERO-SPEED HOLD (operator ruling 2026-09-20). After a power cycle the wheel motors come up with torque OFF; free
    # wheels dither at two velocity counts when the arm moves and the base-zero guard of the arm executor (tolerance
    # unchanged) then refuses arm motion. The checks above have already established VELOCITY mode, goal velocity 0 and a
    # present velocity of at most one count on every wheel. For a wheel whose torque is OFF: write the zero velocity
    # target FIRST, then enable its torque, then read everything back. A wheel that is already holding is not written. The
    # arm is never written here.
    held = []
    for m in base:
        if int(torque[m]) != 1:
            bus.write("Goal_Velocity", m, 0, normalize=False)
            if int(bus.read("Goal_Velocity", m, normalize=False)) != 0:
                _refuse(self, [f"{m}: the zero velocity target did not read back as 0; its torque was NOT enabled"])
            bus.write("Torque_Enable", m, 1, normalize=False)
            held.append(m)
    readback = {m: tuple(int(bus.read(name, m, normalize=False)) for name in ("Operating_Mode", "Goal_Velocity", "Torque_Enable")) for m in base}
    if any(state != (VELOCITY_MODE, 0, 1) for state in readback.values()):
        _refuse(self, [f"base zero-speed hold not established: (mode, goal velocity, torque) = {readback}"])
    print(f"BASE ZERO-SPEED HOLD: newly enabled {held or 'none (already holding)'}; readback (mode, goal velocity, torque): {readback}", flush=True)
    info = {m: (int(bus.read("Acceleration", m, normalize=False)), int(bus.read("Lock", m, normalize=False))) for m in arm}
    print(f"SAFE ATTACH OK ({'SCAN MODE: five joints, gripper limp and never written' if SCAN_MODE else 'normal mode'}): arm joints holding, goal ~ present (ticks): { {m: int(goal[m]) - int(present[m]) for m in arm} }", flush=True)
    print(f"SAFE ATTACH OK: base present velocity read (raw, one count = {WHEEL_REST_MAX_RAW}): { {m: int(velocity[m]) for m in base} }; NO arm register was written; base writes: {'zero velocity target + torque on ' + str(held) if held else 'none'}. (info: Acceleration, Lock per arm motor: {info})", flush=True)


def _refuse_calibration(self) -> None:
    _refuse(self, ["the servo calibration registers differ from the calibration file; calibration is never written under a holding arm"])


LeKiwi.configure = _verify_only_configure
if SCAN_MODE:
    _stock_send_action = LeKiwi.send_action

    def _send_action_without_gripper(self, action):
        return _stock_send_action(self, {k: v for k, v in action.items() if k != GRIPPER + ".pos"})

    LeKiwi.send_action = _send_action_without_gripper
LeKiwi.calibrate = _refuse_calibration

from lerobot.robots.lekiwi.lekiwi_host import main  # noqa: E402

# --- HOST BOUNDARY begin: OFF unless KC_HOST_BOUNDARY=1; unset, this block imports nothing and replaces nothing ---
if os.environ.get("KC_HOST_BOUNDARY") == "1":
    import host_boundary  # noqa: E402  bin/pi/host_boundary.py, next to this file: ownership at ingestion, stamps, tap; no motor access
    from lerobot.robots.lekiwi.lekiwi_host import LeKiwiHost  # noqa: E402

    host_boundary.install(LeKiwi, LeKiwiHost)
# --- HOST BOUNDARY end ---

# --- HOST COMMAND LOOPBACK begin: OFF unless KC_HOST_COMMAND_LOOPBACK=1 (the Cake-supervised run-host.sh sets it) ---
if os.environ.get("KC_HOST_COMMAND_LOOPBACK") == "1":
    import time  # noqa: E402

    import zmq  # noqa: E402  the pyzmq the stock host itself imports
    from lerobot.robots.lekiwi.lekiwi_host import LeKiwiHost as _CommandLoopbackHost  # noqa: E402

    _stock_host_init = _CommandLoopbackHost.__init__

    def _host_init_command_on_loopback(self, config):
        """The stock constructor, then its command socket replaced by one bound to 127.0.0.1 ONLY.

        The stock constructor binds tcp://*:<port> and nothing configures the address, so the stock socket exists for the
        rest of this function and is closed unread: the host loop that reads commands starts only after the constructor
        returns. The replacement is the stock kind (PULL, CONFLATE 1) on the stock port. The endpoint actually bound is
        read back: anything but tcp://127.0.0.1:<port> ends the process here, before the loop, so no command is ever read
        from a socket other than the loopback one."""
        _stock_host_init(self, config)
        stock = self.zmq_cmd_socket
        if type(stock) is not zmq.Socket:  # the host boundary replaced it with its gate: the two are never combined
            raise SystemExit("refusing to start: the command socket was already replaced; the loopback binding is not combined with another replacement")
        wanted = f"tcp://127.0.0.1:{int(config.port_zmq_cmd)}"
        stock.unbind(stock.getsockopt_string(zmq.LAST_ENDPOINT))
        stock.close(0)
        replacement = self.zmq_context.socket(zmq.PULL)
        replacement.setsockopt(zmq.CONFLATE, 1)
        deadline = time.monotonic() + 2.0  # the stock socket's port is released by the zmq I/O thread, not synchronously
        while True:
            try:
                replacement.bind(wanted)
                break
            except zmq.ZMQError as error:
                if time.monotonic() >= deadline:
                    replacement.close(0)
                    raise SystemExit(f"refusing to start: the command socket could not be bound to {wanted} ({error})") from None
                time.sleep(0.05)
        bound = replacement.getsockopt_string(zmq.LAST_ENDPOINT)
        if bound != wanted:
            replacement.close(0)
            raise SystemExit(f"refusing to start: the command socket is bound to {bound}, not {wanted}")
        self.zmq_cmd_socket = replacement
        print(f"HOST COMMAND SOCKET: bound to {bound} only (loopback); observations on port {config.port_zmq_observations} are the stock binding",
              file=sys.stderr, flush=True)

    _CommandLoopbackHost.__init__ = _host_init_command_on_loopback
# --- HOST COMMAND LOOPBACK end ---

if __name__ == "__main__":
    if not any(arg.replace(" ", "") == "--robot.cameras={}" for arg in sys.argv):
        sys.exit("refusing to start: pass --robot.cameras={} (this host must own no camera)")
    if "--robot.disable_torque_on_disconnect=false" not in sys.argv:
        sys.exit("refusing to start: pass --robot.disable_torque_on_disconnect=false (stopping the host must not drop the arm)")
    main()
