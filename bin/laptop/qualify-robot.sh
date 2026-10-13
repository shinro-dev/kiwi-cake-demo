#!/usr/bin/env bash
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
#
# bin/laptop/qualify-robot.sh: the ONE attended hardware qualification session. Runs on the laptop. It runs steps 0 to 4
# in order, stops at the first FAIL, prints a numbered transcript, and writes every raw number it measured to a
# directory outside this checkout. The runbook that goes with it, and the pass criteria in prose, are
# docs/autonomous-demo/hardware-qualification.md. Read that first; this script is its driver, not its explanation.
#
# THIS SCRIPT NEVER ENABLES TORQUE AND NEVER COMMANDS A MOTOR. Steps 0 to 4 involve no motion. The only things it writes
# anywhere are: files under its own output directory and under two directories of the robot computer's home; the camera
# service it starts and stops; and, in step 2, six deliberately INVALID command messages whose whole purpose is to be
# counted as rejected. None of those six carries a joint position or a wheel velocity, and none is sent until the
# boundary's own tap has been seen publishing, which only happens when the boundary is installed.
#
# THE SOURCE VERIFICATION OF STEP 0 RUNS HERE, not on the robot computer, and it is the one thing in this script that
# needs nothing deployed. bin/pi/host_boundary.py --verify-robot and --verify-stock-host read TEXT: they import nothing,
# open no bus and move nothing, so they do not have to run where the files live. The robot computer is only READ, in ssh
# calls that write nothing and leave nothing behind: one asks the interpreter the host runs with where lerobot is
# installed, and one per file copies that file's text back here with cat. This laptop's own python3 then reads the
# copies with this checkout's own host_boundary.py. The deployment below (deploy_qual) is a separate, older thing: it
# puts the camera probe and its modules on the robot computer for step 1, which really does have to run there.
#
# WHAT A SOURCE PASS PROVES, AND WHAT IT DOES NOT. It proves that the lerobot source installed on the robot computer is
# the source this repository expects. IT IS NOT A STATEMENT THAT THE MOTOR HOST WILL ATTACH. Attachment is decided
# afterwards, by bin/pi/lekiwi_host_safe_attach.py, against the LIVE REGISTERS of the arm: it refuses when a joint's
# torque is not ON, and when a joint's goal and present positions disagree by more than about one degree, which is
# exactly the state a power cycle leaves behind, because it drops torque and resets every goal while the arm stays
# where it physically is. A step 0 that passes and an attach that refuses are both ordinary and can be true at once.
#
#   bin/laptop/qualify-robot.sh                      the whole session
#   bin/laptop/qualify-robot.sh --skip 0,1           resume a session whose first two steps passed
#   bin/laptop/qualify-robot.sh --skip 0,1,2,3       step 4 alone
#
# Options: --out-dir DIR (default: .local-dev/qualification/<UTC timestamp> under this checkout, which git ignores),
# --window-s N (the bounded measurement windows, default 60; camera-service.md asks for 600 in a real session),
# --gap-s N (how long the tap subscriber stays away in step 3, default 20), --rate-tolerance-pct N (default 10),
# --port-offset N (added to every forwarded local port when one of them is already in use here), --yes (do not ask the
# operator to confirm step 2; only for a repeat of a step that passed earlier in the same session).
#
# THE STEPS, and what the operator does between them:
#   0  read-only preconditions: reachable, the host's interpreter, pyzmq and libzmq, the read-only check of the
#      INSTALLED lerobot source (made HERE, over copies of those files, see below), no camera owner, no camera lock
#      held, nothing running.
#   1  camera ownership: the legacy probe works, the service takes the wrist lock, the probe is then REFUSED, the
#      service stops, the probe works again. No motion, no motor host needed.
#   2  command rejection: the operator starts the motor host WITH the boundary in another terminal
#      (bin/pi/start-host.sh boundary); this session proves from the tap that a command without lease metadata is
#      rejected and dispatches nothing, and that the host keeps running and its dispatch clock is not fed.
#   3  observation freshness: the tap's source session, sequence advance, measured rate and gap counters over bounded
#      windows with the camera stream off and on, and the host loop rate with a tap subscriber attached and with none.
#   4  camera cadence and cost: the numbers docs/autonomous-demo/camera-service.md asks for, measured with the preview
#      subscribed, mission captures in flight and the tap watched at the same time.
# Steps 2, 3 and 4 need the motor host running WITH the boundary; steps 0 and 1 need it NOT running.
#
# Every identity comes from the environment, never from this file and never from a default:
#   KC_PI_SSH            the ssh target of the robot computer (key-based login; BatchMode never asks for a password)
#   KC_PI_IP             the address the LeKiwi client connects to (step 2 pushes its rejected probes at that port)
#   KC_WRIST_CAMERA_ID   the stable identity of the wrist camera on the robot computer
# Optional: KC_PI_PYTHON, an absolute path to the python of the lerobot install on the robot computer.
# No identity is printed, written to the transcript or written to any number file, and none is put in an argument
# vector. The front camera is never enabled here.
# Exit status: 0 every step that ran passed, 1 a step FAILED, 4 a file copied to the robot computer does not match,
# 5 usage or a missing/unsafe identity, 7 a tool this script needs is missing here.
set -euo pipefail

usage() {
  cat >&2 <<'USAGE'
usage: qualify-robot.sh [--skip LIST] [--out-dir DIR] [--window-s N] [--gap-s N]
                        [--rate-tolerance-pct N] [--port-offset N] [--yes]
  --skip LIST   comma separated step numbers to skip, for resuming a session (example: --skip 0,1)
USAGE
  exit 5
}

SKIP=""
OUT_ARG=""
WINDOW_S=60
GAP_S=20
RATE_TOL=10
PORT_OFFSET=0
ASSUME_YES=0
PROBE_STATUS=0
number() { case "$1" in '' | *[!0-9]*) return 1 ;; *) return 0 ;; esac; }
while [ "$#" -gt 0 ]; do
  case "$1" in
    --skip) [ -n "${2:-}" ] || usage; SKIP=",$2,"; shift 2 ;;
    --out-dir) [ -n "${2:-}" ] || usage; OUT_ARG="$2"; shift 2 ;;
    --window-s) number "${2:-}" || usage; WINDOW_S="$2"; shift 2 ;;
    --gap-s) number "${2:-}" || usage; GAP_S="$2"; shift 2 ;;
    --rate-tolerance-pct) number "${2:-}" || usage; RATE_TOL="$2"; shift 2 ;;
    --port-offset) number "${2:-}" || usage; PORT_OFFSET="$2"; shift 2 ;;
    --yes) ASSUME_YES=1; shift ;;
    -h | --help) usage ;;
    *) usage ;;
  esac
done
[ "$WINDOW_S" -ge 10 ] || { echo "REFUSE: --window-s must be at least 10" >&2; exit 5; }
[ "$GAP_S" -ge 5 ] || { echo "REFUSE: --gap-s must be at least 5" >&2; exit 5; }

