# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""autonomy.perception.mission_capture_client: the controller's capture, asked of the camera service.

WHY THIS EXISTS. bin/pi/camera_service.py holds each camera open for its whole life, so once it runs, the per-capture
provider (PiCamera in run_stage1_scan.py, which opens the device over ssh for ONE lifecycle per frame) can no longer
open the wrist camera at all: it fails closed with camera_lock_held. A service that owns the camera and a controller
that opens it directly cannot both exist, so the service must not be started before this client can take its place.

WHAT IT GUARANTEES, AND WHY EACH ONE IS HERE.

  A FRESH FRAME, OR A REFUSAL. The service decides freshness (it drains, it settles, it compares against the previous
  mission frame) and says so in the reply. This client never decides a frame is good enough: it passes the service's
  own verdict through, and a request that could not be answered is an error, not a stale frame quietly returned. There
  is NO fallback to a preview picture. A preview frame is a low-rate JPEG for a screen; a mission capture is decision
  evidence, and substituting one for the other would put a picture nobody asked for behind a measurement.

  THE FRAME THAT WAS JUST TAKEN, PROVEN. This is the defect class that already happened here: on 2026-09-19 a shared
  directory name made scp nest into an existing directory and the OLD frame was read back as the new capture, and five
  observations stored the same image. Nothing failed. So the service names each frame with a random stem and states its
  sha256 and byte count, and this client REFUSES a file whose digest does not match the reply. A stale or truncated
  frame is an error with a reason code, never data.

  THE IMAGE PROPERTIES THE CONTROLLER NEEDS. The perception the controller runs was built against 1280x720 captures at
  the settle depth the scan tool used. Those are asked for explicitly and CHECKED on the way back: a service configured
  for something else is refused rather than silently feeding smaller frames into a solver calibrated for larger ones.

  EVIDENCE THAT BELONGS TO THIS REQUEST. The reply carries the service session, the frame sequence and the source
  stamp, and they travel with the frame so a record can name the capture it actually rests on.

CLOCKS ARE NOT MIXED. Every stamp the service returns is the robot computer's monotonic clock and is passed through
verbatim under that name. The only laptop-clock number this module produces is how long the round trip took, and it is
labelled as such. An age across the two clocks is not computed, because nothing has measured their relation.

THIS MODULE COMMANDS NO MOTION. It asks for a picture over a loopback socket and copies a file back. It cannot move a
motor, and it holds no lock of its own.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

DEFAULT_MISSION_PORT = 5570  # bin/pi/camera_service.py: DEFAULT_MISSION_PORT
PROVIDER = "camera_service"
ROLES = ("wrist", "front")
# What the controller's perception was built against. Asked for, and checked on the way back.
REQUIRED_WIDTH, REQUIRED_HEIGHT = 1280, 720
DEFAULT_SETTLE_FRAMES = 6
DEFAULT_TIMEOUT_S = 20.0


class CaptureRefused(RuntimeError):
    """A mission capture did not happen, or what came back could not be trusted. It carries a reason CODE, so a caller
    routes on the code and an operator reads the sentence; neither is parsed out of the other."""

    def __init__(self, reason_code: str, detail: str):
        super().__init__(f"{reason_code}: {detail}")
        self.reason_code, self.detail = reason_code, detail


@dataclass(frozen=True)
class MissionFrame:
    """One capture: where the image is on THIS computer, and everything the service said about it."""

    role: str
    path: Path
    sha256: str
    bytes: int
    width: Optional[int]
    height: Optional[int]
    service_session: str
    seq: Optional[int]
    source_stamp: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)
    lifecycle_record: dict = field(default_factory=dict)
    # LAPTOP monotonic, nanoseconds: the round trip and the copy, as two separate numbers. Never compared with a
    # robot-computer stamp.
    request_ns: int = 0
    transfer_ns: int = 0

    @property
    def identical_to_previous(self) -> bool:
        """The SERVICE's own suspicion that this frame is the previous mission frame again. Passed through, never
        recomputed here: it is the producer that can tell."""
        return bool(self.metadata.get("identical_to_previous_mission_frame"))


