# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""remote_perception: the robot's side of the ONE offboard exception, and the protocol both sides speak.

WHY THIS EXISTS. The unchanged per-frame perception (OWLv2, the cube recognizer, three pad outline fits, the print
template) takes 62.8 s a frame on the robot, throttled, against about 12 s on the desktop GPU (handoff section 12.3).
The operator approved exactly one exception: the complete frame-perception operation may run on the desktop, with the
existing GPU environment, the existing model, preprocessing and thresholds, and the existing geometry. Cake, the host,
the mission and its controller, target selection, the physical predicates and all motion authority stay on the robot.
The desktop returns PERCEPTION RESULTS ONLY: a perception record, its overlay picture and the cube proposals, for the
exact frame the robot sent. This module is how the robot asks, and how it decides whether an answer may be used at all.

WHAT AN ANSWER MUST BE BEFORE ANYTHING ON THE ROBOT READS IT (accept(), in this order; the first failure is the reason):
  1. it arrived before the robot's OWN monotonic deadline for this request (late: never used, whatever it says);
  2. it is well formed: this protocol's version, the op that was asked, the parts that op carries;
  3. its request_id is the outstanding request's (a new uuid4 per request, never reused);
  4. it echoes the request's robot identity (resident session, run, mission, target, scan, controller pid) and its
     capture identity (observation, camera service session and sequence, file sha256, decoded-frame digest) exactly,
     compared as canonical JSON bytes;
  5. the service says ok (a refusal of the service is a refusal here, with the service's own code kept);
  6. the model revision and the digest of the model's files are the configured pin; the seven frozen sources and
     perceive() are the robot's own (which are the benchmark's: FROZEN_* below); the pad template is the robot's;
  7. the frame the service decoded is the frame the robot sent: the sha256 of the bytes it received and the digest of
     the pixels it decoded both equal the robot's own recomputation;
  8. the record and overlay bytes are the ones the header names (sha256), and the record parses as a JSON object.
Any failure raises RemotePerceptionRefused with a reason code. The controller turns that into a CycleFailed (exit 5, the
arm holds), nothing is written as a perception result and nothing is appended to the results. There is no retry and no
fall back to local perception: a local model on the robot would be the 62.8 s path the exception exists to avoid, and a
silent switch between the two would hide which one produced a decision.

A STOP IS NOT BLOCKED BY AN OUTSTANDING REQUEST. The client waits in poll slices of at most POLL_SLICE_S and asks its
cancel predicate between slices. The controller's predicate is its stop flag, EXCEPT during the read-only re-observation
that measures where a base pulse stopped (the same exemption as the local mission gate): that one runs to its answer or
to its deadline, because the report must state where the base is. On cancel the socket is closed at once with LINGER 0,
so a reply that comes later has nowhere to arrive; RemotePerceptionCancelled is raised and nothing is written.

CLOCKS ARE NOT MIXED. The deadline, the round trip and every other robot number are the robot's monotonic clock. The
service's queue and perceive times are the desktop's monotonic clock and are carried under that name, verbatim. No age
across the two clocks is computed anywhere, because nothing has measured their relation.

