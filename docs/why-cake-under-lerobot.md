<!-- Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE. -->

# Why Cake sits under the LeKiwi teleoperation host

This page compares three ways of running the same LeKiwi host on the
robot: by hand in a terminal, under a plain systemd service, and under
Cake. Each is given its due. The page is held to the same standard as
the demo record: every capability sentence about Cake is one of the rows
in `docs/claims.md`, cited inline, and where a thing was not measured,
the page says so.

## The setup

A LeKiwi robot: a Raspberry Pi 5, one servo controller driving a
six-joint SO-101 arm and three wheels, two cameras. Teleoperated from a
laptop with an SO-101 leader arm over LeRobot's own ZMQ protocol on
ports 5555 and 5556. The laptop side is unchanged in protocol and ports.
It runs LeRobot's own `LeKiwiClient` class through
`bin/laptop/teleop.py`, because `lerobot-teleoperate` at the pinned
commit (b4e2d0b) has no LeKiwi client type; nothing on the laptop knows
Cake exists.

In every setup below the robot runs the same control program, the
LeKiwi host, which owns the serial bus and the cameras and speaks the
wire protocol. What differs is what runs the host.

## 1. The host by hand, in a terminal

You ssh in and start the host. Its lines go to that terminal, and that
is the log.

The pinned host is sturdier than "a single process" suggests. An error
while handling one message is logged (`Message fetching failed: ...`)
and the loop goes on to the next message. What ends the host is a crash
outside that loop, a signal, the terminal going away, or its own
connection time elapsing: after `--host.connection_time_s` it prints
`Cycle time reached.` and exits by itself.

- Restart: none. Someone notices, sshes in, and starts it again.
- Logs: the terminal, for as long as its scrollback lasts.
- Identity: the pid, and nothing else.

For a bench rig this is fine, and it is how the runbook has you prove
the host once before anything else runs it
(`docs/reproduce-end-to-end.md`, section 4d).

## 2. The host under a plain systemd service

A user unit whose `ExecStart=` names the host, with `Restart=on-failure`
or `Restart=always` and a `RestartSec=`. This is a real step up, and it
costs one file.

- Restart: a crashed or killed host is started again after `RestartSec`,
  as often as `StartLimitBurst` allows within `StartLimitIntervalSec`;
  under `Restart=always` a host that exits by itself at its connection
  time is started again too.
- Logs: journald keeps the host's stdout and stderr with timestamps,
  and the unit's own lifecycle lines (started, exited with its status,
  scheduled restart), readable with `journalctl --user -u <unit>`.
- Identity: the unit name, `MainPID` and `NRestarts`, from
  `systemctl --user show <unit>`.

What it does not check or keep: it starts whatever file `ExecStart=`
names, unverified byte for byte; every restart is a restart of the whole
thing, since there is only one thing; and beyond the unit file there is
no record of what the host was started from.

## 3. The host under Cake

Cake runs on the robot beneath the same host. It does not replace the
host and it does not touch the wire protocol. The resident starts with
a signed package admitted and launches the host as its supervised
child, owning its lifecycle (`docs/claims.md` row 4). In segment 2 the
resident itself runs under a systemd user unit with a restart on
abnormal exit, so setup 2's restart is still there, one level down, for
the resident.

- Restart: when the host dies, the supervisor spawns a fresh host under
  the next restart ordinal, with the exit and the restart in the event
  trace (`docs/claims.md` row 6). SIGINT or SIGTERM to the host alone
  does not stop the demo; the supervisor respawns the host
  (`docs/claims.md` row 11).
- Logs: the resident's own lines reach journald as in setup 2. What
  Cake keeps of the host's life is a structured event trace, read over
  a local admin socket beside two other read queries and decoded by
  name (`docs/claims.md` row 5).
- Identity: a session identity, the Plan digest, the configuration
  identity, the build identity and the target-profile digest, read back
  over the socket. When the resident is killed with SIGKILL, it comes
  back from the same configuration with a fresh session identity while
  the other four stay identical, and the killed resident's host does
  not survive it (`docs/claims.md` row 7).

## What Cake adds

- Signed admission. The host runs as the child of a package built and
  signed on your machine with a key you generate, and a Plan names that
  package by its content identity (`docs/claims.md` row 1). An unsigned
  copy of the package is refused, and so is a copy signed with a key
  the resident does not trust (`docs/claims.md` row 3).
- Tamper refusal. When one byte of the stored package is changed, the
  resident refuses to start and names the content identity; restoring
  the byte clears the refusal (`docs/claims.md` row 2).
- A declared-identity relaunch. After SIGKILL of the resident: a fresh
  session identity, the four declared identities identical, no orphaned
  host (`docs/claims.md` row 7).
- Decoded lifecycle events over a socket. Three read queries, printed
  as a live table with every event decoded by name (`docs/claims.md`
  row 5).
- The safe-stop hook. A command you configure runs once per observed
  child exit (`docs/claims.md` row 9). The demo shows that it ran, not
  what it did: the shipped template writes one log line, and anything
  that touches the robot is yours to write and to test on a stand.
- A clean stop on request, closing the socket and the host with it
  (`docs/claims.md` row 8).

## What Cake does not add

- A torque concept. The host re-enables torque on every connect,
  recovery included, and Cake has nothing to gate that with:
  actuator-safe recovery is not demonstrated (`docs/claims.md` row 10).
  That is why segment 2 runs on a stand under `SAFETY.md`.
- A live replacement of a running module (`docs/claims.md` row 12).
- A log store for the host's lines. If you want them kept, setup 2
  keeps them.
- A stop of anything that touches the robot. When the host dies,
  nothing here stops the wheels: the configured safe-stop command runs
  once per exit (`docs/claims.md` row 9), but the template this release
  ships only appends a log line, and anything that actually stops a
  motor is yours to write and test on a stand. That is why segment 2
  always runs with the wheels off the ground.

## The one-line version

Same laptop, same robot, same open protocol. By hand you get the host
and nothing else; under systemd you get restarts, timestamps and a unit
name; under Cake you get a supervisor admitted only from a signed package
(`docs/claims.md` row 1), restarts that carry an ordinal
(`docs/claims.md` row 6), a relaunch that names its identities
(`docs/claims.md` row 7), and an event trace read over a socket
(`docs/claims.md` row 5). In all three, the stand does the safety work.
