"""Diff fresh sim_data vs sim_data that has been through one generation.

Hypothesis (from doc/v1_v2_report.md): mp and ray engines diverge from
v1 because they share the same ``sim_data`` Python object across all
generations within one process / actor, while v2-nextflow loads
sim_data fresh per gen. Mutations accumulate on the shared object →
gen 2 sees mutated gen 1 state → drift compounds → late gens fail.

This script loads sim_data twice (one "clean" reference, one "used"),
runs a single generation against the "used" copy (same code path
``run_composite_lineage_ray.py`` follows: build EcoliCellProcess,
run to division), then walks both objects field-by-field and prints
every difference.

That gives us the exact leak surface — the list of fields that need
to be reset per gen (or the set the deep-copy must cover).

Usage:
    .venv/bin/python runscripts/probe_sim_data_drift.py \\
        --sim-data-path out/kb/simData.cPickle \\
        --max-duration 2700
"""
import os
os.environ.setdefault('OMP_NUM_THREADS', '1')
os.environ.setdefault('MKL_NUM_THREADS', '1')
os.environ.setdefault('OPENBLAS_NUM_THREADS', '1')
os.environ.setdefault('NUMBA_NUM_THREADS', '1')

import argparse
import copy
import pickle
import time

import numpy as np


def _summarize(value, depth=0, max_depth=4):
    """Compact summary for diff reporting."""
    if depth > max_depth:
        return f'<deep:{type(value).__name__}>'
    if value is None or isinstance(value, (bool, int, float, str)):
        if isinstance(value, str) and len(value) > 80:
            return value[:77] + '...'
        return value
    if isinstance(value, np.ndarray):
        out = {'_np': True, 'shape': list(value.shape), 'dtype': str(value.dtype)}
        if value.dtype.kind in 'iuf':
            out['sum'] = float(value.sum()) if value.size else 0.0
        return out
    if isinstance(value, dict):
        return f'<dict len={len(value)} keys={sorted(list(value.keys()))[:5]}>'
    if isinstance(value, (list, tuple)):
        return f'<{type(value).__name__} len={len(value)}>'
    if isinstance(value, set):
        return f'<set len={len(value)}>'
    return f'<{type(value).__name__}>'


def _walk_attrs(obj, prefix=(), seen=None, max_depth=6):
    """Walk a Python object's attribute tree, yielding (path, value)
    for leaf fields. Skips private (_x), callables, modules, classes.

    ``seen`` prevents infinite cycles. ``max_depth`` caps recursion.
    """
    if seen is None:
        seen = set()
    if len(prefix) > max_depth:
        return
    oid = id(obj)
    if oid in seen:
        return
    seen.add(oid)

    # Leaf types — yield directly
    if obj is None or isinstance(obj, (bool, int, float, str, np.ndarray)):
        yield prefix, obj
        return
    if isinstance(obj, (list, tuple, set, frozenset)):
        yield prefix, obj
        return
    if callable(obj) and not hasattr(obj, '__dict__'):
        return

    # Dict — recurse into keys
    if isinstance(obj, dict):
        for k, v in obj.items():
            if not isinstance(k, (str, int, float, bool)):
                continue
            yield from _walk_attrs(v, prefix + (str(k),), seen, max_depth)
        return

    # Object with attributes — recurse
    try:
        attrs = vars(obj)
    except TypeError:
        yield prefix, obj
        return
    for name, value in attrs.items():
        if name.startswith('_'):
            continue
        if callable(value) and not hasattr(value, '__dict__'):
            continue
        yield from _walk_attrs(value, prefix + (name,), seen, max_depth)


def _values_differ(a, b):
    """Equality check that handles numpy + nested structures."""
    if type(a) != type(b):
        return True
    if isinstance(a, np.ndarray):
        if a.shape != b.shape:
            return True
        if a.dtype != b.dtype:
            return True
        if a.dtype.kind in 'iuf':
            return not np.allclose(a, b, equal_nan=True)
        if a.dtype.kind in 'OUS':
            try:
                return not np.array_equal(a, b)
            except Exception:
                return True
        return False
    if isinstance(a, (list, tuple)):
        if len(a) != len(b):
            return True
        return any(_values_differ(x, y) for x, y in zip(a, b))
    if isinstance(a, (set, frozenset)):
        return a != b
    if isinstance(a, dict):
        if set(a.keys()) != set(b.keys()):
            return True
        return any(_values_differ(a[k], b[k]) for k in a)
    if a is None or isinstance(a, (bool, int, float, str)):
        return a != b
    # Objects — compare by identity (already deep-copied, so should be different)
    return False  # don't flag opaque objects


