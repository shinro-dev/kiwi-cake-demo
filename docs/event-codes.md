<!-- Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE. -->

# Event codes the demo can emit

`bin/telemetry.sh` decodes each flight record's event code to its name using
this table. The table is limited to the events this demo's resident and
supervisor module can emit; a code not listed here prints as
`UNKNOWN_<hex>` rather than failing the poll. The high byte of a code is its
domain; the low 24 bits are a permanent identifier inside that domain.

Fields `arg0` and `arg1` are two integers whose meaning depends on the
event, given in the third column where the demo reads them.

| Code | Name | Meaning |
| --- | --- | --- |
| `0x01000001` | `EVT_SESSION_START` | a process session began after identity and the flight ring were initialized |
| `0x01000002` | `EVT_SESSION_STOP` | a process session ended through the clean shutdown path |
| `0x03000001` | `EVT_PLAN_ACTIVATED` | a validated Plan activated and became authoritative |
| `0x03000002` | `EVT_PLAN_CANDIDATE_FAILURE` | a validated Plan candidate failed and was torn down without publication |
| `0x03000003` | `EVT_PLAN_REFUSED` | a configured Plan was refused before any instance existed |
| `0x03000004` | `EVT_EPOCH_PUBLISHED` | the activated graph's committed epoch was published |
| `0x03000005` | `EVT_BINDING_PUBLISHED` | one consumer's committed binding snapshot pointer was published |
| `0x05000002` | `EVT_LIFECYCLE_CREATE` | one instance completed create |
| `0x05000003` | `EVT_LIFECYCLE_PUBLISH_PROVISIONS` | one instance completed provision publication |
| `0x05000004` | `EVT_LIFECYCLE_BIND` | one instance completed binding |
| `0x05000005` | `EVT_LIFECYCLE_INITIALIZE` | one instance completed initialization |
| `0x05000006` | `EVT_LIFECYCLE_ACTIVATE` | one instance completed activation |
| `0x05FFFFF5` | `EVT_SUPERVISOR_SAFE_STOP_FAILED` | the supervisor could not spawn its configured safe-stop command; arg0 is the platform error number, arg1 the count of such failures |
| `0x05FFFFF6` | `EVT_SUPERVISOR_SAFE_STOP_RAN` | the supervisor ran its configured safe-stop command for one observed child exit; arg0 is the raw wait status word of the safe-stop child, arg1 the count of safe-stop runs |
| `0x05FFFFF7` | `EVT_SUPERVISOR_SPAWN_FAILED` | the supervisor could not spawn its configured child; arg0 is the platform error number, arg1 the restart ordinal |
| `0x05FFFFF8` | `EVT_SUPERVISOR_HEALTH_CHANGED` | the supervisor's own health state changed; arg0 is the new state, arg1 the previous one |
| `0x05FFFFF9` | `EVT_SUPERVISOR_RESTART_BOUND_REACHED` | the supervisor reached its restart bound and entered its terminal give-up state; arg0 is the restart ordinal, arg1 the configured bound |
| `0x05FFFFFA` | `EVT_SUPERVISOR_OUTPUT_ACCOUNTED` | the supervisor accounted for its child's output; arg0 lines, arg1 bytes; no output byte is in the record |
| `0x05FFFFFB` | `EVT_SUPERVISOR_CHILD_EXITED` | the supervisor reaped its child; arg0 is the raw wait status word, arg1 the restart ordinal |
| `0x05FFFFFC` | `EVT_SUPERVISOR_CHILD_STARTED` | the supervisor spawned its configured child; arg0 is the restart ordinal (0 for the first spawn), arg1 the spawn count |
| `0x0B000001` | `EVT_ADMIN_CONNECTION_ACCEPTED` | an admin connection was accepted and its peer credentials captured |
| `0x0B000002` | `EVT_ADMIN_CONNECTION_REFUSED_CAPACITY` | an admin connection was refused because the concurrent bound was reached |
| `0x0B000003` | `EVT_ADMIN_CONNECTION_REFUSED_NO_CREDENTIALS` | an admin connection was closed because peer credentials could not be read |
| `0x0B000004` | `EVT_ADMIN_GROUP_NOT_APPLIED` | the configured administrative group could not be applied to the socket |
| `0x0B000005` | `EVT_ADMIN_REQUEST_REJECTED` | an administrative request was rejected before any operation ran |
| `0x0B000008` | `EVT_ADMIN_SHUTDOWN_REQUESTED` | a clean-shutdown request was accepted |
| `0x0B000009` | `EVT_ADMIN_LISTING_REFUSED` | a slot or binding listing was refused because no coherent listing was available |
| `0x0B00000A` | `EVT_ADMIN_RUNNING_GRAPH_REFUSED` | a status read was refused because one coherent graph read was unavailable |
| `0x0B00000F` | `EVT_ADMIN_HEALTH_REFUSED` | a health read was refused because the active slots' health could not be read at one read epoch |

Reading a raw wait status word: if the low seven bits are nonzero, the child
was ended by that signal number (9 is SIGKILL, 15 is SIGTERM); otherwise the
exit status is the next eight bits. Every poll of the flight ring opens an
admin connection and therefore mints one `EVT_ADMIN_CONNECTION_ACCEPTED`
record of its own; a tight poll loop can push older records out of the
bounded ring, which is why the demo runner detects restarts from the process
table first and reads the ring only a few times afterward.
