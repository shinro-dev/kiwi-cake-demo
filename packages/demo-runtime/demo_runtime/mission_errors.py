# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""demo_runtime.mission_errors: the exceptions a mission and its execution adapter share, and nothing else.

WHY A MODULE OF ITS OWN. The mission coordinator catches these and the execution adapter raises them, and the two are
written apart: the robot-side adapter is a separate task that must be able to import what it raises without importing
the coordinator, its journal or the Cake activation module. A small module both sides can import is the whole reason
this file exists, so it holds exceptions and no behaviour.

TWO DIFFERENT THINGS, KEPT APART ON PURPOSE:
  - MissionCancelled is a STOP. The operator asked for one and it was noticed at a machine step. The coordinator
    records the mission STOPPED and never FAILED, because nothing went wrong.
  - ExecutionRefused is a REFUSAL BY THE EXECUTION SIDE. The adapter could not perform an operation the way section 7
    of the Cake console handoff requires (a physical mapping does not exist, a proven builder refused, a guard said
    no), and said so by raising instead of answering. The coordinator records the mission FAILED with the exception's
    class name and message.

WHAT A REFUSAL MUST NEVER BECOME. The state machine reads the answer to each judgment gate and aborts on anything it
does not accept, with the words "operator ABORT" for the one it reads as the operator's. An adapter that expressed a
hardware refusal by answering ABORT would put a hardware fact in the operator's mouth, and a later reader would look for
an operator decision that was never made. Raising one of these instead is how an adapter keeps the two apart.
"""
from __future__ import annotations


class MissionCancelled(Exception):
    """Raised inside an injected callable to unwind the state machine when a stop is asked for.

    The machine driving a target is a synchronous loop, so the only way out of it that does not wait for the current
    target to finish is an exception raised from one of the callables it holds. It is caught by the coordinator and
    recorded as a stop; it never escapes to a caller as a failure.
    """


class ExecutionRefused(Exception):
    """The execution adapter refused an operation, and says why in its message.

    The MESSAGE IS SHOWN. It is written into the mission record beside the class name and served to a console, so an
    adapter writes it as a sentence about the refusal and never puts a path, a host name or a device serial in it. The
    runtime's sanitizer scrubs what it knows about on the way out; it cannot know about everything an adapter might
    say.
    """


class HardwareMappingUnavailable(ExecutionRefused):
    """A physical mapping the operation needs does not exist, so the hardware operation is refused.

    Section 7 of docs/autonomous-demo/cake-console-handoff-2026-09-23.md: where a required physical mapping does not
    exist, that operation is refused, and it is never satisfied by an unconditional confirmation or by a weaker check.
    This is the refusal, raised so that it reaches the record as what it is.
    """
