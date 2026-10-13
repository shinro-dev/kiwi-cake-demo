#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# autonomy/tests/test_lease_metadata.py: the host command lease on the commands the single-cube controller SENDS.
# With the two variables of run_single_cube_cycle.py set, every action of a scripted cycle and every action of a real base
# pulse carries kc.lease and kc.session, and bin/pi/host_boundary.py's own Lease admits each one; with the keys removed the
# way that host removes them, the payloads are byte for byte the payloads of the same run without a lease. With the
# variables unset nothing is attached. With half a lease, or a pair the host could never have issued, the run refuses
# before anything moves. No robot, no camera, no network, no lerobot: the lerobot client is a scripted fake and the host
# boundary is loaded from its file and driven in this process.
# Run directly: python3 autonomy/tests/test_lease_metadata.py
import ast
import copy
import importlib.util
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "autonomy" / "tests"))
sys.path.insert(0, str(ROOT / "autonomy" / "motion"))
sys.path.insert(0, str(ROOT / "bin" / "laptop" / "manipulation"))

import run_single_cube_cycle as current  # noqa: E402
import test_cycle_behavior_preserved as differential  # noqa: E402  (the scripted World, the synthetic lerobot, run_scripted_world)
import test_run_single_cube_cycle as fixtures  # noqa: E402  (the synthetic runtime configuration)

# A generation and a session no real host issued: the session is the distinctive string this file searches for in stdout
# and in the event record, to show that neither reaches a reader of either.
GENERATION, SESSION = 7, 987654321098765
LEASE_ENVIRONMENT = {"KC_HOST_LEASE": str(GENERATION), "KC_HOST_SESSION": str(SESSION)}


