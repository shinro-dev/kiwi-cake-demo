#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""wheels_safe_stop.py: the wheel stop of the post-exit safe-stop command (bin/templates/safe-stop.sh.in). It runs ON THE
ROBOT COMPUTER, started by the rendered safe-stop.sh after the Cake supervisor observed the motor host or the capability
exit. bin/pi/lekiwi-deploy.sh install deploys it and renders safe-stop.sh to run it.

  wheels_safe_stop.py --port DEVICE --lock PATH [--budget-ms N]
  wheels_safe_stop.py --status --port DEVICE --lock PATH
  wheels_safe_stop.py --self-check

WHY. The motor host's 500 ms base watchdog lives inside the host's own loop, and the wheels run in velocity mode. A host
that dies without its disconnect path (SIGKILL, SIGTERM, which the pinned host does not handle, a crash inside the bus
code) leaves every wheel at its last goal velocity. Nothing else on the robot computer writes the wheels after that.

WHAT IT WRITES, AND TO WHICH SERVOS. Goal_Velocity (address 46) = 0 on the three wheel servos only, ids 7, 8 and 9
(base_left_wheel, base_back_wheel, base_right_wheel: the ids lerobot's LeKiwi class gives them at the pinned commit
b4e2d0b). First one SYNC WRITE packet whose data names those three ids and no other, the way the host's own stop_base()
writes it; then one acknowledged WRITE to each wheel; each wheel not acknowledged is written once more while time
allows. No register of an arm servo (ids 1 to 6) is ever read or written: every read and write here goes through Bus,
which refuses any id but the three wheels. A wheel is verified only when its goal velocity reads back 0 AND its
operating mode (address 33) reads back 1, velocity mode. No torque register is written, with ONE exception: a wheel
that is not verified gets Torque_Enable 0 (address 40), while time allows, so that it stops being driven. It then turns
freely until an armed host start holds it again (the safe attach writes the zero goal and then the torque; it refuses to
start on a wheel that is not in velocity mode). Nothing else is ever written: no operating mode, no position, no
acceleration, no EEPROM register, nothing on disconnect.

WHAT A GOAL WRITE DOES TO TORQUE IS NOT KNOWN. Whether writing Goal_Velocity to a wheel servo whose torque is off
switches its torque on has not been measured on this robot's servos, so treat every stop as one that may leave a wheel's
torque on. Nothing here claims the torque a wheel had before: the zero goes first and the torque is read only after it,
and the log reports it as read then. Should the write switch torque on, a wheel in velocity mode holds at zero speed, as
the host's zero-speed hold does. A wheel that is NOT in velocity mode is not stopped by a zero goal velocity, and
switched on by the write it could be driven toward its stored goal position. So its operating mode is read back right
after its goal velocity, in the same pass, and a wheel whose mode does not read 1 is NOT VERIFIED and gets the fallback
above. So does a wheel whose mode cannot be read (no answer, a reply that does not check out) or is not read because
the time budget is spent. The zero still goes first and the mode is not read before it, so such a wheel can move
between the writes and its fallback, a few bus exchanges later. The host's safe attach refuses to start on such a wheel,
and this runs when that refused host exits, and again at each held restart. --status (below) settles the torque
question on a stand.

WHAT IT READS BACK AND LOGS, one line each on stdout, which safe-stop.sh appends to its log: per wheel its goal velocity
and operating mode right after the writes, then its present velocity and torque, then the three present velocities
again until all read 0 or the time budget ends, and one DONE or NOT VERIFIED line. The servo device is named by its
/dev node, never by its /dev/serial/by-id/ entry, which carries the adapter's serial number. The log is evidence, never
a precondition: a line that cannot be written (a full disk, a failing card) is dropped, and the writes go on as if it
had been.

THE LOCK. It writes the bus only while it holds an exclusive flock on the lock file (--lock), from before its owner
check until after the port is closed. Its other holders: another wheel stop or a --status run, and a host that
bin/templates/run-host.sh.in started. That wrapper takes the lock before its own owner check and hands it to the host
through its exec, so the host holds it until the process exits: no wheel stop writes while such a host starts (it opens
the bus seconds after its wrapper's check) or runs (a live host stops the base with its own watchdog). The host's and
the capability's safe-stops can run at the same moment, at a resident stop, so a second one WAITS for the first (up to
the budget less LOCK_RESERVE_S) and then writes too, which the first may have skipped because the host was still up. The
file is opened read-only, and created when missing: flock needs no write access, so a lock file this account may only
read is still taken (so, by the kernel's rules, is one that exists on a file system gone read-only; that case is not
tested here).

