# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""demo_runtime.signature_check: the release's own signature verifier, run on the robot for every deployed capsule, with
NO MOTION, no slot touched and nothing asked of the resident.

WHAT IT IS FOR. The operator's ruling asks the console to showcase signature module verification. The resident decides
signature policy ONLY when it starts: it admits every capsule its Plan names under `signature_policy require` and the
trusted key list in resident.conf, and there is no live admission operation in it (`AdmitCapsule` is answered
unavailable), so no ApplyPlan candidate can ever exercise that policy and the abort-swap candidate's refusal is a
transactional activation failure, not a signature refusal. What CAN be shown from the browser is the same verifier the
release ships and the deploy's prove-signature runs at a terminal: `demo-plan verify --policy require`, on the robot,
against the deployed store, under the resident's own trusted keys. That is what this module runs, and what it says it is.

WHAT IT RUNS, AND ONLY THAT. One allowlisted invocation shape, exactly prove-signature's (bin/pi/lekiwi-deploy.sh):
`<demo-plan> verify --capsule <store object> --policy require --trusted <keys> --scratch <dir>`, for every capsule digest
the deployed manifest names, twice: under the resident's trusted key list (expected admitted) and under a fresh
throwaway Ed25519 key (expected refused, signature_key_id_not_trusted); and for every unsigned twin the manifest names,
once under the trusted list (expected refused, signature_missing). The verifier is named by an absolute path from the
private configuration (cake.signature), never searched for; the store objects are the manifest's; the trusted keys are
resident.conf's, so the check answers under the RESIDENT'S policy and not a policy of its own. The throwaway key is
generated here (RFC 8032, a real key), written only inside a scratch directory under the runtime's private data, and
removed with that directory after every run, refusal or not. It is never trusted by anything and never signs anything.

WHAT IT NEVER DOES. It commands no motion, opens no admin socket, sends no ApplyPlan, touches no slot and writes nothing
into the release or the store: `--scratch` points inside the scratch directory, which is discarded. It is refused while a
mission is carried, a run is active, a command is unresolved or a capability slot check is in flight (the same claim the
slot check makes, demo_runtime.daemon._check_slot_refusal), and it never queues: a second run while one is in flight is
answered signature_check_in_flight.

THE VERDICT TABLE. One row per (capsule, key): the capsule's content digest, which key it was verified under, what was
expected, what the verifier printed, its reason code, whether it was admitted, and whether that is what was expected. A
verifier that exits non-zero, times out, cannot be started or prints no verdict line is an ERROR row (never a pass), a
capsule the manifest names but the store lacks is an ERROR row and the verifier is not run for it, and with no unsigned
twin staged the twin row is SKIPPED with its own reason, never a pass, and the check is not `complete`. `as_expected` is
true only when every row that RAN read as expected; `complete` only when no row was skipped or an error.

THE RECORD. One JSON line per run in signature-checks.jsonl beside capability-checks.jsonl, through journal.Journal
(fsynced before append returns), written after the last verifier answered. A refusal before the first verifier writes
nothing. The answer is the record's view, and it carries no path: capsule digests, key hex and codes only.

SOFTWARE COVERAGE IS NOT EVIDENCE ABOUT THE RELEASE. The tests drive a fake verifier. The operator's run on the robot,
from the console, is what shows the release verifier's verdicts on the deployed capsules.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from typing import Callable, Optional

from .journal import Journal, read_json_lines, utc_now_text

#: This surface's own version, moved when the shape of its answer changes. It is not the contract version.
SIGNATURE_CHECK_VERSION = "1"
#: The record file's own version, moved when a record's shape changes.
RECORD_VERSION = 1
CHECKS_FILE_NAME = "signature-checks.jsonl"
RECORD_KIND = "signature_check"
#: The runs a state answer carries, newest last. The file keeps every one; the answer says how many it left out.
MAX_CHECKS_SHOWN = 10
#: The policy the resident runs under and the only one this check verifies under. resident.conf has to agree.
POLICY = "require"
#: How long one verifier child may take. The release verifier answers in well under a second; this bounds a stuck one.
VERIFY_TIMEOUT_S = 20.0
#: What close() adds to one verifier bound when it waits for a run in flight: the run checks for closing between
#: children, so at most one child and the Python around it can still be spending when the wait starts.
CLOSE_MARGIN_S = 2.0

