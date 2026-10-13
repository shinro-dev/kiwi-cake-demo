# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""demo_runtime.mission_coordinator: a thin coordinator that carries a two-cube mission and writes down what it did.

WHAT IT IS NOT. It is not a manipulation implementation and it holds no robot behaviour of its own. It owns the
ORDER of a mission: which target is being carried, that a target waits when the capability it needs is not
available, that an activation is submitted once and confirmed by reading the slot, and that the same target resumes
afterwards. Everything that touches a robot arrives as an INJECTED EXECUTION ADAPTER and everything that reads or
moves a Cake slot arrives as an INJECTED CAPABILITY ADAPTER, so what this file decides can be tested without either.

THE TRANSITION LOGIC IS REUSED, NOT REWRITTEN. Each target is carried by
`autonomy.manipulation.hybrid_pick_place.run_hybrid_pick_place`, which already holds the sixteen states and the
MOBILE_READY -> WAITING_FOR_CAPABILITY -> MOBILE_APPROACH transition behind its own injected seam. This coordinator
supplies that seam and does not duplicate it.

SOFTWARE COVERAGE DOES NOT TRANSFER PHYSICAL QUALIFICATION. The execution adapter passed in decides what actually
happens to a robot, and a recording adapter that performs no motion is a rehearsal, never evidence about hardware.
An adapter must say which it is, and `ExecutionAdapter.performs_motion` is that answer; a mission records it, with the
adapter's label, in its first record (mission.mission_started), and the mission view shows both, so no later reader
can mistake a rehearsal outcome for a physical one.

NOTHING LONG-RUNNING HAPPENS IN A REQUEST. `start` writes one record and returns an id; the mission itself runs on
this coordinator's own thread. `stop` sets the mission's event, writes one record and returns, and every place this
coordinator can block checks that event, so a stop is answered while a target is waiting on a capability just as it is
while one is being carried.

ONE CANCEL EVENT PER MISSION, keyed by its id. The carrying thread, the capability gate, the wait poll, every machine
step and `submit_activation` all read the event of THEIR mission. A single shared event used to be cleared by the next
`start`, so an activation still in flight for a stopped mission A read "not stopped" once mission B had started and
reported itself against a mission that was not A's. Events are never cleared; a new mission gets a new one.

A STOP IS NOTICED AT EVERY MACHINE STEP, AND ONLY THERE. Every callable of the execution adapter is handed to the machine
wrapped, so each refuses with MissionCancelled once the mission's event is set. That is a refusal added in front of each
step and nothing else: it replaces no measured predicate the adapter answers with (section 7 of
docs/autonomous-demo/cake-console-handoff-2026-09-23.md). It CANNOT interrupt a callable that is already running: a move
in progress finishes or fails on its own terms, and stopping hardware mid-move is the guarded controller stop's job, not
this wrapper's.

A LATE ANSWER NEVER RESUMES A STOPPED MISSION. An activation is submitted on the CALLER's thread and takes as long as
the resident takes, so a stop can be asked for while one is in flight and the answer can arrive after the mission has
already ended. Two things hold then, and they are separate: the answer is RECORDED, because a slot may really have
moved and a record that said otherwise would say less than happened; and the target does NOT resume, because the
waiting thread refuses to leave the capability gate once a stop has been asked for, whatever the slot now carries, and
the fold refuses to let anything after the end change it (demo_runtime.mission). `submit_activation` reports the second
as `stopped_during_activation`, from the event of the mission the answer was for, rather than leaving it to be inferred.

ONE ACTIVATION IN FLIGHT PER MISSION. A second press while the first operation is still with the resident, or after
the resident answered that its transaction is still running, would be a fresh operation uuid for the same intent: the
resubmission on a guess that the retry rules forbid (section 2 of the handoff). `submit_activation` refuses it with
activation_in_flight and submits nothing. A console whose request timed out while the resident took its time is the
case this exists for: pressing again must not send a second ApplyPlan.

A STOP OUTLIVES THE PROCESS THAT RECEIVED IT. It is written down (mission.mission_stop_requested) as well as set in
memory, and a mission that nothing is carrying any more (its runtime ended while it was RUNNING or WAITING) is ended
STOPPED by the stop itself, because no thread exists that would ever notice the event.

