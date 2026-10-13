# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""The validator fails closed: a schema it cannot evaluate in full raises when it is loaded, never at some later
validate() call and never not at all. Also the JSON semantics Python would get wrong by default (true is not 1, NaN is
not a number, a final $ does not match before a newline) and the shape of the shipped schema files.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE / "python"))

import demo_contracts  # noqa: E402
from demo_contracts import SchemaError, SchemaSet  # noqa: E402

SHIPPED = ["common", "event", "command", "snapshot", "capability", "component", "cake", "source", "history", "manifest", "export"]


def schema_set(**files) -> SchemaSet:
    directory = Path(tempfile.mkdtemp(prefix="demo-contracts-test-"))
    # mkdtemp never removes what it made. A module cleanup rather than a context manager, because the SchemaSet reads
    # its directory lazily: it has to outlive this call and last until the tests of this module are done.
    unittest.addModuleCleanup(shutil.rmtree, directory, ignore_errors=True)
    for stem, schema in files.items():
        (directory / f"{stem}.schema.json").write_text(json.dumps(schema), encoding="utf-8")
    return SchemaSet(directory)


class FailsClosed(unittest.TestCase):
    def test_an_unsupported_keyword_raises_at_load_time(self):
        unsupported = {
            "format": {"type": "string", "format": "date-time"},
            "anyOf": {"anyOf": [{"type": "string"}]},
            "allOf": {"allOf": [{"type": "string"}]},
            "else": {"if": {"type": "string"}, "then": {"minLength": 1}, "else": {"type": "null"}},
            "patternProperties": {"type": "object", "patternProperties": {"^x": {"type": "string"}}},
            "exclusiveMinimum": {"type": "number", "exclusiveMinimum": 0},
            "uniqueItems": {"type": "array", "uniqueItems": True},
            "dependentRequired": {"type": "object", "dependentRequired": {"a": ["b"]}},
            "misspelled": {"type": "object", "aditionalProperties": False},
        }
        for keyword, schema in unsupported.items():
            with self.subTest(keyword=keyword):
                with self.assertRaises(SchemaError) as caught:
                    schema_set(probe=schema).load_schema("probe")
                self.assertIn("unsupported keyword", str(caught.exception))

    def test_an_unsupported_keyword_is_found_at_any_depth(self):
        nested = {"type": "object", "properties": {"a": {"type": "array", "items": {"oneOf": [{"type": "null"}, {"type": "string", "format": "uri"}]}}}}
        with self.assertRaises(SchemaError):
            schema_set(probe=nested).load_schema("probe")
        in_defs = {"type": "object", "$defs": {"never_referenced": {"contains": {"type": "string"}}}}
        with self.assertRaises(SchemaError):
            schema_set(probe=in_defs).load_schema("probe")

    def test_it_raises_before_any_value_is_judged(self):
        schemas = schema_set(probe={"type": "string", "format": "email"})
        with self.assertRaises(SchemaError):
            schemas.validate("probe", "anything")
        with self.assertRaises(SchemaError):  # and it keeps raising: a failed load is not cached as loaded
            schemas.validate("probe", "anything")

    def test_an_unsupported_keyword_in_a_referenced_file_raises(self):
        schemas = schema_set(outer={"type": "object", "properties": {"inner": {"$ref": "inner.schema.json"}}}, inner={"type": "string", "format": "uuid"})
        with self.assertRaises(SchemaError):
            schemas.load_schema("outer")

    def test_unsupported_values_of_supported_keywords_raise(self):
        for label, schema in {
            "additionalProperties true": {"type": "object", "additionalProperties": True},
            "additionalProperties schema": {"type": "object", "additionalProperties": {"type": "string"}},
            "unknown type name": {"type": "float"},
            "boolean subschema": {"type": "object", "properties": {"a": True}},
            "pattern that does not compile": {"type": "string", "pattern": "("},
            "empty enum": {"enum": []},
            "if without then": {"if": {"type": "string"}},
            "closed object requiring an undeclared property": {"type": "object", "additionalProperties": False, "required": ["kind"], "properties": {"knd": {"type": "string"}}},
            "two branches pinning the same discriminator value": {"oneOf": [{"properties": {"k": {"const": "a"}}}, {"properties": {"k": {"const": "a"}}}]},
        }.items():
            with self.subTest(case=label):
                with self.assertRaises(SchemaError):
                    schema_set(probe=schema).load_schema("probe")

    def test_a_ref_that_does_not_resolve_or_leaves_the_directory_raises(self):
        for label, ref in {"missing definition": "#/$defs/nope", "missing file": "nope.schema.json", "url": "https://example.invalid/x.schema.json",
                           "parent directory": "../common.schema.json", "sibling keywords": None}.items():
            schema = {"$ref": "#/$defs/a", "description": "siblings"} if ref is None else {"$ref": ref}
            with self.subTest(case=label):
                with self.assertRaises(SchemaError):
                    schema_set(probe={"type": "object", "properties": {"a": schema}, "$defs": {"a": {"type": "string"}}}).load_schema("probe")

    def test_unknown_names_raise_and_a_definitions_container_never_answers_valid(self):
        with self.assertRaises(SchemaError):
            demo_contracts.validate("no_such_schema", {})
        with self.assertRaises(SchemaError):
            demo_contracts.validate("../event", {})
        errors = demo_contracts.validate("common", {"anything": "at all"})
        self.assertEqual(len(errors), 1)
        self.assertIn("asserts nothing", errors[0])


