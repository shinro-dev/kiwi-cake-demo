<!-- Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE. -->

# Stopping and cleanup

This is the ordered exit procedure for segment 2, with expected motor behavior
after each step, the commands that prove nothing is left, the removal of
the user unit, what to do if a stop hangs, and the emergency stop. The
other documents point here rather than repeating it.

Software cleanup checks establish process, socket and listener state.
They do not measure torque. Inspect host shutdown errors and check the
supported robot's physical state separately. For a failure report, retain
the relevant output using [Diagnosing a run](diagnosing-a-run.md).

Segment 1 needs none of this: its runner stops its own resident at the end
of the run and on any failure, and `bin/demo-stop.sh` covers a run that was
interrupted. Nothing in segment 1 can move anything.

## The exit sequence, in order

| Step | Where | What you do | Expected behavior and checks |
| --- | --- | --- | --- |
| 1 | the leader arm | Move the leader so the follower rests low and physically supported. | Torque on; the follower holds the parked pose under the host's torque. |
| 2 | the laptop | Ctrl-C the client (`bin/laptop/teleop.py`). Expected line: `teleop: client closed; the follower holds its pose under the host's torque until the host exits`. | The client closes its sockets; it does not disable host torque. The host's command watchdog depends on its loop continuing to execute and is not an independent motor-stop guarantee. |
| 3 | the Pi | Stop the resident: press Enter at the runner's STOP beat, or run `bin/demo-stop.sh` at any other time. Expected: `stopped: no resident, no host, no socket, no listener` from the runner, or `kiwi-cake: stopped kiwi-cake-demo.service` then `kiwi-cake: done (1 stop action(s)): no resident, no supervised child, no socket` and exit 0 from the stop script; the stop script exits 1 and prints `kiwi-cake: FAIL:` naming what is left when the stop did not complete. | systemd's stop signals the resident only (`KillMode=mixed`, set in the unit), and the resident's own shutdown quiesces the host: SIGINT to the host, then a bounded deadline, then force. A host that completes its disconnect with `--robot.disable_torque_on_disconnect=true` releases torque, so the arm must already be parked and supported. An error or forced termination can interrupt disconnect; torque may remain. Inspect shutdown output and check the robot. |
| 4 | the Pi | Run the checks below. | Unchanged. |

Never signal the host directly. SIGINT or SIGTERM to the LeRobot host alone
is a child exit to the supervisor, which can respawn the host, and the
fresh host enables torque on connect (`docs/claims.md` row 11). The only
correct stop is the resident's, which closes the socket and the child with
it (`docs/claims.md` row 8).

## Prove nothing is left

From the checkout root, every line is expected to print nothing, or the
state named in its comment:

```
systemctl --user is-active kiwi-cake-demo.service                # inactive
pgrep -af cake-resident                                           # nothing
pgrep -af lekiwi_host                                             # nothing
bash -c '. bin/lib/common.sh; ls "$KC_RUNTIME/admin.sock"'        # No such file or directory
ss -tln | grep -E ':555[56] '                                     # nothing
```

The socket path is taken from `bin/lib/common.sh` (`KC_RUNTIME`), the same
source every script uses, rather than restated here: it is
`$XDG_RUNTIME_DIR/kiwi-cake-demo/admin.sock` for a logged-in user (on the
tested board, `/run/user/<uid>/kiwi-cake-demo/admin.sock`), with a fallback
under `/tmp` when `XDG_RUNTIME_DIR` is unset.

`bin/demo-stop.sh` may be run again at this point; it reports
`kiwi-cake: kiwi-cake-demo.service is installed and not active` and
`kiwi-cake: done (0 stop action(s)): no resident, no supervised child, no
socket` when there was nothing to stop, and exits 0. It exits 1 with one
`still alive: <pid> <cmdline>` line per process and `kiwi-cake: FAIL: <n>
process(es) still in the process table after the stop` when a host, a
stub or a resident of this checkout outlived the stop, or `kiwi-cake:
FAIL: kiwi-cake-demo.service is still <state> after <n> s` when the unit
did not stop within `KC_STOP_DEADLINE` seconds (default 75); that is a
finding to report (below), not something to work around. It always
prints `kiwi-cake: motor state is your host's own disconnect behaviour
and is not visible to this script; check the robot` before its last
line.

## Remove the user unit

When you are done with segment 2 on this board:

```
systemctl --user disable --now kiwi-cake-demo.service
rm ~/.config/systemd/user/kiwi-cake-demo.service
systemctl --user daemon-reload
```

The runner reinstalls the unit from its template on the next segment 2
run, so removing it costs nothing.

## If a stop hangs

The clean stop is bounded on both sides. The resident gives the host its
stop signal and a bounded deadline before ending it by force
(`SAFETY.md`). The unit the runner installs sets `KillMode=mixed` and
`TimeoutStopSec=60`: on `systemctl --user stop`, systemd sends SIGTERM to
the resident only, never to the host, and waits up to 60 seconds (a
configured wait written in the unit, longer than the resident's own
bounded teardown, not a measurement) for the resident's shutdown to
finish; only then does it end whatever is left in the unit's control
group with SIGKILL. While the resident lives, the host hears only from
the resident; a host that is slow to exit is still ended by the
resident's force after its deadline, and then torque may remain. Wait it
out with your eyes on the arm and a hand near power, then run the checks
above.

