# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""demo_runtime.daemon: the runtime daemon.

    python3 -m demo_runtime.daemon --config FILE --data-dir DIR [--enable-hardware-commands] 
    python3 -m demo_runtime.daemon --data-dir DIR --synthetic            (only the fake controller ever runs)
    python3 -m demo_runtime.daemon --data-dir DIR --replay-only          (no supervisor, no command path at all)

THE TWO SOURCES. In live mode the daemon attaches demo_runtime.sources: a subscriber of the host tap and one of the
camera service's preview, each with its own state (disconnected, connecting, connected, stalled, failed) and its own
reason code. Both are SUB sockets: attaching them commands nothing. They feed the telemetry stream the browser reads,
they decide the snapshot's link (connected only while fresh samples arrive), and a source that is not configured says
exactly that instead of showing a number. See demo_runtime.sources for where the endpoints come from.

THE FORWARDS AND THE LEASE. Those endpoints are loopback ports of THIS laptop that only exist while an ssh forward to the
robot computer does, and a host with its boundary on drops every command that does not carry the live lease. In live mode
the daemon therefore owns both: demo_runtime.forwards opens one `ssh -N -L` child per purpose (control, tap, preview) and
demo_runtime.supervisor takes the lease over the control forward before it spawns a controller that could command motion.
Both are OFF unless the configuration enables the tap, and both leave the robot alone: `-N` runs no remote command, the
lease is JSON on a loopback socket, and no part of this process constructs a robot client or sends an action.

LIFETIME. The daemon is independent of the API server and of the browser: they are clients of its socket and may come and
go. Starting the daemon, or a client, NEVER acquires motors, releases torque, arms anything or contacts the robot: a start
reads the journal, looks at the local process table for a controller a previous daemon left behind, and listens. The
daemon is not in the motion path either: the controller talks to the robot by itself, its output goes to files, and it
keeps running when the daemon dies. The next daemon adopts it.

THE DATA DIRECTORY is private (0700) and outside the repository:
    runtime.sock     the RPC socket, 0600           journal.jsonl   the command journal (fsynced)
    events.jsonl     the runtime event log           daemon.lock     one daemon per data directory (flock)
    missions.jsonl   the mission record (fsynced), written only by a runtime that has a mission surface at all
    capability-checks.jsonl  the capability slot check record (fsynced), written only by a LIVE runtime that has a check
    bundles/<digest>/  pinned, read-only run bundles   history/<run_id>/  the run directory (manifest, controller events, output, completion)

MODES. live: commands are blocked until --enable-hardware-commands, and then the preflight and ownership decide each one.
synthetic: the supervisor runs ONLY packages/demo-runtime/tests/fake_controller.py and every snapshot says data_mode
synthetic. replay-only: the Supervisor class is never constructed (its module is not even imported); submit, stop and resolve
answer reason_code replay_only; history and events are served from what the directory already holds.
"""
from __future__ import annotations

import argparse
import fcntl
import functools
import importlib.util
import json
import os
import signal
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

from . import cake_admin, read_models, robot_link, sources
from .config import robot_link_is_local, robot_of, signature_paths
from .journal import (CONTRACT_VERSION, RUNTIME_VERSION, EventLog, Journal, command_result, fold_commands, history_entries, journal_counters, last_outcome, make_event,
                      utc_compact, utc_now_text)
from .rpc import RuntimeServer, stream_events, stream_telemetry

import demo_contracts  # importable once .journal has been imported

SOCKET_NAME, JOURNAL_NAME, EVENTS_NAME, LOCK_NAME = "runtime.sock", "journal.jsonl", "events.jsonl", "daemon.lock"
# The run directory and the manifest the supervisor writes into it, named HERE because a replay-only daemon must never
# import that module: not importing it is what makes a replay-only process unable to start anything at all. A test keeps
# these two names equal to the supervisor's own.
HISTORY_DIR_NAME, MANIFEST_FILE_NAME = "history", "manifest.json"
# The controller's own output, in the same run directory and named here for the same reason: serving it must not make a
# replay-only daemon able to import the supervisor. The same test keeps these equal to the supervisor's names.
STDOUT_FILE_NAME, STDERR_FILE_NAME = "stdout.log", "stderr.log"
# The most controller output one read returns. A run can print for an hour; a reader gets the END of it and is told so.
MAX_OUTPUT_BYTES = 256 * 1024
FAKE_CONTROLLER_ENTRY = "packages/demo-runtime/tests/fake_controller.py"
LIVE, SYNTHETIC, REPLAY = "live", "synthetic", "replay"
REPLAY_REFUSAL = {"ok": False, "reason_code": "replay_only"}
# The largest pid a robot session confirmation may name: Linux's own ceiling (pid_max is at most 2^22).
MAX_HOST_PID = robot_link.MAX_HOST_PID
UNKNOWN_LINK = {"state": "unknown", "source_stamp": None, "receive_age_ms": None}
# The snapshot properties that are OPTIONAL in the contract. They are INSIDE the contract object now: a client validates
# the whole answer and strips nothing, and a snapshot recorded by a runtime that had none of them is still valid, which is
# what "optional" buys. Two of them (unresolved_command, lease) were carried BESIDE the object while it had no room for
# them. A test in demo-contracts keeps this tuple equal to the schema's optional set, so neither side can move alone.
ADDITIVE_SNAPSHOT_KEYS = ("unresolved_command", "lease", "timings", "run_evidence", "execution", "latest_retained_observation",
                          "latest_measured_state")
LEASE_OWNER_LABEL = "kiwi-console-runtime"  # what the host shows as the owner of the lease: a role, never an identity
# ONE STOP, THREE DOORS. The plain stop op, the mission stop op and a submitted stop COMMAND all reach
# RuntimeCore._stop_run_and_mission. The controller run is always stopped as an OPERATOR CONSOLE stop: that is the only
# origin contract v1's stop.requested has for a person pressing Stop, and a mission Stop pressed on the console is one.
# Which door it came through is recorded on the mission's own record, which is not contract v1 and has room for it.
STOP_RUN_ORIGIN = "operator_console"
STOP_BY_STOP_OP, STOP_BY_MISSION_STOP, STOP_BY_COMMAND = "stop", "mission_stop", "stop_command"
# Why a command other than stop is refused while a mission is carried: the mission holds the single run slot.
MISSION_HOLDS_RUN_SLOT = "mission_running"
# Why a command other than stop, or a mission start, is refused while a capability slot check holds the run slot. It is
# capability_check.IN_FLIGHT, named here so this module does not import that one to be loaded; a test keeps the two equal.
CAPABILITY_CHECK_HOLDS_RUN_SLOT = "capability_check_in_flight"


def default_source_root() -> Path:
    """The checkout (or the bundle) this module was loaded from: <root>/packages/demo-runtime/demo_runtime/daemon.py."""
    return Path(__file__).resolve().parents[3]


class DataDirBusy(RuntimeError):
    """Another daemon holds this data directory."""


class RobotConfigRefused(ValueError):
    """A runtime mode that must never reach the robot's resident was given a configuration that names one."""


class _SyntheticObservations:
    """Observations of --synthetic mode: built in memory by the fake controller's module, on request. Nothing is read from disk."""

    def __init__(self, module):
        self._module = module

    def record(self, observation_id):
        return self._module.synthetic_record(observation_id)

    def perception(self, observation_id):
        return self._module.synthetic_perception(observation_id)

    def image(self, observation_id, what):
        return self._module.synthetic_frame(observation_id, what)


