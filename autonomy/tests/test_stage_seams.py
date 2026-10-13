#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# autonomy/tests/test_stage_seams.py: the seam between the single-cube cycle's orchestrator and its stage policy modules
# (docs/autonomous-demo/overnight-plan-2026-09-26.md, section 2 and track P0a). What is proven here is the SHAPE of the
# seam: one CycleFailed class, a read-only live view of the controller, no stage module that imports the controller, every
# controller name a stage reads present in the controller, run_cycle an orchestrator of the stage procedures, and every
# stage module inside the controller bundle. That the extraction changed no behaviour is proven elsewhere:
# test_cycle_behavior_preserved.py (the differential against the tagged controller, not edited), test_frozen_primitives.py
# (not edited), test_run_single_cube_cycle.py, test_cycle_events.py and test_cycle_mission_mode.py. Two of these were edited
# by the extraction, and only their source readers: test_cycle_events.ReasonCodeTest.source() and its regex now read the
# stage modules and accept ctx, and two source readers in test_run_single_cube_cycle.py follow the code that moved. No
# assertion of either was loosened.
# No robot, no network. Run directly: python3 autonomy/tests/test_stage_seams.py
import ast
import collections
import contextlib
import inspect
import io
import math
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bin" / "laptop" / "manipulation"))
sys.path.insert(0, str(ROOT / "packages" / "demo-runtime"))
sys.path.insert(0, str(ROOT / "autonomy" / "tests"))

import numpy as np  # noqa: E402

import run_single_cube_cycle as cycle  # noqa: E402
import stage_harness as harness  # noqa: E402
import stage_policy  # noqa: E402

MANIPULATION = ROOT / "autonomy" / "manipulation"
STAGE_MODULES = ("policy_search", "policy_align", "policy_grasp", "policy_placement", "policy_return")
# The stage procedures run_cycle hands the Cycle to, in the order it calls them.
PROCEDURES = (("policy_search", "run_search"), ("policy_align", "run_pregrasp_align"), ("policy_grasp", "run_grasp"), ("policy_placement", "run_placement"),
              ("policy_return", "run_return"))
PRESENT = tuple(name for name in STAGE_MODULES if (MANIPULATION / f"{name}.py").is_file())


def _tree(name: str) -> ast.AST:
    return ast.parse((MANIPULATION / f"{name}.py").read_text(encoding="utf-8"))


class OneFailureClass(unittest.TestCase):
    def test_the_controller_and_the_seam_raise_and_catch_the_same_class(self):
        self.assertIs(cycle.CycleFailed, stage_policy.CycleFailed)
        self.assertTrue(issubclass(cycle.CycleFailed, RuntimeError), "the controller's `except RuntimeError` after `except CycleFailed` keeps its order")


class TheControllerView(unittest.TestCase):
    def test_a_name_is_read_when_the_stage_reads_it_never_copied(self):
        namespace = {"PLACEMENT_ENVELOPE": ()}
        view = stage_policy.ControllerView(namespace)
        self.assertEqual((), view.PLACEMENT_ENVELOPE)
        namespace["PLACEMENT_ENVELOPE"] = ("projected",)
        self.assertEqual(("projected",), view.PLACEMENT_ENVELOPE)

    def test_it_is_read_only_and_an_unknown_name_is_an_attribute_error(self):
        view = stage_policy.ControllerView({})
        with self.assertRaises(AttributeError):
            view.NOT_A_NAME  # noqa: B018
        with self.assertRaises(AttributeError):
            view.PLACEMENT_ENVELOPE = ("projected",)

    def test_a_test_may_put_overrides_in_front_of_the_controller(self):
        view = stage_policy.ControllerView(collections.ChainMap({"BASE_RETURN_TOLERANCE_MM": 1.0}, vars(cycle)))
        self.assertEqual(1.0, view.BASE_RETURN_TOLERANCE_MM)
        self.assertIs(cycle.placement_plan, view.placement_plan)

    def test_the_controller_hands_its_own_live_namespace(self):
        self.assertIs(cycle.placement_plan, cycle._CONTROLLER_VIEW.placement_plan)
        saved = cycle.PLACEMENT_ENVELOPE
        try:
            cycle.set_placement_envelope(("projected",))
            self.assertEqual(("projected",), cycle._CONTROLLER_VIEW.PLACEMENT_ENVELOPE)
        finally:
            cycle.set_placement_envelope(saved)


class TheStageModules(unittest.TestCase):
    def test_every_stage_module_of_the_plan_exists_and_has_its_procedure(self):
        self.assertEqual(STAGE_MODULES, PRESENT)
        self.assertEqual(STAGE_MODULES, tuple(name for name, _ in PROCEDURES))

    def test_no_stage_module_imports_the_controller(self):
        for name in PRESENT + ("stage_policy",):
            with self.subTest(name):
                for node in ast.walk(_tree(name)):
                    if isinstance(node, ast.Import):
                        self.assertNotIn("run_single_cube_cycle", [alias.name for alias in node.names])
                    elif isinstance(node, ast.ImportFrom):
                        self.assertNotEqual("run_single_cube_cycle", node.module)

    def test_every_controller_name_a_stage_reads_is_defined_by_the_controller(self):
        for name in PRESENT:
            with self.subTest(name):
                read = {node.attr for node in ast.walk(_tree(name)) if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "ctl"}
                self.assertTrue(read, "a stage that reads nothing through ctl is not wired to the controller")
                self.assertEqual(set(), {attr for attr in read if not hasattr(cycle, attr)})

    def test_no_stage_module_keeps_its_own_copy_of_a_controller_name(self):
        """A stage reads the controller's constants and formulas through ctl; a copy under the same name would not follow a
        patch on the controller (a replay's counterfactual) or a change to it."""
        for name in PRESENT:
            with self.subTest(name):
                module = _stage_module(name)
                own = {attr for attr, value in vars(module).items() if not attr.startswith("_") and getattr(value, "__module__", name) in (name, None)
                       and not inspect.ismodule(value)}
                self.assertEqual(set(), {attr for attr in own if attr in vars(cycle)})

    def test_run_cycle_hands_every_stage_the_cycle_and_the_controller_view_in_order(self):
        source = inspect.getsource(cycle.Cycle.run_cycle)
        at = []
        for name, procedure in PROCEDURES:
            with self.subTest(name):
                call = f"{name}.{procedure}(self, _CONTROLLER_VIEW"
                self.assertEqual(1, source.count(call))
                at.append(source.index(call))
        self.assertEqual(sorted(at), at, "run_cycle calls the stages out of their order")

    def test_run_cycle_decides_nothing_a_stage_decides(self):
        """The orchestrator raises one failure of its own, the start pose, and states one state, the envelope words; every
        other raise site and every other state is a stage's."""
        source = inspect.getsource(cycle.Cycle.run_cycle)
        self.assertEqual(1, source.count("raise self.failed("))
        self.assertIn('raise self.failed("start_pose_not_low_scan"', source)
        self.assertEqual(1, source.count("self.state("))
        self.assertIn('self.state("PLACEMENT_ENVELOPE"', source)
        self.assertNotIn("self.run_pose(", source)
        self.assertNotIn("self.grip(", source)