class MissionCaptureClient:
    """Asks the camera service for one full-resolution frame and brings it back, verified.

    `request` speaks one JSON request/reply on the service's REP socket; `fetch` copies one file back. Both are
    injected so this class is testable without zmq, without ssh and without a camera.
    """

    def __init__(self, *, request: Callable[[dict, float], dict], fetch: Callable[[str, Path], None],
                 local_dir, width: int = REQUIRED_WIDTH, height: int = REQUIRED_HEIGHT,
                 settle_frames: int = DEFAULT_SETTLE_FRAMES, timeout_s: float = DEFAULT_TIMEOUT_S,
                 clock_ns: Callable[[], int] = time.monotonic_ns):
        self._request, self._fetch = request, fetch
        self.local_dir = Path(local_dir)
        self.width, self.height, self.settle_frames = width, height, settle_frames
        self.timeout_s, self._clock_ns = timeout_s, clock_ns
        self.captures_ok = self.captures_refused = 0

    def ping(self) -> dict:
        reply = self._ask({"op": "ping"})
        if reply.get("provider") != PROVIDER:
            raise CaptureRefused("not_the_camera_service", "the socket answered, but not as the camera service")
        return reply

    def status(self) -> dict:
        return self._ask({"op": "status"}).get("status") or {}

    def capture(self, role: str) -> MissionFrame:
        """One fresh frame of `role`, or CaptureRefused. Nothing is returned that was not taken for THIS request."""
        if role not in ROLES:
            raise CaptureRefused("unknown_role", "a capture names one of the service's camera roles")
        started_ns = self._clock_ns()
        reply = self._ask({"op": "capture", "role": role, "settle_frames": self.settle_frames,
                           "width": self.width, "height": self.height})
        request_ns = self._clock_ns() - started_ns
        metadata = reply.get("frame")
        if not isinstance(metadata, dict):
            self.captures_refused += 1
            raise CaptureRefused("no_frame_in_reply", "the service answered a capture without naming a frame")
        self._check_properties(role, metadata)

        relative = metadata.get("relative_path")
        if not isinstance(relative, str) or not relative:
            self.captures_refused += 1
            raise CaptureRefused("no_frame_path", "the reply named no path for the frame it says it took")
        # A name of the service's own choosing, under a directory of ours. The service picks a random stem per frame,
        # so two captures never land on one name and a copy can never quietly overwrite an earlier frame.
        destination = self.local_dir / relative
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if destination.exists():
            self.captures_refused += 1
            raise CaptureRefused("frame_path_collision", "a file of that name is already here; refusing to read a possibly stale frame")
        transfer_started_ns = self._clock_ns()
        try:
            self._fetch(relative, destination)
        except Exception as error:  # noqa: BLE001 - the transport's own failure, named by reason code
            self.captures_refused += 1
            raise CaptureRefused("frame_transfer_failed", f"the frame could not be copied back: {type(error).__name__}") from None
        transfer_ns = self._clock_ns() - transfer_started_ns

        self._verify_bytes(destination, metadata)
        self.captures_ok += 1
        return MissionFrame(
            role=role, path=destination, sha256=str(metadata.get("sha256")), bytes=int(metadata.get("bytes") or 0),
            width=_int_or_none(metadata.get("width")), height=_int_or_none(metadata.get("height")),
            service_session=str(reply.get("service_session") or metadata.get("session") or ""),
            seq=_int_or_none((metadata.get("source_stamp") or {}).get("seq")),
            source_stamp=dict(metadata.get("source_stamp") or {}), metadata=dict(metadata),
            lifecycle_record=dict(reply.get("lifecycle_record") or {}),
            request_ns=request_ns, transfer_ns=transfer_ns)

    # -- the checks ---------------------------------------------------------------------------------------------------
    def _ask(self, request: dict) -> dict:
        try:
            reply = self._request(request, self.timeout_s)
        except Exception as error:  # noqa: BLE001
            raise CaptureRefused("service_unreachable", f"the camera service did not answer: {type(error).__name__}") from None
        if not isinstance(reply, dict):
            raise CaptureRefused("malformed_reply", "the service's answer was not a JSON object")
        if not reply.get("ok"):
            # The service's OWN reason is kept. Rewriting it here would lose the one thing that says what it refused.
            raise CaptureRefused(str(reply.get("reason") or "capture_refused"), str(reply.get("detail") or "the service refused the request"))
        return reply

    def _check_properties(self, role: str, metadata: dict) -> None:
        """The properties the controller's perception was built against. A service configured for something else is
        refused, rather than feeding smaller frames into a solver calibrated for larger ones."""
        if metadata.get("role") not in (None, role):
            self.captures_refused += 1
            raise CaptureRefused("wrong_role_in_reply", "the service answered with a different camera than the one asked for")
        width, height = _int_or_none(metadata.get("width")), _int_or_none(metadata.get("height"))
        if width is not None and height is not None and (width, height) != (self.width, self.height):
            self.captures_refused += 1
            raise CaptureRefused("frame_properties_not_as_required",
                                 f"the frame is {width}x{height} and this controller's perception needs {self.width}x{self.height}")
        if metadata.get("encoding") not in (None, "png"):
            self.captures_refused += 1
            raise CaptureRefused("frame_encoding_not_as_required", "a mission frame is a lossless png")

    def _verify_bytes(self, destination: Path, metadata: dict) -> None:
        """THE FRAME THAT WAS JUST TAKEN, PROVEN. A digest that does not match means the bytes here are not the bytes
        the service says it wrote, whatever the reason: a stale file, a truncated copy, a name that collided."""
        declared = metadata.get("sha256")
        data = destination.read_bytes()
        actual = hashlib.sha256(data).hexdigest()
        expected_bytes = _int_or_none(metadata.get("bytes"))
        if expected_bytes is not None and len(data) != expected_bytes:
            self.captures_refused += 1
            destination.unlink(missing_ok=True)
            raise CaptureRefused("frame_size_mismatch", "the copied frame is not the size the service stated")
        if isinstance(declared, str) and declared and actual != declared:
            self.captures_refused += 1
            destination.unlink(missing_ok=True)
            raise CaptureRefused("frame_digest_mismatch", "the copied frame is not the frame the service says it took")


