<!-- Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE. -->

# Stable motion gateway contract

This freezes the narrow interface between `mobile_approach`
(`autonomy/motion/mobile_approach.py`) and the stable motion gateway that
`docs/autonomous-demo/autonomy-motion.md` and
`docs/autonomous-demo/mobile-approach-spec.md` already named but left
unspecified as a schema. Not a Cake document; this is the data-plane
boundary from `mobile-approach-spec.md` section 1's diagram, one level down.

## The message

`autonomy/schemas/desired_base_motion.schema.json` (now committed, no longer
"conceptual, not frozen"):

```text
DesiredBaseMotion
  command_id    string, ^[A-Za-z0-9_.-]{1,64}$
  target_id     string or null, same pattern
  timestamp_ns  integer >= 0
  vx_mps        number
  vy_mps        number
  omega_rps     number
  expiry_ns     integer >= 0, absolute
```

`mobile_approach` publishes these. It never publishes anything else toward
hardware, and never imports `lerobot` or opens any device.

## Who owns what

`mobile_approach` owns:
- computing `vx_mps`/`vy_mps`/`omega_rps` from the current
  `TargetObservation`;
- its own advisory clamps (`vx_max_mps`, `vy_max_mps`, deadband, slew limit)
  -- these keep its OWN output sane, they are not a safety boundary;
- reporting its internal state (`ControllerState`) and a human-readable
  reason alongside every command, including every zero command.

The gateway owns, unconditionally, regardless of what `mobile_approach`
sends it (this list is `autonomy-motion.md`'s "Stable motion safety
gateway" section, restated as a contract `mobile_approach` can be tested
against):
- **velocity limits** -- clamp/reject `vx_mps`/`vy_mps`/`omega_rps` against
  the gateway's own configured hard bounds, independent of
  `mobile_approach`'s advisory clamps;
- **command TTL/lease** -- apply a command for at most until `expiry_ns`,
  never longer, regardless of whether a newer command has arrived;
- **zero on timeout** -- no new valid command before the lease expires means
  zero base velocity, not "hold the last command";
- **disconnect stop** -- loss of the data-plane connection to
  `mobile_approach` (crash, restart, Cake replacement event) means zero,
  the same path as timeout;
- **acceleration/change bounds** where practical -- the gateway may apply
  its own slew limiting independent of `mobile_approach`'s;
- **autonomous-mode permission** -- the gateway refuses to apply any
  `DesiredBaseMotion` unless the system is explicitly in an autonomous-
  motion-permitted mode;
- **operator stop** -- terminal, overrides everything above, requires an
  explicit restart per `autonomy-motion.md`'s failure table.

`mobile_approach` must never have a direct LeRobot motor path. If a future
change makes that untrue, it has silently moved a safety-critical
responsibility into the Cake-replaceable process this whole demo's premise
depends on being safe to replace -- see `mobile-approach-spec.md` section 9.

## What this does not specify

The wire transport (recommended: Unix-domain-socket ZeroMQ per
`mobile-approach-spec.md` section 4, reusing LeRobot's existing `pyzmq`
dependency) and the gateway's own implementation are separate, not-yet-built
work. This contract only fixes the message shape and the responsibility
split so `mobile_approach` can be implemented and tested (as it now is,
offline) against a stable target, before the gateway exists.
