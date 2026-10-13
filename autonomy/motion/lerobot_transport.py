#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""LeKiwiTransport: the real BaseMotionTransport adapter over LeRobot's
LeKiwiClient. Source-verified against the pinned lerobot 0.6.1 install at
~/lerobot/src/lerobot/robots/lekiwi/{lekiwi_client,lekiwi}.py -- nothing
here is guessed:

- action keys are exactly "x.vel", "y.vel", "theta.vel"
  (lekiwi_client.py's own _from_keyboard_to_base_action comments them:
  x.vel/y.vel in m/s, theta.vel in DEGREES/s -- NOT rad/s);
- lekiwi.py's host-side send_action indexes all three directly
  (base_goal_vel["x.vel"] etc.), so every action dict sent MUST include all
  three keys or the host raises KeyError; arm ".pos" keys are filtered, not
  indexed, so omitting them is safe;
- LeKiwiClient.connect()/send_action()/disconnect() and .is_connected are
  the only methods this adapter needs (see lekiwi_client.py).

This module is NOT instantiated anywhere in this codebase. Constructing
LeKiwiTransport requires an explicit remote_ip and robot id that nothing
here supplies; the lerobot import itself is deferred into __init__ so that
importing this MODULE (e.g. for the class to exist behind the transport
interface) never requires lerobot to be installed or touches any device.
Future real hardware integration is a separate, explicit command -- see
bin/laptop/manual_target_harness.py's --execute flag, itself not run by
anything in this repository.
"""

import contextlib
import math

from transport import BaseMotionTransport

# CLIENT FACTORY. A factory is a callable (remote_ip, robot_id) -> an UNCONNECTED client with the stock client's interface
# (connect, disconnect, is_connected, send_action, get_observation). None, the default everywhere, means the stock
# LeKiwiClient built exactly as before. It exists for ONE caller: the single-cube cycle hands in the client class that also
# reports, per read, whether the observation it served was new (demo_runtime.fresh_client), so that the reads a base
# primitive takes to confirm its stop carry a stamp like every other read of that run. It changes which class answers
# get_observation(); what is sent, and when, is this adapter's and the gateway's business and stays as it was.
#
# LEASE METADATA, the SAME seam. A motor host started with bin/pi/host_boundary.py hands its loop only commands that carry
# the live lease as two numeric keys. The metadata is a mapping (or a callable returning one, or None), and where it is not
# None every action this transport sends carries it, attached by demo_runtime.lease_client.attach_lease_metadata on a COPY
# at the moment the action leaves the client: after the gateway clamped it, so nothing else in the payload changes. None,
# the default everywhere, sends exactly the dict this adapter always sent.
_scoped_client_factory = None
_scoped_lease_metadata = None


@contextlib.contextmanager
def client_factory_scope(factory, lease_metadata=None):
    """While the block runs, a LeKiwiTransport constructed WITHOUT its own factory uses this one, and without its own lease
    metadata uses this. For a primitive that builds its transport itself and cannot be handed either
    (bin/laptop/manipulation/run_hybrid_pick_place.py builds one per pulse). Scoped, not a permanent module setting: the
    previous values are restored on the way out, whatever happened inside."""
    global _scoped_client_factory, _scoped_lease_metadata
    previous = _scoped_client_factory, _scoped_lease_metadata
    _scoped_client_factory, _scoped_lease_metadata = factory, lease_metadata
    try:
        yield
    finally:
        _scoped_client_factory, _scoped_lease_metadata = previous


def _attach_lease_metadata(client, metadata):
    """demo_runtime.lease_client.attach_lease_metadata, imported on first use from THIS checkout (the code that was
    reviewed, not an installed copy). Nothing of the lease is reimplemented here: that module owns the two keys, the copy
    and the detach. Deferred like the lerobot import above, so importing this module still needs nothing."""
    import importlib
    import sys
    from pathlib import Path

    package = str(Path(__file__).resolve().parents[2] / "packages" / "demo-runtime")
    if package not in sys.path:
        sys.path.insert(0, package)
    return importlib.import_module("demo_runtime.lease_client").attach_lease_metadata(client, metadata)


class LeKiwiTransport(BaseMotionTransport):
    def __init__(self, remote_ip: str, robot_id: str, client_factory=None, lease_metadata=None):
        factory = client_factory if client_factory is not None else _scoped_client_factory
        if factory is not None:
            self._client = factory(remote_ip, robot_id)
        else:
            # Deferred import: constructing this class is the only thing that
            # requires lerobot to be installed; importing this module does not.
            from lerobot.robots.lekiwi import LeKiwiClient, LeKiwiClientConfig

            self._client = LeKiwiClient(LeKiwiClientConfig(remote_ip=remote_ip, id=robot_id))
        metadata = lease_metadata if lease_metadata is not None else _scoped_lease_metadata
        # The detach is kept and never called: a pulse throws its client away with the transport. It exists so that a
        # reader can see the wrap is undoable and that nothing else on the client was touched.
        self._detach_lease = _attach_lease_metadata(self._client, metadata) if metadata is not None else None
        self._explicitly_connected = False

    def connect(self):
        self._client.connect()
        self._explicitly_connected = True

    def disconnect(self):
        self._client.disconnect()
        self._explicitly_connected = False

    def send_velocity(self, vx_mps: float, vy_mps: float, omega_rps: float) -> None:
        omega_degps = omega_rps * 180.0 / math.pi  # theta.vel is degrees/s, not rad/s -- source-verified above
        self._client.send_action({"x.vel": vx_mps, "y.vel": vy_mps, "theta.vel": omega_degps})

    def send_zero(self) -> None:
        self._client.send_action({"x.vel": 0.0, "y.vel": 0.0, "theta.vel": 0.0})

    def is_connected(self) -> bool:
        return self._explicitly_connected and self._client.is_connected