def diff(clean, used):
    """Walk both objects, report every leaf where values differ."""
    clean_map = {p: v for p, v in _walk_attrs(clean)}
    used_map = {p: v for p, v in _walk_attrs(used)}
    common = set(clean_map.keys()) & set(used_map.keys())
    only_clean = sorted(set(clean_map.keys()) - common)
    only_used = sorted(set(used_map.keys()) - common)

    diffs = []
    for p in sorted(common):
        c, u = clean_map[p], used_map[p]
        try:
            if _values_differ(c, u):
                diffs.append((p, c, u))
        except Exception as e:
            diffs.append((p, f'<diff-err:{e}>', None))
    return only_clean, only_used, diffs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sim-data-path', default='out/kb/simData.cPickle')
    ap.add_argument('--max-duration', type=float, default=2700.0)
    ap.add_argument('--show-limit', type=int, default=100,
                    help='Max diffs to print.')
    args = ap.parse_args()

    sim_data_path = os.path.abspath(args.sim_data_path)

    # Load sim_data twice — independent objects
    print(f'[probe] loading clean sim_data #1...', flush=True)
    t0 = time.perf_counter()
    with open(sim_data_path, 'rb') as f:
        sd_clean = pickle.load(f)
    print(f'[probe]   loaded in {time.perf_counter()-t0:.1f}s', flush=True)

    print(f'[probe] loading sim_data #2 (will be mutated by one gen)...',
          flush=True)
    t0 = time.perf_counter()
    with open(sim_data_path, 'rb') as f:
        sd_used = pickle.load(f)
    print(f'[probe]   loaded in {time.perf_counter()-t0:.1f}s', flush=True)

    # Verify both objects start identical (sanity check)
    only_a0, only_b0, diffs0 = diff(sd_clean, sd_used)
    print(f'[probe] sanity check pre-run: only_clean={len(only_a0)} '
          f'only_used={len(only_b0)} value_diffs={len(diffs0)}', flush=True)

    # Build and run one generation against sd_used — mirrors what
    # run_composite_lineage_ray.py does for gen 0 of every actor.
    from configs import CONFIG_DIR_PATH
    from ecoli.experiments.ecoli_master_sim import EcoliSim
    from ecoli.library.sim_data import LoadSimData
    from ecoli.library.bigraph_types import ECOLI_TYPES
    from ecoli.composites.ecoli_composite import (
        build_ecoli_document, run_to_division)
    from process_bigraph import Composite, allocate_core

    sim = EcoliSim.from_file(os.path.join(CONFIG_DIR_PATH, 'default.json'))
    sim.processes = sim._retrieve_processes(
        sim.processes, sim.add_processes,
        sim.exclude_processes, sim.swap_processes)
    sim.topology = sim._retrieve_topology(
        sim.topology, sim.processes, sim.swap_processes, sim.log_updates)
    sim.process_configs = sim._retrieve_process_configs(
        sim.process_configs, sim.processes)
    sim_config = dict(sim.config)
    sim_config['sim_data_path'] = sim_data_path
    sim_config['agent_id'] = '0'
    sim_config['seed'] = 0
    sim_config['divide'] = True

    # LoadSimData wraps the already-loaded sd_used and may mutate it
    # (the .condition assignment + internal_shift_dict apply happen
    # here; see ecoli/library/sim_data.py:182).
    lsd = LoadSimData(**{**sim_config, 'seed': 0, 'sim_data': sd_used})

    only_a1, only_b1, diffs1 = diff(sd_clean, sd_used)
    print(f'\n[probe] after LoadSimData(sim_data=sd_used): '
          f'only_clean={len(only_a1)} only_used={len(only_b1)} '
          f'value_diffs={len(diffs1)}', flush=True)

    core = allocate_core()
    core.register_types(ECOLI_TYPES)

    print(f'[probe] building gen-0 cell (mutates sd_used via process __init__'
          f' calls that may touch sim_data)...', flush=True)
    t0 = time.perf_counter()
    state = build_ecoli_document(core, sim_config, load_sim_data=lsd)
    composite = Composite(
        {'schema': {}, 'state': state, 'run_steps_on_init': True},
        core=core)
    print(f'[probe]   built in {time.perf_counter()-t0:.1f}s', flush=True)

    only_a2, only_b2, diffs2 = diff(sd_clean, sd_used)
    print(f'\n[probe] after build + Composite init: '
          f'only_clean={len(only_a2)} only_used={len(only_b2)} '
          f'value_diffs={len(diffs2)}', flush=True)

    print(f'[probe] running mother to division (max={args.max_duration}s)...',
          flush=True)
    t0 = time.perf_counter()
    divided, ct = run_to_division(composite, max_duration=args.max_duration)
    print(f'[probe]   divided={divided} t={ct:.1f} '
          f'wall={time.perf_counter()-t0:.1f}s', flush=True)

    only_a3, only_b3, diffs3 = diff(sd_clean, sd_used)
    print(f'\n=== sd_clean vs sd_used AFTER one full generation ===',
          flush=True)
    print(f'  keys only in clean: {len(only_a3)}', flush=True)
    print(f'  keys only in used:  {len(only_b3)}', flush=True)
    print(f'  value diffs:        {len(diffs3)}', flush=True)

    if only_a3:
        print(f'\n--- fields present in clean, MISSING from used ---', flush=True)
        for p in only_a3[:args.show_limit]:
            print(f'  - {".".join(p)}', flush=True)

    if only_b3:
        print(f'\n--- fields present in used, MISSING from clean (new) ---',
              flush=True)
        for p in only_b3[:args.show_limit]:
            print(f'  + {".".join(p)} ='
                  f' {_summarize(used_value := None)}', flush=True)

    if diffs3:
        print(f'\n--- value differences (clean -> used) ---', flush=True)
        for p, c, u in diffs3[:args.show_limit]:
            path = '.'.join(p)
            print(f'  ~ {path}', flush=True)
            print(f'    clean: {_summarize(c)}', flush=True)
            print(f'    used:  {_summarize(u)}', flush=True)


if __name__ == '__main__':
    main()