THE TRANSPORT. A NEW zmq REQ socket per request (the pattern of mission_capture_client.zmq_request: a REQ socket that timed
out cannot send again, so reusing one would turn one slow answer into every later request failing), LINGER 0, connected to
tcp://127.0.0.1:<port> on the robot. That port is the robot end of an ssh reverse forward the desktop owns
(demo_runtime.forwards.ReverseForward); the service itself binds the desktop's loopback only. Multipart messages: the
request is [header JSON, the stored PNG file's bytes]; an ok answer is [header JSON, record bytes, overlay bytes].

WHAT THIS DOES NOT ESTABLISH. That the desktop computes what the robot would have computed is the benchmark's claim, for
the CPU at ead60a6; the GPU is a separate equivalence (handoff 12.3 compared CPU runs only). That the process answering
on the port is the service: the trust boundary is the robot account and its ssh key, as for every other forward (section
12.2); a squatter on the robot's port would have to produce an answer that passes every check above for the exact frame,
request id and robot identity. The pid and session fields are correlation, not authentication.

Standard library at import time; zmq, numpy and cv2 are imported where a request is made (the module can be imported,
and its pure functions tested, where they are missing).
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

PROTOCOL = 1
OP_PING, OP_PERCEIVE = "ping", "perceive"
STATUS_OK, STATUS_REFUSED = "ok", "refused"
ROLES = ("wrist",)  # the benchmark's equivalence covers wrist frames only; a front frame is refused by both sides
POLL_SLICE_S = 0.1  # the longest a stop waits for the next look at its flag: at most 0.2 s is allowed, half of it is used
MAX_MESSAGE_BYTES = 32 * 1024 * 1024  # a 1280x720 PNG and its overlay are a few MiB; anything far larger is not an answer
MAX_CACHED_FRAMES = 64  # cube_candidates.MAX_CACHED_FRAMES, mirrored: an evicted frame refuses, it is never re-detected
EXCHANGE_SUFFIX = "-remote.json"
EXCHANGE_SCHEMA = "remote-perception-exchange/1"

# THE FROZEN PERCEPTION, pinned. The seven sources and perceive() at ead60a6, the bytes the robot benchmark measured
# (expect.json of the harness, handoff section 12.3). autonomy/tests/test_frozen_perception_pins.py restates every value.
FROZEN_SOURCES_SHA256 = {
    "autonomy/perception/zero_shot_detector.py": "f7f4a930487c73abd8b16620c2a0d6994d8148bdfdb2b57a6ab01db4f651b839",
    "tools/static_world_observation.py": "827dc664a4d20f89e0777a759edbd11a541b9c8e20766148e4a6fd4e3174ab94",
    "autonomy/perception/cube_recognizer.py": "38542d4027d56710c94e7ad412601269cc4163a1c281e5be939b76c24a5a3cbc",
    "autonomy/perception/pad_localizer.py": "e91f4bd4a8dcbe459fb0036e459f3a27c576f30e1974792fe5a5ea5df507e0ec",
    "autonomy/perception/pad_template_localizer.py": "1bd1c556a6f8ca957a9c3987c24c12cf130e70d86d25fd9cb2903be2282bf300",
    "autonomy/perception/frame_illumination.py": "8b6c051892f13b5b56b1843e1fd5ec966cf586584a284d966fe2338a1143f6f8",
    "autonomy/perception/observation_store.py": "0bb94068f9799625ff349b86ecb5a6e5f4f7998effeba198ed0a2f246620dffc",
}
PERCEIVE_SOURCE = "bin/laptop/perception/run_stage1_scan.py"
# The harness method (bench.py): the text from this line up to "\ndef make_detector". An AST extraction hashes differently.
PERCEIVE_HEAD = "def perceive(role: str, image, out_dir: Path, name: str, detector) -> list:"
PERCEIVE_END = "\ndef make_detector"
FROZEN_PERCEIVE_SHA256 = "19f769e525c3f155cacaf08c48f4bd090cfc6e3cd106bc1141922bf3bee9f179"
# What pad_template_localizer.load_template reads from a template directory, and nothing else.
TEMPLATE_FILES = ("template.json", "template.png", "valid.png")
TEMPLATE_ABSENT = "absent"  # static_world_observation.observe_frame runs without the print when the directory is missing

_REVISION = re.compile(r"[0-9a-f]{40}")
_REQUEST_ID = re.compile(r"[0-9a-f]{32}")
_REASON = re.compile(r"[a-z0-9][a-z0-9_.-]{0,63}")
# An observation id becomes two file names on each side; the store's own ids have this shape (observation_store).
_OBSERVATION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}")