WHEN IT WRITES NOTHING, each with one NOT RUN line:
  - the servo device is missing, or is not a character device (exit 3);
  - the lock is held for the whole wait, or cannot be opened or taken (exit 3);
  - another process of this account holds the servo device open (exit 3): a program that takes no lock, such as a bench
    tool or a host started any other way. Two writers on one bus corrupt each other's packets. This check reads /proc
    once, before the port is opened, so it does not see a process that opens the device after it: only the lock keeps
    that off the bus, and only for a host that run-host.sh started. Like the host wrapper's fuser check, it reads /proc
    as this account, so a process of ANOTHER account is not seen;
  - the owner check did not see this process holding its own file open (exit 3): it cannot be trusted, so it fails
    closed;
  - the feetech SDK (scservo_sdk) cannot be imported, or the device cannot be opened (exit 4).

TIME. The supervisor ends a safe-stop that is still running about half a second after its start with SIGKILL (measured
on the development build of the supervisor, not on the robot: the attended test only shows whether a safe-stop there
ends before it), and safe-stop.sh runs this under a harder limit of its own (timeout -s KILL, below 0.5 s). So the zero
is written FIRST, before any read, and everything after it stops at the budget (DEFAULT_BUDGET_S of this process's age,
from its start in /proc/self/stat; a request/reply exchange is begun only when it can end inside the budget). lerobot's
own bus class is NOT used: importing it imports torch (lerobot.utils imports device_utils), which takes seconds; the SDK
it wraps imports in milliseconds. The bus settings are the ones lerobot uses for this robot: 1,000,000 baud, protocol 0
(little-endian words), velocities in sign-magnitude with bit 15 as the sign, and lerobot's packet timeout
(patch_setPacketTimeout: 50 ms plus byte times).

--self-check runs, timed by this process's age, what a stop runs before it opens the bus, but for the device checks and
the lock wait: this file, the owner check's /proc scan with its positive control, and the feetech SDK import. It opens
no device and writes nothing. SELF-CHECK ok (exit 0): this process was ready to open the bus by SELF_CHECK_READY_LIMIT_MS
(300 ms) of its age, the last moment the default budget lets a readback exchange begin. SELF-CHECK TOO SLOW (exit 1): it
was ready later, so a stop from this interpreter could not read back what it writes, and could be ended by the
safe-stop's hard limit before it writes at all. SELF-CHECK FAILED: the scan cannot see this process, so every stop would
refuse to write (exit 3), or the SDK cannot be imported (exit 4). The port open and the bus exchanges are not timed, and
the robot computer may be busier at a stop than at the check, so the time it measures is a lower bound on a stop's delay
to its first write, and an ok is not a promise. lekiwi-deploy.sh install runs it and refuses on any exit but 0.

--status is READ ONLY and is run by hand, never by safe-stop.sh: the same device checks, lock and owner check as a stop
(so it refuses while a host holds the lock or the bus), then per wheel its torque, operating mode, goal velocity and
present velocity, and one summary line. It writes nothing. Run with the unit stopped before and after a stop on wheels
whose torque is off (after a power cycle), it shows whether the stop switched their torque on.

