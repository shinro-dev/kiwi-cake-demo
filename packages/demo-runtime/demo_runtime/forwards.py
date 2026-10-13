# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""demo_runtime.forwards: the loopback forwards the runtime owns, one `ssh -N -L` child per purpose (and one `ssh -N -R`,
ReverseForward near the end of this file, for the desktop perception service the robot asks).

WHY THIS EXISTS. bin/pi/host_boundary.py binds its lease control socket and its observation tap to the robot computer's
OWN loopback address, and bin/pi/camera_service.py does the same with the preview publisher. That is deliberate: none of
the three carries a credential, so none of them may be reachable from a network. The laptop therefore reaches all three
through ssh forwards. Until now nothing created them: an operator opened them by hand, demo_runtime.sources subscribed to
whatever was on those local ports, and a console started from a browser had no way to say whether they were there. This
module makes them the RUNTIME's: one child process per purpose, each with its own state and its own reason code.

WHAT STARTING A FORWARD DOES TO THE ROBOT. It opens an ssh session to the robot computer and asks for a port forward.
That is the whole of it. It touches NO MOTOR, NO CAMERA and NO TORQUE: `-N` means no remote command is run, and the three
sockets at the other end are a REP socket that answers lease requests and two PUB sockets that publish. Nothing in this
module can send an action to a robot and nothing in it constructs a robot client. Opening a forward is not an acquisition:
the lease is taken separately, by the supervisor, and only for a command that could command motion.

OPT-IN, AND OFF UNTIL THE TAP IS ENABLED. from_config() builds forwards only when the runtime configuration enables the
tap. That switch is the operator's statement that this robot computer runs the boundary and the camera service; without
it every purpose is 'not_configured', not one child is started, and the runtime behaves exactly as it did before.

THE SSH TARGET NEVER LEAVES THIS MODULE. It comes from the configuration (pi_ssh_target) and goes into one argv. report()
carries states, reason codes and counters and NO target, NO port and NO endpoint; repr() shows none of them either; and
the child's stdout and stderr go to the null device, because ssh writes the target into its own messages. The LOCAL
endpoint is handed out by endpoint(), which exists for this process's own subscriber and lease client and for nothing
else: it is never put in an event, a report or an answer to a client.

A DEAD FORWARD IS REPORTED, NEVER RETRIED IN A LOOP. check() polls each child; one that has exited becomes 'dead' with
the status it exited on, and it stays dead until somebody starts it again on purpose. There is no timer, no backoff and
no automatic restart in Forward or Forwards: an ssh that keeps dying is a fact an operator has to see, not a loop to hide
it in. THE ONE EXCEPTION is UnixSocketForward at the end of this file, the robot's admin-socket forward: it IS the
connection to the robot's resident, so it is restarted with a bounded backoff, and it reports every state it passes
through (connecting, connected, failed with the reason, since when) together with its death and restart counts.
`-o ExitOnForwardFailure=yes` makes ssh exit rather than hold a session with no tunnel, so a forward that is not really
there reads as dead instead of as running.

