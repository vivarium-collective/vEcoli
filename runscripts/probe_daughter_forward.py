"""Find the first tick where in-memory daughter and JSON-roundtripped
daughter diverge from each other when run forward.

All in ONE process — no intermediate pickle (which would introduce
pint UnitRegistry artifacts that don't happen in production ray /
nextflow). Steps:

  1. Build mother (fresh sim_data, agent_id='0', seed=0).
  2. Run mother to division (~7 min wall).
  3. Extract daughter as:
       * daughter_inmem = mother.inner.state['agents']['00'] +
                           _v2_daughter_payload — the in-memory path
                           used by ray / composite_lineage.
       * daughter_json  = save_v2_daughters() → temp dir → reload via
                          get_state_from_file — the JSON path used
                          by v2-nextflow per-gen.
  4. Build two fresh gen-1 cells, one from each daughter.
  5. Tick both 1 sim-sec at a time, diff every comparable field.
  6. Report the first tick where ANY field differs.

If the gen-1 paths are equivalent, they should stay bit-identical
forever. If they diverge at tick N, that's the leak point.

Usage:
    .venv/bin/python runscripts/probe_daughter_forward.py \\
        --sim-data-path out/kb/simData.cPickle \\
        --max-mother-duration 2700 --ticks 10
"""
import os
os.environ.setdefault('OMP_NUM_THREADS', '1')
os.environ.setdefault('MKL_NUM_THREADS', '1')
os.environ.setdefault('OPENBLAS_NUM_THREADS', '1')
os.environ.setdefault('NUMBA_NUM_THREADS', '1')
os.environ.setdefault('NUMEXPR_NUM_THREADS', '1')
os.environ.setdefault('VECLIB_MAXIMUM_THREADS', '1')
try:
    from threadpoolctl import threadpool_limits as _tpl
    _tpl(limits=1)
except ImportError:
    pass

import argparse
import os.path
import tempfile
import time
from copy import deepcopy

import numpy as np


def deep_equal(a, b, path=''):
    if a is None and b is None:
        return True, ''
    if isinstance(a, list) and not isinstance(b, list):
        try: a = np.asarray(a)
        except: pass
    if isinstance(b, list) and not isinstance(a, list):
        try: b = np.asarray(b)
        except: pass
    if isinstance(a, np.ndarray) and isinstance(b, np.ndarray):
        if a.shape != b.shape:
            return False, f'shape {a.shape} vs {b.shape}'
        if a.dtype.kind == 'V':
            if a.dtype.names != b.dtype.names:
                return False, f'struct fields differ'
            for f in a.dtype.names:
                ok, why = deep_equal(a[f], b[f], f'{path}/{f}')
                if not ok: return False, f'field {f}: {why}'
            return True, ''
        if a.dtype != b.dtype:
            try:
                if np.array_equal(a.astype(float), b.astype(float), equal_nan=True):
                    return True, ''
            except: pass
            return False, f'dtype {a.dtype} vs {b.dtype}'
        if a.dtype.kind in 'iuf':
            if not np.array_equal(a, b, equal_nan=True):
                d = np.abs(a.astype(float) - b.astype(float))
                return False, f'max|diff|={float(d.max()):.4g} L1={float(d.sum()):.4g}'
            return True, ''
        if not np.array_equal(a, b):
            return False, 'non-numeric array differ'
        return True, ''
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b): return False, f'len {len(a)} vs {len(b)}'
        for i, (x, y) in enumerate(zip(a, b)):
            ok, why = deep_equal(x, y, f'{path}[{i}]')
            if not ok: return False, f'[{i}]: {why}'
        return True, ''
    if isinstance(a, dict) and isinstance(b, dict):
        only_a = set(a) - set(b)
        only_b = set(b) - set(a)
        if only_a or only_b:
            return False, f'keys only_a={sorted(only_a)[:5]} only_b={sorted(only_b)[:5]}'
        for k in a:
            ok, why = deep_equal(a[k], b[k], f'{path}/{k}')
            if not ok: return False, f'{k}: {why}'
        return True, ''
    # NaN-aware scalar: nan == nan must be True (Python's default
    # gives False; np.isnan handles both float and numpy scalars).
    try:
        if isinstance(a, float) and isinstance(b, float):
            if np.isnan(a) and np.isnan(b):
                return True, ''
    except Exception:
        pass
    try:
        equal = (a == b)
        if hasattr(equal, '__iter__'):
            equal = bool(np.all(equal))
        return bool(equal), '' if equal else f'{a!r} vs {b!r}'
    except Exception as e:
        return type(a) == type(b), f'compare-err: {e}'


