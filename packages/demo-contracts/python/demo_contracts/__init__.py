# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""demo_contracts: the operator console contract v1, and a validator for exactly the JSON Schema subset it uses.

The schema files under schema/v1/ are the source of truth; this module only reads them. It has no dependency beyond the
standard library on purpose: the runtime that emits these records, the API that serves them and the tests that check
them all import it, and none of them may pull a hardware or a network dependency in through a contracts package.

The validator FAILS CLOSED. A schema is checked in full when it is loaded, together with every file it references, and
a keyword outside the supported subset raises SchemaError there and then. A validator that skipped a keyword it did not
know would report "valid" for a rule it never evaluated, which is the one failure a contract check must not have.

Supported keywords (assertions): type (a name or a list of names, null included), required, properties,
additionalProperties (only the value false), enum, const, pattern, minimum, maximum, minLength, maxLength, minItems,
maxItems, items, $ref (to local $defs and to sibling files of the same directory), oneOf, if + then, not.
Annotations, accepted and ignored: $schema, $id, $comment, title, description. $defs holds definitions.

Two uses of oneOf are given better messages than "matches none":
  - a DISCRIMINATED oneOf, where every branch pins the same property with a distinct const (kind for an event, type for
    a command): the branch is selected by that property and only its errors are reported;
  - a NULLABLE oneOf, [{"type": "null"}, X]: the errors of X are reported for a value that is not null.

API: CONTRACT_VERSION, load_schema(name), validate(name, obj) -> list of error strings (empty means valid).
name is a schema file's stem (common, event, command, snapshot, capability, component, cake, source, history, manifest,
export), one of ALIASES, or a stem plus a JSON pointer such as "common#/$defs/source_stamp". A file that only holds
definitions (common, source, history, export) is refused at its root rather than answered "valid".
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

CONTRACT_VERSION = "1"
SCHEMA_DIR = Path(__file__).resolve().parents[2] / "schema" / f"v{CONTRACT_VERSION}"
# The Cake resident status surface, versioned SEPARATELY from the console contract and kept in its own directory so
# that the two cannot drift into each other: schema/v1 does not reference it, it does not reference schema/v1, and
# raising its version leaves CONTRACT_VERSION alone. It is self-contained for the same reason, because a $ref here
# resolves only inside the file or to a sibling of the same directory.
CAKE_STATUS_VERSION = "1"
CAKE_STATUS_SCHEMA_DIR = Path(__file__).resolve().parents[2] / "schema" / "cake_status" / f"v{CAKE_STATUS_VERSION}"
# The ROBOT's resident status surface, versioned separately from both of the above for the same reasons: its own
# directory, its own SchemaSet, no reference into or out of schema/v1 or cake_status.
ROBOT_STATUS_VERSION = "2"
ROBOT_STATUS_SCHEMA_DIR = Path(__file__).resolve().parents[2] / "schema" / "robot_status" / f"v{ROBOT_STATUS_VERSION}"

# Definitions that are records in their own right, under the names the rest of the system uses for them.
ALIASES = {
    "command_result": "command#/$defs/command_result",
    "cake_ladder": "cake#/$defs/ladder",
    "cake_slot": "cake#/$defs/slot",
    "cake_transaction": "cake#/$defs/transaction",
    "cake_update_preview": "cake#/$defs/update_preview",
    "cake_continuity": "cake#/$defs/continuity",
    "tap_slot": "source#/$defs/tap_slot",
    "preview_slot": "source#/$defs/preview_slot",
    "history_entry": "history#/$defs/history_entry",
    "run_export_index": "export#/$defs/run_export_index",
}

ANNOTATIONS = frozenset({"$schema", "$id", "$comment", "title", "description"})
ASSERTIONS = frozenset({"type", "required", "properties", "additionalProperties", "enum", "const", "pattern", "minimum", "maximum", "minLength", "maxLength",
                        "minItems", "maxItems", "items", "$ref", "oneOf", "if", "then", "not"})
TYPE_NAMES = ("null", "boolean", "integer", "number", "string", "array", "object")
_STEM = re.compile(r"[a-z][a-z0-9_]*")
_REF_FILE = re.compile(r"[a-z][a-z0-9_]*\.schema\.json")
_HINT_MAX = 96  # a description longer than this explains the field, it does not name the rule; it stays out of error messages


