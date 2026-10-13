<!-- Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE. -->

# Autonomy and motion design

## Deterministic mission executive

```text
INIT
 -> LOCATE_PAD
 -> SEARCH_CUBE
 -> SELECT_TARGET
 -> ASSESS_REACHABILITY
      -> PICK, if ready
      -> MOBILE_APPROACH, if capability available
      -> WAITING_FOR_CAPABILITY, otherwise
 -> VERIFY_GRASP
 -> LOCATE_PAD
 -> PAD_APPROACH
 -> PLACE
 -> VERIFY_PLACE
 -> SEARCH_CUBE or COMPLETE
```

`WAITING_FOR_CAPABILITY` preserves the same mission ID and target ID across
Cake activation.

## `mobile_approach`

Task-bounded visual servoing, not generic navigation.

Input:
- fresh target observation `(x,y,quality,timestamp)`.

Behavior:
1. reject stale/low-quality data;
2. compute error to handoff region;
3. produce bounded holonomic command;
4. move for short bounded interval;
5. command zero;
6. reacquire;
7. repeat until manipulation-ready.

Start with bounded P control:
`vx = clamp(Kx*ex)`
`vy = clamp(Ky*ey)`

Add yaw only if experiments require it.

Contract version 2 (2026-09-26, `mobile-approach-spec.md` section 10): the observation the capability is fed is
the proven placement executor's own remaining bounded advance, in the pad-derived `anchor_reach` frame, from a
stationary stamped capture, with a publication stamp the command echoes and an age bound derived from the perception
budget. The controller's version 2 configuration saturates at the executor's pulse speed for every remaining advance
beyond the executor's tolerance; the executor still bounds every pulse and measures every displacement.

## Stable motion safety gateway

The replaceable capability never owns unbounded motors.

Stable gateway enforces:
- TTL/lease;
- velocity limits;
- change/acceleration limits where practical;
- zero on timeout/disconnect;
- command sequence;
- autonomous-mode permission;
- operator stop;
- no unintended motion during manipulation;
- no deliberate driving onto the pad in first demo.

Cake/VLA cannot weaken this.

## Pad return

While carrying, assume wrist visibility may degrade.

Use body-camera pad reacquisition and closed-loop approach to a canonical pose
adjacent to the pad, stop base, then hand off to PLACE.

No SLAM required.

## Failure behavior
- target lost -> zero + reacquire;
- pad lost -> zero + bounded search;
- stale perception -> zero;
- desktop/transport loss -> command lease expires;
- capability missing -> WAITING_FOR_CAPABILITY;
- capability failure -> zero + explicit failure;
- operator stop -> terminal until explicit restart.
