"""Deep diff on FluxBalanceAnalysis solver __dict__ before/after bundle
round-trip. Task #5 fix target: identify which LP/basis state gets lost.
"""
import argparse
import os
import sys
import tempfile

import numpy as np


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


def get_metabolism_model(comp):
    paths = {**getattr(comp, 'step_paths', {}),
             **getattr(comp, 'process_paths', {})}
    for path, entry in paths.items():
        inst = entry.get('instance') if isinstance(entry, dict) else (
            entry[0] if isinstance(entry, tuple) and entry else entry)
        if inst is not None and type(inst).__name__ == 'Metabolism':
            return getattr(inst, 'model', None)
    return None


def describe(v):
    if isinstance(v, np.ndarray):
        return f'ndarray shape={list(v.shape)} dtype={v.dtype}'
    if v is None:
        return 'None'
    if isinstance(v, (int, float, bool)):
        return f'{type(v).__name__}={v!r}'
    if isinstance(v, str):
        return f'str={v!r}'[:80]
    if isinstance(v, dict):
        return f'dict[{len(v)}]'
    if isinstance(v, (list, tuple)):
        return f'{type(v).__name__}[{len(v)}]'
    return type(v).__name__


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--config', default='configs/two_generations_v2.json')
    args = p.parse_args()

    sys.path.insert(0, os.path.dirname(__file__))
    from diff_bundle_roundtrip import _values_equal

    print('Building fresh composite...', flush=True)
    pre = build_composite(args.config)
    pre_model = get_metabolism_model(pre)
    pre_fba = pre_model.fba
    pre_attrs = dict(pre_fba.__dict__)
    print(f'  FBA attrs pre-save: {len(pre_attrs)}')

    out_dir = tempfile.mkdtemp(prefix='probe_fba_solver_')
    print(f'Saving -> {out_dir}', flush=True)
    pre.save_bundle(out_dir)

    from process_bigraph.types.process import _shared_processes
    _shared_processes.clear()

    print('Loading...', flush=True)
    post = load_composite(out_dir)
    post_model = get_metabolism_model(post)
    post_fba = post_model.fba
    post_attrs = dict(post_fba.__dict__)
    print(f'  FBA attrs post-load: {len(post_attrs)}')

    only_pre = set(pre_attrs) - set(post_attrs)
    only_post = set(post_attrs) - set(pre_attrs)
    common = set(pre_attrs) & set(post_attrs)

    print(f'\n== MISSING AFTER LOAD ({len(only_pre)}) ==')
    for k in sorted(only_pre):
        print(f'  {k}: {describe(pre_attrs[k])}')

    print(f'\n== NEW AFTER LOAD ({len(only_post)}) ==')
    for k in sorted(only_post):
        print(f'  {k}: {describe(post_attrs[k])}')

    changed = []
    for k in sorted(common):
        if not _values_equal(pre_attrs[k], post_attrs[k]):
            changed.append(k)

    print(f'\n== CHANGED ({len(changed)}) ==')
    for k in changed:
        print(f'  {k}: {describe(pre_attrs[k])}  !=  {describe(post_attrs[k])}')


if __name__ == '__main__':
    main()
