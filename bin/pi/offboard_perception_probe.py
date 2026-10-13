#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""offboard_perception_probe: the desktop perception, proven WITHOUT MOTION, from the robot.

WHAT IT PROVES, ON THE ROBOT. A frame the robot's own camera service captured is sent to the desktop perception service
through the reverse forward, the real desktop perception runs on it, and the answer is read back ON THE ROBOT and put
through the same acceptance the controller uses (autonomy/perception/remote_perception.py accept()). Then the ways an
answer must NOT be used are exercised for real, each against its expected refusal:

  normal   N captures, each perceived and accepted; the FULL round trip on the robot's clock (capture, send, the
           desktop's work, accept, the file writes) with p50, p90 and max, and the desktop's own perceive time reported
           separately, labelled as the desktop's clock. The cube listing is run on the robot on each accepted answer, and
           whether its pass 1 reproduces the desktop's record is reported (cube_candidates: never measured in this
           direction before).
  replay   an old accepted answer offered for a new capture: refused on the request id; the same answer with the new
           request id pasted in: refused on the capture identity.
  missing  a port nobody listens on: refused at the deadline, nothing written.
  timeout  the deadline set below the desktop's perceive time: refused at the deadline, nothing written.
  late     the same short deadline, but the expired socket is kept and drained until the answer comes: the answer is put
           through accept() and refused as late, nothing written.
  stop     a SIGINT to this process two seconds (--stop-after-s) into an outstanding request: the request is abandoned
           and the call returns (signal to return under STOP_BOUND_S), and the next request, once the service is free,
           is accepted.