# Fail closed on identities: every missing name is reported at once and no value is ever printed.
MISSING=0
need() {
  local name="$1" what="$2"
  if [ -z "${!name:-}" ]; then echo "REFUSE: $name is not set ($what). Set it in your shell from your private, uncommitted runtime configuration." >&2; MISSING=1; return 0; fi
  case "${!name}" in
    -* | *[!A-Za-z0-9_./:@+=-]*) echo "REFUSE: $name starts with '-' or holds a character outside A-Za-z0-9_./:@+=- ; it is used inside a remote command line and is not escaped." >&2; MISSING=1 ;;
  esac
}
need KC_PI_SSH "the ssh target of the robot computer"
need KC_PI_IP "the address the LeKiwi client connects to"
need KC_WRIST_CAMERA_ID "the stable identity of the wrist camera on the robot computer"
[ -z "${KC_PI_PYTHON:-}" ] || need KC_PI_PYTHON "the python of the lerobot install on the robot computer"
[ "$MISSING" -eq 0 ] || exit 5
case "${KC_PI_PYTHON:-/}" in /*) ;; *) echo "REFUSE: KC_PI_PYTHON must be an absolute path on the robot computer." >&2; exit 5 ;; esac
command -v python3 >/dev/null 2>&1 || { echo "REFUSE: python3 is not on PATH here; this laptop makes the measurements" >&2; exit 7; }

# The repository root comes from where this script is, never from a fixed checkout path.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
# Resolved ONCE to an absolute path, as bin/laptop/run-cycle.sh does: a relative path means "relative to where the
# operator stands", which is not where git -C would look for it.
OUT="$(realpath -m "${OUT_ARG:-$ROOT/.local-dev/qualification/$TS}")"
# These files hold real numbers from a real robot: inside this checkout they may only go where git ignores them.
case "$OUT/" in
  "$(realpath -m "$ROOT")"/*) git -C "$ROOT" check-ignore -q "$OUT" || { echo "REFUSE: the output directory is inside this checkout and git does not ignore it" >&2; exit 5; } ;;
esac
mkdir -p "$OUT"
TRANSCRIPT="$OUT/transcript.txt"
: >"$TRANSCRIPT"

SSH=(ssh -o BatchMode=yes -o ConnectTimeout=6 "$KC_PI_SSH")
if [ -n "${KC_PI_PYTHON:-}" ]; then REMOTE_PYTHON="'$KC_PI_PYTHON'"; else REMOTE_PYTHON="\"\$HOME/miniconda3/bin/python3\""; fi
REMOTE_DIR="kc-qualification/deploy"
QUAL_FILES=("bin/pi/host_boundary.py" "bin/pi/camera_multiplex_probe.py" "autonomy/perception/camera_cycle_probe.py"
            "autonomy/perception/camera_lifecycle.py" "autonomy/perception/camera_lock.py")
TAP_PORT=$((5558 + PORT_OFFSET))
LEASE_PORT=$((5557 + PORT_OFFSET))
MISSION_PORT=$((5570 + PORT_OFFSET))
PREVIEW_PORT=$((5571 + PORT_OFFSET))
FORWARD_PID=""
HELPER="$(mktemp "${TMPDIR:-/tmp}/kc-qualify-XXXXXX.py")"
SOURCE_DIR=""  # the directory of this laptop's own that holds the copies of the installed lerobot source, while step 0 reads them

cleanup() {
  [ -z "$FORWARD_PID" ] || kill "$FORWARD_PID" 2>/dev/null || true
  rm -f "$HELPER"
  [ -z "$SOURCE_DIR" ] || rm -rf "$SOURCE_DIR"
}
trap cleanup EXIT

say() { printf '%s\n' "$*"; printf '%s\n' "$*" >>"$TRANSCRIPT"; }
show() { tee -a "$TRANSCRIPT" <"$1"; }
STEP=""
N=0
begin() { STEP="$1"; N=0; say ""; say "=== step $1: $2"; }
item() { N=$((N + 1)); say "  $STEP.$N $*"; }
warn() { say "  WARNING: $*"; }
step_fail() { say "  step $STEP: FAIL: $*"; say ""; say "The session stops here: nothing after this step ran. The numbers are under $OUT"; exit 1; }
step_pass() { say "  step $STEP: PASS: $*"; }
skipped() { case "$SKIP" in *",$1,"*) return 0 ;; *) return 1 ;; esac; }

# ---------------------------------------------------------------------------------------------------------------------
# The measurement helper. One python program with several subcommands. It writes its JSON to the file named by
# KC_QUAL_JSON, prints human lines, and ends with exactly one 'VERDICT PASS' or 'VERDICT FAIL: <reason>' line. It is
# read-only with respect to the robot except for the deliberately invalid messages of reject-probe.
# ---------------------------------------------------------------------------------------------------------------------
cat >"$HELPER" <<'HELPER_PY'
"""Measurement helper of bin/laptop/qualify-robot.sh. Never imports the robot package, never commands a motor.
Host times and laptop times are kept in separate fields and are never subtracted from each other."""
import json
import os
import statistics
import sys
import time

OUT = os.environ.get("KC_QUAL_JSON")
TOL = float(os.environ.get("KC_QUAL_RATE_TOL_PCT", "10"))


def line(text):
    print("      " + text)


def emit(document, ok, reason=""):
    if OUT:
        with open(OUT, "w", encoding="utf-8") as handle:
            json.dump(document, handle, indent=2, sort_keys=True, default=str)
            handle.write("\n")
    print("VERDICT PASS" if ok else "VERDICT FAIL: " + reason)
    raise SystemExit(0 if ok else 1)


def clients():
    sys.path.insert(0, os.path.join(os.environ["KC_QUAL_REPO"], "packages", "demo-runtime"))
    try:
        from demo_runtime.preview_client import PreviewSubscriber
        from demo_runtime.tap_client import TapClient
    except Exception as error:  # noqa: BLE001 -- the reason matters more than the type
        print("VERDICT FAIL: the laptop tap and preview clients could not be imported: %s" % error)
        raise SystemExit(1) from None
    return TapClient, PreviewSubscriber


def rate(samples):
    """(loop rate in Hz, span in s) from the HOST's own stamps and sequence numbers, so no laptop time is mixed in."""
    if len(samples) < 2:
        return None, None
    span_ns = samples[-1]["host_sample_mono_ns"] - samples[0]["host_sample_mono_ns"]
    if not span_ns or span_ns <= 0:
        return None, None
    return round((samples[-1]["seq"] - samples[0]["seq"]) * 1e9 / span_ns, 3), round(span_ns / 1e9, 3)


def collect(client, seconds):
    samples, started = [], time.monotonic()
    while time.monotonic() - started < seconds:
        samples.extend(client.poll(200))
    return samples


def summarize(samples, stats, label):
    loop_hz, span_s = rate(samples)
    rx_hz = None
    if len(samples) >= 2 and samples[-1]["rx_mono_ns"] > samples[0]["rx_mono_ns"]:
        rx_hz = round((len(samples) - 1) * 1e9 / (samples[-1]["rx_mono_ns"] - samples[0]["rx_mono_ns"]), 3)
    intervals = [b["host_sample_mono_ns"] - a["host_sample_mono_ns"] for a, b in zip(samples, samples[1:])]
    last = samples[-1] if samples else {}
    return {
        "label": label, "samples": len(samples), "sessions": sorted({s["session"] for s in samples}),
        "first_seq": samples[0]["seq"] if samples else None, "last_seq": last.get("seq"),
        "host_span_s": span_s, "host_loop_rate_hz": loop_hz, "receive_rate_hz": rx_hz,
        "longest_host_interval_ms": round(max(intervals) / 1e6, 3) if intervals else None,
        "missed_total": sum(s["missed_before"] or 0 for s in samples),
        "publisher_drops_total": sum(s["publisher_drops_before"] or 0 for s in samples),
        "lost_after_send_total": sum(s["lost_after_send_before"] or 0 for s in samples),
        "subscriber_stats": stats, "lease": last.get("lease"), "rejections": last.get("rejections"),
        "commands": last.get("commands"), "ingestion": last.get("ingestion"), "publisher": last.get("publisher"),
        "observation_keys": sorted(last.get("observation") or {}) if isinstance(last.get("observation"), dict) else None,
    }


def report(summary):
    line("samples %s, source session %s, seq %s to %s"
         % (summary["samples"], ",".join(summary["sessions"]) or "none", summary["first_seq"], summary["last_seq"]))
    line("host loop rate %s Hz over %s s of host time; this laptop received %s samples per second"
         % (summary["host_loop_rate_hz"], summary["host_span_s"], summary["receive_rate_hz"]))
    line("longest interval between two received samples, host clock: %s ms" % summary["longest_host_interval_ms"])
    line("gaps: %s samples missed, %s of them the publisher accounts for, %s lost after a successful send"
         % (summary["missed_total"], summary["publisher_drops_total"], summary["lost_after_send_total"]))


def window_problems(summary, minimum):
    problems = []
    if summary["samples"] < minimum:
        problems.append("only %s tap samples arrived (at least %s were expected)" % (summary["samples"], minimum))
    if len(summary["sessions"]) != 1:
        problems.append("the source session was not one and the same: %s" % summary["sessions"])
    if summary["missed_total"]:
        problems.append("%s observation samples did not reach this laptop" % summary["missed_total"])
    stats = summary["subscriber_stats"] or {}
    if stats.get("out_of_order_count"):
        problems.append("%s samples arrived out of order" % stats["out_of_order_count"])
    if stats.get("malformed_count") or stats.get("transport_errors"):
        problems.append("the subscriber saw %s malformed samples and %s transport errors"
                        % (stats.get("malformed_count"), stats.get("transport_errors")))
    if not summary["host_loop_rate_hz"]:
        problems.append("no host loop rate could be measured")
    return problems


def cmd_tap_window(endpoint, seconds, label):
    TapClient, _ = clients()
    client = TapClient(endpoint)
    try:
        samples = collect(client, float(seconds))
        stats = client.stats()
    finally:
        client.close()
    summary = summarize(samples, stats, label)
    report(summary)
    problems = window_problems(summary, 5)
    emit(summary, not problems, "; ".join(problems))


def cmd_tap_gap(endpoint, gap_s, sample_s):
    """The host loop rate with a tap subscriber attached, and across a window with none attached. Both rates come from
    the host's own stamps, so the comparison never mixes two clocks. 'Unsubscribed' means no subscriber is connected:
    the host publishes either way and libzmq drops what nobody is there to want."""
    TapClient, _ = clients()
    first = TapClient(endpoint)
    before = collect(first, float(sample_s))
    stats_before = first.stats()
    first.close()
    if len(before) < 2:
        emit({"samples_before": len(before)}, False,
             "the tap published nothing before the gap: is the motor host running WITH the boundary?")
    time.sleep(float(gap_s))
    second = TapClient(endpoint)
    after = collect(second, float(sample_s))
    stats_after = second.stats()
    second.close()
    if len(after) < 2:
        emit({"samples_before": len(before), "samples_after": len(after)}, False, "the tap published nothing after the gap")
    rate_before, span_before = rate(before)
    rate_after, span_after = rate(after)
    across_hz, across_span = rate([before[-1], after[0]])
    measured = [value for value in (rate_before, rate_after) if value]
    subscribed = statistics.mean(measured) if measured else None
    document = {
        "subscribed_rate_hz_before": rate_before, "subscribed_span_s_before": span_before,
        "subscribed_rate_hz_after": rate_after, "subscribed_span_s_after": span_after,
        "subscribed_rate_hz_mean": subscribed, "unsubscribed_rate_hz": across_hz, "unsubscribed_span_s": across_span,
        "requested_gap_s": float(gap_s), "sessions": sorted({s["session"] for s in before + after}),
        "subscriber_stats_before": stats_before, "subscriber_stats_after": stats_after,
    }
    line("host loop rate with a tap subscriber attached: %s Hz, then %s Hz" % (rate_before, rate_after))
    line("host loop rate across %s s with NO tap subscriber attached: %s Hz over %s s of host time"
         % (gap_s, across_hz, across_span))
    problems = []
    if len(document["sessions"]) != 1:
        problems.append("the host session changed across the gap: the host restarted (%s)" % document["sessions"])
    if not subscribed or not across_hz:
        problems.append("one of the two rates could not be measured")
    else:
        difference = abs(across_hz - subscribed) / subscribed * 100.0
        document["difference_pct"] = round(difference, 2)
        line("difference: %.2f percent (the tolerance is %.1f percent)" % (difference, TOL))
        if difference > TOL:
            problems.append("the loop rate differs by %.2f percent between subscribed and unsubscribed" % difference)
    emit(document, not problems, "; ".join(problems))


def cmd_reject_probe(tap_endpoint, count):
    """Send commands that carry NO lease metadata and prove from the host's own counters that they were rejected and
    that nothing was dispatched. Nothing is sent until the tap has been seen publishing, which happens only when the
    boundary is installed; and no message carries a joint or a wheel key, so even a host without the boundary could not
    reach a motor with one (the stock send_action raises on the missing wheel key before it writes any register)."""
    import zmq

    TapClient, _ = clients()
    count = int(count)
    client = TapClient(tap_endpoint)
    baseline, started = None, time.monotonic()
    while baseline is None and time.monotonic() - started < 10.0:
        samples = client.poll(500)
        baseline = samples[-1] if samples else None
    if baseline is None:
        client.close()
        emit({}, False, "the tap published nothing in 10 s: the host is not running with KC_HOST_BOUNDARY=1, so NOTHING was sent")
    for key in ("rejections", "ingestion", "commands", "lease"):
        if not isinstance(baseline.get(key), dict):
            client.close()
            emit({"baseline_keys": sorted(baseline)}, False,
                 "the tap sample carries no %r: this is not the host boundary's tap, so NOTHING was sent" % key)
    line("the boundary is publishing: session %s, seq %s, lease state %s"
         % (baseline["session"], baseline["seq"], baseline["lease"].get("state")))

    context = zmq.Context.instance()
    push = context.socket(zmq.PUSH)
    push.setsockopt(zmq.LINGER, 2000)
    push.setsockopt(zmq.SNDTIMEO, 2000)
    push.connect(os.environ["KC_QUAL_CMD_ENDPOINT"])
    time.sleep(1.0)  # let the connection establish, so that nothing sits unsent in the socket
    sent = []
    for index in range(count):
        push.send_string(json.dumps({"kc.probe": index + 1}))
        sent.append("a JSON object with no lease metadata, no joint key and no wheel key")
        time.sleep(0.1)
    push.send_string("this is not json")
    sent.append("not a JSON object at all")
    time.sleep(1.0)
    push.close()

    final, started = baseline, time.monotonic()
    while time.monotonic() - started < 4.0:
        samples = client.poll(500)
        if samples:
            final = samples[-1]
    stats = client.stats()
    client.close()

    def delta(section, key):
        return (final[section].get(key) or 0) - (baseline[section].get(key) or 0)

    rejected = {reason: delta("rejections", reason) for reason in set(baseline["rejections"]) | set(final["rejections"])}
    kept = {"session", "seq", "rejections", "ingestion", "commands", "lease"}
    document = {
        "messages_sent": sent,
        "baseline": {k: v for k, v in baseline.items() if k in kept},
        "final": {k: v for k, v in final.items() if k in kept},
        "rejection_deltas": rejected, "received_delta": delta("ingestion", "received"),
        "dispatched_total_delta": delta("commands", "dispatched_total"),
        "internal_errors_delta": delta("ingestion", "internal_errors"),
        "seq_advance": final["seq"] - baseline["seq"], "subscriber_stats": stats,
        "since_last_dispatch_ms_before": baseline["commands"].get("since_last_ms"),
        "since_last_dispatch_ms_after": final["commands"].get("since_last_ms"),
    }
    line("sent %s messages with no lease metadata, and one that is not JSON at all" % count)
    line("the host read %s more messages and rejected: %s"
         % (document["received_delta"], {k: v for k, v in sorted(rejected.items()) if v}))
    line("commands DISPATCHED to the host loop in that time: %s (a dispatch, and nothing else, feeds the base watchdog)"
         % document["dispatched_total_delta"])
    line("time since the last dispatched command: %s ms before, %s ms after"
         % (document["since_last_dispatch_ms_before"], document["since_last_dispatch_ms_after"]))
    line("the host kept running: its sequence advanced by %s, session %s" % (document["seq_advance"], final["session"]))
    problems = []
    if final["session"] != baseline["session"]:
        problems.append("the host session changed: it restarted during the probe")
    if document["dispatched_total_delta"] != 0:
        problems.append("%s command(s) were DISPATCHED: the boundary did not reject them" % document["dispatched_total_delta"])
    if rejected.get("no_lease", 0) != count:
        problems.append("no_lease went up by %s, not by %s" % (rejected.get("no_lease", 0), count))
    if rejected.get("malformed", 0) != 1:
        problems.append("malformed went up by %s, not by 1" % rejected.get("malformed", 0))
    if document["received_delta"] != count + 1:
        problems.append("the host read %s messages, not %s" % (document["received_delta"], count + 1))
    if document["internal_errors_delta"] != 0:
        problems.append("%s fault(s) inside the boundary" % document["internal_errors_delta"])
    if document["seq_advance"] < 5:
        problems.append("the host produced only %s observations during the probe" % document["seq_advance"])
    before_ms, after_ms = document["since_last_dispatch_ms_before"], document["since_last_dispatch_ms_after"]
    if before_ms is None and after_ms is not None:
        problems.append("nothing had ever been dispatched and now something has: a rejected command reached the host loop")
    elif before_ms is not None and (after_ms is None or after_ms < before_ms):
        problems.append("the time since the last DISPATCHED command went backwards (%s then %s ms): a rejected command "
                        "fed the clock the base watchdog reads" % (before_ms, after_ms))
    emit(document, not problems, "; ".join(problems))


def ask(endpoint, request, timeout_s=15.0):
    import zmq

    context = zmq.Context.instance()
    socket = context.socket(zmq.REQ)
    socket.setsockopt(zmq.LINGER, 0)
    socket.setsockopt(zmq.SNDTIMEO, 3000)
    socket.setsockopt(zmq.RCVTIMEO, int(timeout_s * 1000))
    socket.connect(endpoint)
    try:
        socket.send_string(json.dumps(request))
        return json.loads(socket.recv_string())
    except zmq.Again:
        return {"ok": False, "reason": "no_reply", "detail": "the camera service did not answer within %.1f s" % timeout_s}
    except ValueError as error:
        return {"ok": False, "reason": "bad_reply", "detail": str(error)}
    finally:
        socket.close(0)


def wrist_of(reply):
    return (((reply.get("status") or {}).get("cameras") or {}).get("wrist")) if isinstance(reply, dict) else None


def cmd_camera_status(mission_endpoint, label):
    reply = ask(mission_endpoint, {"op": "status"})
    wrist = wrist_of(reply)
    if isinstance(wrist, dict):
        negotiated = wrist.get("negotiated") or {}
        line("wrist: state %s, lock holder %s, negotiated %s %sx%s"
             % (wrist.get("state"), (wrist.get("lock") or {}).get("holder"), negotiated.get("pixel_format"),
                negotiated.get("width"), negotiated.get("height")))
        line("       source_fps_reported %s, source_fps_measured %s, frames_seen %s, reads_without_frame %s, driver timestamp trusted %s"
             % (negotiated.get("source_fps_reported"), wrist.get("source_fps_measured"), wrist.get("frames_seen"),
                wrist.get("reads_without_frame"), (wrist.get("driver_timestamp") or {}).get("trusted")))
    else:
        line("the service answered: %s" % json.dumps(reply)[:400])
    emit({"label": label, "reply": reply}, bool(reply.get("ok")),
         "the camera service did not answer with ok: %s" % json.dumps(reply)[:200])


def cmd_stream_window(tap_endpoint, preview_endpoint, mission_endpoint, seconds, captures):
    """What the open stream costs and what it delivers, measured together: the motor host's observations on the tap, the
    preview as this laptop sees it, and mission captures in flight, over one window."""
    TapClient, PreviewSubscriber = clients()
    seconds, captures = float(seconds), int(captures)
    tap = TapClient(tap_endpoint)
    preview = PreviewSubscriber(preview_endpoint, role="wrist")
    preview.open()
    samples, frames = [], []
    started = time.monotonic()
    next_capture = started + 2.0
    while time.monotonic() - started < seconds:
        samples.extend(tap.poll(0))
        preview.poll_once(50)
        if len(frames) < captures and time.monotonic() >= next_capture:
            request_mono_ns = time.monotonic_ns()
            reply = ask(mission_endpoint, {"op": "capture", "role": "wrist", "session": "qualification", "timeout_s": 10})
            frames.append({"reply": reply, "laptop_round_trip_ms": round((time.monotonic_ns() - request_mono_ns) / 1e6, 3),
                           "laptop_clock_domain": "laptop_monotonic"})
            next_capture = time.monotonic() + max(5.0, seconds / (captures + 1))
    samples.extend(tap.poll(200))
    tap_stats, preview_stats = tap.stats(), preview.stats()
    tap.close()
    preview.stop()

    summary = summarize(samples, tap_stats, "stream on")
    report(summary)
    gaps = preview_stats.get("gaps") or {}
    longest = preview_stats.get("longest_interval_ns")
    line("preview as this laptop sees it: %s frames, measured %s fps, longest interval %s ms, gap events %s, missing previews %s"
         % (preview_stats.get("received"), preview_stats.get("measured_fps"),
            None if longest is None else round(longest / 1e6, 1), gaps.get("events"), gaps.get("missing_previews")))
    problems = window_problems(summary, 5)
    kept = 0
    for index, item in enumerate(frames, start=1):
        reply = item["reply"]
        if not reply.get("ok"):
            problems.append("mission capture %s was refused: %s" % (index, reply.get("reason")))
            line("capture %s: REFUSED (%s) %s" % (index, reply.get("reason"), reply.get("detail")))
            continue
        kept += 1
        frame = reply.get("frame") or {}
        negotiated = frame.get("negotiated") or {}
        guard, request_ns, capture_ns = frame.get("exposure_guard_ns"), frame.get("request_mono_ns"), frame.get("capture_mono_ns")
        boundary_ns, semantics = frame.get("settle_boundary_mono_ns"), frame.get("timestamp_semantics")
        latency = None if capture_ns is None or request_ns is None else round((capture_ns - request_ns) / 1e6, 3)
        line("capture %s: %s, guard %s ms from %s, request to frame %s ms (robot computer clock), round trip %s ms (laptop clock)"
             % (index, semantics, None if guard is None else round(guard / 1e6, 2), frame.get("exposure_guard_source"),
                latency, item["laptop_round_trip_ms"]))
        line("           negotiated %s %sx%s, source_fps_reported %s, source_fps_measured %s, discarded stale %s, "
             "discarded settle %s, drained %s"
             % (negotiated.get("pixel_format"), frame.get("width"), frame.get("height"),
                negotiated.get("source_fps_reported"), frame.get("source_fps_measured"),
                frame.get("frames_discarded_stale"), frame.get("frames_discarded_settle"), frame.get("frames_drained")))
        if semantics == "driver_buffer_timestamp":
            if not (capture_ns and request_ns and guard is not None and capture_ns > request_ns + guard):
                problems.append("capture %s was not taken after the request plus the exposure guard" % index)
            if boundary_ns is not None and capture_ns is not None and capture_ns <= boundary_ns:
                problems.append("capture %s is not later than its last settle frame" % index)
        else:
            line("           WEAKER MODE: %s (%s). The frame is chosen by buffer depth, not by a measured capture time."
                 % (semantics, frame.get("fallback_reason")))
    if kept < captures:
        problems.append("only %s of %s mission captures returned a frame" % (kept, captures))
    if (preview_stats.get("received") or 0) < 2:
        problems.append("the preview delivered %s frames to this laptop" % preview_stats.get("received"))
    if gaps.get("events"):
        line("NOTE: the preview showed %s gap event(s). A dropped preview is not an error: it is the latest-only rule."
             % gaps["events"])
    emit({"tap": summary, "preview": preview_stats, "captures": frames}, not problems, "; ".join(problems))


COMMANDS = {"tap-window": cmd_tap_window, "tap-gap": cmd_tap_gap, "reject-probe": cmd_reject_probe,
            "camera-status": cmd_camera_status, "stream-window": cmd_stream_window}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        sys.exit("usage: <helper> {%s} ..." % "|".join(sorted(COMMANDS)))
    COMMANDS[sys.argv[1]](*sys.argv[2:])
HELPER_PY

# Runs one helper subcommand: its JSON goes to <out>/<name>.json, its lines to the terminal and the transcript, and its
# exit status is the verdict.
run_helper() {
  local name="$1" status=0
  shift
  KC_QUAL_JSON="$OUT/$name.json" KC_QUAL_REPO="$ROOT" KC_QUAL_RATE_TOL_PCT="$RATE_TOL" \
    python3 "$HELPER" "$@" >"$OUT/$name.log" 2>&1 || status=$?
  show "$OUT/$name.log"
  return "$status"
}

# ---------------------------------------------------------------------------------------------------------------------
# Robot computer helpers
# ---------------------------------------------------------------------------------------------------------------------
deploy_qual() {
  local f local_sum remote_sum
  # shellcheck disable=SC2029  # REMOTE_DIR is a constant of this script
  "${SSH[@]}" "mkdir -p '$REMOTE_DIR/bin/pi' '$REMOTE_DIR/autonomy/perception' kc-qualification/runs"
  for f in "${QUAL_FILES[@]}"; do
    [ -f "$ROOT/$f" ] || { echo "REFUSE: $f is missing from this checkout" >&2; exit 5; }
    scp -q -o BatchMode=yes -o ConnectTimeout=6 "$ROOT/$f" "$KC_PI_SSH:$REMOTE_DIR/$f"
    local_sum="$(sha256sum "$ROOT/$f" | cut -d' ' -f1)"
    # shellcheck disable=SC2029
    remote_sum="$("${SSH[@]}" "sha256sum '$REMOTE_DIR/$f'" | cut -d' ' -f1)"
    if [ -z "$local_sum" ] || [ "$local_sum" != "$remote_sum" ]; then
      echo "REFUSE: checksum mismatch between the local $f and the copy on the robot computer" >&2
      exit 4
    fi
  done
}

# The installed lerobot source, copied here and read here. 0 when both passes hold, 1 when anything else happened, and
# the log it wrote says which: a pass that ran and found the source changed, a pass whose file never arrived, and a pass
# that exited without a verdict are three different facts and are written as three different sentences. No path of the
# robot computer is ever printed or written: those paths name the account it runs as, and this output goes into a
# transcript. See the header for what a pass proves and, more importantly, what it does not.
read_installed_source() {  # read_installed_source <name for the log it writes under the output directory>
  local log="$OUT/$1.log" located=0 lekiwi_dir="" status=0 problems=0 mode file
  : >"$log"
  SOURCE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/kc-installed-source-XXXXXX")"
  # shellcheck disable=SC2029  # REMOTE_PYTHON is built in this script and is never printed
  lekiwi_dir="$(
    "${SSH[@]}" "PYBIN=$REMOTE_PYTHON bash -s" 2>>"$log" <<'LOCATE'
set -uo pipefail
[ -x "$PYBIN" ] || { echo "the interpreter the host runs with was not found on the robot computer (set KC_PI_PYTHON)" >&2; exit 3; }
INSTALLED="$("$PYBIN" -c 'import importlib.util as u; s = u.find_spec("lerobot"); print(s.submodule_search_locations[0] if s else "")' 2>/dev/null)" || INSTALLED=""
[ -n "$INSTALLED" ] || { echo "no lerobot is installed under the interpreter the host runs with" >&2; exit 3; }
printf '%s\n' "$INSTALLED/robots/lekiwi"
LOCATE
  )" || located=$?
  if [ "$located" -ne 0 ]; then
    printf '%s\n' "the robot computer could not say where its lerobot is installed (status $located): no file was read and no pass could be run" >>"$log"
    rm -rf "$SOURCE_DIR"
    SOURCE_DIR=""
    show "$log"
    return 1
  fi
  # That path goes inside single quotes into a command line the REMOTE shell parses, exactly as the identities do, and
  # nothing here escapes it either. It came from the robot computer rather than from a person, so it is held to the same
  # character set and refused rather than sent. The value itself is never printed.
  case "$lekiwi_dir" in
    "" | -* | *[!A-Za-z0-9_./:@+=-]*)
      printf '%s\n' "the directory the robot computer named for the installed lerobot is empty, starts with '-' or holds a character outside A-Za-z0-9_./:@+=- ; it would go unescaped into a remote command line, so no file was read and no pass could be run" >>"$log"
      rm -rf "$SOURCE_DIR"
      SOURCE_DIR=""
      show "$log"
      return 1 ;;
  esac
  # lekiwi.py, the config_lekiwi.py that --verify-robot reads beside it, and the stock host loop. The remote cat's own
  # stderr is dropped because the message it prints for a file it cannot open carries the path; a file that did not
  # arrive is named below by its basename instead, and leaves no half-written copy behind for a pass to read.
  for file in lekiwi.py config_lekiwi.py lekiwi_host.py; do
    status=0
    # shellcheck disable=SC2029  # the directory came from the robot computer and was checked above against the same character set the identities are
    "${SSH[@]}" "cat '$lekiwi_dir/$file'" >"$SOURCE_DIR/$file" 2>/dev/null || status=$?
    [ "$status" -eq 0 ] || rm -f "$SOURCE_DIR/$file"
  done
  for mode in --verify-robot --verify-stock-host; do
    case "$mode" in --verify-robot) file="lekiwi.py" ;; *) file="lekiwi_host.py" ;; esac
    if [ ! -f "$SOURCE_DIR/$file" ]; then
      printf '%s\n' "$file, the file $mode reads, did not come back from the robot computer: that pass could not be run, which says nothing about the installed source" >>"$log"
      problems=$((problems + 1))
      continue
    fi
    status=0
    python3 "$ROOT/bin/pi/host_boundary.py" "$mode" "$SOURCE_DIR/$file" >>"$log" 2>&1 || status=$?
    [ "$status" -le 1 ] || printf '%s\n' "$mode exited $status without printing a verdict: that pass could not be run, which says nothing about the installed source" >>"$log"
    [ "$status" -eq 0 ] || problems=$((problems + 1))
  done
  rm -rf "$SOURCE_DIR"
  SOURCE_DIR=""
  show "$log"
  [ "$problems" -eq 0 ]
}

host_running() {  # 0 when the safe-attach motor host runs there, 1 when it does not; an unreachable robot stops the run
  local status=0
  "${SSH[@]}" "pgrep -f 'lekiwi_host_safe_attach[.]py' >/dev/null 2>&1" || status=$?
  case "$status" in
    0) return 0 ;;
    1) return 1 ;;
    *) step_fail "the robot computer could not be reached over ssh (status $status)" ;;
  esac
}

ensure_forward() {
  [ -z "$FORWARD_PID" ] || return 0
  ssh -N -o BatchMode=yes -o ConnectTimeout=6 -o ExitOnForwardFailure=yes -o ServerAliveInterval=5 -o ServerAliveCountMax=3 \
    -L "127.0.0.1:$TAP_PORT:127.0.0.1:5558" -L "127.0.0.1:$LEASE_PORT:127.0.0.1:5557" \
    -L "127.0.0.1:$MISSION_PORT:127.0.0.1:5570" -L "127.0.0.1:$PREVIEW_PORT:127.0.0.1:5571" "$KC_PI_SSH" &
  FORWARD_PID=$!
  local waited=0
  while [ "$waited" -lt 15 ]; do
    if python3 -c 'import socket,sys
for port in sys.argv[1:]:
    with socket.socket() as probe:
        probe.settimeout(1.0)
        probe.connect(("127.0.0.1", int(port)))' "$TAP_PORT" "$LEASE_PORT" "$MISSION_PORT" "$PREVIEW_PORT" 2>/dev/null; then
      item "the loopback sockets of the robot computer are forwarded here (local ports $TAP_PORT, $LEASE_PORT, $MISSION_PORT, $PREVIEW_PORT)"
      return 0
    fi
    if ! kill -0 "$FORWARD_PID" 2>/dev/null; then
      FORWARD_PID=""
      step_fail "the ssh forward exited at once; one of the local ports is probably in use here (try --port-offset)"
    fi
    sleep 1
    waited=$((waited + 1))
  done
  step_fail "the ssh forward did not come up within 15 s (try --port-offset if a local port is in use)"
}

# The camera service launcher. Its stdout (for lock-status, one JSON document) and its prose are kept separately.
camera_service() {
  local action="$1" name="$2" status=0
  "$ROOT/bin/pi/start-camera-service.sh" "$action" >"$OUT/$name.out" 2>"$OUT/$name.err" || status=$?
  cat "$OUT/$name.out" "$OUT/$name.err" >>"$TRANSCRIPT"
  return "$status"
}

lock_held_by() {  # lock_held_by <file> <role> <expected holder label>
  python3 -c 'import json,sys
status = json.load(open(sys.argv[1], encoding="utf-8"))
entry = (status.get("roles") or {}).get(sys.argv[2]) or {}
holder = (entry.get("holder") or {}).get("holder")
sys.exit(0 if entry.get("held") and holder == sys.argv[3] else 1)' "$1" "$2" "$3"
}

any_lock_held() {  # any_lock_held <file>
  python3 -c 'import json,sys
status = json.load(open(sys.argv[1], encoding="utf-8"))
held = [role for role, entry in (status.get("roles") or {}).items() if entry.get("held")]
print(" ".join(sorted(held)))
sys.exit(0 if held else 1)' "$1"
}

# ---------------------------------------------------------------------------------------------------------------------
# step 0: read-only preconditions
# ---------------------------------------------------------------------------------------------------------------------
step0() {
  begin 0 "read-only preconditions (nothing is started, no camera is opened, no motor is touched)"
  item "deploying the files this session reads from, and verifying every checksum"
  deploy_qual
  item "asking the robot computer about itself"
  local status=0
  # REMOTE_DIR is no longer named here: nothing this call runs lives in it any more, because the source verification
  # moved to this laptop and the rest of this block only looks at the robot computer itself.
  # shellcheck disable=SC2029  # REMOTE_PYTHON is built in this script and is not printed
  "${SSH[@]}" "PYBIN=$REMOTE_PYTHON bash -s" >"$OUT/step0-robot.txt" 2>&1 <<'PRE' || status=$?
set -uo pipefail
FAILS=0
cd "$HOME" || exit 3
echo "reachable: yes"
echo "machine: $(uname -m)"
if [ -x "$PYBIN" ]; then echo "host interpreter: found"; else echo "host interpreter: NOT FOUND (set KC_PI_PYTHON)"; FAILS=$((FAILS + 1)); fi
"$PYBIN" - <<'PY' || FAILS=$((FAILS + 1))
import sys
print("python: %d.%d.%d" % sys.version_info[:3])
try:
    import zmq
    print("pyzmq: %s, libzmq: %s" % (zmq.__version__, zmq.zmq_version()))
except Exception as error:
    print("pyzmq: MISSING (%s)" % error)
    raise SystemExit(1)
for name in ("lerobot", "numpy", "cv2"):
    try:
        __import__(name)
        print("%s: present" % name)
    except Exception as error:
        print("%s: MISSING (%s)" % (name, error))
        raise SystemExit(1)
PY
if pgrep -f 'lekiwi_host|lerobot' >/dev/null 2>&1; then
  echo "motor host: ALREADY RUNNING (step 0 wants nothing running)"; FAILS=$((FAILS + 1))
else
  echo "motor host: not running"
fi
if pgrep -f 'camera_service[.]py' >/dev/null 2>&1; then
  echo "camera service: ALREADY RUNNING (step 0 wants nothing running)"; FAILS=$((FAILS + 1))
else
  echo "camera service: not running"
fi
OWNED=0
for n in /dev/video*; do
  [ -e "$n" ] || continue
  if fuser "$n" >/dev/null 2>&1; then echo "camera owner: $n HAS AN OWNER"; OWNED=1; fi
done
if [ "$OWNED" -eq 0 ]; then echo "camera owner: none"; else FAILS=$((FAILS + 1)); fi
echo "checks failed: $FAILS"
[ "$FAILS" -eq 0 ]
PRE
  show "$OUT/step0-robot.txt"
  [ "$status" -eq 0 ] || step_fail "a precondition of the robot computer does not hold (the lines above say which)"
  # The installed lerobot source, copied here and read here. It needs nothing of this script's deployment: the two
  # passes read text, and the text is fetched. The robot source is the one the base path depends on and the one a
  # checkout on that computer silently reverts; the stock host loop is what the boundary assumes when it patches it.
  item "the INSTALLED lerobot source, copied here and read on this laptop (the robot computer is only read)"
  read_installed_source step0-source || step_fail "the installed lerobot source does not hold what host_boundary.py reads it for, or a pass could not be run at all (the lines above say which of the two it was)"
  item "the camera locks"
  camera_service lock-status step0-locks || step_fail "the camera lock status could not be read"
  show "$OUT/step0-locks.out"
  if any_lock_held "$OUT/step0-locks.out" >/dev/null; then step_fail "a camera lock is already held; stop whatever holds it"; fi
  step_pass "reachable, the host's interpreter has pyzmq, lerobot, numpy and cv2, the INSTALLED robot source and host loop hold what host_boundary.py reads them for, nothing is running, no camera is owned and no lock is held. That is a verdict about the SOURCE, not a promise that the motor host will attach: the attach reads the live registers and refuses an arm that is not acquired"
}

# ---------------------------------------------------------------------------------------------------------------------
# step 1: camera ownership
# ---------------------------------------------------------------------------------------------------------------------
probe_once() {  # probe_once <label>: runs the legacy probe once, leaves its status in PROBE_STATUS and its report in <out>
  local label="$1" status=0
  # shellcheck disable=SC2029  # the wrist identity is validated above, is passed in the remote environment and is never printed
  "${SSH[@]}" "PYBIN=$REMOTE_PYTHON DIR='$REMOTE_DIR' RUN='kc-qualification/runs/$TS/$label' KC_WRIST_CAMERA_ID='$KC_WRIST_CAMERA_ID' bash -s" \
    >"$OUT/step1-$label.txt" 2>&1 <<'PROBE' || status=$?
set -uo pipefail
cd "$HOME" || exit 3
"$PYBIN" "$DIR/bin/pi/camera_multiplex_probe.py" --cycles 1 --order wrist --out-dir "$RUN"
PROBE
  PROBE_STATUS="$status"
  show "$OUT/step1-$label.txt"
  scp -q -o BatchMode=yes -o ConnectTimeout=6 "$KC_PI_SSH:kc-qualification/runs/$TS/$label/report.json" "$OUT/step1-$label.json" 2>/dev/null || true
}

probe_field() {  # probe_field <label> <dotted path in the report>
  python3 -c 'import json,sys
value = json.load(open(sys.argv[1], encoding="utf-8"))
for key in sys.argv[2].split("."):
    value = value[key] if isinstance(value, dict) else None
print(json.dumps(value))' "$OUT/step1-$1.json" "$2" 2>/dev/null || echo '"unreadable"'
}

step1() {
  begin 1 "camera ownership: one owner at a time, and the refusal is visible (no motion; no motor host needed)"
  item "the legacy per-capture probe with no service running: it must work"
  probe_once "before"
  [ "$PROBE_STATUS" -eq 0 ] || step_fail "the legacy probe failed before any service was started (status $PROBE_STATUS): the camera path is broken independently of the service"
  item "its report: refused=$(probe_field before refused), valid wrist frames=$(probe_field before summary.wrist.frame_valid)"
  [ "$(probe_field before summary.wrist.frame_valid)" != "0" ] || step_fail "the legacy probe kept no valid wrist frame"

  item "starting the camera service (wrist only, preview on)"
  camera_service start step1-start || step_fail "the camera service did not start"
  item "the camera locks while it runs:"
  camera_service lock-status step1-locks-held || step_fail "the camera lock status could not be read"
  show "$OUT/step1-locks-held.out"
  lock_held_by "$OUT/step1-locks-held.out" wrist camera_service || step_fail "the wrist lock is not held by camera_service while the service runs"

  item "the legacy probe again, with the service running: it must be REFUSED and open nothing"
  probe_once "refused"
  [ "$PROBE_STATUS" -eq 3 ] || step_fail "the legacy probe exited $PROBE_STATUS, not 3: it was not refused by the lock"
  item "its report: refused reason=$(probe_field refused refused.reason), valid wrist frames=$(probe_field refused summary.wrist.frame_valid)"
  [ "$(probe_field refused refused.reason)" = '"camera_lock_held"' ] || step_fail "the probe was refused for another reason than camera_lock_held"
  [ "$(probe_field refused summary.wrist.frame_valid)" = "0" ] || step_fail "the refused probe still kept a frame"

  item "stopping the camera service"
  camera_service stop step1-stop || step_fail "the camera service did not stop"
  item "the camera locks after the stop:"
  camera_service lock-status step1-locks-free || step_fail "the camera lock status could not be read"
  show "$OUT/step1-locks-free.out"
  if any_lock_held "$OUT/step1-locks-free.out" >/dev/null; then step_fail "a camera lock is still held after the service stopped"; fi

  item "the legacy probe once more: the camera must be usable again"
  probe_once "after"
  [ "$PROBE_STATUS" -eq 0 ] || step_fail "the legacy probe did not work again after the service stopped (status $PROBE_STATUS): the handoff back is not clean"
  [ "$(probe_field after summary.wrist.frame_valid)" != "0" ] || step_fail "the legacy probe kept no valid wrist frame after the handoff back"
  step_pass "exactly one provider owned the wrist camera at a time, the refusal is visible in the probe's report and its exit status, and the camera came back to the legacy path"
}

# ---------------------------------------------------------------------------------------------------------------------
# steps 2 to 4 need the motor host running WITH the boundary, started by the operator in another terminal
# ---------------------------------------------------------------------------------------------------------------------
require_host() {
  host_running && return 0
  say ""
  say "  The motor host is not running. In ANOTHER terminal, from this checkout, with the arm already acquired and"
  say "  HOLDING its pose, start it WITH the boundary and leave it in the foreground of that terminal:"
  say ""
  say "      bin/pi/start-host.sh boundary"
  say ""
  say "  Wait for 'SAFE ATTACH OK', 'HOST BOUNDARY ON' and 'Waiting for commands...'. This session waits up to 5 minutes."
  local waited=0
  while [ "$waited" -lt 150 ]; do
    sleep 2
    waited=$((waited + 1))
    if host_running; then item "the motor host is running"; return 0; fi
  done
  step_fail "no motor host appeared within 5 minutes"
}

confirm_step2() {
  say ""
  say "  BEFORE YOU CONTINUE. This step sends SIX messages that the host must refuse. None of them carries a joint"
  say "  position or a wheel velocity, so none could become a motor write even if it were dispatched, and nothing is"
  say "  sent at all until the boundary's tap has been seen publishing. No torque is changed, no motor is commanded."
  say "  Even so: the arm must be SUPPORTED or resting at the pose it holds, the base must have free floor, and your"
  say "  hand must be near the servo power switch."
  if [ "$ASSUME_YES" -eq 1 ]; then item "continuing without asking (--yes)"; return 0; fi
  [ -t 0 ] || step_fail "this step asks the operator to confirm and stdin is not a terminal; run it from a terminal or pass --yes"
  local answer=""
  printf '  type CONFIRM to continue: '
  read -r answer
  printf 'operator answered: %s\n' "$answer" >>"$TRANSCRIPT"
  [ "$answer" = "CONFIRM" ] || step_fail "the operator did not confirm"
}

step2() {
  begin 2 "command rejection: a command without lease metadata is dropped at ingestion and dispatches nothing"
  require_host
  ensure_forward
  confirm_step2
  item "probing the boundary through its tap"
  export KC_QUAL_CMD_ENDPOINT="tcp://$KC_PI_IP:5555"
  run_helper step2-reject reject-probe "tcp://127.0.0.1:$TAP_PORT" 5 \
    || step_fail "the rejection probe did not pass (the VERDICT line above says why)"
  unset KC_QUAL_CMD_ENDPOINT
  step_pass "every message without live lease metadata was counted as rejected, nothing reached the host loop, the dispatch clock that feeds the base watchdog was not touched, and the host kept publishing"
}

step3() {
  begin 3 "observation freshness: source session, sequence advance, measured rate and gap counters, stream off and on"
  require_host
  ensure_forward
  item "making sure the camera service is stopped for the baseline"
  camera_service stop step3-stop-first || step_fail "a camera service is running and did not stop"
  item "window A: ${WINDOW_S}s of tap with the camera stream OFF"
  run_helper step3-stream-off tap-window "tcp://127.0.0.1:$TAP_PORT" "$WINDOW_S" "stream off" \
    || step_fail "the tap window with the stream off did not pass"
  item "the host loop rate with a tap subscriber attached and with none, across a ${GAP_S}s gap"
  run_helper step3-tap-cost tap-gap "tcp://127.0.0.1:$TAP_PORT" "$GAP_S" 10 \
    || step_fail "the host loop rate is not the same with a tap subscriber attached and without one"
  item "starting the camera service for window B"
  camera_service start step3-start || step_fail "the camera service did not start"
  item "window B: ${WINDOW_S}s of tap with the camera stream ON"
  local status=0
  run_helper step3-stream-on tap-window "tcp://127.0.0.1:$TAP_PORT" "$WINDOW_S" "stream on" || status=$?
  item "stopping the camera service again, to leave the robot computer as this step found it"
  camera_service stop step3-stop || warn "the camera service did not stop; it still owns the wrist camera"
  [ "$status" -eq 0 ] || step_fail "the tap window with the stream on did not pass"
  step_pass "the tap carried one source session with a strictly advancing sequence and no lost sample, with the camera stream off and on, and the host loop rate did not change with a tap subscriber attached"
}

step4() {
  begin 4 "camera cadence and cost: the numbers docs/autonomous-demo/camera-service.md asks for"
  require_host
  ensure_forward
  item "making sure the camera service is stopped for the baseline"
  camera_service stop step4-stop-first || step_fail "a camera service is running and did not stop"
  item "baseline: ${WINDOW_S}s of tap with the camera service stopped"
  run_helper step4-baseline tap-window "tcp://127.0.0.1:$TAP_PORT" "$WINDOW_S" "baseline, stream off" \
    || step_fail "the baseline tap window did not pass"
  item "starting the camera service (wrist only, preview on)"
  camera_service start step4-start || step_fail "the camera service did not start"
  local status=0 compared=0
  item "the service as it starts"
  run_helper step4-status-start camera-status "tcp://127.0.0.1:$MISSION_PORT" "at the start" || status=$?
  if [ "$status" -eq 0 ]; then
    item "${WINDOW_S}s with the preview subscribed, three mission captures in flight, and the tap watched at the same time"
    run_helper step4-stream stream-window "tcp://127.0.0.1:$TAP_PORT" "tcp://127.0.0.1:$PREVIEW_PORT" "tcp://127.0.0.1:$MISSION_PORT" "$WINDOW_S" 3 || status=$?
  fi
  if [ "$status" -eq 0 ]; then
    item "the service at the end of the window"
    run_helper step4-status-end camera-status "tcp://127.0.0.1:$MISSION_PORT" "at the end" || status=$?
  fi
  if [ "$status" -eq 0 ]; then
    item "the motor host's loop rate with the stream open, against the baseline"
    python3 -c 'import json,sys
baseline = json.load(open(sys.argv[1], encoding="utf-8"))
stream = json.load(open(sys.argv[2], encoding="utf-8"))["tap"]
tolerance = float(sys.argv[3])
before, after = baseline["host_loop_rate_hz"], stream["host_loop_rate_hz"]
print("      host loop rate: %s Hz with the stream off, %s Hz with it on" % (before, after))
print("      longest interval between observations: %s ms off, %s ms on"
      % (baseline["longest_host_interval_ms"], stream["longest_host_interval_ms"]))
if not before or not after:
    print("      one of the two rates is missing")
    raise SystemExit(1)
difference = abs(after - before) / before * 100.0
print("      difference %.2f percent (the tolerance is %.1f percent)" % (difference, tolerance))
raise SystemExit(0 if difference <= tolerance else 1)' \
      "$OUT/step4-baseline.json" "$OUT/step4-stream.json" "$RATE_TOL" >"$OUT/step4-compare.log" 2>&1 || compared=$?
    show "$OUT/step4-compare.log"
  fi
  item "stopping the camera service, to leave the robot computer as this step found it"
  camera_service stop step4-stop || warn "the camera service did not stop; it still owns the wrist camera"
  [ "$status" -eq 0 ] || step_fail "the camera measurements did not pass (the VERDICT line above says why)"
  [ "$compared" -eq 0 ] || step_fail "the motor host's loop rate or its observation continuity changed with the camera stream open"
  step_pass "every mission capture was taken after its request, the preview reached this laptop, and the open stream did not change the motor host's observation rate beyond the tolerance"
}

# ---------------------------------------------------------------------------------------------------------------------
say "kiwi-cake hardware qualification, $TS"
say "This session never enables torque and never commands a motor. Steps 0 to 4 involve no motion."
say "Runbook: docs/autonomous-demo/hardware-qualification.md"
say "Numbers: $OUT"
say "Windows ${WINDOW_S}s, tap gap ${GAP_S}s, rate tolerance ${RATE_TOL} percent"
RAN=""
for step in 0 1 2 3 4; do
  if skipped "$step"; then say ""; say "=== step $step: SKIPPED by argument"; continue; fi
  "step$step"
  RAN="$RAN $step"
done
say ""
if [ -z "$RAN" ]; then
  say "Nothing ran: every step was skipped."
else
  say "PASS: step(s)$RAN. Browser-triggered motion stays disabled until the runbook's own gate is satisfied as well."
fi
say "Transcript: $TRANSCRIPT"