PORTS. A purpose whose local port is fixed by the configuration uses that port: demo_runtime.sources computes the
endpoints its two subscribers connect to, and a forward that landed anywhere else would be invisible to them. The control
forward has no configured port, so one is CHOSEN on the loopback interface: a socket is bound to 127.0.0.1:0, its port is
read, and the socket is closed again so that ssh can bind it a moment later. The residual is stated rather than hidden:
another process can take that port inside the window, ssh then exits under ExitOnForwardFailure, and the forward reads as
dead. A stopped forward releases its port and has no endpoint at all.
"""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import threading
import time
from typing import Callable, Optional

from . import sources

CONTROL, TAP, PREVIEW = "control", "tap", "preview"
PREVIEW_ROLES = ("wrist", "front")


def preview_purpose(role: str) -> str:
    """One forward per camera role. The role is part of the PURPOSE, so two cameras can never share a tunnel."""
    return f"{PREVIEW}:{role}"


PURPOSES = (CONTROL, TAP) + tuple(preview_purpose(role) for role in PREVIEW_ROLES)
LOOPBACK = "127.0.0.1"

# The camera service's preview socket per role, ON THE ROBOT COMPUTER: the REMOTE side of each preview forward. Written
# out per role rather than as a base plus an offset, because that offset is exactly what let one port serve two cameras.
# Pinned against bin/pi/camera_service.py by packages/demo-runtime/tests/test_forwards.py.
CAMERA_SERVICE_PREVIEW_PORTS = {"wrist": 5571, "front": 5572}

# Mirrors of bin/pi/host_boundary.py (DEFAULT_CONTROL_PORT, DEFAULT_TAP_PORT): the ports the boundary binds ON THE ROBOT
# COMPUTER, which are the REMOTE side of the control and tap forwards. Copied rather than imported, because that file
# belongs to the robot computer and imports zmq; pinned against its text by packages/demo-runtime/tests/test_forwards.py.
HOST_CONTROL_PORT, HOST_TAP_PORT = 5557, 5558

# The robot host's RAW command port (lerobot LeKiwiHostConfig.port_zmq_cmd, demo_runtime.config.DEFAULT_CMD_PORT). NO
# forward may carry it. On the LeKiwi deployment the host binds it on the robot's loopback only, which limits who can
# reach it to processes on the robot; the host itself checks nothing, and the session, ownership and admission checks
# apply only to commands routed through the robot-side service. An ssh forward from this computer to the robot's
# 127.0.0.1:5555 would hand every local process the socket the loopback binding took away, which is the bypass itself. A forward naming it is refused as a state (never raised), like any other forward that cannot run.
# Forwards.from_config adds the configured host.cmd_port when it differs.
RAW_HOST_COMMAND_PORT = 5555
REFUSED_RAW_COMMAND_PORT = "raw_host_command_port_refused"

# The six states a forward can be in, each with a reason code beside it. Only 'running' means a tunnel that has stayed up.
NOT_CONFIGURED, STOPPED, STARTING, RUNNING, DEAD, FAILED = "not_configured", "stopped", "starting", "running", "dead", "failed"
STATES = (NOT_CONFIGURED, STOPPED, STARTING, RUNNING, DEAD, FAILED)

SETTLE_S = 0.5  # how long a freshly started ssh must stay alive before it is called running rather than starting
STOP_GRACE_S = 3.0
# BatchMode: never ask this daemon's terminal for a passphrase, because a daemon has no terminal to ask on.
# ExitOnForwardFailure: an ssh that could not open the forward EXITS, so a missing tunnel is a death and not a lie.
# The keepalive turns a silently dropped connection into an exit as well, which is the only way this module hears of one.
SSH_OPTIONS = ("-N", "-o", "BatchMode=yes", "-o", "ExitOnForwardFailure=yes", "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3")


def endpoint_port(endpoint) -> Optional[int]:
    """The port of a `tcp://127.0.0.1:<port>` endpoint (demo_runtime.sources builds them), else None."""
    if not isinstance(endpoint, str):
        return None
    head, _, port = endpoint.rpartition(":")
    if head not in (f"tcp://{LOOPBACK}", "tcp://localhost") or not port.isdigit():
        return None
    number = int(port)
    return number if 0 < number < 65536 else None


def free_loopback_port() -> Optional[int]:
    """A port the kernel says is free on the loopback interface, or None. The socket is closed again at once: ssh has to
    bind the port itself a moment later, and two processes cannot hold it. See the note on PORTS above for the window."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as holder:
            holder.bind((LOOPBACK, 0))
            return int(holder.getsockname()[1])
    except OSError:
        return None


def exit_reason(status: Optional[int]) -> str:
    """A reason code for the status an ssh exited on. 255 is ssh's own 'the session failed'; a negative value is a signal."""
    if status is None:
        return "ssh_exit_status_unavailable"
    return f"ssh_killed_by_signal_{-status}" if status < 0 else f"ssh_exited_{status}"


