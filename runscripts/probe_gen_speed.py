"""Measure per-tick wall time for fresh-build composite vs loaded-bundle
composite. If loaded is materially slower, we've confirmed the gen_2
regression source (task #5).

Runs ~50 1s ticks on a fresh composite, saves a bundle, loads it, runs
~50 1s ticks on the loaded composite. Reports ticks/sec.
"""
import argparse
import os
import sys
import tempfile
import time


def build_composite(config_path):
    from ecoli.experiments.ecoli_master_sim import EcoliSim
    from ecoli.library.bigraph_types import ECOLI_TYPES
    from ecoli.composites.ecoli_composite import build_composite_native
    from process_bigraph import Composite
    from bigraph_schema import allocate_core

    sim = EcoliSim.from_file(filepath=config_path)
    sim.config['engine'] = 'composite'
    sim.config['emitter'] = 'null'
    sim.processes = sim._retrieve_processes(
        sim.processes, sim.add_processes,
        sim.exclude_processes, sim.swap_processes)
    sim.topology = sim._retrieve_topology(
        sim.topology, sim.processes, sim.swap_processes, sim.log_updates)
    sim.process_configs = sim._retrieve_process_configs(
        sim.process_configs, sim.processes)
    core = allocate_core()
    core.register_types(ECOLI_TYPES)
    state = build_composite_native(core, sim.config)
    return Composite({'schema': {}, 'state': state}, core=core)


def load_composite(bundle_dir):
    from ecoli.library.bigraph_types import ECOLI_TYPES
    from process_bigraph import Composite
    from bigraph_schema import allocate_core
    core = allocate_core()
    core.register_types(ECOLI_TYPES)
    return Composite.load_bundle(bundle_dir, core=core)


def run_and_time(comp, n_ticks, label):
    """Run n_ticks of 1s each, printing per-10-tick timings."""
    print(f'\n== Running {label} for {n_ticks} 1s ticks ==', flush=True)
    t0 = time.monotonic()
    last = t0
    for i in range(n_ticks):
        comp.run(1.0)
        if (i + 1) % 10 == 0:
            now = time.monotonic()
            print(f'  {label} ticks {i-8}..{i}: '
                  f'{(now-last):.2f}s wall '
                  f'({(now-last)/10:.3f}s/tick)', flush=True)
            last = now
    total = time.monotonic() - t0
    print(f'{label} TOTAL: {total:.2f}s for {n_ticks} ticks '
          f'({total/n_ticks:.3f}s/tick, {n_ticks/total:.2f} ticks/sec)',
          flush=True)
    return total


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--config', default='configs/two_generations_v2.json')
    p.add_argument('--ticks', type=int, default=50)
    args = p.parse_args()

    print('=== Phase 1: build fresh composite ===', flush=True)
    pre = build_composite(args.config)
    pre_dur = run_and_time(pre, args.ticks, 'FRESH')

    out_dir = tempfile.mkdtemp(prefix='probe_speed_')
    print(f'\n=== Saving bundle -> {out_dir} ===', flush=True)
    pre.save_bundle(out_dir)

    print('\n=== Phase 2: load bundle ===', flush=True)
    from process_bigraph.types.process import _shared_processes
    _shared_processes.clear()
    post = load_composite(out_dir)
    post_dur = run_and_time(post, args.ticks, 'LOADED')

    ratio = post_dur / pre_dur if pre_dur > 0 else float('nan')
    print(f'\n=== SUMMARY ===')
    print(f'FRESH : {pre_dur:.2f}s  ({args.ticks/pre_dur:.2f} ticks/s)')
    print(f'LOADED: {post_dur:.2f}s ({args.ticks/post_dur:.2f} ticks/s)')
    print(f'LOADED/FRESH ratio: {ratio:.2f}x')


if __name__ == '__main__':
    main()
