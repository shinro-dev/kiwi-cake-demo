<!-- Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE. -->

# Stopping and cleanup

This is the one ordered exit procedure for segment 2, with the motor state
after each step, the commands that prove nothing is left, the removal of
the user unit, what to do if a stop hangs, and the emergency stop. The
other documents point here rather than repeating it.

Segment 1 needs none of this: its runner stops its own resident at the end
of the run and on any failure, and `bin/demo-stop.sh` covers a run that was
interrupted. Nothing in segment 1 can move anything.

## The exit sequence, in order

| Step | Where | What you do | Motor state afterwards |
| --- | --- | --- | --- |
| 1 | the leader arm | Move the leader so the follower rests low and physically supported. | Torque on; the follower holds the parked pose under the host's torque. |
| 2 | the laptop | Ctrl-C the client (`bin/laptop/teleop.py`). Expected line: `teleop: client closed; the follower holds its pose under the host's torque until the host exits`. | Torque on. The client only closed its sockets; the host's watchdog stops the base within its timeout; the arm keeps holding. |
| 3 | the Pi | Stop the resident: press Enter at the runner's STOP beat, or run `bin/demo-stop.sh` at any other time. Expected: `stopped: no resident, no host, no socket, no listener` from the runner, or `kiwi-cake: stopped kiwi-cake-demo.service` then `kiwi-cake: done (1 stop action(s))` from the stop script. | The resident's own shutdown quiesces the host: SIGINT to the host, then a bounded deadline, then force. A host that exits on the SIGINT runs its disconnect and releases torque (`--robot.disable_torque_on_disconnect=true`), so the arm goes limp: it must already be parked. A host ended by force after the deadline did not run its disconnect, so torque may remain: check the robot. |
| 4 | the Pi | Run the checks below. | Unchanged. |

Never signal the host directly. SIGINT or SIGTERM to the LeRobot host alone
is a child exit to the supervisor, which respawns the host at once, and the
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
`kiwi-cake-demo.service is installed and not active` and `done (0 stop
action(s))` when there was nothing to stop, and it warns if a supervised
child process is still in the process table.

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
stop signal and a bounded deadline before ending it by force (`SAFETY.md`);
`systemctl --user stop` waits for the unit's stop timeout, which is
systemd's `DefaultTimeoutStopSec` (90 seconds unless your systemd is
configured otherwise; a systemd figure, not a Cake one), and then ends what
is left. Wait it out with your eyes on the
arm and a hand near power, then run the checks above.

A host process that outlives its resident is a finding to report, not
something to work around: note the output of the checks and of
`journalctl --user -u kiwi-cake-demo.service --no-pager | tail -n 40`.
End such a process by its pid only with the robot on the stand, the wheels
off the ground and a hand at power, because a host that is killed does not
run its disconnect and leaves the servos as they were.

One message in that journal is cosmetic: on the tested board the resident's
own shutdown completed and printed `stopped` before systemd's cgroup
teardown found and killed one lingering ZeroMQ background thread of the
host. The devices were free.

## Emergency stop

Any unexpected motion, any wheel movement, any re-arm you did not expect,
or anything that makes you uneasy: stop the resident at once
(`bin/demo-stop.sh`), and if that is not fast enough, cut power. Then
report what you saw rather than smoothing it over. `SAFETY.md` is the
authority for this rule and for the preconditions that make it rare.
