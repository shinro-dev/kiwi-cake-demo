#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""perception_service: the desktop half of the ONE offboard exception. It returns PERCEPTION RESULTS ONLY.

WHAT THE OPERATOR APPROVED (2026-09-25), and nothing more. The complete per-frame perception operation, OWLv2 and the pad
fitting included, may run on the desktop, with the existing GPU environment for detection and the existing geometry
implementation. Cake, the supervised robot host, mission and controller execution, target selection, the physical
predicates and all motion authority stay on the robot. This process therefore has no robot link, no command path, no
lease and no camera: it receives one stored frame, runs the controller's own per-frame function on it, and answers with
the bytes that function wrote. Whether an answer is used is decided on the robot (autonomy/perception/remote_perception.py
accept()), never here.

THE COMPUTATION IS THE CONTROLLER'S, UNCHANGED. Every request is answered by calling run_stage1_scan.perceive, looked up
on that module at call time (so the module's function, not a copy of it, is what runs), with the detector the mission
path already builds: cube_candidates.CubeDetectionCache(run_stage1_scan.make_detector()). That is OWLv2 with its own
preprocessing, labels and threshold; recognize_cube; the pad outline fit at the three focal-sweep factors; the print
template; the overlay; the summary. Nothing here reimplements, tunes or reorders a stage. The one addition is a
pass-through recorder around the detector that keeps a copy of the answer it returned, for the evidence and for the GPU
equivalence comparison; it returns the detector's own list object, so perceive computes exactly what it computes without it.

WHAT IS PROVEN BEFORE THE FIRST REQUEST IS SERVED (the process exits 3 otherwise):
  - the seven frozen sources and perceive() are the bytes the robot benchmark measured (remote_perception.FROZEN_*,
    hashed by the harness's method);
  - the thresholds, labels, focal sweep and pad size the modules carry are the benchmark's;
  - the model in the offline hub cache is the pinned revision, and every file of that snapshot has its pinned sha256
    (the harness's own identification; HF_HUB_OFFLINE and TRANSFORMERS_OFFLINE are forced before any model code loads,
    so nothing is downloaded and the cache's snapshot is what loads). Checked again after the weights are loaded;
  - the socket is bound on 127.0.0.1 and the bound endpoint, read back, says so.

EVERY REQUEST (a refusal is an answer with status "refused" and a reason code; the service never goes silent on a request
it received, because the robot's REQ socket is waiting for exactly one answer):
  - protocol version, op, request id and the parts that op carries;
  - the robot's expected identity (code, template, model) equals this service's own; the pad template on disk has not
    changed since start (the localizer caches what it loaded, so a changed file would silently not be used);
  - the frame decodes (cv2.imdecode, IMREAD_COLOR: the pixels cv2.imread gives for the same PNG bytes) and is a wrist frame;
  - the request's budget is not already spent by the time perceive would start (desktop time from receipt).
The answer echoes the request's robot and capture identity unchanged and states what it received and decoded (the sha256
of the bytes, the digest of the pixels), the service's identity, and its own times, labelled as the desktop's monotonic
clock. Time a request spent queued in the transport before this process received it is NOT visible here: one request is
served at a time, and a request that arrives during another's perceive waits in the socket. The robot's own deadline, on
the robot's clock, is the only bound on that and the only authority on lateness.

THE REVERSE FORWARD (--reverse-forward). The robot reaches this socket through `ssh -N -R 127.0.0.1:<robot port>:127.0.0.1:
<service port>` that THIS process owns (demo_runtime.forwards.ReverseForward), to the target in KC_PI_SSH, which is never
printed. The robot port may not be one of the robot's own service ports (the host's command and observation sockets, the
host boundary's, the camera service's mission and preview sockets): a listener there would stand in for that service.

Run with the GPU environment's interpreter. Exit status: 0 stopped by a signal, 3 a startup check refused, 5 usage.
"""
import argparse
import copy
import json
import os
import signal
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
for _sub in ("bin/laptop/perception", "autonomy/perception", "autonomy/manipulation", "tools"):
    if str(ROOT / _sub) not in sys.path:
        sys.path.insert(0, str(ROOT / _sub))

import remote_perception as rp  # noqa: E402  (standard library at import time)

# The benchmark's thresholds (expect.json, handoff section 12.3). The sources that carry them are hash-pinned as well; this
# reads them back from the loaded modules, so a module that was replaced in memory is caught too.
PINNED_THRESHOLDS = {"MODEL_NAME": "google/owlv2-base-patch16-ensemble", "DEFAULT_THRESHOLD": 0.08, "CUBE_LABELS": ["a yellow cube", "a small wooden block"],
                     "PAD_LABELS": ["a mouse pad", "a dark blue desk mat"], "NOMINAL_FOCAL_PX": {"front": 1200.0, "wrist": 1200.0}, "FOCAL_SWEEP": [0.85, 1.0, 1.15],
                     "PAD_SIZE_M": [0.415, 0.295], "CONTACT_CONFIDENCE_FLOOR": 0.5}
OFFLINE_ENV = {"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"}
POLL_MS = 200
BOUND_PREFIX = "tcp://127.0.0.1:"

# Reason codes of a refusal, the service's own vocabulary (the robot keeps them as service_reason).
REQUEST_MALFORMED, PROTOCOL_UNSUPPORTED, OP_UNKNOWN = "request_malformed", "protocol_unsupported", "op_unknown"
TEMPLATE_CHANGED, FRAME_UNDECODABLE, ROLE_UNSUPPORTED = "template_changed_since_start", "frame_undecodable", "role_not_supported"
BUDGET_SPENT, PERCEPTION_RAISED, SERVICE_ERROR = "budget_spent_before_perceive", "perception_raised", "service_error"


def thresholds_now() -> dict:
    import static_world_observation as swo
    import zero_shot_detector as zsd

    return json.loads(json.dumps({"MODEL_NAME": zsd.MODEL_NAME, "DEFAULT_THRESHOLD": zsd.DEFAULT_THRESHOLD, "CUBE_LABELS": list(zsd.CUBE_LABELS),
                                  "PAD_LABELS": list(zsd.PAD_LABELS), "NOMINAL_FOCAL_PX": dict(swo.NOMINAL_FOCAL_PX), "FOCAL_SWEEP": list(swo.FOCAL_SWEEP),
                                  "PAD_SIZE_M": list(swo.PAD_SIZE_M), "CONTACT_CONFIDENCE_FLOOR": swo.CONTACT_CONFIDENCE_FLOOR}))


def model_pins(model_name: str) -> dict:
    """The model the detector will load, identified from the local hub cache, offline: the revision refs/main names and
    every file of that snapshot with its sha256. The perception harness's own method."""
    from huggingface_hub import constants

    repo = os.path.join(constants.HF_HUB_CACHE, "models--" + model_name.replace("/", "--"))
    with open(os.path.join(repo, "refs", "main")) as handle:
        revision = handle.read().strip()
    snapshot = os.path.join(repo, "snapshots", revision)
    return {"revision": revision, "files_sha256": {name: rp.file_sha256(os.path.join(snapshot, name)) for name in sorted(os.listdir(snapshot))}}


def read_model_pin(path) -> dict:
    """{revision, files_sha256} from a pin file: either that object, or the harness's expect.json (its "model" section)."""
    document = json.loads(Path(path).read_text())
    pin = document.get("model", document) if isinstance(document, dict) else None
    if not (isinstance(pin, dict) and isinstance(pin.get("revision"), str) and isinstance(pin.get("files_sha256"), dict) and pin["files_sha256"]):
        raise ValueError("a model pin names a revision and a non-empty map of file name to sha256")
    return {"revision": pin["revision"], "files_sha256": {str(k): str(v) for k, v in pin["files_sha256"].items()}}


class _Recording:
    """Pass-through: detect() forwards unchanged and returns the wrapped detector's own answer; a deep copy of that answer
    and the number of calls are kept. Any other attribute is the wrapped detector's own."""

    def __init__(self, inner):
        self._inner, self.calls, self.last = inner, 0, None

    def detect(self, image_bgr, labels, *args, **kwargs):
        answer = self._inner.detect(image_bgr, labels, *args, **kwargs)
        self.calls, self.last = self.calls + 1, copy.deepcopy(answer)
        return answer

    def __getattr__(self, name):
        inner = self.__dict__.get("_inner")
        if inner is None:
            raise AttributeError(name)
        return getattr(inner, name)


class PerceptionService:
    """Answers requests, one at a time. handle() never raises: every request gets exactly one answer."""

    def __init__(self, *, identity: rp.PerceptionIdentity, detector, recorder: _Recording, info: dict, clock_ns=time.monotonic_ns):
        import run_stage1_scan

        self.identity, self.detector, self.recorder, self._clock_ns = identity, detector, recorder, clock_ns
        self._scan = run_stage1_scan  # perceive is looked up on THIS module at every call, never copied
        self.info = dict(info)
        self.served = self.refused = 0

    def template_dir(self):
        """The directory perceive will read the print from: the scan CLI's configured one, else static_world_observation's
        own default, exactly as perceive decides it."""
        import static_world_observation as swo

        configured = getattr(self._scan, "_PAD_TEMPLATE_DIR", None)
        return swo.TEMPLATE_DIR if configured is None else configured

    # -- answers ---------------------------------------------------------------------------------------------------------
    def _header(self, request: dict, status: str, reason=None, **extra) -> dict:
        return {"v": rp.PROTOCOL, "op": request.get("op"), "request_id": request.get("request_id"), "robot": request.get("robot"),
                "capture": request.get("capture"), "status": status, "reason_code": reason, "service": self.identity.to_wire(),
                "service_info": self.info, **extra}

    def _refusal(self, request: dict, reason: str, received_ns: int) -> list:
        self.refused += 1
        return [rp.canonical(self._header(request, rp.STATUS_REFUSED, reason, timing={"clock": "desktop_monotonic", "total_ns": self._clock_ns() - received_ns}))]

    def handle(self, parts, received_ns: int) -> list:
        request = {}
        try:
            try:
                request = json.loads(bytes(parts[0]).decode("ascii"))
            except (IndexError, UnicodeDecodeError, ValueError):
                request = {}
            if not isinstance(request, dict) or not request:
                return self._refusal({}, REQUEST_MALFORMED, received_ns)
            if request.get("v") != rp.PROTOCOL:
                return self._refusal(request, PROTOCOL_UNSUPPORTED, received_ns)
            if request.get("op") not in (rp.OP_PING, rp.OP_PERCEIVE):
                return self._refusal(request, OP_UNKNOWN, received_ns)
            if not isinstance(request.get("request_id"), str) or not rp._REQUEST_ID.fullmatch(request["request_id"]) or not isinstance(request.get("robot"), dict):
                return self._refusal(request, REQUEST_MALFORMED, received_ns)
            try:
                expected = rp.PerceptionIdentity.from_wire(request.get("expect"))
            except ValueError:
                return self._refusal(request, REQUEST_MALFORMED, received_ns)
            mismatch = self.identity.mismatch(expected)
            if mismatch is not None:
                return self._refusal(request, mismatch, received_ns)
            if rp.template_digest(self.template_dir()) != self.identity.template_sha256:
                return self._refusal(request, TEMPLATE_CHANGED, received_ns)
            if request["op"] == rp.OP_PING:
                if len(parts) != 1:
                    return self._refusal(request, REQUEST_MALFORMED, received_ns)
                self.served += 1
                return [rp.canonical(self._header(request, rp.STATUS_OK, timing={"clock": "desktop_monotonic", "total_ns": self._clock_ns() - received_ns}))]
            return self._perceive(request, parts, received_ns)
        except Exception:  # noqa: BLE001 -- one answer per request, always; the class name alone is not worth the risk of a path
            return self._refusal(request if isinstance(request, dict) else {}, SERVICE_ERROR, received_ns)

    def _perceive(self, request: dict, parts, received_ns: int) -> list:
        import cv2
        import numpy as np

        from cube_candidates import frame_digest

        capture = request.get("capture")
        budget = request.get("budget_ms")
        if len(parts) != 2 or not isinstance(capture, dict) or not isinstance(budget, int) or isinstance(budget, bool) or budget <= 0:
            return self._refusal(request, REQUEST_MALFORMED, received_ns)
        name = capture.get("observation_id")
        if not isinstance(name, str) or not rp._OBSERVATION_ID.fullmatch(name):
            return self._refusal(request, REQUEST_MALFORMED, received_ns)
        if capture.get("role") not in rp.ROLES:
            return self._refusal(request, ROLE_UNSUPPORTED, received_ns)
        payload = bytes(parts[1])
        image = cv2.imdecode(np.frombuffer(payload, np.uint8), cv2.IMREAD_COLOR) if payload else None
        if image is None:
            return self._refusal(request, FRAME_UNDECODABLE, received_ns)
        frame = {"file_sha256": rp.sha256_hex(payload), "frame_digest": frame_digest(image), "shape": [int(v) for v in image.shape]}
        started_ns = self._clock_ns()
        queue_ns = started_ns - received_ns
        if queue_ns >= budget * 1_000_000:
            return self._refusal(request, BUDGET_SPENT, received_ns)
        calls_before = self.recorder.calls
        try:
            with tempfile.TemporaryDirectory(prefix="kc-perception-") as scratch:
                summary = self._scan.perceive(capture["role"], image, Path(scratch), name, self.detector)
                sidecar = (Path(scratch) / f"{name}.json").read_bytes()
                overlay = (Path(scratch) / f"{name}-overlay.png").read_bytes()
        except Exception:  # noqa: BLE001 -- whatever the unchanged perception raised, the robot gets an answer that says so
            return self._refusal(request, PERCEPTION_RAISED, received_ns)
        perceive_ns = self._clock_ns() - started_ns
        calls = self.recorder.calls - calls_before
        self.served += 1
        header = self._header(request, rp.STATUS_OK, frame=frame,
                              result={"summary": [str(line) for line in summary], "sidecar_sha256": rp.sha256_hex(sidecar), "overlay_sha256": rp.sha256_hex(overlay),
                                      "cube_detections": self.detector.cube_detections(frame["frame_digest"]),
                                      "detections": self.recorder.last if calls == 1 else None, "detect_calls": calls},
                              timing={"clock": "desktop_monotonic", "queue_ns": queue_ns, "perceive_ns": perceive_ns, "total_ns": self._clock_ns() - received_ns,
                                      "served": self.served})
        return [json.dumps(header, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=float).encode("ascii"), sidecar, overlay]

    # -- the loop --------------------------------------------------------------------------------------------------------
    def serve(self, sock, stop: threading.Event, max_requests=None) -> int:
        import zmq

        poller = zmq.Poller()
        poller.register(sock, zmq.POLLIN)
        answered = 0
        while not stop.is_set():
            if not dict(poller.poll(POLL_MS)).get(sock):
                continue
            parts = sock.recv_multipart()
            received_ns = self._clock_ns()
            sock.send_multipart(self.handle(parts, received_ns))
            answered += 1
            if max_requests is not None and answered >= max_requests:
                break
        return answered


def bind_loopback(context, port: int):
    """A REP socket on 127.0.0.1:<port> (0: the kernel picks), with the bound endpoint read back. Anything but loopback
    ends the process: the service carries no credential and must not be reachable from a network."""
    import zmq

    sock = context.socket(zmq.REP)
    sock.setsockopt(zmq.LINGER, 0)
    sock.setsockopt(zmq.MAXMSGSIZE, rp.MAX_MESSAGE_BYTES)
    sock.bind(f"{BOUND_PREFIX}{int(port)}")
    bound = sock.getsockopt(zmq.LAST_ENDPOINT).decode("ascii")
    if not bound.startswith(BOUND_PREFIX):
        sock.close(0)
        raise RuntimeError("the service socket is not bound on loopback only")
    return sock, int(bound.rsplit(":", 1)[1])


def identify(root, template_dir, model_pin: dict, cache_pins: dict) -> tuple:
    """(PerceptionIdentity, problems). Problems name WHAT is wrong, never a path."""
    digests = rp.code_digests(root)
    problems = [f"source not frozen: {name}" for name in rp.frozen_code_problems(digests)]
    if thresholds_now() != PINNED_THRESHOLDS:
        problems.append("thresholds differ from the benchmark's")
    if cache_pins["revision"] != model_pin["revision"]:
        problems.append("the cached model revision is not the pinned one")
    if cache_pins["files_sha256"] != model_pin["files_sha256"]:
        problems.append("the cached model files are not the pinned ones")
    identity = rp.PerceptionIdentity(digests["sources_sha256"], digests["perceive_sha256"], rp.template_digest(template_dir), model_pin["revision"],
                                     rp.model_files_digest(model_pin["files_sha256"]))
    return identity, problems


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="perception_service.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, required=True, help="the loopback port to serve on (0: any free one)")
    parser.add_argument("--template-dir", required=True, help="the pad print template perceive reads (the robot compares its digest)")
    parser.add_argument("--model-pin", required=True, help="json with the pinned revision and files_sha256 (or the harness's expect.json)")
    parser.add_argument("--reverse-forward", action="store_true", help="own an ssh -R forward from the robot's loopback to this service (KC_PI_SSH)")
    parser.add_argument("--robot-port", type=int, default=None, help="the robot-side loopback port of the reverse forward")
    parser.add_argument("--max-requests", type=int, default=None, help="stop after this many answers (tests, a bounded dry-run)")
    args = parser.parse_args(argv)
    if args.reverse_forward and not args.robot_port:
        parser.error("--reverse-forward needs --robot-port")
    for name, value in OFFLINE_ENV.items():  # BEFORE any model code is imported: the cache's snapshot is what loads, nothing is fetched
        os.environ[name] = value
    import run_stage1_scan as scan
    import zero_shot_detector as zsd
    from cube_candidates import CubeDetectionCache

    template_dir = Path(os.path.expanduser(args.template_dir))
    try:
        model_pin = read_model_pin(args.model_pin)
    except (OSError, ValueError) as error:
        print(f"REFUSED: the model pin cannot be read ({type(error).__name__})", file=sys.stderr)
        return 3
    identity, problems = identify(ROOT, template_dir, model_pin, model_pins(zsd.MODEL_NAME))
    if problems:
        print("REFUSED: " + "; ".join(problems), file=sys.stderr)
        return 3
    scan.use_runtime_paths(scan.RuntimePaths(None, scan._LEGACY_HOME_FILE, template_dir, None))  # perceive reads the print from HERE
    inner = scan.make_detector()
    inner._load()  # the detector's own lazy load, done once now (as the benchmark harness does) so the first request does not pay it
    if model_pins(zsd.MODEL_NAME) != model_pin:
        print("REFUSED: the model cache changed while the weights were loading", file=sys.stderr)
        return 3
    recorder = _Recording(inner)
    import cv2
    import numpy
    import torch
    import transformers

    info = {"model_name": zsd.MODEL_NAME, "device": str(inner._device), "service_session": uuid.uuid4().hex,
            "versions": {"torch": torch.__version__, "transformers": transformers.__version__, "cv2": cv2.__version__, "numpy": numpy.__version__}}
    service = PerceptionService(identity=identity, detector=CubeDetectionCache(recorder), recorder=recorder, info=info)
    import zmq

    context = zmq.Context.instance()
    try:
        sock, port = bind_loopback(context, args.port)
    except Exception as error:  # noqa: BLE001
        print(f"REFUSED: the service socket could not be bound on loopback ({type(error).__name__})", file=sys.stderr)
        return 3
    stop = threading.Event()
    for number in (signal.SIGINT, signal.SIGTERM):
        signal.signal(number, lambda *_: stop.set())
    forward = None
    if args.reverse_forward:
        sys.path.insert(0, str(ROOT / "packages" / "demo-runtime"))
        from demo_runtime import forwards

        forward = forwards.ReverseForward(ssh_target=os.environ.get("KC_PI_SSH", ""), robot_port=args.robot_port, service_port=port)
        if not forward.start():
            print(f"REFUSED: the reverse forward was not started ({forward.reason})", file=sys.stderr)
            sock.close(0)
            return 3
    print(f"perception service: model {zsd.MODEL_NAME}@{model_pin['revision'][:12]} on {info['device']}; code frozen; template {identity.template_sha256[:19]}; "
          f"serving on 127.0.0.1:{port}" + ("; reverse forward starting" if forward else ""), flush=True)
    try:
        if forward is not None:
            watcher = threading.Thread(target=_watch_forward, args=(forward, stop), daemon=True)
            watcher.start()
        service.serve(sock, stop, args.max_requests)
    finally:
        if forward is not None:
            forward.stop()
        sock.close(0)
    print(f"perception service: stopped; answered {service.served} ok and {service.refused} refused", flush=True)
    return 0


def _watch_forward(forward, stop: threading.Event) -> None:
    """A forward that dies stops the service: without it no robot can reach the socket, and a service nobody can reach
    should not look like one that is serving. Nothing restarts it here (demo_runtime.forwards: a dead forward is a fact)."""
    announced = False
    while not stop.is_set():
        state = forward.check()
        if state == "running" and not announced:
            print("perception service: reverse forward running", flush=True)
            announced = True
        elif state not in ("starting", "running"):
            print(f"perception service: reverse forward {state} ({forward.reason}); stopping", flush=True)
            stop.set()
            return
        time.sleep(0.5)


if __name__ == "__main__":
    sys.exit(main())
