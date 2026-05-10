#!/usr/bin/env bash
# Bootstrap the head node and launch the v2 vEcoli workflow on AWS Batch.
# Runs ON the head node (AL2023 ARM64) after setup_head_node.sh.
#
# Pre-req: composite branch must be pushed to origin so it can be cloned here.
#
# Idempotent: re-running picks up where it left off. After the docker-group
# add on first run the script exits and asks you to run it again in a fresh
# shell so the new group membership takes effect.

set -euo pipefail

VECOLI_REPO="${VECOLI_REPO:-https://github.com/vivarium-collective/vEcoli.git}"
VECOLI_BRANCH="${VECOLI_BRANCH:-composite}"
VECOLI_DIR="${HOME}/vEcoli"
# Override via env to launch a different workflow (e.g. v1 vs v2):
#   CONFIG_RELPATH=configs/comparison_10s_16g_v1_aws.json
#   SESSION=vecoli-v1
CONFIG_RELPATH="${CONFIG_RELPATH:-configs/comparison_10s_16g_v2_aws.json}"
SESSION="${SESSION:-vecoli-v2}"
REGION="us-gov-west-1"

# --- 1. system packages -----------------------------------------------------
sudo dnf -y update
sudo dnf -y install git java-21-amazon-corretto-headless docker tmux \
                    gcc gcc-c++ make

# --- 2. docker --------------------------------------------------------------
sudo systemctl enable --now docker
if ! id -nG | tr ' ' '\n' | grep -qx docker; then
  sudo usermod -aG docker "$USER"
  echo
  echo "User added to docker group. Log out and back in (or run 'newgrp docker')"
  echo "and rerun this script. Stopping here — the docker group needs to be live"
  echo "before the ECR push step can run."
  exit 0
fi

# --- 3. AWS region default --------------------------------------------------
aws configure set region "$REGION"

# --- 4. nextflow ------------------------------------------------------------
if ! command -v nextflow >/dev/null; then
  curl -s https://get.nextflow.io | bash
  sudo mv nextflow /usr/local/bin/
  sudo chmod +x /usr/local/bin/nextflow
fi
nextflow -v

# --- 5. uv ------------------------------------------------------------------
if ! command -v uv >/dev/null; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
export PATH="$HOME/.local/bin:$PATH"
hash -r

# --- 6. fetch vEcoli code ---------------------------------------------------
# Two modes:
#   SKIP_GIT_RESET=1 (default for ``run launch <variant>``):
#     CLI rsynced local working tree into ~/vEcoli before invoking us.
#     Trust those files — don't touch git. Lets local edits run without
#     requiring commit + push to origin/composite.
#   SKIP_GIT_RESET=0 (default when running bootstrap_head.sh directly,
#     or via ``run launch <variant> --from-origin``):
#     Clone if missing, then fetch + checkout + hard-reset to
#     origin/<branch>. Use this for clean / production runs.
if [[ "${SKIP_GIT_RESET:-0}" == "1" ]]; then
  if [[ ! -d "$VECOLI_DIR" ]]; then
    echo "$VECOLI_DIR missing on head — first launch needs --from-origin to clone." >&2
    exit 1
  fi
  cd "$VECOLI_DIR"
  if [[ -d .git ]]; then
    echo "vEcoli at $(git rev-parse --short HEAD 2>/dev/null || echo "?") (rsynced from CLI; skipping git reset)"
  else
    echo "vEcoli (rsynced from CLI; no .git on head)"
  fi
else
  if [[ ! -d "$VECOLI_DIR/.git" ]]; then
    git clone --filter=blob:none "$VECOLI_REPO" "$VECOLI_DIR"
  fi
  cd "$VECOLI_DIR"
  # Re-point origin if it's pointing at a different remote (e.g. earlier
  # bootstrap cloned upstream by mistake). Idempotent.
  current_origin=$(git remote get-url origin 2>/dev/null || true)
  if [[ "$current_origin" != "$VECOLI_REPO" ]]; then
    git remote set-url origin "$VECOLI_REPO"
  fi
  # The head is an execution environment, not a dev workspace. Any local
  # tracked-file modifications are forfeit — hard-reset to remote so
  # pulls never get blocked. Untracked files (our scp'd scripts) are kept.
  git fetch --all --tags
  git checkout "$VECOLI_BRANCH" 2>/dev/null || git checkout -B "$VECOLI_BRANCH" "origin/$VECOLI_BRANCH"
  git reset --hard "origin/$VECOLI_BRANCH"
  echo "vEcoli at $(git rev-parse --short HEAD) on $(git rev-parse --abbrev-ref HEAD)"
fi