class Forward:
    """ONE `ssh -N -L` child for ONE purpose. Every method is safe to call twice and none of them raises into a caller:
    a forward that cannot be opened is a state and a reason code, never an exception in the daemon's start path."""

    def __init__(self, purpose: str, *, ssh_target: str = "", remote_port: int = 0, local_port: Optional[int] = None,
                 local_port_fixed: bool = False, ssh_binary: str = "ssh", clock_ns: Callable[[], int] = time.monotonic_ns,
                 settle_s: float = SETTLE_S, stop_grace_s: float = STOP_GRACE_S, refused_remote_ports=()):
        self.purpose = purpose
        self._target = ssh_target if isinstance(ssh_target, str) else ""
        self._remote_port = int(remote_port or 0)
        self._refused = self._remote_port in {RAW_HOST_COMMAND_PORT, *(int(p) for p in refused_remote_ports if p)}
        self._wanted_local_port = int(local_port) if local_port else None
        self._local_port_fixed = bool(local_port_fixed)
        self._ssh_binary, self._clock_ns = ssh_binary, clock_ns
        self._settle_ns, self._stop_grace_s = int(settle_s * 1e9), float(stop_grace_s)
        self._lock = threading.RLock()  # reentrant: start() and stop() set the state while holding it
        self._proc = None
        self._started_ns: Optional[int] = None
        self.local_port: Optional[int] = None
        self.starts = self.deaths = self.forced_stops = 0
        self.last_exit_status: Optional[int] = None
        configured = self.configured
        self.state = STOPPED if configured else NOT_CONFIGURED
        self.reason = "not_started" if configured else self._missing()

    # -- what this forward is ------------------------------------------------------------------------------------------
    @property
    def configured(self) -> bool:
        """Is there something to open? A target, a remote port, and a local port when the configuration fixes one."""
        if self._refused or not self._target or not 0 < self._remote_port < 65536:
            return False
        return not (self._local_port_fixed and not self._wanted_local_port)

    def _missing(self) -> str:
        if self._refused:
            return REFUSED_RAW_COMMAND_PORT
        if not self._target:
            return "ssh_target_not_configured"
        if not 0 < self._remote_port < 65536:
            return "remote_port_not_configured"
        return "local_port_not_configured"

    def __repr__(self) -> str:  # never the target, never a port: this object's repr can end up in a traceback
        return f"Forward(purpose={self.purpose!r}, state={self.state!r}, reason={self.reason!r})"

    def endpoint(self) -> Optional[str]:
        """`tcp://127.0.0.1:<local port>` while a child of this forward exists, else None. PRIVATE: it is for this
        process's own subscriber and lease client. It is never reported, logged or sent to a client."""
        with self._lock:
            return f"tcp://{LOOPBACK}:{self.local_port}" if self.local_port and self.state in (STARTING, RUNNING) else None

    def _set(self, state: str, reason: Optional[str]) -> None:
        """The state and its reason move TOGETHER: a reader must never see one of the two updated."""
        with self._lock:
            self.state, self.reason = state, reason

    # -- lifetime ------------------------------------------------------------------------------------------------------
    def start(self) -> bool:
        """Open the forward. True when a child exists afterwards. Contacts the robot computer over ssh and nothing else:
        no remote command runs, and no motor, camera or torque is touched at either end."""
        with self._lock:
            if not self.configured:
                self._set(NOT_CONFIGURED, self._missing())
                return False
            if self._proc is not None:
                return True
            binary = shutil.which(self._ssh_binary)
            if binary is None:
                self._set(FAILED, "ssh_not_found")
                return False
            port = self._wanted_local_port or free_loopback_port()
            if not port:
                self._set(FAILED, "no_local_port")
                return False
            argv = self._argv(binary, port)
            try:
                # The output goes to the null device: ssh writes the target into its own messages, and this module's
                # promise is that the target never reaches a file, a log or a report. start_new_session: a stop signals
                # THIS child through its own handle, never a process group somebody else's children share.
                proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                        start_new_session=True, close_fds=True)
            except Exception:  # noqa: BLE001 -- the message of an OSError carries the argv, and the argv carries the target
                self._set(FAILED, "spawn_failed")
                return False
            self._proc, self.local_port, self._started_ns = proc, port, self._clock_ns()
            self.starts += 1
            self._set(STARTING, "waiting_for_the_forward")
            return True

    def _argv(self, binary: str, port: int) -> list:
        """`ssh -N -L 127.0.0.1:<local>:127.0.0.1:<remote> <target>`: a port on THIS computer's loopback that reaches the
        robot computer's loopback. ReverseForward overrides only this."""
        return [binary, *SSH_OPTIONS, "-L", f"{LOOPBACK}:{port}:{LOOPBACK}:{self._remote_port}", self._target]

    def check(self) -> str:
        """Look once at the child and return the state. A child that has exited becomes 'dead' with the status it exited
        on and STAYS dead: nothing here restarts anything, so a forward that keeps failing is visible instead of looping."""
        with self._lock:
            proc = self._proc
            if proc is None:
                return self.state
            status = proc.poll()
            if status is not None:
                self._proc, self.local_port, self._started_ns = None, None, None
                self.deaths, self.last_exit_status = self.deaths + 1, status
                self._set(DEAD, exit_reason(status))
                return self.state
            if self.state == STARTING and self._started_ns is not None and self._clock_ns() - self._started_ns >= self._settle_ns:
                self._set(RUNNING, None)
            return self.state

    def stop(self) -> None:
        """Close the forward and release its port. SIGTERM to this child through its own handle; a child that ignores it
        is killed after the grace period, which is counted and reported. Nothing on the robot computer is told anything:
        the ssh session simply ends, and no motor, camera or torque is touched by its ending."""
        with self._lock:
            proc, self._proc = self._proc, None
            self.local_port, self._started_ns = None, None
            configured = self.configured
            self._set(STOPPED if configured else NOT_CONFIGURED, "stopped" if configured else self._missing())
        if proc is None:
            return
        try:
            proc.terminate()
        except OSError:
            pass  # already gone; the wait below reaps it
        try:
            self.last_exit_status = proc.wait(self._stop_grace_s)
        except subprocess.TimeoutExpired:
            self.forced_stops += 1
            try:
                proc.kill()
                self.last_exit_status = proc.wait(1.0)
            except (OSError, subprocess.TimeoutExpired):
                pass
        except OSError:
            pass

    # -- views ---------------------------------------------------------------------------------------------------------
    def report(self) -> dict:
        """What `status` says about this forward. No target, no port, no endpoint: states, reason codes and counters."""
        with self._lock:
            return {"purpose": self.purpose, "configured": self.configured, "state": self.state, "reason": self.reason,
                    "starts": self.starts, "deaths": self.deaths, "forced_stops": self.forced_stops,
                    "exit_status": self.last_exit_status, "child": self._proc is not None}


