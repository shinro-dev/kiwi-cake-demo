# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""demo_runtime.fresh_client: for the EXACT observation the controller consumed, was it a new sample or the cached one?

WHY. The stock LeKiwi client (lerobot 0.6.1, lerobot/robots/lekiwi/lekiwi_client.py) answers get_observation() from
_get_data(), and _get_data() returns the CACHED last state whenever _poll_and_get_latest_message() returns None (nothing new
within the polling timeout), whenever the message is not JSON and whenever processing it raises. The caller cannot tell: the
dict looks the same. A guard that takes several reads a fraction of a second apart and wants them all to show a base at rest
may therefore be counting ONE sample several times. This adapter does not change what is served. It records, per read, what
was served.

HOW, WITHOUT TOUCHING lerobot. make_fresh_client_class(base_cls) returns a subclass that overrides two methods and calls super
in both. _poll_and_get_latest_message() notes that a message arrived, numbers it and reads the host's metadata keys from the
raw string; get_observation() returns the base class's value UNCHANGED and then settles last_stamp() for that read. Nothing in
the installed package is patched, and the lerobot import is deferred to fresh_client(), so this module imports (and its tests
run) on a machine without lerobot or zmq.

HOST METADATA. _remote_state_from_obs() builds the state from the _state_order keys only and skips every other key that is not
a camera. A host may therefore add keys to the observation JSON without breaking a stock client, and a stock client DROPS them.
This adapter reads three such keys from the raw message, where they still exist: kc.session, kc.seq, kc.t_mono_ns. They never
reach the returned observation, so they never become a policy input or a dataset column.

CLOCK DOMAINS. local_rx_mono_ns and age_ns are laptop monotonic. host.sample_mono_ns is the host's own monotonic clock and is
reported as the host sent it. The two are never subtracted from each other here, and nothing downstream may do so either.

WHAT "fresh" MEANS. A read is fresh when, during that read, a message arrived, it was a JSON object, the base class adopted it
as its state (when the base class exposes last_remote_state, as the stock one does) and, when the host stamps, the host stamp
moved on (another session, or a seq above every seq this session has shown). Anything else is a read that served, in whole or
in effect, a sample that was already served.

THE REFUSAL is isolated and OFF by default: set_require_fresh(True) makes get_observation() poll again, for a bounded time and
through the instance's own polling, instead of returning a sample that is not fresh, and raise StaleSourceError when none
arrives. With it off, this class returns what the base class returns, every time. Tolerances of the guards that consume the
observation are not this module's business and are not touched.