# --- 7. uv sync (PyPI for bigraph-schema / process-bigraph) -----------------
# pyproject.toml has the editable [tool.uv.sources] for those siblings
# commented out, so uv sync resolves them from PyPI.
[[ -d .venv ]] || uv venv
# shellcheck disable=SC1091
source .venv/bin/activate
# --frozen against uv.lock so numpy / scipy / etc. don't drift, and
# --extra aws so boto3/botocore come from the lock (not ad-hoc pip).
# s3fs is in main deps already. See pyproject.toml [aws] extras +
# memory:feedback_pin_via_lock_on_ec2 for the rationale.
uv sync --frozen --extra aws

# Auto-activate venv in future logins.
if ! grep -qF "vEcoli/.venv/bin/activate" "$HOME/.bashrc"; then
  echo "source $VECOLI_DIR/.venv/bin/activate" >> "$HOME/.bashrc"
fi

# --- 8. sanity-check the config and Batch queue -----------------------------
test -f "$CONFIG_RELPATH" || { echo "Missing $CONFIG_RELPATH"; exit 1; }
QUEUE=$(python -c "import json; print(json.load(open('$CONFIG_RELPATH'))['aws']['batch_queue'])")
STATUS=$(aws batch describe-job-queues --job-queues "$QUEUE" \
  --query 'jobQueues[0].status' --output text 2>/dev/null || echo "MISSING")
echo "Batch queue $QUEUE status: $STATUS"
if [[ "$STATUS" != "VALID" ]]; then
  echo "Batch queue is not VALID; cannot submit jobs. Pick a different queue or fix this one."
  exit 1
fi

# --- 9. launch workflow inside tmux so SSH disconnect doesn't kill it -------
# experiment_id: if EXPERIMENT_ID env was passed (vecoli_aws.sh assigns one
# per ``run launch <variant>`` and persists it in a sidecar), thread it
# through as ``--experiment-id`` so workflow.py uses that exact ID. Falls
# back to the config-derived ID for legacy use.
CFG_EXP_ID=$(python -c "import json; print(json.load(open('$CONFIG_RELPATH'))['experiment_id'])")
EFFECTIVE_EXP_ID="${EXPERIMENT_ID:-$CFG_EXP_ID}"
EXP_ID_FLAG=""
if [[ -n "${EXPERIMENT_ID:-}" ]]; then
  EXP_ID_FLAG="--experiment-id ${EXPERIMENT_ID}"
  echo "Using CLI-supplied experiment_id=${EXPERIMENT_ID}"
fi

# Set RESUME=1 to pass --resume <experiment_id> so Nextflow picks up where
# a prior run stopped (skips cached tasks that already wrote to S3).
RESUME_FLAG=""
if [[ -n "${RESUME:-}" && "${RESUME}" != "0" && "${RESUME,,}" != "false" ]]; then
  RESUME_FLAG="--resume ${EFFECTIVE_EXP_ID}"
  echo "Resuming experiment_id=${EFFECTIVE_EXP_ID}"
else
  echo "Starting fresh experiment_id=${EFFECTIVE_EXP_ID}"
fi

# Image lifecycle is owned by the ``image`` namespace in vecoli_aws.sh
# (build/push/pull). Default ``run launch`` to skip the in-workflow
# rebuild — otherwise workflow.py's _confirm_overwrite() prompts on
# stdin when the tag is already in ECR, hanging the tmux session.
# BUILD_IMAGE=1 → pass --build-image (force build, even if config says
#                 build_image=false; this is needed for v1's config).
# BUILD_IMAGE unset/0 → pass --no-build-image (skip rebuild).
# Pre-default to a local var first so ``set -u`` + ``,,`` lowercase
# expansion don't blow up when BUILD_IMAGE is unset.
_build_image="${BUILD_IMAGE:-0}"
BUILD_FLAG=""
if [[ "$_build_image" == "1" || "${_build_image,,}" == "true" ]]; then
  BUILD_FLAG="--build-image"
  echo "BUILD_IMAGE=$_build_image — forcing in-workflow image rebuild + push."
else
  BUILD_FLAG="--no-build-image"
  echo "Skipping in-workflow image rebuild (BUILD_IMAGE unset). Use --build to opt in."
fi

if tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "tmux session '$SESSION' already exists; not relaunching."
  exit 0
fi
LOG_FILE="\$HOME/${SESSION}_workflow.log"
tmux new-session -d -s "$SESSION" \
  "cd $VECOLI_DIR && source .venv/bin/activate && \
   NXF_ANSI_LOG=false \
   python runscripts/workflow.py --config $CONFIG_RELPATH $RESUME_FLAG $BUILD_FLAG $EXP_ID_FLAG 2>&1 | tee ${LOG_FILE}"
echo "Workflow launched (session=$SESSION, log=~/${SESSION}_workflow.log)."
