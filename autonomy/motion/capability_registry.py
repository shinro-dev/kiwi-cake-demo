#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""capability_registry: the smallest capability-facing abstraction between
the physical manipulation state machine
(autonomy/manipulation/hybrid_pick_place.py) and Cake.

Models the "Path A" behavior documented in
docs/autonomous-demo/cake-capability-assessment.md and
docs/autonomous-demo/cake-runtime-contract.md: a capability starts
UNAVAILABLE (bound to an inert generation/capsule identity), a live
candidate is checked against a pre-declared admitted set (an *unadmitted*
candidate is refused before any effect -- not "corrupted" or "malicious",
just not a capsule identity Cake has verified/pre-admitted), and a
candidate matching an admitted, capability-bound identity activates it.
This mirrors cake-capability-assessment.md section 1's Path A exactly: "a
candidate outside the admitted set is refused before any effect."

This is a software-only capability provider -- it does not talk to a real
Cake admin socket. It is the seam a later session replaces with a real
adapter (polling Cake's admin socket per mobile-approach-spec.md section 6:
slot listing content identity + lifecycle + health) without
hybrid_pick_place.py changing at all: the state machine only ever calls
has_capability()/wait_for_change() through its own injected deps, never
this module directly (see hybrid_pick_place.py's module docstring for the
same boundary applied to hardware).

Pure, hardware-free, no lerobot import -- matches this project's existing
autonomy/motion and autonomy/manipulation module boundary.
"""

import threading
from dataclasses import dataclass, field

ADMITTED = "ADMITTED"
REJECTED = "REJECTED"


@dataclass(frozen=True)
class Candidate:
    """capsule_identity: the same "candidate names a capsule the process has
    or has not admitted" concept from cake-capability-assessment.md -- an
    opaque identity string here, not a real signed artifact."""

    capsule_identity: str


class CapabilityRegistry:
    """admitted_identities: the pre-declared set of capsule identities Cake
    has already verified (Path A: admission happened at resident start, not
    live -- cake-capability-assessment.md section 1). log: the same
    event-log callable a run's PickPlaceDeps.log uses, so CAKE_CANDIDATE_*
    events interleave naturally with the state machine's own
    STATE_ENTER/CAPABILITY_* events without hybrid_pick_place.py needing to
    know anything about Cake."""

    def __init__(self, admitted_identities: frozenset, log=lambda event, **fields: print(event, fields)):
        self.admitted_identities = frozenset(admitted_identities)
        self.log = log
        self._available: dict = {}
        self._change_event = threading.Event()

    def has_capability(self, name: str) -> bool:
        return self._available.get(name, False)

    def submit_candidate(self, name: str, candidate: Candidate) -> str:
        self.log("CAKE_CANDIDATE_RECEIVED", capability=name, capsule_identity=candidate.capsule_identity)
        if candidate.capsule_identity not in self.admitted_identities:
            self.log(
                "CAKE_CANDIDATE_REJECTED",
                capability=name,
                capsule_identity=candidate.capsule_identity,
                reason="candidate names an unadmitted capsule",
            )
            # Still pulses wait_for_change(): a caller blocked waiting for
            # *any* submission outcome must wake and re-observe
            # has_capability() == False, not hang forever after a rejection.
            # Admission/rejection outcomes themselves are unchanged by this.
            self._change_event.set()
            return REJECTED

        self._available[name] = True
        self.log("CAKE_CANDIDATE_ADMITTED", capability=name, capsule_identity=candidate.capsule_identity)
        self.log("CAPABILITY_ACTIVATED", capability=name)
        self._change_event.set()
        return ADMITTED

    def wait_for_change(self, timeout=None) -> None:
        """Blocks until submit_candidate() changes some capability's state,
        or timeout elapses. The real adapter polls Cake's admin socket
        (mobile-approach-spec.md section 6) with backoff instead of
        blocking on this event; tests drive submit_candidate() directly and
        never rely on this actually blocking."""
        self._change_event.wait(timeout=timeout)
        self._change_event.clear()