WHAT IT NEVER DOES. It opens no host, no serial device and no lease, constructs no robot client and sends no command.
The camera is asked through the camera service, which owns it (the same client ServiceCamera builds with local=True,
used directly so that the service's session and sequence number are available for the capture identity). Before
anything, and before and after every case, the robot's resident is read (demo_runtime.robot_link.read_binding): the
probe REFUSES unless the resident answers as the pinned build and reports the supervised host HELD (host_running false),
and it reports whether the resident session stayed the same throughout. The service's port must have a listener on
loopback only, read from the kernel (ss): the robot's sshd GatewayPorts setting decides where a reverse forward binds,
and it is not known from here.

--frames-from DIR is the laptop DRY RUN: frames are files, not robot captures, and every record says so
(robot_originated false). No resident is read in a dry run.

Exit status: 0 every case gave its expected verdict; 1 a case did not; 3 refused before the first case; 5 usage.
Evidence (captures, the answers' records and overlays, one exchange file per request, probe-result.json and
service-rows.json in the benchmark harness's row format) goes to --evidence-dir, which must be new or empty.
"""
import argparse
import json
import os
import signal
import socket
import statistics
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for _sub in ("autonomy/perception", "autonomy/manipulation", "bin/laptop/perception", "tools", "packages/demo-runtime"):
    if str(ROOT / _sub) not in sys.path:
        sys.path.insert(0, str(ROOT / _sub))

import remote_perception as rp  # noqa: E402

CASES = ("normal", "replay", "missing", "timeout", "late", "stop")
STOP_AFTER_S = 2.0
STOP_BOUND_S = 0.25  # the brief's bound on signal-to-return
LOOPBACK_ADDRESSES = ("127.0.0.1", "[::1]", "::1")


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def percentile(values, q):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))] if ordered else None


def listeners(port: int, run=subprocess.run) -> dict:
    """The kernel's TCP listeners on `port` (ss -H -ltnp): {"addresses": [...], "processes": [...], "readable": bool}. The
    process column is filled only for this account's processes; it is evidence, not a verdict."""
    try:
        table = run(["ss", "-H", "-ltnp"], capture_output=True, text=True, timeout=5, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return {"readable": False, "addresses": [], "processes": []}
    addresses, processes = [], []
    for line in table.splitlines():
        fields = line.split()
        if len(fields) < 4:
            continue
        address, _, bound = fields[3].rpartition(":")
        if bound == str(port):
            addresses.append(address)
            processes.append(fields[5].split('"')[1] if len(fields) > 5 and '"' in fields[5] else None)
    return {"readable": True, "addresses": addresses, "processes": processes}


def free_loopback_port() -> int:
    with socket.socket() as holder:
        holder.bind(("127.0.0.1", 0))
        return int(holder.getsockname()[1])


class Frames:
    """Where the frames come from: the camera service on the robot, or files in a dry run. Each frame is
    {path, name, robot_originated, capture (session, seq), capture_ns}."""

    def __init__(self, args, evidence: Path):
        self.args, self.evidence, self.count = args, evidence, 0
        self.files = sorted(Path(args.frames_from).glob("*.png")) if args.frames_from else None
        self.client = None
        if self.files is None:
            from mission_capture_client import MissionCaptureClient, endpoint_for, zmq_request
            from run_stage1_scan import MISSION_REMOTE_ROOT, local_fetch

            store = Path(args.store_root) if args.store_root else Path.home() / MISSION_REMOTE_ROOT
            self.client = MissionCaptureClient(request=zmq_request(endpoint_for(args.mission_port)), fetch=local_fetch(store), local_dir=evidence / "captures")
            self.client.ping()  # a socket that answers as anything else than the camera service refuses here

    def next(self) -> dict:
        self.count += 1
        if self.files is not None:
            if not self.files:
                raise RuntimeError("the dry-run frame directory holds no png")
            path = self.files[(self.count - 1) % len(self.files)]
            return {"path": path, "name": path.stem if self.count <= len(self.files) else f"{path.stem}-r{self.count}", "robot_originated": False,
                    "capture": {"camera_service_session": None, "seq": None}, "capture_ns": 0, "source": path.name}
        started = time.monotonic_ns()
        frame = self.client.capture("wrist")
        return {"path": frame.path, "name": f"probe-{self.count:03d}-{frame.sha256[:12]}", "robot_originated": True,
                "capture": {"camera_service_session": frame.service_session or None, "seq": frame.seq}, "capture_ns": time.monotonic_ns() - started,
                "source": frame.path.name}


class Probe:
    def __init__(self, args):
        self.args = args
        self.evidence = Path(args.evidence_dir)
        self.result = {"schema": "offboard-perception-probe/1", "started_utc": utc_now(), "dry_run": bool(args.frames_from), "cases": {}, "bindings": [],
                       "port": args.port, "deadline_s": args.deadline_s, "short_deadline_s": args.short_deadline_s}
        self.stop_flag = {"set": False, "handler_ns": None}

    # -- the robot's resident: read, never changed -----------------------------------------------------------------------
    def binding(self, when: str):
        if self.args.frames_from:
            return None
        from demo_runtime import robot_link

        binding = robot_link.read_binding(self.args.admin_probe, self.args.admin_socket, expected_build_identity=self.args.expected_build_identity)
        entry = {"when": when, "session_uuid": binding.get("session_uuid"), "host_running": binding.get("host_running"), "reason_code": binding.get("reason_code"),
                 "observed_wall_utc": binding.get("observed_wall_utc")}
        self.result["bindings"].append(entry)
        return entry

    def held(self, entry) -> bool:
        return entry is None or (entry["session_uuid"] is not None and entry["host_running"] is False)

    # -- clients ----------------------------------------------------------------------------------------------------------
    def client(self, deadline_s: float, port=None):
        client = rp.RemotePerception(port=port or self.args.port, deadline_s=deadline_s, expect=self.expect, robot=self.robot,
                                     should_cancel=lambda: self.stop_flag["set"])
        client.detections = self.detections  # one proposal store across the probe's clients, as one controller has
        return client

    def wait_until_free(self) -> dict:
        """A ping, waited for as long as the drain bound: it queues behind whatever the service is still computing, so its
        answer says the service is free. Not a case: its time is recorded, never judged."""
        started = time.monotonic_ns()
        self.client(self.args.late_drain_s).ping()
        return {"waited_ns": time.monotonic_ns() - started}

    def out_dir(self, case: str) -> Path:
        path = self.evidence / case
        path.mkdir(parents=True, exist_ok=True)
        return path

    # -- the cases --------------------------------------------------------------------------------------------------------
    def case_normal(self) -> dict:
        import cv2
        import cube_candidates

        rows, out = [], self.out_dir("normal")
        count = self.args.frames or (len(self.frames.files) if self.frames.files is not None else 5)
        client = self.client(self.args.deadline_s)
        for _ in range(count):
            frame = self.frames.next()
            started = time.monotonic_ns()
            try:
                summary = client.perceive(frame["path"], out, frame["name"], capture=frame["capture"])
            except rp.RemotePerceptionRefused as refusal:
                rows.append({"name": frame["name"], "accepted": False, "refusal": refusal.reason_code, "service_reason": refusal.service_reason})
                continue
            perceived_ns = time.monotonic_ns() - started
            exchange = json.loads((out / f"{frame['name']}{rp.EXCHANGE_SUFFIX}").read_text())
            listed_started = time.monotonic_ns()
            listing = cube_candidates.enumerate_observation(cv2.imread(str(frame["path"])), client.detections, out, frame["name"])
            desktop = exchange["reply"].get("timing") or {}
            rows.append({"name": frame["name"], "source": frame["source"], "robot_originated": frame["robot_originated"], "accepted": True,
                         "capture_ns": frame["capture_ns"], "round_trip_ns": exchange["timing"]["round_trip_ns"], "write_ns": exchange["timing"].get("write_ns"),
                         "full_ns": frame["capture_ns"] + perceived_ns, "listing_ns": time.monotonic_ns() - listed_started,
                         "desktop_perceive_ns": desktop.get("perceive_ns"), "desktop_queue_ns": desktop.get("queue_ns"), "summary": summary,
                         "listing_refused": listing.get("refused"), "listing_pass1_matches_record": listing.get("pass1_matches_record"),
                         "cubes_listed": len(listing.get("cubes") or [])})
        accepted = [row for row in rows if row["accepted"]]

        def stats(key):
            values = [row[key] for row in accepted if isinstance(row.get(key), int)]
            return {"n": len(values), "p50_ms": None if not values else statistics.median(values) / 1e6, "p90_ms": None if not values else percentile(values, 0.9) / 1e6,
                    "max_ms": None if not values else max(values) / 1e6}

        return {"expected": "accepted", "got": f"{len(accepted)} of {len(rows)} accepted", "ok": bool(rows) and len(accepted) == len(rows), "frames": rows,
                "full_round_trip_robot_clock": stats("full_ns"),
                "send_to_accept_robot_clock": stats("round_trip_ns"), "desktop_perceive_desktop_clock": stats("desktop_perceive_ns"),
                "listing_pass1_all_match": all(row["listing_pass1_matches_record"] for row in accepted) if accepted else None}

    def case_replay(self) -> dict:
        import cv2
        from cube_candidates import frame_digest

        client = self.client(self.args.deadline_s)

        def outstanding_for(frame, request_id=None):
            payload = Path(frame["path"]).read_bytes()
            capture = {"observation_id": frame["name"], "role": "wrist", "camera_service_session": None, "seq": None, **frame["capture"],
                       "file_sha256": rp.sha256_hex(payload), "frame_digest": frame_digest(cv2.imread(str(frame["path"])))}
            header = rp.new_request(rp.OP_PERCEIVE, robot=self.robot, expect=self.expect, budget_ms=int(self.args.deadline_s * 1000), capture=capture, request_id=request_id)
            return header, payload, capture

        old = self.frames.next()
        header, payload, capture = outstanding_for(old)
        sock, sent = client.send(header, payload, file_sha256=capture["file_sha256"], frame_digest=capture["frame_digest"])
        try:
            parts = client.wait(sock, sent, cancellable=False)
        finally:
            sock.close(0)
        if parts is None:
            return {"expected": ["request_id_mismatch", "capture_mismatch"], "ok": False, "detail": "the first answer did not come within the deadline"}
        first = rp.accept(parts, sent, sent.received_ns)  # the genuine answer to its own request: accepted
        new = self.frames.next()
        header_b, _payload_b, capture_b = outstanding_for(new)
        now = time.monotonic_ns()
        fresh = rp.Outstanding(header=header_b, sent_ns=now, deadline_ns=now + int(self.args.deadline_s * 1e9), expect=self.expect,
                               file_sha256=capture_b["file_sha256"], frame_digest=capture_b["frame_digest"])
        verdicts = []
        forged = json.loads(bytes(parts[0]).decode("ascii"))
        forged["request_id"] = header_b["request_id"]
        for offered in (list(parts), [rp.canonical(forged)] + list(parts[1:])):
            try:
                rp.accept(offered, fresh, time.monotonic_ns())
                verdicts.append("accepted")
            except rp.RemotePerceptionRefused as refusal:
                verdicts.append(refusal.reason_code)
        return {"expected": ["request_id_mismatch", "capture_mismatch"], "got": verdicts, "ok": verdicts == ["request_id_mismatch", "capture_mismatch"],
                "first_answer_accepted": bool(first.record is not None), "old_observation": old["name"], "new_observation": new["name"]}

    def refused_case(self, case: str, client, frame, expected: str) -> dict:
        out = self.out_dir(case)
        started = time.monotonic_ns()
        try:
            client.perceive(frame["path"], out, frame["name"], capture=frame["capture"])
            got = "accepted"
        except rp.RemotePerceptionRefused as refusal:
            got = refusal.reason_code
        written = sorted(path.name for path in out.iterdir() if not path.name.endswith(rp.EXCHANGE_SUFFIX))
        return {"expected": expected, "got": got, "ok": got == expected and not written, "results_written": written, "returned_after_ns": time.monotonic_ns() - started}

    def case_missing(self) -> dict:
        port = free_loopback_port()  # nothing listens here: the service is, for this client, down
        return {**self.refused_case("missing", self.client(self.args.short_deadline_s, port=port), self.frames.next(), rp.DEADLINE), "port_nobody_listens_on": port}

    def case_timeout(self) -> dict:
        return self.refused_case("timeout", self.client(self.args.short_deadline_s), self.frames.next(), rp.DEADLINE)

    def case_late(self) -> dict:
        import cv2
        from cube_candidates import frame_digest

        out, frame = self.out_dir("late"), self.frames.next()
        client = self.client(self.args.short_deadline_s)
        payload = Path(frame["path"]).read_bytes()
        capture = {"observation_id": frame["name"], "role": "wrist", "camera_service_session": None, "seq": None, **frame["capture"],
                   "file_sha256": rp.sha256_hex(payload), "frame_digest": frame_digest(cv2.imread(str(frame["path"])))}
        header = rp.new_request(rp.OP_PERCEIVE, robot=self.robot, expect=self.expect, budget_ms=int(self.args.short_deadline_s * 1000) or 1, capture=capture)
        sock, sent = client.send(header, payload, file_sha256=capture["file_sha256"], frame_digest=capture["frame_digest"])
        try:
            before_deadline = client.wait(sock, sent, cancellable=False)
            drained = None if before_deadline is not None else client.wait(sock, sent, until_ns=time.monotonic_ns() + int(self.args.late_drain_s * 1e9), cancellable=False)
        finally:
            sock.close(0)
        if before_deadline is not None:
            return {"expected": rp.LATE, "got": "answered_before_the_deadline", "ok": False, "detail": "the short deadline was longer than the desktop's work"}
        if drained is None:
            return {"expected": rp.LATE, "got": "no_answer_within_the_drain", "ok": False}
        try:
            rp.accept(drained, sent, sent.received_ns)
            got = "accepted"
        except rp.RemotePerceptionRefused as refusal:
            got = refusal.reason_code
        written = sorted(path.name for path in out.iterdir())
        return {"expected": rp.LATE, "got": got, "ok": got == rp.LATE and not written, "results_written": written,
                "answer_after_deadline_ms": (sent.received_ns - sent.deadline_ns) / 1e6}

    def case_stop(self) -> dict:
        out, frame = self.out_dir("stop"), self.frames.next()
        client = self.client(self.args.deadline_s)
        previous = signal.getsignal(signal.SIGINT)
        sent_at = {}

        def handler(*_):  # as trivial as the controller's: two dict writes, the first stamp kept
            self.stop_flag["handler_ns"] = self.stop_flag["handler_ns"] or time.monotonic_ns()
            self.stop_flag["set"] = True

        def interrupt():
            sent_at["ns"] = time.monotonic_ns()
            os.kill(os.getpid(), signal.SIGINT)

        signal.signal(signal.SIGINT, handler)
        timer = threading.Timer(self.args.stop_after_s, interrupt)
        got = None
        try:
            timer.start()
            try:
                client.perceive(frame["path"], out, frame["name"], capture=frame["capture"])
                got = "accepted"
            except rp.RemotePerceptionCancelled:
                got = "cancelled"
            except rp.RemotePerceptionRefused as refusal:
                got = refusal.reason_code
            returned_ns = time.monotonic_ns()
        finally:
            timer.cancel()
            signal.signal(signal.SIGINT, previous)
        handler_ns, signalled_ns = self.stop_flag["handler_ns"], sent_at.get("ns")
        self.stop_flag.update(set=False, handler_ns=None)
        latency = {"signal_to_return_ms": None if signalled_ns is None else (returned_ns - signalled_ns) / 1e6,
                   "handler_to_return_ms": None if handler_ns is None else (returned_ns - handler_ns) / 1e6}
        written = sorted(path.name for path in out.iterdir() if not path.name.endswith(rp.EXCHANGE_SUFFIX))
        free = self.wait_until_free()
        after = self.frames.next()
        try:
            self.client(self.args.deadline_s).perceive(after["path"], self.out_dir("stop-next"), after["name"], capture=after["capture"])
            next_verdict = "accepted"
        except rp.RemotePerceptionRefused as refusal:
            next_verdict = refusal.reason_code
        ok = (got == "cancelled" and not written and latency["signal_to_return_ms"] is not None and latency["signal_to_return_ms"] < STOP_BOUND_S * 1000
              and next_verdict == "accepted")
        return {"expected": "cancelled, then the next request accepted", "got": got, "ok": ok, **latency, "bound_ms": STOP_BOUND_S * 1000,
                "results_written": written, "service_free_after_ms": free["waited_ns"] / 1e6, "next_request": next_verdict}

    # -- the run ----------------------------------------------------------------------------------------------------------
    def refuse(self, text: str) -> int:
        self.result["refused"] = text
        self.write()
        print(f"REFUSED before any case: {text}", flush=True)
        return 3

    def write(self) -> None:
        self.result["finished_utc"] = utc_now()
        (self.evidence / "probe-result.json").write_text(json.dumps(self.result, indent=1, sort_keys=True, default=str))

    def run(self) -> int:
        self.evidence.mkdir(mode=0o700, parents=True, exist_ok=True)
        if any(self.evidence.iterdir()):
            print("REFUSED: the evidence directory is not empty", flush=True)
            return 5
        entry = self.binding("before_any_case")
        if not self.held(entry):
            return self.refuse(f"the robot's resident does not report a held host under the pinned build ({entry['reason_code'] or 'host_running ' + str(entry['host_running'])})")
        seen = listeners(self.args.port)
        self.result["listener"] = seen
        if not seen["readable"]:
            return self.refuse("the kernel's listener table could not be read")
        if not seen["addresses"]:
            return self.refuse("nothing listens on the service port: the reverse forward is not up")
        if any(address not in LOOPBACK_ADDRESSES for address in seen["addresses"]):
            return self.refuse("the service port listens beyond loopback: the robot's sshd bound the reverse forward on a network address")
        try:
            model = json.loads(Path(self.args.model_pin).read_text())
            model = model.get("model", model)
            self.expect = rp.robot_identity(ROOT, Path(self.args.template_dir) if self.args.template_dir else None, model_revision=model["revision"],
                                            model_files_digest_value=rp.model_files_digest(model["files_sha256"]))
        except (OSError, ValueError, KeyError, TypeError, rp.RemotePerceptionRefused) as error:
            return self.refuse(f"this robot's expected identity cannot be formed ({getattr(error, 'reason_code', type(error).__name__)})")
        run_id = f"offboard-probe-{utc_now()}-{uuid.uuid4().hex[:6]}"
        self.robot = {"resident_session": None if entry is None else entry["session_uuid"], "run_id": run_id, "mission_id": None, "target_id": None,
                      "scan_id": run_id, "controller_pid": os.getpid()}
        self.detections = rp.RemoteCubeDetections()
        self.result.update(run_id=run_id, expect=self.expect.to_wire(), robot=self.robot)
        try:
            self.result["service"] = self.client(self.args.deadline_s).ping()
        except rp.RemotePerceptionRefused as refusal:
            return self.refuse(f"the service did not answer as this robot's service ({refusal.reason_code}{', ' + refusal.service_reason if refusal.service_reason else ''})")
        try:
            self.frames = Frames(self.args, self.evidence)
        except Exception as error:  # noqa: BLE001 -- the camera service's own reason code when it has one
            return self.refuse(f"no frame source ({getattr(error, 'reason_code', type(error).__name__)})")
        failed = 0
        for case in self.args.cases:
            entry = self.binding(f"before_{case}")
            if not self.held(entry):
                self.result["cases"][case] = {"ok": False, "detail": "the host was not held at the read before this case; the case was not run"}
                failed += 1
                break
            try:
                outcome = getattr(self, f"case_{case}")()
            except (rp.RemotePerceptionRefused, rp.RemotePerceptionCancelled, OSError, RuntimeError) as error:
                outcome = {"ok": False, "detail": f"the case ended early ({getattr(error, 'reason_code', type(error).__name__)})"}
            self.result["cases"][case] = outcome
            failed += 0 if outcome.get("ok") else 1
            print(f"case {case}: {'as expected' if outcome.get('ok') else 'NOT as expected'} (expected {outcome.get('expected')}, got {outcome.get('got', '-')})", flush=True)
            if case in ("timeout", "late", "stop", "normal", "replay"):
                try:
                    self.result["cases"][case]["service_free_after_ms"] = self.wait_until_free()["waited_ns"] / 1e6
                except rp.RemotePerceptionRefused as refusal:
                    self.result["cases"][case]["service_free_after"] = refusal.reason_code
        last = self.binding("after_the_last_case")
        sessions = {item["session_uuid"] for item in self.result["bindings"]}
        self.result["host_held_at_every_read"] = all(self.held(item) for item in self.result["bindings"]) if self.result["bindings"] else None
        self.result["resident_session_unchanged"] = (len(sessions) == 1) if self.result["bindings"] else None
        if last is not None and not (self.result["host_held_at_every_read"] and self.result["resident_session_unchanged"]):
            failed += 1
        self.write_rows()
        self.write()
        normal = self.result["cases"].get("normal") or {}
        if normal.get("full_round_trip_robot_clock"):
            full, desk = normal["full_round_trip_robot_clock"], normal["desktop_perceive_desktop_clock"]
            print(f"full round trip (robot clock): p50 {full['p50_ms']} ms, p90 {full['p90_ms']} ms, max {full['max_ms']} ms over {full['n']}; "
                  f"desktop perceive (desktop clock): p50 {desk['p50_ms']} ms, max {desk['max_ms']} ms", flush=True)
        print(f"probe: {len(self.args.cases) - failed} of {len(self.args.cases)} cases as expected; host held at every read: {self.result['host_held_at_every_read']}; "
              f"resident session unchanged: {self.result['resident_session_unchanged']}; evidence written", flush=True)
        return 0 if failed == 0 else 1

    def write_rows(self) -> None:
        """The normal case's answers as benchmark harness rows ({frame, detections, record, summary}), so the harness's
        compare.py can set the service's results beside the robot benchmark's."""
        rows = []
        for row in (self.result["cases"].get("normal") or {}).get("frames") or []:
            if not row.get("accepted"):
                continue
            base = self.evidence / "normal" / row["name"]
            exchange = json.loads(Path(f"{base}{rp.EXCHANGE_SUFFIX}").read_text())
            rows.append({"frame": row["source"], "detections": (exchange["reply"].get("result") or {}).get("detections") or [],
                         "record": json.loads(Path(f"{base}.json").read_text()), "summary": row["summary"]})
        service = self.result.get("service") or {}
        (self.evidence / "service-rows.json").write_text(json.dumps({"harness": "offboard_perception_probe", "machine": "desktop-service", "device": service.get("device"),
                                                                     "versions": service.get("versions") or {"torch": None}, "torch_threads": None, "passes": [rows]},
                                                                    indent=1, default=float))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="offboard_perception_probe.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, required=True, help="the loopback port the service answers on (on the robot: the reverse forward's)")
    parser.add_argument("--deadline-s", type=float, required=True, help="the deadline of the normal, replay and stop requests")
    parser.add_argument("--short-deadline-s", type=float, required=True, help="a deadline BELOW the desktop's perceive time (timeout, late, missing)")
    parser.add_argument("--late-drain-s", type=float, default=180.0, help="how long an expired socket is drained for its late answer")
    parser.add_argument("--stop-after-s", type=float, default=STOP_AFTER_S, help="when the stop case's SIGINT is sent, into its outstanding request")
    parser.add_argument("--model-pin", required=True, help="json with the pinned model revision and files_sha256 (or the harness's expect.json)")
    parser.add_argument("--template-dir", default=None, help="the pad template this robot's controller would read (absent: its digest says absent)")
    parser.add_argument("--evidence-dir", required=True)
    parser.add_argument("--cases", default=",".join(CASES), help=f"comma separated, in order, from {', '.join(CASES)}")
    parser.add_argument("--frames", type=int, default=None, help="captures in the normal case (dry run: every file once)")
    parser.add_argument("--frames-from", default=None, help="DRY RUN: frames from this directory of png files; no resident is read")
    parser.add_argument("--admin-probe", default=None)
    parser.add_argument("--admin-socket", default=None)
    parser.add_argument("--expected-build-identity", default=None)
    parser.add_argument("--mission-port", type=int, default=5570)
    parser.add_argument("--store-root", default=None, help="the camera service's store (default: its own, under the home directory)")
    args = parser.parse_args(argv)
    args.cases = [case for case in args.cases.split(",") if case]
    if any(case not in CASES for case in args.cases):
        parser.error(f"--cases takes {', '.join(CASES)}")
    if not args.frames_from and not (args.admin_probe and args.admin_socket and args.expected_build_identity):
        parser.error("on the robot the resident is read: --admin-probe, --admin-socket and --expected-build-identity are required")
    if not 0 < args.short_deadline_s < args.deadline_s:
        parser.error("--short-deadline-s is positive and below --deadline-s")
    return Probe(args).run()


if __name__ == "__main__":
    sys.exit(main())
