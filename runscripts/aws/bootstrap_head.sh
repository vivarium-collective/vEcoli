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

# --- 6. clone vEcoli on composite -------------------------------------------
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
# tracked-file modifications (e.g. from a previous report render writing
# to doc/v1_v2_report.md) are forfeit — hard-reset to remote so pulls
# never get blocked. Untracked files (our scp'd scripts) are kept.
git fetch --all --tags
git checkout "$VECOLI_BRANCH" 2>/dev/null || git checkout -B "$VECOLI_BRANCH" "origin/$VECOLI_BRANCH"
git reset --hard "origin/$VECOLI_BRANCH"
echo "vEcoli at $(git rev-parse --short HEAD) on $(git rev-parse --abbrev-ref HEAD)"

# --- 7. uv sync (PyPI for bigraph-schema / process-bigraph) -----------------
# pyproject.toml has the editable [tool.uv.sources] for those siblings
# commented out, so uv sync resolves them from PyPI.
[[ -d .venv ]] || uv venv
# shellcheck disable=SC1091
source .venv/bin/activate
# Use --frozen against uv.lock so numpy / scipy / etc. don't drift
# from the version we develop against locally. See bootstrap_head_mp.sh
# for the np.in1d / numpy 2.x incident that prompted this.
uv sync --frozen
uv pip install s3fs boto3

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
# Set RESUME=1 to pass --resume <experiment_id> so Nextflow picks up where
# a prior run stopped (skips cached tasks that already wrote to S3).
EXP_ID=$(python -c "import json; print(json.load(open('$CONFIG_RELPATH'))['experiment_id'])")
RESUME_FLAG=""
if [[ -n "${RESUME:-}" && "${RESUME}" != "0" && "${RESUME,,}" != "false" ]]; then
  RESUME_FLAG="--resume ${EXP_ID}"
  echo "Resuming experiment_id=${EXP_ID}"
else
  echo "Starting fresh experiment_id=${EXP_ID}"
fi

if tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "tmux session '$SESSION' already exists. Attach with: tmux attach -t $SESSION"
  exit 0
fi
LOG_FILE="\$HOME/${SESSION}_workflow.log"
tmux new-session -d -s "$SESSION" \
  "cd $VECOLI_DIR && source .venv/bin/activate && \
   NXF_ANSI_LOG=false \
   python runscripts/workflow.py --config $CONFIG_RELPATH $RESUME_FLAG 2>&1 | tee ${LOG_FILE}"

cat <<EOF

Workflow launched in tmux session '$SESSION'.
  Tail log:    tail -f ~/${SESSION}_workflow.log
  Attach:      tmux attach -t $SESSION
  Detach:      Ctrl+B then D
  Outputs to:  $(python -c "import json;print(json.load(open('$CONFIG_RELPATH'))['emitter_arg']['out_uri'])")

When the run finishes, generate the v1 vs v2 comparison report from the head:
  cd $VECOLI_DIR
  source .venv/bin/activate
  # Pull v1 + v2 nextflow trace CSVs from S3, then:
  python runscripts/v1_v2_report.py --help

EOF
