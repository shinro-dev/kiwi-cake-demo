# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""The seam between the single-cube cycle's orchestrator and its stage policies.

bin/laptop/manipulation/run_single_cube_cycle.py runs as a script. A stage policy module (policy_search, policy_align,
policy_grasp, policy_placement, policy_return, all in this directory) therefore never imports it: an import would load a
second copy of the controller with its own globals, its own envelope words and its own CycleFailed. The controller hands
each stage procedure two things instead:

  ctx  the Cycle object itself. Every primitive a stage calls (run_pose, grip, observe, state, failed, events, the base
       pulse family) is called on it, exactly as run_cycle called it on `self`, so a test that scripts those methods on a
       Cycle (the scripted machine of autonomy/tests/test_run_single_cube_cycle.py) scripts the stage too.
  ctl  a ControllerView over the controller's module namespace: the geometry gates, the pure formulas and the constants,
       read by name when the stage reads them. A name the controller rebinds while it runs (the envelope words of a run, the
       saved pose path of a runtime configuration) and a name a test patches on the controller module are the names a stage
       sees. Nothing is copied, and a stage never assigns into it.

CycleFailed lives here, and the controller imports it back, so every `except CycleFailed` in the controller, in a stage
and in a test catches the one class.

THE POLICY IDENTITY. A stage policy is (stage, selection, digest): the stage's name, the selection this run made among its
behaviours ("proven" with no word, else its sorted words joined by "-"), and the sha256 of the stage module's bytes. Each
stage module declares one StagePolicy with the predicates it decides, and decorates its procedures with
StagePolicy.decides: while one runs, the events object (events.enter_policy / exit_policy) stamps every predicate that stage
declares with rule_ref "<stage>.<selection>" and rule_digest "sha256:<hex>" (event.schema.json, predicate.evaluated). A
predicate no running stage declares (the pinned base primitives', the start pose's) carries neither. A fix to a stage
module's procedure code changes that stage's digest and no other, so a record shows which rule decided and what changed.
The digest names the module's bytes as they were imported, which in a bundled run are the bundle's. Its scope is that
module alone: the pure formulas and the constants a stage reads through ctl (alignment_from_bbox,
coarse_pregrasp_from_low_pose, placement_plan, base_advance_needed_mm, closed_on_something, the release margins, every
constant) live in the controller, and no stage digest covers them. A changed numeric or boolean constant shows its value in run.config's
effective constants; a change to the controller's code (a formula included) shows only in the controller bundle's digest,
which the runtime records as artifact_digest, and a controller started by hand records none.