def host_boundary():
    """bin/pi/host_boundary.py loaded from its file, as packages/demo-runtime/tests/test_lease_client.py loads it. It is
    the robot computer's module and is not importable as a package; it imports nothing but the standard library."""
    spec = importlib.util.spec_from_file_location("kc_host_boundary", ROOT / "bin" / "pi" / "host_boundary.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def runtime_lease_variable_names():
    """(LEASE_ENV_GENERATION, LEASE_ENV_SESSION) as packages/demo-runtime/demo_runtime/supervisor.py assigns them, read
    from its source with ast: those two names are the ONLY place the runtime and this controller meet, and the runtime
    puts nothing in a child's environment under any other pair. Parsed, not imported: that module belongs to another
    change and may be mid-edit, and what has to agree is a literal, not a running import."""
    tree = ast.parse((ROOT / "packages" / "demo-runtime" / "demo_runtime" / "supervisor.py").read_text())
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1 or not isinstance(node.targets[0], ast.Tuple):
            continue
        names = [element.id for element in node.targets[0].elts if isinstance(element, ast.Name)]
        if names == ["LEASE_ENV_GENERATION", "LEASE_ENV_SESSION"] and isinstance(node.value, ast.Tuple):
            return tuple(element.value for element in node.value.elts if isinstance(element, ast.Constant))
    return None


def held_lease(test, generation=GENERATION, session=SESSION):
    """A real host Lease, held at `generation` by the real acquire path (a generation is reached by acquiring that many
    times: the host never sets one). Returned with the module, so a test can use the host's own constants as well."""
    boundary = host_boundary()
    lease = boundary.Lease(host_session=session)
    for _ in range(generation):
        if lease.state() == boundary.HELD:
            lease.release(str(session), lease.generation)
        granted = lease.acquire("single-cube-cycle", ttl_s=boundary.MAX_TTL_S)
        test.assertTrue(granted["ok"], granted)
    test.assertEqual(generation, lease.generation)
    return boundary, lease


class Recorder:
    """A client with the one method the lease helper wraps. It keeps the object it was handed, not a copy, so a test can
    ask whether the dict that reached it is the caller's."""

    def __init__(self):
        self.sent = []
        self.is_connected = False

    def connect(self):
        self.is_connected = True

    def disconnect(self):
        self.is_connected = False

    def send_action(self, action):
        self.sent.append(action)
        return action

    def get_observation(self):
        return {"x.vel": 0.0, "y.vel": 0.0, "theta.vel": 0.0}


class LeaseCase(unittest.TestCase):
    def setUp(self):
        self.directory = fixtures.use_synthetic_runtime(self)
        current.use_runtime_paths(current.cli.runtime_paths({}))  # these runs start from the LEGACY layout, as the differential ones do

    def scripted_run(self, **options):
        world = differential.World()
        return world, differential.run_scripted_world(current, world, self.directory, **options)


class TheKeysArePinnedToTheHost(LeaseCase):
    def test_the_controller_sends_the_two_keys_the_host_module_itself_names(self):
        boundary = host_boundary()
        metadata = current.host_lease_metadata(LEASE_ENVIRONMENT)
        self.assertEqual({boundary.LEASE_KEY, boundary.SESSION_KEY}, set(metadata), "the controller's metadata is not the pair host_boundary.py names")
        self.assertEqual({float(GENERATION), float(SESSION)}, set(metadata.values()))
        self.assertEqual(float(GENERATION), metadata[boundary.LEASE_KEY])
        self.assertEqual(float(SESSION), metadata[boundary.SESSION_KEY])
        for value in metadata.values():  # the host coerces every action value to float and then demands a whole number
            self.assertIsInstance(value, float)
            self.assertIsNotNone(boundary.whole_number(value))

    def test_the_key_names_are_read_from_the_host_file_and_not_copied_into_this_test(self):
        """If the two names are ever changed on the robot computer, the assertion above has to fail rather than keep
        agreeing with a copy that lives here. So the names themselves come out of that file, twice over."""
        boundary = host_boundary()
        source = (ROOT / "bin" / "pi" / "host_boundary.py").read_text()
        self.assertIn(f'LEASE_KEY, SESSION_KEY = "{boundary.LEASE_KEY}", "{boundary.SESSION_KEY}"', source)
        self.assertEqual(("kc.lease", "kc.session"), (boundary.LEASE_KEY, boundary.SESSION_KEY), "the host renamed its keys: every assertion in this file follows it, this one states it")

    def test_a_lease_the_host_could_never_have_issued_is_refused_by_the_metadata_itself(self):
        for name, environment in (("no generation", {"KC_HOST_SESSION": str(SESSION)}),
                                  ("no session", {"KC_HOST_LEASE": "1"}),
                                  ("a generation of zero", {**LEASE_ENVIRONMENT, "KC_HOST_LEASE": "0"}),
                                  ("a fractional generation", {**LEASE_ENVIRONMENT, "KC_HOST_LEASE": "1.5"}),
                                  ("a word", {**LEASE_ENVIRONMENT, "KC_HOST_LEASE": "latest"}),
                                  ("a negative session", {**LEASE_ENVIRONMENT, "KC_HOST_SESSION": "-1"}),
                                  ("a session of zero", {**LEASE_ENVIRONMENT, "KC_HOST_SESSION": "0"}),
                                  ("a session no float holds exactly", {**LEASE_ENVIRONMENT, "KC_HOST_SESSION": str(2 ** 53 + 1)})):
            with self.subTest(lease=name):
                with self.assertRaises(current.ScanRefused) as refused:
                    current.host_lease_metadata(environment)
                self.assertIn(current.LEASE_GENERATION_ENV, str(refused.exception))
                self.assertIn(current.LEASE_SESSION_ENV, str(refused.exception))

    def test_an_environment_that_names_no_lease_produces_none(self):
        for environment in ({}, {"KC_HOST_LEASE": "", "KC_HOST_SESSION": ""}, {"KC_RUNTIME_CONFIG": "x"}):
            self.assertIsNone(current.host_lease_metadata(environment))

    def test_the_two_variables_are_the_pair_the_runtime_exports(self):
        """The other half of the pinning: the KEYS come from the host module, the VARIABLE NAMES come from the runtime
        that sets them. A controller reading any other pair gets nothing from that runtime and every command is dropped,
        which on hardware looks exactly like a robot that is not listening."""
        exported = runtime_lease_variable_names()
        self.assertIsNotNone(exported, "demo_runtime.supervisor no longer assigns LEASE_ENV_GENERATION, LEASE_ENV_SESSION as a literal pair")
        self.assertEqual(exported, (current.LEASE_GENERATION_ENV, current.LEASE_SESSION_ENV), "the runtime exports one pair of variables and this controller reads another")
        self.assertIn(current.LEASE_GENERATION_ENV, current.__doc__, "the module docstring no longer states the pair it reads")
        self.assertIn(current.LEASE_SESSION_ENV, current.__doc__)


class EveryCommandOfAScriptedCycleCarriesIt(LeaseCase):
    def test_the_host_admits_every_command_and_what_it_dispatches_is_the_command_without_a_lease_byte_for_byte(self):
        boundary, lease = held_lease(self)
        _, plain = self.scripted_run()
        _, leased = self.scripted_run(extra_env=LEASE_ENVIRONMENT)
        self.assertGreater(len(plain["sent"]), 1000, "the scenario did not run a full cycle: there is nothing to compare")
        self.assertEqual(len(plain["sent"]), len(leased["sent"]), "the lease changed HOW MANY commands the run sent")
        for index, (bare, carried) in enumerate(zip(plain["sent"], leased["sent"])):
            self.assertEqual({boundary.LEASE_KEY: float(GENERATION), boundary.SESSION_KEY: float(SESSION)},
                             {key: value for key, value in carried.items() if key.startswith(boundary.META_PREFIX)}, f"command {index} does not carry the lease")
            self.assertIsNone(lease.admit(carried), f"the host would reject command {index}")
            dispatched = {key: value for key, value in carried.items() if not key.startswith(boundary.META_PREFIX)}  # what CommandGate hands the stock loop
            self.assertEqual(json.dumps(bare), json.dumps(dispatched), f"command {index} is not the command this run would have sent without a lease")
        self.assertEqual(plain["ending"], leased["ending"])
        self.assertEqual(plain["transitions"], leased["transitions"])
        self.assertEqual(plain["base_pulses"], leased["base_pulses"])

    def test_without_the_lease_no_command_carries_a_kc_key(self):
        boundary = host_boundary()
        _, plain = self.scripted_run()
        self.assertTrue(plain["sent"])
        for index, action in enumerate(plain["sent"]):
            self.assertEqual([], [key for key in action if key.startswith(boundary.META_PREFIX)], f"command {index} carries a lease nobody asked for")
        self.assertNotIn("HOST LEASE", plain["stdout"])

    def test_a_command_of_an_earlier_generation_is_the_one_the_host_rejects(self):
        """Not vacuous: the admission above has to depend on the two numbers, not on the mere presence of the keys."""
        boundary, lease = held_lease(self)
        _, leased = self.scripted_run(extra_env={**LEASE_ENVIRONMENT, "KC_HOST_LEASE": str(GENERATION - 1)})
        self.assertTrue(leased["sent"])
        self.assertEqual(boundary.WRONG_GENERATION, lease.admit(leased["sent"][0]))
        _, wrong_host = self.scripted_run(extra_env={**LEASE_ENVIRONMENT, "KC_HOST_SESSION": str(SESSION - 1)})
        self.assertEqual(boundary.WRONG_SESSION, lease.admit(wrong_host["sent"][0]))

    def test_neither_number_reaches_stdout_or_the_event_record(self):
        events_path = os.path.join(self.directory, "lease-events.jsonl")
        if os.path.exists(events_path):
            os.unlink(events_path)
        _, leased = self.scripted_run(configured=True, events_path=events_path, extra_env=LEASE_ENVIRONMENT)
        self.assertTrue(leased["sent"])
        self.assertIn("HOST LEASE", leased["stdout"], "a run under a lease says so, without saying which lease")
        self.assertEqual("", leased["stderr"])
        written = Path(events_path).read_text()
        self.assertGreater(len(written), 0, "events were on and nothing was written: this test proved nothing")
        for where, text in (("stdout", leased["stdout"]), ("the event record", written)):
            self.assertNotIn(str(SESSION), text, f"the host session reached {where}")
            self.assertNotIn(current.LEASE_SESSION_ENV, text, f"the name of the session variable reached {where}")


class EveryBasePulseCarriesIt(LeaseCase):
    """The base path is the other half: run_hybrid_pick_place builds a transport per pulse and is handed neither a client
    factory nor a lease, so both travel the scope of autonomy/motion/lerobot_transport.py. The primitive below is the REAL
    one (MobileApproachController -> StableMotionGateway -> LeKiwiTransport); only the lerobot client is a fake."""

    def pulses(self, **options):
        world = differential.World()
        with mock.patch.dict(sys.modules, differential.synthetic_lerobot(world)):
            move_base = current.make_move_base("192.0.2.10", "synthetic-unit", **options)
            self.assertTrue(move_base(current.BASE_VX_MPS, current.BASE_PULSE_S[0]), "the forward pulse did not confirm its stop")
            self.assertTrue(move_base(-current.BASE_VX_MPS, current.BASE_PULSE_S[0]), "the reverse pulse did not confirm its stop")
        return world.sent

    def test_every_action_of_a_forward_and_a_reverse_pulse_carries_the_lease_and_the_host_admits_it(self):
        boundary, lease = held_lease(self)
        metadata = current.host_lease_metadata(LEASE_ENVIRONMENT)
        sent = self.pulses(lease_metadata=metadata)
        self.assertGreater(len(sent), 4, "the pulses sent almost nothing: there is nothing to check")
        for index, action in enumerate(sent):
            self.assertEqual({boundary.LEASE_KEY: float(GENERATION), boundary.SESSION_KEY: float(SESSION)},
                             {key: value for key, value in action.items() if key.startswith(boundary.META_PREFIX)}, f"base action {index} does not carry the lease")
            self.assertIsNone(lease.admit(action), f"the host would reject base action {index}")
            self.assertEqual(["x.vel", "y.vel", "theta.vel"], [key for key in action if not key.startswith(boundary.META_PREFIX)],
                             f"base action {index} is no longer the three keys the stock host indexes")

    def test_without_a_lease_a_pulse_sends_exactly_what_it_always_sent(self):
        boundary = host_boundary()
        bare = self.pulses()
        leased = self.pulses(lease_metadata=current.host_lease_metadata(LEASE_ENVIRONMENT))
        self.assertTrue(bare)
        for action in bare:
            self.assertEqual([], [key for key in action if key.startswith(boundary.META_PREFIX)])
        for index, action in enumerate(leased[:len(bare)]):
            stripped = {key: value for key, value in action.items() if not key.startswith(boundary.META_PREFIX)}
            self.assertEqual(json.dumps(bare[index]), json.dumps(stripped), f"base action {index} is not what the same pulse sends without a lease")

    def test_the_scope_is_restored_whatever_happened_inside_it(self):
        import lerobot_transport

        self.assertIsNone(lerobot_transport._scoped_lease_metadata)
        self.assertIsNone(lerobot_transport._scoped_client_factory)
        self.pulses(lease_metadata=current.host_lease_metadata(LEASE_ENVIRONMENT))
        self.assertIsNone(lerobot_transport._scoped_lease_metadata, "a pulse left its lease on the module")
        self.assertIsNone(lerobot_transport._scoped_client_factory)
        with self.assertRaises(ZeroDivisionError):
            with lerobot_transport.client_factory_scope(object(), {"kc.lease": 1.0}):
                raise ZeroDivisionError("inside the block")
        self.assertIsNone(lerobot_transport._scoped_lease_metadata)
        self.assertIsNone(lerobot_transport._scoped_client_factory)


class AMalformedLeaseRefusesBeforeMotion(LeaseCase):
    BROKEN = {"half a lease, the generation alone": {"KC_HOST_LEASE": str(GENERATION)},
              "half a lease, the session alone": {"KC_HOST_SESSION": str(SESSION)},
              "a generation that is not a number": {**LEASE_ENVIRONMENT, "KC_HOST_LEASE": "latest"},
              "a generation of zero": {**LEASE_ENVIRONMENT, "KC_HOST_LEASE": "0"},
              "a session that is not a number": {**LEASE_ENVIRONMENT, "KC_HOST_SESSION": "the-host"},
              "a session no float holds exactly": {**LEASE_ENVIRONMENT, "KC_HOST_SESSION": str(2 ** 53 + 1)}}

    def test_nothing_is_commanded_and_the_run_ends_with_the_refusal_status(self):
        for name, environment in self.BROKEN.items():
            with self.subTest(lease=name):
                world, run = self.scripted_run(extra_env=environment)
                self.assertEqual(("returned", 3), run["ending"], run["stdout"][-2000:])
                self.assertEqual([], world.sent, "a run with a lease the host would reject commanded something anyway")
                self.assertEqual([], world.base_pulses)
                self.assertEqual(0, world.disconnects, "the robot was constructed and connected before the lease was looked at")
                self.assertIn("REFUSED before any motion", run["stdout"])
                self.assertIn(current.LEASE_GENERATION_ENV, run["stdout"])
                self.assertIn(current.LEASE_SESSION_ENV, run["stdout"])
                self.assertNotIn(str(SESSION), run["stdout"], "the refusal repeated the host session back")

    def test_the_refusal_is_stated_with_its_own_reason_code_and_carries_no_value(self):
        events_path = os.path.join(self.directory, "refusal-events.jsonl")
        if os.path.exists(events_path):
            os.unlink(events_path)
        world, run = self.scripted_run(configured=True, events_path=events_path,
                                       extra_env={**LEASE_ENVIRONMENT, "KC_HOST_LEASE": "latest"})
        self.assertEqual(("returned", 3), run["ending"], run["stdout"][-2000:])
        self.assertEqual([], world.sent)
        lines = [json.loads(line) for line in Path(events_path).read_text().splitlines() if line.strip()]
        refused = [line for line in lines if line["kind"] == "action.refused"]
        self.assertEqual(["host_lease_malformed"], [line["reason_code"] for line in refused])
        self.assertTrue(refused[0]["payload"]["before_motion"])
        self.assertNotIn(str(SESSION), Path(events_path).read_text())


class NoCallerOfEitherPathLosesItsDict(LeaseCase):
    def test_the_arm_path_sends_a_copy_and_leaves_the_action_and_the_metadata_alone(self):
        metadata = current.host_lease_metadata(LEASE_ENVIRONMENT)
        metadata_before, recorder = copy.deepcopy(metadata), Recorder()
        current.attach_lease(recorder, metadata)
        action = {"arm_shoulder_pan.pos": -9.58, "arm_gripper.pos": 79.9, "x.vel": 0.0, "y.vel": 0.0, "theta.vel": 0.0}
        action_before = copy.deepcopy(action)
        recorder.send_action(action)
        recorder.send_action(action)
        self.assertEqual(action_before, action, "the caller's action dict was modified")
        self.assertEqual(metadata_before, metadata, "the metadata dict was modified")
        for sent in recorder.sent:
            self.assertIsNot(action, sent, "the action that left is the caller's own dict")
            self.assertEqual({**action_before, **metadata_before}, sent)
            self.assertEqual(list(action_before) + list(metadata_before), list(sent), "the lease keys are not appended last: the payload was rebuilt")
        self.assertIsNot(recorder.sent[0], recorder.sent[1], "two sends shared one dict")

    def test_the_base_path_sends_a_fresh_dict_per_action_and_leaves_the_metadata_alone(self):
        from lerobot_transport import LeKiwiTransport

        metadata = current.host_lease_metadata(LEASE_ENVIRONMENT)
        metadata_before, recorder = copy.deepcopy(metadata), Recorder()
        transport = LeKiwiTransport(remote_ip="192.0.2.10", robot_id="synthetic-unit", client_factory=lambda address, identity: recorder, lease_metadata=metadata)
        transport.connect()
        transport.send_velocity(0.05, 0.0, 0.0)
        transport.send_zero()
        self.assertEqual(metadata_before, metadata, "the metadata dict was modified")
        self.assertEqual(2, len(recorder.sent))
        self.assertIsNot(recorder.sent[0], recorder.sent[1], "two actions shared one dict")
        for sent in recorder.sent:
            self.assertEqual(metadata_before, {key: value for key, value in sent.items() if key.startswith("kc.")})
        bare = Recorder()
        LeKiwiTransport(remote_ip="192.0.2.10", robot_id="synthetic-unit", client_factory=lambda address, identity: bare).send_zero()
        self.assertEqual([{"x.vel": 0.0, "y.vel": 0.0, "theta.vel": 0.0}], bare.sent, "a transport that was given no lease attached one anyway")


if __name__ == "__main__":
    unittest.main()