A FAILED ATTEMPT HOLDS FOR THE OPERATOR WHEN IT CAN BE ANSWERED (operator ruling 1's default). When the run target says
what an attempt came to (its outcome's `recovery`: the class, the controller's reason code, phase, measurements and
frames, and the stop state), every attempt that did not place the target is written down (mission.target_attempt) with
the actions a press can take now. A retryable or operator-needed class does not end the mission: it WAITS with the reason
operator_decision, and NOTHING MOVES until a person presses Retry or Stop. Retry (request_retry) is accepted only for the
hold it names, only while that attempt offers one (nothing held, the base home, budget and a park left: mission.
next_actions), and is written down before it is acted on; the carrying thread then PARKS the arm with the run target's
park (the unchanged `--do exit`) and starts a FRESH attempt whose SEARCH observes anew. A STOP OUTRANKS EVERY STEP: the
hold, the park and the fresh spawn each read the mission's cancel event first. A run target that reports no `recovery`
(the rehearsal's) ends the mission on a failure exactly as before.

A RESUME READS, IT DOES NOT RESTART. Recovery folds the durable file and reports what was true. It never re-enters
a state machine, never resubmits an activation and never replays a motion command; a mission interrupted mid-target
is reported as it was left, for an operator to decide about.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from typing import Callable, Optional

from . import cake_activation, mission
from .journal import Journal, read_json_lines, utc_now_text
# Re-exported HERE, where every caller has always imported it from: the class itself lives in mission_errors so the
# robot-side execution adapter can raise it without importing this module.
from .mission_errors import ExecutionRefused, MissionCancelled  # noqa: F401

#: How often a waiting target re-reads the capability. Short enough that an activation is noticed promptly, and it
#: is a POLL rather than a blocking wait so that a stop is never queued behind it.
CAPABILITY_POLL_S = 0.5

#: Why an activation or a stop was answered the way it was. Each is a CODE and names no path, mission or value.
MISSION_STOPPING = "mission_stopping"      # an activation for a mission whose stop was asked for: nothing is submitted
NO_RUNNING_MISSION = "no_running_mission"  # a stop with nothing to stop: answered, with stopping false
ACTIVATION_IN_FLIGHT = "activation_in_flight"  # this mission already has an operation with the resident: nothing is submitted
# Why a Retry was refused. Each refusal writes nothing and starts nothing.
MISSION_NOT_HELD = "mission_not_held"                  # the mission is not waiting for the operator's decision
MISSION_NOT_CARRIED = "mission_not_carried"            # nothing carries the held mission any more (its runtime ended)
RETRY_NOT_OFFERED = "mission_retry_not_offered"        # the held attempt offers no retry (something held, the budget spent)
RETRY_ALREADY_REQUESTED = "mission_retry_already_requested"  # this hold's Retry was already asked for
PARK_UNAVAILABLE = "mission_park_unavailable"          # the run target cannot park the arm, so it cannot retry
PARK_FAILED = "mission_park_failed"                    # the park before a retry did not end at the low scan pose
# A LIVE runtime that does not admit a Retry at all (the supervisor's own codes, supervisor.MISSION_RETRY_NOT_*): its
# configuration does not set mission.retry, or sets it and names no attended label. The hold then offers only the Stop.
RETRY_NOT_ENABLED = "mission_retry_not_enabled"
RETRY_NOT_ATTENDED = "mission_retry_not_attended"

#: Retries per target after a failed attempt. One: the first attempt and one retry, then the mission holds with only the Stop.
RETRY_BUDGET_PER_TARGET = 1
#: Who asked for a Retry, on its record: a person at the console, or the automatic retry below.
RETRY_BY_OPERATOR, RETRY_BY_AUTOMATIC = "operator_console", "automatic"
#: THE AUTOMATIC PARK AND ONE RETRY per target for a retryable failure, OFF. Operator ruling 1: the mission holds for the
#: operator until this is ruled on and qualified on the robot. When on, it takes the place of the press once per target,
#: only for the retryable class (nothing held, the base home), and never after a stop.
AUTOMATIC_RETRY = False
#: How often a hold re-reads its Retry and the mission's stop.
HOLD_POLL_S = 0.2

#: The answer outcome for a candidate Cake refused before any transaction existed, in cake_activation's vocabulary
#: (cake_activation.REFUSED_BEFORE_TRANSACTION once it classifies status mutation_refused; a test keeps the two equal).
#: Spelled here so the verdict below recognizes it whichever of the two lands first.
REFUSED_BEFORE_TRANSACTION_OUTCOME = getattr(cake_activation, "REFUSED_BEFORE_TRANSACTION", "REFUSED_BEFORE_TRANSACTION")

#: The detail of a mission ended by a stop that found no thread carrying it. It says what is known and no more: the
#: runtime that carried the mission ended, so what the mission was doing after that point was never recorded.
ORPHAN_STOP_DETAIL = "stopped with no runtime carrying it: the runtime that carried this mission ended before the stop"

#: The longest refusal message an execution adapter can put on the record. A refusal is a sentence, not a log.
MAX_REFUSAL_TEXT = 200

#: The five callables of the machine's own seam, in PickPlaceDeps order. Each is wrapped before the machine sees it.
MACHINE_STEPS = ("read_pose", "read_base", "move_arm", "move_base_sustained", "confirm")


@dataclass
class ExecutionAdapter:
    """The robot-facing half of a mission, injected whole.

    The five callables are the PickPlaceDeps seam of hybrid_pick_place, unchanged, so a proven implementation can be
    passed straight through. `performs_motion` is not optional and has no default: an adapter that did not say
    would be recorded as if it had, and a rehearsal would be indistinguishable from a physical run.

    THREE OPTIONAL SEAMS for the robot-side adapter, all absent from the rehearsal one, and none changes anything
    when absent:
      - `log` receives the machine's own log events, exactly as PickPlaceDeps.log does (an event name and keyword
        fields). Absent, the events go nowhere, which is what a rehearsal has always done. It is a sink and never a
        control path: it is NOT wrapped with the stop check below, because a logger that could raise would make a log
        line decide when a mission stops.
      - `attach_cancel` is called ONCE per mission with that mission's cancel Event, on the carrying thread, before the
        first target is carried. It is how an adapter that blocks inside one callable (a sustained base move, a hold)
        can see a stop there as well; what it does on seeing one is its own guarded stop. An exception it raises is
        recorded like one raised by any step.
      - `attach_mission` is called ONCE per mission with (mission_id, targets), on the carrying thread, before
        attach_cancel and before the first target is carried. The robot-side adapter writes a mission target file per
        run naming the mission and EVERY declared target (autonomy/manipulation/target_binding.py), and this is how it
        learns them from the coordinator instead of guessing. The targets are copies; nothing it does changes them.

    A refusal is RAISED, never answered: an adapter that cannot perform an operation the way section 7 requires raises
    mission_errors.ExecutionRefused (HardwareMappingUnavailable for a missing physical mapping), and the mission is
    recorded FAILED with the class name and message. Answering a judgment gate with ABORT instead would be recorded as
    an operator's decision nobody made.
    """

    read_pose: Callable
    read_base: Callable
    move_arm: Callable
    move_base_sustained: Callable
    confirm: Callable
    performs_motion: bool
    label: str
    log: Optional[Callable] = None
    attach_cancel: Optional[Callable] = None
    attach_mission: Optional[Callable] = None


@dataclass
class CapabilityAdapter:
    """The Cake-facing half. `available` answers from the capability SLOT and nothing else.

    `submit` carries a prepared candidate and returns cake_activation's answer dict. `observe` re-reads and answers
    whether the capability is now usable, which is the only thing a mission may act on: an answer that a
    transaction completed is not an activation, and this keeps the two callables apart so a caller cannot conflate
    them.

    `read_slot`, when present, is the same read with what it said kept: cake_activation.slot_reading of one list-slots
    answer. An activation then reads the slot just before its submit and again after the answer, records both, and
    decides OBSERVED from the `capability_active` of the reading it records, so the read that decided and the read
    that was written down are one read. Absent (a test double), `observe` decides and no slot detail is recorded.
    """

    available: Callable[[], bool]
    submit: Callable[[str, bytes], dict]
    observe: Callable[[], bool]
    candidates: object
    read_slot: Optional[Callable[[], dict]] = None


class MissionCoordinator:
    """One mission at a time, carried on one thread, written down before it is attempted."""

    def __init__(self, data_dir, execution: ExecutionAdapter, capability: CapabilityAdapter, *,
                 data_mode: str, run_target: Optional[Callable] = None, now_text: Callable = utc_now_text):
        self._path = str(data_dir)
        self._journal = Journal(self._path)
        self._execution = execution
        self._capability = capability
        self._data_mode = data_mode
        self._now = now_text
        # Injected so a test can drive the coordinator's ORDER without the manipulation machine, and so the real
        # machine is reached through exactly one seam rather than imported at module scope by a packaged runtime.
        self._run_target = run_target or _run_hybrid_target
        self._lock = threading.Lock()
        # One Event per mission id, created when the mission starts (or, for a mission a previous runtime left on the
        # record, when something first asks about it). Never cleared and never shared. Its own lock, because stop()
        # reaches it while holding _lock and a Lock is not re-entrant.
        self._cancels: dict = {}
        self._cancels_lock = threading.Lock()
        # One Lock per mission id, held by submit_activation from its checks to the answer record, and only ever TRIED:
        # a second activation for the same mission is refused, never queued behind the first. Guarded by _cancels_lock
        # like the events, and created only for a mission that is on the record.
        self._activation_locks: dict = {}
        # One Event per mission id, set by request_retry after the Retry is on the record and read by the carrying thread's
        # hold. Guarded by _cancels_lock like the events above.
        self._retry_events: dict = {}
        self._thread: Optional[threading.Thread] = None
        self._mission_id: Optional[str] = None
        # The mission the live thread is carrying. A stop compares with this to tell a carried mission from one that
        # nothing is carrying any more.
        self._carrying: Optional[str] = None
        # mission id -> the origin of a stop cancel_now set the event for and stop() has not recorded yet. The carrying
        # thread can notice that event and end the mission before stop() writes its request; it then writes the request
        # itself, with this origin, so the record says a stop was asked for whichever of the two writes first.
        self._pending_stops: dict = {}

    # ---- durable state ------------------------------------------------------------------------------------
    def _append(self, record: dict) -> None:
        self._journal.append(record)

    def _folded(self) -> dict:
        records, _ = read_json_lines(self._path)
        return mission.fold_missions(records)

    def _cancel_for(self, mission_id: str) -> threading.Event:
        """THIS mission's cancel event, created on first use. A mission's event says only whether THAT mission was
        asked to stop; no other mission's start or stop can set or clear it."""
        with self._cancels_lock:
            event = self._cancels.get(mission_id)
            if event is None:
                event = self._cancels[mission_id] = threading.Event()
            return event

    def _activation_lock_for(self, mission_id: str) -> threading.Lock:
        """THIS mission's activation lock, created on first use. Per mission, so an activation for one mission is never
        refused because of an operation in flight for another."""
        with self._cancels_lock:
            lock = self._activation_locks.get(mission_id)
            if lock is None:
                lock = self._activation_locks[mission_id] = threading.Lock()
            return lock

    def _retry_event_for(self, mission_id: str) -> threading.Event:
        with self._cancels_lock:
            event = self._retry_events.get(mission_id)
            if event is None:
                event = self._retry_events[mission_id] = threading.Event()
            return event

    def can_park(self) -> bool:
        """Whether the run target can park the arm before a retry: it has a `park`, and its `can_park` (when it has one)
        says the supervisor beneath it can start one. Without that a hold offers no Retry."""
        park, able = getattr(self._run_target, "park", None), getattr(self._run_target, "can_park", None)
        return callable(park) and (not callable(able) or bool(able()))

    def retry_refusal(self) -> Optional[str]:
        """Why the runtime beneath admits no Retry at all, or None: the run target's own `retry_refusal` (for the proven
        path, the supervisor's: a LIVE runtime without mission.retry and the attended label). A run target that drives the
        hardware and cannot say is refused (RETRY_NOT_ENABLED); any other run target admits one. With a refusal a hold
        offers only the Stop, and a Retry request is answered with it before anything is written."""
        gate = getattr(self._run_target, "retry_refusal", None)
        if callable(gate):
            return gate()
        return RETRY_NOT_ENABLED if getattr(self._run_target, "hardware_run_target", False) else None

    def state(self, mission_id: Optional[str] = None) -> Optional[dict]:
        """What is on the record, folded. A READ: it starts nothing and submits nothing."""
        table = self._folded()
        wanted = mission_id or self._mission_id
        if wanted is None:
            wanted = next(reversed(table), None) if table else None
        return self._served(mission.mission_view(table.get(wanted)), wanted) if wanted in table else None

    def _served(self, view: Optional[dict], mission_id) -> Optional[dict]:
        """The view as this runtime can act on it. A hold that no thread carries (the runtime that held it ended, and
        recover() adopted it without re-entering it) offers no Retry: request_retry answers mission_not_carried, and a
        Stop ends it. The record is unchanged; only what the served view offers now follows what a press would get."""
        if view is None or view.get("hold") is None:
            return view
        if self._carrying == mission_id and self._thread is not None and self._thread.is_alive():
            return view
        view["hold"]["next_actions"] = [action for action in view["hold"]["next_actions"] if action not in mission.RETRY_ACTIONS]
        view["next_actions"] = [action for action in view["next_actions"] if action not in mission.RETRY_ACTIONS]
        return view

    def recover(self) -> Optional[dict]:
        """After a restart, adopt the newest mission on the record WITHOUT re-entering it.

        A mission left RUNNING by a daemon that died is reported exactly as it was left. It is not resumed here and
        no activation is resubmitted: what happened while nobody was recording is not knowable, and a coordinator
        that guessed would be inventing the part that matters. A stop can still end it (see stop).
        """
        table = self._folded()
        if not table:
            return None
        newest = next(reversed(table))
        self._mission_id = newest
        return self._served(mission.mission_view(table[newest]), newest)

    # ---- running ------------------------------------------------------------------------------------------
    def start(self, targets: list) -> dict:
        """Write the mission down, then carry it on this coordinator's own thread. Returns immediately."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return {"ok": False, "reason_code": "mission_already_running", "mission_id": self._mission_id}
            mission_id = mission.new_mission_id()
            # A FRESH event for a fresh mission. Nothing is cleared: an activation still in flight for the previous
            # mission keeps reading the previous mission's event, which stays set if that mission was stopped.
            cancel = self._cancel_for(mission_id)
            self._mission_id = mission_id
            # WRITTEN BEFORE ANYTHING IS ATTEMPTED, and fsynced by the journal before this returns.
            self._append(mission.mission_started(mission_id, targets, t_wall_utc=self._now(),
                                                 data_mode=self._data_mode,
                                                 performs_motion=self._execution.performs_motion,
                                                 execution_label=self._execution.label))
            self._carrying = mission_id
            self._thread = threading.Thread(target=self._carry, args=(mission_id, list(targets), cancel),
                                            name="mission-coordinator", daemon=True)
            self._thread.start()
            return {"ok": True, "mission_id": mission_id}

    def stop(self, *, origin: str = "mission_stop") -> dict:
        """Ask the newest mission to stop. Answered at once; the carrying thread notices at its next check.

        THREE ANSWERS, each with `ok` true because each is an answer and not a failure:
          - nothing to stop (no mission, or the newest one already ended): `stopping` false, reason no_running_mission;
          - a mission a thread is carrying: its event is set, the request is written down, `stopping` true, and the
            thread writes STOPPED when it notices, which is within one poll or at the next machine step;
          - a mission NOTHING is carrying (its runtime ended while it was RUNNING or WAITING): the request is written
            down and the mission is ended STOPPED here, with a detail saying the carrying runtime ended, because no
            thread exists that would ever notice the event. Before this, such a mission could never be stopped.

        THE EVENT IS SET BEFORE THE RECORD IS WRITTEN, so the refusal at every machine step and in submit_activation
        takes effect in the time it takes to set a flag rather than the time an fsync takes. A crash between the two
        takes the event and the carrying thread with it and leaves the mission on the record as it was, which the next
        runtime's stop ends as one nothing is carrying.

        NOTHING HERE TOUCHES A ROBOT, A HOST OR A RESIDENT. A motion in progress is stopped by the runtime's guarded
        controller stop, which the daemon calls first (daemon.RuntimeCore._stop_run_and_mission); this only ends the
        mission's own order.
        """
        with self._lock:
            table = self._folded()
            wanted = self._mission_id if self._mission_id in table else (next(reversed(table)) if table else None)
            state = table.get(wanted)
            pending = wanted is not None and self._pending_stops.pop(wanted, None) is not None
            if state is not None and state["state"] == mission.MISSION_STOPPED and pending:
                # THIS stop's own cancel_now ended the mission before this call got the lock: the carrying thread saw the
                # event, wrote the request with this stop's origin and ended the mission STOPPED. It is the stop that was
                # asked for, and it is answered as one.
                return self._stop_answer(wanted, state, stopping=True, carried=True)
            if state is None or state["state"] in mission.MISSION_TERMINAL:
                return self._stop_answer(wanted, state, stopping=False, carried=False)
            self._cancel_for(wanted).set()
            if not state["stop_requested"]:
                self._append(mission.mission_stop_requested(wanted, origin=origin, t_wall_utc=self._now()))
            carried = self._carrying == wanted and self._thread is not None and self._thread.is_alive()
            if not carried:
                self._end(wanted, mission.MISSION_STOPPED, ORPHAN_STOP_DETAIL)
            after = self._folded().get(wanted)
            # The end is sticky: a mission that finished on its own between the read above and the write is still
            # COMPLETE, the request was counted as arriving after its end, and this stop stopped nothing.
            return self._stop_answer(wanted, after, stopping=bool(after and after["stop_requested"]), carried=carried)

    def cancel_now(self, *, origin: str = "mission_stop") -> Optional[str]:
        """Set the CARRIED mission's cancel event, now, and nothing else. The mission id, or None when nothing is carried.

        LOCK-FREE AND WITHOUT AN FSYNC, on purpose. The daemon's stop calls this FIRST, before the supervisor's stop and
        before stop() above, which takes this coordinator's lock and writes a record. Everything that can start motion for
        a mission reads this event: the supervisor reads it inside the lock that publishes a mission's run, the robot-side
        adapter's wait reads it every 0.2 s, and the capability gate reads it before it lets a target through. Setting it
        first therefore closes the window in which a stop has been asked for and a run could still be published because
        the record of the stop was being written. It writes nothing: stop(), called right after, writes the request.

        It reads two references and sets one Event. The dictionary read is a single lookup; the event itself is created
        in start() before the carrying thread exists, so a carried mission always has one here."""
        thread, carrying = self._thread, self._carrying
        if carrying is None or thread is None or not thread.is_alive():
            return None
        event = self._cancels.get(carrying)
        if event is None:
            return None
        self._pending_stops[carrying] = origin  # before the event, so the carrying thread that sees it also sees this
        event.set()
        return carrying

    def on_run_started(self, mission_id: str, target_id: str, run_id: str, *, run_kind=None) -> None:
        """Record that a supervised run was started for this target (mission.target_run_started), BEFORE its spawn.

        Called by the robot-side adapter from inside the supervisor's spawn path (supervisor.MissionRun.on_run_started),
        after the run's mission target file exists and before a child exists. An exception here ends that run as never
        started, which is what a mission that could not write down which run it started must do. `run_kind` is
        mission.RUN_KIND_PARK for the park before a retry."""
        self._append(mission.target_run_started(mission_id, target_id, run_id, t_wall_utc=self._now(), run_kind=run_kind))

    def on_target_bound(self, mission_id: str, target_id: str, *, run_id: str, observation_id, bound_pad_m, mode: str) -> None:
        """Record where the first execution's SEARCH bound one declared target (mission.mission_target_bound)."""
        self._append(mission.mission_target_bound(mission_id, target_id, run_id=run_id, observation_id=observation_id,
                                                  bound_pad_m=bound_pad_m, mode=mode, t_wall_utc=self._now()))

    @staticmethod
    def _stop_answer(mission_id, state, *, stopping: bool, carried: bool) -> dict:
        """What a stop says. `state` is the mission's state as folded AFTER the stop; it is a request's answer and
        never says that anything physical ceased."""
        return {"ok": True, "mission_id": mission_id, "stopping": stopping,
                "reason_code": None if stopping else NO_RUNNING_MISSION,
                "state": state["state"] if state else None,
                "stop_requested": bool(state and state["stop_requested"]), "carried": carried}

    def join(self, timeout=None) -> bool:
        thread = self._thread
        if thread is None:
            return True
        thread.join(timeout)
        return not thread.is_alive()

    @property
    def performs_motion(self) -> bool:
        """The execution adapter's own answer: whether a mission carried here can move a robot."""
        return bool(self._execution.performs_motion)

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _carry(self, mission_id: str, targets: list, cancel: threading.Event) -> None:
        try:
            if self._execution.attach_mission is not None:
                # Before the cancel and before the first target, on this thread: the mission's id and its declared targets,
                # as copies, so an adapter that writes a per-run target file names exactly what this mission declared.
                self._execution.attach_mission(mission_id, [dict(target) for target in targets])
            if self._execution.attach_cancel is not None:
                # Before the first target, on this thread, so an adapter learns of THIS mission's event before any of
                # its steps can be asked to block.
                self._execution.attach_cancel(cancel)
            for target in targets:
                if cancel.is_set():
                    self._end_stopped(mission_id, "stopped before this target was started")
                    return
                target_id = target.get("target_id")
                self._append(mission.target_selected(mission_id, target_id, t_wall_utc=self._now()))
                outcome = self._carry_one(mission_id, target_id, target, cancel)
                if outcome is None:  # cancelled
                    self._end_stopped(mission_id, "stopped while carrying a target")
                    return
                if outcome.get("target_state") != mission.TARGET_PLACED:
                    self._append(mission.target_outcome(mission_id, target_id, mission.TARGET_FAILED,
                                                        run_id=outcome.get("run_id"),
                                                        detail=outcome.get("detail"), t_wall_utc=self._now(),
                                                        evidence=outcome.get("evidence")))
                    self._end(mission_id, mission.MISSION_FAILED, outcome.get("detail"))
                    return
                self._append(mission.target_outcome(mission_id, target_id, mission.TARGET_PLACED,
                                                    run_id=outcome.get("run_id"), detail=outcome.get("detail"),
                                                    t_wall_utc=self._now(), evidence=outcome.get("evidence")))
            self._end(mission_id, mission.MISSION_COMPLETE, None)
        except MissionCancelled:
            self._end_stopped(mission_id, "stopped")
        except ExecutionRefused as refusal:
            # A REFUSAL BY THE EXECUTION SIDE, recorded as what it is: FAILED, with the adapter's class name and its
            # sentence. It is never an operator ABORT and never a stop.
            self._end(mission_id, mission.MISSION_FAILED, refusal_text(refusal))
        except Exception as error:  # noqa: BLE001 -- a class name, never a message that could carry a path
            self._end(mission_id, mission.MISSION_FAILED, type(error).__name__)

    def _carry_one(self, mission_id: str, target_id: str, target: dict, cancel: threading.Event) -> Optional[dict]:
        """One target, through the injected execution adapter and the reused transition logic."""
        # Whether a wait is open NOW. A wait is recorded when it opens and a resume when it closes, so a target the
        # machine sends back to the gate a second time says so again rather than reading RUNNING while it waits.
        waiting = [False]

        def capability_available(_name) -> bool:
            # A STOP OUTRANKS A CAPABILITY THAT HAS JUST ARRIVED, and this is the line that makes that true.
            #
            # An activation is submitted on ANOTHER thread and takes as long as the resident takes. A stop asked for
            # while one is in flight ends the mission; the answer then lands afterwards and the slot may well be
            # carrying the capability by the time this callable is next reached. Without this check the wait loop
            # would see an available capability, leave the gate and carry the target - a stopped mission resumed by a
            # late answer, which is the one outcome a stop must never have. It unwinds by the same exception a stop
            # noticed inside the poll below does, so both arrive at the coordinator as one stop.
            if cancel.is_set():
                raise MissionCancelled()
            # A target that declares no capability never waits. One declared as needing it, and one declared "auto"
            # (mission.REQUIRES_CAPABILITY_AUTO), read the slot: the hardware runner reaches this closure for an auto
            # target only once its run MEASURED BASE_ADVANCE (mission_execution.admit_capability), and the rehearsal's
            # machine, which measures nothing, asks it for every target that may need the capability.
            if target.get("requires_capability") not in (True, mission.REQUIRES_CAPABILITY_AUTO):
                return True
            if self._capability.available():
                if waiting[0]:
                    # The slot read can take a while; a stop that landed during it still wins over the resume.
                    if cancel.is_set():
                        raise MissionCancelled()
                    # THE SAME TARGET RESUMES, and the record says so: ACTIVE again, the mission RUNNING, the waiting
                    # reason cleared. Written HERE, where the slot was read, and never by an activation's answer.
                    waiting[0] = False
                    self._append(mission.target_resumed(mission_id, target_id, t_wall_utc=self._now()))
                return True
            # THE WAIT IS RECORDED when it opens, with its reason. A mission that says only "waiting" cannot be told
            # from one that is stuck.
            if not waiting[0]:
                waiting[0] = True
                self._append(mission.target_waiting(mission_id, target_id, mission.WAITING_ON_CAPABILITY,
                                                    t_wall_utc=self._now()))
            return False

        def wait_for_capability_change() -> None:
            # A POLL THAT A STOP INTERRUPTS. Event.wait returns as soon as the event is set, so a stop asked for
            # while a target waits is answered within one poll rather than at the end of the mission.
            if cancel.wait(CAPABILITY_POLL_S):
                raise MissionCancelled()

        if cancel.is_set():
            return None
        attempt, park_run_id, retries, automatic_used = 1, None, 0, False
        while True:
            try:
                result = self._run_target(refusing_after_stop(self._execution, cancel), capability_available,
                                          wait_for_capability_change, target)
            except MissionCancelled as stop:
                self._record_stopped_attempt(mission_id, target_id, attempt, park_run_id, retries, stop)
                return None
            recovery = result.get("recovery") if isinstance(result, dict) else None
            if not isinstance(recovery, dict) or result.get("target_state") == mission.TARGET_PLACED:
                return result
            # WRITTEN DOWN WHATEVER FOLLOWS: what this attempt came to, where it left the robot, and what a press can do.
            outcome_class = recovery.get("outcome_class")
            # A Retry is offered only when the budget has one, the run target can park, the runtime admits a Retry at all
            # (retry_refusal) and the ending is one a Retry may follow (recovery["retry"], mission_execution.retry_allowed).
            offer = (retries < RETRY_BUDGET_PER_TARGET and self.can_park() and self.retry_refusal() is None
                     and recovery.get("retry") is not False)
            actions = mission.next_actions(outcome_class, recovery.get("stop_state"), retry_left=offer)
            self._append(mission.target_attempt(
                mission_id, target_id, attempt=attempt, run_id=result.get("run_id"), park_run_id=park_run_id,
                outcome_class=outcome_class, reason_code=recovery.get("reason_code"), phase=recovery.get("phase"),
                measurements=recovery.get("measurements"), observation_ids=recovery.get("observation_ids"),
                stop_state=recovery.get("stop_state"), next_actions=actions, budget_left=RETRY_BUDGET_PER_TARGET - retries,
                t_wall_utc=self._now(), evidence=result.get("evidence") if isinstance(result.get("evidence"), dict) else None,
                detail=recovery.get("detail")))
            if outcome_class not in mission.HOLDING_CLASSES:
                return result
            # THE HOLD: the mission waits for the operator, and nothing moves until a press. The Event a press sets is
            # cleared BEFORE the hold is written: no press can be accepted for this hold until then (mission_not_held),
            # so whatever set it earlier belongs to an earlier hold and never resumes this one.
            self._retry_event_for(mission_id).clear()
            self._append(mission.target_waiting(mission_id, target_id, mission.WAITING_ON_OPERATOR, t_wall_utc=self._now()))
            automatic = (AUTOMATIC_RETRY and not automatic_used and outcome_class == mission.OUTCOME_RETRYABLE
                         and mission.NEXT_RETRY in actions)
            try:
                self._await_retry(mission_id, target_id, attempt, cancel, automatic=automatic)
            except MissionCancelled:
                return None
            automatic_used = automatic_used or automatic
            retries += 1
            self._append(mission.target_resumed(mission_id, target_id, t_wall_utc=self._now()))
            # THE PARK, then a fresh attempt. The park reads the stop before it starts anything, and so does the attempt.
            try:
                parked = self._run_target.park(mission_id, target_id)
            except MissionCancelled:
                return None
            park_run_id = parked.get("run_id")
            if not parked.get("ok"):
                return {"target_state": mission.TARGET_FAILED, "run_id": park_run_id,
                        "detail": f"{PARK_FAILED}:{parked.get('reason_code')}", "evidence": None}
            if cancel.is_set():
                return None
            attempt += 1

    def _record_stopped_attempt(self, mission_id: str, target_id: str, attempt: int, park_run_id, retries: int, stop) -> None:
        """A stop that ended a run the run target started (a RunStopped, which says where that run left the robot) is an
        attempt too: written down with the STOPPED class and the actions after the end. A stop with no run behind it (at a
        gate, before a spawn) writes nothing: nothing moved for this attempt."""
        stop_state = getattr(stop, "stop_state", None)
        if stop_state is None:
            return
        self._append(mission.target_attempt(
            mission_id, target_id, attempt=attempt, run_id=getattr(stop, "run_id", None), park_run_id=park_run_id,
            outcome_class=mission.OUTCOME_STOPPED, reason_code=None, phase=None, measurements=None, observation_ids=None,
            stop_state=stop_state, next_actions=mission.next_actions(mission.OUTCOME_STOPPED, stop_state, retry_left=False),
            budget_left=RETRY_BUDGET_PER_TARGET - retries, t_wall_utc=self._now()))

    def _await_retry(self, mission_id: str, target_id: str, attempt: int, cancel: threading.Event, *, automatic: bool) -> None:
        """The hold: returns when a Retry was asked for this hold, raises MissionCancelled on a stop. The stop is read
        first on every turn and once more after a Retry arrived, so a Retry and a Stop pressed together end STOPPED.

        A WAKE-UP IS NOT A PRESS: the hold resumes only when the record holds a Retry for THIS attempt (the request is
        written before the Event is set), and any other wake-up is cleared and the hold goes on waiting.

        The automatic retry (AUTOMATIC_RETRY, OFF) writes its own request, under the lock the stop writes under, and never
        after a stop. An operator's press accepted for this hold before it (the window after the hold's record) IS the
        retry: no second request is written, and the Event that press set is cleared, so it cannot resume a later hold."""
        retry = self._retry_event_for(mission_id)
        if automatic:
            with self._lock:
                if cancel.is_set():
                    raise MissionCancelled()
                retry.clear()
                if not self._retry_recorded(mission_id, target_id, attempt):
                    self._append(mission.retry_requested(mission_id, target_id, attempt=attempt, origin=RETRY_BY_AUTOMATIC,
                                                         t_wall_utc=self._now()))
            return
        while True:
            if cancel.is_set():
                raise MissionCancelled()
            if retry.wait(HOLD_POLL_S):
                with self._lock:
                    retry.clear()
                    if cancel.is_set():
                        raise MissionCancelled()
                    if self._retry_recorded(mission_id, target_id, attempt):
                        return

    def _retry_recorded(self, mission_id: str, target_id: str, attempt: int) -> bool:
        """Whether the record holds a Retry of `attempt` of the target. Called under self._lock, the lock request_retry
        writes under, so a request is either wholly on the record or not yet made."""
        state = self._folded().get(mission_id) or {}
        return any(entry.get("attempt") == attempt for entry in state.get("target_retries", {}).get(target_id) or [])

    def request_retry(self, *, mission_id: Optional[str] = None, origin: str = RETRY_BY_OPERATOR) -> dict:
        """A Retry of the attempt the mission holds on. Answered at once; the carrying thread parks and retries.

        REFUSED, WITH NOTHING WRITTEN AND NOTHING STARTED, when: the mission is unknown; a stop was asked for
        (mission_stopping); the mission does not wait for the operator (mission_not_held); nothing carries it any more
        (mission_not_carried: its runtime ended, and a Stop ends it); the runtime admits no Retry at all (retry_refusal:
        mission_retry_not_enabled or mission_retry_not_attended); the held attempt offers no retry (a cube may be held,
        the base is ahead, or the budget is spent: mission_retry_not_offered); this hold's Retry was already asked for; or
        the run target cannot park (mission_park_unavailable). Otherwise the request is WRITTEN DOWN (mission.
        retry_requested), under the lock a stop writes under, and only then handed to the hold."""
        with self._lock:
            table = self._folded()
            wanted = mission_id or self._mission_id
            state = table.get(wanted)
            if state is None:
                return {"ok": False, "reason_code": "unknown_mission"}
            if self._cancel_for(wanted).is_set() or state.get("stop_requested"):
                return {"ok": False, "reason_code": MISSION_STOPPING, "mission_id": wanted}
            if state["state"] != mission.MISSION_WAITING or state.get("waiting_reason") != mission.WAITING_ON_OPERATOR:
                return {"ok": False, "reason_code": MISSION_NOT_HELD, "mission_id": wanted}
            if not (self._carrying == wanted and self._thread is not None and self._thread.is_alive()):
                return {"ok": False, "reason_code": MISSION_NOT_CARRIED, "mission_id": wanted}
            refused = self.retry_refusal()
            if refused is not None:
                return {"ok": False, "reason_code": refused, "mission_id": wanted}
            target_id = state.get("selected_target")
            attempt = mission.latest_attempt(state, target_id)
            if attempt is None or not any(action in mission.RETRY_ACTIONS for action in attempt.get("next_actions") or []):
                return {"ok": False, "reason_code": RETRY_NOT_OFFERED, "mission_id": wanted}
            if mission.retry_asked_after(state, target_id, attempt):
                return {"ok": False, "reason_code": RETRY_ALREADY_REQUESTED, "mission_id": wanted}
            if not self.can_park():
                return {"ok": False, "reason_code": PARK_UNAVAILABLE, "mission_id": wanted}
            self._append(mission.retry_requested(wanted, target_id, attempt=attempt["attempt"], origin=origin, t_wall_utc=self._now()))
            self._retry_event_for(wanted).set()
            return {"ok": True, "mission_id": wanted, "target_id": target_id, "attempt": attempt["attempt"], "retrying": True}

    def _end(self, mission_id: str, state: str, detail) -> None:
        self._append(mission.mission_ended(mission_id, state, detail=detail, t_wall_utc=self._now()))

    def _end_stopped(self, mission_id: str, detail) -> None:
        """End the mission STOPPED from the carrying thread, UNDER THIS COORDINATOR'S LOCK, the one stop() writes under.

        When the stop was set by cancel_now and stop() has not yet recorded its request, the request is written here
        first, with the origin cancel_now was given, so the record never shows a mission ended STOPPED by an operator's
        stop without the stop that was asked for. Under the lock, the two writers of that request cannot both write it."""
        with self._lock:
            origin = self._pending_stops.get(mission_id)
            if origin is not None:
                state = self._folded().get(mission_id)
                if state is not None and not state["stop_requested"] and state["state"] not in mission.MISSION_TERMINAL:
                    self._append(mission.mission_stop_requested(mission_id, origin=origin, t_wall_utc=self._now()))
            self._end(mission_id, mission.MISSION_STOPPED, detail)

    # ---- activation ---------------------------------------------------------------------------------------
    def _read_slot(self) -> Optional[dict]:
        """One slot reading, or None when the capability adapter reads no slot detail. It NEVER raises: it is called
        after a submit, and an exception there would lose the record of an operation the resident may have applied."""
        reader = self._capability.read_slot
        if reader is None:
            return None
        try:
            reading = reader()
        except Exception:  # noqa: BLE001 -- a read that failed is a failed read, recorded as one
            reading = None
        if not isinstance(reading, dict):
            return {"read": cake_activation.READ_FAILED, "capability_active": False}
        return dict(reading)

    def submit_activation(self, candidate_key: str, *, mission_id: Optional[str] = None) -> dict:
        """Submit ONE prepared candidate for the mission that is waiting, and record all three outcomes apart.

        THE CANDIDATE IS NAMED, NEVER SUPPLIED. `candidate_key` selects among candidates this runtime prepared; the
        bytes come from that preparation and the caller contributes none of them.

        ONLY A WAITING MISSION MAY BE ACTIVATED FOR. An activation accepted at any other time would be a slot moved
        for no stated reason, and the record would not say what it was for.

        A STOPPED MISSION GETS NOTHING SUBMITTED. When that mission's stop was asked for (its event, or the request on
        the record), this answers mission_stopping BEFORE an attempt record is written, and it asks again after the
        slot is read and immediately before the attempt is written, because that read can take a while.

        THE WINDOW THAT CANNOT BE CLOSED FROM HERE. Between that last check and the submit, a stop can still land: the
        stop and the submit are on different threads and the resident offers no "only if the mission is still running"
        precondition. The submit then goes out. What holds even then is what the module docstring says: the answer is
        recorded against THIS mission with stopped_during_activation true, and the waiting thread refuses to resume.

        THE EVIDENCE IS KEPT. The attempt record carries the slot as read just before the submit; the answer record
        carries what the resident said about the transaction and the ONE read-back that decided the verdict.
        SUBMITTED means in flight (no answer yet, or the transaction still running); COMPLETED_NOT_OBSERVED means the
        transaction completed and the slot, read back, does not carry the capability. Only OBSERVED is an activation.

        ONE OPERATION IN FLIGHT, REFUSED RATHER THAN QUEUED. The checks, the attempt record, the submit and the answer
        record run under THIS mission's activation lock, which is only ever tried: a second call while the first holds
        it is answered activation_in_flight at once. Queueing it instead would send it the moment the first returned,
        which is the second ApplyPlan this exists to prevent. Under the lock the record is read again, and a mission
        whose newest activation is still SUBMITTED is refused the same way, naming that operation: a transaction the
        resident said is still running is retrieved by its uuid, never replaced by a fresh one on a guess. An answer
        (rejected, failed, completed or observed) ends that operation, and the next press is a new one.
        """
        table = self._folded()
        wanted = mission_id or self._mission_id
        state = table.get(wanted)
        if state is None:
            return {"ok": False, "reason_code": "unknown_mission"}
        cancel = self._cancel_for(wanted)
        # FIRST among the refusals that are about the mission, so a stop that has already ended the mission is still
        # answered as the stop it is rather than as "not waiting" or "in flight". Checked before the lock too, so a
        # press after a stop is answered as the stop whatever else is going on.
        if cancel.is_set() or state.get("stop_requested"):
            return {"ok": False, "reason_code": MISSION_STOPPING, "mission_id": wanted}
        lock = self._activation_lock_for(wanted)
        if not lock.acquire(blocking=False):
            return self._in_flight(wanted, self._folded().get(wanted))
        try:
            return self._submit_holding_lock(wanted, candidate_key, cancel)
        finally:
            lock.release()

    @staticmethod
    def _submitted_operation(state: Optional[dict]) -> Optional[str]:
        """The uuid of this mission's NEWEST activation when it is still SUBMITTED (in flight), else None."""
        if not state or not state.get("activation_order"):
            return None
        newest = state["activation_order"][-1]
        entry = state["activations"].get(newest) or {}
        return newest if entry.get("activation_state") == mission.ACTIVATION_SUBMITTED else None

    def _in_flight(self, mission_id: str, state: Optional[dict]) -> dict:
        """The activation_in_flight refusal. It names the operation in flight when the record already holds it; a
        press that arrives before the first one's attempt record is written gets null there, not a guess."""
        return {"ok": False, "reason_code": ACTIVATION_IN_FLIGHT, "mission_id": mission_id,
                "operation_uuid": self._submitted_operation(state)}

    def _submit_holding_lock(self, wanted: str, candidate_key: str, cancel: threading.Event) -> dict:
        """submit_activation's body, entered only by the holder of this mission's activation lock."""
        # AGAIN, UNDER THE LOCK. The record read before it may predate an operation that finished a moment ago, or a
        # stop written since.
        state = self._folded().get(wanted)
        if state is None:
            return {"ok": False, "reason_code": "unknown_mission"}
        if cancel.is_set() or state.get("stop_requested"):
            return {"ok": False, "reason_code": MISSION_STOPPING, "mission_id": wanted}
        if state["state"] != mission.MISSION_WAITING or state.get("waiting_reason") != mission.WAITING_ON_CAPABILITY:
            return {"ok": False, "reason_code": "mission_not_waiting_for_capability"}
        if self._submitted_operation(state) is not None:
            return self._in_flight(wanted, state)
        payload = self._capability.candidates.payload(candidate_key)
        if payload is None:
            return {"ok": False, "reason_code": "unknown_candidate",
                    "detail": self._capability.candidates.problem(candidate_key)}
        digest = self._capability.candidates.digest(candidate_key)
        operation_uuid = cake_activation.new_operation_uuid()
        slot_before = self._read_slot()
        if cancel.is_set():
            return {"ok": False, "reason_code": MISSION_STOPPING, "mission_id": wanted}
        # WRITTEN BEFORE THE SUBMIT, binding the uuid to the bytes. A crash between this line and the answer leaves
        # a record that says which operation was in flight and what it carried, which is what a retry needs.
        self._append(mission.activation_attempt(wanted, state.get("selected_target"), operation_uuid,
                                                candidate_key, digest, t_wall_utc=self._now(),
                                                slot_before=slot_before))
        answer = self._capability.submit(operation_uuid, payload)
        outcome = answer.get("outcome")
        # READ BACK WHATEVER THE OUTCOME. Only an ACCEPTED answer is decided by it; for the others it is evidence of
        # where the slot stood afterwards, which a request that timed out especially needs.
        slot_after = self._read_slot()
        # ONE DECIDER, shared with the robot's capability slot check (demo_runtime.capability_check), so the two surfaces
        # that submit an ApplyPlan cannot come to call the same answer and the same read-back two different things.
        activation_state, detail = activation_verdict(answer, slot_after, self._capability.observe)
        transaction = transaction_evidence(operation_uuid, answer)
        # AND IT RESUMES NOTHING. The target is not restarted from here under any outcome; a waiting mission resumes
        # by its own thread noticing the slot, and that thread refuses to leave the gate once a stop has been asked
        # for (see capability_available). This flag is what says so out loud, read from THIS mission's own event, so
        # a console that asked for a stop and then received a successful activation does not draw a mission that is
        # about to carry on, and a mission started since cannot make it read false.
        stopped = cancel.is_set()
        # THE ANSWER IS RECORDED WHATEVER BECAME OF THE MISSION. A stop that landed while this was in flight does not
        # make the operation un-happen: a slot may have moved, and a record that omitted that would say less than
        # happened.
        self._append(mission.activation_answer(wanted, operation_uuid, activation_state, outcome=outcome,
                                               transaction_phase=answer.get("transaction_phase"),
                                               transaction_uuid=answer.get("transaction_uuid"),
                                               detail=detail, t_wall_utc=self._now(), transaction=transaction,
                                               slot_after=slot_after, stopped_during_activation=stopped))
        return {"ok": True, "mission_id": wanted, "operation_uuid": operation_uuid,
                "activation_state": activation_state, "outcome": outcome,
                "transaction_phase": answer.get("transaction_phase"), "detail": detail,
                "stopped_during_activation": stopped, "transaction": transaction,
                "slot_before": slot_before, "slot_after": slot_after}

    def close(self) -> None:
        """Stop the mission before closing the file it writes to.

        Closing underneath a running thread would make the next append raise inside the mission, and the mission
        would then record itself as FAILED with the name of an error caused by the shutdown rather than by anything
        that happened to the robot. The carried mission's stop is asked for first and waited for. It is not written
        down as an operator's stop request: nobody asked for one, the runtime is ending.
        """
        carrying = self._carrying
        if carrying is not None:
            self._cancel_for(carrying).set()
        self.join(5.0)
        self._journal.close()


def refusing_after_stop(execution: ExecutionAdapter, cancel: threading.Event) -> ExecutionAdapter:
    """The adapter the machine is handed: the same one, with each of its five steps refusing once a stop was asked for.

    A REFUSAL IN FRONT OF EACH STEP, AND NOTHING ELSE. The check runs before the adapter's own callable is entered, so
    the callable's answer (a measured predicate, a proven builder's result, a base-zero read) is never replaced, only
    not asked for. A callable that is already running when the stop lands is NOT interrupted by this: it finishes or
    fails on its own, and a physical motion in progress is stopped by the guarded controller stop, not here. `log`,
    `attach_cancel` and `attach_mission` are passed through untouched.
    """

    def refusing(call: Callable) -> Callable:
        def step(*args, **kwargs):
            if cancel.is_set():
                raise MissionCancelled()
            return call(*args, **kwargs)
        return step

    return replace(execution, **{name: refusing(getattr(execution, name)) for name in MACHINE_STEPS})


def activation_verdict(answer: dict, slot_after: Optional[dict], observe: Callable[[], bool]) -> tuple:
    """(activation_state, detail): what ONE ApplyPlan answer and the ONE slot read taken after it come to.

    MOVED HERE UNCHANGED out of MissionCoordinator._submit_holding_lock, so the mission's activation and the capability
    slot check (demo_runtime.capability_check) decide with the same lines. Three things it keeps apart, exactly as before:
      - a TRANSACTION that completed is not an activation: only `slot_after`, read back, makes one OBSERVED;
      - a slot reading with no boolean verdict decided nothing, and is reported as a slot not read back rather than as
        a slot seen without the capability;
      - REQUEST_FAILED is Cake judging nothing, and is never reported as a rejection; a refusal BEFORE any transaction
        (REFUSED_BEFORE_TRANSACTION, status mutation_refused) is Cake judging, and is its own verdict.
    `observe` is called ONLY when there is no reading at all (a capability adapter that reads no slot detail), and then
    at most once. It is a callable rather than a value so a caller whose adapter does read the slot never asks twice."""
    outcome = answer.get("outcome")
    if outcome == cake_activation.ACCEPTED:
        # THE TRANSACTION COMPLETED. THAT IS NOT THE ACTIVATION. The slot is read back, and only that decides.
        # `is True` and .get, never ["capability_active"]: this runs AFTER the submit, and a reading that lacks its
        # verdict (or carries a non-boolean one) must neither raise and lose the answer record nor count as seen.
        observed = (slot_after.get("capability_active") is True if slot_after is not None
                    else bool(observe()))
        if observed:
            return mission.ACTIVATION_OBSERVED, answer.get("detail")
        # A reading with no boolean verdict decided nothing, so it is reported as a slot not read back rather
        # than as a slot seen without the capability.
        unread = slot_after is not None and (slot_after.get("read") != cake_activation.READ_OK
                                             or not isinstance(slot_after.get("capability_active"), bool))
        return mission.ACTIVATION_COMPLETED_NOT_OBSERVED, (
            "the transaction completed and the slot could not be read back" if unread
            else "the transaction completed but the slot does not carry the capability")
    if outcome == cake_activation.REJECTED_BY_CAKE:
        return mission.ACTIVATION_REJECTED, answer.get("detail")
    if outcome == REFUSED_BEFORE_TRANSACTION_OUTCOME:
        # CAKE JUDGED THE CANDIDATE and refused it before any transaction: never a failed request, never a transaction
        # that aborted, and nothing is in flight. The slot is not read to decide it; the refusal code names why.
        return mission.ACTIVATION_REFUSED_BEFORE_TRANSACTION, (
            answer.get("detail") or f"Cake refused the candidate before any transaction ({answer.get('reason_code') or 'no refusal code'})")
    if outcome == cake_activation.PENDING:
        # The resident answered and the transaction is still running: in flight, which is what SUBMITTED means.
        return mission.ACTIVATION_SUBMITTED, answer.get("detail")
    # REQUEST_FAILED: Cake judged nothing, and this is never reported as a rejection.
    return mission.ACTIVATION_FAILED, answer.get("detail")


def transaction_evidence(operation_uuid: str, answer: dict) -> dict:
    """What the resident's answer said about ONE operation, for the record, beside the verdict and never read as one."""
    return {"operation_uuid": operation_uuid, "transaction_uuid": answer.get("transaction_uuid"),
            "transaction_handle": answer.get("transaction_handle"),
            "transaction_phase": answer.get("transaction_phase"), "status": answer.get("status"),
            "reason_code": answer.get("reason_code"),
            # The epoch THE ANSWER printed, when it printed one. The epochs before and after the operation
            # are the two slot readings' own.
            "answer_epoch": answer.get("epoch")}


def refusal_text(refusal: ExecutionRefused) -> str:
    """`ClassName: sentence`, bounded. The class name is always there, so a refusal with no message still says what
    kind of refusal it was."""
    message = str(refusal).strip()
    name = type(refusal).__name__
    return f"{name}: {message[:MAX_REFUSAL_TEXT]}" if message else name


def _run_hybrid_target(execution: ExecutionAdapter, capability_available, wait_for_capability_change,
                       target: dict) -> dict:
    """Carry one target with the repository's own transition logic.

    Imported HERE rather than at module scope: `demo_runtime` is a packaged runtime and `autonomy` is a checkout
    layout, so a runtime that imported it to be loaded at all would fail in a pinned bundle. A coordinator driven
    with an injected `run_target` never reaches this function.
    """
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from autonomy.manipulation.hybrid_pick_place import (PickPlaceDeps, STATE_DONE,  # noqa: E402
                                                         run_hybrid_pick_place)

    deps = PickPlaceDeps(
        read_pose=execution.read_pose,
        read_base=execution.read_base,
        move_arm=execution.move_arm,
        move_base_sustained=execution.move_base_sustained,
        confirm=execution.confirm,
        # The adapter's own sink when it has one (the robot-side adapter); otherwise the events go nowhere, exactly as
        # a rehearsal has always had them go.
        log=execution.log if execution.log is not None else (lambda *args, **kwargs: None),
        capability_available=capability_available,
        wait_for_capability_change=wait_for_capability_change,
    )
    result = run_hybrid_pick_place(deps)
    placed = result.final_state == STATE_DONE
    return {"target_state": mission.TARGET_PLACED if placed else mission.TARGET_FAILED,
            "detail": result.abort_reason, "run_id": target.get("run_id"),
            "final_state": result.final_state}
