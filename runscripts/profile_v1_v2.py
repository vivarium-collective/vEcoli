"""Profile v1 vs v2 sims for the same number of ticks and compare hot
spots. Usage:

    python runscripts/profile_v1_v2.py --ticks 100 \
        [--engine v1|v2|both]

Writes cProfile dumps to /tmp/profile_v{1,2}.prof and prints the top
functions for each.
"""
import argparse
import cProfile
import os
import pstats
import sys
import time


def run_v2(ticks):
    from ecoli.experiments.ecoli_master_sim import EcoliSim
    from ecoli.library.bigraph_types import ECOLI_TYPES
    from ecoli.composites.ecoli_composite import build_composite_native
    from process_bigraph import Composite
    from bigraph_schema import allocate_core
    sim = EcoliSim.from_file(filepath='configs/two_generations_v2.json')
    sim.config['engine'] = 'composite'
    sim.config['emitter'] = 'null'
    sim.processes = sim._retrieve_processes(
        sim.processes, sim.add_processes, sim.exclude_processes,
        sim.swap_processes)
    sim.topology = sim._retrieve_topology(
        sim.topology, sim.processes, sim.swap_processes, sim.log_updates)
    sim.process_configs = sim._retrieve_process_configs(
        sim.process_configs, sim.processes)
    core = allocate_core()
    core.register_types(ECOLI_TYPES)
    state = build_composite_native(core, sim.config)
    comp = Composite({'schema': {}, 'state': state}, core=core)
    print(f'V2 built. Profiling {ticks} ticks...', flush=True)
    t0 = time.monotonic()
    for _ in range(ticks):
        comp.run(1.0)
    elapsed = time.monotonic() - t0
    print(f'V2: {ticks} ticks in {elapsed:.2f}s ({elapsed/ticks*1000:.1f}ms/tick)',
          flush=True)
    return elapsed


def run_v1(ticks):
    from ecoli.experiments.ecoli_master_sim import EcoliSim
    sim = EcoliSim.from_file(filepath='configs/two_generations_v1.json')
    sim.config['sim_data_path'] = (
        'out/two_generations_v1/parca/kb/simData.cPickle')
    sim.config['engine'] = 'vivarium'
    sim.config['emitter'] = 'null'
    sim.config['max_duration'] = ticks
    sim.config['fail_at_max_duration'] = False
    sim.config['progress_bar'] = False
    sim.build_ecoli()
    print(f'V1 built. Profiling {ticks} ticks via run()...', flush=True)
    t0 = time.monotonic()
    sim.run()
    elapsed = time.monotonic() - t0
    print(f'V1: ~{ticks} ticks in {elapsed:.2f}s ({elapsed/ticks*1000:.1f}ms/tick)',
          flush=True)
    return elapsed


def profile(target, ticks, out_path):
    pr = cProfile.Profile()
    pr.enable()
    target(ticks)
    pr.disable()
    pr.dump_stats(out_path)
    print(f'\n=== TOP 25 by cumulative time ({out_path}) ===')
    p = pstats.Stats(out_path)
    p.sort_stats('cumulative').print_stats(25)
    print(f'\n=== TOP 20 by total time (own time) ===')
    p.sort_stats('tottime').print_stats(20)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--ticks', type=int, default=100)
    p.add_argument('--engine', choices=['v1', 'v2', 'both'], default='both')
    args = p.parse_args()

    if args.engine in ('v1', 'both'):
        profile(run_v1, args.ticks, '/tmp/profile_v1.prof')
    if args.engine in ('v2', 'both'):
        profile(run_v2, args.ticks, '/tmp/profile_v2.prof')


if __name__ == '__main__':
    main()
