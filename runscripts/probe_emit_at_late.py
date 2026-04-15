"""Measure tick wall with/without parquet emit AT HIGH sim_t (loaded
from a gen_1 daughter bundle). At sim_t~1380+, cell state is bigger
so emit rows are bigger. If emit is cheap at t=0 but costly at t=1400,
we've found the gen_2 slowdown.
"""
import argparse
import sys
import tempfile
import time


def load_composite(bundle_dir):
    from ecoli.library.bigraph_types import ECOLI_TYPES
    from process_bigraph import Composite
    from bigraph_schema import allocate_core
    core = allocate_core()
    core.register_types(ECOLI_TYPES)
    return Composite.load_bundle(bundle_dir, core=core)


def _make_sim(config_path):
    from ecoli.experiments.ecoli_master_sim import EcoliSim
    sim = EcoliSim.from_file(filepath=config_path)
    sim.config['engine'] = 'composite'
    sim.processes = sim._retrieve_processes(
        sim.processes, sim.add_processes,
        sim.exclude_processes, sim.swap_processes)
    sim.topology = sim._retrieve_topology(
        sim.topology, sim.processes, sim.swap_processes, sim.log_updates)
    sim.process_configs = sim._retrieve_process_configs(
        sim.process_configs, sim.processes)
    return sim


def main():
    p = argparse.ArgumentParser()
    p.add_argument(
        '--bundle',
        default='out/two_generations_v2/daughter_states/variant=0/seed=0/generation=1/agent_id=0/daughter_state_0')
    p.add_argument('--config', default='configs/two_generations_v2.json')
    p.add_argument('--ticks', type=int, default=200)
    args = p.parse_args()

    from ecoli.library.parquet_emitter import ParquetEmitter
    from process_bigraph.types.process import _shared_processes

    sim = _make_sim(args.config)

    # Phase 1: load + run WITHOUT emit
    _shared_processes.clear()
    print('Loading + NO_EMIT...', flush=True)
    comp_a = load_composite(args.bundle)
    t0 = time.monotonic()
    bstart = t0
    bucket = max(10, args.ticks // 4)
    for i in range(args.ticks):
        comp_a.run(1.0)
        if (i + 1) % bucket == 0:
            now = time.monotonic()
            gt = comp_a.state.get('global_time', 0.0)
            b = now - bstart
            print(f'  NO_EMIT tick {i+1} (sim_t={gt:.0f}): '
                  f'{b:.1f}s ({b/bucket:.3f}s/tick)', flush=True)
            bstart = now
    no_emit = time.monotonic() - t0
    print(f'NO_EMIT TOTAL: {no_emit:.2f}s ({no_emit/args.ticks:.3f}s/tick)',
          flush=True)

    # Phase 2: load + run WITH emit
    _shared_processes.clear()
    print('\nLoading + WITH_EMIT...', flush=True)
    comp_b = load_composite(args.bundle)
    sim._composite = comp_b

    out_dir = tempfile.mkdtemp(prefix='emit_late_')
    emitter = ParquetEmitter({'out_dir': out_dir, 'threaded': False})
    cfg = sim.get_metadata()
    cfg['experiment_id'] = 'probe_emit_late'
    cfg['variant'] = 0
    cfg['lineage_seed'] = 0
    cfg['agent_id'] = '0'
    cfg['initial_global_time'] = float(comp_b.state.get('global_time', 0.0))
    cfg['output_metadata'] = sim._collect_output_metadata()
    emitter.emit({'table': 'configuration', 'data': {'metadata': cfg}})

    t0 = time.monotonic()
    bstart = t0
    for i in range(args.ticks):
        comp_b.run(1.0)
        sim._emit_composite_history(emitter, comp_b)
        if (i + 1) % bucket == 0:
            now = time.monotonic()
            gt = comp_b.state.get('global_time', 0.0)
            b = now - bstart
            print(f'  WITH_EMIT tick {i+1} (sim_t={gt:.0f}): '
                  f'{b:.1f}s ({b/bucket:.3f}s/tick)', flush=True)
            bstart = now
    with_emit = time.monotonic() - t0
    print(f'WITH_EMIT TOTAL: {with_emit:.2f}s ({with_emit/args.ticks:.3f}s/tick)',
          flush=True)
    emitter.finalize()

    print('\n=== SUMMARY ===')
    print(f'NO_EMIT  : {no_emit:.2f}s ({no_emit/args.ticks:.3f}s/tick)')
    print(f'WITH_EMIT: {with_emit:.2f}s ({with_emit/args.ticks:.3f}s/tick)')
    if no_emit > 0:
        print(f'WITH/NO ratio: {with_emit/no_emit:.2f}x')


if __name__ == '__main__':
    main()
