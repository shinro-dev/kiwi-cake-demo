<!-- Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE. -->

# demo-runtime: explicit runtime configuration and preflight

`demo_runtime` is a small Python package (standard library only) with two jobs:

1. `demo_runtime.config`: state, in ONE private file, every machine-specific input a run depends on.
2. `demo_runtime.preflight`: check all of it BEFORE the arm moves, and report each problem with a stable reason code.

**Status: not wired in.** Nothing under `bin/`, `autonomy/` or `tools/` imports this package. The controller still resolves
its data the old way. Wiring the config and the preflight into the controller is a separate change with its own review.
This package only makes that change possible and testable.

## The problem it answers

Today the controller and its perception tools find mutable machine data relative to their own `__file__`:

| Input | Where today's code looks | Read by |
|---|---|---|
| saved scan pose | `<repo>/.local-dev/calibration/scan-home-current.json` | `bin/laptop/manipulation/run_single_cube_cycle.py`, `bin/laptop/perception/run_stage1_scan.py` (`HOME_FILE`) |
| pad template | `<repo>/.local-dev/calibration/pad-template/` | `tools/static_world_observation.py` (`TEMPLATE_DIR`) |
| observation store | `<repo>/.local-dev/observations/` | `autonomy/perception/observation_store.py` (`DEFAULT_ROOT`) |
| robot address, robot id | required CLI arguments | both CLIs |
| ssh target | `--pi-ssh`, default `$KC_PI_SSH` | both CLIs |
| camera ids | `$KC_WRIST_CAMERA_ID`, `$KC_FRONT_CAMERA_ID`, read at capture time | `PiCamera.capture` |

Three consequences, each observed in the code:

- the saved pose is first read in the middle of a cycle (placement and base return), so a missing file is discovered
  **with the cube held**;
- a missing template directory makes `static_world_observation.py` record `"pad_by_template": null` and carry on: the
  pad is localized without the template and **nothing says so**;
- an unset camera id variable is simply left out of the capture command: **silently omitted**.

None of these needs the robot to be detected. They are facts about files and configuration.

## What this package owns

- the schema of the private runtime config, how it is located, and how it is validated;
- redaction: what may appear in `repr()`, in an error, in a browser event, in a run bundle;
- `config_digest` and `input_digests`: how a run states WHICH configuration and WHICH inputs it used, without revealing them;
- the preflight: the checks, their ids and their reason codes;
- the typed action allowlist (which action kinds exist and what shape their arguments have).

## What this package must NOT own

- **motion bounds and physical gates.** `pan:4` and `pan:400` are both well formed here. The step limits, the elbow
  guard, the gripper bands and every refusal that depends on a measured pose stay in the controller, next to the physics
  that justifies them. A passing preflight means "the inputs exist and the request is well formed", never "safe to execute";
- hardware, cameras and robot clients: the package opens none of them, and `import demo_runtime` pulls in no numpy,
  OpenCV, ZeroMQ or robot client (a test proves it). The ONE thing it reaches the robot computer for is the `motor_host`
  preflight check, which reads a process list over `ssh` and is described with the preflight below;
- the observation store itself: the preflight applies the store's local-only RULE to the configured root; saving, record
  validation and the all-or-nothing write stay in `observation_store.py`;
- perception: whether the template MATCHES the pad in front of the camera is a runtime question. The preflight reads 24
  bytes of each PNG header and never decodes a pixel;
- secrets: the config holds identities (an address, device ids), not credentials. Do not put a password or a token in it.

## The config

The file is **private**. It names a robot, an address and device ids, and none of those may be committed (`AGENTS.md`).
Its path comes from `--config` or from the environment variable `KC_RUNTIME_CONFIG`; `--config` wins. There is
deliberately **no default path inside the repository**: a config that is found by accident is the failure this removes.

`config.example.json` shows every field, with reserved values only (`192.0.2.x`, `example-robot`, `example-camera-id`).
Copy it OUTSIDE the repository and edit the copy.