Exit status: 0 every wheel read back goal velocity 0 in velocity mode (--status: every value was read; --self-check: ok);
1 at least one did not (see its lines; --status: not every value was read; --self-check: too slow); 2 usage; 3 not run,
nothing written; 4 the bus could not be reached, nothing written. safe-stop.sh logs the status and always exits 0.
"""
import fcntl
import os
import stat
import sys
import time


def process_age_s():
    """Seconds since this process started (its start in /proc/self/stat against CLOCK_BOOTTIME), or None."""
    try:
        with open("/proc/self/stat", "rb") as handle:
            data = handle.read()
        start_ticks = int(data[data.rindex(b")") + 2:].split()[19])  # field 22, starttime, in clock ticks since boot
        return max(0.0, time.clock_gettime(time.CLOCK_BOOTTIME) - start_ticks / os.sysconf("SC_CLK_TCK"))
    except (OSError, ValueError, IndexError, AttributeError):
        return None


AGE_AT_IMPORT_S = process_age_s()
IMPORTED_AT = time.monotonic()

WHEELS = ((7, "base_left_wheel"), (8, "base_back_wheel"), (9, "base_right_wheel"))
WHEEL_IDS = tuple(sid for sid, _ in WHEELS)
NAMES = dict(WHEELS)
ADDR_OPERATING_MODE, ADDR_TORQUE_ENABLE, ADDR_GOAL_VELOCITY, ADDR_PRESENT_VELOCITY = 33, 40, 46, 58
VELOCITY_SIGN_BIT = 15
BAUDRATE = 1_000_000
PROTOCOL_END = 0  # the SDK's protocol 0: STS/SMS servos, little-endian words
PACKET_TIMEOUT_MS = 50.0  # lerobot's patch_setPacketTimeout margin
EXCHANGE_S = 0.06  # one request/reply exchange at worst: the packet timeout and some slack
DEFAULT_BUDGET_S = 0.36
# --self-check fails when its process is ready to open the bus later than this, in ms of its age: the last moment at which
# the default budget still lets a readback exchange begin.
SELF_CHECK_READY_LIMIT_MS = round((DEFAULT_BUDGET_S - EXCHANGE_S) * 1000)
LOCK_RESERVE_S = 0.12  # stop waiting for the lock when less than this is left of the budget
SETTLE_INTERVAL_S = 0.02
OWNERS_NAMED = 5  # a NOT RUN line names at most this many owners of the device, then counts the rest
EXIT_VERIFIED, EXIT_NOT_VERIFIED, EXIT_USAGE, EXIT_NOT_RUN, EXIT_NO_BUS = 0, 1, 2, 3, 4
RESULTS = {0: "ok", -1: "port busy", -2: "transmit failed", -3: "receive failed", -4: "bad instruction packet",
           -5: "still receiving", -6: "no status packet", -7: "corrupt status packet", -9: "not available"}
STATUS_FLAGS = ((0x01, "voltage"), (0x02, "angle sensor"), (0x04, "overheat"), (0x08, "overcurrent"), (0x20, "overload"))
USAGE = "usage: wheels_safe_stop.py [--status] --port DEVICE --lock PATH [--budget-ms N] | --self-check"


class ProcessClock:
    """This process's age in seconds: its age when this module was imported, plus the monotonic time since."""

    def __init__(self, age_at_import=AGE_AT_IMPORT_S, imported_at=IMPORTED_AT):
        self.age0 = age_at_import or 0.0
        self.t0 = imported_at

    def __call__(self):
        return self.age0 + (time.monotonic() - self.t0)


def result_text(result):
    return RESULTS.get(result, f"result {result}")


def flags_text(error):
    if not error:
        return ""
    names = [name for bit, name in STATUS_FLAGS if error & bit]
    return f"; status flags 0x{error:02x} ({', '.join(names) or 'unknown'})"


def decode_velocity(raw):
    magnitude = raw & ((1 << VELOCITY_SIGN_BIT) - 1)
    return -magnitude if (raw >> VELOCITY_SIGN_BIT) & 1 else magnitude


def show(value):
    return "?" if value is None else str(value)