class SchemaError(Exception):
    """A schema is outside the supported subset, malformed, or references something that does not resolve."""


def _is_type(value, name: str) -> bool:
    if name == "null":
        return value is None
    if name == "boolean":
        return isinstance(value, bool)
    if isinstance(value, bool):  # bool is an int in Python and is never a JSON number
        return False
    if name == "integer":
        return isinstance(value, int) or (isinstance(value, float) and math.isfinite(value) and value.is_integer())
    if name == "number":
        return isinstance(value, (int, float))
    if name == "string":
        return isinstance(value, str)
    if name == "array":
        return isinstance(value, list)
    if name == "object":
        return isinstance(value, dict)
    return False


def _type_name(value) -> str:
    for name in ("null", "boolean", "integer", "number", "string", "array", "object"):
        if _is_type(value, name):
            return name
    return f"a non-JSON value ({type(value).__name__})"


def _equal(a, b) -> bool:
    """JSON equality for scalars: true is not 1, and 0 equals 0.0."""
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a == b
    return type(a) is type(b) and a == b


def _show(value) -> str:
    try:
        text = json.dumps(value, ensure_ascii=True)
    except (TypeError, ValueError):
        text = repr(value)
    return text if len(text) <= 80 else text[:77] + "..."


def _hint(schema: dict) -> str:
    text = schema.get("description")
    return f" ({text})" if isinstance(text, str) and len(text) <= _HINT_MAX else ""


def _is_scalar(value) -> bool:
    return value is None or isinstance(value, (bool, int, float, str))


def _compile(pattern: str, where: str):
    # JSON Schema patterns are ECMAScript: a final $ matches only at the very end. Python's $ also matches before a
    # trailing newline, which would let "abc\n" through an identifier pattern, so a final unescaped $ becomes \Z.
    source = pattern[:-1] + r"\Z" if pattern.endswith("$") and not pattern.endswith("\\$") else pattern
    try:
        return re.compile(source)
    except re.error as error:
        raise SchemaError(f"{where}: pattern {pattern!r} does not compile: {error}") from None