#: The two keys a row is verified under, and the two kinds of capsule a row is about.
KEY_DEPLOYMENT, KEY_THROWAWAY = "deployment", "throwaway"
KIND_SIGNED, KIND_TWIN = "signed_capsule", "unsigned_twin"
#: What a row expected or observed.
ADMITTED, REFUSED, ERROR, SKIPPED = "admitted", "refused", "error", "skipped"
#: The verifier's own reason codes this check expects, exactly as the admission crate names them.
REASON_KEY_NOT_TRUSTED = "signature_key_id_not_trusted"
REASON_SIGNATURE_MISSING = "signature_missing"

#: Why a run was refused. Each is a CODE: none names a path, a value or a machine.
UNAVAILABLE = "signature_check_unavailable"
IN_FLIGHT = "signature_check_in_flight"
VERIFIER_UNAVAILABLE = "verifier_binary_unavailable"
MANIFEST_UNREADABLE = "release_manifest_unreadable"
CONF_UNREADABLE = "resident_conf_unreadable"
POLICY_NOT_REQUIRE = "resident_policy_not_require"
NO_TRUSTED_KEY = "resident_trusted_keys_unreadable"
NO_CAPSULE = "manifest_names_no_capsule"
SCRATCH_UNAVAILABLE = "scratch_directory_unavailable"
KEY_COLLISION = "throwaway_key_equals_trusted_key"
#: The `detail` beside UNAVAILABLE for a run refused because the runtime has begun to close.
RUNTIME_CLOSING = "runtime_closing"
#: Why a runtime has no check at all (the `detail` beside UNAVAILABLE), in the order unavailable_reason checks them.
NOT_LIVE = "signature_check_requires_live_mode"
ROBOT_NOT_CONFIGURED = "robot_not_configured"
LINK_NOT_LOCAL = "robot_link_not_local"
NOT_CONFIGURED = "signature_check_not_configured"
#: A row's own reason codes, beside the verifier's.
TWIN_NOT_STAGED = "unsigned_twin_not_staged"
CAPSULE_NOT_IN_STORE = "capsule_not_in_store"
VERIFIER_FAILED = "verifier_failed"
VERIFIER_TIMEOUT = "verifier_timeout"
VERIFIER_UNRUNNABLE = "verifier_unrunnable"
VERDICT_UNREADABLE = "verdict_unreadable"

_HEX64 = re.compile(r"[0-9a-f]{64}")
_CAPSULE_DIGEST_LINE = re.compile(r"^[a-z_]*capsule_digest ([0-9a-f]{64})$")
_TWIN_PATH_LINE = re.compile(r"^[a-z_]*unsigned[a-z_]*path (.*)$")
_STORE_ROOT_LINE = re.compile(r"^store_root (.*)$")
_CONF_LINE = re.compile(r"^\s*([A-Za-z_]+)\s*=\s*(.*?)\s*$")
_VERIFY_LINE = re.compile(r"^(verdict|reason|policy|trusted_keys) (.*)$")
_REASON_CODE = re.compile(r"[a-z][a-z0-9_]*")


# ---- what the tools print, read as they print it --------------------------------------------------------------------------

def parse_verify_output(text: str) -> dict:
    """`demo-plan verify` prints `policy <name>`, `trusted_keys <n>`, `verdict admitted|refused` and, for a refusal,
    `reason <code>`. The FIRST verdict line decides; a reason that is not the shape of a code is dropped rather than
    carried (a free-text reason could name a path). Nothing on stdout is a verdict of nothing."""
    out: dict = {"verdict": None, "reason": None, "policy": None, "trusted_keys": None}
    for raw in text.splitlines():
        match = _VERIFY_LINE.match(raw.strip())
        if match is None:
            continue
        name, value = match.group(1), match.group(2).strip()
        if name == "verdict" and out["verdict"] is None and value in (ADMITTED, REFUSED):
            out["verdict"] = value
        elif name == "reason" and out["reason"] is None and _REASON_CODE.fullmatch(value):
            out["reason"] = value
        elif name == "policy" and out["policy"] is None:
            out["policy"] = value
        elif name == "trusted_keys" and out["trusted_keys"] is None:
            try:
                out["trusted_keys"] = int(value)
            except ValueError:
                pass
    return out