One instance, one thread: the stock client is not thread safe and neither is the bookkeeping here.
"""
import copy
import json
import math
import os
import re
import time
from typing import Callable, Optional

HOST_SESSION_KEY, HOST_SEQ_KEY, HOST_SAMPLE_KEY = "kc.session", "kc.seq", "kc.t_mono_ns"
HOST_KEYS = (HOST_SESSION_KEY, HOST_SEQ_KEY, HOST_SAMPLE_KEY)

HOST_STAMPED, HOST_DOES_NOT_STAMP, NO_MESSAGE_YET = "host_stamped", "host_does_not_stamp", "no_message_yet"
AVAILABILITIES = (HOST_STAMPED, HOST_DOES_NOT_STAMP, NO_MESSAGE_YET)

# Mirrors of packages/demo-contracts/schema/v1/common.schema.json, pinned against the schema file by tests/test_fresh_client.py.
# Copied, not imported: this module is imported next to the controller and must not grow a dependency to describe a read.
SESSION_PATTERN = r"^[A-Za-z0-9_.-]{1,64}$"  # $defs/source_stamp/properties/session/pattern
CLOCK_LAPTOP, CLOCK_PI = "laptop_monotonic", "pi_monotonic"  # two of $defs/clock_domain/enum
_SESSION = re.compile(SESSION_PATTERN)

DEFAULT_MAX_WAIT_S = 0.5
# Between two polls of a refusing read. The stock poll already blocks for polling_timeout_ms, so this only matters for a base
# class whose poll returns at once; without it such a base would spin a core for max_wait_s.
REPOLL_INTERVAL_S = 0.002
_MISSING = object()


class StaleSourceError(RuntimeError):
    """The observation source went stale while freshness was required. .stamp is last_stamp() of the read that gave up."""

    def __init__(self, message: str, stamp: dict):
        super().__init__(message)
        self.stamp = stamp


def _non_negative_int(value) -> Optional[int]:
    # bool is an int in Python, and True is not a sequence number. A float is refused even when it is whole: the host writes
    # these with json.dumps from Python ints, so a float means something else wrote the key.
    return value if type(value) is int and value >= 0 else None


def parse_host_metadata(raw) -> tuple:
    """(is_json_object, host, rejected) for one raw observation message. Never raises.

    host is {"session", "seq", "sample_mono_ns"} when the message carries a usable IDENTITY: a session that fits the contract's
    pattern AND a non-negative integer seq. A stamp that cannot say which sample this is cannot prove a sample is new, so it
    counts as no stamp (host None). sample_mono_ns alone may be None: a missing or malformed sample time costs the time, not
    the identity. rejected is True when at least one kc.* key was present and host is None, so that a host which stamps badly
    can be told from a host which does not stamp.
    """
    try:
        message = json.loads(raw)
    except (TypeError, ValueError, RecursionError):  # json.JSONDecodeError is a ValueError; bytes that are not UTF-8 are too
        return False, None, False
    if not isinstance(message, dict):
        return False, None, False  # valid JSON, but the base class cannot read a state out of it either
    if not any(key in message for key in HOST_KEYS):
        return True, None, False
    session, seq = message.get(HOST_SESSION_KEY), _non_negative_int(message.get(HOST_SEQ_KEY))
    if not isinstance(session, str) or not _SESSION.fullmatch(session) or seq is None:
        return True, None, True
    return True, {"session": session, "seq": seq, "sample_mono_ns": _non_negative_int(message.get(HOST_SAMPLE_KEY))}, False


def source_stamp_from(stamp: dict, local_rx_session: str) -> Optional[dict]:
    """last_stamp() -> the contract's common source_stamp ({session, seq, sample_mono_ns, clock_domain}), or None.

    host_stamped        -> the host's own identity and sample time, clock_domain pi_monotonic.
    host_does_not_stamp -> a RECEIVER-SIDE stamp: this adapter's receive session, its receive counter and its receive time,
                           clock_domain laptop_monotonic. Every field then comes from one source (the laptop's receive side)
                           and the clock domain says so. It is not the host's sample time and must not be read as one; the
                           emitter should record an evidence.gap 'source_stamp_missing' next to it.
    no_message_yet      -> None. There is no sample to identify.

    The contract's source_stamp has NO field for fresh, for age_ns, for the receive time of a host-stamped sample or for
    availability, and none is invented here. Freshness is still recoverable from the stamps alone: two consecutive reads that
    carry the same (session, seq) consumed the same sample.
    """
    if stamp["availability"] == NO_MESSAGE_YET:
        return None
    host = stamp["host"]
    if host is not None:
        return {"session": host["session"], "seq": host["seq"], "sample_mono_ns": host["sample_mono_ns"], "clock_domain": CLOCK_PI}
    return {"session": local_rx_session, "seq": stamp["local_rx_seq"], "sample_mono_ns": stamp["local_rx_mono_ns"], "clock_domain": CLOCK_LAPTOP}


def _empty_stamp() -> dict:
    return {"fresh": False, "local_rx_seq": 0, "local_rx_mono_ns": None, "age_ns": None, "host": None, "availability": NO_MESSAGE_YET}


def make_fresh_client_class(base_cls, *, clock_ns: Callable[[], int] = time.monotonic_ns, sleep: Callable[[float], None] = time.sleep):
    """A subclass of base_cls that reports, per read, whether the observation it served was new.

    base_cls needs two methods: _poll_and_get_latest_message() -> str | None and get_observation(), the second reaching the
    first. The stock LeKiwiClient is one such class; a scripted fake is another, which is how the tests run without zmq.
    clock_ns and sleep are injectable for the same reason. clock_ns must be monotonic: it is only ever compared with itself.
    """

    class FreshClient(base_cls):
        def __init__(self, *args, **kwargs):
            # Before super().__init__: a base class that polls while it is being constructed must find the bookkeeping there.
            self._kc_rx_count = 0  # every message received, adopted or not
            self._kc_pending = None  # what the poll of the read in progress received
            self._kc_served = None  # the last message the base class ADOPTED: (rx_seq, rx_mono_ns, host)
            self._kc_host_high = None  # (session, highest seq adopted in it)
            self._kc_stamp = _empty_stamp()
            self._kc_require_fresh, self._kc_max_wait_s = False, DEFAULT_MAX_WAIT_S
            self.host_stamps_rejected = 0  # messages whose kc.* keys were present and unusable; a host bug, worth a look
            # Minted here, never derived from the address or the robot id: it ends up in records a browser can read.
            self.local_rx_session = "laptop-rx-" + os.urandom(6).hex()
            super().__init__(*args, **kwargs)

        def _poll_and_get_latest_message(self):
            message = super()._poll_and_get_latest_message()
            if message is not None:
                self._kc_rx_count += 1
                # The message is parsed a second time by the base class. That is the price of not patching it: the base
                # class's own parse throws the kc.* keys away before anything outside it can see them.
                is_object, host, rejected = parse_host_metadata(message)
                self.host_stamps_rejected += 1 if rejected else 0
                self._kc_pending = (self._kc_rx_count, clock_ns(), is_object, host)
            return message

        def _kc_read_once(self):
            self._kc_pending = None
            before = getattr(self, "last_remote_state", _MISSING)  # held, not id(): a freed dict's id can be handed out again
            observation = super().get_observation()
            pending, self._kc_pending = self._kc_pending, None
            adopted = pending is not None and pending[2]
            if adopted and before is not _MISSING:
                # The stock _get_data() assigns a NEW dict to last_remote_state only when it adopted the message; when
                # processing raised, it kept the old one and served it. Should a later lerobot update that dict in place,
                # this reads "not adopted" and every read reports stale: wrong in the direction that stops, not the one that moves.
                adopted = getattr(self, "last_remote_state", _MISSING) is not before
            fresh = False
            if adopted:
                rx_seq, rx_mono_ns, _, host = pending
                high = self._kc_host_high
                # A 'new' message that repeats the host's seq, or goes back, is the host sending a sample it already sent.
                # A different session is a restarted host, whose seq starts over; that is new. The comparison is against the
                # HIGHEST seq of the session, not the last one: after 5, 3 the message 4 is not news either.
                fresh = host is None or high is None or host["session"] != high[0] or host["seq"] > high[1]
                if host is not None and fresh:
                    self._kc_host_high = (host["session"], host["seq"])
                self._kc_served = (rx_seq, rx_mono_ns, host)
            if self._kc_served is None:
                self._kc_stamp = _empty_stamp()
            else:
                rx_seq, rx_mono_ns, host = self._kc_served
                # age_ns is settled HERE, at the read, not when last_stamp() is called: it is the age of the sample at the
                # moment the controller consumed it. Both terms are this laptop's monotonic clock.
                self._kc_stamp = {"fresh": fresh, "local_rx_seq": rx_seq, "local_rx_mono_ns": rx_mono_ns, "age_ns": max(0, clock_ns() - rx_mono_ns),
                                  "host": host, "availability": HOST_STAMPED if host is not None else HOST_DOES_NOT_STAMP}
            return observation

        def get_observation(self):
            observation = self._kc_read_once()
            if not self._kc_require_fresh or self._kc_stamp["fresh"]:
                return observation
            deadline_ns = clock_ns() + int(self._kc_max_wait_s * 1e9)
            while not self._kc_stamp["fresh"]:
                if clock_ns() >= deadline_ns:
                    last = "no message has been received yet" if self._kc_stamp["local_rx_mono_ns"] is None else f"the last sample served was received {self._kc_stamp['age_ns'] / 1e9:.3f} s before this read gave up"
                    raise StaleSourceError(f"the observation source went stale: no new host observation within {self._kc_max_wait_s:.3f} s ({last}). "
                                           "Nothing further should be commanded until the source is fresh again.", self.last_stamp())
                sleep(REPOLL_INTERVAL_S)
                observation = self._kc_read_once()
            return observation

        def last_stamp(self) -> dict:
            """Describes the read get_observation() just served. A copy: the caller may keep it or change it.

            {fresh, local_rx_seq, local_rx_mono_ns, age_ns, host: {session, seq, sample_mono_ns} | None, availability}.
            local_rx_seq and local_rx_mono_ns belong to the sample that was SERVED, so a cached read repeats the numbers of
            the read before it; a gap in local_rx_seq is a message that arrived and that the base class could not use.
            age_ns is None (not a made-up number) while no message has been received.
            """
            return copy.deepcopy(self._kc_stamp)

        def to_source_stamp(self) -> Optional[dict]:
            return source_stamp_from(self._kc_stamp, self.local_rx_session)

        def set_require_fresh(self, enabled: bool, max_wait_s: float = DEFAULT_MAX_WAIT_S) -> None:
            """Off by default. On: a read that would serve a sample already served polls again instead, for at most
            max_wait_s, and raises StaleSourceError when nothing new arrives. 0 means one poll and no waiting."""
            if not isinstance(enabled, bool):  # "false" is truthy; a refusal switch is not the place for truthiness
                raise TypeError("enabled must be a bool")
            if isinstance(max_wait_s, bool) or not isinstance(max_wait_s, (int, float)) or not math.isfinite(max_wait_s) or max_wait_s < 0:
                raise ValueError("max_wait_s must be a finite number of seconds, 0 or more")
            self._kc_require_fresh, self._kc_max_wait_s = enabled, float(max_wait_s)

        @property
        def require_fresh(self) -> bool:
            return self._kc_require_fresh

    FreshClient.__name__ = FreshClient.__qualname__ = f"Fresh{base_cls.__name__}"
    return FreshClient


def fresh_client(remote_ip: str, robot_id: str, **opts):
    """The stock LeKiwi client with the per-read stamp, constructed the way the scripts here construct the stock one.
    Not connected: the caller calls connect(), as with the stock class.

    opts: require_fresh (default False) and max_wait_s go to set_require_fresh(); every other option goes to
    LeKiwiClientConfig unchanged (polling_timeout_ms, cameras, ...). lerobot is imported HERE, not at module import.
    """
    from lerobot.robots.lekiwi import LeKiwiClient, LeKiwiClientConfig

    require_fresh, max_wait_s = opts.pop("require_fresh", False), opts.pop("max_wait_s", DEFAULT_MAX_WAIT_S)
    client = make_fresh_client_class(LeKiwiClient)(LeKiwiClientConfig(remote_ip=remote_ip, id=robot_id, **opts))
    client.set_require_fresh(require_fresh, max_wait_s)
    return client
