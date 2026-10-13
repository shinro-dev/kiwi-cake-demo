<!-- Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE. -->

# console-api

The operator console's HTTP surface. It forwards to the runtime daemon over that daemon's private Unix socket and serves
read models. It is a window, not an authority.

## What it does not own

No run, no motor, no lease, no task execution, no Cake semantics. It starts no controller and it never decides whether a
command is allowed: a command goes to the runtime and comes back as the runtime's own contract `command_result`.
Capabilities are the runtime's truth, computed by the same arbitration a submit goes through, so this process cannot
"unlock" anything by getting a read model wrong. Evidence rules live in the runtime; the browser renders what it is
handed.

It is **restartable at any time**. Kill it in the middle of a cycle and the cycle continues: the controller is the
runtime's child, and the runtime is not in the motion path either. An open tab does not have to be reloaded either, as
long as the runtime it was talking to is the same one: the credential belongs to the RUNTIME SESSION and a new API
process reads the same value back out of the runtime's private directory. `tests/test_live.py` kills a real API process
during a real synthetic run and checks that the run finishes normally, that a new client resumes from the cursor it had,
and that a credential served before the restart is still accepted after it.

## Running it

```bash
PYTHONPATH=apps/console-api python3 -m console_api --runtime-socket "$KC_DATA_DIR/runtime.sock"
```

| option | meaning |
| --- | --- |
| `--runtime-socket PATH` | required; the daemon's socket, `<its data directory>/runtime.sock` |
| `--port N` | loopback port, default 8770 |
| `--web-build DIR` | the built console to serve at `/`; without it only the API is served |
| `--credential-file PATH` | where this launch's credential is written; default is a 0600 file in the private per-user runtime directory |

The listener is `127.0.0.1` and nothing else. There is no LAN mode in v1. To use the console from another machine,
forward the port:

```bash
ssh -N -L 8770:127.0.0.1:8770 "$KC_PI_SSH"
```

and open `http://127.0.0.1:8770` on the local machine. Open it by that address and not by a name: the guard below
compares `Host` against the address the listener is actually bound to.

Starting this process acquires no motor, opens no camera and contacts no robot. It connects to the runtime's socket only
when a request arrives, so it starts even when the daemon is not running yet and reports `runtime_unavailable` until it
is. The access log is off on purpose: a request line would write query strings to a file.

## Security

Five things must all hold before a POST reaches the runtime, and a page on another origin can produce none of them:

| check | reason_code |
| --- | --- |
| `Host` equals the bound address (read from the socket, not from a configured guess) | `host_not_allowed` |
| `Origin` equals `http://<that address>` | `origin_not_allowed` |
| `Content-Type` is `application/json` | `content_type_not_json` |
| `X-Console-Request: 1`, a header that needs a CORS preflight no one answers | `console_header_missing` |
| `X-Console-Credential` matches the credential this server is serving | `credential_missing`, `credential_invalid`, `credential_superseded` |

`Host` is checked on **every** request, reads included: that is what stops a name which resolves to the loopback address
from reaching this port under its own name. No CORS header is ever sent, so no other origin can read an answer either.
`OPTIONS` is not answered, so no preflight succeeds.

The credential is random from its first character, kept in memory and in 0600 files outside the repository (writing one
inside the repository is refused). It is never logged, never put in a URL, and never in a JSON body. It reaches a page
only inside the `index.html` this server serves itself.

**The runtime session owns it, not this process.** Before it binds the port, the API asks the runtime who it is and takes
that session's credential out of the runtime's own data directory, creating it atomically if it is the first to ask
(`CredentialStore`). Restart the API against the same runtime and the same value comes back, so a tab that was left open
goes on commanding instead of being answered 403 until somebody reloads it. A runtime that is not up yet cannot name a
session, so that launch mints its own and says so on its ready line and in `credential_owner` on `GET /api/hello`; it
binds to the first session it hears about afterwards.

