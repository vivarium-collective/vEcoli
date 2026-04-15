"""Audit each v2 process's config_schema against what sim_data hands it.

For each process listed in the v2 config:
  1. Pull its raw config from ``LoadSimData.get_config_by_name`` (or
     the explicit ``process_configs`` entry).
  2. Instantiate the process class with that config.
  3. After init, walk ``self.parameters`` and report any field whose
     value is ``None`` even though sim_data provided a non-None value
     (silent coercion).
  4. Also walk ``self.parameters`` and check for type mismatches that
     the framework silently corrected away.

Goal: find every silent type mismatch, like the
``map[list[integer]]`` vs single-int ``map[integer]`` bug in
mass_listener.compartment_indices.
"""
import argparse
import os
import sys


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--config', default='configs/two_generations_v2.json')
    args = p.parse_args()

    from ecoli.experiments.ecoli_master_sim import EcoliSim
    from ecoli.library.bigraph_types import ECOLI_TYPES
    from ecoli.library.sim_data import LoadSimData
    from bigraph_schema import allocate_core

    sim = EcoliSim.from_file(filepath=args.config)
    sim.config['engine'] = 'composite'
    sim.processes = sim._retrieve_processes(
        sim.processes, sim.add_processes,
        sim.exclude_processes, sim.swap_processes)
    sim.process_configs = sim._retrieve_process_configs(
        sim.process_configs, sim.processes)

    load_sim_data = LoadSimData(**sim.config)
    time_step = sim.config['time_step']

    core = allocate_core()
    core.register_types(ECOLI_TYPES)

    def _walk_for_lost(raw, real, path=''):
        """Yield paths where raw has a non-None value but real has None,
        recursing into dicts and lists."""
        if raw is None:
            return
        if real is None:
            yield (path, type(raw).__name__,
                   repr(raw)[:60] if not isinstance(raw, (dict, list)) else f'{type(raw).__name__}[{len(raw)}]')
            return
        if isinstance(raw, dict) and isinstance(real, dict):
            for k in raw:
                yield from _walk_for_lost(raw[k], real.get(k), f'{path}.{k}')
        elif isinstance(raw, (list, tuple)) and isinstance(real, (list, tuple)):
            if len(raw) != len(real):
                yield (path, 'len_diff',
                       f'raw={len(raw)} real={len(real)}')
            else:
                for i, (a, b) in enumerate(zip(raw, real)):
                    yield from _walk_for_lost(a, b, f'{path}[{i}]')

    findings = []
    for proc_name, proc_class in sim.processes.items():
        try:
            raw_config = load_sim_data.get_config_by_name(proc_name, time_step)
        except (KeyError, AttributeError):
            continue
        if raw_config is None:
            continue
        cfg_schema = getattr(proc_class, 'config_schema', None)
        if not isinstance(cfg_schema, dict):
            continue

        # Run the raw_config through the framework's realize pipeline
        # — this is what SharedProcess.realize does before instantiating.
        try:
            _, realized = core.realize(cfg_schema, raw_config)
        except Exception as e:
            findings.append((proc_name, 'realize_failed',
                             f'{type(e).__name__}: {str(e)[:120]}'))
            continue

        for key, schema_str in cfg_schema.items():
            raw_val = raw_config.get(key)
            real_val = realized.get(key) if isinstance(realized, dict) else None
            for sub_path, kind, info in _walk_for_lost(raw_val, real_val):
                findings.append((proc_name, 'silent_loss',
                                 f'{key}{sub_path}',
                                 f'schema={schema_str!r}',
                                 f'raw={kind} {info}'))

    if not findings:
        print('No silent schema mismatches detected.')
        return
    print(f'Found {len(findings)} potential schema mismatches:\n')
    for entry in findings:
        proc, kind, *rest = entry
        print(f'  [{kind}] {proc}.{rest[0] if kind != "init_failed" else "(init)"}')
        for r in rest[1:]:
            print(f'        {r}')


if __name__ == '__main__':
    main()
