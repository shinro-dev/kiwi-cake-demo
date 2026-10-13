<!-- Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE. -->

# Where the observation gaps are not, and what is left

A bounded idle comparison, run 2026-09-23 under a temporary maintenance window. It narrows where the long
observation-delivery gaps are made. It does not name their source and it qualifies nothing.

## What was run

`tools/observation_cadence_probe.py` at revision `4805077`, file sha256 `e1f0cbde98b5ea5c...`. It measures RECEIVER
CADENCE: the intervals between the moments observations reach the process running it. It is NOT the instrument that
produced the 2026-09-22 A/B/A series, and these numbers are not part of that series.

## The conditions, equal in every way that was controlled

| | |
|---|---|
| duration | 45 s each, run one after the other, not simultaneously |
| host | the NORMAL host, untouched, acquisition preserved |
| cameras | the wrist-only camera service running, session `cs-20260923T080554Z-38c37522`; FRONT OFF in both |
| controller | none running, in either condition |
| command admission | removed at the runtime: the daemon was restarted WITHOUT `--enable-hardware-commands`, so the CLI and any other client of its socket were refused too, not only the browser. Verified by submitting a cycle from the CLI and watching it be refused. |
| other consumers | checked on the robot: **zero** connections to the host's observation port before each run. The `--sole-consumer` flag is a declaration; this is the check. |
| cleanup | a shell trap restored the normal daemon and the API in both runs; command availability came back each time |

The observation socket is PUSH/PULL, so the probe was the only consumer by construction once the count was confirmed
at zero.

## The result

| | A: on the robot, loopback | B: from the laptop, over the LAN |
|---|---|---|
| observations received | 1348 | 1348 |
| rate from arrivals | 29.931 Hz | 29.939 Hz |
| median interval | 33.02 ms | 33.25 ms |
| p95 | 34.04 ms | 37.92 ms |
| p99 | 35.00 ms | 42.77 ms |
| **longest interval** | **35.99 ms** | **226.78 ms** |
| intervals at or above 100 ms | **none** | **six**: 226.8, 226.6, 226.1, 225.4, 220.1, 219.0 |

## What this establishes, stated exactly

**These were two separate windows, not one stream measured at both ends.** The observation socket is PUSH/PULL and
each run was the sole consumer of its own window, so no message in condition A is a message in condition B. Nothing
below is a per-message comparison, and no message was followed from the host to the laptop.

**Loopback delivery was regular during its 45-second window.** On the machine that produces them, the observations
arrived at a steady 30 Hz with a worst interval of 36 ms, about one frame period, and no excursion at all.

**Laptop delivery showed long gaps during a separate 45-second window.** Six waits of 219 to 227 ms, the magnitude
the earlier A/B/A reported (246.46, 243.92, 291.79 ms).

**The front camera is not NECESSARY for these gaps to occur.** It was off in both windows and the long waits still
appeared in the laptop window. That is the whole of the claim. It does NOT exclude an additional effect when the
front camera is enabled, and it does not establish the exact source of the delays.

**Internal publication timing and loss remain unmeasured.** The host supplies no timestamp and no sequence number, so
nothing here measures when it published, and a dropped sample cannot be told from a late one at either end. A regular
arrival pattern at loopback is consistent with regular publication; it does not prove it.

## What this does NOT establish

- **It is not a qualification of the path the controller uses.** The controller runs on the laptop and reads the LAN
  path, so those 219 to 227 ms waits are real for it. Loopback narrows where a delay is made; it does not make the
  laptop path acceptable, and the documented criterion is still exceeded on the path that matters.
- **It does not name the mechanism.** LAN, the ssh forward, and the laptop's own scheduling are all still candidates,
  and this comparison does not separate them. The stock host neither stamps nor sequences observations, so even here
  a dropped sample and a late one cannot be told apart.
- **The two conditions were sequential.** They are not the same 45 seconds, so a transient affecting only one window
  is not excluded. They were adjacent and the host configuration did not change between them.
- **It says nothing about the front camera's own prerequisites.** Front streaming stays disabled until those are
  satisfied. Nothing here qualifies it.

## The endpoints, traced, and what that eliminates

Read from the code rather than assumed, because SSH having launched a remote process is not evidence that any
measured path traverses SSH.

- **The controller's observation path is DIRECT over the LAN.** The supervisor passes `--remote-ip <address>` to the
  controller (`supervisor.py:199`), which builds its transport against that address
  (`run_single_cube_cycle.py:2068`). No forward is involved. The runtime's forwards exist for the control socket, the
  tap and the previews; this configuration has `tap: null` and the observation stream is not among them.
- **Condition B is therefore the controller's own path**, not an approximation of it. The 219 to 227 ms waits are
  waits the controller would have experienced.
- **Condition A attached to the robot's own loopback.** SSH launched that process and then had nothing to do with
  it: a remote process started over SSH talks to a local socket directly.

**SSH is eliminated as a candidate.** Neither measured path carried observation bytes through an SSH forward. What
remains is the LAN between the two machines, and the laptop's own receiving and scheduling. Those two are what a
further comparison has to separate, and separating them is the next experiment, not another repetition of this one.

## A separate thing that was confused with this

**A browser Reload does not restart a dead preview forward, and never could.** The forward is an `ssh -N -L` child of
the runtime daemon, opened once at daemon start and deliberately never restarted
(`packages/demo-runtime/demo_runtime/forwards.py`: an ssh that keeps dying is a fact an operator has to see, not a
loop to hide it in). A reload gets the TAB a current credential; it does not touch the runtime's children.

The actual recovery for a dead preview, in order:

1. `bin/pi/start-camera-service.sh lock-status`, then `status`. This opens no camera and starts nothing.
2. If no service answers, start it: `bin/pi/start-camera-service.sh start`, wrist only, front NOT enabled.
3. If the service is running and the console still shows no preview, the FORWARD is dead, not the camera. Restart the
   runtime daemon with its original arguments; it opens its forwards at start. The API can be restarted with it.
4. The console's own signal for this is the camera card reading no frame while the runtime keeps answering. That is
   what the frame-age and line-age figures were separated for: a dead forward shows as lines arriving and no frames.
