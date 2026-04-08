"""Compare vivarium (v1) and composite (v2) engines on an identical EcoliSim.

Run from the vEcoli root directory:
    python runscripts/compare_engines.py [--duration 4]
"""

import argparse
import copy
import time
import sys
import os

import numpy as np
from contextlib import chdir

ROOT_PATH = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run_v1(duration):
    """Run vivarium engine, return (runtime, initial_bulk, final_bulk, query_data)."""
    from ecoli.experiments.ecoli_master_sim import EcoliSim
    from ecoli.library.schema import not_a_process

    sim = EcoliSim.from_file()
    sim.max_duration = int(duration)
    sim.emitter = 'null'
    sim.divide = False
    sim.build_ecoli()

    init = sim.generated_initial_state
    if 'agents' in init:
        init = init['agents'][next(iter(init['agents']))]
    initial_bulk = init['bulk']['count'].copy()

    t0 = time.time()
    sim.run()
    runtime = time.time() - t0

    state = sim.ecoli_experiment.state.get_value(condition=not_a_process)
    final_bulk = state['bulk']['count'].copy()

    sim.ecoli_experiment.end()

    return runtime, initial_bulk, final_bulk, None


def run_v2(duration):
    """Run composite engine, return (runtime, initial_bulk, final_bulk)."""
    from ecoli.experiments.ecoli_master_sim import EcoliSim

    sim = EcoliSim.from_file()
    sim.max_duration = int(duration)
    sim.emitter = 'null'
    sim.divide = False
    sim.config['engine'] = 'composite'
    sim.build_ecoli()

    # Grab initial bulk before running
    # After build_ecoli, generated_initial_state has the bulk
    init = sim.generated_initial_state
    if 'agents' in init:
        init = init['agents'][next(iter(init['agents']))]
    initial_bulk = init['bulk']['count'].copy()

    t0 = time.time()
    sim.run()
    runtime = time.time() - t0

    # Extract final bulk from composite state
    composite = sim._composite
    # Find the cell state (may be under agents/0 or directly)
    cell = None
    if 'agents' in composite.state:
        agents = composite.state['agents']
        first_key = next(iter(agents))
        cell = agents[first_key]
    else:
        cell = composite.state

    final_bulk = cell['bulk']['count'].copy()

    return runtime, initial_bulk, final_bulk


def compare(duration=4.0):
    print(f"=== Engine Comparison ({duration}s simulated) ===\n", flush=True)

    # Run in separate subprocesses to avoid shared state issues
    # (numba JIT recompilation hangs on second build in same process)
    import subprocess, pickle, tempfile

    def run_in_subprocess(func_name, duration):
        script = f"""
import pickle, sys
sys.path.insert(0, '.')
from runscripts.compare_engines import {func_name}
result = {func_name}({duration})
with open(sys.argv[1], 'wb') as f:
    pickle.dump(result, f)
"""
        with tempfile.NamedTemporaryFile(suffix='.pkl', delete=False) as tmp:
            tmp_path = tmp.name
        proc = subprocess.run(
            [sys.executable, '-c', script, tmp_path],
            capture_output=True, text=True, timeout=300)
        if proc.returncode != 0:
            print(f"  STDERR: {proc.stderr[-500:]}", flush=True)
            raise RuntimeError(f"{func_name} failed: {proc.stderr[-200:]}")
        with open(tmp_path, 'rb') as f:
            return pickle.load(f)

    print("Running v1 (vivarium)...", flush=True)
    v1_runtime, v1_init, v1_final, v1_ts = run_in_subprocess('run_v1', duration)
    print(f"  v1 done: {v1_runtime:.2f}s wall time\n", flush=True)

    print("Running v2 (composite)...", flush=True)
    v2_runtime, v2_init, v2_final = run_in_subprocess('run_v2', duration)
    print(f"  v2 done: {v2_runtime:.2f}s wall time\n", flush=True)

    # Check initial states match
    init_match = np.array_equal(v1_init, v2_init)
    print(f"Initial states match: {init_match}")
    if not init_match:
        diff_count = (v1_init != v2_init).sum()
        print(f"  WARNING: {diff_count} initial bulk values differ")

    # Count changed molecules
    v1_changed = (v1_init != v1_final).sum()
    v2_changed = (v2_init != v2_final).sum()
    print(f"v1 molecules changed: {v1_changed} / {len(v1_init)}")
    print(f"v2 molecules changed: {v2_changed} / {len(v2_init)}")

    # Compute correlation on shared changes
    both_changed = (v1_init != v1_final) & (v2_init != v2_final)
    n_both = both_changed.sum()
    print(f"Both changed: {n_both}")

    if n_both > 1:
        d1 = (v1_final[both_changed] - v1_init[both_changed]).astype(float)
        d2 = (v2_final[both_changed] - v2_init[both_changed]).astype(float)
        corr = np.corrcoef(d1, d2)[0, 1]
        print(f"Bulk delta correlation: {corr:.6f}")

        # Also check exact match
        exact = np.array_equal(v1_final, v2_final)
        print(f"Exact bulk match: {exact}")
        if not exact:
            diff_mask = v1_final != v2_final
            n_diff = diff_mask.sum()
            max_diff = np.abs(v1_final[diff_mask].astype(float) - v2_final[diff_mask].astype(float)).max()
            print(f"  {n_diff} molecules differ, max difference: {max_diff}")
    else:
        corr = 0.0
        print("Not enough shared changes for correlation")

    # Runtime comparison
    speedup = v1_runtime / v2_runtime if v2_runtime > 0 else float('inf')
    print(f"\nRuntime: v1={v1_runtime:.2f}s, v2={v2_runtime:.2f}s ({speedup:.2f}x)")

    # Mass fraction comparison from v1 timeseries
    # (v2 doesn't have timeseries yet, so compare final mass)
    try:
        from ecoli.library.schema import bulk_name_to_idx
        print("\n--- Mass Summary ---")
        # Just report bulk total counts as a proxy
        print(f"  v1 total bulk count: {v1_final.sum()}")
        print(f"  v2 total bulk count: {v2_final.sum()}")
        print(f"  difference: {v2_final.sum() - v1_final.sum()}")
    except ImportError:
        pass

    print(f"\n{'PASS' if corr > 0.90 else 'FAIL'}: correlation = {corr:.6f}")
    return corr


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Compare vEcoli engines')
    parser.add_argument('--duration', type=float, default=4.0,
                        help='Simulated seconds (default: 4)')
    args = parser.parse_args()

    with chdir(ROOT_PATH):
        corr = compare(args.duration)
        sys.exit(0 if corr > 0.90 else 1)