# Reason codes. The controller prefixes each with "perception_remote_".
DEADLINE, LATE, MALFORMED = "deadline", "late", "reply_malformed"
REQUEST_ID_MISMATCH, ROBOT_MISMATCH, CAPTURE_MISMATCH = "request_id_mismatch", "robot_identity_mismatch", "capture_mismatch"
SERVICE_REFUSED = "service_refused"
MODEL_MISMATCH, CODE_MISMATCH, TEMPLATE_MISMATCH = "model_mismatch", "code_mismatch", "template_mismatch"
FRAME_FILE_MISMATCH, FRAME_DIGEST_MISMATCH = "frame_file_mismatch", "frame_digest_mismatch"
PAYLOAD_MISMATCH, SIDECAR_UNREADABLE = "payload_digest_mismatch", "sidecar_unreadable"
FRAME_UNREADABLE, TRANSPORT = "frame_unreadable", "transport_failed"
NOT_FROZEN, CONFIG_INCOMPLETE = "code_not_frozen", "config_incomplete"
MODEL_NOT_HERE = "model_not_on_this_computer"


class RemotePerceptionRefused(Exception):
    """An answer (or the absence of one) that must not be used. reason_code routes; detail is for the operator and names
    no path, address or value. service_reason is the service's own code when the service refused."""

    def __init__(self, reason_code: str, detail: str, service_reason: Optional[str] = None):
        super().__init__(f"{reason_code}: {detail}")
        self.reason_code, self.detail, self.service_reason = reason_code, detail, service_reason


class RemotePerceptionCancelled(Exception):
    """The request was abandoned because the cancel predicate (the stop) said so. Its answer, if it ever comes, arrives
    at a closed socket."""


# ---- digests: what both sides compute, the same way ------------------------------------------------------------------