# Data-layer keys to diff per tick. Skip declared edges (process /
# step decls), sim_data_objects, step_flow.
COMPARE_KEYS = ('bulk', 'unique', 'listeners', 'environment', 'boundary',
                'allocate', 'request', 'next_update_time', 'process_state',
                'global_time', 'divide', 'division_threshold')


def snapshot(cell_state):
    return {k: cell_state[k] for k in COMPARE_KEYS if k in cell_state}


def diff_cells(state_a, state_b):
    a = snapshot(state_a)
    b = snapshot(state_b)
    diffs = []
    for key in sorted(set(a.keys()) | set(b.keys())):
        if key not in a: diffs.append((key, 'only-in-B')); continue
        if key not in b: diffs.append((key, 'only-in-A')); continue
        ok, why = deep_equal(a[key], b[key])
        if not ok:
            diffs.append((key, why))
    return diffs


def _rebind_quantities(obj, ureg):
    """Walk obj; replace any pint Quantity with a freshly-constructed one
    in ``ureg`` (the current process's registry). Idempotent for objects
    already in ureg. Handles dict/list/tuple recursion."""
    # pint Quantity check: duck-type via magnitude + units attrs and a
    # _REGISTRY attribute. (`isinstance(obj, ureg.Quantity)` would only
    # catch obj already in ureg; we want to catch foreign-registry too.)
    if hasattr(obj, '_REGISTRY') and hasattr(obj, 'magnitude') and hasattr(obj, 'units'):
        if obj._REGISTRY is ureg:
            return obj
        try:
            return ureg.Quantity(obj.magnitude, str(obj.units))
        except Exception:
            return obj
    if isinstance(obj, dict):
        return {k: _rebind_quantities(v, ureg) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_rebind_quantities(x, ureg) for x in obj]
    if isinstance(obj, tuple):
        return tuple(_rebind_quantities(x, ureg) for x in obj)
    return obj