def _int_or_none(value) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def scp_fetch(ssh_target: str, remote_root: str, *, options=("-q",), timeout_s: float = 60.0) -> Callable[[str, Path], None]:
    """A `fetch` that copies one named frame back over scp. The remote path is built from the service's OWN relative
    path under a root this caller names, so nothing in a reply can point the copy at an arbitrary file."""
    def fetch(relative: str, destination: Path) -> None:
        if relative.startswith("/") or ".." in Path(relative).parts:
            raise ValueError("a frame path is relative and never climbs out of the store")
        subprocess.run(["scp", *options, f"{ssh_target}:{remote_root}/{relative}", str(destination)],
                       check=True, timeout=timeout_s, capture_output=True)
    return fetch


def zmq_request(endpoint: str) -> Callable[[dict, float], dict]:
    """A `request` that speaks one JSON request/reply on the service's REP socket, with a NEW socket per exchange.

    A REQ socket that has timed out is in a state where the next send is invalid, and reusing it would turn one slow
    capture into every later capture failing. One socket per exchange costs a connect and keeps the failure local.
    """
    import zmq  # imported here: nothing that merely imports this module needs zmq

    def request(payload: dict, timeout_s: float) -> dict:
        context = zmq.Context.instance()
        socket = context.socket(zmq.REQ)
        try:
            socket.setsockopt(zmq.LINGER, 0)
            socket.setsockopt(zmq.RCVTIMEO, int(timeout_s * 1000))
            socket.setsockopt(zmq.SNDTIMEO, int(timeout_s * 1000))
            socket.connect(endpoint)
            socket.send_json(payload)
            return socket.recv_json()
        finally:
            socket.close(0)
    return request


def endpoint_for(port: int = DEFAULT_MISSION_PORT) -> str:
    """Always loopback: the mission socket is reached through the forward this laptop opened, never over a network."""
    return f"tcp://127.0.0.1:{int(port)}"


def load_metadata(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))