def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_sha256(path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical(value) -> bytes:
    """The one serialization two identities are compared in: sorted keys, no whitespace, ASCII."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def perceive_source_sha256(text: str) -> str:
    start = text.index(PERCEIVE_HEAD)
    return hashlib.sha256(text[start:text.index(PERCEIVE_END, start)].encode()).hexdigest()


def code_digests(root) -> dict:
    """The frozen sources and perceive() of the tree at `root`, hashed as the benchmark hashed them."""
    root = Path(root)
    return {"sources_sha256": {relative: file_sha256(root / relative) for relative in FROZEN_SOURCES_SHA256},
            "perceive_sha256": perceive_source_sha256((root / PERCEIVE_SOURCE).read_text())}


def frozen_code_problems(digests: dict) -> list:
    """The names (sorted) whose digest is not the benchmark's. Empty means the tree runs the measured perception."""
    problems = [relative for relative, expected in FROZEN_SOURCES_SHA256.items() if (digests.get("sources_sha256") or {}).get(relative) != expected]
    if digests.get("perceive_sha256") != FROZEN_PERCEIVE_SHA256:
        problems.append("run_stage1_scan.perceive")
    return sorted(problems)


def template_digest(template_dir) -> str:
    """"sha256:<hex>" over the files the print localizer reads, by name, or "absent" when the directory is not there (then
    the unchanged observe_frame runs without the print, on both sides alike). A directory that exists without one of the
    files is hashed with that file marked missing: the localizer would fail on it, and so must a comparison."""
    if template_dir is None or not Path(template_dir).is_dir():
        return TEMPLATE_ABSENT
    digest = hashlib.sha256()
    for name in TEMPLATE_FILES:
        path = Path(template_dir) / name
        digest.update(f"{name}\0{file_sha256(path) if path.is_file() else 'missing'}\n".encode())
    return "sha256:" + digest.hexdigest()


def model_files_digest(files_sha256: dict) -> str:
    """"sha256:<hex>" over the model snapshot's {file name: sha256} map, canonical. demo_runtime.config computes the same
    (a test keeps the two equal): the configuration pins the map, the controller's environment carries this digest."""
    return "sha256:" + sha256_hex(canonical({str(name): str(value) for name, value in dict(files_sha256).items()}))


@dataclass(frozen=True)
class PerceptionIdentity:
    """What produced (or must have produced) an answer: the code, the pad template and the model. Two identities are
    equal or the answer is not used."""

    sources_sha256: dict
    perceive_sha256: str
    template_sha256: str
    model_revision: str
    model_files_digest: str

    def to_wire(self) -> dict:
        return {"sources_sha256": dict(self.sources_sha256), "perceive_sha256": self.perceive_sha256, "template_sha256": self.template_sha256,
                "model_revision": self.model_revision, "model_files_digest": self.model_files_digest}

    @classmethod
    def from_wire(cls, value) -> "PerceptionIdentity":
        if not isinstance(value, dict):
            raise ValueError("an identity is a JSON object")
        sources = value.get("sources_sha256")
        if not isinstance(sources, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in sources.items()):
            raise ValueError("sources_sha256 is a map of text to text")
        fields = [value.get(name) for name in ("perceive_sha256", "template_sha256", "model_revision", "model_files_digest")]
        if not all(isinstance(item, str) for item in fields):
            raise ValueError("every digest of an identity is text")
        return cls(dict(sources), *fields)

    def mismatch(self, other: "PerceptionIdentity") -> Optional[str]:
        """The reason code of the first way `other` is not this identity, or None."""
        if (other.model_revision, other.model_files_digest) != (self.model_revision, self.model_files_digest):
            return MODEL_MISMATCH
        if canonical(other.sources_sha256) != canonical(self.sources_sha256) or other.perceive_sha256 != self.perceive_sha256:
            return CODE_MISMATCH
        if other.template_sha256 != self.template_sha256:
            return TEMPLATE_MISMATCH
        return None


def robot_identity(root, template_dir, *, model_revision: str, model_files_digest_value: str) -> PerceptionIdentity:
    """The identity the ROBOT expects: its own tree's code (which must be the frozen code), its own pad template, and the
    configured model pin. Raises RemotePerceptionRefused(code_not_frozen) when its tree is not the measured perception."""
    digests = code_digests(root)
    problems = frozen_code_problems(digests)
    if problems:
        raise RemotePerceptionRefused(NOT_FROZEN, f"this tree does not run the measured perception ({', '.join(problems)})")
    if not _REVISION.fullmatch(model_revision or "") or not (model_files_digest_value or "").startswith("sha256:"):
        raise RemotePerceptionRefused(CONFIG_INCOMPLETE, "the model pin is not a 40-hex revision and a sha256 digest of its files")
    return PerceptionIdentity(digests["sources_sha256"], digests["perceive_sha256"], template_digest(template_dir), model_revision, model_files_digest_value)


# ---- the request and the acceptance ------------------------------------------------------------------------------------

def new_request(op: str, *, robot: dict, expect: PerceptionIdentity, budget_ms: int, capture: Optional[dict] = None,
                request_id: Optional[str] = None) -> dict:
    header = {"op": op, "v": PROTOCOL, "request_id": request_id or uuid.uuid4().hex, "robot": dict(robot), "expect": expect.to_wire(),
              "budget_ms": int(budget_ms)}
    if capture is not None:
        header["capture"] = dict(capture)
    return header


@dataclass
class Outstanding:
    """One request the robot sent and has not settled. Everything accept() compares an answer with is here, taken when
    the request was built: nothing is re-read from the answer to decide what it should have said."""

    header: dict
    sent_ns: int
    deadline_ns: int
    expect: PerceptionIdentity
    file_sha256: Optional[str] = None
    frame_digest: Optional[str] = None
    received_ns: Optional[int] = None  # stamped by wait() the moment the answer is read, before anything else is done

    @property
    def request_id(self) -> str:
        return self.header["request_id"]

    @property
    def op(self) -> str:
        return self.header["op"]


@dataclass
class Accepted:
    header: dict
    received_ns: int
    record: Optional[dict] = None
    sidecar: bytes = b""
    overlay: bytes = b""
    summary: list = field(default_factory=list)
    cube_detections: Optional[list] = None


def _refuse(code: str, detail: str, service_reason: Optional[str] = None):
    raise RemotePerceptionRefused(code, detail, service_reason)


def _detections_or_none(value):
    if value is None:
        return None
    if not isinstance(value, list):
        _refuse(MALFORMED, "the cube proposals are not a list")
    for item in value:
        box = item.get("box_xyxy") if isinstance(item, dict) else None
        if not (isinstance(item, dict) and isinstance(item.get("label"), str) and isinstance(item.get("score"), (int, float)) and not isinstance(item.get("score"), bool)
                and isinstance(box, list) and len(box) == 4 and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in box)):
            _refuse(MALFORMED, "a cube proposal is not {label, score, box_xyxy}")
    return copy.deepcopy(value)