def parse_manifest(text: str) -> dict:
    """The deployed manifest, exactly as prove-signature reads it: every `<prefix>capsule_digest <64 hex>` line once,
    sorted; `store_root`; every `<prefix>unsigned<infix>path` line. Anything else it prints is left alone."""
    digests, twins, store = set(), [], None
    for raw in text.splitlines():
        line = raw.strip()
        match = _CAPSULE_DIGEST_LINE.match(line)
        if match is not None:
            digests.add(match.group(1))
            continue
        match = _TWIN_PATH_LINE.match(line)
        if match is not None and match.group(1).strip():
            twins.append(match.group(1).strip())
            continue
        match = _STORE_ROOT_LINE.match(line)
        if match is not None and store is None:
            store = match.group(1).strip()
    return {"capsule_digests": sorted(digests), "store_root": store, "unsigned_twin_paths": twins}


def parse_resident_conf(text: str) -> dict:
    """resident.conf's `name = value` lines: the signature policy and the trusted key list (comma separated, each a 64
    hex public key; an entry that is not one is dropped, so an unreadable list reads as empty and is refused)."""
    policy, trusted = None, []
    for raw in text.splitlines():
        match = _CONF_LINE.match(raw)
        if match is None:
            continue
        name, value = match.group(1), match.group(2)
        if name == "signature_policy" and policy is None:
            policy = value
        elif name == "trusted_keys" and not trusted:
            trusted = [entry.strip() for entry in value.split(",") if _HEX64.fullmatch(entry.strip())]
    return {"signature_policy": policy, "trusted_keys": trusted}


# ---- the throwaway key: a real Ed25519 key, RFC 8032 section 5.1, derived here so nothing is searched for on PATH ---------

_P = 2 ** 255 - 19
_D = (-121665 * pow(121666, _P - 2, _P)) % _P
_G_X = 15112221349535400772501151409588531511454012693041857206046113283949847762202
_G_Y = 46316835694926478169428394003475163141307993866256225615783033603165251855960
_G = (_G_X, _G_Y, 1, _G_X * _G_Y % _P)


def _point_add(a: tuple, b: tuple) -> tuple:
    x1, y1, z1, t1 = a
    x2, y2, z2, t2 = b
    e = (y1 - x1) * (y2 - x2) % _P
    f = (y1 + x1) * (y2 + x2) % _P
    g = 2 * t1 * t2 * _D % _P
    h = 2 * z1 * z2 % _P
    ee, ff, gg, hh = f - e, h - g, h + g, f + e
    return (ee * ff % _P, gg * hh % _P, ff * gg % _P, ee * hh % _P)


def _point_mul(scalar: int, point: tuple) -> tuple:
    result = (0, 1, 1, 0)
    while scalar > 0:
        if scalar & 1:
            result = _point_add(result, point)
        point = _point_add(point, point)
        scalar >>= 1
    return result


def ed25519_public_key(seed: bytes) -> bytes:
    """The 32-byte public key of a 32-byte seed, by the reference derivation: the clamped first half of SHA-512(seed)
    times the base point, compressed. Checked against RFC 8032's first test vector and against openssl by the tests."""
    if len(seed) != 32:
        raise ValueError("an Ed25519 seed is 32 bytes")
    digest = hashlib.sha512(seed).digest()
    scalar = int.from_bytes(digest[:32], "little")
    scalar &= (1 << 254) - 8
    scalar |= 1 << 254
    x, y, z, _t = _point_mul(scalar, _G)
    inverse = pow(z, _P - 2, _P)
    x, y = x * inverse % _P, y * inverse % _P
    return (y | ((x & 1) << 255)).to_bytes(32, "little")


def write_throwaway_key(path: str) -> str:
    """Write the two-line key file demo-plan reads (`ed25519-seed <hex>`, `ed25519-public <hex>`), mode 0600, from a
    fresh random seed, and return the public half. The file lives only in the scratch directory and goes with it."""
    seed = os.urandom(32)
    public = ed25519_public_key(seed).hex()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="ascii") as handle:
        handle.write(f"ed25519-seed {seed.hex()}\ned25519-public {public}\n")
    return public


# ---- the one invocation shape -------------------------------------------------------------------------------------------

