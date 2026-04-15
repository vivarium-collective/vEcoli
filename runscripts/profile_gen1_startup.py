"""Profile v2 gen_1 startup (build_composite_native + Composite init).
Compare to v1's build_ecoli.
"""
import argparse
import cProfile
import os
import pstats
import time


def time_v2_startup():
    from ecoli.experiments.ecoli_master_sim import EcoliSim
    from ecoli.library.bigraph_types import ECOLI_TYPES
    from ecoli.composites.ecoli_composite import build_composite_native
    from process_bigraph import Composite
    from process_bigraph.types.process import (
        register_types as register_pb_types)
    from bigraph_schema import Core, BASE_TYPES

    sim = EcoliSim.from_file(filepath='configs/two_generations_v2.json')
    sim.config['engine'] = 'composite'
    sim.config['emitter'] = 'null'

    t0 = time.monotonic()
    sim.processes = sim._retrieve_processes(
        sim.processes, sim.add_processes,
        sim.exclude_processes, sim.swap_processes)
    sim.topology = sim._retrieve_topology(
        sim.topology, sim.processes, sim.swap_processes, sim.log_updates)
    sim.process_configs = sim._retrieve_process_configs(
        sim.process_configs, sim.processes)
    print(f'V2 retrieve_*: {time.monotonic()-t0:.2f}s')

    t0 = time.monotonic()
    core = Core(BASE_TYPES)
    register_pb_types(core)
    core.register_types(ECOLI_TYPES)
    print(f'V2 Core init: {time.monotonic()-t0:.2f}s')

    t0 = time.monotonic()
    state = build_composite_native(core, sim.config)
    print(f'V2 build_composite_native: {time.monotonic()-t0:.2f}s')

    t0 = time.monotonic()
    comp = Composite({'schema': {}, 'state': state}, core=core)
    print(f'V2 Composite.__init__ (realize): {time.monotonic()-t0:.2f}s')


def time_v1_startup():
    from ecoli.experiments.ecoli_master_sim import EcoliSim
    sim = EcoliSim.from_file(filepath='configs/two_generations_v1.json')
    sim.config['sim_data_path'] = (
        'out/two_generations_v1/parca/kb/simData.cPickle')
    sim.config['engine'] = 'vivarium'
    sim.config['emitter'] = 'null'

    t0 = time.monotonic()
    sim.build_ecoli()
    print(f'V1 build_ecoli total: {time.monotonic()-t0:.2f}s')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--engine', choices=['v1', 'v2', 'both'], default='both')
    p.add_argument('--profile-v2', action='store_true',
                   help='Run cProfile on v2 startup')
    args = p.parse_args()

    if args.engine in ('v1', 'both'):
        time_v1_startup()
    print()
    if args.engine in ('v2', 'both'):
        if args.profile_v2:
            pr = cProfile.Profile()
            pr.enable()
            time_v2_startup()
            pr.disable()
            pr.dump_stats('/tmp/profile_v2_startup.prof')
            print('\n=== top 30 by cumulative time ===')
            pstats.Stats('/tmp/profile_v2_startup.prof').sort_stats(
                'cumulative').print_stats(30)
            print('\n=== top 25 by own time ===')
            pstats.Stats('/tmp/profile_v2_startup.prof').sort_stats(
                'tottime').print_stats(25)
        else:
            time_v2_startup()


if __name__ == '__main__':
    main()