class JsonSemantics(unittest.TestCase):
    def test_a_boolean_is_never_a_number(self):
        schemas = schema_set(probe={"type": "object", "properties": {"n": {"type": "integer", "minimum": 0}, "flag": {"const": True}, "zero": {"const": 0}, "pick": {"enum": [0, 1]}}})
        self.assertEqual(schemas.validate("probe", {"n": 1, "flag": True, "zero": 0.0, "pick": 1}), [])
        self.assertEqual(schemas.validate("probe", {"n": 2.0}), [], "2.0 is an integer in JSON Schema")
        for bad in ({"n": True}, {"flag": 1}, {"zero": False}, {"pick": True}, {"n": 1.5}):
            with self.subTest(value=bad):
                self.assertEqual(len(schemas.validate("probe", bad)), 1)

    def test_non_finite_numbers_are_rejected(self):
        schemas = schema_set(probe={"type": ["number", "null"]})
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=repr(bad)):
                self.assertIn("not a finite JSON number", schemas.validate("probe", bad)[0])

    def test_a_trailing_newline_does_not_pass_an_anchored_pattern(self):
        self.assertEqual(demo_contracts.validate("common#/$defs/id", "synthetic-run-0001"), [])
        self.assertEqual(len(demo_contracts.validate("common#/$defs/id", "synthetic-run-0001\n")), 1)

    def test_a_pattern_applies_to_strings_only_and_not_needs_its_own_type(self):
        named = {"name": "n", "unit": None}
        for value in (1, 2.5, True, None, "plain text 1.2.3"):
            with self.subTest(value=value):
                self.assertEqual(demo_contracts.validate("common#/$defs/named_value", {**named, "value": value}), [])
        parts = ["192", "0", "2", "7"]  # a documentation address, assembled here so that no address literal sits in this file
        for value in (".".join(parts), "/etc/synthetic", "a:b", "x@y"):
            with self.subTest(value=value):
                self.assertTrue(demo_contracts.validate("common#/$defs/named_value", {**named, "value": value}))

    def test_a_nullable_ref_reports_the_errors_of_the_non_null_shape(self):
        stamp = {"session": "synthetic-host-session-0001", "seq": -1, "sample_mono_ns": None, "clock_domain": "pi_monotonic"}
        errors = demo_contracts.validate("snapshot#/properties/link", {"state": "connected", "source_stamp": stamp, "receive_age_ms": None})
        self.assertEqual(errors, ["$.source_stamp.seq: -1 is below the minimum 0"])
        self.assertEqual(demo_contracts.validate("snapshot#/properties/link", {"state": "connected", "source_stamp": None, "receive_age_ms": None}), [])

    def test_safe_text_refuses_a_home_path_and_an_address(self):
        home = "/" + "home" + "/" + "somebody"  # assembled: the repository gates forbid the literal in a tracked file
        for text in (f"reading {home}/config", "/" + "Users" + "/somebody/x", ".".join(["198", "51", "100", "4"]) + " refused the connection"):
            with self.subTest(text=text[:12]):
                self.assertIn("must not be", demo_contracts.validate("common#/$defs/safe_text", text)[0])
        self.assertEqual(demo_contracts.validate("common#/$defs/safe_text", "version 0.1.3 of the controller, 29.5 mm clearance"), [])

    def test_model_identity_names_a_model_and_never_where_it_was_loaded_from(self):
        # provenance rides on every event, and the naive value for a model's identity is the path or the URL it was
        # loaded from. The character set has to admit a registry id (org/name@revision, name@sha256:...), so it also
        # admits / : and @; the tripwire is what keeps a location out. Forbidden strings are assembled, as above.
        provenance = {"observation_ids": [], "calibration_digest": None, "config_digest": None, "module_generation": None, "reconstructed": False}
        for identity in (None, "synthetic-model-0001", "synthetic-org/synthetic-detector@rev-0001", "synthetic-org/synthetic-policy@sha256:" + "0" * 12, "detector-v0.1.3"):
            with self.subTest(accepted=identity):
                self.assertEqual(demo_contracts.validate("common#/$defs/provenance", {**provenance, "model_identity": identity}), [])
        home = "home" + "/" + "somebody"
        address = ".".join(["192", "0", "2", "10"])
        for identity in ("file:" + "//" + "/" + home + "/models/policy", "http:" + "//" + address + ":8000/v1/policy", "https:" + "//" + "example.invalid/policy",
                         "user@" + address + ":models/policy", "cache/" + home + "/policy", "C:/" + "Users" + "/somebody/policy"):
            with self.subTest(refused=identity[:8]):
                errors = demo_contracts.validate("common#/$defs/provenance", {**provenance, "model_identity": identity})
                self.assertEqual(len(errors), 1, errors)
                self.assertIn("$.model_identity: must not be a URL, a home-directory path or a dotted-quad address", errors[0])
        self.assertTrue(demo_contracts.validate("common#/$defs/provenance", {**provenance, "model_identity": "/" + "opt/models/policy"}), "an absolute path never starts with a letter or a digit")