def _still_frame():
    frame = np.full((720, 1280), 120, np.uint8)
    frame[300:660, 380:800] = 200
    frame[300:380, 380:800] = 90
    return frame


class TheStageHarness(unittest.TestCase):
    """stage_harness.py runs one stage alone on a scripted Cycle: what a track builds its own stage tests on."""

    def setUp(self):
        self.ctl = harness.controller_view()
        self.quiet = contextlib.redirect_stdout(io.StringIO())
        self.quiet.__enter__()
        self.addCleanup(self.quiet.__exit__, None, None, None)

    def test_search_ends_at_done_already_placed_for_a_cube_inside_the_pad_and_commands_nothing(self):
        import policy_search

        record = harness.cube_record([515, 485, 590, 581], contact_px=[553.0, 545.0], cube_center_pad_m=[0.0, 0.0], pad_frame=harness.pad_frame())
        ctx = harness.HarnessCycle({"observe": harness.view(harness.LOW_POSE, [record])})
        found = policy_search.run_search(ctx, self.ctl, {"placed": False}, ctx.pose())
        self.assertEqual({"placed": True, "note": "the cube footprint is already inside the pad"}, found.done)
        self.assertEqual(["SEARCH", "DONE_ALREADY_PLACED"], ctx.states())
        self.assertEqual(["observe"], ctx.labels())
        self.assertEqual([True], [entry["value"] for entry in ctx.events.predicates("already_placed")])

    def test_align_ends_align_cube_not_detected_with_the_recognizers_reason_when_the_pregrasp_view_is_empty(self):
        import policy_align

        seen = harness.cube_record([515, 485, 590, 581], contact_px=[553.0, 545.0], cube_center_pad_m=[0.0145, -0.1825], pad_frame=harness.pad_frame())
        ctx = harness.HarnessCycle({"perception:PREGRASP": [[harness.cube_record(None)]]})  # one call, one perception list
        coarse = policy_align.coarse_pregrasp(ctx, self.ctl, seen, ctx.pose())
        with self.assertRaises(stage_policy.CycleFailed) as failure:
            policy_align.run_pregrasp_align(ctx, self.ctl, ctx.pose(), coarse, seen, -35.0)
        self.assertEqual("align_cube_not_detected", harness.failure_code(ctx, failure.exception))
        self.assertIn("harness: no proposal verified", str(failure.exception))
        self.assertEqual(["PREGRASP (non-contact)"], ctx.labels("run_pose"))

    def test_grasp_closes_three_times_on_nothing_then_ends_grasp_missed_with_the_jaws_open(self):
        import policy_grasp

        ctx = harness.HarnessCycle({"grip:latch": [66.9, 66.9, 66.9]}, pose=harness.DESCENDED_POSE)
        with self.assertRaises(stage_policy.CycleFailed) as failure:
            policy_grasp.run_grasp(ctx, self.ctl, {"object_secured": False}, harness.view(harness.DESCENDED_POSE), {"radial_mm": 0.0, "lateral_mm": 3.0, "aligned": True})
        self.assertEqual("grasp_missed", harness.failure_code(ctx, failure.exception))
        self.assertEqual(["latch", "open"] * 3, ctx.labels("grip"))
        self.assertEqual(2, ctx.states().count("GRASP_RETRY_ADVANCE"))

    def test_grasp_secures_a_cube_that_rises_with_the_jaws(self):
        import policy_grasp

        ctx = harness.HarnessCycle({"grip:latch": [68.4], "frame": lambda _ctx: _still_frame()}, pose=harness.DESCENDED_POSE)
        summary = {"object_secured": False}
        held = policy_grasp.run_grasp(ctx, self.ctl, summary, harness.view(harness.DESCENDED_POSE), {"radial_mm": 0.0, "lateral_mm": 3.0, "aligned": True})
        self.assertTrue(summary["object_secured"])
        self.assertEqual(["APPROACH", "STRADDLING", "GRASP_CLOSE", "OBJECT_SECURED"], ctx.states())
        self.assertEqual(["VERIFICATION lift", "CLEARANCE lift", "CONFIRM lift"], ctx.labels("run_pose")[-3:])
        self.assertEqual(68.4, held.closed["gripper"])

    def test_return_is_silent_when_the_base_never_moved_and_the_cube_is_placed(self):
        import policy_return

        ctx = harness.HarnessCycle()
        summary = {"placed": True, "base_returned": True}
        self.assertIs(summary, policy_return.run_return(ctx, self.ctl, summary, advanced=0.0, anchor=[0.0, 0.0], axis=(0.0, 1.0), rate=100.0, final={}, worst=40.0, margin=50.0, placed={}))
        self.assertEqual([], ctx.calls)

    def test_return_ends_base_not_returned_when_the_measured_remainder_exceeds_the_tolerance(self):
        import policy_return

        ctx = harness.HarnessCycle({"return_base": (12.0, 1.5)})
        with self.assertRaises(stage_policy.CycleFailed) as failure:
            policy_return.run_return(ctx, self.ctl, {"placed": True, "base_returned": True}, advanced=60.0, anchor=[0.0, 0.0], axis=(0.0, 1.0), rate=100.0, final={}, worst=40.0,
                                     margin=50.0, placed={})
        self.assertEqual("base_not_returned", harness.failure_code(ctx, failure.exception))
        self.assertEqual([False], [entry["value"] for entry in ctx.events.predicates("base_returned")])

    def test_a_constant_can_be_overridden_for_one_stage_run(self):
        import policy_return

        ctx = harness.HarnessCycle({"return_base": (12.0, 1.5)})
        summary = {"placed": True, "base_returned": True}
        policy_return.run_return(ctx, harness.controller_view(BASE_RETURN_TOLERANCE_MM=15.0), summary, advanced=60.0, anchor=[0.0, 0.0], axis=(0.0, 1.0), rate=100.0, final={},
                                 worst=40.0, margin=50.0, placed={})
        self.assertTrue(summary["base_returned"])

    def test_a_script_that_runs_out_fails_the_test_loudly(self):
        ctx = harness.HarnessCycle({"grip:latch": [66.9]})
        ctx.grip(cycle.GRIPPER_LATCH, "latch")
        with self.assertRaises(harness.ScriptExhausted):
            ctx.grip(cycle.GRIPPER_LATCH, "latch")