def accept(parts, outstanding: Outstanding, now_ns: int) -> Accepted:
    """The answer `parts` (a multipart message) for `outstanding`, received at `now_ns` on the robot's monotonic clock, or
    RemotePerceptionRefused. The order is the module docstring's; the first failure is the reason."""
    if now_ns > outstanding.deadline_ns:
        _refuse(LATE, "the answer arrived after this request's deadline; it is never used")
    if not isinstance(parts, (list, tuple)) or not parts or not all(isinstance(part, (bytes, bytearray, memoryview)) for part in parts):
        _refuse(MALFORMED, "the answer is not a multipart message")
    try:
        header = json.loads(bytes(parts[0]).decode("ascii"))
    except (UnicodeDecodeError, ValueError):
        _refuse(MALFORMED, "the answer's header is not ASCII JSON")
    if not isinstance(header, dict) or header.get("v") != PROTOCOL or header.get("op") != outstanding.op:
        _refuse(MALFORMED, "the answer is not this protocol's answer to this op")
    if header.get("request_id") != outstanding.request_id:
        _refuse(REQUEST_ID_MISMATCH, "the answer names another request")
    if canonical(header.get("robot")) != canonical(outstanding.header.get("robot")):
        _refuse(ROBOT_MISMATCH, "the answer does not echo this robot's session, run and target")
    if canonical(header.get("capture")) != canonical(outstanding.header.get("capture")):
        _refuse(CAPTURE_MISMATCH, "the answer does not echo the capture this request named")
    if header.get("status") != STATUS_OK:
        reason = header.get("reason_code")  # kept only in the shape of a reason code: it reaches the operator's screen
        _refuse(SERVICE_REFUSED, "the service refused the request", reason if isinstance(reason, str) and _REASON.fullmatch(reason) else None)
    try:
        answered = PerceptionIdentity.from_wire(header.get("service"))
    except ValueError:
        _refuse(MALFORMED, "the answer does not state the service's identity")
    mismatch = outstanding.expect.mismatch(answered)
    if mismatch is not None:
        _refuse(mismatch, "the service's model, code or pad template is not the one this robot expects")
    accepted = Accepted(header=header, received_ns=now_ns)
    if outstanding.op == OP_PING:
        if len(parts) != 1:
            _refuse(MALFORMED, "a ping answer is one part")
        return accepted
    frame = header.get("frame") if isinstance(header.get("frame"), dict) else {}
    if frame.get("file_sha256") != outstanding.file_sha256:
        _refuse(FRAME_FILE_MISMATCH, "the bytes the service received are not the file this robot sent")
    if frame.get("frame_digest") != outstanding.frame_digest:
        _refuse(FRAME_DIGEST_MISMATCH, "the pixels the service decoded are not the pixels this robot decoded")
    result = header.get("result") if isinstance(header.get("result"), dict) else {}
    if len(parts) != 3:
        _refuse(MALFORMED, "a perception answer is three parts: header, record, overlay")
    sidecar, overlay = bytes(parts[1]), bytes(parts[2])
    if result.get("sidecar_sha256") != sha256_hex(sidecar) or result.get("overlay_sha256") != sha256_hex(overlay):
        _refuse(PAYLOAD_MISMATCH, "the record or the overlay is not the bytes the header names")
    try:
        record = json.loads(sidecar.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        _refuse(SIDECAR_UNREADABLE, "the perception record does not parse as JSON")
    if not isinstance(record, dict):
        _refuse(SIDECAR_UNREADABLE, "the perception record is not a JSON object")
    summary = result.get("summary")
    if not isinstance(summary, list) or not all(isinstance(line, str) for line in summary):
        _refuse(MALFORMED, "the summary is not a list of lines")
    accepted.record, accepted.sidecar, accepted.overlay, accepted.summary = record, sidecar, overlay, list(summary)
    accepted.cube_detections = _detections_or_none(result.get("cube_detections"))
    return accepted


# ---- what the controller hands to the cube listing in place of the detector cache --------------------------------------

class RemoteCubeDetections:
    """The robot-side stand-in for cube_candidates.CubeDetectionCache: the cube proposals of every ACCEPTED answer, keyed
    by the frame digest the robot computed itself (and the service's equalled). cube_candidates.enumerate_observation
    asks it for them exactly as it asks the cache, so the listing runs on the robot on the proposals that produced the
    record. detect() refuses: no model runs on the robot, and nothing may quietly start one."""

    def __init__(self, max_frames: int = MAX_CACHED_FRAMES, model_identity: Optional[str] = None):
        self._max_frames = int(max_frames)
        self._cube_detections = OrderedDict()
        self.model_identity = model_identity  # "<model>@<revision>" of the service the robot accepted at the ping

    def remember(self, digest: str, cube_detections) -> None:
        if cube_detections is None:
            self._cube_detections.pop(digest, None)  # a frame without proposals kept is "not seen", as in the cache
            return
        self._cube_detections[digest] = copy.deepcopy(list(cube_detections))
        self._cube_detections.move_to_end(digest)
        while len(self._cube_detections) > self._max_frames:
            self._cube_detections.popitem(last=False)

    def cube_detections(self, digest: str):
        found = self._cube_detections.get(digest)
        return None if found is None else copy.deepcopy(found)

    def detect(self, *_args, **_kwargs):
        raise RemotePerceptionRefused(MODEL_NOT_HERE, "no detector runs on this computer; perception is the desktop service's")


# ---- the client --------------------------------------------------------------------------------------------------------

def endpoint_for(port: int) -> str:
    """Always loopback: on the robot this is the robot end of the desktop's reverse forward."""
    return f"tcp://127.0.0.1:{int(port)}"


def _atomic_write(path: Path, data: bytes) -> None:
    partial = path.with_name(f".{path.name}.partial")
    partial.write_bytes(data)
    os.replace(partial, path)  # a reader sees the whole file or none of it


class RemotePerception:
    """One robot-side client of the desktop perception service. It never retries and never falls back."""

    def __init__(self, *, port: int, deadline_s: float, expect: PerceptionIdentity, robot: dict,
                 should_cancel: Callable[[], bool] = lambda: False, clock_ns: Callable[[], int] = time.monotonic_ns,
                 poll_slice_s: float = POLL_SLICE_S, context=None):
        if not 0 < int(port) < 65536:
            raise ValueError("the service port is 1..65535")
        if not float(deadline_s) > 0:
            raise ValueError("the deadline is a positive number of seconds")
        self.port, self.deadline_ns = int(port), int(float(deadline_s) * 1e9)
        self.expect, self.robot = expect, dict(robot)
        self.should_cancel, self._clock_ns = should_cancel, clock_ns
        self.poll_slice_s = min(float(poll_slice_s), 0.2)
        self._context = context
        self.detections = RemoteCubeDetections()
        self.service = None  # the service's own statement at the accepted ping
        self.exchanges = []  # (observation id, verdict, round trip ns): what this client did, for its owner's report

    # -- the socket ------------------------------------------------------------------------------------------------------
    def _zmq(self):
        import zmq  # imported here: importing this module must not need zmq

        return zmq

    def open_socket(self):
        zmq = self._zmq()
        context = self._context or zmq.Context.instance()
        sock = context.socket(zmq.REQ)
        sock.setsockopt(zmq.LINGER, 0)
        sock.setsockopt(zmq.MAXMSGSIZE, MAX_MESSAGE_BYTES)
        sock.connect(endpoint_for(self.port))
        return sock

    def send(self, header: dict, payload: Optional[bytes], *, file_sha256=None, frame_digest=None, deadline_ns=None):
        """(socket, Outstanding). The deadline starts when the request is handed to the socket."""
        sock = self.open_socket()
        sent_ns = self._clock_ns()
        outstanding = Outstanding(header=header, sent_ns=sent_ns, deadline_ns=sent_ns + (self.deadline_ns if deadline_ns is None else int(deadline_ns)),
                                  expect=self.expect, file_sha256=file_sha256, frame_digest=frame_digest)
        try:
            sock.send_multipart([canonical(header)] + ([] if payload is None else [payload]), copy=False)
        except Exception as error:  # noqa: BLE001 -- zmq's own failure, named by class only
            sock.close(0)
            _refuse(TRANSPORT, f"the request could not be sent ({type(error).__name__})")
        return sock, outstanding

    def wait(self, sock, outstanding: Outstanding, *, until_ns: Optional[int] = None, cancellable: bool = True):
        """The parts of the answer, or None when `until_ns` (default: the deadline) passed first. Raises
        RemotePerceptionCancelled when the cancel predicate turns true between slices. The socket stays open."""
        zmq = self._zmq()
        poller = zmq.Poller()
        poller.register(sock, zmq.POLLIN)
        until_ns = outstanding.deadline_ns if until_ns is None else until_ns
        while True:
            if cancellable and self.should_cancel():
                raise RemotePerceptionCancelled()
            remaining_ns = until_ns - self._clock_ns()
            if remaining_ns <= 0:
                return None
            ready = dict(poller.poll(max(1, int(min(remaining_ns / 1e6, self.poll_slice_s * 1000.0)))))
            if ready.get(sock):
                try:
                    parts = sock.recv_multipart(zmq.NOBLOCK)
                except zmq.Again:
                    continue
                except Exception as error:  # noqa: BLE001
                    _refuse(TRANSPORT, f"the answer could not be received ({type(error).__name__})")
                outstanding.received_ns = self._clock_ns()
                return parts

    def exchange(self, header: dict, payload: Optional[bytes], *, file_sha256=None, frame_digest=None, cancellable: bool = True):
        """Send, wait for the deadline, close, accept. Returns (Accepted, Outstanding)."""
        sock, outstanding = self.send(header, payload, file_sha256=file_sha256, frame_digest=frame_digest)
        try:
            parts = self.wait(sock, outstanding, cancellable=cancellable)
        finally:
            sock.close(0)  # LINGER 0: an answer that comes after this has nowhere to arrive
        if parts is None:
            _refuse(DEADLINE, f"no answer within the deadline of {self.deadline_ns / 1e9:g} s")
        return accept(parts, outstanding, outstanding.received_ns), outstanding

    # -- the two operations ----------------------------------------------------------------------------------------------
    def ping(self) -> dict:
        """The service's statement of what it runs, accepted only when it is this robot's expectation. Not cancellable:
        it happens before the controller has a stop flag, and it is bounded by the deadline."""
        header = new_request(OP_PING, robot=self.robot, expect=self.expect, budget_ms=self.deadline_ns // 1_000_000)
        accepted, _ = self.exchange(header, None, cancellable=False)
        service = accepted.header.get("service_info") if isinstance(accepted.header.get("service_info"), dict) else {}
        self.service = {"identity": accepted.header.get("service"), **service}
        name = service.get("model_name")
        if isinstance(name, str) and name:
            self.detections.model_identity = f"{name}@{self.expect.model_revision}"
        return self.service

    def perceive(self, frame_path, out_dir, name: str, *, role: str = "wrist", capture: Optional[dict] = None) -> list:
        """The desktop's perception of the stored frame at `frame_path`, written where run_stage1_scan.perceive writes it
        (<out_dir>/<name>.json and <name>-overlay.png, the service's bytes unchanged) only once accepted. Returns the
        summary lines. Always writes <name>-remote.json, the exchange as evidence (accepted or not); never a result on a
        refusal. Raises RemotePerceptionRefused or RemotePerceptionCancelled."""
        import cv2  # the robot's own decode, as the controller's legacy path decodes (cv2.imread)

        from cube_candidates import frame_digest as digest_of

        if role not in ROLES:
            _refuse(MALFORMED, "only wrist frames are perceived remotely")
        if not _OBSERVATION_ID.fullmatch(str(name)):
            _refuse(MALFORMED, "the observation id is not a plain file name")
        out_dir, started_ns = Path(out_dir), self._clock_ns()
        try:
            payload = Path(frame_path).read_bytes()
        except OSError:
            _refuse(FRAME_UNREADABLE, "the stored frame could not be read on this computer")
        image = cv2.imread(str(frame_path))
        if image is None:
            _refuse(FRAME_UNREADABLE, "the stored frame could not be decoded on this computer")
        sent_sha, sent_digest = sha256_hex(payload), digest_of(image)
        capture_identity = {"observation_id": name, "role": role, "camera_service_session": None, "seq": None, **(capture or {}),
                            "file_sha256": sent_sha, "frame_digest": sent_digest}
        header = new_request(OP_PERCEIVE, robot=self.robot, expect=self.expect, budget_ms=self.deadline_ns // 1_000_000, capture=capture_identity)
        evidence = {"schema": EXCHANGE_SCHEMA, "request": header, "payload_bytes": len(payload), "reply": None, "accepted": False, "refusal": None,
                    "timing": {"clock": "robot_monotonic", "prepare_ns": self._clock_ns() - started_ns}}
        out_dir.mkdir(parents=True, exist_ok=True)
        try:
            accepted, outstanding = self.exchange(header, payload, file_sha256=sent_sha, frame_digest=sent_digest)
        except RemotePerceptionCancelled:
            evidence["refusal"] = "cancelled_by_stop"
            self._record(out_dir, name, evidence, "cancelled", started_ns)
            raise
        except RemotePerceptionRefused as refusal:
            evidence["refusal"] = {"reason_code": refusal.reason_code, "service_reason": refusal.service_reason}
            self._record(out_dir, name, evidence, refusal.reason_code, started_ns)
            raise
        evidence["reply"], evidence["accepted"] = accepted.header, True
        evidence["timing"].update(round_trip_ns=accepted.received_ns - outstanding.sent_ns, deadline_ns=self.deadline_ns)
        write_started = self._clock_ns()
        _atomic_write(out_dir / f"{name}.json", accepted.sidecar)
        _atomic_write(out_dir / f"{name}-overlay.png", accepted.overlay)
        self.detections.remember(sent_digest, accepted.cube_detections)
        evidence["timing"]["write_ns"] = self._clock_ns() - write_started
        self._record(out_dir, name, evidence, "accepted", started_ns)
        return list(accepted.summary)

    def _record(self, out_dir: Path, name: str, evidence: dict, verdict: str, started_ns: int) -> None:
        evidence["timing"]["total_ns"] = self._clock_ns() - started_ns
        self.exchanges.append((name, verdict, evidence["timing"].get("round_trip_ns")))
        del self.exchanges[:-256]  # a report of the latest exchanges; nothing here may grow without bound
        try:
            _atomic_write(out_dir / f"{name}{EXCHANGE_SUFFIX}", json.dumps(evidence, indent=1, sort_keys=True).encode("ascii"))
        except (OSError, TypeError, ValueError):
            pass  # evidence of the exchange is not a result; failing to keep it changes no verdict