def build_cell(core, sim_config_template, sim_data_path, daughter_state,
               agent_id, gen_seed):
    from ecoli.library.sim_data import LoadSimData
    from ecoli.composites.ecoli_composite import build_ecoli_document
    from process_bigraph import Composite

    sim_config = deepcopy(sim_config_template)
    sim_config['sim_data_path'] = sim_data_path
    sim_config['seed'] = gen_seed
    sim_config['agent_id'] = agent_id
    sim_config['divide'] = True
    sim_config['initial_state'] = daughter_state
    sim_config['initial_state_file'] = None

    lsd = LoadSimData(**{**sim_config, 'seed': gen_seed})
    state = build_ecoli_document(core, sim_config, load_sim_data=lsd)
    return Composite(
        {'schema': {}, 'state': state, 'run_steps_on_init': True},
        core=core)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sim-data-path', default='out/kb/simData.cPickle')
    ap.add_argument('--max-mother-duration', type=float, default=2700.0)
    ap.add_argument('--ticks', type=int, default=5)
    ap.add_argument('--gen-seed', type=int, default=1)
    ap.add_argument('--from-pickle', default=None,
                    help='Skip mother sim; load daughters from a pickle '
                         'previously saved by probe_daughter_handoff.py. '
                         'Rebinds pint Quantity to current registry first '
                         'so the cross-process unpickle is invisible.')
    args = ap.parse_args()

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
    from vivarium.library.units import units as vivunits

    sim = EcoliSim.from_file(os.path.join(CONFIG_DIR_PATH, 'default.json'))
    sim.processes = sim._retrieve_processes(
        sim.processes, sim.add_processes,
        sim.exclude_processes, sim.swap_processes)
    sim.topology = sim._retrieve_topology(
        sim.topology, sim.processes, sim.swap_processes, sim.log_updates)
    sim.process_configs = sim._retrieve_process_configs(
        sim.process_configs, sim.processes)
    sim_config_template = dict(sim.config)

    if args.from_pickle:
        import pickle as _pkl
        print(f'[probe] loading saved daughters from {args.from_pickle}...',
              flush=True)
        with open(args.from_pickle, 'rb') as f:
            saved = _pkl.load(f)
        daughter_inmem = _rebind_quantities(saved['inmem'], vivunits)
        daughter_json = _rebind_quantities(saved['json'], vivunits)
        print(f'[probe]   loaded + rebound Quantity to vivunits', flush=True)
    else:
        sim_config = dict(sim_config_template)
        sim_config['sim_data_path'] = sim_data_path
        sim_config['agent_id'] = '0'
        sim_config['seed'] = 0
        sim_config['divide'] = True

        core_mother = allocate_core()
        core_mother.register_types(ECOLI_TYPES)
        lsd = LoadSimData(**{**sim_config, 'seed': 0})

        print('[probe] building mother...', flush=True)
        t0 = time.perf_counter()
        state = build_ecoli_document(core_mother, sim_config, load_sim_data=lsd)
        mother = Composite(
            {'schema': {}, 'state': state, 'run_steps_on_init': True},
            core=core_mother)
        print(f'[probe]   built in {time.perf_counter()-t0:.1f}s', flush=True)

        print(f'[probe] running mother to divide (max={args.max_mother_duration}s)...',
              flush=True)
        t0 = time.perf_counter()
        divided, ct = run_to_division(mother,
                                      max_duration=args.max_mother_duration)
        print(f'[probe]   divided={divided} t={ct:.1f} '
              f'wall={time.perf_counter()-t0:.1f}s', flush=True)
        if not divided:
            return

        keys = sorted(mother.state['agents'].keys())
        print(f'[probe]   agents after divide: {keys}', flush=True)
        if len(keys) < 2:
            return

        # PATH A — in-memory daughter (Ray / composite_lineage style)
        daughter_inmem = deepcopy(mother.state['agents'][keys[0]])
        _v2_daughter_payload(daughter_inmem)

        # PATH B — JSON roundtrip (v2-nextflow style)
        with tempfile.TemporaryDirectory(prefix='dprobe_') as tmpdir:
            prev_cwd = os.getcwd()
            os.chdir(tmpdir)
            try:
                save_v2_daughters(mother.state, tmpdir)
            finally:
                os.chdir(prev_cwd)
            full = get_state_from_file(
                path=os.path.join(tmpdir, 'daughter_state_0.json'))
        daughter_json = next(iter(full.get('agents', {}).values()))

    print(f'[probe] daughters ready. Building gen-1 cells from each...',
          flush=True)

    # Build cell_A from daughter_inmem on its own core
    core_a = allocate_core(); core_a.register_types(ECOLI_TYPES)
    t0 = time.perf_counter()
    cell_a = build_cell(core_a, sim_config_template, sim_data_path,
                        daughter_inmem, agent_id='00',
                        gen_seed=args.gen_seed)
    print(f'[probe]   cell_A built in {time.perf_counter()-t0:.1f}s', flush=True)

    # Build cell_B from daughter_json on its own core
    core_b = allocate_core(); core_b.register_types(ECOLI_TYPES)
    t0 = time.perf_counter()
    cell_b = build_cell(core_b, sim_config_template, sim_data_path,
                        daughter_json, agent_id='00',
                        gen_seed=args.gen_seed)
    print(f'[probe]   cell_B built in {time.perf_counter()-t0:.1f}s', flush=True)

    # Diff at t=0 (post-build, post-priming-steps)
    state_a = cell_a.state['agents']['00']
    state_b = cell_b.state['agents']['00']
    diffs = diff_cells(state_a, state_b)
    print(f'\n[probe] post-build (t=0): {len(diffs)} top-level diffs', flush=True)
    for key, why in diffs[:30]:
        print(f'  ~ {key}: {why}', flush=True)

    # Tick both for N steps in lockstep
    for i in range(1, args.ticks + 1):
        t0 = time.perf_counter()
        cell_a.run(1.0)
        cell_b.run(1.0)
        wall = time.perf_counter() - t0
        state_a = cell_a.state['agents']['00']
        state_b = cell_b.state['agents']['00']
        diffs = diff_cells(state_a, state_b)
        print(f'\n[probe] tick {i}: A_t={cell_a.state.get("global_time"):.1f} '
              f'B_t={cell_b.state.get("global_time"):.1f} '
              f'wall={wall:.1f}s diffs={len(diffs)}', flush=True)
        for key, why in diffs[:30]:
            print(f'  ~ {key}: {why}', flush=True)
        if diffs and i == 1:
            print(f'[probe] (first divergence at tick {i}; continuing to '
                  f'see if more accumulate)', flush=True)

    print(f'\n[probe] done.', flush=True)


if __name__ == '__main__':
    main()
