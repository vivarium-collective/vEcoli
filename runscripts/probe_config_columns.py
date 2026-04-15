"""Verify v2 ``configuration`` emit column set matches v1's.

Builds a composite, invokes the same emit the sim does, then diffs
against v1's existing configuration.pq column set. Surfaces missing
keys (e.g. ``git_hash``) without running the full workflow.
"""
import argparse
import os
import sys
import tempfile


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--config', default='configs/two_generations_v2.json')
    p.add_argument(
        '--v1-config-pq',
        default='out/two_generations_v1/configuration/'
                'experiment_id=two_generations_v1/variant=0/'
                'lineage_seed=0/generation=1/agent_id=0/config.pq')
    args = p.parse_args()

    import polars as pl
    from ecoli.experiments.ecoli_master_sim import EcoliSim
    from ecoli.library.bigraph_types import ECOLI_TYPES
    from ecoli.library.parquet_emitter import ParquetEmitter
    from ecoli.composites.ecoli_composite import build_composite_native
    from process_bigraph import Composite
    from bigraph_schema import allocate_core

    sim = EcoliSim.from_file(filepath=args.config)
    sim.config['engine'] = 'composite'
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
    ecoli = Composite({'schema': {}, 'state': state}, core=core)
    sim._composite = ecoli

    # Redirect parquet output to a tempdir so we don't clobber real
    # simulation data.
    out = tempfile.mkdtemp(prefix='probe_cfg_')
    emitter_arg = dict(sim.emitter_arg or {})
    emitter_arg['out_dir'] = out
    emitter = ParquetEmitter(emitter_arg)

    cfg_metadata = sim.get_metadata()
    cfg_metadata['experiment_id'] = sim.experiment_id
    cfg_metadata['variant'] = sim.config.get('variant', 0)
    cfg_metadata['lineage_seed'] = sim.lineage_seed
    cfg_metadata['agent_id'] = str(sim.agent_id)
    cfg_metadata['initial_global_time'] = float(
        ecoli.state.get('global_time', 0.0))
    cfg_metadata['output_metadata'] = sim._collect_output_metadata()
    emitter.emit({
        'table': 'configuration',
        'data': {'metadata': cfg_metadata},
    })

    # Where does ParquetEmitter write the config file?
    import glob
    config_files = glob.glob(f'{out}/**/config.pq', recursive=True)
    if not config_files:
        print('ERROR: no config.pq produced')
        sys.exit(1)
    v2 = pl.read_parquet(config_files[0])
    v2_cols = set(v2.columns)
    print(f'v2 configuration columns: {len(v2_cols)}')

    v1 = pl.read_parquet(args.v1_config_pq)
    v1_cols = set(v1.columns)
    print(f'v1 configuration columns: {len(v1_cols)}')

    missing = v1_cols - v2_cols
    extra = v2_cols - v1_cols
    print(f'\nMissing in v2 ({len(missing)}):')
    for c in sorted(missing)[:30]:
        print(f'  {c}')
    if len(missing) > 30:
        print(f'  ... {len(missing) - 30} more')
    print(f'\nExtra in v2 ({len(extra)}):')
    for c in sorted(extra)[:30]:
        print(f'  {c}')
    if len(extra) > 30:
        print(f'  ... {len(extra) - 30} more')


if __name__ == '__main__':
    main()
