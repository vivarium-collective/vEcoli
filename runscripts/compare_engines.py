"""Compare vivarium (v1) and composite (v2) engines on an identical EcoliSim.

Run from the vEcoli root directory:
    python runscripts/compare_engines.py [--duration 4] [--divide]
                                          [--division-threshold 290]
"""

import argparse
import copy
import time
import sys
import os

import numpy as np
from contextlib import chdir

ROOT_PATH = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run_v1(duration, divide=False, division_threshold=None):
    """Run vivarium engine, return (runtime, initial_bulk, final_bulk, divided)."""
    from ecoli.experiments.ecoli_master_sim import EcoliSim
    from ecoli.library.schema import not_a_process
    from ecoli.experiments.ecoli_master_sim import TimeLimitError
    from ecoli.processes.cell_division import DivisionDetected

    sim = EcoliSim.from_file()
    sim.max_duration = int(duration)
    sim.emitter = 'null'
    sim.divide = divide
    if division_threshold is not None:
        sim.division_threshold = division_threshold
    sim.build_ecoli()

    init = sim.generated_initial_state
    if 'agents' in init:
        init = init['agents'][next(iter(init['agents']))]
    initial_bulk = init['bulk']['count'].copy()

    divided = False
    t0 = time.time()
    try:
        sim.run()
    except DivisionDetected:
        divided = True
    except TimeLimitError:
        pass
    runtime = time.time() - t0

    state = sim.ecoli_experiment.state.get_value(condition=not_a_process)
    if 'agents' in state and isinstance(state['agents'], dict):
        agent_keys = list(state['agents'].keys())
        print(f"  v1 post-run agents: {agent_keys}", flush=True)
        if agent_keys:
            first = state['agents'][agent_keys[0]]
            if isinstance(first, dict) and 'bulk' in first:
                final_bulk = first['bulk']['count'].copy()
            else:
                final_bulk = initial_bulk.copy()
        else:
            final_bulk = initial_bulk.copy()
    elif 'bulk' in state:
        final_bulk = state['bulk']['count'].copy()
    else:
        final_bulk = initial_bulk.copy()

    sim.ecoli_experiment.end()

    return runtime, initial_bulk, final_bulk, divided


def run_v2(duration, divide=False, division_threshold=None, parallel_steps=False, parallel_workers=None):
    """Run composite engine, return (runtime, initial_bulk, final_bulk, divided)."""
    from ecoli.experiments.ecoli_master_sim import EcoliSim
    from ecoli.composites.ecoli_composite import build_composite_native
    from ecoli.library.bigraph_types import ECOLI_TYPES
    from process_bigraph import Composite
    from bigraph_schema import allocate_core

    sim = EcoliSim.from_file()
    sim.max_duration = int(duration)
    sim.emitter = 'null'
    sim.divide = divide
    if division_threshold is not None:
        sim.division_threshold = division_threshold

    # Resolve registries (cheap, no vivarium engine)
    sim.processes = sim._retrieve_processes(
        sim.processes, sim.add_processes, sim.exclude_processes, sim.swap_processes)
    sim.topology = sim._retrieve_topology(
        sim.topology, sim.processes, sim.swap_processes, sim.log_updates)
    sim.process_configs = sim._retrieve_process_configs(
        sim.process_configs, sim.processes)

    core = allocate_core()
    core.register_types(ECOLI_TYPES)

    state = build_composite_native(core, sim.config)
    composite_config = {'schema': {}, 'state': state}
    if parallel_steps:
        composite_config['parallel_steps'] = True
        if parallel_workers is not None:
            composite_config['parallel_workers'] = parallel_workers
    composite = Composite(composite_config, core=core)
    composite.to_run = []

    if 'agents' in composite.state:
        cell = composite.state['agents'][next(iter(composite.state['agents']))]
    else:
        cell = composite.state
    initial_bulk = cell['bulk']['count'].copy()

    t0 = time.time()
    composite.run(float(duration))
    runtime = time.time() - t0

    # Note: v2 native does NOT yet implement the _divide handler, so even
    # if Division fires, the mother is not replaced by daughters. divided
    # is reported as False until a divide_map type lands.
    divided = False
    if 'agents' in composite.state:
        cell = composite.state['agents'][next(iter(composite.state['agents']))]
    else:
        cell = composite.state
    final_bulk = cell['bulk']['count'].copy()

    return runtime, initial_bulk, final_bulk, divided


