#!/usr/bin/env bash
# Bring up a Ray cluster on EC2 via SSM (NOT ``ray up``) and run the
# composite_lineage_ray workflow against it.
#
# This head node is the *driver*: small instance, runs
# runscripts/aws/ec2_cluster_ray.py which uses
# ``process_bigraph.protocols.clusters.ec2_ssm.EC2SSMRayCluster`` to
# provision real workers, dispatch the experiment, and tear down. No
# ``ray up`` — the SSM path bypasses Ray's autoscaler and its
# IAM-permission requirements (see memory:reference_ec2_ssm_ray_cluster).
#
# Pre-req: composite branch pushed; AWS GovCloud creds configured;
# ECR has a vEcoli image with ray[default] installed (current
# Dockerfile installs it after `uv sync`).

set -euo pipefail

VECOLI_REPO="${VECOLI_REPO:-https://github.com/vivarium-collective/vEcoli.git}"
VECOLI_BRANCH="${VECOLI_BRANCH:-composite}"
VECOLI_DIR="${HOME}/vEcoli"
CONFIG_RELPATH="${CONFIG_RELPATH:-configs/comparison_10s_16g_v2_ray_aws.json}"
SESSION="${SESSION:-vecoli-v2-ray}"
REGION="us-gov-west-1"

N_SEEDS="${N_SEEDS:-10}"
GENERATIONS="${GENERATIONS:-16}"
MAX_DURATION="${MAX_DURATION:-3000.0}"

# --- 1. system packages -----------------------------------------------------
sudo dnf -y update
sudo dnf -y install git tmux gcc gcc-c++ make

# --- 2. AWS region default --------------------------------------------------
aws configure set region "$REGION"

# --- 3. uv ------------------------------------------------------------------
if ! command -v uv >/dev/null; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
export PATH="$HOME/.local/bin:$PATH"
hash -r

# --- 4. clone vEcoli on composite -------------------------------------------
if [[ ! -d "$VECOLI_DIR/.git" ]]; then
  git clone --filter=blob:none "$VECOLI_REPO" "$VECOLI_DIR"
fi
cd "$VECOLI_DIR"
current_origin=$(git remote get-url origin 2>/dev/null || true)
if [[ "$current_origin" != "$VECOLI_REPO" ]]; then
  git remote set-url origin "$VECOLI_REPO"
fi
git fetch --all --tags
git checkout "$VECOLI_BRANCH" 2>/dev/null || git checkout -B "$VECOLI_BRANCH" "origin/$VECOLI_BRANCH"
git reset --hard "origin/$VECOLI_BRANCH"
echo "vEcoli at $(git rev-parse --short HEAD) on $(git rev-parse --abbrev-ref HEAD)"

# --- 5. uv sync (driver only — actual sims run in cluster Docker) ----------
# The head only needs boto3 + process-bigraph (for EC2SSMRayCluster).
# It does NOT need vEcoli's full sim deps; the cluster pulls those from
# the ECR image. Still install full vEcoli so the driver scripts and
# memory tooling work.
[[ -d .venv ]] || uv venv
# shellcheck disable=SC1091
source .venv/bin/activate
# Pin via uv.lock (numpy 2.x drift broke np.in1d on a fresh sync —
# see bootstrap_head_mp.sh). s3fs/boto3/ray on top.
uv sync --frozen
uv pip install s3fs boto3
# Ray client lib for the driver's ``import ray`` (driver doesn't
# ``ray up``; it just uses EC2SSMRayCluster + ``ray.init(address='auto')``
# inside the experiment script that runs INSIDE the cluster).
uv pip install 'process-bigraph[ec2-ssm]' || uv pip install 'ray[default]>=2.10'

if ! grep -qF "vEcoli/.venv/bin/activate" "$HOME/.bashrc"; then
  echo "source $VECOLI_DIR/.venv/bin/activate" >> "$HOME/.bashrc"
fi

# --- 6. config + sim_data + image_uri resolution ---------------------------
test -f "$CONFIG_RELPATH" || { echo "Missing $CONFIG_RELPATH"; exit 1; }
EXP_ID=$(python -c "import json; print(json.load(open('$CONFIG_RELPATH'))['experiment_id'])")
OUT_URI=$(python -c "import json; print(json.load(open('$CONFIG_RELPATH'))['emitter_arg']['out_uri'])")

# IMAGE_URI must be the ECR image with ray installed — see Dockerfile
# `pip install ray` line. If unset, fall back to the standard v2-aws
# image (assumes it was rebuilt with the updated Dockerfile).
IMAGE_URI="${IMAGE_URI:-}"
if [[ -z "$IMAGE_URI" ]]; then
  ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
  IMAGE_URI="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/vecoli:v2-comparison-arm64"
  echo "IMAGE_URI not set; defaulting to ${IMAGE_URI}"
fi

# sim_data_path: must be S3 for the cluster — workers fetch via fsspec.
if [[ -z "${SIM_DATA_S3_URI:-}" ]]; then
  echo "ERROR: SIM_DATA_S3_URI must be set (cluster workers can't reach local files)" >&2
  echo "Example: SIM_DATA_S3_URI=s3://bucket/.../parca/kb/simData.cPickle" >&2
  exit 1
fi

# --- 7. launch driver inside tmux so SSH disconnect doesn't kill it --------
# The driver is ec2_cluster_ray.py — provisions cluster, runs
# experiment via SSM, tears down. Cluster lives in a separate set of
# EC2 instances (provisioned by the driver via boto3); this head
# node just steers.
if tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "tmux session '$SESSION' already exists. Attach with: tmux attach -t $SESSION"
  exit 0
fi
LOG_FILE="\$HOME/${SESSION}_workflow.log"
tmux new-session -d -s "$SESSION" \
  "cd $VECOLI_DIR && source .venv/bin/activate && \
   IMAGE_URI='$IMAGE_URI' \
   OUT_URI='$OUT_URI' \
   SIM_DATA_URI='$SIM_DATA_S3_URI' \
   CONFIG_RELPATH='$CONFIG_RELPATH' \
   N_SEEDS=$N_SEEDS \
   GENERATIONS=$GENERATIONS \
   MAX_DURATION=$MAX_DURATION \
   python runscripts/aws/ec2_cluster_ray.py \
     2>&1 | tee ${LOG_FILE}"

cat <<EOF

Ray driver launched in tmux session '$SESSION'.
  Tail log:    tail -f ~/${SESSION}_workflow.log
  Attach:      tmux attach -t $SESSION
  Detach:      Ctrl+B then D
  Outputs to:  $OUT_URI

The driver provisions the cluster, runs the experiment via SSM, and
tears down on completion. If you need to cancel mid-run:
  ssh ec2-user@<head-dns> 'tmux kill-session -t $SESSION'
  # then manually terminate any stray cluster instances tagged with
  # the cluster_id (visible in the driver's startup logs).

EOF
