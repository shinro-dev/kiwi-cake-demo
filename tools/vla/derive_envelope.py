#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""derive_envelope.py: the VLA stage's limits, derived from the recorded demonstrations and nothing else.

Reads a LeRobot dataset root (the trimmed training set) and writes vla/envelope.json:
  joint_min_deg / joint_max_deg  per action joint, the recorded range widened by JOINT_MARGIN_DEG;
  floor_tool_z_m                 the lowest recorded tool height (forward kinematics of every action) minus FLOOR_MARGIN_M;
  start_pose_deg                 the median first action of every take (the recorder's start pose as executed);
  wrist_roll_deg                 the median recorded wrist roll (frozen in every take);
  end_pose_min_deg / _max_deg    pan, lift, elbow, wrist flex where every take ENDED, widened by END_MARGIN_DEG. The takes
                                 end with the block held at the pad and contain no release (the recorder's return opened
                                 the jaws, unrecorded), so the episode releases by rule only inside this region;
  grasp_lift_max_deg             the highest lift at which any take closed the jaws (the end region must lie above it);
  model_sha256 / dataset         provenance.
  python3 tools/vla/derive_envelope.py --dataset DIR --model-sha256 HEX --out vla/envelope.json
"""
import argparse
import glob
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "autonomy/manipulation"))
import cartesian_lift  # noqa: E402

JOINTS = ("shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper")
JOINT_MARGIN_DEG = 3.0
FLOOR_MARGIN_M = 0.005
END_MARGIN_DEG = 5.0
CLOSED_BELOW = 73.0


def derive(actions_by_take: list) -> dict:
    allact = np.concatenate(actions_by_take)
    zs = [cartesian_lift.forward_kinematics(dict(zip(JOINTS[:5], (*row[:4], 0.0))))[2, 3] for row in allact[::3]]
    starts = np.stack([take[0] for take in actions_by_take])
    ends = np.stack([take[-1][:4] for take in actions_by_take])
    closes = [take[np.argmax(take[:, 5] < CLOSED_BELOW)] for take in actions_by_take if (take[:, 5] < CLOSED_BELOW).any()]
    return {"joints": list(JOINTS),
            "joint_min_deg": [round(float(v) - JOINT_MARGIN_DEG, 2) for v in allact.min(0)],
            "joint_max_deg": [round(float(v) + JOINT_MARGIN_DEG, 2) for v in allact.max(0)],
            "floor_tool_z_m": round(float(min(zs)) - FLOOR_MARGIN_M, 4),
            "start_pose_deg": [round(float(v), 2) for v in np.median(starts, 0)],
            "wrist_roll_deg": round(float(np.median(allact[:, 4])), 2),
            "end_pose_min_deg": [round(float(v) - END_MARGIN_DEG, 2) for v in ends.min(0)],
            "end_pose_max_deg": [round(float(v) + END_MARGIN_DEG, 2) for v in ends.max(0)],
            "grasp_lift_max_deg": round(float(max(c[1] for c in closes)), 2),
            "takes": len(actions_by_take), "frames": int(len(allact))}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--model-sha256", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    df = pd.concat([pd.read_parquet(f, columns=["episode_index", "frame_index", "action"])
                    for f in sorted(glob.glob(f"{a.dataset}/data/**/*.parquet", recursive=True))])
    takes = [np.stack(g.sort_values("frame_index")["action"].values) for _, g in df.groupby("episode_index")]
    env = derive(takes)
    env.update({"model_sha256": a.model_sha256, "dataset": Path(a.dataset).name,
                "joint_margin_deg": JOINT_MARGIN_DEG, "floor_margin_m": FLOOR_MARGIN_M, "end_margin_deg": END_MARGIN_DEG})
    Path(a.out).write_text(json.dumps(env, indent=1) + "\n")
    print(json.dumps(env))
    return 0


if __name__ == "__main__":
    sys.exit(main())
