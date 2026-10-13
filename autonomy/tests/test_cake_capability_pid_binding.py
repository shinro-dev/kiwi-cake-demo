#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# autonomy/tests/test_cake_capability_pid_binding.py: cake_capability_adapter.bind_command_to_launched_process, the
# binding of a capability command to the process Cake launched (get-health arg1), and parse_health. The health answer
# below has the row shape a resident printed (health arg0=.. arg1=.. code=.. flags=.. sequence=.. slot=.. state=..
# state_name=.. surface=..); the uuids, digests and pids are made up.
# Run directly: python3 autonomy/tests/test_cake_capability_pid_binding.py
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "motion"))

from cake_capability_adapter import (  # noqa: E402
    CakeCapabilityAdapter,
    PID_MAX_LIMIT,
    SlotState,
    bind_command_to_launched_process,
    parse_health,
    parse_slots,
    pid_prefix,
)

CAP_SLOT = "5050543040508050000000000000cafe"
HOLDER_SLOT = "5050543040508050000000000000beef"
SUPERVISOR_SLOT = "50505430405080500000737570657276"
MOBILE = "b" * 64
INERT = "a" * 64
LISTING = f"""status ok
slot capsule={"c" * 64} config={"d" * 64} generation=1 instance=1 name=supervisor.lekiwi state=ACTIVE uuid={SUPERVISOR_SLOT}
slot capsule={MOBILE} config={"e" * 64} generation=2 instance=2 name=mobile_approach state=ACTIVE uuid={CAP_SLOT}
slot capsule={MOBILE} config={"f" * 64} generation=1 instance=3 name=mobile_approach.holder state=ACTIVE uuid={HOLDER_SLOT}
"""
HEALTH = f"""status ok
health_read_epoch 3
health_rows 3
health arg0=6 arg1=4242 code=0 flags=1 sequence=2 slot={SUPERVISOR_SLOT} state=1 state_name=HEALTHY surface=reported
health arg0=6 arg1=31337 code=0 flags=1 sequence=4 slot={CAP_SLOT} state=1 state_name=HEALTHY surface=reported
health arg0=10458335022666574450 arg1=18321403732347129034 code=0 flags=0 sequence=8589934594 slot={HOLDER_SLOT} state=1 state_name=HEALTHY surface=reported
"""


def bind(command_id, *, slot=None, health=None, capsule=MOBILE):
    slots = parse_slots(LISTING)
    return bind_command_to_launched_process(command_id, capability="mobile_approach",
                                            slot=slots[CAP_SLOT] if slot is None else slot,
                                            active_capsule=capsule, health=parse_health(HEALTH) if health is None else health)


class ParseHealth(unittest.TestCase):
    def test_rows_by_slot_with_numbers_as_integers(self):
        rows = parse_health(HEALTH)
        self.assertEqual(set(rows), {SUPERVISOR_SLOT, CAP_SLOT, HOLDER_SLOT})
        self.assertEqual((rows[CAP_SLOT].arg1, rows[CAP_SLOT].surface, rows[CAP_SLOT].state_name), (31337, "reported", "HEALTHY"))
        self.assertEqual(rows[HOLDER_SLOT].arg1, 18321403732347129034)

    def test_a_row_without_a_slot_or_with_text_numbers_is_not_invented(self):
        rows = parse_health("status ok\nhealth arg1=12 surface=reported\nhealth arg1=x slot=ab surface=none\n")
        self.assertEqual(list(rows), ["ab"])
        self.assertIsNone(rows["ab"].arg1)