| Field | Meaning |
|---|---|
| `data_root` | absolute path (or `~/...`). A relative root is refused: it would resolve against the current directory |
| `saved_pose_file`, `pad_template_dir`, `observation_root` | absolute, or relative to `data_root` (never to the cwd, never to `__file__`) |
| `host.address`, `host.robot_id` | the robot host and the robot id the client selects its calibration by |
| `host.cmd_port`, `host.obs_port` | default 5555 / 5556 |
| `pi_ssh_target` | the ssh destination used for captures |
| `cameras.wrist_id` | required by the preflight |
| `cameras.front_id` | a string, or `null` to run without the front camera ON PURPOSE. An empty string is refused |
| `model.identity`, `model.revision` | which detector a run used; `revision` may be `null` |
| `capture_provider` | `legacy_per_capture` (default: one ssh and one camera lifecycle per frame) or `camera_service` |
| `freshness_refusal_enabled` | boolean, default `false` |
| `tap` | `null`, or `{ "enabled": bool, "port": int }`; a port is required when enabled |
| `execution_backend` | `baseline_normal_host` (the default) or `boundary_lease_host`; see below |

**`execution_backend`** decides how a run reaches the robot, and it is the one switch a file may leave out and still get
a definite answer: a configuration that names neither backend gets `baseline_normal_host`, because that is the path with
a record on hardware. `baseline_normal_host` runs the proven controller against the motor host in its NORMAL mode and
takes no lease of any kind; the only thing that owns the robot under it is the runtime's own single run slot on the
computer the runtime runs on, and nothing on the robot refuses a command from anywhere else. `boundary_lease_host`
needs the robot-side host boundary and takes that boundary's command lease before it starts anything. The runtime SAYS
which one it is on, in its startup line, in `status` and in the snapshot's `execution`.

Loading validates STRUCTURE only: types, enum choices, port ranges, an absolute root, and **unknown keys are refused** (a
typo must not become a silent default). The refusal says how many unknown keys the section holds and lists the keys the
section TAKES; it never repeats a key it was given, because a badly nested file can put a value where a key belongs and a
robot id made of lowercase letters and digits looks exactly like a field name. Whether the identities are present and the files exist is the preflight's job, so
that one report can list everything that is wrong instead of stopping at the first problem.

```python
from demo_runtime import add_config_argument, load_config, run_preflight, sanitized_summary, input_digests

add_config_argument(parser)                    # adds --config
config = load_config(args.config)              # --config, else $KC_RUNTIME_CONFIG, else ConfigError
report = run_preflight(config, args.do)        # before robot.connect(), before any motion
if not report.ok:
    print(report.to_dict())                    # safe to print and to send to a browser
    return 3
bundle = {"config": sanitized_summary(config), "inputs": input_digests(config), "preflight": report.to_dict()}
```

### Redaction

| Surface | Shows |
|---|---|
| `repr(config)`, `str(config)`, and the same for `host`, `cameras`, `tap` | `<redacted>` / `<unset>` for the address, the robot id, the ssh target, the camera ids, the tap port and every path (a path names a home directory). Ports of the host and the model identity are shown |
| `ConfigError` | a FIELD name or the SOURCE (`--config`, `KC_RUNTIME_CONFIG`). Never a value, never a key read from the file, never the file's path, never the broken document. It carries no `__cause__` and no `__context__`: `json.JSONDecodeError` keeps the whole document in `.doc` |
| `sanitized_summary(config)` | booleans, enum choices, the model identity and revision (only in the public shapes below), `config_digest`. No number at all, so no port |
| `PreflightReport.to_dict()` | check ids, reason codes, details that name fields, fixed file names, joint names and exception CLASSES; the action KIND when it parsed. Never the raw action text |
| `config_digest(config)` | sha256 over a canonical serialization of the FULL config |

