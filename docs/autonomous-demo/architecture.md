<!-- Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE. -->

# Autonomous collection architecture

## Preferred topology

```text
DESKTOP / Ubuntu AMD64 / RTX 4060 Ti
+-----------------------------------------------------------+
| task executive                                            |
| perception + geometry                                     |
| SmolVLA inference                                         |
| pad return                                                |
| UI / recorder                                             |
|                                                           |
| Cake resident/runtime                                     |
|   +-- Cake-managed mobile_approach capability             |
|                                                           |
| stable robot-command client/gateway boundary              |
+----------------------------+------------------------------+
                             |
                         network
                             |
+----------------------------v------------------------------+
| RASPBERRY PI / LEKIWI                                     |
| stable LeRobot hardware host                              |
| cameras / arm / gripper / base                            |
| command expiry + hard motion bounds + safe stop           |
+-----------------------------------------------------------+
```

The actual existing AMD64 Cake descriptor/process/IPC interface is
authoritative. This diagram is a target decomposition, not permission to
invent a parallel API.

## Stable versus replaceable

Stable for the first demo:

- hardware host;
- actuator safety gateway;
- camera transport;
- mission state;
- target selection;
- perception;
- VLA manipulation;
- operator UI.

Preferred Cake-managed replaceable capability:

- `mobile_approach`.

This capability computes bounded desired base motion from target observations.
It cannot bypass the stable motion gateway.

## Why desktop Cake

The desktop already owns the heavy behavior loop:
perception, VLA inference, mission state and UI. An available Ubuntu AMD64 Cake
build allows the replacement event to happen near those components while the
Pi remains the stable hardware endpoint.

## Private Cake extension path

If the current AMD64 binary lacks a required lifecycle/IPC feature:

1. write a minimal requirement in this public-demo branch;
2. implement/test it in the private Cake source repository;
3. build a local AMD64 release candidate;
4. consume it locally;
5. after E2E + review, build/sign the final intended release;
6. copy only approved public artifacts to this repo.

No private Cake source or private signing material crosses the boundary.