# Predicates a stage decides that are stated through an events hook rather than by a predicate() call in its own module.
STATED_THROUGH_A_HOOK = {"policy_search": {"placement_plan"}}


def _stage_module(name: str):
    import importlib

    return importlib.import_module(name)


class ThePolicyIdentity(unittest.TestCase):
    """rule_ref and rule_digest on every predicate a stage decides; the five identities in run.config; one
    capability_output_applied per relayed pulse (docs/autonomous-demo/overnight-plan-2026-09-26.md, section 2.4, P0a)."""

    def test_each_stage_declares_exactly_the_predicates_its_module_evaluates(self):
        for name in STAGE_MODULES:
            with self.subTest(name):
                evaluated = {node.args[0].value for node in ast.walk(_tree(name)) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                             and node.func.attr == "predicate" and node.args and isinstance(node.args[0], ast.Constant)}
                policy = _stage_module(name).POLICY
                self.assertEqual(evaluated | STATED_THROUGH_A_HOOK.get(name, set()), set(policy.predicates))
                self.assertEqual(name.replace("policy_", ""), policy.stage)

    def test_the_digest_is_the_sha256_of_the_module_bytes(self):
        import hashlib

        for name in STAGE_MODULES:
            with self.subTest(name):
                self.assertEqual("sha256:" + hashlib.sha256((MANIPULATION / f"{name}.py").read_bytes()).hexdigest(), _stage_module(name).POLICY.digest)
        self.assertEqual(len(STAGE_MODULES), len({_stage_module(name).POLICY.digest for name in STAGE_MODULES}))

    def test_every_stage_function_the_controller_calls_is_decided_under_its_stage(self):
        for name in STAGE_MODULES:
            module = _stage_module(name)
            for function_name, function in inspect.getmembers(module, inspect.isfunction):
                if function.__module__ != name or list(inspect.signature(function).parameters)[:2] != ["ctx", "ctl"]:
                    continue  # a pure function of the stage (a formula, a bound) runs under whoever calls it
                with self.subTest(f"{name}.{function_name}"):
                    self.assertIs(module.POLICY, getattr(function, "stage_policy", None), "a stage function that runs without its stage's identity")

    def test_the_rule_ref_is_a_contract_id_proven_or_the_sorted_words(self):
        import re

        self.assertEqual("placement.proven", stage_policy.rule_ref_of("placement", ()))
        self.assertEqual("placement.projected-side_offset", stage_policy.rule_ref_of("placement", ("side_offset", "projected")))
        long = stage_policy.rule_ref_of("placement", ("projected", "side_offset", "reach_after_advance", "placed_multiview", "base_lateral"))
        self.assertTrue(long.startswith("placement.words-"))
        for ref in (long, stage_policy.rule_ref_of("return", ())):
            self.assertRegex(ref, r"^[A-Za-z0-9_.-]{1,64}$")
        self.assertIsNone(re.search(r"[^A-Za-z0-9_.+ -]", stage_policy.selection_of(("projected", "side_offset"))), "a selection is a named value's text")

    def test_the_harness_sees_each_stage_enter_and_leave_its_policy(self):
        import policy_return

        ctx = harness.HarnessCycle()
        policy_return.run_return(ctx, harness.controller_view(), {"placed": True, "base_returned": True}, advanced=0.0, anchor=[0.0, 0.0], axis=(0.0, 1.0), rate=100.0,
                                 final={}, worst=40.0, margin=50.0, placed={})
        kinds = [kind for kind, _args, _kwargs in ctx.events.calls]
        self.assertEqual(["enter_policy", "exit_policy"], kinds)
        entered = ctx.events.calls[0][1][0]
        self.assertEqual(("return", "proven", "return.proven", policy_return.POLICY.digest), (entered["stage"], entered["selection"], entered["rule_ref"], entered["rule_digest"]))