The model identity and revision are shown by ALLOWLIST, in the summary and in `repr()` alike. An identity is shown only
while it has the shape of a public hub name (`name` or `org/name`: letters, digits, `.`, `_`, `-`); a revision only as a
commit hash, a tag or a branch (the same alphabet plus `/`, as in `refs/pr/7`). Anything else is withheld: a local
checkpoint path (it names a home directory), the URL of a served model (an address and a port), a URL that carries a
credential, anything containing `..`, and a dotted quad even though it fits the alphabet. `model_identity_withheld` and
`model_revision_withheld` say so, so a withheld value does not read as "none".

Named residuals, stated rather than hidden:

- the ATTRIBUTES are plain values (the consumers need them). `vars(config)`, `dataclasses.asdict(config)` and
  `config.to_private_dict()` show everything and must never be logged;
- `config_digest` is **unsalted**. Someone who already knows every other field could search a low-entropy field (an
  address inside a known /24) against a published digest. It detects change; it is not a commitment scheme.

## The preflight

`run_preflight(config, action)` runs EVERY check, always, in a fixed order, for every action; no check stops the others.
A check that raises is a FAILED check (`check_error`), never a skipped one. It reads local files, writes one probe file
(removed at once, and only if `observation_root` already exists) and calls the local `git` binary once. It never touches
a camera, a motor or a robot client.

**One check asks the robot computer, and only when the caller hands it something to ask with.**
`run_preflight(config, action, motor_host=probe_motor_host)` adds the `motor_host` check: the host preflight that
`bin/laptop/run-cycle.sh` runs before every call it makes. Without that argument nothing leaves this machine and the
report is exactly the local report. The probe is one `ssh` with a script on stdin that READS `pgrep` and one
`/proc/PID/environ`; it starts nothing, stops nothing and commands no motor, and it reports what it found through its
exit status, so no text from the other machine is repeated in the report. The runtime binds it for the baseline backend
only, and only for a SUBMIT: a snapshot never asks, because a snapshot is taken every few seconds and an `ssh` that has
to time out would stall each one.

| Check id | Fails with | When |
|---|---|---|
| `saved_pose` | `saved_pose_missing`, `saved_pose_unreadable`, `saved_pose_not_json`, `saved_pose_joints_missing`, `saved_pose_joint_not_numeric` | the file must exist, parse, and hold `measured_joints_deg` with a finite number for every joint in the controller's `SCAN_JOINTS` |
| `pad_template` | `pad_template_dir_missing`, `pad_template_file_missing`, `pad_template_json_invalid`, `pad_template_image_invalid`, `pad_template_size_mismatch` | the directory must hold `template.json`, `template.png`, `valid.png` (the three files `load_template()` opens); `template.json` must parse and hold the 3x3 `pad_to_raster` the localizer reads unconditionally; both images must be PNGs of one size (the localizer refuses a mismatched pair at the first capture) |
| `observation_root_writable` | `observation_root_not_writable` | an existing root is probed with a real write; a root that does not exist yet passes if its nearest existing parent has write permission, and is NOT created |
| `observation_root_local_only` | `observation_root_not_local_only`, `observation_root_git_unavailable` | see below |
| `wrist_camera_id` | `wrist_camera_id_missing`, `wrist_camera_id_unsafe` | |
| `front_camera_id` | `front_camera_id_empty`, `front_camera_id_unsafe` | `null` passes |
| `host_address`, `robot_id`, `pi_ssh_target` | `<id>_missing`, `<id>_unsafe` | |
| `motor_host` | `motor_host_not_running`, `motor_host_in_scan_mode`, `motor_host_mode_unknown`, `motor_host_unreachable` | only when a `motor_host` probe is given. The safe-attach motor host must be running on the robot computer and not in scan mode (where the gripper is limp and every gripper key is stripped). A process that matches and cannot be read leaves the mode unknown, and a connection that did not answer is not an answer: both refuse. With no usable `pi_ssh_target` nothing is asked and the check says so, because inventing a target is worse than letting the controller's own connect fail |
| `model_identity` | `model_identity_missing` | |
| `action` | `action_missing`, `action_not_allowed`, `action_argument_invalid` | the kinds are those `run_single_cube_cycle.py --do` dispatches on: `observe`, `pan:DEG`, `move:F,DZ`, `grasp:F,DZ`, `grip:latch`, `grip:open`, `base:S`, `exit`, `cycle`. A number is plain ASCII decimal, optionally with an exponent, and finite: `float()` alone would also take surrounding whitespace (a trailing newline included), `1_0` and the digits of any script, and the text can come from a browser. A colon with nothing after it is refused |

