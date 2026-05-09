#!/usr/bin/env bash
# Bring up a Ray cluster on EC2 (1 head + 5 workers, pre-allocated, no
# autoscale) and run the composite_lineage_ray workflow against it.
#
# Runs on a small EC2 head node — it's the *driver*, not the cluster.
# Ray's managed key lets it SSH to its own provisioned instances. The
# driver doesn't run sims itself; it just calls
# run_composite_lineage_ray.py --ray_address auto inside the cluster.
#
# Pre-req: composite branch pushed; AWS GovCloud creds configured.

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

# --- 5. uv sync + ray ------------------------------------------------------
[[ -d .venv ]] || uv venv
# shellcheck disable=SC1091
source .venv/bin/activate
uv pip install -e . s3fs boto3 'ray[default]>=2.10'

if ! grep -qF "vEcoli/.venv/bin/activate" "$HOME/.bashrc"; then
  echo "source $VECOLI_DIR/.venv/bin/activate" >> "$HOME/.bashrc"
fi

# --- 6. config + sim_data resolution ---------------------------------------
test -f "$CONFIG_RELPATH" || { echo "Missing $CONFIG_RELPATH"; exit 1; }
EXP_ID=$(python -c "import json; print(json.load(open('$CONFIG_RELPATH'))['experiment_id'])")
OUT_URI=$(python -c "import json; print(json.load(open('$CONFIG_RELPATH'))['emitter_arg']['out_uri'])")
RAY_YAML=$(python -c "import json; print(json.load(open('$CONFIG_RELPATH'))['aws']['ray']['cluster_yaml'])")
test -f "$RAY_YAML" || { echo "Missing Ray cluster yaml: $RAY_YAML"; exit 1; }

# sim_data_path: prefer S3 if SIM_DATA_S3_URI env var set, else run
# parca on the head once. Workers can pull from S3 via fsspec when
# the runner passes the s3:// URI through to LoadSimData.
if [[ -n "${SIM_DATA_S3_URI:-}" ]]; then
  SIM_DATA_PATH="$SIM_DATA_S3_URI"
  echo "Using sim_data from $SIM_DATA_PATH"
else
  SIM_DATA_LOCAL="$VECOLI_DIR/out/$EXP_ID/parca/kb/simData.cPickle"
  if [[ ! -f "$SIM_DATA_LOCAL" ]]; then
    echo "Running parca to produce $SIM_DATA_LOCAL (one-time)..."
    python runscripts/parca.py --config "$CONFIG_RELPATH" --outdir "$VECOLI_DIR/out/$EXP_ID/parca"
  fi
  SIM_DATA_PATH="$SIM_DATA_LOCAL"
fi

# --- 7. ray up -------------------------------------------------------------
# Provisions head + workers. Ray manages SSH keys to its own nodes.
echo "Provisioning Ray cluster from $RAY_YAML ..."
ray up "$RAY_YAML" --yes --no-config-cache
echo "Ray cluster up. Dashboard: http://<head_dns>:8265"

# --- 8. launch run inside tmux so SSH disconnect doesn't kill it -----------
# The driver does ray.init(address='auto') and submits actors. It runs
# on this small head; the heavy work happens on the cluster's workers.
if tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "tmux session '$SESSION' already exists. Attach with: tmux attach -t $SESSION"
  exit 0
fi
LOG_FILE="\$HOME/${SESSION}_workflow.log"
tmux new-session -d -s "$SESSION" \
  "cd $VECOLI_DIR && source .venv/bin/activate && \
   POLARS_MAX_THREADS=1 \
   python runscripts/run_composite_lineage_ray.py \
     --config $CONFIG_RELPATH \
     --sim_data_path $SIM_DATA_PATH \
     --out_uri $OUT_URI \
     --n_seeds $N_SEEDS \
     --generations $GENERATIONS \
     --max_duration $MAX_DURATION \
     --ray_address auto \
     2>&1 | tee ${LOG_FILE}"

cat <<EOF

Ray workflow launched in tmux session '$SESSION'.
  Tail log:    tail -f ~/${SESSION}_workflow.log
  Attach:      tmux attach -t $SESSION
  Detach:      Ctrl+B then D
  Outputs to:  $OUT_URI

When the run finishes, tear the cluster down:
  ray down $RAY_YAML --yes

EOF
