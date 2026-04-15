"""Build a v2 composite and dump what ``_collect_output_metadata`` produces.

Fast local probe — avoids running the full workflow. Builds the v2
composite from sim_data (no sim ticks, no division), invokes
``_collect_output_metadata``, flattens, and diffs against v1's
``configuration`` parquet column set to see which ``output_metadata__*``
columns v2 is missing or has extra.

Usage:
    python runscripts/probe_output_metadata.py \
        [--config configs/two_generations_v2.json] \
        [--v1-config-pq <path>]
"""
import argparse
import os
import sys


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
    from ecoli.composites.ecoli_composite import build_composite_native
    from process_bigraph import Composite
    from bigraph_schema import allocate_core
    from ecoli.library.parquet_emitter import flatten_dict

    sim = EcoliSim.from_file(filepath=args.config)
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
    print('Building composite...')
    state = build_composite_native(core, sim.config)
    print('Creating Composite (realize)...')
    ecoli = Composite({'schema': {}, 'state': state}, core=core)
    sim._composite = ecoli

    print('Invoking _collect_output_metadata...')
    md = sim._collect_output_metadata()

    # Flatten and prefix like the parquet emitter does
    flat = flatten_dict(md)
    v2_cols = {f'output_metadata__{k}' for k in flat.keys()}
    print(f'\nv2 output_metadata columns produced: {len(v2_cols)}')

    v1_cfg = pl.read_parquet(args.v1_config_pq)
    v1_cols = {c for c in v1_cfg.columns if c.startswith('output_metadata__')}
    print(f'v1 output_metadata columns expected:  {len(v1_cols)}')

    missing = v1_cols - v2_cols
    extra = v2_cols - v1_cols
    print(f'\nMissing in v2 ({len(missing)}):')
    for c in sorted(missing)[:25]:
        print(f'  {c}')
    if len(missing) > 25:
        print(f'  ... {len(missing) - 25} more')
    print(f'\nExtra in v2 ({len(extra)}):')
    for c in sorted(extra)[:25]:
        print(f'  {c}')
    if len(extra) > 25:
        print(f'  ... {len(extra) - 25} more')


if __name__ == '__main__':
    main()