`*_unsafe`: today a camera id is pasted between single quotes into a shell command run on the Pi, and the ssh target is an
argv entry of `ssh`. A value that starts with `-` or contains whitespace, a quote or a shell metacharacter is refused.

**The local-only rule** is `ObservationStore.ensure_local_only`, generalized from "the checkout the code lives in" to ANY
repository: raw captures may only land outside every git repository, or somewhere git ignores. The path is compared by
real path, so a symlink cannot stand in for an outside location. The enclosing repository is the innermost directory at
or above the root that has a `.git` entry (a directory, or the file a worktree has). If `git check-ignore` cannot answer
(git missing, a repository git refuses to open, a timeout), the root is **refused**. `GIT_DIR` and its relatives are
removed from the environment of that call, so the answer is the same inside a git hook.

`input_digests(config)` returns `saved_pose_sha256` and `pad_template_sha256` for the run bundle. The template digest is
ONE sha256 over the three required files in sorted name order, each framed as name, NUL, 8-byte big-endian length,
content, so bytes cannot move between files without moving the digest. A missing input RAISES `PreflightInputError`: a
bundle must not carry a digest of nothing.

Named residuals:

- the preflight and the use of a file are separate moments: a file removed after a passing preflight is still missing
  later. The digests record what was there when they were taken;
- PNG headers only: a truncated image body passes here and fails at decode;
- for a root that does not exist yet, writability is judged from permission bits, not by a probe;
- a repository that is reachable only through `GIT_DIR`-style configuration (no `.git` entry in any parent) is not seen;
- with nested repositories only the INNERMOST one is asked. That is the store's own rule whenever the observation root
  sits directly in the code checkout (the legacy layout). A root inside a repository nested in the code checkout can pass
  here and still be refused by `ObservationStore`, which asks the code checkout;
- the write probe runs whether or not the local-only rule passes, so an empty probe file can exist for an instant inside
  a directory the rule then refuses. It is removed at once and holds no capture;
- the config FILE's own location is not checked. `--config` can name a file inside a repository; keeping the private
  file out of git is the operator's job (copy the example OUTSIDE the repository).

## How this replaces `__file__`-relative access

The intended wiring, for the later change: the controller receives a `RuntimeConfig` at start, runs the preflight before
`robot.connect()`, and then reads `config.saved_pose_file`, `config.pad_template_dir` and `config.observation_root`
where it reads `HOME_FILE`, `TEMPLATE_DIR` and `DEFAULT_ROOT` today; the address, the robot id, the ssh target and the
camera ids come from the same object instead of from four different places. The code checkout then holds code only, and
can be read-only, shared or replaced without moving anyone's calibration.

## Legacy fallback

`legacy_layout(repo_root, ...)` builds a `RuntimeConfig` with exactly today's paths (`<repo>/.local-dev/...`), so a
caller without a config file can behave as before while still getting the preflight. The identities have no file to come
from in that layout: the address and the robot id are keyword arguments (they are required CLI arguments today), and the
ssh target and the camera ids are read from an `environ` mapping under the names today's code uses (`KC_PI_SSH`,
`KC_WRIST_CAMERA_ID`, `KC_FRONT_CAMERA_ID`). Pass `os.environ` explicitly; with no `environ` it reads no variable at
all. The resulting config reports `"layout": "legacy"` in its summary, so a run bundle shows that the fallback was used.