class SchemaSet:
    """The schema files of one directory, loaded lazily, each checked in full before first use."""

    def __init__(self, directory: Path | str = SCHEMA_DIR):
        self.directory = Path(directory)
        self._files: dict[str, dict] = {}
        self._patterns: dict[str, re.Pattern] = {}

    # ------------------------------------------------------------------ loading
    def _file(self, stem: str) -> dict:
        if stem in self._files:
            return self._files[stem]
        if not _STEM.fullmatch(stem):
            raise SchemaError(f"schema name {stem!r} is not a plain file stem")
        path = self.directory / f"{stem}.schema.json"
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise SchemaError(f"no schema file {path.name} in the contract directory") from None
        except json.JSONDecodeError as error:
            raise SchemaError(f"{path.name} is not valid JSON: {error}") from None
        if not isinstance(document, dict):
            raise SchemaError(f"{path.name}: a schema must be a JSON object (boolean schemas are not supported)")
        self._files[stem] = document  # registered BEFORE the check so that files that reference each other terminate
        try:
            self._check(document, f"{path.name}#", stem)
        except SchemaError:
            del self._files[stem]
            raise
        return document

    def _resolve(self, ref: str, stem: str) -> tuple[dict, str]:
        """A $ref -> (schema, stem of the file it lives in). Only a sibling file of this directory and a JSON pointer."""
        file_part, _, pointer = ref.partition("#")
        if file_part:
            if not _REF_FILE.fullmatch(file_part):
                raise SchemaError(f"$ref {ref!r}: only a sibling file named <stem>.schema.json is supported, never a URL or a path")
            stem = file_part[: -len(".schema.json")]
        node = self._file(stem)
        for token in [t for t in pointer.split("/") if t] if pointer else []:
            token = token.replace("~1", "/").replace("~0", "~")
            if not isinstance(node, dict) or token not in node:
                raise SchemaError(f"$ref {ref!r} does not resolve: no {token!r}")
            node = node[token]
        if not isinstance(node, dict):
            raise SchemaError(f"$ref {ref!r} does not point at a schema object")
        return node, stem

    def _check(self, schema, where: str, stem: str) -> None:
        if not isinstance(schema, dict):
            raise SchemaError(f"{where}: a schema must be a JSON object (boolean schemas are not supported)")
        for key in schema:
            if key not in ASSERTIONS and key not in ANNOTATIONS and key != "$defs":
                raise SchemaError(f"{where}: unsupported keyword {key!r}; this validator refuses a schema it cannot evaluate in full")
        if "$ref" in schema:
            # A $ref stands alone. Siblings are legal in draft 2020-12 but the TypeScript generator then copies the
            # target instead of naming it, and the two outputs of one schema would drift apart.
            if len(schema) != 1 or not isinstance(schema["$ref"], str):
                raise SchemaError(f"{where}: $ref must be a string and must stand alone in its schema object")
            self._resolve(schema["$ref"], stem)
            return
        if "type" in schema:
            names = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
            if not names or any(name not in TYPE_NAMES for name in names):
                raise SchemaError(f"{where}: type {schema['type']!r} is not a JSON Schema type name or a list of them")
        for key in ("properties", "$defs"):
            if key in schema:
                if not isinstance(schema[key], dict):
                    raise SchemaError(f"{where}: {key} must be an object")
                for name, sub in schema[key].items():
                    self._check(sub, f"{where}/{key}/{name}", stem)
        if "additionalProperties" in schema and schema["additionalProperties"] is not False:
            raise SchemaError(f"{where}: additionalProperties is supported only with the value false")
        if "required" in schema:
            required = schema["required"]
            if not isinstance(required, list) or any(not isinstance(name, str) for name in required) or len(set(required)) != len(required):
                raise SchemaError(f"{where}: required must be a list of distinct property names")
            if schema.get("additionalProperties") is False:
                missing = [name for name in required if name not in schema.get("properties", {})]
                if missing:  # a closed object that requires a property it does not declare can never validate: a typo
                    raise SchemaError(f"{where}: requires {missing} but is closed and does not declare them")
        if "enum" in schema and (not isinstance(schema["enum"], list) or not schema["enum"] or not all(_is_scalar(v) for v in schema["enum"])):
            raise SchemaError(f"{where}: enum must be a non-empty list of scalars")
        if "const" in schema and not _is_scalar(schema["const"]):
            raise SchemaError(f"{where}: const is supported only for scalars")
        if "pattern" in schema:
            if not isinstance(schema["pattern"], str):
                raise SchemaError(f"{where}: pattern must be a string")
            self._patterns[schema["pattern"]] = _compile(schema["pattern"], where)
        for key in ("minimum", "maximum"):
            if key in schema and (isinstance(schema[key], bool) or not isinstance(schema[key], (int, float)) or not math.isfinite(schema[key])):
                raise SchemaError(f"{where}: {key} must be a finite number")
        for key in ("minLength", "maxLength", "minItems", "maxItems"):
            if key in schema and (isinstance(schema[key], bool) or not isinstance(schema[key], int) or schema[key] < 0):
                raise SchemaError(f"{where}: {key} must be a non-negative integer")
        for key in ("items", "if", "then", "not"):
            if key in schema:
                self._check(schema[key], f"{where}/{key}", stem)
        if ("if" in schema) != ("then" in schema):
            raise SchemaError(f"{where}: if and then are supported only together (else is not supported)")
        if "oneOf" in schema:
            if not isinstance(schema["oneOf"], list) or not schema["oneOf"]:
                raise SchemaError(f"{where}: oneOf must be a non-empty list")
            for index, sub in enumerate(schema["oneOf"]):
                self._check(sub, f"{where}/oneOf/{index}", stem)
            key = self._discriminator(schema["oneOf"])
            if key is not None:
                values = [branch["properties"][key]["const"] for branch in schema["oneOf"]]
                if any(_equal(a, b) for i, a in enumerate(values) for b in values[i + 1:]):
                    raise SchemaError(f"{where}: two oneOf branches pin {key!r} to the same value, so no value could select exactly one")

    @staticmethod
    def _discriminator(branches: list) -> str | None:
        """The one property every branch pins with a const, or None when the oneOf is not discriminated."""
        if len(branches) < 2:
            return None
        common = None
        for branch in branches:
            pinned = {name for name, sub in branch.get("properties", {}).items() if isinstance(sub, dict) and "const" in sub}
            common = pinned if common is None else common & pinned
        return next(iter(common)) if common is not None and len(common) == 1 else None

    def load_schema(self, name: str) -> dict:
        """The checked schema object for a name. Treat it as read-only."""
        target = ALIASES.get(name, name)
        stem, _, pointer = target.partition("#")
        self._file(stem)
        return self._resolve(f"{stem}.schema.json#{pointer}", stem)[0]

    # ------------------------------------------------------------------ validation
    def validate(self, name: str, obj) -> list[str]:
        target = ALIASES.get(name, name)
        stem = target.partition("#")[0]
        schema = self.load_schema(name)
        if not set(schema) - ANNOTATIONS - {"$defs"}:
            # Anything would "validate" against a definitions container. Refuse instead of answering valid.
            return [f"schema {name!r} asserts nothing (it only holds definitions); name one, for example {stem}#/$defs/<name>"]
        errors: list[str] = []
        self._validate(schema, obj, "$", stem, errors)
        return errors

    def _validate(self, schema: dict, value, path: str, stem: str, out: list[str]) -> None:
        if "$ref" in schema:
            target, target_stem = self._resolve(schema["$ref"], stem)
            self._validate(target, value, path, target_stem, out)
            return
        if isinstance(value, float) and not math.isfinite(value):
            out.append(f"{path}: not a finite JSON number")
            return
        if "type" in schema:
            names = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
            if not any(_is_type(value, name) for name in names):
                out.append(f"{path}: expected {' or '.join(names)}, got {_type_name(value)}{_hint(schema)}")
                return  # every further message about a value of the wrong type would be noise
        if "const" in schema and not _equal(value, schema["const"]):
            out.append(f"{path}: expected {_show(schema['const'])}, got {_show(value)}{_hint(schema)}")
        if "enum" in schema and not any(_equal(value, option) for option in schema["enum"]):
            out.append(f"{path}: {_show(value)} is not one of {_show(schema['enum'])}")
        if _is_type(value, "number"):
            if "minimum" in schema and value < schema["minimum"]:
                out.append(f"{path}: {_show(value)} is below the minimum {schema['minimum']}{_hint(schema)}")
            if "maximum" in schema and value > schema["maximum"]:
                out.append(f"{path}: {_show(value)} is above the maximum {schema['maximum']}{_hint(schema)}")
        elif isinstance(value, str):
            if "minLength" in schema and len(value) < schema["minLength"]:
                out.append(f"{path}: shorter than {schema['minLength']} characters")
            if "maxLength" in schema and len(value) > schema["maxLength"]:
                out.append(f"{path}: longer than {schema['maxLength']} characters")
            if "pattern" in schema and not self._patterns[schema["pattern"]].search(value):
                out.append(f"{path}: {_show(value)} does not match {schema['pattern']}")
        elif isinstance(value, list):
            if "minItems" in schema and len(value) < schema["minItems"]:
                out.append(f"{path}: fewer than {schema['minItems']} item(s)")
            if "maxItems" in schema and len(value) > schema["maxItems"]:
                out.append(f"{path}: more than {schema['maxItems']} item(s)")
            if "items" in schema:
                for index, item in enumerate(value):
                    self._validate(schema["items"], item, f"{path}[{index}]", stem, out)
        elif isinstance(value, dict):
            properties = schema.get("properties", {})
            for name in schema.get("required", []):
                if name not in value:
                    out.append(f"{path}: missing required property {name!r}")
            if schema.get("additionalProperties") is False:
                for name in value:
                    if name not in properties:
                        out.append(f"{path}: unexpected property {name!r}")
            for name, sub in properties.items():
                if name in value:
                    self._validate(sub, value[name], f"{path}.{name}", stem, out)
        if "oneOf" in schema:
            self._one_of(schema, value, path, stem, out)
        if "if" in schema:
            probe: list[str] = []
            self._validate(schema["if"], value, path, stem, probe)
            if not probe:
                self._validate(schema["then"], value, path, stem, out)
        if "not" in schema:
            probe = []
            self._validate(schema["not"], value, path, stem, probe)
            if not probe:
                excluded = schema["not"].get("description", "a value of the excluded shape")
                out.append(f"{path}: must not be {excluded}")

    def _one_of(self, schema: dict, value, path: str, stem: str, out: list[str]) -> None:
        branches = schema["oneOf"]
        key = self._discriminator(branches)
        if key is not None:
            if not isinstance(value, dict) or key not in value:
                out.append(f"{path}: cannot select a variant without the discriminator {key!r}")
                return
            chosen = [branch for branch in branches if _equal(branch["properties"][key]["const"], value[key])]
            if not chosen:
                out.append(f"{path}: no variant for {key}={_show(value[key])}")
                return
            self._validate(chosen[0], value, path, stem, out)  # consts are distinct (checked at load): at most one branch
            return
        results = []
        for branch in branches:
            probe: list[str] = []
            self._validate(branch, value, path, stem, probe)
            results.append(probe)
        passing = [index for index, probe in enumerate(results) if not probe]
        if len(passing) == 1:
            return
        if len(passing) > 1:
            out.append(f"{path}: matches alternatives {passing} at once, exactly one is required")
            return
        if len(branches) == 2 and value is not None and {"type": "null"} in branches:
            out.extend(results[1 - branches.index({"type": "null"})])  # nullable: the errors of the non-null shape are the useful ones
            return
        detail = "; ".join(f"[{index}] {probe[0]}" for index, probe in enumerate(results))
        out.append(f"{path}: matches none of the {len(branches)} alternatives{_hint(schema)}: {detail}")


