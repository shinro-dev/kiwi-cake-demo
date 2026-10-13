<!-- Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE. -->

# Qualifying against the NORMAL motor host: what its observation path can and cannot answer

`docs/autonomous-demo/camera-service.md` step 2 asks for the host tap rate, the host's servo read errors and retries,
and the longest gap between two observations. The tap is a PUB socket the BOUNDARY host publishes. The demonstration
runs on the NORMAL host, which is preserved, so the tap does not exist and those figures cannot simply be read. This
file records what the NORMAL host's existing observation path actually is, so that a qualification run against it
measures what it really measures and leaves the rest unverified by name.

## What the installed host loop does

Read from the installed `lekiwi_host.py`, in the interpreter the host runs under, on 2026-09-22. Each iteration, in
order: take one command if one is waiting, trip the base watchdog if no command has arrived in time, then

```
last_observation = robot.get_observation()
...
host.zmq_observation_socket.send_string(json.dumps(last_observation), flags=zmq.NOBLOCK)
```

Three properties follow from that, and they decide what a measurement means.

**EVERY PUBLISHED SAMPLE IS A FRESH SERVO READ.** `get_observation()` is called unconditionally on every iteration and
its result is what is published. There is no cache and no staleness path on the host side: a sample that arrives is a
read that happened. (The stock CLIENT is the thing that caches, serving its last message when a poll returns nothing;
that is a property of the reader, not of this socket.)

**DELIVERY IS LOSSY AND THE LOSS IS INVISIBLE.** The send is `zmq.NOBLOCK`, and on `zmq.Again` the sample is dropped
with a log line and no counter. A dropped observation therefore leaves NO evidence at the receiver.

**A SAMPLE CARRIES NEITHER A SEQUENCE NUMBER NOR A TIMESTAMP.** Measured on the wire: the fields are the six arm joint
positions and the three base velocities, and nothing else. So a receiver cannot tell a dropped sample from a slow one,
and cannot relate a sample to the host's clock at all.

With `--robot.cameras={}`, which is how this host is started, `robot.cameras` is empty and the JPEG encode loop in the
host is a no-op. The host touches no camera.

## The three kinds of measurement, kept apart

**Internal host and servo-read measurements.** Read errors, retries, bus timeouts and the loop period on the host's own
clock. NOT AVAILABLE. The stock host exports none of them; read failures appear only as text in the terminal the host
runs in, which is why the operator watches that terminal during a window.

**End-to-end observation delivery.** Available, at one PULL consumer, on the laptop's clock: how many fresh reads
arrived, the cadence, and the distribution of inter-arrival gaps. This is what a non-interference comparison against
this backend rests on.

**Unavailable with this backend.** Dropped-observation count, host loop rate, servo read health, and any age of a
sample, because none of the three is published.

## The rule this file exists to enforce

RECEIVER CADENCE IS NOT THE HOST LOOP RATE. They are close when nothing is dropped and they diverge exactly when
something is, and nothing here can tell those apart. A qualification record against this backend says "received
cadence" and never "loop rate", and it never subtracts a laptop arrival stamp from a robot-side clock, because the two
have no measured relation.

## How to run a comparison window

One PULL socket connected to the host's observation port, sending nothing, for a bounded window; then the same again
with the only variable changed. It is not a second servo-bus reader: the bus is read once per loop by the host whether
anybody is listening or not. Attaching a consumer does change one thing, and it is worth stating: with no client
attached the host drops every sample, so both windows must have a consumer attached for the comparison to be fair.

Acceptance: the existing bound is `qualify-robot.sh --rate-tolerance-pct`, default 10 percent, applied to the received
cadence. Kernel USB and serial error counts on the robot computer, and the host's own terminal log, are collected
either side of the windows as the available error record.

## The smallest step that would close the gap

A counter inside the host for reads, read failures and loop period, published where a subscriber can see it. That is
what the boundary host's tap already is, so closing this gap means running the boundary host, which replaces the
NORMAL one. That is a separate decision and is not made here.
