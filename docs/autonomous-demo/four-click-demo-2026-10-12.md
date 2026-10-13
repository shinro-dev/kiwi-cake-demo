<!-- Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE. -->

# The four-click demo, 2026-10-12

What ran on the robot on 2026-10-12 from the operator console in a browser, with the four buttons the operator ruled on
2026-10-11: Confirm this robot, Start, Enable VLA, Start (Confirm this robot opens a dialog that also asks for a tick
and a confirm). Mission 1 is the runtime's scripted pick with the runner's scripted carry; mission 2 is the SmolVLA
policy on the arm. The screen is the show screen (`console-redesign-2026-09-27.md`, section 13); the runner is
described in `vla/README.md`.

This segment is not the host-supervision demo that the later sections of `README.md`, `LIMITATIONS.md` and
`SAFETY.md` describe: here Cake also holds a mobile approach capability, a learned policy drives the arm, and the robot
drives on the floor.

Times are UTC. Sources: the runtime's mission states and the VLA runner's events, read during the session (mostly by a
monitor polling every 3 s); the frames the runner keeps and wrist frames taken after a run; the operator's screen
recording of the last run (times in it are positions in the recording, m:ss); the code and commits named; and operator
statements, marked as such. The monitor log, the frames and the session's notes are not published. Runs 1 to 4 were
clicked by the session's browser automation; the recording was clicked by the operator. Commit ids cited here name commits of the development history, which is not published: each published change is one commit.

## 1. What was installed

| Part | Version |
| --- | --- |
| Runtime release on the robot | 4508a79 (branch `fix/latch-margin-20261009`, not in this branch's history). By operator ruling of 2026-10-09 the gripper's empty-close margin is 0.73, so a jaw reading above 67.53 on the gripper's 0 to 100 scale counts as holding something (67.67 before). It sits on dfe2bf6, the 0.5 deg wrist-flex ceiling margin (operator ruling 2026-10-08). |
| VLA runner on the robot | `vla/` of this branch, deployed with `tools/vla/deploy-pi.sh`: before fe9bdbf for run 1, fe9bdbf for run 2, 06de776 for run 3, cd72c96 from run 4 on. |
| Console on the desktop | `apps/console-web` and `apps/console-api` built from 0c9bd91. |
| Policy | SmolVLA checkpoint with sha256 `a8e140720bf3cdfa...` (`vla/envelope.json`; the checkpoint and its 30 recorded takes are not published), inference on the robot's CPU. |
| Cameras | The wrist camera only (`camera-service.md`, entry of 2026-10-11). |

Before the mission 1 runs at 12:38 and 13:03 and in the recording the block was set at its start spot, centred about
7 cm before the pad's near edge (the scripted pick's window, measured 2026-10-08). Before VLA runs 1 and 2 and in the
recording it was put back by hand at about the same spot; in the recording the console asks for that once the module
is enabled ("Put the block back at its start, then press Start."). No record says the block was moved before VLA runs
3 and 4: the last records put it at its start spot after run 2 (operator) and on the floor in front of the pad after
run 3 (the wrist view after that run), a spot nobody measured.

## 2. The runs