The legacy paths and the facts the preflight mirrors (`SCAN_JOINTS`, the template file names, the saved-pose key, the
action kinds, the detector's model name) are COPIED here, because their sources import numpy, OpenCV and the robot client
and a preflight must be able to refuse on a machine where those are broken. Each copy is pinned to its source by a test
that reads the source text, so a moved constant fails a test instead of drifting.

## Tests

From the repository root:

```bash
python3 -m unittest discover -s packages/demo-runtime/tests -t .
```

or either file directly (`python3 packages/demo-runtime/tests/test_preflight.py`). Tempdir only, synthetic fixtures
generated at run time (the PNGs are built byte by byte), no robot, camera or network. The local-only tests create real
throwaway git repositories and need `git` on the PATH; they are skipped, visibly, without it.

## The runtime daemon

`demo_runtime.daemon` is ONE command authority for the robot, behind a private Unix socket. The API server, the command
line and every manual action are clients of that socket, so a cycle started from the command line blocks Start in the
console and the other way round.

```bash
PYTHONPATH=packages/demo-runtime python3 -m demo_runtime.daemon --config FILE --data-dir DIR [--enable-hardware-commands]
PYTHONPATH=packages/demo-runtime python3 -m demo_runtime.daemon --data-dir DIR --synthetic      # only tests/fake_controller.py ever runs
PYTHONPATH=packages/demo-runtime python3 -m demo_runtime.daemon --data-dir DIR --replay-only    # no supervisor, no command path
PYTHONPATH=packages/demo-runtime python3 -m demo_runtime.cli --data-dir DIR status | run cycle|observe|return | stop | result ID | resolve ID FINDING
```

| Module | Owns |
|---|---|
| `daemon.py` | the process, the data directory and its lock, the three modes, the handlers behind the socket |
| `rpc.py` | the socket server (newline-delimited JSON) and `RuntimeClient` |
| `journal.py` | the command journal (fsynced) and the runtime event log (validated, one ordered seq, finite ring) |
| `supervisor.py` | arbitration, spawn, stop, the completion record, recovery, the merge of the controller's event file |
| `bundle.py` | the pinned, content-addressed run bundle and the run manifest |
| `read_models.py` | capabilities, components, the Cake ladder, observation projections, telemetry slots, the sanitizer |
| `cli.py` | `kiwi-demo`, through the same socket |

**Lifetime.** Starting the daemon or a client never acquires motors, releases torque, arms anything or contacts the robot:
a start reads the journal, looks at the local process table and listens. The daemon is not in the motion path. The
controller runs in its own session with its output in files, so it keeps running when the daemon dies, and the next
daemon adopts it.

**The data directory** is private (0700) and outside the repository: `runtime.sock` (0600), `journal.jsonl`,
`events.jsonl`, `daemon.lock` (one daemon per directory), `bundles/<digest>/`, `history/<run_id>/` (manifest, the
controller's event file, its stdout and stderr, the completion record). A directory deeper than a socket address allows
is reached through a descriptor of the directory.

### The socket

One request object per line, one or more response lines. `submit`, `stop` and `resolve` need a compatible `hello` on the
same connection; a client whose `hello` is answered `contract_incompatible` becomes read-only.

| Request | Answer |
|---|---|
| `{"op":"hello","contract_version":"1"}` | `{"ok":true,"contract_version":"1","runtime_session":".."}` or `{"ok":false,"reason_code":"contract_incompatible"}` |
| `{"op":"snapshot"}` | a contract `snapshot` |
| `{"op":"events","since":N}` | a stream: first `{"resync":{"retained_from_seq":M,"missed":[N+1,M-1]}}` when N is older than the ring, then one contract `event` per line |
| `{"op":"telemetry"}` | a stream, latest-only: `{"tap":{..}}`, `{"preview":{..}}` |
| `{"op":"submit","command":{..}}` | a contract `command_result` |
| `{"op":"result","command_id":".."}` | a contract `command_result`: the only way to resolve a lost answer; a client never resends blindly |

A `command_result` carries the contract's optional `payload_digest` whenever the journal recorded the command object it
is a receipt for. That is what lets a caller see that a repeat under a known id is not the same request, instead of
being answered with the first one's outcome; it survives a restart because it is taken from the journal and not from
memory. A rejected command records no command object, so its receipt carries no digest, and that means the journal
cannot say rather than that the two payloads agree. `first_seen_wall_utc` is folded from the first record of a command,
so it is on a receipt read back from the journal and absent on one the running supervisor answers out of its live table.
| `{"op":"stop"}` | `{"ok":true,"stop":{"requested_mono_ns":..,"run_id":..}}` or `"stop":null` |
| `{"op":"resolve","command_id":"..","finding":".."}` | the operator's resolution of an uncertain command |
| `{"op":"observation","observation_id":"..","what":"record"\|"perception"}` | an allowlisted projection |
| `{"op":"frame","observation_id":"..","what":"raw"\|"overlay","max_width":N}` | `{"content_type":"image/jpeg","bytes_b64":".."}`, rendered on demand, cached in memory; the stored frame is only ever read |
| `{"op":"history"}`, `{"op":"history_events","run_id":".."}` | finished runs; one run's events plus `origins` (see below) |
| `{"op":"status"}` | mode, counters (daemon starts, client reconnects, controller launches, dropped subscribers), the stop mechanism in use |

A reader that stops reading costs one thread that ends after a send timeout; a subscriber whose bounded queue overflows
is dropped and resumes with `since`. Delivery is never promised to be gapless: `retained_from_seq` says what can be served.

### Commands, states, recovery

A typed allowlist, validated by `demo_contracts` (the bounds live in the schema) and mapped to the controller's argv: no
shell, no free text. While a run is active only `stop` is accepted (`run_active`); nothing is queued.

`accepted -> spawning -> running -> finished`, plus `rejected` and `uncertain`. `accepted` is fsynced BEFORE any spawn,
`spawning` before the child is created, `running` carries the child's identity. The same `command_id` twice returns the
recorded result.

On start, every command left in `accepted`, `spawning` or `running` is reconciled: a live child is ADOPTED; a dead child
that left a `run.finished` record is finished from that record (exit status unavailable); anything else becomes
`uncertain`, the owner becomes `unresolved`, and every command but stop is blocked until `resolve` with a declared
finding (`no_controller_running`, `controller_finished`, `controller_stopped_by_operator`). A resolution is refused
while a process of that run is alive. The supervisor never respawns.

**Identity.** A controller is this daemon's only when its argv carries the scan id
(`console-<UTC>-<first 8 of command_id>`) AND it was started with `KC_EVENTS_PATH` naming that run's file inside this
data directory (compared by real path). The scan id alone is not enough: two daemons on one machine produced the same
one, and with a single match a daemon would have adopted, and then stopped, another daemon's controller. An environment
that cannot be read is treated as not ours.

**The invariant that a first build broke:** once a child exists, nothing lets go of it except its own exit. That build
called `os.pidfd_open`, which some interpreter builds lack; the call raised AFTER the spawn, the run was recorded as
"never started" and the robot was released while the controller ran a whole cycle. "Never started" is now reachable only
while no child exists, and the same rule is applied to the stop path, the waiter and adoption.

### Stop

SIGINT to the controller PID only, never the process group (the controller's ssh and scp children share it), sent
before anything is logged and without waiting for the tailer, the journal or a submit in progress. There is no
escalation to a stronger signal anywhere; the one helper that sends through a pidfd hardcodes SIGINT.

- Mechanism: `os.pidfd_open` when the interpreter has it, otherwise the kernel call directly (x86_64 and aarch64);
  without any pidfd, the `Popen` handle for the daemon's own child, and for an adopted process the pid after its identity
  was checked again (`pid_verified`, a residual window between the check and the signal). `status` names the mechanism.
- Requested, acknowledged and the observed exit are recorded separately. The acknowledgement time is the controller's
  own timestamp when its event is in the `laptop_monotonic` domain, otherwise the moment the runtime read it, and the
  record says which (`ack_latency_basis`).
- Classification: `acknowledged`, `completed_anyway`, `pre_handler_exit`, `crash`, `exit_unobserved`. MEASURED on
  CPython 3.13: a SIGINT that lands before the controller installs its handler ends the process with status -2, or with
  status 1 and the interpreter's `KeyboardInterrupt` in stderr (a traceback, or "Fatal Python error: init_import_site"
  followed by a thread dump). All of these are the requested stop: outcome `stopped`, evidence incomplete, and
  `pre_handler_evidence` says which shape it was. The real controller imports for seconds before its handler exists.
- An unacknowledged SIGINT is repeated to the same PID every 0.4 s, ten times at most. MEASURED: about 1 early stop in
  360 under load was LOST and the run went on to place the cube; with the repeat, 720 of 720 stopped, one of them on the
  second signal. The mechanism of the loss was NOT established (a repeat tied to the exec did not catch it), so the
  remedy does not depend on one. 0 of 720 bounds the residual only loosely.

### The pinned bundle

Before a spawn the controller entry, its static import closure (ast, function-level imports included), `demo_runtime`,
`demo-contracts` (python and schema) and the `PI_FILES` the controller ships to the robot host are copied to
`bundles/<digest>/`, read-only, where digest is the sha256 over the sorted (relative path, file sha256) list. The child
runs FROM the bundle (`cwd` = bundle root, `PYTHONPATH` = the bundle's packages only) with `KC_RUNTIME_CONFIG`,
`KC_EVENTS_PATH`, `KC_RUN_ID`, `KC_ARTIFACT_DIGEST`, `PYTHONUNBUFFERED=1`, stdin from the null device, stdout and stderr
to files. The manifest records the bundle digest, the git revision and dirty flag, the contract version, `config_digest`,
the input digests, the interpreter and key package versions, and the model as a reference. The closure is static: a
module imported by a computed name is not seen.

### Named residuals

- The contract's event kinds are a closed set, so the daemon's own facts (accepted, rejected, spawned, exited, owner
  changes, reconcile results) are `diagnostic.text` events told apart by `reason_code`.
- The contract envelope has no field for the controller's own seq. It is kept, with the controller's session and the
  byte offset of its line, in the `origin` of each line of `events.jsonl`, and served by `history_events` as `origins`.
  The envelope's `source_session` stays the controller's.
- The runtime says `run.started` itself (it owns the run's identity); the controller's copy is folded. The controller's
  `run.finished` is held until its process is gone and then emitted once, with the observed exit status and the gaps
  the runtime detected added to the controller's.
- No tap or preview source is attached in live mode: `telemetry` answers `tap_source_not_attached` and
  `preview_source_not_attached`, and `link.state` is `unknown`. In synthetic mode a synthetic tap runs while somebody reads.
- The Cake ladder rests on three local probes (a binary on disk, a resident process, an admin socket file); the runtime
  speaks to no resident, so `admin_interface_compatible` is never determined and `synthetic` is true by the contract's
  definition. `mission_participation` is false: the controller has no Cake adapter.
- The live spawn path (the real controller, `--enable-hardware-commands`) has never been run: every process these tests
  start is the fake. A controller that still finds its data relative to its own file would look inside the bundle.
- `KC_RUNTIME_FAULT_AFTER` (accepted, spawning, spawned, running) makes a SYNTHETIC daemon kill itself at that state.
  It exists for `test_daemon_recovery.py` and is ignored in every other mode.

### Runtime tests

Each file by its explicit path, from the repository root. Every child process is `tests/fake_controller.py`:

```bash
python3 packages/demo-runtime/tests/test_journal.py
python3 packages/demo-runtime/tests/test_bundle.py
python3 packages/demo-runtime/tests/test_supervisor.py
python3 packages/demo-runtime/tests/test_rpc.py
python3 packages/demo-runtime/tests/test_daemon_recovery.py
```

`test_daemon_recovery.py` starts real daemon processes, kills them with SIGKILL at each command state and starts a new
one on the same directory. Timing-sensitive behavior (an early stop, adoption) was also run with twelve test processes
at once; that is how the lost signal and the scan id collision were found.
