"""Diff in-memory daughter handoff vs JSON-roundtripped daughter handoff.

Same source mother, same divide. Then:
  * daughter_inmem = mother.inner.state['agents']['00'] +
                     _v2_daughter_payload — what the Ray /
                     composite_lineage in-memory path hands to gen 1.
  * daughter_json  = save_v2_daughters() writes daughter JSONs to
                     disk + get_state_from_file() loads them back —
                     what the v2-nextflow per-gen path hands to gen 1.

Per the parity matrix in doc/v1_v2_report.md:
  * v2-nextflow gen 1 is bit-identical to v1 gen 1 across all 160 cells
  * ray gen 1 diverges from v1 gen 1 starting at t=2 in 143/143 cells

So the JSON-roundtripped daughter is the "correct" state. The in-memory
one diverges. This probe shows EXACTLY which fields differ — that's
the leak.

Usage:
    .venv/bin/python runscripts/probe_daughter_handoff.py \\
        --sim-data-path out/kb/simData.cPickle \\
        --max-duration 2700
"""
import os
os.environ.setdefault('OMP_NUM_THREADS', '1')
os.environ.setdefault('MKL_NUM_THREADS', '1')
os.environ.setdefault('OPENBLAS_NUM_THREADS', '1')
os.environ.setdefault('NUMBA_NUM_THREADS', '1')

import argparse
import json
import os.path
import tempfile
import time
from copy import deepcopy

import numpy as np


def _summarize(value, depth=0, max_depth=4):
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
        elif value.dtype.kind == 'V':  # structured
            for f in value.dtype.names or ():
                if value[f].dtype.kind in 'iuf':
                    out[f'sum.{f}'] = float(value[f].sum())
        return out
    if isinstance(value, dict):
        return {k: _summarize(v, depth + 1) for k, v in list(value.items())[:8]}
    if isinstance(value, (list, tuple)):
        return [_summarize(v, depth + 1) for v in value[:5]]
    if isinstance(value, set):
        return f'<set len={len(value)}>'
    return f'<{type(value).__name__}>'


def _walk(state, prefix=()):
    """Yield (path, value) for leaves in a nested dict."""
    if isinstance(state, dict):
        for k, v in state.items():
            if isinstance(v, dict):
                yield from _walk(v, prefix + (k,))
            else:
                yield prefix + (k,), v
    else:
        yield prefix, state


def _normalize(v):
    """Coerce list <-> ndarray distinction away. JSON round-trip turns
    arrays into nested lists; we want to compare VALUES not types."""
    if isinstance(v, list):
        try:
            return np.asarray(v)
        except Exception:
            return v
    return v


def _values_differ(a, b, rtol=1e-9, atol=1e-12):
    """Value-level diff. Normalizes list<->ndarray before compare.
    For floats uses np.allclose to absorb tiny JSON serialization noise.
    Returns (differs: bool, why: str)."""
    a = _normalize(a)
    b = _normalize(b)

    if isinstance(a, np.ndarray) and isinstance(b, np.ndarray):
        if a.shape != b.shape:
            return True, f'shape {a.shape} vs {b.shape}'
        if a.dtype.kind == 'V' or b.dtype.kind == 'V':
            # structured — compare field-by-field
            if a.dtype.names != b.dtype.names:
                return True, f'struct fields {a.dtype.names} vs {b.dtype.names}'
            for f in a.dtype.names or ():
                d, why = _values_differ(a[f], b[f], rtol, atol)
                if d:
                    return True, f'field {f!r}: {why}'
            return False, ''
        if a.dtype.kind in 'iu' and b.dtype.kind in 'iu':
            if not np.array_equal(a, b):
                diff = np.abs(a.astype(np.int64) - b.astype(np.int64))
                return True, f'int max|diff|={int(diff.max())} L1={int(diff.sum())}'
            return False, ''
        if a.dtype.kind in 'fiu' and b.dtype.kind in 'fiu':
            try:
                if np.allclose(a, b, rtol=rtol, atol=atol, equal_nan=True):
                    return False, ''
                diff = np.abs(a.astype(float) - b.astype(float))
                return True, f'float max|diff|={float(diff.max()):.4g} L1={float(diff.sum()):.4g}'
            except Exception as e:
                return True, f'compare-err: {e}'
        if a.dtype.kind == 'b' and b.dtype.kind == 'b':
            if not np.array_equal(a, b):
                return True, f'bool diff at {int((a != b).sum())} positions'
            return False, ''
        # object / string array
        try:
            return (not np.array_equal(a, b)), 'object/string content diff'
        except Exception as e:
            return True, f'compare-err: {e}'

    if isinstance(a, (set, frozenset)) and isinstance(b, (set, frozenset)):
        if a != b:
            return True, f'set diff: only_a={sorted(a - b)[:5]} only_b={sorted(b - a)[:5]}'
        return False, ''
    if a is None and b is None:
        return False, ''
    if isinstance(a, bool) and isinstance(b, bool):
        return (a != b), f'{a} vs {b}'
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        if np.isclose(a, b, rtol=rtol, atol=atol, equal_nan=True):
            return False, ''
        return True, f'scalar {a} vs {b}'
    if isinstance(a, str) and isinstance(b, str):
        return (a != b), f'string diff'
    # type mismatch we couldn't normalize away
    return True, f'type {type(a).__name__} vs {type(b).__name__}'


