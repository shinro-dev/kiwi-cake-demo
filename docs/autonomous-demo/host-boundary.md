<!-- Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE. -->

# Host boundary: command ownership at ingestion, stamped observations, a read-only tap

**Status: built and unit-tested with fakes. The gate has never been installed in a running host, on the robot computer
or anywhere else, and has never driven hardware.** What does run on the robot computer is the read-only VERIFIER of the
same module, which opens no socket and touches no motor: `--verify-stock-host` was run there by hand on 2026-09-21, and
since 2026-09-21 `bin/pi/start-host.sh` copies `host_boundary.py` to the robot computer and runs `--verify-robot` there
over the installed lerobot at the start of EVERY host, with the word `boundary` or without it. The gate itself is OFF
unless the host is started with `KC_HOST_BOUNDARY=1`; `bin/pi/start-host.sh boundary` is what sets that variable, and a
start without the word copies and runs the same module and sets nothing. See "What is not done" and "Before it is
turned on" at the end; both are part of the design, not an afterthought.

| Piece | File |
|---|---|
| gate, lease, stamp, tap (robot computer) | `bin/pi/host_boundary.py` |
| the few lines that install it | `bin/pi/lekiwi_host_safe_attach.py`, between the two `HOST BOUNDARY` marker lines |
| lease client, metadata helper (laptop) | `packages/demo-runtime/demo_runtime/lease_client.py` |
| tap subscriber (laptop) | `packages/demo-runtime/demo_runtime/tap_client.py` |
| tests | `autonomy/tests/test_host_boundary.py`, `autonomy/tests/test_host_safe_attach_static.py`, `packages/demo-runtime/tests/test_lease_client.py`, `packages/demo-runtime/tests/test_tap_client.py` |

## The problem, in the stock code

Read in lerobot at commit b4e2d0b6, `src/lerobot/robots/lekiwi/`:

- `lekiwi_host.py:42-44`: the command socket is a PULL socket with `CONFLATE=1`, bound on every interface. Anyone who can
  reach the port can push to it, and the socket keeps ONE message: the newest, whoever sent it.
- `lekiwi_host.py:81-85`: per iteration the loop reads one message, `dict(json.loads(msg))`, `robot.send_action(data)`,
  and only then `last_cmd_time = time.time()`. That timestamp is the 500 ms watchdog, and the watchdog stops the BASE only
  (`lekiwi_host.py:93-98`).
- `lekiwi_host.py:86-90`: `zmq.Again` is the "no command" branch; any other exception is logged and the loop goes on.
- `lekiwi_host.py:100-114`: `robot.get_observation()`, `json.dumps`, `send_string(..., NOBLOCK)` on a PUSH socket.
- `lekiwi_client.py:329-330`: the client sends `{key: float(value)}` for EVERY key of the action, so extra numeric keys
  travel, as floats. `lekiwi_client.py:207-220`: the client builds its state from its own key list and the camera names,
  so extra observation keys are ignored by a stock client.

Two consequences. A second client does not have to win a race inside `send_action`: with CONFLATE its message DISPLACES
the owner's newest command inside the socket, before any code of ours could look at it. And a check that raises inside
`send_action` is the wrong tool: the displaced owner command is already gone, and the exception is one more log line.
So the check has to sit where the loop reads the socket.

## What is installed, and only with `KC_HOST_BOUNDARY=1`

With the variable unset the wrapper imports nothing more and replaces nothing more than it did before, although
`host_boundary.py` is now sitting in the directory beside it on every start: the launcher copies that module so that
its step 3 can RUN it there to read the installed lerobot, and a file being present next to the wrapper is not the same
fact as the host importing it. A test cuts the marked block out of the wrapper and checks that the rest still has the
digest of the body that was demonstrated on the robot, or, while one change awaits its hardware re-proof (the attach's
one-count wheel rule, 2026-10-01), the digest of that body; the block has a digest of its own. A second test parses the
wrapper and requires every mention of `host_boundary`, and the name `KC_HOST_BOUNDARY` itself, to sit inside the one
module-level block that the variable gates, so an unset variable can reach no import.

With the variable set, `host_boundary.install(LeKiwi, LeKiwiHost)` runs when the wrapper is loaded, after the
replacements the wrapper itself makes (`configure`, `calibrate`, and `send_action` in scan mode) and before `main()`:

1. The lease control socket and the tap socket are bound AT ONCE, on `127.0.0.1` only (ports 5557 and 5558, or
   `KC_HOST_BOUNDARY_CONTROL_PORT` / `KC_HOST_BOUNDARY_TAP_PORT`). A busy port ends the process here: the servo bus has
   not been opened yet.
2. `LeKiwiHost.__init__` is wrapped. After the stock constructor, the conflating command socket is unbound and closed, and
   a PULL socket WITHOUT conflate (`RCVHWM` 16) is bound on the same endpoint. `host.zmq_cmd_socket` becomes a
   `CommandGate`. By then `robot.connect()` has run, so the verify-only `configure` of the wrapper has already passed.
   If the endpoint cannot be bound again within 5 s the constructor raises and the host does not start, which is what a
   busy port does to the stock host too.
3. `LeKiwi.get_observation` is wrapped: the stock reading, then the stamp, the lease service and the tap.

`host_boundary.py` has no motor access: it never names the robot's bus, and the only robot method it names is the
`get_observation` it wraps. A static test checks this. Everything runs on the host loop's own thread.

## Ingestion

The stock loop calls `host.zmq_cmd_socket.recv_string(zmq.NOBLOCK)` unchanged. `CommandGate.recv_string`:

- drains everything pending (at most 256 messages per call);
- validates each message: it must be a JSON object of at most 16384 bytes in one frame, and carry, AS NUMBERS,
  `kc.lease` (the lease generation) and `kc.session` (the host session); both must equal the live lease;
- keeps the NEWEST VALID command, removes every key that starts with `kc.`, and returns the rest as JSON text;
- raises `zmq.Again` when no message was valid, exactly as an empty socket does. The stock loop then takes its "no
  command" branch: no `send_action`, no `last_cmd_time` refresh, no exception, no shutdown. A fault inside the gate is
  swallowed, counted (`internal_errors`) and also ends in `zmq.Again`.

Rejections are counted, never raised. The reasons, in the order they are decided:

| Reason | Meaning |
|---|---|
| `malformed` | not one frame, too large, not UTF-8 JSON, not an object; or (under a live lease) only one of the two keys, or a key that is not a finite whole number |
| `no_lease` | the host holds no live lease (never acquired, released, expired), whatever the message carries; or the message carries neither key |
| `wrong_session` | the numbers are well formed and the session is another host process |
| `wrong_generation` | the session matches and the generation is not the live one: an earlier owner |

A command that was valid but older than another valid one in the same drain is counted as `superseded`. Every valid
command extends the lease, superseded or not.

## The lease