**A replaced runtime is a new credential, deliberately.** The API follows the session named by the handshake and by every
snapshot, so within one poll of a daemon being replaced it serves and accepts the new session's credential. A tab still
holding the old one is refused `credential_superseded`, which is the API saying it served that value itself and has
stopped accepting it. That is not `credential_invalid`, and the difference is the point: the console reconciles and asks
for readiness against the new runtime instead of treating a refusal as an attack or as a lost response.

From a command line:

```bash
curl -sS -X POST http://127.0.0.1:8770/api/stop \
  -H 'Origin: http://127.0.0.1:8770' -H 'Content-Type: application/json' -H 'X-Console-Request: 1' \
  -H "X-Console-Credential: $(cat "$KC_CONSOLE_CREDENTIAL_FILE")" --data '{}'
```

## What the web build must provide

`index.html` is served with the credential injected as a **data element**, not as executable code:

```html
<script type="application/json" id="kc-console-boot">{"contract_version":"1","api_base":"/api","credential":"...","credential_header":"X-Console-Credential","request_header":"X-Console-Request","api_session":"..."}</script>
```

The page reads it once with `JSON.parse(document.getElementById("kc-console-boot").textContent)` and keeps the value in
memory. If the build already contains an element with that id, its contents are replaced; otherwise the element is
inserted before `</head>`. `index.html` is served `no-store`, because it carries a per-launch secret and a reload must
get the current one.

Because no inline script is needed, `index.html` carries a Content-Security-Policy with `script-src 'self'`. **The build
must not inline its scripts**; a hashed external module is what the policy expects. Assets whose file name carries a
content hash are served immutable, everything else `no-cache`, and a path that climbs out of the build directory, or a
name beginning with a dot, is refused.

## The HTTP surface

