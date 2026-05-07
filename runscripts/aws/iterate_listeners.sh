#!/usr/bin/env bash
# Local v1<->v2 listener parity iteration loop.
#
# Runs the v1 (vivarium) and v2 (composite) engines locally for a short
# duration via configs/iter_v1.json and configs/iter_v2.json (10 ticks,
# 1 seed, no division, parquet output). Then scans every column for
# divergence and writes a per-column TSV.
#
# Usage:
#   runscripts/aws/iterate_listeners.sh
#
# Iteration loop:
#   1. edit v2 listener wiring (e.g. ecoli/processes/listeners/*.py)
#   2. ./runscripts/aws/iterate_listeners.sh
#   3. read the divergence summary; goto 1
#
# First run is slow (~10 min) because ParCa generates sim_data from
# scratch. Subsequent runs reuse the cached sim_data and finish in ~30s.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${REPO_ROOT}"

# Activate the project venv so uv-installed deps (polars, pyarrow) are on path.
if [[ ! -d .venv ]]; then
  echo "no .venv at $REPO_ROOT — run 'uv sync' first" >&2
  exit 1
fi
# shellcheck disable=SC1091
source .venv/bin/activate

V1_HISTORY="out/iter_v1/history/experiment_id=iter_v1/variant=0/lineage_seed=0/generation=1/agent_id=0"
V2_HISTORY="out/iter_v2/history/experiment_id=iter_v2/variant=0/lineage_seed=0/generation=1/agent_id=0"

run_one() {
  local engine="$1" config="configs/iter_$1.json"
  echo
  echo "=== running $engine (configs/iter_$1.json) ==="
  # Wipe previous output so we never read stale parquet on a config-error run.
  rm -rf "out/iter_$1"
  python runscripts/sim.py --config "$config"
}

run_one v1
run_one v2

if [[ ! -d "$V1_HISTORY" ]]; then
  echo "no v1 history at $V1_HISTORY — sim probably errored" >&2
  exit 1
fi
if [[ ! -d "$V2_HISTORY" ]]; then
  echo "no v2 history at $V2_HISTORY — sim probably errored" >&2
  exit 1
fi

echo
echo "=== scanning columns ==="
python runscripts/aws/scan_all_columns.py \
  --v1-path "$V1_HISTORY" --v2-path "$V2_HISTORY" \
  --seed 0 --gen 1 \
  --v1-id iter_v1 --v2-id iter_v2

echo
echo "Per-column TSV: ~/scan_columns_seed0_gen1.tsv"
echo "To grep for t=0 divergences:"
echo "  awk -F\\\$'\\\\t' '\\\$3==0' ~/scan_columns_seed0_gen1.tsv"
