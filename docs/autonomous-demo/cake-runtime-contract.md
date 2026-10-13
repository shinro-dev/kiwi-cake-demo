<!-- Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE. -->

# Cake runtime contract

## What Cake must prove

The demo must distinguish Cake runtime evidence from robotics behavior.

Protected continuity across `mobile_approach` activation:

- robot hardware host remains active;
- desktop robot client/connection remains logically continuous where supported;
- cameras remain available;
- mission ID remains unchanged;
- selected target ID remains unchanged;
- perception and VLA remain active;
- stable motion safety gateway is unchanged.

A brief pause is acceptable and should be measured.

## Runtime placement

Preferred: Ubuntu AMD64 Cake on desktop.

This is based on the developer's available local/private AMD64 build, even if
the current public `main` documentation does not yet advertise that target.

Actual local Cake interfaces are authoritative.

## Capability

Initial:
```text
perception        AVAILABLE
manipulation      AVAILABLE
pad_return        AVAILABLE
mobile_approach   UNAVAILABLE
```

After valid activation:
```text
mobile_approach   AVAILABLE
```

## Logical interface

`mobile_approach` consumes fresh target observations and produces only bounded
desired base motion plus state/events.

The stable command gateway validates/limits the motion before it reaches
LeRobot.

Do not create a fake Cake ABI. Adapt this logical contract to the real
descriptor/process/IPC mechanism.

## Corrupted-update beat

1. known-good state remains active;
2. corrupted candidate is presented;
3. deterministic admission rejects it before activation;
4. active state is unchanged;
5. rejection reason is visible;
6. valid candidate is then admitted/activated.

Dev artifacts/signatures stay local/ignored. Only final approved release
artifacts are candidates for publication.

## Private Cake source change path

If missing functionality is discovered:
- document minimal required behavior;
- implement/test privately;
- build local AMD64 candidate;
- consume locally;
- only after E2E/review prepare signed public artifact.

## Evidence
Expose safe fields:
- Cake session identity;
- module/capability identity;
- content/artifact short identity;
- admission result;
- activation timestamp/ordinal;
- transition duration;
- mission ID;
- target ID;
- reconnect/restart counters.

Never expose private signing material, local paths or machine identifiers.