class Forwards:
    """The forwards of one runtime, keyed by purpose. It owns nothing else: no journal, no run, no lease, no command."""

    def __init__(self, forwards: Optional[dict] = None):
        given = forwards or {}
        self._forwards = {purpose: given.get(purpose) or Forward(purpose) for purpose in PURPOSES}

    @classmethod
    def from_config(cls, config, **options) -> "Forwards":
        """The forwards THIS configuration needs, or none at all.

        The tap and the preview are SEPARATE switches, because they are separate things: the tap carries the host's
        observation stream and a preview carries a camera picture, and a deployment may want either, both or neither.
        The baseline NORMAL host runs with a preview and no tap at all, so gating the preview on the tap (which is what
        this did) made two real cameras impossible to reach without also enabling the boundary's stream.

        One preview forward PER ROLE, each with its own local and remote port, so two cameras can never share a tunnel.
        The local ports are the ones demo_runtime.sources connects to; the remote ports are the robot computer's own.
        The control forward has no configured port and gets a chosen one, and is opened whenever anything else is.
        """
        target = getattr(config, "pi_ssh_target", "") or ""
        options.setdefault("refused_remote_ports", (getattr(getattr(config, "host", None), "cmd_port", None),))
        tap_endpoint = sources.tap_endpoint(config)
        previews = {role: sources.preview_endpoint(config, role) for role in PREVIEW_ROLES}
        previews = {role: endpoint for role, endpoint in previews.items() if endpoint is not None}
        if tap_endpoint is None and not previews:
            return cls()
        built = {CONTROL: Forward(CONTROL, ssh_target=target, remote_port=HOST_CONTROL_PORT, **options)}
        if tap_endpoint is not None:
            built[TAP] = Forward(TAP, ssh_target=target, remote_port=HOST_TAP_PORT, local_port=endpoint_port(tap_endpoint),
                                 local_port_fixed=True, **options)
        for role, endpoint in previews.items():
            purpose = preview_purpose(role)
            built[purpose] = Forward(purpose, ssh_target=target, remote_port=CAMERA_SERVICE_PREVIEW_PORTS[role],
                                     local_port=endpoint_port(endpoint), local_port_fixed=True, **options)
        return cls(built)

    def get(self, purpose: str) -> Optional[Forward]:
        return self._forwards.get(purpose)

    @property
    def configured(self) -> bool:
        return any(forward.configured for forward in self._forwards.values())

    def start(self) -> dict:
        """Open every configured forward. {purpose: whether a child exists}. One that cannot be opened does not stop
        the others: each carries its own reason and the daemon reports all three."""
        return {purpose: forward.start() for purpose, forward in self._forwards.items()}

    def check(self) -> dict:
        return {purpose: forward.check() for purpose, forward in self._forwards.items()}

    def stop(self) -> None:
        for forward in self._forwards.values():
            forward.stop()

    def endpoint(self, purpose: str) -> Optional[str]:
        forward = self._forwards.get(purpose)
        return forward.endpoint() if forward is not None else None

    def dead(self) -> list:
        """The purposes whose child has exited, checked NOW. The caller reports them; nothing here restarts one."""
        return [purpose for purpose, forward in self._forwards.items() if forward.check() == DEAD]

    def report(self) -> dict:
        return {purpose: forward.report() for purpose, forward in self._forwards.items()}