class AnIdentityDefectCostsEvidenceOnly(unittest.TestCase):
    def test_a_stage_whose_identity_cannot_be_computed_still_runs_and_its_predicates_carry_no_rule(self):
        import types

        import policy_return
        from test_cycle_events import Recorder

        recorder = Recorder()
        events = cycle.CycleEvents(recorder)
        ctx = harness.HarnessCycle({"return_base": (12.0, 1.5)})
        ctx.events = events
        broken = stage_policy.ControllerView(types.MappingProxyType({name: value for name, value in vars(cycle).items() if name != "PLACEMENT_ENVELOPE"}))
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(stage_policy.CycleFailed):
            policy_return.run_return(ctx, broken, {"placed": True, "base_returned": True}, advanced=60.0, anchor=[0.0, 0.0], axis=(0.0, 1.0), rate=100.0, final={}, worst=40.0,
                                     margin=50.0, placed={})
        (returned,) = [record["payload"] for record in recorder.records if record["kind"] == "predicate.evaluated" and record["payload"]["predicate"] == "base_returned"]
        self.assertNotIn("rule_ref", returned)
        self.assertEqual([], events._policies, "the placeholder was popped: no later predicate is stamped with a stale rule")  # noqa: SLF001


class TheIdentityOnTheRecord(unittest.TestCase):
    """A whole scripted cycle with events on (the scripted World of test_cycle_behavior_preserved.py): what the record says."""

    def setUp(self):
        import test_run_single_cube_cycle as fixtures

        self.directory = fixtures.use_synthetic_runtime(self)
        cycle.use_runtime_paths(cycle.cli.runtime_paths({}))

    def _run(self, **options):
        import test_cycle_events

        return test_cycle_events.EventRun(self, **options)

    def _owner(self):
        owners = {}
        for name in STAGE_MODULES:
            for predicate in _stage_module(name).POLICY.predicates:
                owners.setdefault(predicate, []).append(name)
        return owners

    def test_every_stage_predicate_names_its_rule_and_digest_and_no_other_predicate_does(self):
        sys.path.insert(0, str(ROOT / "packages" / "demo-contracts" / "python"))
        import demo_contracts

        written = self._run()
        self.assertEqual(("returned", 0), written.run["ending"], written.run["stdout"][-2000:])
        owners, stamped = self._owner(), set()
        for line in written.of_kind("predicate.evaluated"):
            self.assertEqual([], demo_contracts.validate("event", line))
            payload = line["payload"]
            if payload["predicate"] in owners and payload["predicate"] != "base_returned" or payload.get("rule_ref") is not None:
                stage = payload["rule_ref"].split(".")[0]
                self.assertIn(f"policy_{stage}", owners[payload["predicate"]], payload)
                self.assertEqual(f"{stage}.proven", payload["rule_ref"])
                self.assertEqual(_stage_module(f"policy_{stage}").POLICY.digest, payload["rule_digest"])
                stamped.add(payload["predicate"])
            else:
                self.assertNotIn("rule_ref", payload, payload["predicate"])
        self.assertTrue({"already_placed", "aligned", "closed_on_something", "object_secured", "placement_plan", "release_margin", "placed", "base_returned"} <= stamped, stamped)
        self.assertEqual([], [line["payload"]["predicate"] for line in written.predicates("base_measured") if "rule_ref" in line["payload"]], "a pinned primitive's predicate is no stage's rule")

    def test_a_predicate_a_stage_asks_another_stage_to_decide_carries_the_deciding_stage(self):
        written = self._run()
        (band,) = written.predicates("pregrasp_cube_within_reach")
        self.assertEqual("align.proven", band["payload"]["rule_ref"], "the pickup band is ALIGN's, asked for by SEARCH")
        self.assertEqual("search.proven", written.predicates("already_placed")[0]["payload"]["rule_ref"])

    def test_run_config_names_all_five_stage_policies(self):
        written = self._run()
        summary = {item["name"]: item["value"] for item in written.of_kind("run.config")[0]["payload"]["summary"]}
        for name in STAGE_MODULES:
            stage = name.replace("policy_", "")
            with self.subTest(stage):
                self.assertEqual("proven", summary[f"policy.{stage}.selection"])
                self.assertEqual(_stage_module(name).POLICY.digest.split(":", 1)[1], summary[f"policy.{stage}.digest"])

    def test_under_a_placement_word_only_the_placement_rule_names_it(self):
        written = self._run(extra_env={"KC_PLACEMENT_ENVELOPE": "projected"})
        summary = {item["name"]: item["value"] for item in written.of_kind("run.config")[0]["payload"]["summary"]}
        self.assertEqual("projected", summary["policy.placement.selection"])
        self.assertEqual("proven", summary["policy.search.selection"])
        refs = {line["payload"]["predicate"]: line["payload"].get("rule_ref") for line in written.of_kind("predicate.evaluated")}
        self.assertEqual("placement.projected", refs["release_margin"])
        self.assertEqual("grasp.proven", refs["object_secured"])


class CapabilityOutputApplied(unittest.TestCase):
    """One predicate per base pulse the capability relay ran, stated where the pulse ended (CycleEvents.base_stop_confirmed)."""

    def test_each_relayed_pulse_is_stated_once_with_its_generation_pid_and_counts(self):
        import types

        from test_cycle_events import Recorder

        recorder = Recorder()
        events = cycle.CycleEvents(recorder)
        relay = types.SimpleNamespace(pulses=[])
        move_base = types.SimpleNamespace(capability=relay, consumed_reads=[])
        relay.pulses.append({"slot_generation": 2, "producer_pid": 43289, "commands_accepted": 14, "commands_applied": 13, "commands_dropped": {"stale": 1}})
        events.base_stop_confirmed(True, move_base)
        relay.pulses.append({"slot_generation": 2, "producer_pid": 43289, "commands_accepted": 0, "commands_applied": 0, "commands_dropped": {}})
        events.base_stop_confirmed(True, move_base)
        events.base_stop_confirmed(True, types.SimpleNamespace(consumed_reads=[]))  # the proven reverse primitive: not the relay's, nothing stated
        stated = [record["payload"] for record in recorder.records if record["kind"] == "predicate.evaluated" and record["payload"]["predicate"] == "capability_output_applied"]
        self.assertEqual([True, False], [payload["value"] for payload in stated])
        first = {item["name"]: item["value"] for item in stated[0]["measurements"]}
        self.assertEqual({"slot_generation": 2, "producer_pid": 43289, "commands_accepted": 14, "commands_applied": 13, "commands_dropped": 1}, first)
        self.assertEqual(0, events.hook_errors)


