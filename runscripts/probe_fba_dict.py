"""Deep compare ecoli-metabolism's FluxBalanceAnalysisModel __dict__
before and after bundle round-trip.

The top-level fidelity probe reported that this object's state differs
post-load. This probe drills into the specific attributes so we can
identify which FBA-critical fields ``capture_object_state`` drops.

Usage:
    python runscripts/probe_fba_dict.py [--config configs/two_generations_v2.json]
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


def get_metabolism_instance(comp):
    """Find the Metabolism process instance by walking step/process paths."""
    paths = {**getattr(comp, 'step_paths', {}),
             **getattr(comp, 'process_paths', {})}
    for path, entry in paths.items():
        inst = entry.get('instance') if isinstance(entry, dict) else (
            entry[0] if isinstance(entry, tuple) and entry else entry)
        if inst is not None and type(inst).__name__ == 'Metabolism':
            return inst, path
    # Fallback: check shared_processes too
    from process_bigraph.types.process import _shared_processes
    for pid, inst in _shared_processes.items():
        if type(inst).__name__ == 'Metabolism':
            return inst, pid
    return None, None


def get_fba_model(inst):
    return getattr(inst, 'model', None)


def describe(v):
    if isinstance(v, np.ndarray):
        return f'ndarray shape={list(v.shape)} dtype={v.dtype}'
    if v is None:
        return 'None'
    if isinstance(v, (int, float, bool, str)):
        return f'{type(v).__name__}={v!r}'[:80]
    if isinstance(v, dict):
        return f'dict[{len(v)}]'
    if isinstance(v, (list, tuple)):
        return f'{type(v).__name__}[{len(v)}]'
    return type(v).__name__


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--config', default='configs/two_generations_v2.json')
    args = p.parse_args()

    print('Building fresh composite...', flush=True)
    pre = build_composite(args.config)

    from process_bigraph.types.process import _shared_processes
    pre_inst, _ = get_metabolism_instance(pre)
    if pre_inst is None:
        print('ERROR: no Metabolism instance found in _shared_processes')
        sys.exit(1)
    pre_model = get_fba_model(pre_inst)
    if pre_model is None:
        print('ERROR: Metabolism.model is None')
        sys.exit(1)
    pre_attrs = dict(pre_model.__dict__)
    print(f'  Metabolism.model attrs pre-save: {len(pre_attrs)}')

    out_dir = tempfile.mkdtemp(prefix='probe_fba_')
    print(f'Saving bundle -> {out_dir}', flush=True)
    pre.save_bundle(out_dir)

    # IMPORTANT: clear the shared registry before reload so we can tell
    # the pre vs post instances apart.
    _shared_processes.clear()

    print('Loading bundle back...', flush=True)
    post = load_composite(out_dir)
    post_inst, _ = get_metabolism_instance(post)
    post_model = get_fba_model(post_inst)
    if post_model is None:
        print('ERROR: post-load Metabolism.model is None')
        sys.exit(1)
    post_attrs = dict(post_model.__dict__)
    print(f'  Metabolism.model attrs post-load: {len(post_attrs)}')

    only_pre = set(pre_attrs) - set(post_attrs)
    only_post = set(post_attrs) - set(pre_attrs)
    common = set(pre_attrs) & set(post_attrs)

    print(f'\n== MISSING AFTER LOAD ({len(only_pre)}) ==')
    for k in sorted(only_pre):
        print(f'  {k}: {describe(pre_attrs[k])}')

    print(f'\n== NEW AFTER LOAD ({len(only_post)}) ==')
    for k in sorted(only_post):
        print(f'  {k}: {describe(post_attrs[k])}')

    sys.path.insert(0, os.path.dirname(__file__))
    from diff_bundle_roundtrip import _values_equal

    changed = []
    for k in sorted(common):
        pv = pre_attrs[k]
        pv2 = post_attrs[k]
        if not _values_equal(pv, pv2):
            changed.append((k, describe(pv), describe(pv2)))

    print(f'\n== CHANGED (same keys but different values) ({len(changed)}) ==')
    for k, a, b in changed:
        print(f'  {k}: {a}  !=  {b}')


if __name__ == '__main__':
    main()