# ---- the desktop perception service: a REVERSE forward, from the robot's loopback to this computer's ------------------
#
# WHY THE DIRECTION IS REVERSED. Every forward above lets THIS computer reach a socket on the robot. The one offboard
# exception the operator approved (the complete frame perception on the desktop GPU; bin/laptop/perception/
# perception_service.py) is the other way round: the robot-side controller asks, the desktop answers. The service binds
# this computer's loopback only and carries no credential, so the robot reaches it through `ssh -N -R 127.0.0.1:<robot
# port>:127.0.0.1:<service port>`, which this computer owns: the tunnel exists exactly as long as the service's owner wants
# it, and the robot account opens nothing towards the desktop.
#
# WHAT IT DOES TO THE ROBOT. One ssh session, `-N`, no remote command; sshd on the robot listens on the named port and hands
# each connection to this computer's service. It touches no motor, camera or torque, and it carries perception answers only.
#
# THE ROBOT PORT MAY NOT BE ONE OF THE ROBOT'S OWN SERVICE PORTS. A listener there would STAND IN for that service: on the
# camera service's mission port a controller would send its capture requests to the desktop; on the host's command port it
# would push velocities into a tunnel. Those ports are refused as a state, before ssh is spawned, like the raw command
# port above: the host's command and observation sockets (and the configured ones when they differ), the host boundary's
# control and tap sockets, the camera service's mission socket and both of its preview sockets.
#
# WHERE sshd BINDS IT IS THE ROBOT'S DECISION, NOT THIS ARGV'S. The address in `-R 127.0.0.1:...` is honoured only when
# the robot's sshd has GatewayPorts clientspecified; with the default (no) it binds loopback anyway, and with `yes` it binds
# every interface whatever is asked. That setting is UNKNOWN from here, so the listener is read back from the robot's
# kernel (ss -ltn) by bin/pi/offboard_perception_probe.py, and anything but loopback refuses.
REVERSE_PERCEPTION = "perception_reverse"
CAMERA_SERVICE_MISSION_PORT = 5570  # bin/pi/camera_service.py DEFAULT_MISSION_PORT (demo_runtime.config spells it too)
HOST_OBSERVATION_PORT = 5556  # lerobot LeKiwiHostConfig.port_zmq_observations, demo_runtime.config.DEFAULT_OBS_PORT
ROBOT_SERVICE_PORTS = tuple(sorted({RAW_HOST_COMMAND_PORT, HOST_OBSERVATION_PORT, HOST_CONTROL_PORT, HOST_TAP_PORT, CAMERA_SERVICE_MISSION_PORT,
                                    *CAMERA_SERVICE_PREVIEW_PORTS.values()}))
REFUSED_ROBOT_SERVICE_PORT = "robot_service_port_refused"