| Time | Run | What happened |
| --- | --- | --- |
| 12:38:52 | Mission 1, `mission-b07b648ca351` | SEARCH, then the mission stopped at the capability boundary waiting for Cake (`WAITING_FOR_CAPABILITY`, 12:39:10). The console's demo director (code in the browser tab that, after the first Start, sends Activate, Stop and the carry on its own) pressed Activate on the prepared Cake Plan with the role `activate` (the runtime prepares two; the other, with the role `refused-demo`, is one Cake is expected to refuse), Cake activated it, and the mission ran again at 12:39:13, in the same runtime session, with nothing restarted. Pick, then the hold with the block held (`hold_observe_no_pad_frame`, 12:41:43), the director's Stop (12:41:46) and the runner's carry: jaws opened at the release pose (12:41:57), done at 12:42:06, 0.101 m out and 0.102 m back by the base's own wheel readings. |
| 12:49 | VLA run 1 | At 127 s the runner's dead-man stopped the episode, "no status poll for 6 s". The session attributed the missing polls to its automation tab running in a background Chrome window, where timers are throttled; the gap was not measured. No grasp. fe9bdbf then raised the dead-man to 90 s. |
| 13:03:07 | Mission 1, `mission-14950e0f3fc4` | The monitor, polling every 3 s, saw no wait for Cake between the mission's first runtime run, the search (13:03:09), and its second, the pick (13:03:27); no activation record of this mission was read. Hold (13:06:01), the director's Stop (13:06:03), the carry done at 13:06:25, 0.103 m out and 0.101 m back. |
| 13:11 | VLA run 2 | Latched at 70.4 s; the jaw reading, 67.081, was below 67.53, so the runner called the close empty (`grip_empty`), reopened the jaws and the policy went on. Latched again at 203.8 s; `object_secured` at 214.8 s, retention 0.975. Released by rule at 303.4 s, done at 312.4 s. The grip frames at 204 to 215 s show the block held in the jaws; after the run the block was at its start spot (operator: "the VLA failed to pick up the block. it is still on the starting position"). This version kept no frame at the release, so when the block left the jaws is not known. 06de776 then added the grip watch (after the grip check the jaw reading is checked at every chunk boundary, and a block that leaves the jaws is said and may be grasped again) and a frame at every release. |
| 13:52 | VLA run 3 | Latched at 144.9 s; `object_secured` at 152.0 s, retention 0.905, jaws 67.702. Released by rule at 225.6 s, done at 234.5 s. The release frame shows the block held over the pad's near edge; after the run it lay on the floor in front of the pad. The fall itself was not seen, and the grip watch did not trigger. cd72c96 then added a set-down before the rule's release: up to five straight-down steps of 8 mm through the runtime's own step. |
| 14:36:54 | VLA run 4 | Latched at 188.1 s; `object_secured` at 195.2 s, retention 0.965, jaws 67.702. The set-down took no step: the runtime refused the first, "this step targets the tool -16.9 mm relative to the PROVEN grasp height: never below it". Released by rule at 240.0 s, done at 248.8 s. The wrist frame after the run shows the block on the pad between its logos. |
| 15:44 | Recording attempt | Runtime restarted (new session) and runner restarted cold for the operator's recording. A status read at 16:08 showed the last VLA run, clicked by the operator: 8 chunks, one latch that read empty with the jaws reopened, then the operator's Stop; no grasp. The runner keeps only its last run, so whether that attempt had other VLA runs is not known; its mission 1 was not watched. |
| 16:10 | The recording | Runtime and runner restarted again at 16:08 (a fresh runtime session; the runner in standby with no model loaded). The operator clicked the four buttons in their own tab and recorded the screen. Operator statement: "full end to end demo was successful". Section 3 is what the recording shows. |

## 3. The recorded run

The recording is 9:52 long, uncut, in real time. The circle at the lower left is the recording's webcam, looking down
at the robot and mirrored left to right; it is not a robot camera.

- **0:07 to 0:09, Confirm this robot.** The dialog names the resident session and the host instance; after the tick
  and the confirm the verdict reads Ready.