| method and path | what it is |
| --- | --- |
| `GET /api/hello?contract_version=1` | the handshake. A tab that states another version is told `read_only: true` and nothing else |
| `GET /api/snapshot` | the contract `snapshot`, as the runtime built it |
| `GET /api/events?since=N` | SSE. `id:` is the event's seq; `Last-Event-ID` is used when `since` is absent |
| `GET /api/telemetry` | SSE, latest-only: `tap` and `preview` records under their own event names |
| `GET /api/preview.mjpg?role=wrist` | `multipart/x-mixed-replace` fed from the runtime's latest preview frame |
| `GET /api/observations/{id}/record` and `/perception` | the runtime's sanitized DTO; immutable and cacheable |
| `GET /api/observations/{id}/frame?what=raw\|overlay&w=N` | the decision frame as JPEG, transcoded by the runtime |
| `GET /api/history` and `GET /api/history/{run_id}/events` | finished runs and one run's events |
| `POST /api/commands` | body is a contract command; the answer is a contract `command_result` |
| `GET /api/commands/{command_id}` | the result lookup: the only way to resolve a lost answer |
| `POST /api/commands/{command_id}/resolve` | body `{"finding": "...", "note": "..."}`; the runtime reconciles that uncertain command |
| `POST /api/stop` | body is a contract `stop` command (or `{}`); the runtime's stop answer |
| `GET /api/mission` | the mission the runtime has on the record, folded. Its own surface, not a contract object |
| `GET /api/mission/candidates` | the prepared activation candidates, by `key` and `payload_digest` |
| `POST /api/mission/start` | body `{}` or `{"targets": [...]}`; the runtime validates the targets and answers a `mission_id` |
| `POST /api/mission/stop` | body `{}`; answered while a target waits on a capability, not only while one is carried |
| `POST /api/mission/activate` | body `{"candidate_key": "..."}` and nothing else |
| `GET /api/robot` | the ROBOT's Cake resident through the runtime's ssh unix-socket forward: its own `robot_status_version`, not a contract object |
| `POST /api/robot/confirm-session` | body exactly `{"session_uuid": "...", "host_instance": {"slot_instance": "...", "spawn_sequence": N, "pid": N}}`: the robot resident session and the `supervisor.lekiwi` host instance (slot instance handle as a decimal string, spawn sequence, child pid) the operator saw; the same guard as `POST /api/commands`; refused unless the resident reports the build pinned for the robot, a host runs under the slot, and every component equals what the robot reports now; a change of any component revokes it; the permission lives in the running runtime only, so a runtime restart needs a fresh confirmation. The build identity identifies the SOFTWARE the resident runs and the pin refuses a different build and nothing more; which deployment is observed is established by the authenticated connection to the intended robot (the ssh forward to that robot's admin socket, or the local socket on the robot) and the session that endpoint's resident reports now |
| `GET /api/capability/check` | the capability slot check's read: the prepared candidates by `key` and `payload_digest`, the robot's resident as the cached reading reports it (session, whether a HEALTHY host runs under the supervisor slot, whether the host is held, the capability slot), `off_reason` (the first reason an apply would be refused now) and every check on the record. Its own `capability_check_version`, not a contract object; `capability_check_unavailable` (409) with a `detail` in a runtime that has none |
| `POST /api/capability/check` | body exactly `{"candidate_key": "...", "payload_digest": "sha256:...", "session_uuid": "..."}`: apply ONE prepared Plan to the robot's capability slot and read it back, with NO motion and never for a mission; the same guard as `POST /api/commands`; refused, with nothing submitted, unless a fresh read reports the pinned build, exactly that session and a held host: no host process under the supervisor slot, HEALTHY or not |
| `GET /` and the build's files | the console |

**`GET /api/snapshot` is forwarded whole.** Besides the contract object the runtime adds the keys it names in
`demo_runtime.daemon.ADDITIVE_SNAPSHOT_KEYS`, today `unresolved_command`: the one command whose execution is not known,
with the machine evidence the daemon read, the classification, the question the operator is being asked, the findings it
will accept and the commands that stay blocked meanwhile. That key sits outside the contract object because
`snapshot.schema.json` has `additionalProperties: false`. This process must not strip it, or a browser cannot explain a
block without a terminal; a client that validates against the contract strips the additive keys first.

**`POST /api/commands/{command_id}/resolve` is a statement, never a retry.** It carries no command and mints no id: the
id in the path names a command the runtime already holds. The runtime re-reads the process table, the run's own files
and the observation store, classifies what it found, records the `finding` as an attestation beside that evidence, and
decides on its own whether the block lifts; this process forwards the request and the answer and decides neither. The
answer carries `classification`, `evidence`, `cleared` and `cleared_by`, or a refusal with the runtime's reason:
`controller_alive` and `command_not_uncertain` are conflicts (409), `finding_not_allowed` and `note_not_allowed` are
about the body (400). An inconclusive reconcile is a **200** with `cleared: false` and
`reason_code: "evidence_unreadable"`, because the request was taken; reporting it as a failure would invite a second
try. The route passes the same guard as every other POST, in the same order and with the same reason codes.

**The mission is a surface of its own and is not contract v1.** A mission is not a command and a target is not a run,
so none of the five routes above adds a command type, an event kind or a snapshot key. The view carries its own
`mission_record_version`, exactly as `GET /api/cake` carries its own `cake_status_version`, and it can be revised
without touching a sealed schema. The two reads take no body and reach runtime ops that only fold a file that was
already written, so a page load or a reload starts nothing, submits nothing and resumes nothing. The three POSTs pass
the same guard `POST /api/commands` passes, through the same function, in the same order and with the same reason
codes. A runtime that has no mission surface at all answers every one of them `mission_unavailable` with a `detail`
saying which piece is missing; that is a 409, because it is a fact about the world and not about the request.

**The browser supplies a candidate key, and there is no field for anything else.** `POST /api/mission/activate` refuses
a body carrying any key besides `candidate_key`, so Plan bytes, a file path, a socket path and an admin operation have
nowhere to arrive. The bytes belong to the runtime: it read them once at start-up from the paths in its private
configuration, hashed them, and binds them to a fresh operation uuid before it submits. A key it did not prepare is
refused by name and nothing is submitted. `status ok` is not success and is never reported as one: the answer carries
the `transaction_phase` and, separately, the `activation_state`, which reaches `OBSERVED` only once the capability slot
has been read back. An answer that says `stopped_during_activation` means a stop landed while the operation was in
flight: the answer is on the record and the target does not resume.

**The capability slot check moves a Cake slot and no motor.** `POST /api/capability/check` exists so the non-motion
activation can be driven against the robot's resident while no mission exists: the mission's own activation is offered
only to a mission waiting for a capability. It carries a candidate KEY from the runtime's own list, the digest shown
beside that key and the resident session the operator was shown, and nothing else. The runtime refuses it, with nothing
submitted, for a key it did not prepare, a digest that is not that key's (`payload_digest_mismatch`), a check already
in flight (`capability_check_in_flight`), a mission or a run holding the run slot, and a fresh read of the robot that
does not report the pinned build, that session (`robot_session_mismatch`) and a HELD host (`robot_host_not_held`). Held
means no host process under the supervisor slot: one the supervisor reports in any state but HEALTHY is not held, and a
slot that is not ACTIVE is held only when its health row is missing or shows child pid 0. Then it writes the attempt,
submits one ApplyPlan, reads the slot and the host again and records the answer. Both host reads carry the supervisor
slot's instance and generation and its health row's spawns (`sequence`) and restarts (`flags`); `host_held_throughout`
is true only when both reads were held and all of those read the same, false (with `host_throughout_detail` naming
each one that differed, such as `spawns 5 to 10`) when anything was spawned or re-applied between the reads, and null
when it cannot be told. An ApplyPlan whose answer did not arrive in time is recorded `REQUEST_FAILED` with
`unresolved: true`: it may still be running, so the check stays closed to another (`capability_check_in_flight`, naming
it) for the life of that runtime, and it is never said to have not landed. The verdict is
the mission's own decider, narrowed only downward: `OBSERVED` only when the slot, read back, carries the capability, and
a resident session that changed across the operation, or a slot that did not change (capsule, generation and instance
all as before; `slot_changed` is recorded with every answer), turns that into `COMPLETED_NOT_OBSERVED`. While a check is in flight every command but
stop and every mission start is refused `capability_check_in_flight`; Stop is never delayed by it.

**A command always answers 200 with a `command_result`**, refusals included: `status: "rejected"` with the runtime's own
`reason_code` is what an operator needs to see, and it is not a transport failure. 4xx and 5xx are kept for requests that
never reached the runtime: the guard (403), a body that is not a command (400), an unreachable runtime (503),
replay-only stop (409). When the runtime answers a submit with a bare refusal rather than a `command_result`, that
refusal is turned into one with the same `reason_code`, so a client has exactly one shape to handle.

**`POST /api/stop` reads its body, and a stop is a command with an identity.** A body that is a contract `stop` command
is SUBMITTED, so the runtime journals it under the id the browser minted and answers a repeat of that id with the
recorded result instead of stopping twice; the answer carries that receipt under `command`. A body that carries no
command keeps the runtime's plain stop op, which journals nothing. Either way `ok` says whether the RUNTIME took the
request, and whether the robot stopped is a third fact this route never claims: the run's `stop.requested`,
`stop.acknowledged` and `stop.outcome` records say that. A stop that found no active run answers `ok: false` with
`no_active_run` and no stop object, because pressing the button and stopping nothing is a thing the screen must say.
The stop path has a dedicated worker thread, so it never waits behind reads queued on the loop's shared executor.

**A reused command id is answered, not guessed at.** The runtime's receipt carries `payload_digest`, the digest of the
command it recorded under that id. When a submit comes back with a digest that is not the digest of the body just sent,
the two are not the same request: the runtime started nothing (it answered out of its journal) and this process answers
**409** `command_id_reused` with both digests, rather than handing back the first command's outcome as if it were this
one's. A receipt without a digest is a runtime that cannot say, and that is reported as what it is. A command is also
preceded by one handshake when this process is bound to a runtime session, so a daemon that has been replaced since is
answered **409** `runtime_session_changed` rather than being sent a command nothing did readiness against.

**A lost answer is never a resend.** If the connection breaks after the command went out, the answer is
`status: "uncertain"`, `reason_code: "response_lost"`, and the client asks `GET /api/commands/{command_id}`.

### Streams

`id:` on an event is its seq, so a browser reconnect resumes exactly. A **resync gap is its own SSE event** carrying
`retained_from_seq` and the `missed` range, never folded into the data: a client must be able to tell "you missed 40 to
92" from "nothing happened". A heartbeat every two seconds carries no id, so it cannot move a reader's cursor. A stream
that loses its upstream ends with a named event (`runtime_unavailable`, `runtime_stream_failed`) rather than going quiet.

**A slow reader is dropped, in two layers.** Each client has its own bounded queue, and overflow drops that client. Each
write is bounded in time, because a client whose receive window is shut makes the write wait and a queue alone would
never notice. A dropped client's connection is **reset**, not merely closed: a plain close leaves the bytes already given
to the kernel queued for a peer that is not reading, so the connection lingers on zero-window probes and the client sees
a stream that never moves. Nothing of value is lost, because a client reconnects with the seq it last saw and the runtime
states the gap.

Measured on this machine, on loopback, against a client with a 2 KiB receive buffer that never reads: with the socket
send buffer left to the kernel a write first waited after 1 391 000 bytes; with it bounded here, after 131 300. The
second number is how much goes nowhere before the drop rule can fire, which is why it is bounded.

### Preview

The endpoint is fed from the runtime's latest preview record. It opens no device and invents no frame. **With the runtime
as it stands today there is no preview source attached**, so this endpoint answers 503 with the runtime's own reason
(`preview_source_not_attached`) and sends no image at all. The multipart path is implemented and tested against a runtime
that does publish frames; it has never run against a real camera.

## Where this meets apps/console-web

The two were built separately and their HTTP expectations were compared afterwards, against the console's actual source
and its actual build. What follows was checked by running both, not by reading one of them.

**Verified working against the real build**: `index.html` is served with the boot element injected, and every asset the
built page references (`/assets/index-*.js`, `/assets/index-*.css`) answers 200 with the right media type and an
immutable `Cache-Control`. The built page loads its script as an external module, which is what the policy allows.

Three things were changed **here** so that the console needs no change for them:

- **SSE frames are unnamed.** The console subscribes with `EventSource.onmessage`, which never fires for a frame that
  carries an `event:` name. Named frames would have delivered nothing, silently. Forwarding the runtime's line verbatim
  is also the more faithful design, so that is what this does.
- **The heartbeat is an SSE comment**, so it is not delivered as data that a page would have to recognise and discard.
- **`hello` carries `runtime_session` at the top level** as well as inside `runtime`, and **`POST /api/commands` accepts
  `{"command_id": id, "command": {...}}`** as well as the bare contract command.

Four things needed a change **in the console**, and all four have since been made there. They are kept written down
because each one is a rule this side still enforces and a future client would hit again:

1. **Every command carries the credential.** The page reads the boot element and sends `X-Console-Request: 1` and
   `X-Console-Credential` (`api/boot.ts`, `commandHeaders`). A command without them is refused 403
   `console_header_missing`. Weakening the guard instead is not an option: it is the whole CSRF defence.
2. **`POST /api/stop` sends a body and the json content type.** It sends a whole contract `stop` command, which is now
   what makes the stop journaled and idempotent by its own id; a body without a content type is still refused
   `content_type_not_json`.
3. **`GET /api/history` returns `{"ok": true, "runs": [...]}`**, not a bare array; `history()` reads `body['runs']`. The
   runtime's envelope is kept because a refusal (`unknown_run`) needs one too.
4. **`GET /api/history/{run_id}/events` returns `{"ok": true, "run_id": ..., "events": [...], "origins": [...]}`**, not a
   bare array. `origins` is where the controller's own seq lives, which the contract envelope has no field for, so
   flattening to an array would discard it.

One more mismatch was neither side's to fix by renaming: a history entry's start time is `started_wall_utc`, which is
what the runtime emits, and the console's parser reads that name now and falls back to `started`.

## Tests

Each file by its explicit path, from the repository root. Every child process anywhere is the repository's fake
controller. No camera, serial port, network host or robot is touched, and no hardware exists in any of it.

```bash
python3 apps/console-api/tests/test_security.py
python3 apps/console-api/tests/test_api.py
python3 apps/console-api/tests/test_mission.py
python3 apps/console-api/tests/test_resolve.py
python3 apps/console-api/tests/test_streams.py
python3 apps/console-api/tests/test_live.py
```

`test_mission.py` covers the five mission routes, and the two properties that belong to them: each of the three POSTs
is refused by the same reason code a command POST is refused by when any one of the guard's five requirements is
removed, and an activate body carrying anything besides `candidate_key` never reaches the runtime at all.

`test_security.py` and `test_api.py` and `test_streams.py` run the application in process over a stub runtime, which is
how the answers a real runtime gives only rarely (a lost submit, a resync gap, a contract mismatch) get covered.
`test_live.py` starts a real `demo_runtime.daemon --synthetic` and a real `python3 -m console_api`, and is where the
claims that span both processes are proved: the API killed mid-run, a duplicate `command_id` starting the controller
once, and the runtime refusing a blocked command while the API passes the refusal through unchanged.

Strings the repository's text gates refuse (a private address, a user name, a home path, a device id) are assembled at
run time in `tests/support.py`; no literal exists in a committed file.

## Named residuals

- **Reads do not carry the credential; only POSTs do.** A browser's `EventSource` cannot set a header, and a credential
  in a query string would reach access logs and the browser's history, so the protection on a read is the `Host` check
  plus the absence of any CORS header: another origin's page cannot read an answer, and a name that resolves to this
  loopback address is refused. What this does NOT stop is another program running as the same user on this machine,
  which can read the snapshot and the streams without the credential. Commands are a different matter and are guarded.
- **A device id, a robot id or a camera id has no generic shape**, so the last-line sanitizer in this process cannot
  recognise one. That is the runtime's allowlist and its config-derived tokens, which run before a value could reach
  here. `test_live.py` proves that end at the far side, on the fake controller's own trap fields
  (`identity_fingerprint`, `raw_frame_path`, `source_file`), which never appear in an answer.
- **The last line here removes what a generic shape can describe**: an absolute or home-relative path, a dotted quad, a
  hardware address, a `user@host` pair, plus this process's own private strings (its paths, its socket, its credential).
  The base sanitizer removes the address of a `user@host` pair before it looks for the pair, which left the user name
  behind; here the pair is taken whole first.
- **No live path.** Every test runs against `--synthetic` or `--replay-only`. The API has never spoken to a runtime
  started with `--enable-hardware-commands`, and no preview or tap source has ever been attached.
- **A stream is reopened on an idle timeout.** The runtime's client is a blocking generator with no way to interrupt it
  from outside, so a stream is opened with a short idle timeout and reopened with `since=<the last seq seen>`. A thread
  whose tab has gone ends within seconds instead of sitting in `recv` forever. The cost is one reconnection every two
  idle seconds per streaming client.
- **The API learns `data_mode` from the runtime** and uses it only to REFUSE (replay-only), never to allow. Before it has
  seen a snapshot it forwards, and the runtime refuses; the answer a client gets is the same either way.
- **A replaced runtime does invalidate an open tab.** The credential follows the runtime session, so restarting the API
  costs a tab nothing, but restarting the DAEMON rotates it: the tab is refused `credential_superseded` and must be
  reloaded to be served the new session's value and to do readiness against it. That is deliberate.
- **The session check before a command is not airtight.** The handshake and the command are two connections, so a runtime
  replaced between them is not caught by it. It closes the window that lasts as long as this process has not noticed,
  which is the one that lasts minutes; the credential rotation closes the rest within one snapshot poll.
- **`first_seen_wall_utc` is absent on a receipt for a command the running daemon admitted itself.** The runtime's live
  command table is updated in memory and the writer does not keep its own stamp there, so the field appears only once
  the journal has been folded again. Absent means not recorded, and no client fills it in from its own clock.
- **`Host` must be the literal bound address.** Browsing to `http://localhost:8770` is refused with `host_not_allowed`.
- **The `unittest discover` convenience is unverified**: the tests were run by explicit path only, as required while
  other units' tests were being written in the same tree.
