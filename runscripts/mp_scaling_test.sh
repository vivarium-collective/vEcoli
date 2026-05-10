#!/usr/bin/env bash
# Per-cell MP scaling test. Runs 200 ticks of mother for N=1,2,4,8 workers
# back-to-back, measures wall time per run. If per-cell wall is constant
# across N, scaling is linear. If it grows with N, super-linear (the bug).
set -euo pipefail
cd "$(dirname "$0")/.."
CFG=configs/composites/mp_scaling_local.json
OUT=/tmp/mp_scaling.log
: > "$OUT"

for N in 1 2 4 8; do
    echo "=== N=$N workers ===" | tee -a "$OUT"
    # Patch n_init_sims in config
    uv run python -c "
import json
with open('$CFG') as f: c = json.load(f)
c['n_init_sims'] = $N
c['emitter_arg'] = {'out_uri': 'out/mp_scaling_N${N}', 'threaded': False}
with open('$CFG', 'w') as f: json.dump(c, f, indent=4)
"
    rm -rf "out/mp_scaling_N${N}"
    t0=$(date +%s)
    uv run --no-sync python runscripts/run_composite_lineage_mp.py \
        --config "$CFG" 2>&1 | tail -5 | tee -a "$OUT"
    t1=$(date +%s)
    wall=$((t1 - t0))
    per_cell_per_tick=$(echo "scale=4; $wall / 200" | bc)
    echo "  N=$N: total_wall=${wall}s   wall/cell/tick=${per_cell_per_tick}s (200 ticks each)" \
        | tee -a "$OUT"
    echo "" | tee -a "$OUT"
done

echo "=== Summary ===" | tee -a "$OUT"
echo "If wall is roughly constant as N grows -> per-cell parallel scales linearly." | tee -a "$OUT"
echo "If wall grows with N -> super-linear (oversubscription / shared-state bug)." | tee -a "$OUT"
