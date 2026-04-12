"""Test v2 save-bundle → load-bundle → run workflow.

Saves the composite to a bundle directory, loads it back, runs the loaded
copy for the given duration, and compares final bulk counts to v1.

Usage:
    python runscripts/bundle_roundtrip.py [--duration N] [--bundle-dir DIR]
"""
import argparse, os, sys, time
from contextlib import chdir

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def build_fresh_v2(duration):
    """Build v2 from scratch (no bundle), return composite."""
    from ecoli.experiments.ecoli_master_sim import EcoliSim
    from ecoli.composites.ecoli_composite import build_composite_native
    from ecoli.library.bigraph_types import ECOLI_TYPES
    from process_bigraph import Composite
    from bigraph_schema import allocate_core

    sim = EcoliSim.from_file()
    sim.max_duration = int(duration)
    sim.emitter = 'null'
    sim.divide = False
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
    return composite, core


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--duration', type=float, default=1.0)
    parser.add_argument('--bundle-dir', default='out/bundle_roundtrip')
    args = parser.parse_args()

    with chdir(ROOT):
        import numpy as np
        from ecoli.library.bigraph_types import ECOLI_TYPES
        from process_bigraph import Composite
        from bigraph_schema import allocate_core

        # 1. Build fresh and save
        print(f"[1] Building fresh composite...", flush=True)
        t0 = time.monotonic()
        composite_a, core_a = build_fresh_v2(args.duration)
        print(f"    built in {time.monotonic()-t0:.1f}s", flush=True)

        # Capture fresh initial bulk for reference
        if 'agents' in composite_a.state:
            cell_a = composite_a.state['agents'][next(iter(composite_a.state['agents']))]
        else:
            cell_a = composite_a.state
        fresh_initial_bulk = cell_a['bulk']['count'].copy()

        print(f"[2] Saving bundle to {args.bundle_dir}/...", flush=True)
        t0 = time.monotonic()
        summary = composite_a.save_bundle(args.bundle_dir)
        print(f"    saved in {time.monotonic()-t0:.1f}s", flush=True)

        # 2. Load bundle into a fresh composite
        print(f"[3] Loading bundle...", flush=True)
        t0 = time.monotonic()
        core_b = allocate_core()
        core_b.register_types(ECOLI_TYPES)
        composite_b = Composite.load_bundle(args.bundle_dir, core=core_b)
        print(f"    loaded in {time.monotonic()-t0:.1f}s", flush=True)

        if 'agents' in composite_b.state:
            cell_b = composite_b.state['agents'][next(iter(composite_b.state['agents']))]
        else:
            cell_b = composite_b.state
        loaded_initial_bulk = cell_b['bulk']['count'].copy()

        if np.array_equal(fresh_initial_bulk, loaded_initial_bulk):
            print("    initial bulk matches fresh build ✓", flush=True)
        else:
            diff = (fresh_initial_bulk != loaded_initial_bulk).sum()
            print(f"    initial bulk DIFFERS: {diff} molecules differ", flush=True)

        # 3. Run loaded composite
        print(f"[4] Running loaded composite for {args.duration}s...", flush=True)
        t0 = time.monotonic()
        composite_b.run(float(args.duration))
        loaded_final_bulk = cell_b['bulk']['count'].copy()
        print(f"    ran in {time.monotonic()-t0:.1f}s", flush=True)

        # 4. Run fresh composite the same way
        print(f"[5] Running fresh composite for {args.duration}s...", flush=True)
        t0 = time.monotonic()
        composite_a.run(float(args.duration))
        fresh_final_bulk = cell_a['bulk']['count'].copy()
        print(f"    ran in {time.monotonic()-t0:.1f}s", flush=True)

        # 5. Compare final bulk counts
        if np.array_equal(fresh_final_bulk, loaded_final_bulk):
            print(f"\n✓ PASS: bundle roundtrip produces identical final bulk"
                  f" ({(fresh_final_bulk != fresh_initial_bulk).sum()} molecules changed)", flush=True)
            return 0
        else:
            diff_mask = fresh_final_bulk != loaded_final_bulk
            print(f"\n✗ FAIL: {diff_mask.sum()} molecules differ between fresh and loaded runs", flush=True)
            max_diff = np.abs(fresh_final_bulk[diff_mask].astype(np.int64)
                              - loaded_final_bulk[diff_mask].astype(np.int64)).max()
            print(f"  max |diff|: {max_diff}", flush=True)
            return 1


if __name__ == '__main__':
    sys.exit(main())
