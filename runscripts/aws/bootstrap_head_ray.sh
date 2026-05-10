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

# All per-run knobs (n_init_sims, generations, max_duration) live in
# the config — see configs/comparison_10s_16g_v2_ray_aws.json.
# ec2_cluster_ray.py walks the inherited chain and reads them from
# there, then forwards CONFIG_RELPATH to run_composite_lineage_ray.py
# which reads the same keys.

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
# --frozen against uv.lock + --extra aws — same as the other heads.
# Ray is intentionally NOT installed on the driver: ec2_cluster_ray.py
# only imports EC2SSMRayCluster from process-bigraph; ``import ray``
# only runs INSIDE the Docker image during the cluster experiment.
# Keeping ray off the driver keeps pydantic 2.x (ray's transitive
# constraint pulls in pydantic<2). See pyproject.toml [aws] extras.
uv sync --frozen --extra aws

if ! grep -qF "vEcoli/.venv/bin/activate" "$HOME/.bashrc"; then
  echo "source $VECOLI_DIR/.venv/bin/activate" >> "$HOME/.bashrc"
fi

# --- 6. config + sim_data + image_uri resolution ---------------------------
test -f "$CONFIG_RELPATH" || { echo "Missing $CONFIG_RELPATH"; exit 1; }

# Read every per-run knob from the config, walking inherit_from chains
# (and default.json as the implicit base) the same way EcoliSim does.
# This avoids env-var defaults silently shadowing config values — see
# the May 2026 max_duration regression.
RESOLVER='
import json, os, sys
def resolve(path, seen=None):
    seen = seen or set()
    path = os.path.abspath(path)
    if path in seen: return {}
    seen.add(path)
    cfg = json.load(open(path))
    merged = {}
    cfg_dir = os.path.dirname(path)
    if os.path.basename(path) != "default.json":
        for cand in (os.path.join(cfg_dir, "default.json"),
                     os.path.join(cfg_dir, "..", "configs", "default.json")):
            if os.path.isfile(cand):
                merged.update(resolve(cand, seen)); break
    for parent_rel in cfg.get("inherit_from") or []:
        for cand in (os.path.join(cfg_dir, parent_rel),
                     os.path.join(cfg_dir, "..", "configs", parent_rel),
                     os.path.join(cfg_dir, parent_rel + ".json")):
            if os.path.isfile(cand):
                merged.update(resolve(cand, seen)); break
    merged.update(cfg)
    return merged
m = resolve(sys.argv[1])
key = sys.argv[2]
v = m
for p in key.split("."):
    v = (v or {}).get(p) if isinstance(v, dict) else None
print(v or "")
'
EXP_ID=$(python -c "$RESOLVER" "$CONFIG_RELPATH" experiment_id)
OUT_URI=$(python -c "$RESOLVER" "$CONFIG_RELPATH" emitter_arg.out_uri)
SIM_DATA_S3_URI_CFG=$(python -c "$RESOLVER" "$CONFIG_RELPATH" sim_data_path)

# Env var still works as override (e.g. for ad-hoc swaps in a re-run).
SIM_DATA_S3_URI="${SIM_DATA_S3_URI:-$SIM_DATA_S3_URI_CFG}"

# IMAGE_URI: ECR image (the Dockerfile installs ray). If unset, fall
# back to the standard v2-aws image.
IMAGE_URI="${IMAGE_URI:-}"
if [[ -z "$IMAGE_URI" ]]; then
  ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
  IMAGE_URI="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/vecoli:v2-comparison-arm64"
  echo "IMAGE_URI not set; defaulting to ${IMAGE_URI}"
fi

# sim_data_path: must be S3 for the cluster (workers fetch via fsspec)
if [[ -z "${SIM_DATA_S3_URI}" ]]; then
  echo "ERROR: sim_data_path missing." >&2
  echo "  Set 'sim_data_path' in $CONFIG_RELPATH (or any inherit-from parent)" >&2
  echo "  to an s3:// URI. Env-var override SIM_DATA_S3_URI also works." >&2
  exit 1
fi
if [[ "$SIM_DATA_S3_URI" != s3://* ]]; then
  echo "ERROR: sim_data_path must be s3:// for the cluster: $SIM_DATA_S3_URI" >&2
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