class ReachedTheRobot(Exception):
    """make_robot was called: every refusal before the robot is constructed has passed."""


class TheWords(unittest.TestCase):
    """One word mechanism for every stage (docs/autonomous-demo/overnight-plan-2026-09-26.md, section 2.6): the placement
    track's key, words and rules carried across; each stage declares its words and qualified ones; the controller refuses a
    word no stage declares, and in a LIVE run an unqualified word without the attended label, before the robot exists."""

    def _with_word(self, stage="align", word="probe_align_word", qualified=False):
        """The controller with one more word declared by `stage` (qualified or not), for the length of one test."""
        from unittest import mock

        module = _stage_module(f"policy_{stage}")
        extra = stage_policy.StagePolicy(stage, module.__file__, module.POLICY.predicates, module.POLICY.words + (word,), module.POLICY.qualified | ({word} if qualified else set()))
        policies = tuple(extra if policy.stage == stage else policy for policy in cycle._STAGE_POLICIES)
        patch = mock.patch.object(cycle, "_STAGE_POLICIES", policies)
        patch.start()
        self.addCleanup(patch.stop)

    def test_each_stage_declares_its_words_and_qualifies_only_declared_ones(self):
        for name in STAGE_MODULES:
            module = _stage_module(name)
            with self.subTest(name):
                self.assertEqual(module.WORDS, module.POLICY.words)
                self.assertEqual(module.QUALIFIED, module.POLICY.qualified)
                self.assertTrue(module.QUALIFIED <= set(module.WORDS))
        self.assertEqual(len(cycle.envelope_words_declared()), len(set(cycle.envelope_words_declared())), "a word opens one stage")

    def test_the_placement_words_are_the_placement_tracks_and_the_runtime_knows_the_same(self):
        from demo_runtime import config as runtime_config

        # The placement track's four words stay declared; track P may declare more beside them.
        self.assertLessEqual({"projected", "near_reverse", "side_offset", "base_lateral"}, set(cycle.PLACEMENT_ENVELOPE_WORDS))
        self.assertIs(_stage_module("policy_placement").WORDS, cycle.PLACEMENT_ENVELOPE_WORDS)
        self.assertIs(_stage_module("policy_placement").QUALIFIED, cycle.PLACEMENT_ENVELOPE_QUALIFIED)
        self.assertEqual(runtime_config.PLACEMENT_ENVELOPE_WORDS, cycle.PLACEMENT_ENVELOPE_WORDS)

    def test_a_qualified_word_of_any_stage_is_named_by_its_qualification_record(self):
        import glob

        docs = ROOT / "docs" / "autonomous-demo"
        for name in STAGE_MODULES:
            for word in _stage_module(name).QUALIFIED:
                with self.subTest(word):
                    pattern = "placement-envelope-qualification-*.md" if name == "policy_placement" else f"qualification-{word}-*.md"
                    records = [path for path in glob.glob(str(docs / pattern)) if word in Path(path).read_text() and "Copyright 2026 Shinro SAS" in "".join(Path(path).read_text().splitlines(True)[:3])]
                    self.assertTrue(records, f"{word} is qualified and no {pattern} names it")

    def test_a_word_another_stage_declares_runs_alone_and_the_placement_rule_still_holds(self):
        self._with_word()
        env = cycle.PLACEMENT_ENVELOPE_ENV
        self.assertEqual(("probe_align_word",), cycle.placement_envelope_words({env: "probe_align_word"}), "a word of another stage needs no projected")
        self.assertEqual(("projected", "probe_align_word", "side_offset"), cycle.placement_envelope_words({env: "projected,probe_align_word,side_offset"}))
        for text in ("probe_align_word,near_reverse", "probe_align_word,probe_align_word", "probe_other_word", "probe_undeclared_word"):
            with self.subTest(text), self.assertRaises(cycle.ScanRefused):
                cycle.placement_envelope_words({env: text})

    def test_a_word_no_stage_declares_is_refused_and_the_refusal_names_the_declared_words_only(self):
        with self.assertRaises(cycle.ScanRefused) as refused:
            cycle.placement_envelope_words({cycle.PLACEMENT_ENVELOPE_ENV: "probe_align_word"})
        self.assertNotIn("probe_align_word", str(refused.exception), "the refusal echoes the value")
        self.assertIn("projected", str(refused.exception))

    def test_each_stage_selects_only_its_own_words(self):
        self._with_word()
        view = stage_policy.ControllerView({"PLACEMENT_ENVELOPE": ("projected", "probe_align_word")})
        policies = {policy.stage: policy for policy in cycle._STAGE_POLICIES}
        self.assertEqual("align.probe_align_word", policies["align"].identity(view)["rule_ref"])
        self.assertEqual("placement.projected", policies["placement"].identity(view)["rule_ref"])
        self.assertEqual("grasp.proven", policies["grasp"].identity(view)["rule_ref"])

    def _live_main(self, words, attended=None):
        from unittest import mock

        def refuse_to_build(*_args, **_kwargs):
            raise ReachedTheRobot()

        env = {"KC_RUNTIME_MODE": "live", "KC_PLACEMENT_ENVELOPE": words, "KC_PLACEMENT_ENVELOPE_ATTENDED": attended or "", "KC_RUNTIME_CONFIG": "", "KC_EVENTS_PATH": ""}
        absent = ("KC_MISSION_TARGET", "KC_HOST_LEASE", "KC_HOST_SESSION", "KC_PERCEPTION_REMOTE_PORT", "KC_PLACEMENT_SIDE_OFFSET_FORCE_DEG")
        out = io.StringIO()
        with mock.patch.dict(os.environ, env), mock.patch.object(cycle, "make_robot", refuse_to_build), contextlib.redirect_stdout(out):
            for name in absent:
                os.environ.pop(name, None)
            try:
                status = cycle.main(["--do", "cycle", "--remote-ip", "192.0.2.10", "--robot-id", "synthetic-unit", "--pi-ssh", "synthetic-operator@192.0.2.10"])
            except ReachedTheRobot:
                status = "robot"
        return status, out.getvalue()

    def test_a_live_run_naming_an_unqualified_word_of_any_stage_is_refused_before_the_robot_exists(self):
        self._with_word()
        status, stdout = self._live_main("probe_align_word")
        self.assertEqual(3, status)
        self.assertIn("not qualified", stdout)
        self.assertEqual("robot", self._live_main("probe_align_word", attended="qual-2026-09-27-a")[0], "the attended label is the one way past it")

    def test_a_live_run_naming_a_qualified_word_reaches_the_robot(self):
        self._with_word(qualified=True)
        self.assertEqual("robot", self._live_main("probe_align_word")[0])
        self.assertEqual(3, self._live_main("probe_align_word,projected")[0], "projected is not qualified")


