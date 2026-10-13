#!/usr/bin/env bash
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# deploy-pi.sh CHECKPOINT_DIR LEROBOT_SRC_DIR: copies vla/ into ~/vla-dev/app/vla on the robot, the checkpoint into
# ~/vla-dev/models/<sha256>/, and the vendored lerobot source into ~/vla-dev/lerobot-src/lerobot, then verifies the
# checkpoint's sha256 on the robot against vla/envelope.json. Nothing outside ~/vla-dev is written.
set -euo pipefail
: "${KC_PI_SSH:?set KC_PI_SSH (source the console env file)}"
CKPT=${1:?checkpoint dir}
LEROBOT=${2:?lerobot source dir (the directory that contains lerobot/)}
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SHA=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["model_sha256"])' "$ROOT/vla/envelope.json")
LOCAL=$(sha256sum "$CKPT/model.safetensors" | cut -d' ' -f1)
[ "$LOCAL" = "$SHA" ] || { echo "REFUSE: $CKPT is $LOCAL, the envelope names $SHA"; exit 3; }
ssh -o BatchMode=yes "$KC_PI_SSH" "mkdir -p ~/vla-dev/app ~/vla-dev/models/$SHA ~/vla-dev/lerobot-src ~/vla-dev/captures"
rsync -a --delete --exclude __pycache__ --exclude tests "$ROOT/vla/" "$KC_PI_SSH:vla-dev/app/vla/"
rsync -a "$CKPT/" "$KC_PI_SSH:vla-dev/models/$SHA/"
rsync -a --delete --exclude __pycache__ "$LEROBOT/lerobot/" "$KC_PI_SSH:vla-dev/lerobot-src/lerobot/"
REMOTE=$(ssh -o BatchMode=yes "$KC_PI_SSH" "sha256sum ~/vla-dev/models/$SHA/model.safetensors" | cut -d' ' -f1)
[ "$REMOTE" = "$SHA" ] || { echo "REFUSE: the robot's copy is $REMOTE"; exit 3; }
echo "deployed: vla/ and model ${SHA:0:16} to ~/vla-dev on the robot"