def verify_argv(demo_plan_path: str, capsule_path: str, trusted_keys: list, scratch_dir: str) -> list:
    """The argument list, and the whole of it: prove-signature's shape. The keys are joined as demo-plan's `--trusted`
    takes them (one comma-separated list; it refuses the option twice)."""
    return [demo_plan_path, "verify", "--capsule", capsule_path, "--policy", POLICY, "--trusted", ",".join(trusted_keys),
            "--scratch", scratch_dir]


def default_invoke(argv: list, timeout_s: float) -> dict:
    """Run one verifier child: stdin closed, output captured, bounded in time. Never raises for the child's own failures:
    a timeout or a child that could not be started is answered by its reason code so the row can say which."""
    try:
        finished = subprocess.run(argv, capture_output=True, text=True, timeout=timeout_s, stdin=subprocess.DEVNULL, check=False)
    except subprocess.TimeoutExpired:
        return {"returncode": None, "stdout": "", "reason_code": VERIFIER_TIMEOUT}
    except OSError:
        return {"returncode": None, "stdout": "", "reason_code": VERIFIER_UNRUNNABLE}
    return {"returncode": finished.returncode, "stdout": finished.stdout, "reason_code": None}


def observed_of(result: dict) -> tuple:
    """(observed, reason_code) from one invocation: the verifier's verdict and reason, or an ERROR with why. A non-zero
    exit is an error whatever was printed (the tool's own complaint can name a path, so it is not carried)."""
    if result.get("reason_code") is not None:
        return ERROR, result["reason_code"]
    if result.get("returncode") != 0:
        return ERROR, VERIFIER_FAILED
    parsed = parse_verify_output(result.get("stdout") or "")
    if parsed["verdict"] is None:
        return ERROR, VERDICT_UNREADABLE
    return parsed["verdict"], parsed["reason"]


def row(*, capsule: Optional[str], kind: str, key: str, expected: str, expected_reason: Optional[str], observed: str,
        reason_code: Optional[str]) -> dict:
    """One line of the verdict table. `admitted` is the verdict as a boolean, None for an error or a skipped row;
    `as_expected` compares verdict AND reason with what was expected, None for a skipped row, False for an error."""
    admitted = True if observed == ADMITTED else False if observed == REFUSED else None
    if observed == SKIPPED:
        as_expected = None
    elif observed == ERROR:
        as_expected = False
    else:
        as_expected = observed == expected and (expected != REFUSED or reason_code == expected_reason)
    return {"capsule": capsule, "kind": kind, "key": key, "expected": expected, "expected_reason": expected_reason,
            "observed": observed, "reason_code": reason_code, "admitted": admitted, "as_expected": as_expected}


def summarize(rows: list) -> dict:
    """The counts and the two verdicts of a table: as_expected is true only when every row that RAN read as expected;
    complete only when no row was skipped or an error. A skipped row is never a pass and never counted as one."""
    ran = [item for item in rows if item["observed"] in (ADMITTED, REFUSED)]
    skipped = sum(1 for item in rows if item["observed"] == SKIPPED)
    errors = sum(1 for item in rows if item["observed"] == ERROR)
    good = sum(1 for item in ran if item["as_expected"])
    return {"rows_as_expected": good, "rows_not_as_expected": len(ran) - good, "rows_skipped": skipped, "rows_error": errors,
            "as_expected": errors == 0 and good == len(ran), "complete": skipped == 0 and errors == 0}


def unavailable_reason(*, live: bool, robot, local: bool, paths) -> Optional[str]:
    """None when every condition a check needs holds, else the FIRST missing one, in this order: a LIVE runtime; cake.robot;
    a LOCAL link (the verifier and the store are on the robot, and this runtime has to be there too); cake.signature
    naming the verifier, the manifest and resident.conf. The binary's presence is judged at each run, not here: a release
    that is re-staged under the same paths needs no restart to be checked."""
    if not live:
        return NOT_LIVE
    if robot is None:
        return ROBOT_NOT_CONFIGURED
    if not local:
        return LINK_NOT_LOCAL
    if paths is None:
        return NOT_CONFIGURED
    return None


def fold_checks(records: list) -> list:
    """The file as views, in file order: every record of this kind and version, minus the record envelope."""
    views = []
    for record in records:
        if record.get("rec") != RECORD_KIND or record.get("v") != RECORD_VERSION or not isinstance(record.get("check_uuid"), str):
            continue
        views.append({key: value for key, value in record.items() if key not in ("rec", "v")})
    return views