A REP socket on `127.0.0.1` of the robot computer. It is not the command socket: nothing received on it can reach
`send_action` or `last_cmd_time`, and it is served from the observation path, after the commands of that iteration were
drained (so an owner's last command is honoured before its own release). A test sends 21 lease requests through the
reproduced stock loop and sees 21 "no command" iterations and an untouched watchdog timestamp.

Requests and replies are one JSON object each. Every reply carries `ok` and the full status: `state` (`free`, `held`,
`expired`), `host_session` (text), `generation`, `owner_label`, `request_id`, `ttl_s`, `expires_in_ms`, and the counts of
acquisitions, releases and expiries. A refusal adds `reason`.

| Request | Effect |
|---|---|
| `{"op":"acquire","owner_label":L,"ttl_s":T,"request_id":R}` | refused with `lease_held` while a lease is live, for ANY label (a label is not an identity). Otherwise the generation is incremented and the lease is held. `T` defaults to 180 s and is kept within 1 s to 900 s; the reply states what was granted |
| `{"op":"renew","host_session":S,"generation":G}` | extends a HELD lease. `wrong_session`, `wrong_generation`, `no_lease`, `lease_expired` otherwise |
| `{"op":"release","host_session":S,"generation":G}` | the lease becomes free. The generation is NOT reused |
| `{"op":"status"}` | the status |

Why 180 s: the longest quiet perception phase of a cycle lasts about 90 s, and no command is sent during it, so nothing
extends the lease. The TTL has to outlast that with margin.

**Expiry only rejects.** From then on commands are dropped as `no_lease`. Nothing is handed to anyone, no motor is
written, the gripper is left alone; the stock watchdog stops the base 500 ms after the last dispatched command and the
arm holds its goal, as it does whenever commands stop. An expired lease does not come back through `renew`. A new
acquisition is always explicit, and it is a new generation, so whatever the earlier owner still sends is dead.

Who keeps the lease: if the controller disappears, the lease stays until it is released or expires. If the runtime
disappears, a live controller keeps the lease through its own valid commands. There is no forced takeover: an operator
who needs the robot back waits for the expiry or restarts the host.

**Every request that was received becomes a reply, including one the handler could not process.** A REP socket that has
received and not answered refuses to read again, and that refusal is not `zmq.Again`; since the control service is
served from the observation path, one unanswered request would silence the tap for the rest of the host's life and put
the lease out of reach until a restart. A fault inside the handler is therefore counted in `internal_errors` and
answered with `{"ok": false, "reason": "internal_error"}` plus the lease status, so a holder does not read the reply as
a restarted host. Adversarial review on 2026-09-20 found a reachable instance of exactly that: a `host_session` made of
a character `str.isdigit()` accepts and `int()` rejects raised out of the handler, and four observations later the tap
had published nothing and the lease could no longer be acquired. `numeric_field()` now takes plain decimal text only,
and the reply backstop makes the wedge unreachable whatever a future handler does. Both have regression tests.

## Stamp and tap

Every observation the stock loop sends on PUSH gains three keys:

| Key | Value |
|---|---|
| `kc.session` | the host session, as decimal TEXT |
| `kc.seq` | an integer that increases by one per observation, from 1 |
| `kc.t_mono_ns` | `time.monotonic_ns()` of the robot computer, read immediately BEFORE the bus read: a sample is never older than its stamp says |

The same sample is published on a PUB socket (`127.0.0.1`, `SNDHWM` 8, `NOBLOCK`), as one JSON object: `kc.tap` (1),
`session`, `seq`, `t_mono_ns`, `read_ns`, `wall_time_s`, `observation` (scalar values only), `lease` (the status above),
`rejections` (by reason), `commands`, `ingestion` (received, superseded, drain cap hits, internal errors, control
counters) and `publisher` (`sent_count`, `drop_count`, both as they stood BEFORE this send).

`commands` is the command timing measured at the source, on the robot computer's monotonic clock: `dispatched_total`,
`dispatched_in_lease`, `last_gap_ms`, `max_gap_ms_in_lease` (closed gaps between dispatched commands of the current
lease), `first_after_lease_ms`, and `since_last_ms`, the OPEN interval at the moment of the sample. It is what a
continuity measurement across a module replacement would need from outside the replaced module. Nothing consumes it yet.

**Publisher drops are not subscriber gaps.** `publisher.drop_count` counts sends that failed in the host process. A plain
PUB socket does not fail when a subscriber is slow: libzmq drops the message for THAT subscriber and the send succeeds.
Such a loss is visible only to the subscriber, as a gap in `seq`. `tap_client` therefore reports, per sample,
`missed_before` (what this subscriber did not get), `publisher_drops_before` (how much of that the publisher accounts
for) and `lost_after_send_before` (the rest). A subscriber that connects late sees a gap too. None of these numbers says
anything about the PUSH/PULL stream: the tap cannot change which observations the controller receives, and a failed or
absent tap leaves every observation stamped.

## Laptop side

`lease_client.LeaseClient(endpoint, owner_label)`: `acquire`, `renew`, `release`, `status`. Each returns a dict with `ok`
and never raises; a failure on the laptop side is `timeout`, `transport_error` or `bad_reply`. `command_metadata()` gives
the two keys as floats, or `None`; `attach_lease_metadata(robot_client, metadata)` wraps a client's `send_action` so
that every action carries them (the caller's dict is not modified). `snapshot_lease()` has the shape of the console
contract's `owner.lease`. A request that timed out may still be served by the host; for `acquire` that would lock the
robot until expiry, so every acquire carries a random `request_id` and the client ADOPTS a live lease that a later reply
shows under one of its own unanswered ids. `close()` does not release: a controller may still be commanding.

`tap_client.TapClient(endpoint)`: `poll()` and `samples()`. Every age it computes is the LAPTOP's monotonic clock
(`receive_age_ns`: how long ago the sample ARRIVED). The host's `t_mono_ns` and `wall_time_s` are passed on as written
and are never subtracted from a laptop value. `source_stamp(sample)` has the contract's shape with `clock_domain`
`pi_monotonic`.

Both endpoints are loopback sockets of the robot computer. The runtime reaches them through an ssh forward that the
runtime owns; neither module opens ssh or knows an address. Both import `zmq` on first use only. `TapClient` refuses an
endpoint that is not `tcp://127.0.0.1:<port>`, `tcp://localhost:<port>` or `inproc://<name>`, as `preview_client` does,
and does not repeat the refused endpoint: the host binds the tap to its own loopback address, so an endpoint naming
another machine can only be a mistake, and the subscriber would take whatever answered there for the robot. The lease
endpoint is NOT checked that way, because `LeaseClient` is also the module a future forced-takeover tool would use.

## Where the implementation departs from the design brief

- **The session is a number on commands and text everywhere else.** The brief asks for a numeric host session nonce. On
  commands it has to be numeric (the stock client turns every value into a float), so it is a random whole number below
  2**48, which a float and JSON carry exactly. But the console contract (`common.schema.json`, `source_stamp.session`,
  and `snapshot.owner.lease.host_session`) and the freshness client take a STRING there, and a numeric `kc.session` in
  an observation would be rejected by that client as a bad stamp. Observations, lease replies and the tap therefore
  carry the decimal text of the same number; the gate compares numbers.
- **`renew` and `release` take the session as well as the generation.** Generation 1 of a restarted host must not be
  renewed by the holder of generation 1 of the previous one.
- **`request_id` on acquire**, for the adoption described above. Not in the brief; without it a late acquire locks the
  robot for a whole TTL.
- **`kc.t_mono_ns` is read before the bus read**, not after: the conservative side for any age computed on the robot
  computer.
- **The REP service keeps a reply it could not send** and sends it before reading again, because a REP socket that
  skipped a reply stops working.

## Residual bypasses, stated

This is cooperative ownership between processes that mean well. It is not authentication and it is not a safety
function.

- **A client that forges the metadata is accepted.** The session travels in every observation, on a port that is open
  to the network, and the generation is a small integer that the status and the tap report. Any client that reads an
  observation and counts can build a valid command. The gate stops a second controller that was started by mistake and
  an old owner that kept sending; it does not stop someone who wants in.
- **Direct bus access on the robot computer.** A process there that opens the servo port talks to the motors without
  passing the host at all. The start preflight refuses to start a host while another process holds the port; nothing
  watches for one that arrives later.
- **A host started without the variable** has no gate, and neither has any other host entry point
  (`lekiwi_host_noninteractive.py`, `lekiwi_host_eval.py`, the stock module).
- **The lease and tap sockets are open to every local process of the robot computer**, and to whoever can open an ssh
  forward to it. Any of them can acquire a free lease, read the status, and release a lease whose generation it read.
- **A flood costs latency.** Rejected messages are still read and parsed. The drain stops at 256 messages per call and
  libzmq queues per client, so a flooding client cannot displace the owner, but it can delay the owner's command by
  iterations and cost the loop time.
- **Expiry is noticed when the state is looked at** (each command, each lease request, each observation), not by a timer.
  The loop looks about 30 times a second.
- **The action itself is not validated.** A valid owner's command goes to the stock `send_action` as it is, with whatever
  the stock code does for a missing key or a value that is not finite.
- **An owner label is free text within `[A-Za-z0-9_.-]{1,64}`** and appears on the tap and in the host's terminal. Do not
  put an identity in it.

## What is not done

- **The metadata is sent, but nothing checks the lease is still held.** Since 2026-09-21 the controller attaches it on
  both paths, the arm through the client it sends on and the base through the transport each pulse builds, and the
  runtime acquires the lease before it spawns a controller that could command motion. What is missing is liveness: the
  controller validates the shape of the pair it was handed, not that the lease still exists. If it expired between the
  acquire and the first command, the host rejects command by command as `no_lease` instead of the controller refusing up
  front, and if someone else acquires mid-run every later command is `wrong_generation` and this side does not notice.
  A status round trip over the control socket would close both, and it needs the forward the runtime owns.
- **The launcher turns it on only when asked, and nothing has asked yet on hardware.** Since 2026-09-21
  `bin/pi/start-host.sh` copies `host_boundary.py` next to the wrapper and checksums that copy on EVERY start, with the
  word `boundary` or without it, and refuses when the file is missing from this checkout (status 5) or when the copy
  does not match (status 4). It copies the module because step 3 of the launcher RUNS it on the robot computer under
  the host's own interpreter: `--verify-robot` over the installed `lekiwi.py` on every start (precondition 0 below),
  and `--verify-stock-host` over `lekiwi_host.py` only with the word (precondition 1 below). Every pass that was asked
  for runs whatever an earlier one found, and the host is not started unless every pass that ran holds; a pass that
  could not be run at all refuses the start too, and is reported as a check that did not happen rather than as a source
  that changed. What the word `boundary` adds on top of all that is the second pass and `KC_HOST_BOUNDARY=1` in the
  host's environment. Without the word nothing imports the copied module, no variable is set, and the host that starts
  is the host that always started. That the wrapper fails at import when the variable IS set and `host_boundary.py` is
  absent is a second reason the launcher catches a missing file first; a test shows the import failure, and the
  launcher's refusals were exercised against stub ssh and scp, never against the robot. **No host has been started with
  the boundary on hardware yet**: the first one is step 2 of `docs/autonomous-demo/hardware-qualification.md`.
- **The ssh forward and the runtime's supervision of the lease** (when to acquire, renew and release, what the console
  shows) are the runtime's and are not in these files.
- **No contract exists for the tap sample.** Only the pieces that have one are checked against it: the source stamp and
  the snapshot lease.
- The socket swap was exercised on `inproc://` in the tests and, by hand, on the development laptop only: 200 close and
  rebind rounds on a loopback port (no retry was needed), and one pass of the real `take_over_command_socket` over a
  conflating socket bound the way the stock host binds it, fed through the loopback interface (three owner commands, the
  newest dispatched, two superseded). Never on the robot computer.

## Before it is turned on

Preconditions 0 and 1 are the two read-only passes `bin/pi/start-host.sh` makes over the installed lerobot before it
starts anything, and they RUN IN DIFFERENT MODES. Precondition 0 runs on EVERY start, with the word `boundary` or
without it, because what it reads governs the base path itself, which every host has. Precondition 1 runs ONLY with the
word `boundary`, because what it reads governs the assumptions the boundary makes when it patches the stock loop, and a
plain start refused over a loop it will never touch is a refusal for the wrong reason. A pass that was asked for and
does not hold refuses the start, and nothing is started.

Steps 0, 1 and 2 were established on the robot computer on **2026-09-21**, read-only, in the Python environment the
host runs in, and all three are **SATISFIED**: the installed lerobot checkout carries both of the local changes step 0
reads for, `--verify-stock-host` printed `stock host loop: the assumptions of host_boundary.py hold`, and that
interpreter has pyzmq 27.1.0 with libzmq 4.3.5, the versions the offline tests ran on. The launcher makes each of those
first two passes again at every start it applies to and refuses the host when one does not hold, because lerobot on the
robot computer can be updated between one day and the next. Steps 3 to 5 remain open; the attended session that covers
the parts of them that need no motion is `docs/autonomous-demo/hardware-qualification.md`.

0. **Verify the lerobot ROBOT source installed on the robot computer.** SATISFIED 2026-09-21, and re-read at EVERY
   start. `LeKiwi.send_action` in ITS copy of `lekiwi.py` carries two changes that were written by hand into the
   installed checkout, and they are not the same kind of change: the clamp fix IS lerobot's own later commit f66e512
   (issue 4309), written into a checkout at b4e2d0b that predates it, and the empty-arm guard is in no lerobot commit
   at all and is written down only in this repository (`bin/pi/apply-lerobot-patches.sh`, `LIMITATIONS.md`, `NOTICE`).
   Both live only in that installed checkout, so a `git checkout`, a pull or a reinstall takes them away again in
   silence. Without the clamp fix every command raises `KeyError` before a register is written, because the host is
   started with `--robot.max_relative_target=15.0` and that branch is therefore taken on every single command. Without
   the guard a velocity-only command, which is what every base move sends, makes the bus raise `StopIteration` before
   `Goal_Velocity` is written; the stock loop swallows it, the dispatch clock is never refreshed, and the watchdog
   stops the base with no error anywhere the operator is looking. Having applied a patch and the file the host will
   import still carrying it are two different facts, and this pass reads the second one. What was established on
   2026-09-21 is the first: the installed checkout was read on the robot computer by its own `git status` and `git
   diff`, and both changes were in it. On the robot computer, in the Python environment the host runs in, from the
   directory that holds `host_boundary.py`:

   ```bash
   python3 host_boundary.py --verify-robot "$(python3 -c "import importlib.util as u; print(u.find_spec('lerobot').submodule_search_locations[0])")/robots/lekiwi/lekiwi.py"
   ```

   It reads that one file as text and prints either that the assumptions hold or each `MISMATCH`. It checks: a
   `Goal_Position` write inside `if arm_goal_pos_raw:` and NO `Goal_Position` write outside it; an unguarded
   `Goal_Velocity` write following that guard at the same level; the clamp branch indexing
   `present_pos[key.removesuffix(".pos")]`, with no lookup left that skips the strip; the three `base_goal_vel` keys
   the laptop sends on every command, read before the first write; and the base stop of `LeKiwi.stop_base`, which must
   still write `dict.fromkeys(self.base_motors, 0)` with a retry count. A pass that does not hold refuses the start
   with status 3, and NOTHING is started.
1. **Verify the robot computer's installed host loop.** SATISFIED 2026-09-21, and re-read at every start that carries
   the word `boundary`. Everything above rests on the shape of `main()` in ITS copy of
   `lekiwi_host.py`, not on the laptop's. On the robot computer, in the Python environment the host runs in, from the
   directory that holds `host_boundary.py`:

   ```bash
   python3 host_boundary.py --verify-stock-host "$(python3 -c "import importlib.util as u; print(u.find_spec('lerobot').submodule_search_locations[0])")/robots/lekiwi/lekiwi_host.py"
   ```

   It reads that one file as text and prints either that the assumptions hold or each `MISMATCH`. It checks: the command
   socket is read once per iteration with `recv_string`, inside a `try` whose first handler is `zmq.Again` and which also
   catches `Exception`; inside it the order is read, `send_action`, `last_cmd_time`; `last_cmd_time` is refreshed nowhere
   else in the loop; the command socket is used for nothing but `setsockopt`, `bind`, `recv_string` and `close`;
   `get_observation` is called once per iteration and its result is sent on the observation socket. On the development
   laptop (lerobot b4e2d0b6) it passes. This pass reads `lekiwi_host.py` and nothing else: `lekiwi.py` is precondition
   0's file, and `lekiwi_client.py` is checked by no pass at all, so read `_remote_state_from_obs` there by eye.
2. Check the pyzmq and libzmq versions of the robot computer; the tests ran on pyzmq 27.1.0 with libzmq 4.3.5.
   SATISFIED 2026-09-21: pyzmq 27.1.0, libzmq 4.3.5. Step 0 of the qualification session
   (`docs/autonomous-demo/hardware-qualification.md`, not the precondition 0 above) reports them again.
3. Make the controller attach the metadata, and see every command of a cycle accepted on the tap (`rejections` flat,
   `dispatched_in_lease` growing) with the arm SUPPORTED and the operator's authorization for that test. OPEN: that is a
   cycle, so it is motion, and it is not part of the no-motion qualification session. What that session does establish
   first is the opposite half: that a command WITHOUT the metadata is counted as rejected and dispatches nothing
   (step 2 of `hardware-qualification.md`).
4. Prove on hardware what the unit tests only model: that stopping commands under a live lease still trips the stock
   base watchdog, that Ctrl-C still leaves the arm holding (the gate's `close()` runs inside the stock
   `host.disconnect()`), and that the loop rate is unchanged with the tap subscribed and unsubscribed. PARTLY COVERED:
   step 3 of `hardware-qualification.md` measures the loop rate with a tap subscriber attached and with none, both from
   the host's own stamps. The watchdog after a DISPATCHED command and the Ctrl-C behaviour need a lease and a real
   command, and stay open.
5. Only then record a new demonstrated digest for the boundary block in `test_host_safe_attach_static.py`. Today that pin
   records what was reviewed, not what was demonstrated.

## Tests

From the repository root, no hardware, no network, no lerobot import:

```bash
python3 autonomy/tests/test_host_boundary.py
python3 autonomy/tests/test_host_safe_attach_static.py
python3 packages/demo-runtime/tests/test_lease_client.py
python3 packages/demo-runtime/tests/test_tap_client.py
```

`test_host_boundary.py` reproduces the stock loop body statement for statement and drives the real gate with it, with
fake sockets and with real pyzmq sockets on `inproc://`. Set `KC_LEROBOT_HOST_SOURCE` to the path of an installed
`lekiwi_host.py` to run the shape check against it as part of that file; without it that one test is skipped, visibly.