THE WORDS (docs/autonomous-demo/overnight-plan-2026-09-26.md, section 2.6). A behaviour change of a stage runs only under
one of that stage's words, OFF by default: with no word a stage is the proven one. Each stage module declares the words it
knows and the ones QUALIFIED (a word joins its QUALIFIED set only in the commit that adds its qualification record). A run's
words travel in one key for every stage (KC_PLACEMENT_ENVELOPE, the placement track's name, kept); the controller refuses,
before the robot is constructed, a word no stage declares, a repeated word, a later placement word without projected (the
placement track's rule), and in a LIVE run a word no stage has qualified unless the attended label is named.
"""
import functools
import hashlib
import re
from pathlib import Path

STAGES = ("search", "align", "grasp", "placement", "return")
PROVEN = "proven"  # the selection of a stage that runs with none of its words
_ID_MAX = 64  # common#/$defs/id
WORD_SHAPE = re.compile(r"[a-z][a-z0-9_]{0,31}")  # demo_runtime.config's shape of an envelope word


def module_digest(path) -> str:
    """"sha256:<hex>" of the file's bytes."""
    return "sha256:" + hashlib.sha256(Path(path).read_bytes()).hexdigest()


def selection_of(words) -> str:
    """PROVEN for no word, else the distinct words sorted and joined by "-" (a word is [a-z0-9_])."""
    words = sorted(set(words or ()))
    return "-".join(words) if words else PROVEN


def rule_ref_of(stage: str, words) -> str:
    """"<stage>.<selection>", an id of the contract (at most 64 characters): a selection too long for that is named by the
    first 12 hex digits of its own sha256, "<stage>.words-<hex>", and run.config states the selection in full."""
    ref = f"{stage}.{selection_of(words)}"
    return ref if len(ref) <= _ID_MAX else f"{stage}.words-{hashlib.sha256(selection_of(words).encode('ascii')).hexdigest()[:12]}"


class CycleFailed(RuntimeError):
    """A physical predicate was not met and bounded recovery is exhausted. The arm HOLDS."""


class ControllerView:
    """The controller's module namespace as a stage policy reads it: `ctl.NAME` is `namespace["NAME"]` at the moment it is
    read. Read only. The namespace is any mapping (the controller passes its globals(); a test may pass a ChainMap of
    overrides in front of them)."""

    __slots__ = ("_namespace",)

    def __init__(self, namespace):
        object.__setattr__(self, "_namespace", namespace)

    def __getattr__(self, name: str):
        try:
            return self._namespace[name]
        except KeyError:
            raise AttributeError(f"the controller defines no {name!r}") from None

    def __setattr__(self, name: str, value) -> None:
        raise AttributeError("a stage policy never assigns into the controller")


class StagePolicy:
    """One stage's identity: its name, the digest of its module's bytes (read once, when the module is imported) and the
    predicates it decides. `decides` wraps a stage function (ctx, ctl, ...) so that the events object knows, while it runs,
    which rule decides those predicates."""

    def __init__(self, stage: str, module_file: str, predicates, words=(), qualified=frozenset()):
        if stage not in STAGES:
            raise ValueError(f"{stage!r} is not a stage of the cycle")
        words, qualified = tuple(words), frozenset(qualified)
        if any(WORD_SHAPE.fullmatch(word) is None for word in words) or len(set(words)) != len(words):
            raise ValueError(f"the {stage} stage declares a word of the wrong shape, or one word twice")
        if not qualified <= set(words):
            raise ValueError(f"the {stage} stage qualifies a word it does not declare")
        self.stage, self.digest, self.predicates = stage, module_digest(module_file), frozenset(predicates)
        self.words, self.qualified = words, qualified

    def run_words(self, ctl) -> tuple:
        """This run's words that belong to this stage, in the order the run named them (ctl.PLACEMENT_ENVELOPE)."""
        return tuple(word for word in ctl.PLACEMENT_ENVELOPE if word in self.words)

    def identity(self, ctl) -> dict:
        words = self.run_words(ctl)
        return {"stage": self.stage, "selection": selection_of(words), "rule_ref": rule_ref_of(self.stage, words), "rule_digest": self.digest, "predicates": self.predicates}

    def decides(self, function):
        @functools.wraps(function)
        def decided(ctx, ctl, *args, **kwargs):
            try:
                identity = self.identity(ctl)
            except Exception:  # noqa: BLE001 -- the identity is evidence: a defect in it costs the rule_ref, never the stage
                identity = None
            ctx.events.enter_policy(identity)  # None: the stage runs, and its predicates carry no rule
            try:
                return function(ctx, ctl, *args, **kwargs)
            finally:
                ctx.events.exit_policy()
        decided.stage_policy = self
        return decided


def declared_words(policies) -> tuple:
    """Every word the stages declare, in stage order. A word two stages declare is a ValueError: a word opens one stage."""
    words = tuple(word for policy in policies for word in policy.words)
    if len(set(words)) != len(words):
        raise ValueError("two stages declare the same word")
    return words


def qualified_words(policies) -> frozenset:
    return frozenset(word for policy in policies for word in policy.qualified)


def envelope_refusal(words, policies, placement_words):
    """None when `words` (a run's words, in order) may run, else which rule refuses them: "unknown" (a word no stage
    declares, or a repeated word) or "later_placement_word_without_projected" (the placement track's rule: every later
    placement word builds on projected, the first of `placement_words`)."""
    words = tuple(words)
    if any(word not in declared_words(policies) for word in words) or len(set(words)) != len(words):
        return "unknown"
    if any(word in tuple(placement_words)[1:] for word in words) and tuple(placement_words)[0] not in words:
        return "later_placement_word_without_projected"
    return None