class TheSceneScanSeam(unittest.TestCase):
    """--do scan reaches policy_search.scene_scan, which refuses before any motion until the scene-scan stage fills it."""

    def setUp(self):
        import test_run_single_cube_cycle as fixtures

        self.directory = fixtures.use_synthetic_runtime(self)
        cycle.use_runtime_paths(cycle.cli.runtime_paths({}))

    def test_scan_is_refused_before_any_motion_with_the_arm_where_it_stands(self):
        import test_cycle_behavior_preserved as differential

        run = differential.run_scripted_world(cycle, differential.World(), self.directory, do="scan")
        self.assertEqual(("returned", 3), run["ending"], run["stdout"][-2000:])
        self.assertEqual([], run["sent"], "nothing was commanded")
        self.assertEqual([], run["base_pulses"])
        self.assertIn("REFUSED before any motion: SCAN:", run["stdout"])
        self.assertEqual(1, run["disconnects"])

    def test_the_record_names_the_action_and_the_refusal(self):
        import test_cycle_events

        written = test_cycle_events.EventRun(self, do="scan")
        summary = {item["name"]: item["value"] for item in written.of_kind("run.config")[0]["payload"]["summary"]}
        self.assertEqual("scan", summary["action_kind"])
        finished = written.finished()["payload"]
        self.assertEqual("refused", finished["outcome"])
        self.assertEqual([], written.of_kind("action.started"))

    def test_the_seam_is_the_search_stages_and_runs_under_its_identity(self):
        import policy_search

        self.assertIs(policy_search.POLICY, policy_search.scene_scan.stage_policy)
        ctx = harness.HarnessCycle()
        with self.assertRaises(cycle.ScanRefused):
            policy_search.scene_scan(ctx, harness.controller_view(), False)
        self.assertEqual([], ctx.calls)

    def test_a_mission_target_is_never_carried_by_a_scan(self):
        import types

        self.assertEqual("mission_target_needs_the_cycle", cycle.mission_refusal(types.SimpleNamespace(), types.SimpleNamespace(do="scan"))[0])


class TheCrossStageCalls(unittest.TestCase):
    """The calls one stage makes of another (docs/autonomous-demo/overnight-plan-2026-09-26.md, section 2.4), each today's
    decision with no word."""

    def setUp(self):
        self.ctl = harness.controller_view()

    def test_alignment_decision_with_no_word_is_the_controllers_formula(self):
        import policy_align

        for bbox, pitch in (([473, 73, 737, 362], 66.0), ([460, 47, 811, 432], 63.7), ([613, 196, 853, 438], 70.0)):
            with self.subTest(bbox=bbox):
                self.assertEqual(cycle.alignment_from_bbox(bbox, pitch), policy_align.alignment_decision(self.ctl, bbox, pitch, ()))

    def test_a_constant_patched_on_the_controller_is_the_one_the_stage_decides_with(self):
        """A replay's counterfactual patches the controller module (P0c's pins patch CAPTURE_WINDOW_MM and
        APPROACH_REACH_LEAD_MM on it): a stage that kept its own copy of a formula or a constant would not see the patch."""
        from unittest import mock

        import policy_align

        bbox, pitch = [488, 300, 712, 520], 66.0  # lateral 8.1 mm: inside (0, 9), outside (0, 7)
        lateral = cycle.alignment_from_bbox(bbox, pitch)["lateral_mm"]
        self.assertTrue(7.0 < lateral <= 9.0, lateral)
        self.assertTrue(policy_align.alignment_decision(self.ctl, bbox, pitch)["aligned"])
        with mock.patch.object(cycle, "CAPTURE_WINDOW_MM", (0.0, 7.0)):
            self.assertFalse(policy_align.alignment_decision(cycle._CONTROLLER_VIEW, bbox, pitch)["aligned"])

    def test_the_nearest_grasp_target_with_no_word_is_the_proven_radius(self):
        import policy_grasp

        self.assertEqual(0.0, policy_grasp.min_grasp_forward_mm())
        self.assertEqual(0.0, policy_grasp.min_grasp_forward_mm(()))

    def _record(self, contact_px=(553.0, 545.0), centre=(0.0145, -0.1825), pad=True):
        return harness.cube_record([515, 485, 590, 581], contact_px=list(contact_px), cube_center_pad_m=list(centre), pad_frame=harness.pad_frame() if pad else None)

    def test_classify_for_binding_gives_the_mode_a_mission_binding_reads(self):
        import policy_placement

        ctx = harness.HarnessCycle()
        frame = harness.pad_frame()
        for centre in ((0.0145, -0.1825), (0.0145, -0.1475 + 0.012), (0.20, -0.10)):
            with self.subTest(centre=centre):
                entry = self._record(centre=centre)
                expected = cycle.Cycle._mission_seen_cube(entry, frame, -9.58).mode
                direction = cycle.reach_direction_pad(frame["homography_image_to_pad"], entry["cube"]["contact_px"])
                coarse = cycle.coarse_pregrasp_from_low_pose(entry["cube"]["contact_px"], -9.58)
                mode, why = policy_placement.classify_for_binding(ctx, self.ctl, list(centre), direction, max(coarse["cube_out_mm"], 0.0), entry)
                self.assertEqual(expected, mode)
                self.assertEqual(mode is None, why is not None)
        self.assertEqual((None, True), (mode, "sentence" in why))

    def test_classify_block_gives_each_class_with_the_reason_code_search_refuses_with(self):
        import policy_search

        cases = {
            "BASE_ADVANCE": self._record(),
            "ARM_ONLY": self._record(centre=(0.0145, -0.1475 + 0.012)),
            "ALREADY_PLACED": self._record(centre=(0.0, 0.0)),
            "NOT_PLACEABLE": self._record(centre=(0.20, -0.10)),
            "TOO_NEAR": self._record(contact_px=(495.0, 696.0)),
            "OUTSIDE_PAN_BAND": self._record(contact_px=(553.0 + 11.8 * 30.0, 545.0)),
            "NO_PAD_FRAME": self._record(pad=False),
            "SEEN_NOT_MEASURABLE": harness.cube_record(None),
        }
        for expected, record in cases.items():
            with self.subTest(expected):
                classified = policy_search.classify_block(harness.HarnessCycle(), self.ctl, record, -9.58)
                self.assertEqual(expected, classified["class"])
                # and SEARCH itself, on the same frame, refuses with that code or goes on
                ctx = harness.HarnessCycle({"observe": harness.view(harness.LOW_POSE, [record])})
                quiet = contextlib.redirect_stdout(io.StringIO())
                with quiet:
                    try:
                        found = policy_search.run_search(ctx, self.ctl, {"placed": False}, ctx.pose())
                        code = None
                    except stage_policy.CycleFailed as failure:
                        code = harness.failure_code(ctx, failure)
                if expected == "SEEN_NOT_MEASURABLE":
                    self.assertEqual("search_no_cube", code, "with no word a frame with no usable cube is refused, never swept")
                    continue
                self.assertEqual(classified["reason_code"], code, ctx.states())
                if code is None and expected != "ALREADY_PLACED":
                    self.assertIsNone(found.done)