class ReverseForward(Forward):
    """ONE `ssh -N -R 127.0.0.1:<robot port>:127.0.0.1:<service port>` child. The lifetime, the states, the reports and
    the promise that the target reaches no file are Forward's; only the direction and the refused ports differ."""

    def __init__(self, *, ssh_target: str = "", robot_port: int = 0, service_port: int = 0, refused_robot_ports=(), **options):
        super().__init__(REVERSE_PERCEPTION, ssh_target=ssh_target, remote_port=robot_port, local_port=service_port, local_port_fixed=True,
                         refused_remote_ports=(*ROBOT_SERVICE_PORTS, *(int(port) for port in refused_robot_ports if port)), **options)

    def _missing(self) -> str:
        return REFUSED_ROBOT_SERVICE_PORT if self._refused else super()._missing()

    def _argv(self, binary: str, port: int) -> list:
        return [binary, *SSH_OPTIONS, "-R", f"{LOOPBACK}:{self._remote_port}:{LOOPBACK}:{port}", self._target]

    def endpoint(self) -> Optional[str]:
        """None, always: this computer does not CONNECT through a reverse forward. The service it carries is reached on
        the robot's side, at the robot port, by the robot's own controller."""
        return None


# ---- the robot's admin socket: a UNIX-socket forward that is restarted, because it IS the robot connection ----------
#
# WHY THIS ONE IS DIFFERENT. Every forward above is reported dead and left dead, because an ssh that keeps dying is a
# fact an operator has to see. This one carries the ONLY connection this runtime has to the robot's Cake resident, and a
# dropped wifi link is routine: leaving it dead would turn a two second outage into a console that says "robot
# unreachable" until somebody restarts the daemon. So it is restarted, with a BOUNDED backoff, and it still reports
# every state it passes through: connecting, connected, failed with the reason, and since when. A forward that keeps
# failing is visible as `failed` with a growing death count and the time of the next attempt, never hidden in a loop.
#
# WHAT IT DOES TO THE ROBOT. The same as the forwards above: one ssh session, `-N`, no remote command. The remote end
# is the resident's admin socket, and only cake_admin's read-only allowlist (and cake_activation, which says in its
# name that it mutates) ever speaks through it.
#
# A STALE SOCKET FILE IS NOT A RESIDENT. ssh leaves its local socket behind when it is killed. A reader that found that
# file would connect, be refused by nobody, and report a Cake refusal. So this forward removes a stale socket before it
# spawns and after a death, `connected` requires a live child AND the socket file, and demo_runtime.robot_link does not
# ask the resident anything unless this forward says `connected`.
#
# A LIVE SOCKET IS NEVER REPLACED. Before anything at the local path is removed it is CONNECT-PROBED: only a socket that
# refuses the connection (ECONNREFUSED, which is what a socket nobody listens on answers) is stale and removed. One that
# accepts, or one whose state cannot be told, is somebody's live socket (a misconfigured path could name this laptop's
# own resident) and the forward refuses to start. ssh is NOT given StreamLocalBindUnlink: it then refuses to bind over
# anything at the path, and ExitOnForwardFailure makes that an exit, so a socket that appeared between the probe and the
# bind is not replaced either. ConnectTimeout bounds how long an unreachable robot reads as `connecting`.
ROBOT_ADMIN = "robot_admin"
CONNECTING, CONNECTED = "connecting", "connected"
UNIX_FORWARD_STATES = (NOT_CONFIGURED, STOPPED, CONNECTING, CONNECTED, FAILED)
FORWARD_CONNECT_TIMEOUT_S = 6  # the same bound the ssh readiness probe uses (preflight.SSH_CONNECT_TIMEOUT_S)
UNIX_SSH_OPTIONS = SSH_OPTIONS + ("-o", f"ConnectTimeout={FORWARD_CONNECT_TIMEOUT_S}")
BACKOFF_INITIAL_S, BACKOFF_MAX_S = 1.0, 30.0
LIVE_PROBE_TIMEOUT_S = 0.5


def _is_socket(path: str) -> bool:
    import stat

    try:
        return stat.S_ISSOCK(os.lstat(path).st_mode)
    except OSError:
        return False


