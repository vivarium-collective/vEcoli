"""Find the molecule that differs between v1 and v2 at t=1, and at t=2."""

import sys
import os
import subprocess
import pickle
import tempfile
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from contextlib import chdir


def get_state(engine, duration):
    """Run sim and return (initial_bulk, final_bulk, bulk_ids)."""
    script = f"""
import sys, pickle
sys.path.insert(0, '.')
from ecoli.experiments.ecoli_master_sim import EcoliSim

sim = EcoliSim.from_file()
sim.max_duration = int({duration})
sim.emitter = 'null'
sim.divide = False
{"sim.config['engine'] = 'composite'" if engine == 'v2' else ""}
sim.build_ecoli()

init = sim.generated_initial_state
if 'agents' in init:
    init = init['agents'][next(iter(init['agents']))]
init_bulk = init['bulk']['count'].copy()
init_ids = init['bulk']['id'].copy()

sim.run()

if '{engine}' == 'v1':
    from ecoli.library.schema import not_a_process
    state = sim.ecoli_experiment.state.get_value(condition=not_a_process)
    final_bulk = state['bulk']['count'].copy()
    sim.ecoli_experiment.end()
else:
    composite = sim._composite
    if 'agents' in composite.state:
        cell = next(iter(composite.state['agents'].values()))
    else:
        cell = composite.state
    final_bulk = cell['bulk']['count'].copy()

with open(sys.argv[1], 'wb') as f:
    pickle.dump((init_bulk, final_bulk, init_ids), f)
"""
    with tempfile.NamedTemporaryFile(suffix='.pkl', delete=False) as tmp:
        tmp_path = tmp.name
    proc = subprocess.run(
        [sys.executable, '-c', script, tmp_path],
        capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        print(f"STDERR: {proc.stderr[-500:]}", flush=True)
        raise RuntimeError(f"failed: {proc.stderr[-200:]}")
    with open(tmp_path, 'rb') as f:
        return pickle.load(f)


def find_diffs(duration):
    print(f"\n=== t={duration}s ===", flush=True)
    print(f"running v1...", flush=True)
    v1_init, v1_final, ids = get_state('v1', duration)
    print(f"running v2...", flush=True)
    v2_init, v2_final, _ = get_state('v2', duration)

    if not np.array_equal(v1_init, v2_init):
        print(f"  WARNING: initial states differ", flush=True)

    diff_mask = v1_final != v2_final
    n_diff = diff_mask.sum()
    print(f"  {n_diff} molecules differ", flush=True)

    if n_diff == 0:
        return

    # Show top differences by absolute value
    diffs = v2_final.astype(np.int64) - v1_final.astype(np.int64)
    abs_diffs = np.abs(diffs)
    sorted_idx = np.argsort(-abs_diffs)
    print(f"  top {min(20, n_diff)} differences (v2 - v1):", flush=True)
    for i in sorted_idx[:20]:
        if abs_diffs[i] == 0:
            break
        print(f"    {ids[i]:50s} v1={v1_final[i]:>15} v2={v2_final[i]:>15} diff={diffs[i]:>+15}", flush=True)


if __name__ == '__main__':
    with chdir(ROOT):
        find_diffs(1)
        find_diffs(2)
