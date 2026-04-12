"""Compare bundle-loaded v2 to v1.

Workflow:
  1. Build v2 from sim_data and save to bundle (not timed)
  2. Load v2 from bundle (timed)
  3. Run loaded v2 for `duration` seconds (timed)
  4. Run v1 for the same duration (timed, separate subprocess)
  5. Compare final bulk counts

Usage:
    python runscripts/compare_bundle_to_v1.py [--duration N]
                                               [--bundle-dir DIR]
                                               [--divide] [--division-threshold 290]
"""
import argparse, os, pickle, subprocess, sys, tempfile, time
from contextlib import chdir

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def build_and_save_bundle(bundle_dir, duration, divide, division_threshold):
    """Build fresh v2 composite, save to bundle, return initial bulk."""
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
    sim.processes = sim._retrieve_processes(
        sim.processes, sim.add_processes, sim.exclude_processes, sim.swap_processes)
    sim.topology = sim._retrieve_topology(
        sim.topology, sim.processes, sim.swap_processes, sim.log_updates)
    sim.process_configs = sim._retrieve_process_configs(
        sim.process_configs, sim.processes)

    core = allocate_core()
    core.register_types(ECOLI_TYPES)
    state = build_composite_native(core, sim.config)
    composite = Composite({'schema': {}, 'state': state}, core=core)
    composite.save_bundle(bundle_dir)


def load_and_run(bundle_dir, duration):
    """Load bundle, run, return (load_time, run_time, initial_bulk, final_bulk, divided)."""
    from ecoli.library.bigraph_types import ECOLI_TYPES
    from process_bigraph import Composite
    from bigraph_schema import allocate_core
    import numpy as np

    t0 = time.monotonic()
    core = allocate_core()
    core.register_types(ECOLI_TYPES)
    composite = Composite.load_bundle(bundle_dir, core=core)
    load_time = time.monotonic() - t0

    if 'agents' in composite.state:
        agents = composite.state['agents']
        cell = agents[next(iter(agents))]
    else:
        cell = composite.state
    initial_bulk = cell['bulk']['count'].copy()

    t0 = time.monotonic()
    try:
        composite.run(float(duration))
    except Exception as e:
        # Catch DivisionDetected and similar
        from ecoli.processes.cell_division import DivisionDetected
        if not isinstance(e, DivisionDetected):
            raise
    run_time = time.monotonic() - t0

    divided = False
    if 'agents' in composite.state:
        agents = composite.state['agents']
        if len(agents) > 1:
            divided = True
        cell = agents[next(iter(agents))]
    else:
        cell = composite.state
    final_bulk = cell['bulk']['count'].copy()

    return load_time, run_time, initial_bulk, final_bulk, divided


def run_v1_subproc(duration, divide, division_threshold):
    """Run v1 in subprocess, return (runtime, initial_bulk, final_bulk, divided)."""
    script = f"""
import pickle, sys, time
sys.path.insert(0, '.')
from runscripts.compare_engines import run_v1
t0 = time.monotonic()
runtime, ib, fb, divd = run_v1({duration}, divide={divide}, division_threshold={division_threshold!r})
with open(sys.argv[1], 'wb') as f:
    pickle.dump((runtime, ib, fb, divd), f)
"""
    with tempfile.NamedTemporaryFile(suffix='.pkl', delete=False) as tmp:
        tmp_path = tmp.name
    proc = subprocess.Popen(
        [sys.executable, '-u', '-c', script, tmp_path],
        stdout=sys.stdout, stderr=subprocess.STDOUT)
    proc.wait()
    if proc.returncode != 0:
        raise RuntimeError(f"v1 failed rc={proc.returncode}")
    with open(tmp_path, 'rb') as f:
        return pickle.load(f)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--duration', type=float, default=1.0)
    p.add_argument('--bundle-dir', default='out/cmp_bundle')
    p.add_argument('--divide', action='store_true')
    p.add_argument('--division-threshold', type=float, default=None)
    p.add_argument('--skip-build', action='store_true',
                   help='Reuse existing bundle at --bundle-dir')
    args = p.parse_args()

    with chdir(ROOT):
        import numpy as np

        if not args.skip_build:
            print(f"[build] saving bundle to {args.bundle_dir}/ ...", flush=True)
            t0 = time.monotonic()
            build_and_save_bundle(args.bundle_dir, args.duration,
                                  args.divide, args.division_threshold)
            print(f"[build] done in {time.monotonic()-t0:.1f}s (not timed)\n", flush=True)

        print("[v2 bundle] load + run...", flush=True)
        load_t, run_t, v2_init, v2_final, v2_divided = load_and_run(
            args.bundle_dir, args.duration)
        print(f"[v2 bundle] load={load_t:.2f}s, run={run_t:.2f}s, divided={v2_divided}", flush=True)
        print(f"[v2 bundle] TOTAL timed = {load_t + run_t:.2f}s\n", flush=True)

        print("[v1] running in subprocess...", flush=True)
        t0 = time.monotonic()
        v1_runtime, v1_init, v1_final, v1_divided = run_v1_subproc(
            args.duration, args.divide, args.division_threshold)
        print(f"[v1] total subprocess wall time = {time.monotonic()-t0:.2f}s "
              f"(includes v1 build), run={v1_runtime:.2f}s, divided={v1_divided}\n",
              flush=True)

        init_match = np.array_equal(v1_init, v2_init)
        print(f"Initial states match: {init_match}")
        if not init_match:
            print(f"  WARN: {(v1_init != v2_init).sum()} initial bulk differ")

        v1_changed = (v1_init != v1_final).sum()
        v2_changed = (v2_init != v2_final).sum()
        print(f"v1 changed: {v1_changed}/{len(v1_init)}")
        print(f"v2 changed: {v2_changed}/{len(v2_init)}")

        both = (v1_init != v1_final) & (v2_init != v2_final)
        n_both = both.sum()
        print(f"Both changed: {n_both}")
        if n_both > 1:
            d1 = (v1_final[both] - v1_init[both]).astype(float)
            d2 = (v2_final[both] - v2_init[both]).astype(float)
            corr = np.corrcoef(d1, d2)[0, 1]
            print(f"Bulk delta correlation: {corr:.6f}")
            exact = np.array_equal(v1_final, v2_final)
            print(f"Exact bulk match: {exact}")
            if not exact:
                diff = v1_final != v2_final
                n_diff = diff.sum()
                max_diff = np.abs(v1_final[diff].astype(np.int64) -
                                  v2_final[diff].astype(np.int64)).max()
                print(f"  {n_diff} differ, max |diff|: {max_diff}")

        speedup = v1_runtime / run_t if run_t > 0 else float('inf')
        print(f"\nSpeed (run only): v1={v1_runtime:.2f}s vs v2={run_t:.2f}s "
              f"({speedup:.2f}x)")
        print(f"Speed (load+run): v2 total = {load_t + run_t:.2f}s")


if __name__ == '__main__':
    main()
