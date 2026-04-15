"""Time per-tick wall as sim_time grows. If v2 inherently slows down
at high sim_time, gen_1's first 1380 ticks will be fast and the next
~200 ticks (analog of gen_2's start) will be slow — a single fresh
process can answer this without bundle save/load.
"""
import argparse
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
    sim.config['divide'] = False  # don't break out on divide; keep going
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


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--config', default='configs/two_generations_v2.json')
    p.add_argument('--total-ticks', type=int, default=1600)
    p.add_argument('--bucket', type=int, default=100)
    args = p.parse_args()

    print(f'Building...', flush=True)
    comp = build_composite(args.config)

    print(f'Running {args.total_ticks} ticks, reporting every {args.bucket}...',
          flush=True)
    bucket_start = time.monotonic()
    overall_start = bucket_start
    try:
        for i in range(args.total_ticks):
            comp.run(1.0)
            if (i + 1) % args.bucket == 0:
                now = time.monotonic()
                gt = comp.state.get('global_time', 0.0)
                bucket = now - bucket_start
                print(f'  tick {i+1:>5} (sim_t={gt:.0f}): '
                      f'{bucket:.1f}s wall ({bucket/args.bucket:.3f}s/tick)',
                      flush=True)
                bucket_start = now
                # Halt when we hit two agents (post-divide)
                if len(comp.state.get('agents', {})) > 1:
                    print(f'  Divided at tick {i+1}', flush=True)
                    break
    except Exception as e:
        print(f'  EXCEPTION: {type(e).__name__}: {e}', flush=True)

    total = time.monotonic() - overall_start
    print(f'\nTotal: {total:.1f}s', flush=True)


if __name__ == '__main__':
    main()
