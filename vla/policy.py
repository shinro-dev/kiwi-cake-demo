# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""vla.policy: the SmolVLA inference core of the console's VLA beat, on the robot CPU (operator ruling, 2026-10-08).

One call, one chunk: the wrist frame exactly as the guided recorder stored it (H 640 x W 480 x 3, RGB uint8, the
robot's ROTATE_90 already applied) plus the six arm readings in host degrees, in ACTION_KEYS order, return the policy's
next chunk_size actions (chunk_size x 6, host degrees, same order). The checkpoint's own saved pre/post processors do
the work: the wrist -> camera1 rename, the normalisation with the training set's stats, and the unnormalisation.
`identity()` names the checkpoint by the sha256 of its model.safetensors, so a caller can refuse a server that serves
another policy.
"""
import hashlib
import time
from copy import copy as shallow_copy
from pathlib import Path

import numpy as np

ACTION_KEYS = ("arm_shoulder_pan.pos", "arm_shoulder_lift.pos", "arm_elbow_flex.pos", "arm_wrist_flex.pos",
               "arm_wrist_roll.pos", "arm_gripper.pos")
WRIST_KEY = "observation.images.wrist"
WRIST_SHAPE = (640, 480, 3)
TASK = "Pick up the yellow cube and place it in the collection area."


def checkpoint_sha256(checkpoint_dir) -> str:
    h = hashlib.sha256()
    with open(Path(checkpoint_dir) / "model.safetensors", "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


class VlaPolicy:
    def __init__(self, checkpoint_dir, device: str = "cpu", checkpoint_only: bool = True, num_steps=None, threads=None):
        """checkpoint_only: build the VLM from its config alone (fp32) and take every weight from the checkpoint, so
        the base SmolVLM2 weights need not be on the machine (the robot); otherwise the base VLM is loaded in bf16
        first, as training did, and the checkpoint overrides it."""
        import torch
        from lerobot.configs.policies import PreTrainedConfig
        from lerobot.policies import make_pre_post_processors
        from lerobot.policies.smolvla import SmolVLAPolicy

        self.torch = torch
        self.checkpoint_dir = str(Path(checkpoint_dir).resolve())
        self.sha256 = checkpoint_sha256(self.checkpoint_dir)
        cfg = PreTrainedConfig.from_pretrained(self.checkpoint_dir)
        cfg.device = device
        if checkpoint_only:
            cfg.load_vlm_weights = False
        self.policy = SmolVLAPolicy.from_pretrained(self.checkpoint_dir, config=cfg)
        self.policy.to(device)
        if device == "cpu":
            self.policy.float()  # bf16 matmul has no fast CPU kernel on the robot (aarch64): it falls back, about 10x slower
        self.policy.eval()
        if threads:
            torch.set_num_threads(int(threads))
        if num_steps:
            self.policy.config.num_steps = int(num_steps)
            if hasattr(self.policy.model, "config"):
                self.policy.model.config.num_steps = int(num_steps)
        self.device = torch.device(device)
        self.preprocessor, self.postprocessor = make_pre_post_processors(
            policy_cfg=self.policy.config, pretrained_path=self.checkpoint_dir,
            preprocessor_overrides={"device_processor": {"device": device}})
        self.chunk_size = int(self.policy.config.chunk_size)

    def identity(self) -> dict:
        return {"checkpoint_dir": self.checkpoint_dir, "model_sha256": self.sha256, "chunk_size": self.chunk_size,
                "action_keys": list(ACTION_KEYS), "wrist_shape": list(WRIST_SHAPE), "task": TASK}

    def chunk(self, wrist_rgb: np.ndarray, state_deg, task: str = TASK) -> np.ndarray:
        from lerobot.policies.utils import prepare_observation_for_inference

        if tuple(wrist_rgb.shape) != WRIST_SHAPE or wrist_rgb.dtype != np.uint8:
            raise ValueError(f"wrist frame must be {WRIST_SHAPE} uint8 (the recorder's frame), got {wrist_rgb.shape} {wrist_rgb.dtype}")
        state = np.asarray(state_deg, dtype=np.float32).reshape(6)
        obs = {WRIST_KEY: np.ascontiguousarray(wrist_rgb), "observation.state": state}
        with self.torch.inference_mode():
            batch = prepare_observation_for_inference(shallow_copy(obs), self.device, task, "lekiwi_client")
            batch = self.preprocessor(batch)
            out = self.policy.predict_action_chunk(batch)
            out = self.postprocessor(out)
        return out.reshape(-1, out.shape[-1])[:, :6].detach().float().cpu().numpy()


    def warm_up(self) -> int:
        start = time.perf_counter()
        self.chunk(np.zeros(WRIST_SHAPE, np.uint8), [-9.0, -79.5, 34.5, 67.3, -96.6, 79.6])
        return round((time.perf_counter() - start) * 1000)