_DEFAULT = SchemaSet()
# A SEPARATELY VERSIONED SURFACE, not part of contract v1. Nothing in schema/v1 references it and nothing in it
# references schema/v1: it is read with its own SchemaSet over its own directory, so its version moves on its own and
# a change to it is not a change to the console contract. It lives here rather than in the runtime because a schema is
# a schema, and the API, the runtime and the tests all read this package rather than each other.
_CAKE_STATUS = SchemaSet(CAKE_STATUS_SCHEMA_DIR)
_ROBOT_STATUS = SchemaSet(ROBOT_STATUS_SCHEMA_DIR)


def load_schema(name: str) -> dict:
    """The checked v1 schema object for a name (see the module docstring). Raises SchemaError; never returns an unchecked schema."""
    return _DEFAULT.load_schema(name)


def validate(name: str, obj) -> list[str]:
    """Error strings for obj against the named v1 schema; an empty list means valid. Raises SchemaError for a bad schema or name."""
    return _DEFAULT.validate(name, obj)


def load_cake_status_schema() -> dict:
    """The checked cake_status schema object. Checked in full when it loads, like every other schema here."""
    return _CAKE_STATUS.load_schema("cake_status")


def validate_cake_status(obj) -> list[str]:
    """Error strings for obj against the cake_status surface; an empty list means valid.

    Kept out of validate() on purpose: that function answers for contract v1 and a caller must not be able to reach a
    differently versioned surface by passing a name to it.
    """
    return _CAKE_STATUS.validate("cake_status", obj)


def load_robot_status_schema() -> dict:
    """The checked robot_status schema object, checked in full when it loads."""
    return _ROBOT_STATUS.load_schema("robot_status")


def validate_robot_status(obj) -> list[str]:
    """Error strings for obj against the robot_status surface; an empty list means valid. Kept out of validate() for
    the reason validate_cake_status is: a differently versioned surface is not reachable by a name passed to it."""
    return _ROBOT_STATUS.validate("robot_status", obj)


__all__ = ["ALIASES", "CAKE_STATUS_SCHEMA_DIR", "CAKE_STATUS_VERSION", "CONTRACT_VERSION", "ROBOT_STATUS_SCHEMA_DIR", "ROBOT_STATUS_VERSION",
           "SCHEMA_DIR", "SchemaError", "SchemaSet", "load_cake_status_schema", "load_robot_status_schema", "load_schema", "validate",
           "validate_cake_status", "validate_robot_status"]