A host process that outlives its resident is a finding to report, not
something to work around: note the output of the checks and of
`journalctl --user -u kiwi-cake-demo.service --no-pager | tail -n 40` (or
of the system-journal read named under "What was measured", when that
command answers `No journal files were found`).
End such a process by its pid only with the robot on the stand, the wheels
off the ground and a hand at power, because a host that is killed does not
run its disconnect and leaves the servos as they were.

One message in that journal is cosmetic: on the tested board the resident's
own shutdown completed and printed `stopped` before systemd's cgroup
teardown found and killed one lingering ZeroMQ background thread of the
host. The devices were free. Under the current unit, a `Killing process`
line that appears after the resident's `stopped` line is that same case;
one that appears before it is a finding to report.

## What was measured

`tests/unit-stop-signals.sh` is the torque-free measurement behind the
two unit settings above. It installs the unit template around a real
resident whose child is a stub of segment 1's shape (a line-printing
script that logs every signal it receives; no device is named or
opened), starts the unit with `systemctl --user`, stops it with
`systemctl --user stop`, and asserts, in this order:

- the stop command returned 0;
- `systemctl --user show` reports `Result=success`, `ExecMainCode=1`
  and `ExecMainStatus=0` for the unit (1 is `CLD_EXITED`, the `exited`
  of `systemctl status`): the resident caught SIGTERM and exited 0
  rather than dying of it. This needs no journal. It is read before the
  test's cleanup, while a runtime `Wants=` from `default.target` keeps
  the stopped unit loaded: systemd unloads an inactive unit nothing
  references, and a unit loaded again from disk reports no exit at all;
- the stub logged `stub got SIGINT`, logged `stub exit path ran`, and
  logged no `stub got SIGTERM`. Only the resident can send SIGINT under
  a unit whose stop signal is SIGTERM, so the SIGINT line is the
  resident's, and the absent SIGTERM line means systemd signalled
  nothing while the resident lived;
- when this user can read the unit's journal: it carries
  `cake-resident: stopped`; it carries the resident's `shutdown drain`
  line with `deadline_exhausted=0` before that stopped line; it carries
  no `Killing process` line for the stub's pid. When the journal cannot
  be read, each of these three prints a `SKIP` line naming that reason
  and none of them passes; the stub log and the `systemctl show`
  reading remain the proof that no systemd signal reached the child and
  that the resident exited on its own;
- the unit is `inactive`, no process naming the run directory remains,
  and the admin socket is gone.

It prints two artefacts, the journal excerpt (with the command that
read it) and the stub log, and ends on `unit-stop-signals: PASS`, or on
`unit-stop-signals: PASS with <n> skipped check(s)` when the journal
could not be read. The stub is not the LeRobot host: the measurement
says nothing about a host's disconnect finishing inside the resident's
deadline, and nothing about torque.

The board record of the ordinary stop, from the tested Raspberry Pi 5 on
2026-09-13 under this unit template, read from the system journal with
`_SYSTEMD_USER_UNIT=kiwi-cake-demo.service` alone, which carries the
resident's own lines and none of the manager's: the resident's drain
line, `shutdown drain barrier_violations=0 deadline_exhausted=0
destroyed_after_drain=0 destroyed_before_drain=0 drained=1 live=1
members=1 quiesce_calls=1 quiesced_or_inactive=1 required=1`, then
`cake-resident: stopped`. The resident's own drain ran to completion,
its deadline not exhausted, before its stopped line. Whether systemd
then ended anything is not in that record: the manager's `Killing
process` lines carry `USER_UNIT=`, not `_SYSTEMD_USER_UNIT=`, and the
read below, the one the test makes, is the one that carries them.

On that board `journalctl --user -u kiwi-cake-demo.service` answers
`No journal files were found` (journald keeps no per-user journal there)
while the unit's lines are in the system journal. Read them with the
four matches `--user -u` applies to a service (the unit's own processes,
the user manager's lines about it, coredumps of it, and other processes'
lines about its processes), which a member of `adm` or `systemd-journal`
reads without sudo, and which the test falls back to by itself:

```
journalctl _SYSTEMD_USER_UNIT=kiwi-cake-demo.service _UID=$(id -u) \
  + USER_UNIT=kiwi-cake-demo.service _UID=$(id -u) \
  + COREDUMP_USER_UNIT=kiwi-cake-demo.service _UID=0 _UID=$(id -u) \
  + OBJECT_SYSTEMD_USER_UNIT=kiwi-cake-demo.service _UID=0 _UID=$(id -u) \
  --since '<start>' -o short-monotonic --no-pager
```

`tests/unit-stop-signals.sh --template <path>` repeats the capture under
another template. Under a template that sets no `KillMode`, systemd's
default `control-group` sends SIGTERM to every process in the control
group, the child included, and the stub logs `stub got SIGTERM` beside
the resident's `stub got SIGINT`: the stub's handlers only log, and its
exit path runs a moment after the first signal, so a second signal that
lands in the same instant is recorded rather than lost to a handler
that exited first. The measurement records which signals arrived, never
a duration. See [configured waits and performance limits](../LIMITATIONS.md#configured-waits-are-not-performance-measurements).

## Emergency stop

Any unexpected motion, any wheel movement, any re-arm you did not expect,
or anything that makes you uneasy: stop the resident at once
(`bin/demo-stop.sh`), and if that is not fast enough, cut power. Then
report what you saw rather than smoothing it over. `SAFETY.md` is the
authority for this rule and for the preconditions that make it rare.
