# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""demo_runtime: the explicit runtime configuration of the demo and the preflight that checks it before any movement.
See packages/demo-runtime/README.md. Standard library only, so it imports (and can refuse) where numpy or OpenCV do not."""
from .config import (CONFIG_ENV_VAR, CameraConfig, CaptureProvider, ConfigError, HostConfig, ModelConfig, RuntimeConfig, TapConfig, add_config_argument,
                     config_digest, legacy_layout, load_config, resolve_config_path, sanitized_summary)
from .preflight import (Action, ActionKind, ActionRefused, PreflightCheck, PreflightInputError, PreflightReport, input_digests, parse_action, run_preflight)

__all__ = [
    "CONFIG_ENV_VAR", "Action", "ActionKind", "ActionRefused", "CameraConfig", "CaptureProvider", "ConfigError", "HostConfig", "ModelConfig",
    "PreflightCheck", "PreflightInputError", "PreflightReport", "RuntimeConfig", "TapConfig", "add_config_argument", "config_digest", "input_digests",
    "legacy_layout", "load_config", "parse_action", "resolve_config_path", "run_preflight", "sanitized_summary",
]