def socket_is_stale(path: str, timeout_s: float = LIVE_PROBE_TIMEOUT_S) -> bool:
    """True ONLY when a connection to the unix socket at `path` is REFUSED, which is what a socket file nobody listens on
    answers. A socket that accepts is live; a timeout, a permission error or anything else is a socket whose state this
    cannot tell, and neither may be removed."""
    import errno

    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        probe.settimeout(timeout_s)
        probe.connect(path)
    except OSError as failure:
        return failure.errno == errno.ECONNREFUSED
    finally:
        probe.close()
    return False


def _wall_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class UnixSocketForward:
    """ONE `ssh -N -L <local socket>:<remote socket>` child, restarted with a bounded backoff when it dies.

    Every method is safe to call twice and none raises into a caller. check() is the only place a restart happens, so a
    caller that polls it (demo_runtime.robot_link.RobotLink does, on its own thread) decides the cadence, and a test
    that drives it by hand decides every step."""

    def __init__(self, *, ssh_target: str = "", remote_socket: str = "", local_socket: str = "", ssh_binary: str = "ssh",
                 clock_ns: Callable[[], int] = time.monotonic_ns, wall: Callable[[], str] = _wall_now,
                 backoff_initial_s: float = BACKOFF_INITIAL_S, backoff_max_s: float = BACKOFF_MAX_S,
                 stop_grace_s: float = STOP_GRACE_S):
        self.purpose = ROBOT_ADMIN
        self._target = ssh_target if isinstance(ssh_target, str) else ""
        self._remote = remote_socket if isinstance(remote_socket, str) else ""
        self._local = local_socket if isinstance(local_socket, str) else ""
        self._ssh_binary, self._clock_ns, self._wall = ssh_binary, clock_ns, wall
        self._backoff_initial_ns, self._backoff_max_ns = int(backoff_initial_s * 1e9), int(backoff_max_s * 1e9)
        self._backoff_ns = self._backoff_initial_ns
        self._stop_grace_s = float(stop_grace_s)
        self._lock = threading.RLock()
        self._proc = None
        self._wanted = False  # started on purpose and not stopped since: only then does a death lead to a restart
        self._next_attempt_ns: Optional[int] = None
        self.starts = self.deaths = self.restarts = self.forced_stops = 0
        self.last_exit_status: Optional[int] = None
        self.state, self.reason = (STOPPED, "not_started") if self.configured else (NOT_CONFIGURED, self._missing())
        self.since_mono_ns, self.since_wall_utc = self._clock_ns(), self._wall()

    @property
    def configured(self) -> bool:
        return bool(self._target) and self._remote.startswith("/") and self._local.startswith("/")

    def _missing(self) -> str:
        if not self._target:
            return "ssh_target_not_configured"
        if not self._remote.startswith("/"):
            return "remote_socket_not_configured"
        return "local_socket_not_configured"

    def __repr__(self) -> str:  # never the target and never a path
        return f"UnixSocketForward(state={self.state!r}, reason={self.reason!r})"

    def local_socket(self) -> Optional[str]:
        """The local socket path while this forward says it is CONNECTED, else None. PRIVATE: for this process's own
        admin reader. Never reported, logged or sent to a client."""
        with self._lock:
            return self._local if self.state == CONNECTED else None

    def _set(self, state: str, reason: Optional[str]) -> None:
        with self._lock:
            if (state, reason) != (self.state, self.reason):
                self.since_mono_ns, self.since_wall_utc = self._clock_ns(), self._wall()
            self.state, self.reason = state, reason

    def _clear_stale_socket(self) -> Optional[str]:
        """Remove a socket file left at the local path by a dead ssh. A path that holds anything but a socket is NOT
        removed: it is somebody else's file, and the reason code says so. Nor is a socket that answers, or one whose
        state cannot be told: only a connection REFUSED makes it stale (socket_is_stale)."""
        if not os.path.lexists(self._local):
            return None
        if not _is_socket(self._local):
            return "local_socket_path_occupied"
        if not socket_is_stale(self._local):
            return "local_socket_path_live"
        try:
            os.unlink(self._local)
        except OSError:
            return "stale_local_socket_not_removable"
        return None

    def _spawn(self) -> bool:
        binary = shutil.which(self._ssh_binary)
        if binary is None:
            self._fail("ssh_not_found")
            return False
        problem = self._clear_stale_socket()
        if problem is not None:
            self._fail(problem)
            return False
        parent = os.path.dirname(self._local)
        try:
            if parent and not os.path.isdir(parent):
                os.makedirs(parent, mode=0o700, exist_ok=True)
        except OSError:
            self._fail("local_socket_directory_unavailable")
            return False
        argv = [binary, *UNIX_SSH_OPTIONS, "-L", f"{self._local}:{self._remote}", self._target]
        try:
            # The null device, for the reason the forwards above give: ssh writes the target into its own messages.
            proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    start_new_session=True, close_fds=True)
        except Exception:  # noqa: BLE001 -- the message of an OSError carries the argv, and the argv carries the target
            self._fail("spawn_failed")
            return False
        self._proc = proc
        self.starts += 1
        self._set(CONNECTING, "waiting_for_the_forward_socket")
        return True

    def _fail(self, reason: str) -> None:
        """A failure schedules the next attempt, doubling the wait up to the bound. Nothing here sleeps."""
        self._next_attempt_ns = self._clock_ns() + self._backoff_ns
        self._backoff_ns = min(self._backoff_ns * 2, self._backoff_max_ns)
        self._set(FAILED, reason)

    def start(self) -> bool:
        """Open the forward. True when a child exists afterwards. ssh and nothing else: no remote command runs."""
        with self._lock:
            if not self.configured:
                self._set(NOT_CONFIGURED, self._missing())
                return False
            self._wanted = True
            if self._proc is not None:
                return True
            return self._spawn()

    def check(self) -> str:
        """Look once at the child, and restart it when its backoff has run out. Returns the state."""
        with self._lock:
            if not self._wanted:
                return self.state
            proc = self._proc
            if proc is None:
                if self.state == FAILED and self._next_attempt_ns is not None and self._clock_ns() >= self._next_attempt_ns:
                    self.restarts += 1
                    self._spawn()
                return self.state
            status = proc.poll()
            if status is not None:
                self._proc = None
                self.deaths, self.last_exit_status = self.deaths + 1, status
                self._clear_stale_socket()
                self._fail(exit_reason(status))
                return self.state
            if _is_socket(self._local):
                if self.state != CONNECTED:
                    self._backoff_ns = self._backoff_initial_ns  # a forward that came up earns a fresh backoff
                self._set(CONNECTED, None)
            else:
                self._set(CONNECTING, "waiting_for_the_forward_socket" if self.state != CONNECTED else "forward_socket_vanished")
            return self.state

    def stop(self) -> None:
        """Close the forward and remove its local socket. Nothing on the robot is told anything."""
        with self._lock:
            proc, self._proc, self._wanted, self._next_attempt_ns = self._proc, None, False, None
            configured = self.configured
            self._set(STOPPED if configured else NOT_CONFIGURED, "stopped" if configured else self._missing())
        if proc is not None:
            try:
                proc.terminate()
            except OSError:
                pass
            try:
                self.last_exit_status = proc.wait(self._stop_grace_s)
            except subprocess.TimeoutExpired:
                self.forced_stops += 1
                try:
                    proc.kill()
                    self.last_exit_status = proc.wait(1.0)
                except (OSError, subprocess.TimeoutExpired):
                    pass
            except OSError:
                pass
        with self._lock:
            if self.configured and self._proc is None:
                self._clear_stale_socket()

    def report(self) -> dict:
        """States, reason codes, counters and times. No target, no path."""
        with self._lock:
            now = self._clock_ns()
            return {"purpose": self.purpose, "configured": self.configured, "state": self.state, "reason": self.reason,
                    "since_wall_utc": self.since_wall_utc, "since_mono_ns": self.since_mono_ns,
                    "since_age_ms": max(0.0, round((now - self.since_mono_ns) / 1e6, 3)),
                    "starts": self.starts, "deaths": self.deaths, "restarts": self.restarts,
                    "forced_stops": self.forced_stops, "exit_status": self.last_exit_status,
                    "next_attempt_in_ms": (None if self._next_attempt_ns is None or self._proc is not None
                                           else max(0.0, round((self._next_attempt_ns - now) / 1e6, 3))),
                    "child": self._proc is not None}