# The interfaces section 2.4 fixes, as the leading parameters their consumers pass: (name, default or EMPTY). A track may add
# a parameter after them with a default; it never renames, reorders or drops one another track's file passes.
EMPTY = inspect.Parameter.empty
INTERFACES = {
    ("policy_align", "alignment_decision"): (("ctl", EMPTY), ("bbox_xyxy", EMPTY), ("pitch_deg", EMPTY), ("words", ())),
    ("policy_grasp", "min_grasp_forward_mm"): (("words", ()),),
    ("policy_placement", "classify_for_binding"): (("ctx", EMPTY), ("ctl", EMPTY), ("anchor", EMPTY), ("direction", EMPTY), ("grasp_out_mm", EMPTY), ("seen", None)),
    ("policy_search", "classify_block"): (("ctx", EMPTY), ("ctl", EMPTY), ("record", EMPTY), ("pan_deg", EMPTY)),
    ("policy_search", "scene_scan"): (("ctx", EMPTY), ("ctl", EMPTY), ("plan_only", EMPTY)),
}
# Every reason code classify_block may return, by class; None only for a class SEARCH goes on from.
CLASS_REASON_CODES = {
    "SEEN_NOT_MEASURABLE": {"search_no_cube"},
    "NO_PAD_FRAME": {"search_cube_not_in_pad_frame", "search_no_pad_frame"},
    "ALREADY_PLACED": {None},
    "OUTSIDE_PAN_BAND": {"search_cube_beyond_return_bound", "search_cube_beyond_pregrasp_entry_bound"},
    "TOO_NEAR": {"search_cube_inside_pregrasp_reach"},
    "NOT_PLACEABLE": {"search_placement_infeasible"},
    "ARM_ONLY": {None},
    "BASE_ADVANCE": {None},
}


class StopAtTheGraspPose(Exception):
    """The proven grasp pose was asked for: APPROACH's target is known, nothing after it is needed."""