def compare(duration=4.0, divide=False, division_threshold=None, timeout=600,
            parallel_steps=False, parallel_workers=None):
    label = f"{duration}s simulated"
    if divide:
        label += ", divide=True"
        if division_threshold is not None:
            label += f", threshold={division_threshold}"
    if parallel_steps:
        label += f", parallel_steps=True"
        if parallel_workers is not None:
            label += f"({parallel_workers} workers)"
    print(f"=== Engine Comparison ({label}) ===\n", flush=True)

    # Run in separate subprocesses to avoid shared state issues
    # (numba JIT recompilation hangs on second build in same process)
    import subprocess, pickle, tempfile

    def run_in_subprocess(func_name, duration, divide, division_threshold):
        threshold_arg = (
            f", division_threshold={division_threshold!r}"
            if division_threshold is not None else "")
        # parallel_steps only applies to v2
        v2_kwargs = ""
        if func_name == 'run_v2' and parallel_steps:
            v2_kwargs = f", parallel_steps=True"
            if parallel_workers is not None:
                v2_kwargs += f", parallel_workers={parallel_workers}"
        script = f"""
import pickle, sys
sys.path.insert(0, '.')
from runscripts.compare_engines import {func_name}
result = {func_name}({duration}, divide={divide}{threshold_arg}{v2_kwargs})
with open(sys.argv[1], 'wb') as f:
    pickle.dump(result, f)
"""
        with tempfile.NamedTemporaryFile(suffix='.pkl', delete=False) as tmp:
            tmp_path = tmp.name
        # Stream stdout/stderr line-by-line so progress is visible — using
        # subprocess.run(capture_output=True) buffers everything until the
        # subprocess exits, which makes the comparison appear to hang.
        proc = subprocess.Popen(
            [sys.executable, '-u', '-c', script, tmp_path],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1)
        tail = []
        for line in proc.stdout:
            print(f"  | {line}", end='', flush=True)
            tail.append(line)
            if len(tail) > 50:
                tail.pop(0)
        proc.wait(timeout=timeout)
        if proc.returncode != 0:
            raise RuntimeError(
                f"{func_name} failed (rc={proc.returncode}):\n{''.join(tail[-20:])}")
        with open(tmp_path, 'rb') as f:
            return pickle.load(f)

    print("Running v1 (vivarium)...", flush=True)
    v1_runtime, v1_init, v1_final, v1_divided = run_in_subprocess(
        'run_v1', duration, divide, division_threshold)
    print(f"  v1 done: {v1_runtime:.2f}s wall time, divided={v1_divided}\n", flush=True)

    print("Running v2 (composite)...", flush=True)
    v2_runtime, v2_init, v2_final, v2_divided = run_in_subprocess(
        'run_v2', duration, divide, division_threshold)
    print(f"  v2 done: {v2_runtime:.2f}s wall time, divided={v2_divided}\n", flush=True)

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
    parser.add_argument('--divide', action='store_true',
                        help='Enable division (adds Division/MarkDPeriod/StopAfterDivision)')
    parser.add_argument('--division-threshold', type=float, default=None,
                        help='Override division_threshold (e.g. 290 for early divide test)')
    parser.add_argument('--timeout', type=int, default=600,
                        help='Per-subprocess timeout in seconds (default: 600)')
    parser.add_argument('--parallel-steps', action='store_true',
                        help='Enable v2 inner thread pool for layer parallelism')
    parser.add_argument('--parallel-workers', type=int, default=None,
                        help='Cap thread pool size (default: layer width)')
    args = parser.parse_args()

    with chdir(ROOT_PATH):
        corr = compare(
            args.duration,
            divide=args.divide,
            division_threshold=args.division_threshold,
            parallel_steps=args.parallel_steps,
            parallel_workers=args.parallel_workers,
            timeout=args.timeout)
        sys.exit(0 if corr > 0.90 else 1)
