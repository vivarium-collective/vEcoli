#!/usr/bin/env bash
# Same scaling test as mp_scaling_test.sh, but with single-threaded
# numerics enabled in workers. If wall stays flat across N=1..8,
# thread oversubscription was the cause; the fix is to bake these
# env vars into the MP runner.
set -euo pipefail
cd "$(dirname "$0")/.."
CFG=configs/composites/mp_scaling_local.json
OUT=/tmp/mp_scaling_st.log
: > "$OUT"

# Force single-threaded BLAS/numba/numpy in workers. Set BEFORE
# any numpy/scipy import so threading state is locked.
export OMP_NUM_THREADS=1
export MKL_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export NUMBA_NUM_THREADS=1
export VECLIB_MAXIMUM_THREADS=1
export NUMEXPR_NUM_THREADS=1
export POLARS_MAX_THREADS=1

echo "Single-threaded numerics enforced." | tee -a "$OUT"
env | grep -E '_NUM_THREADS|MAX_THREADS' | sort | tee -a "$OUT"
echo "" | tee -a "$OUT"

for N in 1 2 4 8; do
    echo "=== N=$N workers (single-threaded) ===" | tee -a "$OUT"
    uv run python -c "
import json
with open('$CFG') as f: c = json.load(f)
c['n_init_sims'] = $N
c['emitter_arg'] = {'out_uri': 'out/mp_scaling_st_N${N}', 'threaded': False}
with open('$CFG', 'w') as f: json.dump(c, f, indent=4)
"
    rm -rf "out/mp_scaling_st_N${N}"
    t0=$(date +%s)
    uv run --no-sync python runscripts/run_composite_lineage_mp.py \
        --config "$CFG" 2>&1 | tail -3 | tee -a "$OUT"
    t1=$(date +%s)
    wall=$((t1 - t0))
    echo "  N=$N: total_wall=${wall}s (single-threaded)" | tee -a "$OUT"
    echo "" | tee -a "$OUT"
done

echo "=== Summary (single-threaded) ===" | tee -a "$OUT"
echo "Compare to multi-threaded scaling: if flat, oversubscription was the bug." | tee -a "$OUT"
