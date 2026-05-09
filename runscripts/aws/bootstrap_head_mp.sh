#!/usr/bin/env bash
# Bootstrap a single-node EC2 box and run the MP-only composite_lineage
# workflow (no AWS Batch, no Nextflow). One process per lineage_seed,
# parent loads sim_data once, workers inherit via fork copy-on-write,
# parquet writes go directly to s3:// out_uri.
#
# Pre-req: composite branch pushed to origin so we can clone here.
#
# Idempotent: re-running picks up where left off (uv sync is no-op,
# tmux session is reused). After the docker-group add on first run
# the script exits and asks you to run it again in a fresh shell so
# the new group membership takes effect (same as bootstrap_head.sh).

set -euo pipefail

VECOLI_REPO="${VECOLI_REPO:-https://github.com/vivarium-collective/vEcoli.git}"
VECOLI_BRANCH="${VECOLI_BRANCH:-composite}"
VECOLI_DIR="${HOME}/vEcoli"
CONFIG_RELPATH="${CONFIG_RELPATH:-configs/comparison_10s_16g_v2_mp_aws.json}"
SESSION="${SESSION:-vecoli-v2-mp}"
REGION="us-gov-west-1"

# Override via env if you want to vary scale without editing the config.
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

# --- 5. uv sync (PyPI for bigraph-schema / process-bigraph) -----------------
[[ -d .venv ]] || uv venv
# shellcheck disable=SC1091
source .venv/bin/activate
uv pip install -e .
uv pip install s3fs boto3

if ! grep -qF "vEcoli/.venv/bin/activate" "$HOME/.bashrc"; then
  echo "source $VECOLI_DIR/.venv/bin/activate" >> "$HOME/.bashrc"
fi

# --- 6. resolve sim_data via parca (or skip if S3 path provided) -----------
# The MP runner expects a local simData.cPickle (LoadSimData reads
# from sim_data_path). If $SIM_DATA_S3_URI is set, download once;
# otherwise run parca on this node so the pickle ends up at
# $VECOLI_DIR/out/$EXP_ID/parca/kb/simData.cPickle.
test -f "$CONFIG_RELPATH" || { echo "Missing $CONFIG_RELPATH"; exit 1; }
EXP_ID=$(python -c "import json; print(json.load(open('$CONFIG_RELPATH'))['experiment_id'])")
OUT_URI=$(python -c "import json; print(json.load(open('$CONFIG_RELPATH'))['emitter_arg']['out_uri'])")

SIM_DATA_LOCAL="$VECOLI_DIR/out/$EXP_ID/parca/kb/simData.cPickle"
if [[ -n "${SIM_DATA_S3_URI:-}" ]]; then
  mkdir -p "$(dirname "$SIM_DATA_LOCAL")"
  echo "Downloading sim_data from $SIM_DATA_S3_URI ..."
  aws s3 cp "$SIM_DATA_S3_URI" "$SIM_DATA_LOCAL" --no-progress
elif [[ ! -f "$SIM_DATA_LOCAL" ]]; then
  echo "Running parca to produce $SIM_DATA_LOCAL (one-time)..."
  python runscripts/parca.py --config "$CONFIG_RELPATH" --outdir "$VECOLI_DIR/out/$EXP_ID/parca"
fi
test -f "$SIM_DATA_LOCAL" || { echo "sim_data missing: $SIM_DATA_LOCAL"; exit 1; }

# --- 7. launch run inside tmux so SSH disconnect doesn't kill it -----------
if tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "tmux session '$SESSION' already exists. Attach with: tmux attach -t $SESSION"
  exit 0
fi
LOG_FILE="\$HOME/${SESSION}_workflow.log"
tmux new-session -d -s "$SESSION" \
  "cd $VECOLI_DIR && source .venv/bin/activate && \
   POLARS_MAX_THREADS=1 \
   python runscripts/run_composite_lineage_mp.py \
     --config $CONFIG_RELPATH \
     --sim_data_path $SIM_DATA_LOCAL \
     --out_uri $OUT_URI \
     --n_seeds $N_SEEDS \
     --generations $GENERATIONS \
     --max_duration $MAX_DURATION \
     2>&1 | tee ${LOG_FILE}"

cat <<EOF

MP workflow launched in tmux session '$SESSION'.
  Tail log:    tail -f ~/${SESSION}_workflow.log
  Attach:      tmux attach -t $SESSION
  Detach:      Ctrl+B then D
  Outputs to:  $OUT_URI

EOF