class ShippedSchemas(unittest.TestCase):
    def test_every_schema_file_is_draft_2020_12_and_loads(self):
        on_disk = sorted(path.name[: -len(".schema.json")] for path in demo_contracts.SCHEMA_DIR.glob("*.schema.json"))
        self.assertEqual(on_disk, sorted(SHIPPED))
        for stem in SHIPPED:
            with self.subTest(schema=stem):
                self.assertEqual(demo_contracts.load_schema(stem)["$schema"], "https://json-schema.org/draft/2020-12/schema")
        for alias in demo_contracts.ALIASES:
            with self.subTest(alias=alias):
                self.assertEqual(demo_contracts.load_schema(alias)["type"], "object")

    def test_contract_version_matches_the_schemas_and_the_package(self):
        self.assertEqual(demo_contracts.CONTRACT_VERSION, "1")
        self.assertEqual(demo_contracts.SCHEMA_DIR.name, "v1")
        self.assertEqual(demo_contracts.load_schema("common#/$defs/contract_version")["const"], demo_contracts.CONTRACT_VERSION)
        package = json.loads((PACKAGE / "package.json").read_text(encoding="utf-8"))
        self.assertEqual(package["version"].split(".")[0], demo_contracts.CONTRACT_VERSION, "the package major version is the contract version")

    def test_every_object_is_closed_and_every_discriminated_kind_has_one_branch(self):
        # A closed object is what makes "never endpoints, paths or credentials" checkable: a field nobody declared cannot
        # ride along. The only open objects are the two placeholders that a oneOf branch replaces, and the branches themselves.
        def walk(node, where, found):
            if isinstance(node, dict):
                if node.get("type") == "object" and "properties" in node and node.get("additionalProperties") is not False:
                    found.append(where)
                for key, value in node.items():
                    walk(value, f"{where}/{key}", found)
            elif isinstance(node, list):
                for index, value in enumerate(node):
                    walk(value, f"{where}/{index}", found)

        for stem in SHIPPED:
            found: list[str] = []
            walk(demo_contracts.load_schema(stem), stem, found)
            self.assertEqual(found, [], "an object schema with properties that is not closed")
        for stem, key, names in (("event", "kind", demo_contracts.load_schema("event#/$defs/kind")["enum"]), ("command", "type", demo_contracts.load_schema("common#/$defs/command_type")["enum"])):
            branches = [branch["properties"][key]["const"] for branch in demo_contracts.load_schema(stem)["oneOf"]]
            self.assertEqual(sorted(branches), sorted(names), f"{stem}: one branch per {key}, no more and no fewer")

    def test_no_ref_has_siblings(self):
        def walk(node):
            if isinstance(node, dict):
                if "$ref" in node:
                    self.assertEqual(list(node), ["$ref"])
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)

        for stem in SHIPPED:
            walk(demo_contracts.load_schema(stem))


if __name__ == "__main__":
    unittest.main()