- **0:17, Start.** Mission `mission-19a339e153eb` runs. The Cake region's live slot reads `mobile_approach, active,
  generation 2` from the first frame to the last; from 0:20, once the mission is read, the region reads "No Cake
  activation in this mission." and "No record of this mission or of this resident session says what set it." The
  capability was already active, the mission did not wait for Cake, and no activation happens in this recording. The
  Find stage enters at 16:10:23, resets at 0:41 and enters again at 16:10:44: the mission's first runtime run (the
  search) ended at the capability boundary and its second (the pick) began, the same step the monitor saw at 12:39 and
  13:03.
- **Mission 1.** Pick enters at 16:10:57. The arm moves over the block, aligns in small steps and closes on it at 2:08
  to 2:09; the grip check's three lifts follow. Carry enters at 16:12:35. At 3:05 the Carry row reads "Failed here" for
  about a second: the runtime's run has stopped at the hold where, with the block held, it cannot measure the pad,
  although the pad's near edge is in the wrist view (`hold_observe_no_pad_frame`). The director's Stop (3:06) ends the
  runtime's mission, which the runtime records as stopped, and the runner's carry takes over: two forward pulses of
  0.05 m (3:12 and 3:14), the block lowered over the pad (3:17 to 3:21) and let go between its logos (3:22), the arm
  back, and the base rolled back (the screen shows "Back: pulse 1, 0.05 m"; the carry sends two backward pulses).
  "Mission 1 complete" at 3:29 is the console's word for the carry finishing.
- **3:36, Enable VLA.** "Loading the VLA module on the robot." The runner loads the checkpoint from the robot's own
  disk, copied there earlier by `tools/vla/deploy-pi.sh`: no model file is sent at the click, only the runner's `swap`
  request. At 3:42 to 3:43 the operator's hand moves the block from the pad back to the floor at about its start spot.
  The runner moves the arm to the VLA start pose, and at 4:24 the module is enabled.
- **4:25, Start.** The policy drives the arm. The screen counts the chunks under the stage named Find until the jaws
  latch, then says "Found the block": those are the console's words for the chunks before the first latch, not a
  search; the block was in the wrist view, between the open jaws, from 4:24. The jaws close on the block at 7:11.
  "Grip confirmed (0.94)" at 7:22: the jaw reading passed the runtime's held-or-empty rule, and 0.94 is the runtime's
  retention score, a similarity between two wrist frames taken at different heights during the grip check's lifts,
  from -1 to 1 and passing at 0.5; it is not a probability (on 2026-10-09 an empty close scored 0.883). The block is
  carried over the pad, `set_down` shows at 9:36 (its step count was not read), the jaws open by rule, and at 9:45 the
  card reads "Mission 2 complete" with "The VLA policy carried the block to the pad. grip confirmed; jaws opened by rule
  at the pad; 20 chunks in 319 s." The wrist camera and the webcam show the block standing on the pad. The base does
  not move during mission 2: every command the runner sends during the episode carries base velocity 0, so the policy
  cannot drive the wheels.

The front camera tile reads "Connecting" and "No picture from this camera: no_frame_yet." throughout: the front camera
is off. After each mission the top bar reads "Steps first" with "Open the gripper by hand and take the block out."
although the jaws are empty: that line is read from the runtime's mission record, which ends at the director's Stop
with the block held (`console-redesign-2026-09-27.md`, 13.8).

## 4. Where Cake is in this

- Cake holds mission 1's mobile approach capability: the runtime's mission requires it before it picks. In this scene
  it never moves the robot: every mission 1 on record that day stopped at the hold before any base move of its own,
  and the forward and backward pulses are the runner's scripted carry, which runs beside Cake and is not a Cake
  capability.
- The only Cake activation observed that day is the first mission 1 above: `mission-b07b648ca351` waited for the
  capability at 12:39:10, the director pressed Activate on the prepared Plan with the role `activate`, Cake activated it,
  and the mission ran on at 12:39:13 in the same runtime session, nothing restarted.
- The later mission 1 runs on record (13:03 and the recording) went through the boundary with no wait that the monitor
  or the screen shows; the mission 1 of the 15:44 attempt was not watched.
- The VLA module is not a Cake module. Enable VLA is the runner's `swap`: the runner, a process on the robot beside the
  runtime, the Cake resident and the host, loads the checkpoint from the robot's disk, and none of them restarts
  (`vla/runner.py`, `vla/README.md`). During mission 2 the Cake region still reads "No Cake activation in this
  mission.", but that line is about the runtime's record of mission 1: mission 2 has no runtime mission record, so the
  line says nothing about the VLA load either way.

## 5. Residuals

- **The VLA placed the block in two of the six VLA runs on record that day.** One was stopped by the 6 s dead-man
  before any grasp; fe9bdbf then raised the dead-man to 90 s, so a run whose console stops polling now goes on for up to
  90 s. Of the other five, the runner's grip check passed in four (runs 2, 3, 4 and the recording), although after run
  2 the block was at its start spot; the block ended on the pad in run 4 and in the recording. Fixes went in between
  runs. Earlier days ran earlier code and are not counted here.
- **Mission 2 was not tried on purpose from other spots.** In the recording, and before VLA runs 1 and 2, the block
  went back by hand to about mission 1's start spot. No record says it was moved before runs 3 and 4 (see section 1).
  Nothing here shows the policy picking from a place the scripted mission refuses.
- **Where the block lands is not measured.** Once the grip is confirmed, the jaws open by rule wherever the latched arm
  comes to rest inside the end-of-take region (where the 30 recorded takes ended, widened by 5 deg). Run 3 released the
  block over the pad's near edge and it ended on the floor; run 2 may have ended the same way. The set-down cannot go
  below the runtime's proven grasp height: in run 4, the only watched run with it, the runtime refused its first step,
  and the recorded run's step count was not read. The screen's "carried the block to the pad" follows the rule's
  release, not a view of the pad.
- **The VLA is slow on the robot's CPU.** 12.0 to 12.4 s of inference per chunk in the four runs watched on 2026-10-12,
  read at every fifth chunk. On screen a new chunk starts about every 15 s (12 to 13 s of thinking, then 2 to 3 s of
  motion); mission 2 took 319 s for 20 chunks, with the start pose, the grip check, the set-down and the return. On
  2026-10-09 the robot's CPU read 84.5 to 88.4 C during inference, with throttle flags set.
- **The front camera is off.** With both cameras enabled on 2026-10-11 both camera workers failed
  (`camera-service.md`).
- **Mission 1 needs its window and the scripted carry.** In tests on 2026-10-08, with the block about 3 cm farther from
  the robot the arm ran out of reach (the sag check refused the grasp, or the arm stopped with the shoulder-lift joint
  5.3 deg off its target, once with the block already between the jaws), and about 4 cm nearer SEARCH refused
  (`search_cube_inside_pregrasp_reach`). From the hold with the block held the pad cannot be measured
  (`hold_observe_no_pad_frame`), so the runner's scripted carry, two 5 cm pulses measured by the base's wheel readings,
  takes the block to the pad.
- **The console.** The director sends Activate again after Cake refuses the Plan (`console-redesign-2026-09-27.md`,
  13.8). A Stop during the first Start's pose move did not cancel the Start in the version that ran that day; 5e972c9
  fixes it, checked by unit tests and not yet on the robot.
