"""Measure tick wall time with and without parquet emit.

If ticks with emit are materially slower than ticks without, the emit
is the bottleneck. Compares running 100 ticks with emitter='null' vs
emitter='parquet' — both from same fresh composite.
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
    sim.config['divide'] = False
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
    return sim, Composite({'schema': {}, 'state': state}, core=core)


def run_with_emit(sim, comp, n_ticks, label):
    from ecoli.library.parquet_emitter import ParquetEmitter
    out_dir = tempfile.mkdtemp(prefix=f'emit_{label}_')
    emitter = ParquetEmitter({'out_dir': out_dir, 'threaded': False})
    sim._composite = comp  # needed by _collect_output_metadata
    cfg_metadata = sim.get_metadata()
    cfg_metadata['experiment_id'] = f'probe_{label}'
    cfg_metadata['variant'] = 0
    cfg_metadata['lineage_seed'] = 0
    cfg_metadata['agent_id'] = '0'
    cfg_metadata['initial_global_time'] = float(comp.state.get('global_time', 0.0))
    cfg_metadata['output_metadata'] = sim._collect_output_metadata()
    emitter.emit({'table': 'configuration', 'data': {'metadata': cfg_metadata}})

    print(f'\n== {label} (emit=parquet): {n_ticks} ticks ==', flush=True)
    t0 = time.monotonic()
    last = t0
    bucket = max(10, n_ticks // 5)
    for i in range(n_ticks):
        comp.run(1.0)
        sim._emit_composite_history(emitter, comp)
        if (i + 1) % bucket == 0:
            now = time.monotonic()
            print(f'  ticks {i+2-bucket}..{i+1}: {(now-last):.2f}s '
                  f'({(now-last)/bucket:.3f}s/tick)', flush=True)
            last = now
    total = time.monotonic() - t0
    print(f'  {label} TOTAL: {total:.2f}s ({total/n_ticks:.3f}s/tick)',
          flush=True)
    emitter.finalize()
    return total


def run_without_emit(comp, n_ticks, label):
    print(f'\n== {label} (no emit): {n_ticks} ticks ==', flush=True)
    t0 = time.monotonic()
    last = t0
    bucket = max(10, n_ticks // 5)
    for i in range(n_ticks):
        comp.run(1.0)
        if (i + 1) % bucket == 0:
            now = time.monotonic()
            print(f'  ticks {i+2-bucket}..{i+1}: {(now-last):.2f}s '
                  f'({(now-last)/bucket:.3f}s/tick)', flush=True)
            last = now
    total = time.monotonic() - t0
    print(f'  {label} TOTAL: {total:.2f}s ({total/n_ticks:.3f}s/tick)',
          flush=True)
    return total


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--config', default='configs/two_generations_v2.json')
    p.add_argument('--ticks', type=int, default=100)
    args = p.parse_args()

    print('Build fresh composites (no warmup — measure at sim_t=0)...',
          flush=True)
    sim_a, comp_a = build_composite(args.config)
    sim_b, comp_b = build_composite(args.config)

    no_emit = run_without_emit(comp_a, args.ticks, 'NO_EMIT')
    with_emit = run_with_emit(sim_b, comp_b, args.ticks, 'WITH_EMIT')

    print('\n=== SUMMARY (both at sim_t=1300) ===')
    print(f'NO_EMIT   : {no_emit:.2f}s ({no_emit/args.ticks:.3f}s/tick)')
    print(f'WITH_EMIT : {with_emit:.2f}s ({with_emit/args.ticks:.3f}s/tick)')
    if no_emit > 0:
        print(f'WITH/NO ratio: {with_emit/no_emit:.2f}x')


if __name__ == '__main__':
    main()
