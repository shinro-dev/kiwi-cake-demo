<!-- Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE. -->

# Demo UI and evidence

The UI is an evidence surface, not Studio.

Show:
- body camera with cube/pad/target overlay;
- wrist camera;
- mission state/current target;
- projected target position + quality;
- manipulation readiness;
- capability set;
- Cake rejection/verification/activation events;
- frame/event sequence;
- transition pause;
- reconnect/restart counts.

Do not display:
- signatures/private keys/tokens;
- local paths;
- hostnames/IP/MAC addresses;
- device serials;
- operator identity.

Implement synthetic mode so UI can be developed/tested without a robot.

Preferred stack: smallest local web stack consistent with repo conventions.