class SignatureCheck:
    """One release, one verifier, one run at a time, every run written down."""

    def __init__(self, path, *, demo_plan_path: str, manifest_path: str, resident_conf_path: str, scratch_root,
                 runtime_session: str, invoke: Optional[Callable] = None, timeout_s: float = VERIFY_TIMEOUT_S,
                 now_text: Callable[[], str] = utc_now_text, clock_ns: Callable[[], int] = time.monotonic_ns):
        self._path = str(path)
        self._demo_plan, self._manifest, self._conf = demo_plan_path, manifest_path, resident_conf_path
        self._scratch_root = str(scratch_root)
        self._runtime_session = runtime_session
        self._invoke = invoke if invoke is not None else default_invoke
        self._timeout_s = float(timeout_s)
        self._now, self._clock = now_text, clock_ns
        self._journal = Journal(self._path)
        # TRIED, NEVER WAITED ON, except by a runtime that is ending (close).
        self._lock = threading.Lock()
        # Set once by begin_close (and close). A run checks it before it starts and between verifier children.
        self._closing = threading.Event()

    # ---- reads -----------------------------------------------------------------------------------------------------
    def in_flight(self) -> bool:
        return self._lock.locked()

    def checks(self) -> tuple:
        """(the runs on the record, newest last and bounded, how many were left out, damaged lines)."""
        records, damaged = read_json_lines(self._path)
        views = fold_checks(records)
        dropped = max(0, len(views) - MAX_CHECKS_SHOWN)
        return views[dropped:], dropped, damaged

    # ---- the run ---------------------------------------------------------------------------------------------------
    def run(self, *, claim: Callable[[], Optional[str]]) -> dict:
        """Run the verifier over the deployed release and record the table. Every refusal here runs nothing and writes
        nothing, in this order: a run already in flight; a runtime that is closing; `claim()`, the daemon's rule (a
        mission carried, a run active, an unresolved command, a slot check in flight); the verifier binary, the
        manifest, resident.conf (policy require, a readable trusted list, at least one capsule); the scratch directory
        and the throwaway key. Then the rows, then the record, then the answer."""
        if not self._lock.acquire(blocking=False):
            return {"ok": False, "reason_code": IN_FLIGHT}
        try:
            return self._run_holding_lock(claim)
        finally:
            self._lock.release()

    def _run_holding_lock(self, claim) -> dict:
        started = self._clock()
        if self._closing.is_set():
            return {"ok": False, "reason_code": UNAVAILABLE, "detail": RUNTIME_CLOSING}
        refused = claim()
        if refused is not None:
            return {"ok": False, "reason_code": refused}
        if not os.path.isabs(self._demo_plan) or not os.path.isfile(self._demo_plan) or not os.access(self._demo_plan, os.X_OK):
            return {"ok": False, "reason_code": VERIFIER_UNAVAILABLE}
        manifest_text = _read_text(self._manifest)
        if manifest_text is None:
            return {"ok": False, "reason_code": MANIFEST_UNREADABLE}
        conf_text = _read_text(self._conf)
        if conf_text is None:
            return {"ok": False, "reason_code": CONF_UNREADABLE}
        conf = parse_resident_conf(conf_text)
        if conf["signature_policy"] != POLICY:
            # The detail is the policy word the file names (allow, warn, or nothing), which is a code, not a value.
            policy = conf["signature_policy"]
            return {"ok": False, "reason_code": POLICY_NOT_REQUIRE, "detail": policy if isinstance(policy, str) and _REASON_CODE.fullmatch(policy) else None}
        trusted = conf["trusted_keys"]
        if not trusted:
            return {"ok": False, "reason_code": NO_TRUSTED_KEY}
        manifest = parse_manifest(manifest_text)
        if not manifest["capsule_digests"] or not manifest["store_root"]:
            return {"ok": False, "reason_code": NO_CAPSULE}
        try:
            scratch = tempfile.mkdtemp(prefix="signature-check.", dir=self._scratch_root)
        except OSError:
            return {"ok": False, "reason_code": SCRATCH_UNAVAILABLE}
        try:
            throwaway = write_throwaway_key(os.path.join(scratch, "control.keypair"))
            if throwaway in trusted:
                return {"ok": False, "reason_code": KEY_COLLISION}
            rows = self._rows(manifest, trusted, throwaway, scratch)
        finally:
            # REMOVED WHATEVER HAPPENED: the key and every verifier scratch live only here.
            shutil.rmtree(scratch, ignore_errors=True)
        finished = self._clock()
        record = {"rec": RECORD_KIND, "v": RECORD_VERSION, "check_uuid": uuid.uuid4().hex, "runtime_session": self._runtime_session,
                  "t_wall_utc": self._now(), "policy": POLICY, "trusted_keys": list(trusted), "throwaway_public_key": throwaway,
                  "capsules": len(manifest["capsule_digests"]), "unsigned_twins": len(manifest["unsigned_twin_paths"]),
                  "rows": rows, **summarize(rows), "duration_ms": round((finished - started) / 1e6, 3)}
        self._journal.append(record)
        return {"ok": True, **{key: value for key, value in record.items() if key not in ("rec", "v")}}

    def _rows(self, manifest: dict, trusted: list, throwaway: str, scratch: str) -> list:
        rows, n = [], 0
        objects = os.path.join(manifest["store_root"], "objects")
        for digest in manifest["capsule_digests"]:
            capsule_path = os.path.join(objects, digest)
            present = os.path.isfile(capsule_path)
            for key, keys, expected, reason in ((KEY_DEPLOYMENT, trusted, ADMITTED, None),
                                                (KEY_THROWAWAY, [throwaway], REFUSED, REASON_KEY_NOT_TRUSTED)):
                if not present:
                    observed, code = ERROR, CAPSULE_NOT_IN_STORE
                else:
                    n += 1
                    observed, code = self._verify(capsule_path, keys, os.path.join(scratch, f"v{n}"))
                rows.append(row(capsule=digest, kind=KIND_SIGNED, key=key, expected=expected, expected_reason=reason,
                                observed=observed, reason_code=code))
        if not manifest["unsigned_twin_paths"]:
            # SKIPPED, NEVER A PASS: build-mobile stages no twin, and a row that was not run says so.
            rows.append(row(capsule=None, kind=KIND_TWIN, key=KEY_DEPLOYMENT, expected=REFUSED, expected_reason=REASON_SIGNATURE_MISSING,
                            observed=SKIPPED, reason_code=TWIN_NOT_STAGED))
        for twin_path in manifest["unsigned_twin_paths"]:
            digest = _sha256_of(twin_path)
            if digest is None:
                observed, code = ERROR, CAPSULE_NOT_IN_STORE
            else:
                n += 1
                observed, code = self._verify(twin_path, trusted, os.path.join(scratch, f"u{n}"))
            rows.append(row(capsule=digest, kind=KIND_TWIN, key=KEY_DEPLOYMENT, expected=REFUSED, expected_reason=REASON_SIGNATURE_MISSING,
                            observed=observed, reason_code=code))
        return rows

    def _verify(self, capsule_path: str, keys: list, scratch_dir: str) -> tuple:
        """One verifier child, or a SKIPPED row once the runtime has begun to close: nothing new is started then."""
        if self._closing.is_set():
            return SKIPPED, RUNTIME_CLOSING
        return observed_of(self._invoke(verify_argv(self._demo_plan, capsule_path, keys, scratch_dir), self._timeout_s))

    # ---- closing ---------------------------------------------------------------------------------------------------
    def begin_close(self) -> None:
        """From now on no run starts and a run in flight starts no further verifier child."""
        self._closing.set()

    def close(self, *, wait_s: Optional[float] = None) -> None:
        """Close the file after a run in flight has written its record, waiting at most one verifier bound plus a margin
        (begin_close first, so at most one child is still spending). A run that outlives the wait loses its record
        rather than holding the runtime's shutdown open."""
        self.begin_close()
        took = self._lock.acquire(timeout=(self._timeout_s + CLOSE_MARGIN_S) if wait_s is None else wait_s)
        try:
            self._journal.close()
        finally:
            if took:
                self._lock.release()


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except OSError:
        return None


def _sha256_of(path: str) -> Optional[str]:
    """The content digest of a file, which is how the store names a capsule; None when it cannot be read."""
    try:
        with open(path, "rb") as handle:
            return hashlib.sha256(handle.read()).hexdigest()
    except OSError:
        return None