def _load_fake_controller(source_root: Path):
    spec = importlib.util.spec_from_file_location("kc_fake_controller", source_root / FAKE_CONTROLLER_ENTRY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RuntimeCore:
    """Everything behind the socket. One instance per daemon process."""

    def __init__(self, *, data_dir, mode: str, config=None, config_path: Optional[str] = None, hardware_enabled: bool = False, source_root=None,
                 ring_size: Optional[int] = None, cake_probe=None, extra_env: Optional[dict] = None,
                 source_supervisor=None, forwards=None, lease=None, readiness=None, robot_link=None, robot_host_watch=None,
                 controller_entry: Optional[str] = None):
        if mode not in (LIVE, SYNTHETIC, REPLAY):
            raise ValueError("mode must be live, synthetic or replay")
        # A SYNTHETIC RUNTIME NEVER REACHES THE ROBOT'S RESIDENT. Its mission surface carries the rehearsal adapter, and
        # a rehearsal that could submit a real ApplyPlan to the robot through the forward would be a rehearsal that
        # changes a robot. Refused here, before the data directory is even locked, so no part of such a runtime exists.
        self.robot_config = robot_of(config)
        if mode == SYNTHETIC and self.robot_config is not None:
            raise RobotConfigRefused("a synthetic runtime refuses a configuration with cake.robot: a rehearsal must never reach the robot's resident")
        self.mode, self.config = mode, config
        # FOR A TEST: the controller a LIVE runtime would spawn, relative to the source root. A test that builds a live
        # runtime names the fake controller here, so no test can start the real one even if every guard in front of a
        # spawn regressed. main() never passes it: a live daemon always runs bundle.CONTROLLER_ENTRY.
        self._controller_entry = controller_entry
        self.source_root = Path(source_root) if source_root else default_source_root()
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.data_dir, 0o700)
        self._lock_fd = os.open(self.data_dir / LOCK_NAME, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(self._lock_fd)
            raise DataDirBusy("another runtime daemon holds this data directory") from None
        self.runtime_session = f"rt-{utc_compact()}-{uuid.uuid4().hex[:8]}"
        replay = mode == REPLAY
        self.journal = Journal(self.data_dir / JOURNAL_NAME, read_only=replay)
        self.log = EventLog(self.data_dir / EVENTS_NAME, read_only=replay, **({"ring_size": ring_size} if ring_size else {}))
        self.stats = read_models.SessionStats()
        self.log.add_listener(self.stats.observe)
        self.sanitizer = read_models.Sanitizer.for_runtime(config, extra=[str(self.data_dir), str(self.data_dir.resolve()), str(self.source_root), config_path or ""])
        self.hub = read_models.TelemetryHub()
        # THE CAKE PROBE, with a real resident behind it when one is configured.
        #
        # OUTSIDE THE REQUEST PATH, twice over: CachedProbe holds an answer for its TTL, so a snapshot does not start a
        # child process, and the child that is started carries a bounded timeout. A resident that stops answering
        # therefore costs one bounded wait per TTL and cannot stall a snapshot, a command or the robot: nothing in the
        # command path reads this at all.
        # What survives a resident that stops answering, folded by the ONE probe below rather than by a request, so
        # that losing contact retains the last real reading whoever triggered the poll that lost it.
        self.cake_retention = read_models.CakeRetention()
        # THE ROBOT'S RESIDENT, when the configuration names one (cake.robot). The forward that reaches it is owned
        # here in live mode and restarted when it dies. The permission to move rests on the robot resident's session and
        # its supervisor host's pid as the operator confirmed them IN THIS RUNTIME: it is held in memory, so a daemon
        # restart needs a fresh confirmation, and the data directory keeps the last one only as an audit record.
        # Absent robot configuration: all three stay None and nothing changes.
        self.robot = None
        self.robot_host_watch = None
        self._robot_link_source, self._robot_host_watch_source = robot_link, robot_host_watch
        self.motion_permission = _motion_permission(self.data_dir) if self.robot_config is not None else None
        self.cake = read_models.CachedProbe(self._folding_cake_probe(cake_probe or self._probe_cake_with_resident),
                                            ttl_s=(config.cake.poll_interval_s if getattr(config, "cake", None) else 5.0))
        self.client_sessions: set = set()
        self.client_reconnects = 0
        self._clients_lock = threading.Lock()
        self.supervisor = None
        # The background reading of the robot computer. `readiness` is for a TEST: an instance, or a callable handed the
        # ssh target that returns one. It replaces only the construction, never the decision below about whether a
        # reading is wanted at all, so a test that hands one in still exercises that decision.
        self.readiness = None
        self._readiness_source = readiness
        self._synthetic_telemetry = None
        self.sources = None
        self.frames = None
        self.forwards = None
        self.lease = None
        self._observations = None
        self._manifests: dict = {}  # run_id -> the manifest that run wrote, once it has been read and checked
        # THE MISSION SURFACE, built only when this runtime has everything one needs, and answering a reason code
        # when it does not. It is separately versioned and carries no contract object; see demo_runtime.mission.
        #
        # BUILT AFTER THE SUPERVISOR, because a LIVE hardware mission carries each target as a supervised run and is handed
        # that supervisor. Until then there is none, and the supervisor's run-slot gate reads None (nothing carried).
        self.mission, self.mission_candidates, self.mission_unavailable = None, None, None
        # THE CAPABILITY SLOT CHECK (demo_runtime.capability_check), built last and only in a LIVE runtime that names the
        # robot, prepared candidates and a capability binding. None until then, so the supervisor's run-slot gate, which
        # is handed over before it exists, reads "no check in flight".
        self.capability_check, self.capability_check_unavailable = None, None
        # THE SIGNATURE POLICY CHECK (demo_runtime.signature_check): the release's own verifier run on the robot, read-only.
        # Built only in a LIVE runtime on the robot (local link) whose configuration names the release; None otherwise.
        self.signature_check, self.signature_check_unavailable = None, None
        if not replay:
            self.journal.append({"rec": "daemon_start", "runtime_session": self.runtime_session, "mode": mode, "t_wall_utc": utc_now_text()})
            self._attach_host_access(config, forwards, lease)
            self._attach_robot(config)
            self.supervisor = self._build_supervisor(config_path, hardware_enabled, extra_env)
        self.mission, self.mission_candidates, self.mission_unavailable = self._build_mission()
        self.capability_check, self.capability_check_unavailable = self._build_capability_check()
        self.signature_check, self.signature_check_unavailable = self._build_signature_check()
        if mode == SYNTHETIC:
            self._observations = _SyntheticObservations(_load_fake_controller(self.source_root))
            self._synthetic_telemetry = read_models.SyntheticTelemetry(self.hub, "synthetic-host")
            self._synthetic_telemetry.start()
        elif config is not None:
            self._observations = read_models.StoreObservationSource(config.observation_root, self._store_sessions)
        if self._observations is not None:
            self.frames = read_models.FrameService(self._observations)
        if mode == LIVE or source_supervisor is not None:
            # The two read-only sources. They are started even when nothing is configured: the slot then says so with a
            # reason code, which is what the console shows instead of an empty panel. Attaching a subscriber commands
            # nothing: both sockets are SUB sockets, and neither can send. source_supervisor is for a test: an instance,
            # or a callable that is handed THIS core's hub and returns one.
            built = source_supervisor(self.hub) if callable(source_supervisor) else source_supervisor
            self.sources = built if built is not None else sources.SourceSupervisor.from_config(self.hub, config)
            self.sources.start()

    def _attach_host_access(self, config, forwards, lease):
        """The loopback forwards, and then the lease client that speaks through the control one. In that order, because
        the lease client is given the LOCAL endpoint the control forward opened; a forward that is not there leaves the
        runtime with no lease client, and a command that would need one is then refused instead of being started blind.

        Both are live-mode only and both are OFF unless the configuration enables the tap. Opening a forward runs ssh on
        this laptop and asks the robot computer for a port: no remote command, no motor, no camera, no torque. The lease
        client sends JSON to a loopback socket. `forwards` and `lease` are for a test: an instance, or a callable handed
        the config (respectively the forwards) that returns one."""
        from . import forwards as forwards_module

        if forwards is not None:
            self.forwards = forwards(config) if callable(forwards) else forwards
        elif self.mode == LIVE and config is not None:
            self.forwards = forwards_module.Forwards.from_config(config)
        if self.forwards is not None:
            self.forwards.start()
        if lease is not None:
            self.lease = lease(self.forwards) if callable(lease) else lease
            return
        endpoint = self.forwards.endpoint(forwards_module.CONTROL) if self.forwards is not None else None
        if endpoint is not None:
            from .lease_client import LeaseClient

            self.lease = LeaseClient(endpoint, LEASE_OWNER_LABEL)

    def _attach_robot(self, config) -> None:
        """The robot's admin-socket forward, owned by this runtime, in LIVE mode only and only with cake.robot.

        `robot_link` (constructor) is for a test: an instance, or a callable handed the config. Opening the forward runs
        ssh on this laptop with -N: no remote command, no motor, no camera, no torque."""
        if self.mode != LIVE or self.robot_config is None:
            return
        source = self._robot_link_source
        if source is not None:
            link = source(config) if callable(source) else source
        elif robot_link_is_local(config):
            # THE ROBOT-SIDE DEPLOYMENT: this runtime runs on the robot beside the resident and reads its admin socket
            # directly. No ssh is started and nothing is forwarded.
            link = robot_link.LocalRobotLink(config)
        else:
            link = robot_link.RobotLink(config)
        self.robot = link.start() or link

    def _host_watch(self):
        """The ssh readiness reading the robot surface's `host` rests on: the supervisor's own when it has one, else the
        one started for the robot surface. None when this runtime reads nothing about the robot computer."""
        return self.readiness if self.readiness is not None else self.robot_host_watch

    def _build_supervisor(self, config_path, hardware_enabled, extra_env):
        from .config import ExecutionBackend, execution_backend_of
        from .supervisor import Supervisor, find_external_controllers  # imported HERE: a replay-only daemon never loads the module
        from . import bundle

        external, preflight, entry = None, None, FAKE_CONTROLLER_ENTRY
        if self.mode == LIVE:
            from .preflight import (ReadinessWatch, probe_motor_host, probe_motor_host_detail, probe_motor_host_detail_local,
                                    probe_motor_host_local, run_preflight, usable_ssh_target)

            entry, preflight = self._controller_entry or bundle.CONTROLLER_ENTRY, run_preflight
            # ON THE ROBOT (cake.robot.link local) the motor-host question is the SAME script, run here instead of over
            # ssh (preflight.local_runner): the robot computer it reads is the one this runtime runs on.
            local = robot_link_is_local(self.config)
            if local:
                probe_motor_host, probe_motor_host_detail = probe_motor_host_local, probe_motor_host_detail_local
                usable = lambda _target: True  # noqa: E731 -- a local probe has no ssh target to refuse
            else:
                usable = usable_ssh_target
            if execution_backend_of(self.config) is ExecutionBackend.BASELINE_NORMAL_HOST:
                # The baseline runs against the motor host in its NORMAL mode, so the question the proven launcher asks
                # before every call is the question that matters here: is that host up, and out of scan mode. Bound to
                # the preflight the supervisor already runs, so a command that fails it is refused with a named reason
                # and no controller is started. The supervisor switches it off for a snapshot; see _preflight_report.
                preflight = functools.partial(run_preflight, motor_host=probe_motor_host)
                # And the SAME question again, asked off the request path, so a snapshot can say what a submit would
                # find without an ssh on every poll. It is asked for the same target and under the backend that binds
                # the check above, because a snapshot must never report a blocker the submit beside it does not even
                # ask about. THREE THINGS HAVE TO HOLD, and each of them is a reason not to open an ssh at all:
                #   - hardware commands are enabled. Without them block() refuses every command with
                #     hardware_commands_not_enabled before it ever looks at a reading, so the answer would be one
                #     nobody reads;
                #   - the configured target can be handed to ssh. When it cannot, the preflight's own pi_ssh_target
                #     check already refuses, and a second refusal beside it would say the same thing twice;
                #   - this is the baseline backend, as above.
                if hardware_enabled and usable(self.config.pi_ssh_target):
                    source = self._readiness_source
                    # The detail probe asks the same question with the same script and also hands back the pids of the
                    # host processes it read, which the robot surface compares with the supervisor slot's child pid.
                    self.readiness = (source(self.config.pi_ssh_target) if callable(source) else source) if source is not None \
                        else ReadinessWatch(self.config.pi_ssh_target, probe=probe_motor_host_detail)
            if self.readiness is None and self.robot_config is not None and usable(self.config.pi_ssh_target):
                # The robot surface's `host` needs the same READ of the robot computer even when no command could use it
                # (hardware commands off, or the boundary backend). It is handed to NO supervisor, so it can never
                # become a snapshot blocker that the submit beside it does not ask about.
                source = self._robot_host_watch_source
                self.robot_host_watch = (source(self.config.pi_ssh_target) if callable(source) else source) if source is not None \
                    else ReadinessWatch(self.config.pi_ssh_target, probe=probe_motor_host_detail)
            external = lambda: find_external_controllers(os.path.basename(bundle.CONTROLLER_ENTRY), self.supervisor.known_scan_ids() if self.supervisor else [])  # noqa: E731
        supervisor = Supervisor(data_dir=self.data_dir, journal=self.journal, event_log=self.log, runtime_session=self.runtime_session, mode=self.mode,
                                source_root=self.source_root, controller_entry=entry, config=self.config, config_path=config_path, hardware_enabled=hardware_enabled,
                                sanitizer=self.sanitizer, external_probe=external, preflight=preflight, extra_env=extra_env, lease=self.lease, readiness=self.readiness,
                                motion_gate=self._fresh_motion_admission if self.motion_permission is not None else None,
                                run_slot_gate=self._mission_holds_run_slot)
        if self.readiness is not None:
            self.readiness.start()  # started LAST: a reader whose runtime failed to build would be a thread nothing can stop
        if self.robot_host_watch is not None:
            self.robot_host_watch.start()
        return supervisor

    def _build_mission(self) -> tuple:
        """(coordinator, prepared candidates, reason code). The builder decides; this only hands it what it needs.

        THE MODE IS THIS CORE'S OWN and is passed straight through. The execution adapter a mission would use is the
        recording rehearsal one, which demo_runtime.mission_rehearsal refuses to construct outside a synthetic
        runtime; a live core therefore gets no coordinator and every mission op answers a reason code. That refusal
        lives in the adapter's own constructor, not here, so this line cannot be the thing that lets one through.

        THE ONE EXCEPTION, AND IT SHIPS OFF. A LIVE core whose configuration names mission.execution "proven_controller"
        asks build_hardware_mission_coordinator instead, which builds the robot-side execution only when every one of its
        conditions holds and answers the first missing one as the reason code. With the key unset (as shipped) a live
        core answers exactly what it always did.
        """
        from .config import MISSION_EXECUTION_PROVEN, mission_of
        from .mission_service import build_hardware_mission_coordinator, build_mission_coordinator

        section = mission_of(self.config)
        if self.mode == LIVE and section is not None and section.execution == MISSION_EXECUTION_PROVEN:
            hardware_enabled = bool(self.supervisor is not None and self.supervisor.hardware_enabled)
            return build_hardware_mission_coordinator(mode=self.mode, config=self.config, data_dir=self.data_dir,
                                                      supervisor=self.supervisor, hardware_enabled=hardware_enabled)
        return build_mission_coordinator(mode=self.mode, config=self.config, data_dir=self.data_dir)

    def _build_capability_check(self) -> tuple:
        """(check, None) or (None, the reason there is none). capability_check.unavailable_reason decides, in its order.

        THE ADAPTER IS THE ONE THE HARDWARE MISSION WOULD USE (mission_service.capability_adapter with the robot's pinned
        build identity), so every ApplyPlan a check sends is preceded by a get-status of the answering resident and refused
        unless it reports cake.robot.expected_build_identity. Imported HERE: a runtime with no check never loads it."""
        from . import capability_check
        from .mission_service import (ACTIVATION_TIMEOUT_S, SLOT_READ_TIMEOUT_S, capability_adapter, capability_binding,
                                      prepared_candidates)

        probe_path, socket_path = cake_admin.configured_paths(self.config)
        candidates = prepared_candidates(self.config)
        binding = capability_binding(self.config)
        reason = capability_check.unavailable_reason(live=self.mode == LIVE, robot=self.robot_config, probe_path=probe_path,
                                                     socket_path=socket_path, candidates=candidates, binding=binding)
        if reason is not None:
            return None, reason
        _name, slot_uuid, active_capsule = binding
        # THE SAME FIGURES BUILD THE ADAPTER AND BOUND THE SHUTDOWN WAIT (capability_check.close_wait_bound_s), so a
        # change to either bound moves the wait with it.
        adapter = capability_adapter(probe_path=probe_path, socket_path=socket_path, slot_uuid=slot_uuid,
                                     active_capsule=active_capsule, candidates=candidates,
                                     read_timeout_s=SLOT_READ_TIMEOUT_S, submit_timeout_s=ACTIVATION_TIMEOUT_S,
                                     robot_build_identity=self.robot_config.expected_build_identity)
        close_wait_s = capability_check.close_wait_bound_s(read_timeout_s=SLOT_READ_TIMEOUT_S,
                                                           submit_timeout_s=ACTIVATION_TIMEOUT_S,
                                                           binding_timeout_s=self.config.cake.timeout_s)
        check = capability_check.CapabilityCheck(self.data_dir / capability_check.CHECKS_FILE_NAME, capability=adapter,
                                                 candidates=candidates, read_binding=self._current_robot_binding,
                                                 runtime_session=self.runtime_session, close_wait_s=close_wait_s)
        return check, None

    def _build_signature_check(self) -> tuple:
        """(check, None) or (None, the reason there is none). signature_check.unavailable_reason decides, in its order.
        Imported HERE: a runtime with no check never loads it. The verifier is spawned with the module's own bounded
        invoker; the scratch directory it is given lives under this runtime's private data directory."""
        from . import signature_check

        paths = signature_paths(self.config)
        reason = signature_check.unavailable_reason(live=self.mode == LIVE, robot=self.robot_config,
                                                    local=robot_link_is_local(self.config), paths=paths)
        if reason is not None:
            return None, reason
        demo_plan_path, manifest_path, resident_conf_path = paths
        check = signature_check.SignatureCheck(self.data_dir / signature_check.CHECKS_FILE_NAME, demo_plan_path=demo_plan_path,
                                               manifest_path=manifest_path, resident_conf_path=resident_conf_path,
                                               scratch_root=self.data_dir, runtime_session=self.runtime_session)
        return check, None

    def _mission_is_carried(self) -> bool:
        mission_now = self.mission
        return mission_now is not None and mission_now.running

    def _mission_holds_run_slot(self) -> Optional[str]:
        """The supervisor's run slot gate: a reason code while a mission is being carried or a capability slot check is in
        flight, else None.

        ONE RUN SLOT, SHARED HONESTLY. A mission carried on the coordinator's thread and a controller run started by a
        command would otherwise be two executions at once with neither knowing of the other. Every command but stop is
        refused while a mission is carried, and a mission is refused while a run is active (_op_mission_start). A capability
        slot check holds the same slot while its ApplyPlan and read-backs are in flight: it moves nothing, and nothing that
        could move the robot is started under it. The check is read FIRST because it is the narrower fact: a check is only
        ever claimed while no mission is carried (_check_slot_refusal)."""
        check = self.capability_check
        if check is not None and check.in_flight():
            return CAPABILITY_CHECK_HOLDS_RUN_SLOT
        return MISSION_HOLDS_RUN_SLOT if self._mission_is_carried() else None

    def _check_slot_refusal(self) -> Optional[str]:
        """Why a capability slot check may not have the run slot now, or None: a mission is carried, a command is
        unresolved, or a controller run is active. It deliberately does NOT ask _mission_holds_run_slot, which answers
        capability_check_in_flight for the very check that is asking."""
        if self._mission_is_carried():
            return MISSION_HOLDS_RUN_SLOT
        if self.supervisor is not None:
            if self.supervisor.unresolved():
                return "owner_unresolved"
            if self.supervisor.active_run() is not None:
                return "run_active"
        return None

    def _claim_run_slot_for_check(self) -> Optional[str]:
        """The check's claim, run by CapabilityCheck.apply WITH ITS LOCK ALREADY HELD, under the supervisor's submit lock
        (start_if_run_slot_free), which every accept and spawn holds from its arbitration to the moment its run is
        registered. So a command, a mission start and a check cannot all pass their checks and all go on: a submit that
        took the lock first has published its run and this answers run_active; one that takes it after this sees the
        check's lock through the run-slot gate and is refused capability_check_in_flight."""
        if self.supervisor is None:
            return self._check_slot_refusal()
        blocked, refusal = self.supervisor.start_if_run_slot_free(self._check_slot_refusal)
        return blocked if blocked is not None else refusal

    def _stop_run_and_mission(self, door: str, command: Optional[dict] = None) -> dict:
        """THE STOP. The controller run first, through the existing guarded path, and then the mission. Both parts back.

        ORDER. supervisor.stop sends SIGINT to the controller's pid, and only to that pid, BEFORE it logs anything, and it
        never takes the submit lock; asking the mission to stop first would put a journal fsync in front of the signal
        that stops motion. A submitted stop COMMAND goes through supervisor.submit, which journals it under its own id
        and reaches the same supervisor.stop, so a repeat of that id is answered from the journal instead of stopping
        twice.

        WHAT IT NEVER DOES. It does not signal, restart or kill the robot host or the Cake resident, it sends no Cake
        operation (no ApplyPlan, no rollback), and it opens nothing on the robot. The runtime's Cake client stays
        read-only apart from demo_runtime.cake_activation, which nothing here calls. Stopping the host or the resident
        is not how a normal Stop is done.

        Returns {"run": the supervisor's stop answer or None, "command": the stop command's command_result or None,
        "mission": the coordinator's stop answer or None when this runtime has no mission surface}. None of the three
        says that motion ceased: the contract's stop.outcome records are what say that."""
        run_part, command_part, repeat = None, None, False
        if self.supervisor is not None and command is not None:
            # A REPEAT OF A STOP COMMAND STOPS NOTHING NEW. The supervisor answers a known id from its journal and
            # signals nothing; the mission half follows it, so a stop retried after a new mission started cannot
            # stop that new mission on the strength of an old press.
            command_id = command.get("command_id") if isinstance(command, dict) else None
            repeat = isinstance(command_id, str) and self.supervisor.result(command_id) is not None
        # THE MISSION'S CANCEL EVENT FIRST, before the supervisor's stop and before the mission's own stop record: no
        # lock and no fsync (MissionCoordinator.cancel_now). A mission's run is published only while that event is clear
        # (supervisor.start_mission_run), so setting it first means a stop that finds no run to signal still starts none.
        # Not for a repeat and not for a stop command the contract refuses, exactly as the mission's own stop below.
        if self.mission is not None and not repeat and (command is None or not demo_contracts.validate("command", command)):
            self.mission.cancel_now(origin=door)
        if self.supervisor is not None:
            if command is not None:
                command_part = self.supervisor.submit(command)
            else:
                run_part = self.supervisor.stop(origin=STOP_RUN_ORIGIN)
        # A stop COMMAND the contract refused (command_invalid) stopped no run, so it stops no mission either: the two
        # halves of one stop are never allowed to disagree about whether it was a stop at all. It is refused in two
        # shapes and both count: a receipt with status rejected (the id was usable, so the refusal is journaled under
        # it), and a bare {ok false, reason_code} with no status at all (no usable id, so nothing to key a receipt on).
        refused = isinstance(command_part, dict) and (command_part.get("status") == "rejected"
                                                      or command_part.get("ok") is False)
        mission_part = None
        if self.mission is not None and not refused and not repeat:
            mission_part = self.mission.stop(origin=door)
        return {"run": run_part, "command": command_part, "mission": mission_part}

    def _mission_participation(self) -> Optional[dict]:
        """Whether a Cake module took part in the newest mission, by track C's rule (cake_admin.mission_participation)
        over the mission record, named by the configured capability. None when this runtime has no mission surface; a
        record that cannot be read is said to be unread, never guessed."""
        if self.mission is None:
            return None
        from .mission_service import capability_binding

        binding = capability_binding(self.config)
        try:
            view = self.mission.state()
        except Exception:  # noqa: BLE001 -- a read of the record that fails is reported as not read
            return {"value": None, "evidence": "the mission record could not be read, so whether a Cake module took part is not known"}
        return cake_admin.mission_participation(view, module_name=binding[0] if binding else None)

    def _mission_or_refusal(self) -> tuple:
        """(coordinator, refusal). Exactly one of the two is None, every time."""
        from .mission_service import unavailable

        if self.mission is None:
            return None, unavailable(self.mission_unavailable)
        return self.mission, None

    def start(self) -> list:
        """Announce the start and reconcile what a previous daemon left. Touches the journal, the event log and /proc. Nothing else."""
        if self.supervisor is None:
            return []
        self.log.append(make_event("diagnostic.text", {"stream": "log", "level": "info", "text": f"the runtime daemon started in {self.mode} mode", "redacted": False},
                                   source_session=self.runtime_session, reason_code="daemon_started"))
        return self.supervisor.reconcile()

    # ------------------------------------------------------------------ views
    def _table(self) -> dict:
        return self.supervisor.table() if self.supervisor is not None else fold_commands(self.journal.read()[0])

    def _store_sessions(self) -> list:
        return [state["run_id"] for state in self._table().values() if state.get("run_id")]

    def _run_dir(self, run_id) -> Optional[Path]:
        """<data>/history/<run_id>/, for a plain contract id only, resolved and checked to be inside the data directory.
        The ids in the journal are minted here, but the one in a request came from outside and is treated as such."""
        if not isinstance(run_id, str) or demo_contracts.validate("common#/$defs/id", run_id) or run_id in (".", ".."):
            return None
        history = (self.data_dir / HISTORY_DIR_NAME).resolve()
        try:
            resolved = (history / run_id).resolve()
        except OSError:
            return None
        return resolved if history in resolved.parents and resolved.is_dir() else None

    def run_manifest(self, run_id) -> tuple:
        """(manifest, reason_code) for one run: what that run wrote about itself when it started.

        The file is checked AGAINST THE CONTRACT before anything is served or read from it. A client typed against the
        contract cannot use a manifest that does not fit it, and half of one is worse than a named refusal; the
        validator's messages can quote values, so none of them travels and the answer is a reason code. Only a manifest
        that passed is cached, because a run directory exists before its manifest is written and a negative answer must
        not outlive that moment; a finished run's manifest never changes, so the cache is bounded by the runs on disk."""
        if not isinstance(run_id, str):  # checked before the cache: whatever a request carried is not a key to look up
            return None, "unknown_run"
        if run_id in self._manifests:
            return self._manifests[run_id], None
        directory = self._run_dir(run_id)
        if directory is None:
            return None, "unknown_run"
        try:
            document = json.loads((directory / MANIFEST_FILE_NAME).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None, "manifest_not_recorded"
        if not isinstance(document, dict) or demo_contracts.validate("manifest", document):
            return None, "manifest_invalid"
        self._manifests[run_id] = document
        return document, None

    def recorded_data_mode(self, run_id) -> Optional[str]:
        """What a run RECORDED about its own data mode, for the history list. None when it recorded none, which is what
        a history entry then says: absent, never the mode this runtime happens to be in now."""
        manifest = self.run_manifest(run_id)[0]
        return manifest.get("data_mode") if manifest is not None else None

    def link(self) -> dict:
        """The snapshot's link. With a source attached it is the TAP's own state, not the age of the last thing anybody
        published: a slot republished by a heartbeat must never read as a live robot."""
        if self.sources is not None:
            return self.sources.link()
        return self.hub.link() if self.mode != REPLAY else dict(UNKNOWN_LINK)

    def snapshot(self) -> dict:
        data_mode = {LIVE: "live", SYNTHETIC: "synthetic", REPLAY: "replay"}[self.mode]
        if self.supervisor is None:
            owner, active, block, last_exit = {"kind": "free", "run_id": None, "lease": None}, None, ("replay_only", None), None
        else:
            owner, active, block, last_exit = self.supervisor.owner(), self.supervisor.active_run(), self.supervisor.block(), self.supervisor.last_exit()
        # ONE READ OF THE CACHED PROBE for this whole snapshot, so the ladder and the capability sentence beside it
        # cannot describe two different moments. It changes only that sentence: the capability is disabled either way.
        cake_probe = self.cake.get()
        cake_inventory = cake_probe.get("inventory")
        cake_connected = isinstance(cake_inventory, dict) and bool(cake_inventory.get("reachable"))
        # The robot session gate, read from the SAME cached reading. A submit asks again, fresh (_fresh_motion_refusal).
        motion_block = self._motion_refusal(cake_probe) if self.supervisor is not None else None
        return read_models.build_snapshot(
            runtime_session=self.runtime_session, seq_cursor=self.log.seq_cursor, retained_from_seq=self.log.retained_from_seq, data_mode=data_mode,
            link=self.link(),
            owner=owner, active_run=active, last_outcome=last_outcome(self._table(), data_mode, self.recorded_data_mode),
            capability_list=read_models.capabilities(block_code=block[0] if block else None, block_detail=block[1] if block else None, run_active=active is not None,
                                                     replay_only=self.supervisor is None, cake_connected=cake_connected,
                                                     # A MISSION, and only a mission: it is what makes Stop mean
                                                     # something with no run. A check in flight stops on its own.
                                                     mission_carried=self._mission_is_carried(),
                                                     motion_block=motion_block),
            component_list=read_models.components(stats=self.stats, run_active=active is not None, last_exit=last_exit, replay_only=self.supervisor is None, runtime_version=RUNTIME_VERSION),
            cake=read_models.cake_view(cake_probe, participation=self._mission_participation()), evidence=self.stats.evidence(),
            timings=self.stats.timings(), run_evidence=self.stats.run_evidence(),
            # The newest frame this session retained, by reference and never as bytes. A console that connected or
            # reloaded after the capture folded no observation.captured record off the stream, so without this there is
            # nothing anywhere in its state that names a frame it could fetch.
            latest_retained_observation=self.stats.latest_retained_observation(),
            # The newest measured.state record of this session, for the same reason and read the same way. It is one
            # record a controller wrote when it read the robot, never a telemetry feed: an observe that moves nothing
            # still records the whole pose, and without this a tab that reloaded afterwards reports that nothing is
            # measuring while that reading sits in the log.
            latest_measured_state=self.stats.latest_measured_state())

    def status(self) -> dict:
        records = self.journal.read()[0]
        counters = journal_counters(records)
        with self._clients_lock:
            counters["client_reconnects"] = self.client_reconnects
        counters["subscribers_dropped"] = self.log.dropped_subscribers
        return {"ok": True, "runtime_session": self.runtime_session, "mode": self.mode, "supervisor": self.supervisor is not None,
                "hardware_commands_enabled": bool(self.supervisor and self.supervisor.hardware_enabled), "counters": counters,
                "stop_mechanism": (self.supervisor.pidfd_mechanism or "pid_verified") if self.supervisor else None,
                "unresolved": self.supervisor.unresolved() if self.supervisor else [], "event_subscribers": self.log.subscriber_count(),
                "unresolved_detail": self.supervisor.unresolved_detail() if self.supervisor else None,
                "sources": self.sources.report() if self.sources is not None else None, "link": self.link(),
                "forwards": self.forwards_report(), "lease": self.lease_view(), "execution": self.execution_view(),
                # The robot's admin-socket forward, which is restarted and so is reported apart from the three above.
                "robot_forward": self.robot.report() if self.robot is not None else None,
                "robot_readiness": self.supervisor.readiness_report() if self.supervisor is not None else None,
                "journal_damaged_lines": self.supervisor.journal_damaged if self.supervisor else 0, "event_log_damaged_lines": self.log.damaged_lines}

    def forwards_report(self) -> Optional[dict]:
        """The state of each forward, LOOKED AT first. check() is one non-blocking poll per child, so asking for the
        status is what notices a forward that has died; there is no timer and nothing restarts one, because an ssh that
        keeps dying is a fact the operator has to see rather than a loop to hide it in."""
        if self.forwards is None:
            return None
        self.forwards.check()
        return self.forwards.report()

    def lease_view(self) -> Optional[dict]:
        """What a client is told about the host command lease, or None when this runtime has nothing to say about one."""
        if self.supervisor is None:
            return None
        report = self.supervisor.lease_report()
        return read_models.lease_view(report) if (report["attached"] or report["required"]) else None

    def execution_view(self) -> Optional[dict]:
        """Which backend this runtime runs commands through and what owns the robot while it does, or None in replay,
        where there is no command path at all and so nothing owns anything."""
        if self.supervisor is None:
            return None
        return read_models.execution_view(self.supervisor.execution_report())

    # ------------------------------------------------------------------ the socket's handlers
    def handle(self, op: str, request: dict, connection) -> dict:
        try:
            handler = getattr(self, f"_op_{op}", None) if op.isidentifier() else None
            answer = handler(request, connection) if handler else {"ok": False, "reason_code": "unknown_op"}
        except Exception as error:  # noqa: BLE001 -- an internal failure is reported by class only: its text can carry a path
            answer = {"ok": False, "reason_code": "internal_error", "error_class": type(error).__name__}
        return self.sanitizer.scrub(answer)[0]

    def stream(self, op: str, request: dict, connection, closing) -> None:
        scrub = lambda record: self.sanitizer.scrub(record)[0]  # noqa: E731 -- the same last line the unary answers get
        if op == "events":
            since = request.get("since", 0)
            if isinstance(since, bool) or not isinstance(since, int) or since < 0:
                connection.send({"ok": False, "reason_code": "bad_request"})
                return
            stream_events(self.log, since, connection, closing, sanitize=scrub)
        elif op == "telemetry":
            stream_telemetry(self.hub, connection, closing, self.unavailable_slots(), sanitize=scrub)

    def unavailable_slots(self) -> dict:
        """What a telemetry reader is told when a stream never published anything: the reason THIS daemon has, not a
        generic one. With a source supervisor attached the sources answer for themselves."""
        if self.sources is None:
            return {"tap": dict(read_models.UNAVAILABLE_TAP),
                    sources.preview_topic(sources.DEFAULT_ROLE): dict(read_models.UNAVAILABLE_PREVIEW)}
        # One slot per camera, each naming its own role: a reader that has never seen a frame is told WHICH camera it is
        # waiting on, instead of one generic answer that could be about either.
        slots = {"tap": self.sources.tap.slot()}
        for role in self.sources.roles():
            slots[sources.preview_topic(role)] = self.sources.latest_preview(role=role)
        return slots

    def _folding_cake_probe(self, probe):
        """The one Cake probe, with retention folded into it. It returns what the probe returned, UNCHANGED.

        Wrapping rather than editing the probe keeps the snapshot's ladder reading exactly the value it read before,
        and keeps a test that injects its own `cake_probe` working: the wrapper folds whatever that probe reports and
        hands the same object on.
        """

        def folding() -> dict:
            reading = probe()
            self.cake_retention.fold(reading.get("inventory") if isinstance(reading, dict) else None)
            return reading

        return folding

    def _probe_cake_with_resident(self) -> dict:
        """The local probes, and a REAL read-only inventory when the configuration names a resident.

        Absent configuration means exactly what it meant before: the local probes alone, and a runtime that is not
        connected to anything. Nothing is searched for.
        """
        probe_path, socket_path = cake_admin.configured_paths(self.config)
        if self.robot_config is not None:
            # THE ROBOT'S RESIDENT, through the forward, and nothing on this laptop in its place: probe_robot consults
            # no release directory, no process table and no runtime-directory socket.
            # The answer has to come from a resident reporting the robot's pinned build identity; any other build is
            # refused. The pin says which SOFTWARE; which deployment answers is this runtime's connection to the robot
            # and the session its resident reports now.
            return robot_link.probe_robot(self.robot, probe_path, socket_path, timeout_s=self.config.cake.timeout_s,
                                          expected_build_identity=self.robot_config.expected_build_identity)
        if probe_path is None or socket_path is None:
            return read_models.probe_cake(self.source_root)
        timeout = self.config.cake.timeout_s if getattr(self.config, "cake", None) else cake_admin.DEFAULT_TIMEOUT_S
        inventory = cake_admin.read_inventory(probe_path, socket_path, timeout_s=timeout)
        return read_models.probe_cake(self.source_root, inventory=inventory)

    def _op_hello(self, request, connection) -> dict:
        if request.get("contract_version") != CONTRACT_VERSION:
            connection.hello_ok = False
            return {"ok": False, "reason_code": "contract_incompatible", "contract_version": CONTRACT_VERSION}
        connection.hello_ok = True
        session = request.get("client_session")
        if isinstance(session, str) and not demo_contracts.validate("common#/$defs/id", session):
            with self._clients_lock:
                if session in self.client_sessions:
                    self.client_reconnects += 1
                self.client_sessions.add(session)
        return {"ok": True, "contract_version": CONTRACT_VERSION, "runtime_session": self.runtime_session}

    def _op_snapshot(self, request, connection) -> dict:
        """The contract snapshot, plus the unresolved command when there is one and the lease when there is one to speak of.

        BOTH ARE INSIDE THE CONTRACT OBJECT NOW. They were carried beside it while snapshot.schema.json, which refuses
        unknown keys, had no room for them; it declares both as optional properties today, so the whole answer validates
        and no client has to be told to strip anything before it checks. They stay CONDITIONAL rather than null: a key
        that is absent is a runtime saying it has nothing unresolved and nothing to say about a lease, which is what a
        client already reads. owner.lease is in the contract too and carries the same three numbers, but only while the
        lease is held; this key is what says why one is not. timings and run_evidence come from the snapshot assembler
        and are always present. 'execution' says which of the two backends this runtime runs commands through and what
        actually owns the robot under it, so a console never has to infer either from the absence of a lease.
        latest_retained_observation comes from the assembler too and names the newest frame this session retained, so a
        tab that connected after the capture has something to fetch instead of reporting that nothing was captured.
        latest_measured_state closes the same hole for the pose a controller read, and it is not a tap: a runtime with no
        telemetry tap configured still records one of these per command, and a console that reads the absent tap as the
        whole answer prints "nothing is measuring" over a measurement this runtime is holding.
        daemon.ADDITIVE_SNAPSHOT_KEYS names all seven, and a contracts test keeps that tuple equal to the schema's
        optional set."""
        snapshot = self.snapshot()
        detail = self.supervisor.unresolved_detail() if self.supervisor is not None else None
        if detail is not None:
            snapshot["unresolved_command"] = detail
        lease = self.lease_view()
        if lease is not None:
            snapshot["lease"] = lease
        execution = self.execution_view()
        if execution is not None:
            snapshot["execution"] = execution
        return snapshot

    def _op_status(self, request, connection) -> dict:
        return self.status()

    def _op_cake_status(self, request, connection) -> dict:
        """The Cake resident status, versioned SEPARATELY from contract v1 and carried by no snapshot.

        It is a projection of the SAME cached probe result the snapshot's cake ladder is built from: `self.cake.get()`
        is one value, read on one schedule, by one poller. Asking for this starts no child process that a snapshot
        would not have started anyway, and nothing in the command path reads it.

        READ-ONLY IN THE STRICT SENSE. It reaches the resident through `cake_admin`, whose operation allowlist holds
        four questions and no mutation (`cake_admin.READ_OPERATIONS`), and contract v1 has no command that could reach
        one. Answering this submits nothing, to the resident or to the robot. An activation is a different surface
        entirely, submitted by `cake_activation` and never reached by reading this.
        """
        probe = dict(self.cake.get())  # a copy: the cached value is shared with the snapshot and is never mutated here
        probe["cake_configured"] = cake_admin.configured_paths(self.config) != (None, None)
        probe["cake_retained"] = self.cake_retention.last_good()
        probe["cake_session_changed"] = self.cake_retention.session_changed()
        answer = read_models.cake_status_view(probe, now_mono_ns=time.monotonic_ns(), participation=self._mission_participation())
        # CHECKED AGAINST ITS OWN SCHEMA BEFORE IT LEAVES. The surface is small and a console types against it; a field
        # this runtime grew wrong is caught here rather than drawn on a screen as if a resident had said it. The answer
        # carries no `ok` key, exactly like a snapshot: a refusal is the runtime's own {"ok": false, ...} shape, which
        # is not a cake_status object and does not pretend to be one.
        if demo_contracts.validate_cake_status(answer):
            return {"ok": False, "reason_code": "cake_status_invalid"}
        return answer

    # ---- the robot's resident, and the permission to move that rests on it ---------------------------------------

    def _motion_refusal(self, cake_probe) -> Optional[tuple]:
        """(reason_code, sentence) when a motion command would be refused by the robot gate, from a reading already
        taken, or None. None also when no robot is configured: then this gate does not exist."""
        if self.motion_permission is None:
            return None
        inventory = cake_probe.get("inventory") if isinstance(cake_probe, dict) else None
        return self.motion_permission.refusal(robot_link.binding_of(inventory))

    def _current_robot_binding(self) -> dict:
        """What motion permission binds, read NOW through the forward and cake_admin's read-only allowlist: the robot
        resident's session (after its build identity matched the pinned build; the connection and this session are what
        say which deployment answered) and the supervisor.lekiwi HOST INSTANCE
        (slot instance handle, spawn sequence, child pid). It notes that session in the retention and retains nothing
        else: this is not an inventory."""
        if self.robot is None:
            return robot_link.binding_of(cake_admin.unavailable("robot_link_not_started"))
        if not self.robot.connected():
            return robot_link.binding_of(cake_admin.unavailable(robot_link.UNREACHABLE))
        probe_path, socket_path = cake_admin.configured_paths(self.config)
        binding = robot_link.read_binding(probe_path, socket_path, timeout_s=self.config.cake.timeout_s,
                                          expected_build_identity=self.robot_config.expected_build_identity)
        if binding["session_uuid"] is not None:
            self.cake_retention.note_session(binding["session_uuid"])  # a restart seen here is as sticky as one seen by the poll
        return binding

    def _fresh_motion_admission(self) -> tuple:
        """The supervisor's motion gate: (refusal, admitted), exactly one None. FRESH: the robot's identity, session and
        host instance are read again for EVERY call, so the supervisor's call immediately before it admits a motion
        command rests on nothing older than that read, and `admitted` is the identity it journals the command under."""
        if self.motion_permission is None:
            return None, None
        return self.motion_permission.admission(self._current_robot_binding())

    def _fresh_motion_refusal(self) -> Optional[tuple]:
        return self._fresh_motion_admission()[0]

    def _op_robot_status(self, request, connection) -> dict:
        """The robot's resident, versioned SEPARATELY from contract v1 and from cake_status. A READ: a projection of the
        SAME cached probe the snapshot and cake_status read, the forward's own report, the readiness reading and the
        persisted confirmation. It starts no child process a snapshot would not have started."""
        probe = self.cake.get()
        watch = self._host_watch()
        reading = watch.reading() if watch is not None else None
        pids_of = getattr(watch, "host_pids", None)
        answer = robot_link.robot_status_view(
            config=self.config, probe=probe, retained=self.cake_retention.last_good(),
            session_changed=self.cake_retention.session_changed(),
            forward_report=self.robot.report() if self.robot is not None else None,
            readiness=reading, host_pids=pids_of() if callable(pids_of) else None, permission=self.motion_permission)
        if demo_contracts.validate_robot_status(answer):
            return {"ok": False, "reason_code": "robot_status_invalid"}
        return answer

    def _op_robot_confirm_session(self, request, connection) -> dict:
        """The operator confirms the robot resident session AND the supervisor HOST INSTANCE they SAW.

        The request is exactly {"session_uuid": "..", "host_instance": {"slot_instance": "..", "spawn_sequence": N,
        "pid": N}}. Refused unless the answering resident reports the pinned build identity, a host instance RUNS
        under the supervisor slot (never pid 0, never a held or DEGRADED host) and EACH component equals what the robot
        reports now, read fresh. The answer to a refusal does NOT carry the current values: a client that could read
        them here could confirm without an operator ever having looked, so they have to be read again from
        robot_status. The grant lives in this runtime only."""
        if self.supervisor is None:
            return dict(REPLAY_REFUSAL)
        if self.motion_permission is None:
            return {"ok": False, "reason_code": "robot_not_configured"}
        if set(request) - {"op"} != {"session_uuid", "host_instance"}:
            return {"ok": False, "reason_code": "bad_request"}
        session = request.get("session_uuid")
        if not isinstance(session, str) or robot_link.session_of({"reachable": True, "identity": {"session_uuid": session}}) is None:
            return {"ok": False, "reason_code": "bad_request"}
        instance = robot_link.host_instance_of(request.get("host_instance"))
        if instance is None:
            return {"ok": False, "reason_code": "bad_request"}
        current = self._current_robot_binding()
        if current["reason_code"] in robot_link.IDENTITY_REFUSALS:
            self.motion_permission.refusal(current)  # a resident that is not the pinned build revokes a standing confirmation
            return {"ok": False, "reason_code": current["reason_code"]}
        if current["session_uuid"] is None:
            return {"ok": False, "reason_code": robot_link.UNREACHABLE, "detail": current["reason_code"]}
        if session != current["session_uuid"]:
            return {"ok": False, "reason_code": "robot_session_mismatch"}
        if current["host_running"] is False:
            return {"ok": False, "reason_code": robot_link.HOST_NOT_RUNNING, "detail": current["reason_code"]}
        if current["host_instance"] is None:
            return {"ok": False, "reason_code": robot_link.HOST_UNREADABLE, "detail": current["reason_code"]}
        for name, code in (("slot_instance", "robot_host_slot_instance_mismatch"),
                           ("spawn_sequence", "robot_host_spawn_sequence_mismatch"), ("pid", "robot_host_pid_mismatch")):
            if instance[name] != current["host_instance"][name]:
                return {"ok": False, "reason_code": code}
        record = self.motion_permission.confirm(session, instance, build_identity=current["build_identity"],
                                                runtime_session=self.runtime_session)
        self.cake.invalidate()  # the next read shows the confirmation against a current reading
        self.log.append(make_event("diagnostic.text", {"stream": "log", "level": "info", "redacted": False,
                                                       "text": "the operator confirmed the connected robot's resident session and supervisor host instance; "
                                                               "motion is no longer refused on that account in this runtime"},
                                   source_session=self.runtime_session, reason_code="robot_session_confirmed"))
        refused = self._fresh_motion_refusal()
        return {"ok": True, "confirmed_session_uuid": record["session_uuid"], "confirmed_host_instance": record["host_instance"],
                "confirmed_wall_utc": record["confirmed_wall_utc"], "motion_permitted": refused is None,
                "reason_code": refused[0] if refused else None}

    # ---- the capability slot check: one prepared Plan applied and read back, WITHOUT MOTION -------------------------
    #
    # Its own surface, versioned by capability_check.CAPABILITY_CHECK_VERSION; not contract v1 and not the mission. A
    # browser supplies a candidate KEY, the DIGEST it was shown for that key and the resident SESSION it was shown, and
    # nothing else: no bytes, no path, no operation. See demo_runtime.capability_check for what it never does.

    def _capability_check_unavailable(self) -> dict:
        from .capability_check import UNAVAILABLE

        return {"ok": False, "reason_code": UNAVAILABLE, "detail": self.capability_check_unavailable}

    def _op_capability_check_state(self, request, connection) -> dict:
        """What a check would act on, and every check on the record. A READ of the SAME cached probe the robot surface
        reads: it starts no child process, submits nothing and claims nothing.

        `current` is the robot's resident as that cached reading reports it (its session, whether a HEALTHY host runs under
        the supervisor slot as robot_link answers it, whether the host is HELD by the check's own rule, capability_check.
        host_held, the capability slot read from the same listing, and the reading's age). `off_reason` is the first
        reason an apply would be refused for right now, judged from that reading and this runtime's own state; the apply
        itself reads everything again, fresh, and decides on that."""
        from . import capability_check, cake_activation

        check = self.capability_check
        if check is None:
            return self._capability_check_unavailable()
        probe = self.cake.get()
        inventory = probe.get("inventory") if isinstance(probe, dict) else None
        current = inventory if isinstance(inventory, dict) and inventory.get("reachable") else None
        binding = robot_link.binding_of(current if current is not None else inventory)
        _name, slot_uuid, active_capsule = self._capability_binding()
        detail = current.get("robot_detail") if current is not None else None
        slot = (cake_activation.slot_reading(detail.get("slots_text", ""), slot_uuid=slot_uuid, active_capsule=active_capsule)
                if isinstance(detail, dict) else None)
        stamp = binding.get("observed_mono_ns")
        age_ms = max(0.0, round((time.monotonic_ns() - stamp) / 1e6, 3)) if isinstance(stamp, int) else None
        off_reason = None
        if check.in_flight() or check.pending_operation is not None:
            off_reason = capability_check.IN_FLIGHT
        if off_reason is None:
            off_reason = self._check_slot_refusal()
        if off_reason is None:
            refused = capability_check.binding_refusal(binding, binding.get("session_uuid") or "")
            off_reason = refused[0] if refused is not None else None
        if off_reason is None and (slot is None or slot.get("session_uuid") != binding.get("session_uuid")):
            off_reason = capability_check.SLOT_SESSION_MISMATCH
        checks, dropped, damaged = check.checks()
        return {"ok": True, "capability_check_version": capability_check.CAPABILITY_CHECK_VERSION, "available": True,
                "in_flight": check.in_flight(), "pending_operation_uuid": check.pending_operation,
                "off_reason": off_reason, "candidates": check.candidates.view(),
                "current": {"session_uuid": binding.get("session_uuid"), "build_identity": binding.get("build_identity"),
                            "host_running": binding.get("host_running"), "host_held": capability_check.host_held(binding),
                            "host_reason_code": binding.get("reason_code"),
                            "slot": slot, "observed_wall_utc": binding.get("observed_wall_utc"), "age_ms": age_ms},
                "checks": checks, "checks_dropped": dropped, "damaged_lines": damaged}

    def _capability_binding(self) -> tuple:
        from .mission_service import capability_binding

        return capability_binding(self.config) or (None, None, None)

    def _op_capability_check_apply(self, request, connection) -> dict:
        """Apply ONE prepared candidate to the robot's capability slot and read it back. NO MOTION, and never a mission.

        The request is exactly {"candidate_key", "payload_digest", "session_uuid"}. Refused, with nothing submitted, in
        this order: a runtime with no check (not LIVE, or not configured for one); a body that is not exactly that; a
        mission carried, an unresolved command or an active run (asked here and again under the claim); then, inside the
        check, an unknown candidate or a digest that is not its digest, a check already in flight, a runtime that is
        closing, the claim, a PENDING operation, a fresh read of the robot that is not the pinned build, not that session
        or shows a host that is not HELD (capability_check.host_held: a host process under the supervisor slot, HEALTHY or
        not), and a capability slot read from another session. See CapabilityCheck.apply for what follows."""
        from . import capability_check

        if self.supervisor is None:
            return dict(REPLAY_REFUSAL)
        check = self.capability_check
        if check is None:
            return self._capability_check_unavailable()
        if set(request) - {"op"} != {"candidate_key", "payload_digest", "session_uuid"}:
            return {"ok": False, "reason_code": "bad_request"}
        key, digest, session = request.get("candidate_key"), request.get("payload_digest"), request.get("session_uuid")
        if (not isinstance(key, str) or not 0 < len(key) <= 64 or not capability_check.digest_shape(digest)
                or not capability_check.session_shape(session)):
            return {"ok": False, "reason_code": "bad_request"}
        refusal = self._check_slot_refusal()
        if refusal is not None:
            return {"ok": False, "reason_code": refusal}
        answer = check.apply(key, digest, session, claim=self._claim_run_slot_for_check)
        if answer.get("ok"):
            # The next read shows the slot as it is now, not as the cached reading from before the operation had it.
            self.cake.invalidate()
            # The verdict AND whether the slot changed, side by side: a completed Plan that left the slot as it was is not
            # an observed change, and the log line says which of the two it was, not only the verdict. It says what this
            # runtime did and did not send, never a physical fact it does not observe.
            slot_words = {True: "the slot changed", False: "the slot did not change"}.get(
                answer.get("slot_changed"), "whether the slot changed is not known")
            self.log.append(make_event("diagnostic.text", {"stream": "log", "level": "info", "redacted": False,
                                                           "text": f"a capability slot check applied a prepared candidate and "
                                                                   f"recorded {answer['activation_state']}; {slot_words}; "
                                                                   f"no motion command was sent and no mission or run was started"},
                                       source_session=self.runtime_session, reason_code="capability_check_answered"))
        return answer

    # ---- the signature policy check: the release's own verifier run on the robot, WITHOUT MOTION and without a slot ----
    #
    # Its own surface, versioned by signature_check.SIGNATURE_CHECK_VERSION; not contract v1, not the mission and not
    # the slot check. A browser supplies NOTHING: no capsule, no key, no path, no policy. The verifier, the store, the
    # trusted keys and the policy are the deployed release's own, named by the private configuration.

    def _signature_check_unavailable(self) -> dict:
        from .signature_check import UNAVAILABLE

        return {"ok": False, "reason_code": UNAVAILABLE, "detail": self.signature_check_unavailable}

    def _signature_check_refusal(self) -> Optional[str]:
        """Why a signature check may not run now, or None: the slot check's own rule (a mission carried, an unresolved
        command, an active run), and a capability slot check in flight, whose ApplyPlan is moving the store this
        verifier would read."""
        refusal = self._check_slot_refusal()
        if refusal is not None:
            return refusal
        check = self.capability_check
        if check is not None and check.in_flight():
            return CAPABILITY_CHECK_HOLDS_RUN_SLOT
        return None

    def _op_capability_signature_state(self, request, connection) -> dict:
        """The check's READ: whether one is in flight, the first reason a run would be refused now, the policy it
        verifies under and every run on the record. It starts no child process."""
        from . import signature_check

        check = self.signature_check
        if check is None:
            return self._signature_check_unavailable()
        off_reason = signature_check.IN_FLIGHT if check.in_flight() else self._signature_check_refusal()
        checks, dropped, damaged = check.checks()
        return {"ok": True, "signature_check_version": signature_check.SIGNATURE_CHECK_VERSION, "available": True,
                "in_flight": check.in_flight(), "off_reason": off_reason, "policy": signature_check.POLICY,
                "checks": checks, "checks_dropped": dropped, "damaged_lines": damaged}

    def _op_capability_signature_check(self, request, connection) -> dict:
        """Run the release verifier over every deployed capsule and record the verdict table. NO MOTION, no slot.

        The request is exactly {"op"}: there is no field for a capsule, a key, a path or a policy to arrive in. Refused,
        with nothing run, in this order: a runtime with no check (not LIVE, not on the robot, or not configured for one);
        a body beyond the op; a mission carried, an unresolved command, an active run or a slot check in flight (asked
        here and again under the check's own lock); then, inside the check, a run already in flight, a runtime that is
        closing, and a release whose verifier, manifest or resident.conf cannot be read as one under `require`."""
        if self.supervisor is None:
            return dict(REPLAY_REFUSAL)
        check = self.signature_check
        if check is None:
            return self._signature_check_unavailable()
        if set(request) - {"op"}:
            return {"ok": False, "reason_code": "bad_request"}
        refusal = self._signature_check_refusal()
        if refusal is not None:
            return {"ok": False, "reason_code": refusal}
        answer = check.run(claim=self._signature_check_refusal)
        if answer.get("ok"):
            words = ("every row that ran read as expected" if answer.get("as_expected") else "a row did not read as expected")
            skipped = answer.get("rows_skipped") or 0
            self.log.append(make_event("diagnostic.text", {"stream": "log", "level": "info", "redacted": False,
                                                           "text": f"a signature policy check ran the release verifier under require over "
                                                                   f"{answer.get('capsules')} deployed capsule(s): {words}"
                                                                   f"{f', {skipped} row(s) skipped' if skipped else ''}; "
                                                                   f"no motion command was sent, no slot was touched and no mission or run was started"},
                                       source_session=self.runtime_session, reason_code="signature_check_answered"))
        return answer

    # ---- the mission, which is its own surface and is not contract v1 -------------------------------------------
    #
    # A mission is not a command and a target is not a run, so none of these widens the command enum, the event kinds
    # or the snapshot. The records they read and write carry demo_runtime.mission.MISSION_RECORD_VERSION, which moves
    # on its own; a reader that does not know that version is told rather than served a guess.
    #
    # WHAT A BROWSER MAY SUPPLY, and it is the whole list: a candidate KEY, and optionally the targets of a mission it
    # is starting. It never supplies Plan bytes, a file path, a socket path or an admin operation. The bytes come from
    # candidates this runtime prepared at start-up and the paths come from the private configuration.

    def _op_mission_state(self, request, connection) -> dict:
        """What is on the record, folded. A READ: it starts nothing, submits nothing and resumes nothing.

        A BROWSER RELOAD READS THIS, which is the reason it may not be anything else. It answers the mission view or a
        refusal, never a half-built object; a runtime that has recorded no mission says so by reason code rather than
        by an empty view that could be mistaken for a mission with nothing in it.
        """
        coordinator, refusal = self._mission_or_refusal()
        if refusal is not None:
            return refusal
        view = coordinator.state()
        return view if view is not None else {"ok": False, "reason_code": "no_mission_recorded"}

    def _op_mission_candidates(self, request, connection) -> dict:
        """The prepared candidates, by KEY and DIGEST. Never their bytes and never their path: a path names a home
        directory, and the bytes are what a caller must not be able to supply."""
        coordinator, refusal = self._mission_or_refusal()
        if refusal is not None:
            return refusal
        return {"ok": True, "candidates": self.mission_candidates.view()}

    def _op_mission_start(self, request, connection) -> dict:
        """Write the mission down and carry it. Returns as soon as the first record is on disk.

        The targets are validated HERE, at the boundary, because they end up in an append-only record that is read
        back and shown. A caller that names none gets this runtime's own mission: one auto target (mission_service.DEFAULT_TARGETS).
        """
        from .mission_service import TargetsRefused, normalize_targets

        coordinator, refusal = self._mission_or_refusal()
        if refusal is not None:
            return refusal
        try:
            targets = normalize_targets(request.get("targets"))
        except TargetsRefused as refused:
            return {"ok": False, "reason_code": "bad_request", "detail": str(refused)}
        if self.supervisor is None:
            return coordinator.start(targets)
        if coordinator.performs_motion:
            # A MISSION THAT CAN MOVE THE ROBOT IS ADMITTED BEFORE IT IS WRITTEN DOWN, by the same two questions every
            # motion command is asked: the robot's identity gate, FRESH, and block() (without the run-slot gate, which is
            # the one the mission itself will hold). Each of its runs is asked both again, fresh, at its own admission
            # (supervisor.start_mission_run); this only keeps a mission that could not start its first run from being
            # recorded as started.
            refusal = self._fresh_motion_refusal()
            if refusal is not None:
                return {"ok": False, "reason_code": refusal[0]}
            blocked = self.supervisor.block("cycle", run_slot_gate=False)
            if blocked is not None:
                return {"ok": False, "reason_code": blocked[0]}
        # REFUSED WHILE A CONTROLLER RUN HOLDS THE RUN SLOT, checked and started under the supervisor's submit lock so a
        # command admitted at the same moment cannot slip in between the check and the start. A capability slot check
        # in flight refuses it too, under the SAME lock the check claims the slot under (_claim_run_slot_for_check).
        def start_unless_checking() -> dict:
            check = self.capability_check
            if check is not None and check.in_flight():
                return {"ok": False, "reason_code": CAPABILITY_CHECK_HOLDS_RUN_SLOT}
            return coordinator.start(targets)

        blocked, started = self.supervisor.start_if_run_slot_free(start_unless_checking)
        return {"ok": False, "reason_code": blocked} if blocked is not None else started

    def _op_mission_stop(self, request, connection) -> dict:
        """THE STOP, through the mission's door: the controller run first, then the mission (_stop_run_and_mission).

        Answered immediately, INCLUDING while a target is waiting on a capability. The coordinator sets that mission's
        event, which every place it can block checks, so a stop is never queued behind a poll or behind an activation
        that is in flight on another thread. The answer is the mission's own, with the run's stop beside it under
        `run_stop`; a runtime with no mission surface still stops the run and says both things: that there is no
        mission surface, and what became of the run's stop."""
        if self.supervisor is None and self.mission is None:
            return self._mission_or_refusal()[1]
        parts = self._stop_run_and_mission(STOP_BY_MISSION_STOP)
        _coordinator, refusal = self._mission_or_refusal()
        answer = dict(refusal) if refusal is not None else dict(parts["mission"])
        answer["run_stop"] = parts["run"]
        return answer

    def _op_mission_activate(self, request, connection) -> dict:
        """Submit ONE prepared candidate, named by key, for the mission that is waiting.

        THE KEY IS ALL A CALLER GIVES. The bytes were read at start-up from the private configuration, hashed, and are
        bound to a fresh operation uuid before the submit is attempted; a key that was not prepared is refused by name
        and nothing is submitted. `status ok` is not success and is not reported as one: the transaction phase and the
        slot read back are separate answers and this returns both.
        """
        coordinator, refusal = self._mission_or_refusal()
        if refusal is not None:
            return refusal
        candidate_key = request.get("candidate_key")
        if not isinstance(candidate_key, str) or not candidate_key:
            return {"ok": False, "reason_code": "bad_request"}
        return coordinator.submit_activation(candidate_key)

    def _op_mission_retry(self, request, connection) -> dict:
        """Retry the attempt a mission HOLDS on (MissionCoordinator.request_retry): the carrying thread parks the arm with
        the unchanged `--do exit` and starts a fresh attempt. Answered at once; the park and the attempt are the mission's.

        WHAT A CALLER MAY SUPPLY: nothing but, optionally, the `mission_id` of the hold it saw, so a press meant for one
        mission can never retry another. It names no target, no attempt and no action: the hold on the record decides all
        three, and a Retry is accepted only while that hold offers one (nothing held, the base home, budget and a park).

        GUARDED LIKE EVERY OP THAT CAN START MOTION: a socket connection must have said hello first (rpc.MUTATING_OPS is
        the transport's own list; this op checks the same flag itself, so it is never reachable without one), and a
        mission that performs motion is asked the robot's identity gate, FRESH, before the request is taken. Each run the
        retry starts is admitted again by the supervisor, exactly as the mission's first one was.

        ASKED block() BEFORE IT IS WRITTEN DOWN, as a mission's admission is: for the park's `--do exit` and for the fresh
        cycle, without the run-slot gate (the slot is the mission's own, held through the hold). A press block() would
        refuse is refused by that code with nothing written, so the hold stays and a later press can still retry; taken
        instead, the park would be refused at its own admission and the mission would end failed."""
        if connection is not None and not getattr(connection, "hello_ok", False):
            return {"ok": False, "reason_code": "hello_required"}
        coordinator, refusal = self._mission_or_refusal()
        if refusal is not None:
            return refusal
        if set(request) - {"op", "mission_id"}:
            return {"ok": False, "reason_code": "bad_request"}
        mission_id = request.get("mission_id")
        if mission_id is not None and (not isinstance(mission_id, str) or not mission_id):
            return {"ok": False, "reason_code": "bad_request"}
        if coordinator.performs_motion:
            refusal = self._fresh_motion_refusal()
            if refusal is not None:
                return {"ok": False, "reason_code": refusal[0]}
            if self.supervisor is not None:
                for action in ("exit", "cycle"):
                    blocked = self.supervisor.block(action, run_slot_gate=False)
                    if blocked is not None:
                        return {"ok": False, "reason_code": blocked[0]}
        return coordinator.request_retry(mission_id=mission_id)

    def _op_submit(self, request, connection) -> dict:
        """A contract command. A STOP command is THE STOP (_stop_run_and_mission), so the console's journaled Stop ends a
        mission as well as a run.

        THE ANSWER STAYS A CONTRACT command_result. With a mission surface, a stop command's answer carries the
        mission's part under one more key, `mission`, beside the command_result's own fields; console_api takes it off
        before the command_result goes anywhere, so no client of contract v1 ever reads a widened receipt. A runtime
        with no mission surface answers exactly the command_result it always did."""
        if self.supervisor is None:
            return dict(REPLAY_REFUSAL)
        command = request.get("command")
        if isinstance(command, dict) and command.get("type") == "stop":
            parts = self._stop_run_and_mission(STOP_BY_COMMAND, command=command)
            answer = parts["command"]
            if self.mission is not None and isinstance(answer, dict):
                answer = {**answer, "mission": parts["mission"]}
            return answer
        return self.supervisor.submit(command)

    def _op_stop(self, request, connection) -> dict:
        """THE STOP, through the plain door (no command id): the controller run first, then the mission. `stop` is the
        run's part exactly as it always was, and `mission` is the mission's part, or None with no mission surface."""
        if self.supervisor is None:
            return dict(REPLAY_REFUSAL)
        parts = self._stop_run_and_mission(STOP_BY_STOP_OP)
        return {"ok": True, "stop": parts["run"], "mission": parts["mission"]}

    def _op_resolve(self, request, connection) -> dict:
        """Reconcile an uncertain command. The daemon re-checks the evidence itself and the operator's declared finding
        is recorded beside it; the answer says what was found, how it was classified and whether the block is cleared."""
        if self.supervisor is None:
            return dict(REPLAY_REFUSAL)
        return self.supervisor.resolve(request.get("command_id"), request.get("finding"), request.get("note"))

    def _op_preview(self, request, connection) -> dict:
        """The LATEST preview frame, for a caller that wants one picture instead of the telemetry stream. Latest-only:
        there is no queue, so a slow reader misses frames and delays nothing."""
        if self.sources is None:
            return {"ok": False, "reason_code": "preview_source_not_attached", **read_models.UNAVAILABLE_PREVIEW}
        record = self.sources.latest_preview(with_bytes=request.get("with_bytes", True) is not False)
        return {"ok": bool(record.get("available")), **({} if record.get("available") else {"reason_code": record.get("reason")}), **record}

    def _op_result(self, request, connection) -> dict:
        state = self._table().get(request.get("command_id")) if isinstance(request.get("command_id"), str) else None
        return command_result(state) if state and state.get("status") else {"ok": False, "reason_code": "unknown_command"}

    def _op_history(self, request, connection) -> dict:
        return {"ok": True, "runs": history_entries(self._table(), self.recorded_data_mode)}

    def _op_manifest(self, request, connection) -> dict:
        """What one run states about itself: the pinned code bundle, the source revision, the configuration and input
        digests, the interpreter, the package versions and a model REFERENCE. Read-only, out of the run's own directory,
        so a run this data directory never recorded has none to give."""
        run_id = request.get("run_id")
        if not isinstance(run_id, str) or demo_contracts.validate("common#/$defs/id", run_id):
            return {"ok": False, "reason_code": "bad_request"}
        manifest, reason = self.run_manifest(run_id)
        return {"ok": True, "run_id": run_id, "manifest": manifest} if manifest is not None else {"ok": False, "reason_code": reason}

    def _op_export(self, request, connection) -> dict:
        """One run, packaged to be handed to somebody else: its manifest, its ordered records, the frames those records
        point at, and bounded summaries. Read-only, and only for a run THIS data directory recorded, which is the same
        rule the observation store already enforces. The frames come from the store as they were written; a frame the
        store cannot resolve, or one the export's bounds leave out, is a stated gap on the index and never a silent
        omission. demo_runtime.bundle is imported here rather than at module level so that a replay-only daemon still
        loads nothing that can build or spawn anything."""
        from . import bundle

        run_id = request.get("run_id")
        if not isinstance(run_id, str) or demo_contracts.validate("common#/$defs/id", run_id):
            return {"ok": False, "reason_code": "bad_request"}
        manifest, reason = self.run_manifest(run_id)
        if manifest is None:
            return {"ok": False, "reason_code": reason}
        records = [record["event"] for record in self.log.read_run(run_id)]
        if not records:
            return {"ok": False, "reason_code": "unknown_run"}
        export = bundle.run_export(run_id=run_id, manifest=manifest, records=records, image_of=self._recorded_frame, contract_version=CONTRACT_VERSION)
        return {"ok": True, "run_id": run_id, **export}

    def _recorded_frame(self, observation_id: str):
        """The stored frame of one observation, for an export. A source that keeps its observations in memory (the
        synthetic mode does) has no recorded bytes to give and says so, which the export reports as a stated gap rather
        than exporting a rendition and calling it the recording."""
        reader = getattr(self._observations, "frame_bytes", None)
        return reader(observation_id) if callable(reader) and read_models.safe_observation_id(observation_id) else None

    def _op_history_events(self, request, connection) -> dict:
        run_id = request.get("run_id")
        if not isinstance(run_id, str) or demo_contracts.validate("common#/$defs/id", run_id):
            return {"ok": False, "reason_code": "bad_request"}
        records = self.log.read_run(run_id)
        if not records:
            return {"ok": False, "reason_code": "unknown_run"}
        origins = [{"seq": record["event"]["seq"], "source_session": record["origin"].get("source_session"), "source_seq": record["origin"].get("seq")}
                   for record in records if isinstance(record.get("origin"), dict)]
        return {"ok": True, "run_id": run_id, "events": [record["event"] for record in records], "origins": origins}

    def _op_run_output(self, request, connection) -> dict:
        """The controller's own stdout or stderr for ONE run, as a bounded tail.

        The supervisor has always written these to files in the run directory and nothing ever served them, so the one
        place that says what the controller actually did was reachable only by someone with a shell on this machine.
        That is what sent the operator of the 2026-09-22 align failure to a terminal to find out why a run stopped.

        THE PATH IS RESOLVED FROM THE RUN ID AND NEVER FROM THE REQUEST. The id is validated against the contract's own
        id shape first, which admits no separator and no dot, and the directory is then composed here. A caller never
        names a file, so there is no path for one to traverse. The result is scrubbed by the same Sanitizer every other
        answer goes through, with the generic shapes on: this is raw machine output and is the likeliest place for a
        home directory or an address to appear.

        BOUNDED, BECAUSE A RUN CAN PRINT FOR AN HOUR. At most MAX_OUTPUT_BYTES are read, taken from the END of the
        file, and the answer says whether anything was dropped and how big the file is, so a reader is never quietly
        shown a part and told it is the whole.
        """
        run_id, stream = request.get("run_id"), request.get("stream", "stdout")
        # The id shape first, so a malformed id is a bad request; the shared resolver then decides whether that run
        # exists, and it is what keeps `.` and `..` from naming a directory outside history/.
        if not isinstance(run_id, str) or demo_contracts.validate("common#/$defs/id", run_id) or stream not in ("stdout", "stderr"):
            return {"ok": False, "reason_code": "bad_request"}
        limit = request.get("max_bytes", MAX_OUTPUT_BYTES)
        if limit is None:
            limit = MAX_OUTPUT_BYTES
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            return {"ok": False, "reason_code": "bad_request"}
        limit = min(limit, MAX_OUTPUT_BYTES)
        directory = self._run_dir(run_id)
        if directory is None:
            return {"ok": False, "reason_code": "unknown_run"}
        path = directory / (STDOUT_FILE_NAME if stream == "stdout" else STDERR_FILE_NAME)
        try:
            size = path.stat().st_size
            with open(path, "rb") as handle:
                handle.seek(max(0, size - limit))
                raw = handle.read()
        except FileNotFoundError:
            # The run exists and this stream was never written: an empty stderr is the ordinary case for a good run.
            return {"ok": True, "run_id": run_id, "stream": stream, "text": "", "bytes": 0, "total_bytes": 0, "truncated": False}
        except OSError:
            return {"ok": False, "reason_code": "output_unreadable"}
        text = raw.decode("utf-8", "replace")
        truncated = size > len(raw)
        if truncated:  # a tail can begin mid-line, and half a line read as a whole one is a misquote
            text = text.split("\n", 1)[1] if "\n" in text else ""
        return {"ok": True, "run_id": run_id, "stream": stream, "text": self.sanitizer.text(text, generic=True),
                "bytes": len(text.encode("utf-8")), "total_bytes": size, "truncated": truncated}

    def _op_observation(self, request, connection) -> dict:
        observation_id, what = request.get("observation_id"), request.get("what", "record")
        if what not in ("record", "perception") or not read_models.safe_observation_id(observation_id):
            return {"ok": False, "reason_code": "bad_request"}
        if self._observations is None:
            return {"ok": False, "reason_code": "observation_source_unavailable"}
        raw = self._observations.record(observation_id) if what == "record" else self._observations.perception(observation_id)
        if raw is None:
            return {"ok": False, "reason_code": "not_found"}
        allow = read_models.RECORD_ALLOW if what == "record" else read_models.PERCEPTION_ALLOW
        projected = read_models.project(raw, allow)
        if what == "record":
            projected["has_frame"] = raw.get("raw_frame_path") is not None
        return {"ok": True, "observation_id": observation_id, "what": what, "data": self.sanitizer.scrub(projected, generic=True)[0]}

    def _op_frame(self, request, connection) -> dict:
        observation_id, what, max_width = request.get("observation_id"), request.get("what", "raw"), request.get("max_width", 640)
        if what not in ("raw", "overlay") or not read_models.safe_observation_id(observation_id) or isinstance(max_width, bool) or not isinstance(max_width, int):
            return {"ok": False, "reason_code": "bad_request"}
        if self.frames is None:
            return {"ok": False, "reason_code": "observation_source_unavailable"}
        rendition = self.frames.get(observation_id, what, min(1920, max(64, max_width)))
        return rendition if rendition is not None else {"ok": False, "reason_code": "not_found"}

    def close(self) -> None:
        """Let go of everything WITHOUT touching a running controller: it finishes on its own and the next daemon adopts it.

        detach() stops renewing the host lease and deliberately does NOT release it: a controller that is still commanding
        would lose the robot the moment this process ended. The host extends the lease on every valid command, so the
        controller keeps it by itself; a controller that has stopped commanding lets it expire. The lease client's socket
        is closed here, which releases nothing, and the forwards are torn down last so the controller's own ssh and scp
        (its own, not these) are unaffected."""
        if self.capability_check is not None:
            # FIRST, so no check that has not yet written its attempt record submits an ApplyPlan while the rest of this
            # shutdown runs; one already past that point is waited for below, within its derived bound.
            self.capability_check.begin_close()
        if self.signature_check is not None:
            self.signature_check.begin_close()  # no new verifier child from here on
        if self.supervisor is not None:
            self.supervisor.detach()
        if self.mission is not None:
            # A stop first, then the file: closing underneath a running mission would make its next append raise
            # inside the mission thread, and the mission would record itself FAILED with the name of an error caused
            # by this shutdown. close() does both, in that order, and waits.
            self.mission.close()
        if self.capability_check is not None:
            # Only the file, after a check in flight has written its answer (bounded by close_wait_s, derived from the
            # adapter's own bounds: capability_check.close_wait_bound_s). Nothing about the resident or the robot is
            # touched by closing.
            self.capability_check.close()
        if self.signature_check is not None:
            # Only the file, after a run in flight has written its record (one verifier bound plus a margin).
            self.signature_check.close()
        if self.sources is not None:
            self.sources.stop()  # two SUB sockets close; nothing on the robot or the camera is told anything
        if self.lease is not None:
            self.lease.close()  # the socket only: close() is documented never to release
        if self.forwards is not None:
            self.forwards.stop()
        if self.robot is not None:
            self.robot.stop()  # the forward's ssh ends and its local socket is removed; nothing on the robot is told anything
        if self.robot_host_watch is not None:
            self.robot_host_watch.stop()
        if self._synthetic_telemetry is not None:
            self._synthetic_telemetry.stop()
        if self.readiness is not None:
            self.readiness.stop()  # one ssh that reads a process list; it is asked to end and never waited out
        self.journal.close()
        self.log.close()
        try:
            os.close(self._lock_fd)
        except OSError:
            pass


def _motion_permission(data_dir):
    """This runtime's robot motion permission (in memory; its audit record in data_dir). A module-level function because
    RuntimeCore's own `robot_link` parameter shadows the module inside __init__."""
    return robot_link.MotionPermission(data_dir)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m demo_runtime.daemon", description="The demo runtime daemon: one command authority behind a private Unix socket.")
    parser.add_argument("--config", default=None, help="path of the PRIVATE runtime config JSON (or $KC_RUNTIME_CONFIG); required in live mode")
    parser.add_argument("--data-dir", required=True, help="the PRIVATE data directory, outside the repository")
    parser.add_argument("--synthetic", action="store_true", help="run ONLY the fake controller; every snapshot says data_mode synthetic")
    parser.add_argument("--replay-only", action="store_true", help="serve recorded runs; no supervisor and no command path exist")
    parser.add_argument("--enable-hardware-commands", action="store_true", help="live mode: allow commands that start the real controller (the preflight and ownership still decide)")
    args = parser.parse_args(argv)
    if args.synthetic and args.replay_only:
        parser.error("--synthetic and --replay-only exclude each other")
    if args.enable_hardware_commands and (args.synthetic or args.replay_only):
        parser.error("--enable-hardware-commands applies to live mode only")
    mode = SYNTHETIC if args.synthetic else REPLAY if args.replay_only else LIVE
    config, config_path = None, None
    if args.config or os.environ.get("KC_RUNTIME_CONFIG") or mode == LIVE:
        from .config import ConfigError, load_config, resolve_config_path

        try:
            config = load_config(args.config)
            config_path = os.path.abspath(resolve_config_path(args.config)[0])
        except ConfigError as error:
            print(f"demo-runtime: {error}", file=sys.stderr)
            return 2
    try:
        core = RuntimeCore(data_dir=os.path.abspath(os.path.expanduser(args.data_dir)), mode=mode, config=config, config_path=config_path,
                           hardware_enabled=args.enable_hardware_commands)
    except DataDirBusy as busy:
        print(f"demo-runtime: {busy}", file=sys.stderr)
        return 3
    except RobotConfigRefused as refused:
        print(f"demo-runtime: {refused}", file=sys.stderr)
        return 2
    decisions = core.start()
    server = RuntimeServer(core.data_dir / SOCKET_NAME, core.handle, core.stream)
    server.start()
    done = threading.Event()
    for number in (signal.SIGTERM, signal.SIGINT):
        signal.signal(number, lambda *_: done.set())
    mechanism = (core.supervisor.pidfd_mechanism or "pid_verified (NO pidfd on this platform: a stop re-checks the process identity first)") if core.supervisor else "none (no command path)"
    report = core.sources.report() if core.sources is not None else None
    attached = "no source supervisor in this mode" if report is None else f"tap {report['tap']['state']} ({report['tap']['reason'] or 'fresh samples'}), preview {report['preview']['state']} ({report['preview']['reason'] or 'frames arriving'})"
    tunnels = core.forwards_report()
    forwarded = "none (the configuration enables no tap)" if tunnels is None else ", ".join(f"{purpose} {one['state']}" + (f" ({one['reason']})" if one["reason"] else "") for purpose, one in tunnels.items())
    lease = core.lease_view()
    holding = "no lease client" if lease is None else ("held" if lease["held"] else f"not held ({lease['reason']})")
    # The backend and what owns the robot under it are SAID, not left to be inferred from a missing lease line.
    execution = core.execution_view()
    running_through = "none (no command path in this mode)" if execution is None else f"{execution['backend']} (ownership: {execution['detail']})"
    # The robot, said out loud rather than left to be inferred from a console that is not open yet. At this instant the
    # first background reading has usually not landed, and 'never_read' is what that honestly is.
    readiness = core.supervisor.readiness_report() if core.supervisor is not None else None
    robot = "nothing is read about a robot in this mode" if readiness is None else readiness["state"]
    print(f"demo-runtime: ready ({mode}; hardware commands {'ENABLED' if args.enable_hardware_commands else 'not enabled'}; stop mechanism {mechanism}; "
          f"{len(decisions)} interrupted command(s) reconciled: {sorted({decision for _, decision in decisions}) or 'none'}; sources: {attached}; "
          f"forwards: {forwarded}; host lease: {holding}; backend: {running_through}; robot readiness: {robot})", flush=True)
    done.wait()
    server.close()
    core.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