class Bus:
    """The three wheels of the servo bus, through the feetech SDK. Every method takes a wheel id and refuses any other."""

    def __init__(self, scs, device):
        self.scs = scs
        self.port = scs.PortHandler(device)
        port = self.port

        def set_packet_timeout(packet_length):  # lerobot's patch_setPacketTimeout, the timeout the host itself runs with
            port.packet_start_time = port.getCurrentTime()
            port.packet_timeout = (port.tx_time_per_byte * packet_length) + (port.tx_time_per_byte * 3.0) + PACKET_TIMEOUT_MS

        port.setPacketTimeout = set_packet_timeout
        self.packet = scs.PacketHandler(PROTOCOL_END)
        if not port.setBaudRate(BAUDRATE):  # the SDK opens the port on its first baud rate setting
            raise OSError(f"the SDK refused {BAUDRATE} baud")
        self.ok = scs.COMM_SUCCESS

    @staticmethod
    def wheel(sid):
        if sid not in WHEEL_IDS:
            raise ValueError(f"id {sid} is not a wheel; only ids 7, 8 and 9 are ever addressed")
        return sid

    def _exchange(self, call, *args):
        """(value, result, error, why) of one SDK call: value is None for a write; an exception is a failed transmit whose
        text is WHY, never an escape."""
        try:
            reply = call(self.port, *args)
        except Exception as error:  # noqa: BLE001 -- a vanished device raises from pyserial; reported as a failed exchange
            return None, -2, 0, f"{type(error).__name__}: {error}"
        if len(reply) == 2:
            return None, reply[0], reply[1], ""
        return reply[0], reply[1], reply[2], ""

    def sync_write_goal_zero(self):
        writer = self.scs.GroupSyncWrite(self.port, self.packet, ADDR_GOAL_VELOCITY, 2)
        for sid in WHEEL_IDS:
            writer.addParam(self.wheel(sid), [0, 0])
        try:
            return writer.txPacket(), ""
        except Exception as error:  # noqa: BLE001
            return -2, f"{type(error).__name__}: {error}"

    def write_goal_zero(self, sid):
        _, result, error, why = self._exchange(self.packet.write2ByteTxRx, self.wheel(sid), ADDR_GOAL_VELOCITY, 0)
        return result, error, why

    def write_torque_off(self, sid):
        _, result, error, why = self._exchange(self.packet.write1ByteTxRx, self.wheel(sid), ADDR_TORQUE_ENABLE, 0)
        return result, error, why

    def read_velocity(self, sid, address):
        """(velocity or None, result)."""
        raw, result, _, _ = self._exchange(self.packet.read2ByteTxRx, self.wheel(sid), address)
        return (decode_velocity(raw) if result == self.ok and raw is not None else None), result

    def read_byte(self, sid, address):
        """(value or None, result)."""
        value, result, _, _ = self._exchange(self.packet.read1ByteTxRx, self.wheel(sid), address)
        return (value if result == self.ok else None), result

    def close(self):
        try:
            self.port.closePort()
        except Exception:  # noqa: BLE001 -- the process ends right after; the kernel closes the descriptor
            pass