class PidBinding(unittest.TestCase):
    def test_a_command_from_the_launched_process_is_accepted(self):
        binding = bind("mobile_approach.pid31337.cmd_1")
        self.assertTrue(binding.accepted, binding)
        self.assertEqual((binding.reason, binding.pid, binding.expected_prefix),
                         ("bound_to_launched_process", 31337, "mobile_approach.pid31337."))
        self.assertEqual(pid_prefix("mobile_approach", 31337), binding.expected_prefix)

    def test_another_pid_is_refused_and_a_longer_pid_with_the_same_digits_is_not_a_match(self):
        for command_id in ("mobile_approach.pid4242.cmd_1", "mobile_approach.pid313370.cmd_1", "mobile_approach.pid3133.cmd_1"):
            binding = bind(command_id)
            self.assertFalse(binding.accepted, command_id)
            self.assertEqual(binding.reason, "command_not_from_launched_process")

    def test_the_generation_label_of_the_older_method_is_not_a_pid_binding(self):
        self.assertFalse(bind(f"mobile_approach.{MOBILE[:16]}.gen2.cmd_1").accepted)
        self.assertFalse(bind("unidentified.cmd_1").accepted)
        self.assertEqual(bind(None).reason, "command_id_not_text")

    def test_the_slot_decides_first(self):
        slot = parse_slots(LISTING)[CAP_SLOT]
        inert = SlotState(slot.uuid, slot.name, INERT, slot.generation, slot.instance, slot.state)
        stopped = SlotState(slot.uuid, slot.name, slot.capsule, slot.generation, slot.instance, "INERT")
        good = "mobile_approach.pid31337.cmd_1"
        self.assertEqual(bind(good, slot=inert).reason, "slot_not_on_capability_capsule")
        self.assertEqual(bind(good, slot=stopped).reason, "slot_not_active")
        self.assertEqual(bind_command_to_launched_process(good, capability="mobile_approach", slot=None,
                                                          active_capsule=MOBILE, health=parse_health(HEALTH)).reason, "slot_absent")

    def test_the_holder_carrying_the_same_capsule_does_not_lend_its_row(self):
        holder = parse_slots(LISTING)[HOLDER_SLOT]
        binding = bind("mobile_approach.pid18321403732347129034.cmd_1", slot=holder)
        self.assertFalse(binding.accepted)
        self.assertEqual(binding.reason, "health_arg1_not_a_pid")

    def test_health_that_names_no_pid_refuses(self):
        good = "mobile_approach.pid31337.cmd_1"
        self.assertEqual(bind(good, health={}).reason, "health_row_absent")
        self.assertEqual(bind(good, health=parse_health(HEALTH)[SUPERVISOR_SLOT]).reason, "health_row_absent")
        unread = parse_health(f"health arg1=31337 slot={CAP_SLOT} surface=unreadable status=5\n")
        self.assertEqual(bind(good, health=unread).reason, "health_not_reported")
        zero = parse_health(f"health arg1=0 slot={CAP_SLOT} surface=reported state_name=HEALTHY\n")
        self.assertEqual(bind(good, health=zero).reason, "health_pid_absent")
        one = parse_health(f"health arg1=1 slot={CAP_SLOT} surface=reported\n")
        self.assertEqual(bind("mobile_approach.pid1.cmd_1", health=one).reason, "health_arg1_not_a_pid")
        huge = parse_health(f"health arg1={PID_MAX_LIMIT + 1} slot={CAP_SLOT} surface=reported\n")
        self.assertEqual(bind(f"mobile_approach.pid{PID_MAX_LIMIT + 1}.cmd_1", health=huge).reason, "health_arg1_not_a_pid")

    def test_the_generation_based_methods_are_unchanged(self):
        adapter = CakeCapabilityAdapter(capability="mobile_approach", slot_uuid=CAP_SLOT, active_capsule=MOBILE, probe=lambda: LISTING)
        adapter.observe()
        self.assertEqual(adapter.running_identity(), f"mobile_approach.{MOBILE[:16]}.gen2")
        self.assertTrue(adapter.accepts_command_from(f"mobile_approach.{MOBILE[:16]}.gen2.cmd_1"))
        self.assertFalse(adapter.accepts_command_from("mobile_approach.pid31337.cmd_1"))


if __name__ == "__main__":
    unittest.main()
