#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""base_rest: is the base at rest? ONE rule, judged per wheel, in the unit the host really measures.

WHAT THE HOST REPORTS. The LeKiwi host turns the three wheels' Present_Velocity registers into a body velocity
(x.vel m/s, y.vel m/s, theta.vel deg/s) with lerobot's LeKiwi._wheel_raw_to_body: raw steps/s to deg/s at 4096 steps per
revolution, times the wheel radius 0.05 m, through the inverse of the matrix whose rows are (cos a, sin a, 0.125 m) for
the wheel angles 240, 0 and 120 deg minus 90 (lekiwi.py lines 226-230 and 297-345 of the vendored lerobot). The
register moves in steps of 50 steps/s, one encoder step per 20 ms speed window: that is ONE VELOCITY COUNT. Every one
of the 995 value-bearing base reads recorded from 2026-09-19 to 2026-09-27 decomposes into whole counts on the three
wheels, with a largest residual of 4.4e-16 counts.

WHAT REST LOOKS LIKE. With the wheels in the zero-speed hold, and before it with their torque off, a wheel that is not
driven reads zero or one count of either sign, and arm motion makes that happen on one wheel or on two at the same
instant. In 975 recorded reads at rest no wheel ever read two counts. A read of one count on each of two wheels is,
seen on the body axes, up to 0.0044 m/s or 1.17 deg/s, which the former per-axis band (sized for ONE wheel at one count)
called motion: that stopped run console-20260927T142146Z-m6ed2dd9 holding the block, with no base motion commanded.

THE RULE. A read is at rest when no wheel reads more than REST_MAX_COUNTS (one) count. It is the operator's decision of
2026-09-19 (one count is measurement noise, about two counts is not zero) applied per wheel, where the count is, instead
of per body axis, where two wheels' counts add up (operator ruling 2026-09-27, docs/autonomous-demo/decision-log.md).
Reads are whole counts, so the comparison is made at the midpoint between one and two counts: a rounded or re-encoded
value of one count stays at rest and two counts never is.

WHAT IT CATCHES AND WHAT IT DOES NOT. A commanded base motion reads far above it: the only base speed any recorded pulse
used, 0.05 m/s, is 11.3 counts on each driven wheel, and the pad-measured pulse rates (47.9 to 64.0 mm/s) are 10.8 to
14.5. A decelerating base is not at rest until every wheel is down to one count. A STEADY motion slower than one count
per wheel (about 4.4 mm/s forward, 3.8 mm/s sideways, 1.8 deg/s turning) reads like rest on every read, and one
between one and two counts reads like rest on some reads: this rule does not see it. Nothing on this base commands such
a motion; what the base really did is measured separately, against the pad.

Plain python on purpose: every site that judges the base at rest imports this module, on the laptop and in the tests.
"""
import math

WHEELS = ("left", "back", "right")  # the order of lerobot's base motors, and of every tuple in this module
WHEEL_ANGLES_DEG = (240.0 - 90.0, 0.0 - 90.0, 120.0 - 90.0)  # lekiwi.py: np.radians(np.array([240, 0, 120]) - 90)
WHEEL_RADIUS_M = 0.05
BASE_RADIUS_M = 0.125
STEPS_PER_REV = 4096
VELOCITY_COUNT_STEPS_PER_S = 50  # one encoder step per 20 ms: the register's resolution, measured on every recorded read
REST_MAX_COUNTS = 1  # at rest: no wheel reads more than one count (none of 975 recorded reads at rest did)
BODY_KEYS = (("x.vel", "y.vel", "theta.vel"), ("x_vel", "y_vel", "theta_vel"))  # the two key forms the sites read

_STEPS_PER_RAD = STEPS_PER_REV / (2.0 * math.pi)


def _row(angle_deg: float) -> tuple:
    angle = math.radians(angle_deg)
    return (math.cos(angle), math.sin(angle), BASE_RADIUS_M)


_ROWS = tuple(_row(angle) for angle in WHEEL_ANGLES_DEG)


def wheel_counts(x_mps: float, y_mps: float, theta_dps: float) -> tuple:
    """The (left, back, right) wheel speeds, in velocity counts, that the host's body velocity was computed from."""
    body = (float(x_mps), float(y_mps), math.radians(float(theta_dps)))
    counts = []
    for row in _ROWS:
        rim_mps = row[0] * body[0] + row[1] * body[1] + row[2] * body[2]
        counts.append(rim_mps / WHEEL_RADIUS_M * _STEPS_PER_RAD / VELOCITY_COUNT_STEPS_PER_S)
    return tuple(counts)


def body_velocity(counts) -> tuple:
    """The (x.vel m/s, y.vel m/s, theta.vel deg/s) the host reports for whole wheel counts (left, back, right): its own
    inverse-matrix computation, so a test can state a read by the counts it is."""
    rim = [float(c) * VELOCITY_COUNT_STEPS_PER_S / _STEPS_PER_RAD * WHEEL_RADIUS_M for c in counts]
    solved = _solve3([list(row) for row in _ROWS], rim)
    return (solved[0], solved[1], math.degrees(solved[2]))


def _solve3(matrix: list, rhs: list) -> list:
    """Gaussian elimination with partial pivoting for the 3x3 wheel matrix (no numpy in this module)."""
    a = [row[:] + [value] for row, value in zip(matrix, rhs)]
    for col in range(3):
        pivot = max(range(col, 3), key=lambda r: abs(a[r][col]))
        a[col], a[pivot] = a[pivot], a[col]
        for r in range(3):
            if r != col:
                factor = a[r][col] / a[col][col]
                a[r] = [value - factor * base for value, base in zip(a[r], a[col])]
    return [a[i][3] / a[i][i] for i in range(3)]


def at_rest(x_mps: float, y_mps: float, theta_dps: float) -> bool:
    """True when no wheel reads more than REST_MAX_COUNTS velocity counts. A value that is not a finite number is not rest."""
    values = (float(x_mps), float(y_mps), float(theta_dps))
    if not all(math.isfinite(value) for value in values):
        return False
    return all(abs(count) < REST_MAX_COUNTS + 0.5 for count in wheel_counts(*values))


def reads_at_rest(base: dict) -> bool:
    """at_rest for a read in either key form the sites use ("x.vel" from the host, "x_vel" from the hybrid runner's
    adapter). A read that lacks any of the three velocities raises KeyError: it is refused, never completed with zero."""
    for keys in BODY_KEYS:
        if keys[0] in base:
            return at_rest(base[keys[0]], base[keys[1]], base[keys[2]])
    raise KeyError(f"a base read needs {BODY_KEYS[0]} or {BODY_KEYS[1]}, got {sorted(base)}")