def acquire_lock(path, clock, until_s):
    """(descriptor or None, 'acquired' | 'held' | why-unavailable). Waits, polling, until the clock reads UNTIL_S. The file
    is opened read-only (flock needs no write access) and created when missing."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_CREAT | os.O_CLOEXEC, 0o600)
    except OSError as error:
        return None, f"the lock file could not be opened: {error.strerror}"
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd, "acquired"
        except BlockingIOError:
            if clock() >= until_s:
                os.close(fd)
                return None, "held"
            time.sleep(0.005)
        except OSError as error:
            os.close(fd)
            return None, f"flock failed: {error.strerror}"


def device_owners(device, control, proc_root="/proc"):
    """Every other process of this account holding DEVICE open, as (pid, command name), and whether the scan saw this
    process holding CONTROL open (the positive control: a scan that cannot see that cannot be trusted)."""
    me, uid = os.getpid(), os.getuid()
    owners, saw_control = [], False
    try:
        names = os.listdir(proc_root)
    except OSError:
        return owners, False
    for name in names:
        if not name.isdigit():
            continue
        base = os.path.join(proc_root, name)
        try:
            if os.stat(base).st_uid != uid:
                continue
            fds = os.listdir(os.path.join(base, "fd"))
        except OSError:
            continue
        for fd in fds:
            try:
                target = os.readlink(os.path.join(base, "fd", fd))
            except OSError:
                continue
            if int(name) == me:
                saw_control = saw_control or target == control
            elif target == device:
                try:
                    with open(os.path.join(base, "comm"), encoding="utf-8", errors="replace") as handle:
                        comm = handle.read().strip()
                except OSError:
                    comm = "?"
                owners.append((int(name), comm))
                break
    return owners, saw_control


def owner_scan(device, proc_root="/proc"):
    """The owner check as a stop runs it: this file held open as the positive control while /proc is scanned for DEVICE.
    (owners, saw_control) as device_owners gives them; a control that cannot be opened is a scan that saw nothing."""
    control_path = os.path.realpath(__file__)
    try:
        control = os.open(control_path, os.O_RDONLY | os.O_CLOEXEC)
    except OSError:
        return [], False
    try:
        return device_owners(device, control_path, proc_root)
    finally:
        os.close(control)


def zero_wheels(bus, budget_s, clock, out):
    """The writes, then the readback. The first acknowledged write to every wheel is made whatever the budget says; every
    exchange after those is begun only when it can end inside the budget. A wheel is verified only when its goal
    velocity reads back 0 AND its operating mode reads back 1 (velocity), both read in one pass right after the writes;
    every other wheel, whatever the reason (a goal that is not 0, a mode that is not 1, a value that could not be read or
    was not read for want of time), gets the Torque_Enable 0 fallback while time allows. A wheel whose goal read got no
    answer is not asked its mode, and a wheel that has answered no request at all is asked nothing after its fallback."""

    def fits():
        return clock() + EXCHANGE_S <= budget_s

    def said(result, why):
        return f"{result_text(result)}{': ' + why if why else ''}"

    result, why = bus.sync_write_goal_zero()
    out(f"WROTE Goal_Velocity 0 to wheels 7 8 9 in one sync write ({said(result, why)}; a sync write gets no reply: the "
        "acknowledged writes and the readback below are what confirm it)")
    acked = dict.fromkeys(WHEEL_IDS, False)
    answered = dict.fromkeys(WHEEL_IDS, False)
    for attempt in (1, 2):
        for sid in WHEEL_IDS:
            if acked[sid] or (attempt == 2 and not fits()):
                continue
            result, error, why = bus.write_goal_zero(sid)
            acked[sid] = result == bus.ok
            answered[sid] = answered[sid] or acked[sid]
            again = " again" if attempt == 2 else ""
            if acked[sid]:
                out(f"wheel {sid} {NAMES[sid]}: WROTE Goal_Velocity 0{again}, acknowledged{flags_text(error)}")
            else:
                out(f"wheel {sid} {NAMES[sid]}: WROTE Goal_Velocity 0{again}, NOT acknowledged ({said(result, why)})")
        if all(acked.values()) or not fits():
            break
    goal, goal_result = {}, {}
    mode, mode_result = dict.fromkeys(WHEEL_IDS), dict.fromkeys(WHEEL_IDS)
    for sid in WHEEL_IDS:
        goal[sid], goal_result[sid] = bus.read_velocity(sid, ADDR_GOAL_VELOCITY) if fits() else (None, None)
        answered[sid] = answered[sid] or goal_result[sid] == bus.ok
        # A zero goal velocity stops a wheel only in velocity mode: in another mode it does not, and the write itself may
        # have switched that wheel's torque on. So the mode is part of the verification, read right after the goal.
        if goal_result[sid] == bus.ok:
            mode[sid], mode_result[sid] = bus.read_byte(sid, ADDR_OPERATING_MODE) if fits() else (None, None)
    failed = [sid for sid in WHEEL_IDS if goal[sid] != 0 or mode[sid] != 1]
    torque = dict.fromkeys(WHEEL_IDS)
    for sid in failed:
        if goal_result[sid] is None:
            how = "its goal velocity was not read back: the time budget was spent"
        elif goal[sid] is None:
            how = f"its goal velocity could not be read back ({result_text(goal_result[sid])})"
        elif goal[sid] != 0:
            how = f"its goal velocity read back as {goal[sid]}"
        elif mode_result[sid] is None:
            how = "its goal velocity read back 0, but its operating mode was not read back: the time budget was spent"
        elif mode[sid] is None:
            how = f"its goal velocity read back 0, but its operating mode could not be read back ({result_text(mode_result[sid])})"
        else:
            how = (f"its goal velocity read back 0, but its operating mode reads {mode[sid]}, not 1 (velocity mode): a zero goal "
                   "velocity does not stop a wheel in another mode")
        out(f"wheel {sid} {NAMES[sid]}: NOT VERIFIED: {how}")
        if not fits():
            out(f"wheel {sid} {NAMES[sid]}: FALLBACK NOT WRITTEN: the time budget was spent")
            continue
        result, error, why = bus.write_torque_off(sid)
        if result != bus.ok:
            out(f"wheel {sid} {NAMES[sid]}: FALLBACK: WROTE Torque_Enable 0, NOT acknowledged ({said(result, why)}); "
                "whether it took is unknown")
            continue
        answered[sid] = True
        torque[sid] = bus.read_byte(sid, ADDR_TORQUE_ENABLE)[0] if fits() else None
        if torque[sid] == 0:
            after = "its torque reads 0: it is no longer driven and turns freely"
        elif torque[sid] is None:
            after = "its torque was not read back"
        else:
            after = f"its torque still reads {torque[sid]}: the fallback did NOT take"
        out(f"wheel {sid} {NAMES[sid]}: FALLBACK: WROTE Torque_Enable 0 so that it stops being driven, acknowledged; {after}")
    live = [sid for sid in WHEEL_IDS if answered[sid]]
    for sid in WHEEL_IDS:
        if not answered[sid]:
            out(f"wheel {sid} {NAMES[sid]}: it answered no request; nothing more is asked of it")
    present = dict.fromkeys(WHEEL_IDS)

    def sample():
        for sid in live:
            present[sid] = bus.read_velocity(sid, ADDR_PRESENT_VELOCITY)[0] if fits() else None
        out("present velocity after zeroing (wheels 7 8 9): " + " ".join(show(present[sid]) for sid in WHEEL_IDS))

    if live and fits():
        sample()
        for sid in live:
            if torque[sid] is None and fits():
                torque[sid] = bus.read_byte(sid, ADDR_TORQUE_ENABLE)[0]
            note = "" if mode[sid] in (1, None) else " (NOT velocity mode: this wheel does not run on its goal velocity)"
            out(f"wheel {sid} {NAMES[sid]}: READ BACK goal velocity {show(goal[sid])}, present velocity {show(present[sid])}, "
                f"torque {show(torque[sid])}, mode {show(mode[sid])}{note}")
        while any(present[sid] not in (0, None) for sid in live) and fits():
            time.sleep(SETTLE_INTERVAL_S)
            if fits():
                sample()
    elif live:
        out("budget spent: the readback was skipped")
    moving = " ".join(show(present[sid]) for sid in WHEEL_IDS)
    if not failed:
        # The torque is read only AFTER the writes, so it is reported as read then, never as the torque from before: a goal
        # write may itself switch a torque-off servo on (see WHAT A GOAL WRITE DOES TO TORQUE above).
        out(f"DONE: wheels 7 8 9 read back goal velocity 0 in velocity mode; present velocity {moving}; no torque register was "
            f"written; torque read after the writes: {' '.join(show(torque[sid]) for sid in WHEEL_IDS)}; no other servo was read "
            "or written")
        return EXIT_VERIFIED
    out(f"NOT VERIFIED: wheel {' '.join(str(sid) for sid in failed)} did not read back goal velocity 0 in velocity mode (the "
        f"lines above say what was read, and the FALLBACK lines what was done about it); present velocity {moving}. If a "
        "wheel still turns, cut power.")
    return EXIT_NOT_VERIFIED


def wheel_status(bus, out):
    """--status, READ ONLY: per wheel its torque, operating mode, goal velocity and present velocity, then one summary line.
    Nothing is written. A wheel that does not answer its first read is asked nothing more."""
    values = {}
    for sid in WHEEL_IDS:
        torque, result = bus.read_byte(sid, ADDR_TORQUE_ENABLE)
        if result != bus.ok:
            values[sid] = (None, None, None, None)
            out(f"wheel {sid} {NAMES[sid]}: STATUS NOT READ ({result_text(result)}); nothing more is asked of it")
            continue
        values[sid] = (torque, bus.read_byte(sid, ADDR_OPERATING_MODE)[0], bus.read_velocity(sid, ADDR_GOAL_VELOCITY)[0],
                       bus.read_velocity(sid, ADDR_PRESENT_VELOCITY)[0])
        out(f"wheel {sid} {NAMES[sid]}: STATUS torque {show(values[sid][0])}, mode {show(values[sid][1])}, goal velocity "
            f"{show(values[sid][2])}, present velocity {show(values[sid][3])}")
    row = [" ".join(show(values[sid][i]) for sid in WHEEL_IDS) for i in range(4)]
    out(f"STATUS (read only, nothing was written; wheels 7 8 9): torque {row[0]}; mode {row[1]}; goal velocity {row[2]}; "
        f"present velocity {row[3]}")
    return EXIT_VERIFIED if all(v is not None for sid in WHEEL_IDS for v in values[sid]) else EXIT_NOT_VERIFIED


def parse(argv):
    if argv == ["--self-check"]:
        return {"mode": "self-check"}
    options, i, mode = {}, 0, "stop"
    while i < len(argv):
        key = argv[i]
        if key == "--status" and mode == "stop":
            mode, i = "status", i + 1
        elif key in ("--port", "--lock", "--budget-ms") and i + 1 < len(argv) and key not in options:
            options[key] = argv[i + 1]
            i += 2
        else:
            raise ValueError(f"unexpected argument {key!r}")
    if "--port" not in options or "--lock" not in options:
        raise ValueError("--port and --lock are required")
    budget_ms = options.get("--budget-ms", str(int(DEFAULT_BUDGET_S * 1000)))
    if not budget_ms.isdigit() or not 50 <= int(budget_ms) <= 5000:
        raise ValueError("--budget-ms must be a whole number of milliseconds between 50 and 5000")
    return {"mode": mode, "port": options["--port"], "lock": options["--lock"], "budget_s": int(budget_ms) / 1000.0}


def import_sdk():
    import importlib

    return importlib.import_module("scservo_sdk")


def self_check(sdk_loader, clock, out, proc_root="/proc"):
    """--self-check: what a stop runs before it opens the bus, but for the device checks and the lock wait, timed by this
    process's age (the clock a stop's budget runs on): this file, the owner check's /proc scan with its positive control,
    and the feetech SDK import. It opens no device and writes nothing."""
    start = clock()
    _, saw_control = owner_scan(None, proc_root)
    scanned = clock()
    if not saw_control:
        out("SELF-CHECK FAILED: the owner check did not see this process holding its own file open, so every real stop would "
            "refuse to write (NOT RUN); no device was opened")
        return EXIT_NOT_RUN
    try:
        scs = sdk_loader()
    except Exception as error:  # noqa: BLE001 -- any import failure means a real run would write nothing
        out(f"SELF-CHECK FAILED: the feetech SDK (scservo_sdk) could not be imported ({type(error).__name__}: {error}); "
            "a real run would write nothing")
        return EXIT_NO_BUS
    missing = [n for n in ("PortHandler", "PacketHandler", "GroupSyncWrite", "COMM_SUCCESS") if not hasattr(scs, n)]
    if missing:
        out(f"SELF-CHECK FAILED: the feetech SDK lacks {', '.join(missing)}")
        return EXIT_NO_BUS
    ready = clock()
    ready_ms, budget_ms = round(ready * 1000), round(DEFAULT_BUDGET_S * 1000)
    timed = (f"python {sys.version.split()[0]}; the owner check's /proc scan took {round((scanned - start) * 1000)} ms and the "
             f"feetech SDK import {round((ready - scanned) * 1000)} ms; this process was ready to open the bus at +{ready_ms} ms "
             "of its age")
    if ready_ms > SELF_CHECK_READY_LIMIT_MS:
        out(f"SELF-CHECK TOO SLOW: {timed}, past the limit of {SELF_CHECK_READY_LIMIT_MS} ms: a real stop from this interpreter "
            f"would have no time left in its {budget_ms} ms budget to read back what it writes, and could be ended by the "
            "safe-stop's hard limit before it writes at all; no device was opened")
        return EXIT_NOT_VERIFIED
    out(f"SELF-CHECK ok: {timed}, inside the limit of {SELF_CHECK_READY_LIMIT_MS} ms (the last moment its {budget_ms} ms "
        "budget lets a readback begin; not timed here: the lock wait and the port open); no device was opened")
    return EXIT_VERIFIED


def run(port, lock_path, budget_s, sdk_loader, clock, out, proc_root="/proc", status=False):
    """The stop, or with STATUS the read-only status: the same device checks, lock and owner check before either."""
    if not os.path.exists(port):
        out("NOT RUN: the servo device is missing; nothing was written")
        return EXIT_NOT_RUN
    device = os.path.realpath(port)
    try:
        is_char = stat.S_ISCHR(os.stat(device).st_mode)
    except OSError:
        out("NOT RUN: the servo device is missing; nothing was written")
        return EXIT_NOT_RUN
    if not is_char:
        out(f"NOT RUN: the servo device path resolves to {device}, which is not a character device; nothing was written")
        return EXIT_NOT_RUN
    now = time.time()
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now)) + f".{int(now * 1000) % 1000:03d}Z"
    wheels = f"wheels 7 8 9 ({' '.join(NAMES[s] for s in WHEEL_IDS)})"
    if status:
        out(f"status at {stamp}: pid {os.getpid()}, servo device {device}, {wheels}; READ ONLY: nothing is written; the arm "
            "(ids 1 to 6) is never read or written")
    else:
        out(f"start at {stamp}: pid {os.getpid()}, servo device {device}, {wheels}; the arm (ids 1 to 6) is never read or "
            f"written; budget {int(budget_s * 1000)} ms")
    lock_fd, lock_state = acquire_lock(lock_path, clock, budget_s - LOCK_RESERVE_S)
    if lock_state == "held":
        out("NOT RUN: the wheel-stop lock was held for the whole wait: by another wheel stop or a --status run, or by a host "
            "that run-host.sh started, which holds it from before its owner check until it exits; nothing was written")
        return EXIT_NOT_RUN
    if lock_fd is None:
        out(f"NOT RUN: the wheel-stop lock could not be taken ({lock_state}); a stop writes only while it holds that lock, "
            "which keeps it off a bus a starting host is about to open; nothing was written")
        return EXIT_NOT_RUN
    try:
        owners, saw_control = owner_scan(device, proc_root)
        if not saw_control:
            out("NOT RUN: the owner check did not see this process holding its own file open, so it cannot be trusted; it "
                "fails closed and nothing was written")
            return EXIT_NOT_RUN
        if owners:
            who = ", ".join(f"pid {pid} {comm}" for pid, comm in owners[:OWNERS_NAMED])
            if len(owners) > OWNERS_NAMED:
                who += f", and {len(owners) - OWNERS_NAMED} more"
            out(f"NOT RUN: the servo device has an owner ({who}); that process holds the bus (a live host stops the base with "
                "its own watchdog) and two writers on one bus corrupt each other's packets; nothing was written")
            return EXIT_NOT_RUN
        try:
            scs = sdk_loader()
        except Exception as error:  # noqa: BLE001
            out(f"NOT RUN: the feetech SDK (scservo_sdk) could not be imported ({type(error).__name__}: {error}); nothing was written")
            return EXIT_NO_BUS
        try:
            bus = Bus(scs, device)
        except Exception as error:  # noqa: BLE001
            out(f"NOT RUN: the servo device could not be opened ({type(error).__name__}: {error}); nothing was written")
            return EXIT_NO_BUS
        try:
            return wheel_status(bus, out) if status else zero_wheels(bus, budget_s, clock, out)
        finally:
            bus.close()
    finally:
        os.close(lock_fd)  # only once the port is closed: the lock covers every byte this process puts on the bus


def main(argv=None, sdk_loader=None, clock=None, stream=None, proc_root="/proc"):
    argv = sys.argv[1:] if argv is None else list(argv)
    clock = clock or ProcessClock()
    stream = stream or sys.stdout

    def out(message):
        # The log is evidence, never a precondition: a line that cannot be written (a full disk, a failing card, a closed
        # stream) is dropped and the stop goes on. Nothing the log does may reach the writes.
        try:
            print(f"wheels-safe-stop: +{int(clock() * 1000)} ms {message}", file=stream, flush=True)
        except Exception:  # noqa: BLE001 -- whatever the stream raises, the line is lost and nothing else is
            pass

    if argv in (["-h"], ["--help"]):
        print(__doc__, file=stream)
        return EXIT_VERIFIED
    try:
        options = parse(argv)
    except ValueError as error:
        out(f"NOT RUN: {error}; {USAGE}")
        return EXIT_USAGE
    loader = sdk_loader or import_sdk
    if options["mode"] == "self-check":
        return self_check(loader, clock, out, proc_root)
    return run(options["port"], options["lock"], options["budget_s"], loader, clock, out, proc_root,
               status=options["mode"] == "status")


if __name__ == "__main__":
    sys.exit(main())