def diff(label_a, a_state, label_b, b_state, limit=80):
    a = {p: v for p, v in _walk(a_state)}
    b = {p: v for p, v in _walk(b_state)}
    only_a = sorted(set(a.keys()) - set(b.keys()))
    only_b = sorted(set(b.keys()) - set(a.keys()))
    common = sorted(set(a.keys()) & set(b.keys()))

    print(f'\n=== {label_a} vs {label_b} ===', flush=True)
    print(f'  paths only in {label_a}: {len(only_a)}', flush=True)
    for p in only_a[:30]:
        print(f'    - {".".join(str(x) for x in p)}', flush=True)
    print(f'  paths only in {label_b}: {len(only_b)}', flush=True)
    for p in only_b[:30]:
        print(f'    + {".".join(str(x) for x in p)}', flush=True)

    diffs = []
    for p in common:
        try:
            differs, why = _values_differ(a[p], b[p])
            if differs:
                diffs.append((p, why, a[p], b[p]))
        except Exception as e:
            diffs.append((p, f'compare-err:{e}', a[p], None))
    print(f'  value diffs (real, after type normalization): {len(diffs)}',
          flush=True)
    # Group by top-level prefix for skimmability
    by_prefix = {}
    for p, why, va, vb in diffs:
        prefix = p[0] if p else '<root>'
        by_prefix.setdefault(prefix, []).append((p, why, va, vb))
    for prefix in sorted(by_prefix.keys()):
        items = by_prefix[prefix]
        print(f'  --- {prefix} ({len(items)} diffs) ---', flush=True)
        for p, why, va, vb in items[:limit]:
            print(f'    ~ {".".join(str(x) for x in p)}: {why}', flush=True)
            # Show values only for small numeric diffs (helps spot real bugs)
            if 'shape' in why or 'compare-err' in why or 'type' in why:
                print(f'      {label_a}: {_summarize(va)}', flush=True)
                print(f'      {label_b}: {_summarize(vb)}', flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sim-data-path', default='out/kb/simData.cPickle')
    ap.add_argument('--max-duration', type=float, default=2700.0)
    ap.add_argument('--limit', type=int, default=80)
    ap.add_argument('--save', default='/tmp/daughter_states.pkl',
                    help='Pickle both daughter states here after the run '
                         'so we can re-diff without re-simming.')
    ap.add_argument('--reload', action='store_true',
                    help='Skip simulation; load daughter states from --save '
                         'and just run the diff.')
    args = ap.parse_args()

    if args.reload:
        import pickle as _pkl
        print(f'[probe] loading saved daughter states from {args.save}...',
              flush=True)
        with open(args.save, 'rb') as f:
            saved = _pkl.load(f)
        daughter_inmem = saved['inmem']
        daughter_json = saved['json']
        print(f'[probe]   loaded', flush=True)
        diff('inmem (Ray/MP style)', daughter_inmem,
             'json (nextflow style)', daughter_json,
             limit=args.limit)
        return

    sim_data_path = os.path.abspath(args.sim_data_path)

    from configs import CONFIG_DIR_PATH
    from ecoli.experiments.ecoli_master_sim import EcoliSim
    from ecoli.library.sim_data import LoadSimData
    from ecoli.library.bigraph_types import ECOLI_TYPES
    from ecoli.composites.ecoli_composite import (
        build_ecoli_document, run_to_division,
        _v2_daughter_payload, save_v2_daughters)
    from ecoli.library.json_state import get_state_from_file
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

    core = allocate_core()
    core.register_types(ECOLI_TYPES)
    lsd = LoadSimData(**{**sim_config, 'seed': 0})

    print('[probe] building mother...', flush=True)
    t0 = time.perf_counter()
    state = build_ecoli_document(core, sim_config, load_sim_data=lsd)
    composite = Composite(
        {'schema': {}, 'state': state, 'run_steps_on_init': True},
        core=core)
    print(f'[probe]   built in {time.perf_counter()-t0:.1f}s', flush=True)

    print(f'[probe] running to divide (max={args.max_duration}s)...',
          flush=True)
    t0 = time.perf_counter()
    divided, ct = run_to_division(composite, max_duration=args.max_duration)
    print(f'[probe]   divided={divided} t={ct:.1f} '
          f'wall={time.perf_counter()-t0:.1f}s', flush=True)
    if not divided:
        return

    keys = sorted(composite.state['agents'].keys())
    print(f'[probe]   agents post-divide: {keys}', flush=True)

    # PATH A — in-memory (Ray / composite_lineage style):
    daughter_inmem = deepcopy(composite.state['agents'][keys[0]])
    _v2_daughter_payload(daughter_inmem)
    print(f'[probe] in-memory daughter[{keys[0]}] payload-stripped',
          flush=True)

    # PATH B — JSON roundtrip (v2-nextflow style):
    # save_v2_daughters writes daughter_state_{i}.json for each daughter
    # in agents map. We point it at a tempdir, then read daughter 0 back
    # using the same get_state_from_file the nextflow per-gen path uses.
    with tempfile.TemporaryDirectory(prefix='daughter_probe_') as tmpdir:
        # save_v2_daughters writes daughter_state_{i}_uri.txt and
        # division_time.sh in CWD — chdir to tmpdir so they land there
        # too and don't pollute the repo root.
        prev_cwd = os.getcwd()
        os.chdir(tmpdir)
        try:
            save_v2_daughters(composite.state, tmpdir)
        finally:
            os.chdir(prev_cwd)
        daughter_json_path = os.path.join(tmpdir, 'daughter_state_0.json')
        print(f'[probe] reading daughter back from {daughter_json_path}...',
              flush=True)
        full_state = get_state_from_file(path=daughter_json_path)

    # full_state shape is {agents: {agent_id: cell_state}, ...} matching
    # what save_v2_daughters writes. Extract the cell-level subtree.
    daughter_json = next(iter(full_state.get('agents', {}).values()))

    # Save both for re-analysis (avoid re-running the 8-min sim).
    import pickle as _pkl
    with open(args.save, 'wb') as f:
        _pkl.dump({'inmem': daughter_inmem, 'json': daughter_json}, f)
    print(f'[probe] saved daughter states to {args.save}', flush=True)

    # ----- Reports -----
    print(f'\n[probe] daughter_inmem  keys: {sorted(daughter_inmem.keys())[:15]}',
          flush=True)
    print(f'[probe] daughter_json   keys: {sorted(daughter_json.keys())[:15]}',
          flush=True)
    print(f'[probe] bulk inmem count.sum: '
          f'{int(daughter_inmem["bulk"]["count"].sum()) if hasattr(daughter_inmem.get("bulk"), "dtype") else "n/a"}',
          flush=True)
    print(f'[probe] bulk json  count.sum: '
          f'{int(daughter_json["bulk"]["count"].sum()) if hasattr(daughter_json.get("bulk"), "dtype") else "n/a"}',
          flush=True)

    diff('inmem (Ray/MP style)', daughter_inmem,
         'json (nextflow style)', daughter_json,
         limit=args.limit)


if __name__ == '__main__':
    main()
