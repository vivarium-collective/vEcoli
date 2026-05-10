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

# All per-run knobs (n_init_sims, generations, max_duration,
# sim_data_path, emitter_arg.out_uri) live in the config — see
# configs/comparison_10s_16g_v2_mp_aws.json. The runner reads them
# directly so we don't shadow config values with stale shell defaults.

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

# --- 4. fetch vEcoli code ---------------------------------------------------
# SKIP_GIT_RESET=1 (CLI rsynced local) → trust files, don't touch git.
# SKIP_GIT_RESET=0 (default / --from-origin) → clone + reset to origin.
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
  current_origin=$(git remote get-url origin 2>/dev/null || true)
  if [[ "$current_origin" != "$VECOLI_REPO" ]]; then
    git remote set-url origin "$VECOLI_REPO"
  fi
  git fetch --all --tags
  git checkout "$VECOLI_BRANCH" 2>/dev/null || git checkout -B "$VECOLI_BRANCH" "origin/$VECOLI_BRANCH"
  git reset --hard "origin/$VECOLI_BRANCH"
  echo "vEcoli at $(git rev-parse --short HEAD) on $(git rev-parse --abbrev-ref HEAD)"
fi

# --- 5. uv sync (PyPI for bigraph-schema / process-bigraph) -----------------
[[ -d .venv ]] || uv venv
# shellcheck disable=SC1091
source .venv/bin/activate
# --frozen against uv.lock for numpy/scipy/etc. + --extra aws for
# boto3/botocore. s3fs is in main deps. NO ad-hoc ``uv pip install`` —
# everything comes from the lock so the head's env exactly matches
# what was tested locally. See memory:feedback_pin_via_lock_on_ec2.
uv sync --frozen --extra aws

if ! grep -qF "vEcoli/.venv/bin/activate" "$HOME/.bashrc"; then
  echo "source $VECOLI_DIR/.venv/bin/activate" >> "$HOME/.bashrc"
fi

# --- 6. resolve sim_data via config, env override, or parca fallback -------
# Read sim_data_path from the merged config (inherit chain + default.json
# as implicit base). MP runs locally so the runner needs a local pickle —
# if the config points at s3://, we download once; if it points at a
# local file, use as-is. If neither, fall back to running parca on this
# node so the pickle ends up at $VECOLI_DIR/out/$EXP_ID/parca/kb/simData.cPickle.
test -f "$CONFIG_RELPATH" || { echo "Missing $CONFIG_RELPATH"; exit 1; }
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
if "." in key:
    parts = key.split(".")
    v = m
    for p in parts:
        v = (v or {}).get(p) if isinstance(v, dict) else None
    print(v or "")
else:
    print(m.get(key) or "")
'
EXP_ID=$(python -c "$RESOLVER" "$CONFIG_RELPATH" experiment_id)
OUT_URI=$(python -c "$RESOLVER" "$CONFIG_RELPATH" emitter_arg.out_uri)
SIM_DATA_PATH_CFG=$(python -c "$RESOLVER" "$CONFIG_RELPATH" sim_data_path)
# Env override still wins for ad-hoc swaps
SIM_DATA_S3_URI="${SIM_DATA_S3_URI:-$SIM_DATA_PATH_CFG}"

SIM_DATA_LOCAL="$VECOLI_DIR/out/$EXP_ID/parca/kb/simData.cPickle"
if [[ "$SIM_DATA_S3_URI" == s3://* ]]; then
  mkdir -p "$(dirname "$SIM_DATA_LOCAL")"
  echo "Downloading sim_data from $SIM_DATA_S3_URI ..."
  aws s3 cp "$SIM_DATA_S3_URI" "$SIM_DATA_LOCAL" --no-progress
elif [[ -n "$SIM_DATA_S3_URI" && -f "$SIM_DATA_S3_URI" ]]; then
  SIM_DATA_LOCAL="$SIM_DATA_S3_URI"
elif [[ ! -f "$SIM_DATA_LOCAL" ]]; then
  echo "Running parca to produce $SIM_DATA_LOCAL (one-time)..."
  python runscripts/parca.py --config "$CONFIG_RELPATH" --outdir "$VECOLI_DIR/out/$EXP_ID/parca"
fi
test -f "$SIM_DATA_LOCAL" || { echo "sim_data missing: $SIM_DATA_LOCAL"; exit 1; }

# --- 7. launch run inside tmux so SSH disconnect doesn't kill it -----------
# CLI-supplied experiment_id forwarded as --experiment-id (vecoli_aws.sh
# assigns one per ``run launch mp`` and persists it in a sidecar).
EXP_ID_FLAG=""
if [[ -n "${EXPERIMENT_ID:-}" ]]; then
  EXP_ID_FLAG="--experiment-id ${EXPERIMENT_ID}"
  echo "Using CLI-supplied experiment_id=${EXPERIMENT_ID}"
fi
if tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "tmux session '$SESSION' already exists; not relaunching."
  exit 0
fi
LOG_FILE="\$HOME/${SESSION}_workflow.log"
tmux new-session -d -s "$SESSION" \
  "cd $VECOLI_DIR && source .venv/bin/activate && \
   POLARS_MAX_THREADS=1 \
   python runscripts/run_composite_lineage_mp.py \
     --config $CONFIG_RELPATH $EXP_ID_FLAG \
     2>&1 | tee ${LOG_FILE}"
echo "MP workflow launched (session=$SESSION, log=~/${SESSION}_workflow.log)."