class TheCrossStageInterfaces(unittest.TestCase):
    """What another track's stage relies on when it calls one of these (section 2.4): the parameters it passes, the shape it
    reads back and the reason codes it refuses with. Not who calls it today, and not the words of its docstring: a later
    track adds callers and rewrites the prose of its own module."""

    def setUp(self):
        self.ctl = harness.controller_view()

    def test_each_interface_keeps_the_parameters_its_consumers_pass(self):
        for (module_name, function_name), leading in INTERFACES.items():
            with self.subTest(f"{module_name}.{function_name}"):
                parameters = list(inspect.signature(getattr(_stage_module(module_name), function_name)).parameters.values())
                self.assertEqual(leading, tuple((parameter.name, parameter.default) for parameter in parameters[:len(leading)]))
                for parameter in parameters[len(leading):]:
                    self.assertIsNot(EMPTY, parameter.default, f"{parameter.name} is added without a default")

    def test_alignment_decision_returns_the_decision_align_and_grasp_read(self):
        import policy_align

        low, high = cycle.CAPTURE_WINDOW_MM
        for bbox, pitch in (([473, 73, 737, 362], 66.0), ([460, 47, 811, 432], 63.7), ([613, 196, 853, 438], 70.0), ([488, 300, 712, 520], 66.0)):
            with self.subTest(bbox=bbox):
                decision = policy_align.alignment_decision(self.ctl, bbox, pitch)
                self.assertIsInstance(decision, dict)
                self.assertLessEqual({"lateral_mm", "radial_mm", "aligned"}, set(decision))
                self.assertIsInstance(decision["aligned"], bool)
                self.assertTrue(math.isfinite(decision["lateral_mm"]) and math.isfinite(decision["radial_mm"]))
                self.assertEqual(low <= decision["lateral_mm"] <= high, decision["aligned"])

    def test_min_grasp_forward_mm_is_a_float_and_approach_never_plans_nearer(self):
        import policy_grasp

        nearest = policy_grasp.min_grasp_forward_mm()
        self.assertIsInstance(nearest, float)
        asked = []

        def grasp_pose(measured, forward_m, *args):
            asked.append(forward_m * 1000.0)
            raise StopAtTheGraspPose()

        start = harness.view(harness.DESCENDED_POSE)
        for short_mm in (3.0, 0.0):  # a radial estimate 3 mm inside the nearest target, and one exactly on it
            with self.subTest(short_mm=short_mm):
                radial = nearest - short_mm - start["tool"]["out_mm"] - cycle.APPROACH_REACH_LEAD_MM
                ctx = harness.HarnessCycle(pose=harness.DESCENDED_POSE)
                with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(StopAtTheGraspPose):
                    policy_grasp.run_grasp(ctx, harness.controller_view(proven_grasp_pose=grasp_pose), {"object_secured": False}, start, {"radial_mm": radial, "lateral_mm": 3.0, "aligned": True})
                self.assertAlmostEqual(nearest, asked[-1], places=6)

    def test_classify_for_binding_returns_a_mode_or_the_reason_there_is_none(self):
        import policy_placement

        frame = harness.pad_frame()
        for centre in ((0.0145, -0.1825), (0.0145, -0.1475 + 0.012), (0.20, -0.10)):
            with self.subTest(centre=centre):
                record = harness.cube_record([515, 485, 590, 581], contact_px=[553.0, 545.0], cube_center_pad_m=list(centre), pad_frame=frame)
                direction = cycle.reach_direction_pad(frame["homography_image_to_pad"], [553.0, 545.0])
                answer = policy_placement.classify_for_binding(harness.HarnessCycle(), self.ctl, list(centre), direction, 0.0, record)
                self.assertIsInstance(answer, tuple)
                self.assertEqual(2, len(answer))
                mode, why = answer
                if mode is None:
                    self.assertIsInstance(why, dict)
                    self.assertIsInstance(why["sentence"], str)
                else:
                    self.assertIn(mode, ("ARM_ONLY", "BASE_ADVANCE"))
                    self.assertIsNone(why)

    def test_classify_block_returns_a_class_a_reason_code_of_that_class_and_its_values(self):
        import policy_search

        records = (harness.cube_record(None), harness.cube_record([515, 485, 590, 581], contact_px=[553.0, 545.0]),
                   harness.cube_record([515, 485, 590, 581], contact_px=[553.0, 545.0], cube_center_pad_m=[0.0145, -0.1825]))
        records += tuple(harness.cube_record([515, 485, 590, 581], contact_px=list(contact), cube_center_pad_m=list(centre), pad_frame=harness.pad_frame())
                         for contact, centre in (((553.0, 545.0), (0.0145, -0.1825)), ((553.0, 545.0), (0.0145, -0.1355)), ((553.0, 545.0), (0.0, 0.0)),
                                                 ((553.0, 545.0), (0.20, -0.10)), ((495.0, 696.0), (0.0145, -0.1825)), ((553.0 + 11.8 * 30.0, 545.0), (0.0145, -0.1825))))
        seen = set()
        for index, record in enumerate(records):
            with self.subTest(index):
                classified = policy_search.classify_block(harness.HarnessCycle(), self.ctl, record, -9.58)
                self.assertEqual({"class", "reason_code", "values"}, set(classified))
                self.assertIn(classified["class"], CLASS_REASON_CODES)
                self.assertIn(classified["reason_code"], CLASS_REASON_CODES[classified["class"]])
                self.assertIsInstance(classified["values"], dict)
                seen.add(classified["class"])
        self.assertEqual(set(CLASS_REASON_CODES), seen, "every class is reached by one of the records")

    def test_the_pickup_band_refuses_with_its_reason_code_and_the_distance_the_block_must_move(self):
        import policy_align

        seen = harness.cube_record([515, 485, 590, 581], contact_px=[495.0, 696.0])
        ctx = harness.HarnessCycle()
        policy_align.refuse_pickup_band(ctx, self.ctl, {"cube_out_mm": 30.0}, seen)
        self.assertEqual([True], [entry["value"] for entry in ctx.events.predicates("pregrasp_cube_within_reach")])
        ctx = harness.HarnessCycle()
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(stage_policy.CycleFailed) as failure:
            policy_align.refuse_pickup_band(ctx, self.ctl, {"cube_out_mm": -40.0}, seen)
        self.assertEqual("search_cube_inside_pregrasp_reach", harness.failure_code(ctx, failure.exception))
        self.assertEqual([False], [entry["value"] for entry in ctx.events.predicates("pregrasp_cube_within_reach")])
        fields = [args[2] for kind, args, _kwargs in ctx.events.calls if kind == "cycle_failed"][-1]
        self.assertGreater(fields["move_block_at_least_mm"], 0.0)

    def test_a_stage_digest_is_its_module_alone_whatever_the_controller_holds(self):
        view = stage_policy.ControllerView(collections.ChainMap({"CAPTURE_WINDOW_MM": (0.0, 1.0), "APPROACH_REACH_LEAD_MM": 99.0}, vars(cycle)))
        for policy in cycle._STAGE_POLICIES:
            with self.subTest(policy.stage):
                self.assertEqual(policy.identity(cycle._CONTROLLER_VIEW)["rule_digest"], policy.identity(view)["rule_digest"])


class TheControllerBundle(unittest.TestCase):
    def test_the_bundle_of_the_controller_carries_the_seam_and_every_stage_module(self):
        from demo_runtime import bundle

        closure = bundle.import_closure(ROOT / bundle.CONTROLLER_ENTRY, ROOT)
        for name in PRESENT + ("stage_policy",):
            with self.subTest(name):
                self.assertIn(f"autonomy/manipulation/{name}.py", closure)


if __name__ == "__main__":
    unittest.main()
