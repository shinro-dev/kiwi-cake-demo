#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# packages/demo-runtime/tests/test_config_candidate_roles.py: cake.candidate_roles in the runtime configuration. It is
# optional; when present it is validated against the configured candidate keys with cake_activation.roles_refusal and
# reaches the prepared candidates (mission_service.prepared_candidates), which is what the check and the mission view
# serve. A configuration without it keeps the digest it had.
# Run directly: python3 packages/demo-runtime/tests/test_config_candidate_roles.py
from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from demo_runtime import mission_service  # noqa: E402
from demo_runtime.config import ConfigError, RuntimeConfig, config_digest  # noqa: E402
from test_mission_service import BASE_CONFIG, CAPABILITY  # noqa: E402

ACTIVATE, UNDO = "mobile-demo-swapped", "mobile-demo-inert"


class TheRolesKey(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.paths = {}
        for key in (ACTIVATE, UNDO):
            path = os.path.join(self.tmp.name, f"{key}.cakepln")
            with open(path, "wb") as handle:
                handle.write(key.encode())
            self.paths[key] = path

    def config(self, roles=None, *, with_roles=True) -> RuntimeConfig:
        cake = {"admin_probe_path": "/opt/cake/admin-probe", "admin_socket_path": "/run/kc/admin.sock",
                "activation_candidates": dict(self.paths), "capability": dict(CAPABILITY)}
        if with_roles:
            cake["candidate_roles"] = roles
        return RuntimeConfig.from_mapping({**BASE_CONFIG, "cake": cake})

    def test_roles_reach_the_prepared_candidates(self):
        config = self.config({ACTIVATE: "activate", UNDO: {"role": "undo", "label": "Back to the start"}})
        candidates = mission_service.prepared_candidates(config)
        self.assertEqual(candidates.role(ACTIVATE), "activate")
        self.assertEqual(candidates.role(UNDO), "undo")
        self.assertEqual(candidates.primary(), ACTIVATE)
        view = {entry["key"]: entry for entry in candidates.view()}
        self.assertEqual(view[UNDO]["label"], "Back to the start")

    def test_absent_roles_leave_the_view_as_it_was(self):
        candidates = mission_service.prepared_candidates(self.config(with_roles=False))
        self.assertIsNone(candidates.primary())
        self.assertIsNone(candidates.role(ACTIVATE))

    def test_a_malformed_table_is_refused_by_the_loader_with_the_reason(self):
        for roles, reason in (([], "candidate_roles_not_a_table"),
                              ({"not-prepared": "activate"}, "candidate_role_key_not_prepared"),
                              ({ACTIVATE: "launch"}, "candidate_role_unknown"),
                              ({ACTIVATE: "activate", UNDO: "activate"}, "candidate_role_repeated")):
            with self.subTest(reason=reason):
                with self.assertRaises(ConfigError) as caught:
                    self.config(roles)
                self.assertIn(reason, str(caught.exception))

    def test_the_digest_without_roles_is_the_digest_it_had_and_roles_change_it(self):
        without = self.config(with_roles=False)
        self.assertEqual(config_digest(without), config_digest(self.config(None)))
        self.assertNotIn("candidate_roles", without.to_private_dict()["cake"])
        self.assertNotEqual(config_digest(without), config_digest(self.config({ACTIVATE: "activate"})))


if __name__ == "__main__":
    unittest.main()
