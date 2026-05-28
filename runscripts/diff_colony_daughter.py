"""Compare what the inner _divide_state produces vs what the
daughter cell's _build_inner_composite produces from that state.

Both should be the same — if they differ, the difference is the bug
that breaks daughter FBA.

Steps:
  1. Build mother EcoliCellAgent
  2. Run mother to division via run_to_division
  3. Snapshot inner.state.agents['00'] (what _divide_state produced)
  4. Apply _v2_daughter_payload and pass as initial_state to a new
     daughter EcoliCellAgent (same as the colony driver does)
  5. Snapshot the new daughter's inner.state (what _build_inner_composite
     reconstructed)
  6. Diff field-by-field: missing keys, type changes, value diffs
     (bulk counts, unique counts, scalars, etc.)
"""
import os

os.environ.setdefault('OMP_NUM_THREADS', '1')
os.environ.setdefault('MKL_NUM_THREADS', '1')
os.environ.setdefault('OPENBLAS_NUM_THREADS', '1')
os.environ.setdefault('NUMBA_NUM_THREADS', '1')

import argparse
import time
from copy import deepcopy

import numpy as np

from configs import CONFIG_DIR_PATH
from ecoli.library.bigraph_types import ECOLI_TYPES
from ecoli.composites.ecoli_cell_agent import EcoliCellAgent, set_shared_sim_data
from ecoli.composites.ecoli_composite import _v2_daughter_payload


def summarize(state, name='', max_depth=2):
    """Return a flat dict of {path: summary} for comparable values."""
    out = {}
    def walk(d, prefix):
        if isinstance(d, dict):
            for k, v in d.items():
                walk(v, prefix + (k,))
        elif isinstance(d, np.ndarray):
            if d.dtype.names:  # structured
                fields = {f: d[f] for f in d.dtype.names}
                out[prefix] = ('struct_arr',
                               {f: (a.shape,
                                    str(a.dtype),
                                    float(a.sum()) if a.dtype.kind in 'iuf' else None)
                                for f, a in fields.items()})
            else:
                out[prefix] = ('arr', d.shape, str(d.dtype),
                               float(d.sum()) if d.dtype.kind in 'iuf' else None)
        elif isinstance(d, (int, float, bool, str, type(None))):
            out[prefix] = ('scalar', type(d).__name__, d)
        else:
            out[prefix] = ('other', type(d).__name__)
    walk(state, ())
    return out


def diff_states(a_state, b_state, label_a, label_b):
    a = summarize(a_state)
    b = summarize(b_state)
    only_a = sorted(set(a.keys()) - set(b.keys()))
    only_b = sorted(set(b.keys()) - set(a.keys()))
    common = sorted(set(a.keys()) & set(b.keys()))

    print(f'\n=== DIFF: {label_a} vs {label_b} ===')
    print(f'keys only in {label_a}: {len(only_a)}')
    for k in only_a[:30]:
        print(f'  - {".".join(str(x) for x in k)}')
    print(f'keys only in {label_b}: {len(only_b)}')
    for k in only_b[:30]:
        print(f'  + {".".join(str(x) for x in k)}')

    diffs = []
    for k in common:
        if a[k] != b[k]:
            diffs.append((k, a[k], b[k]))
    print(f'value diffs: {len(diffs)}')
    for k, va, vb in diffs[:60]:
        path = '.'.join(str(x) for x in k)
        print(f'  ~ {path}')
        print(f'    {label_a}: {va}')
        print(f'    {label_b}: {vb}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sim-data-path', required=True)
    ap.add_argument('--mother-duration', type=float, default=2700.0)
    args = ap.parse_args()

    from ecoli.experiments.ecoli_master_sim import EcoliSim
    from ecoli.library.sim_data import LoadSimData
    from process_bigraph import allocate_core

    print('[diff] loading config + preprocessing...', flush=True)
    sim = EcoliSim.from_file(os.path.join(CONFIG_DIR_PATH, 'default.json'))
    sim.processes = sim._retrieve_processes(
        sim.processes, sim.add_processes,
        sim.exclude_processes, sim.swap_processes)
    sim.topology = sim._retrieve_topology(
        sim.topology, sim.processes, sim.swap_processes, sim.log_updates)
    sim.process_configs = sim._retrieve_process_configs(
        sim.process_configs, sim.processes)
    sim_config = dict(sim.config)
    sim_config['sim_data_path'] = args.sim_data_path

    print('[diff] loading sim_data...', flush=True)
    base_kwargs = dict(sim_config)
    base_kwargs['seed'] = 0
    base_lsd = LoadSimData(**base_kwargs)
    set_shared_sim_data(base_lsd.sim_data)

    core = allocate_core()
    core.register_types(ECOLI_TYPES)

    print('[diff] building mother...', flush=True)
    mother = EcoliCellAgent(
        config={
            'lineage_seed': 0,
            'agent_id': '0',
            'sim_data_path': args.sim_data_path,
            'sim_config': sim_config,
        },
        core=core,
    )

    print(f'[diff] running mother to division (max={args.mother_duration}s)...',
          flush=True)
    from ecoli.composites.ecoli_composite import run_to_division
    t0 = time.perf_counter()
    divided, ct = run_to_division(mother.inner_composite,
                                  max_duration=args.mother_duration)
    print(f'[diff] mother: divided={divided} at t={ct:.1f} '
          f'({time.perf_counter()-t0:.0f}s wall)',
          flush=True)
    if not divided:
        return

    # State A: what _divide_state produced inside mother's inner
    inner_d0 = deepcopy(mother.inner_composite.state['agents']['00'])
    print(f'[diff] inner-daughter-00 keys: {sorted(inner_d0.keys())[:10]}...',
          flush=True)

    # State B: the same state, payload-filtered
    payload_d0 = deepcopy(inner_d0)
    _v2_daughter_payload(payload_d0)
    print(f'[diff] after _v2_daughter_payload keys: {sorted(payload_d0.keys())[:10]}...',
          flush=True)

    # Build the new daughter with payload as initial_state (same as
    # colony driver does via _build_daughter_decl).
    print('[diff] building daughter cell from payload state...', flush=True)
    daughter = EcoliCellAgent(
        config={
            'lineage_seed': 0,
            'agent_id': '00',
            'sim_data_path': args.sim_data_path,
            'sim_config': sim_config,
            'initial_state': payload_d0,
        },
        core=core,
    )
    # State C: what _build_inner_composite reconstructed
    rebuilt_d0 = deepcopy(daughter.inner_composite.state['agents'].get('00')
                          or daughter.inner_composite.state)
    print(f'[diff] rebuilt-daughter top keys: {sorted(rebuilt_d0.keys())[:10]}...',
          flush=True)

    # Check the critical data fields directly (bulk count + unique counts)
    print('\n=== CRITICAL DATA FIELDS ===', flush=True)
    for label, st in (('payload', payload_d0), ('rebuilt', rebuilt_d0)):
        b = st.get('bulk')
        if hasattr(b, 'dtype') and b.dtype.names and 'count' in b.dtype.names:
            print(f'{label}.bulk.count.sum = {int(b["count"].sum())}', flush=True)
        u = st.get('unique', {})
        if isinstance(u, dict):
            for name, mols in sorted(u.items()):
                if hasattr(mols, '__len__'):
                    try:
                        n = int(mols['_entryState'].sum()) if hasattr(mols, 'dtype') and mols.dtype.names and '_entryState' in mols.dtype.names else len(mols)
                        print(f'{label}.unique.{name}: n_active={n}', flush=True)
                    except Exception:
                        pass

    # ALSO compare mother PRE-divide vs (daughter 0 + daughter 1) for
    # mass conservation — does mother's bulk = sum of daughters' bulk?
    # If not, the divide isn't conserving mass and FBA will see ill-
    # posed constraints.
    print('\n=== MOTHER PRE-DIVIDE vs DAUGHTERS SUM ===', flush=True)
    d1 = mother.inner_composite.state['agents'].get('01', {})
    if 'bulk' in d1 and hasattr(d1['bulk'], 'dtype') and 'count' in d1['bulk'].dtype.names:
        d0_sum = int(inner_d0['bulk']['count'].sum())
        d1_sum = int(d1['bulk']['count'].sum())
        print(f'  daughter 00 bulk.count.sum = {d0_sum}', flush=True)
        print(f'  daughter 01 bulk.count.sum = {d1_sum}', flush=True)
        print(f'  combined daughters         = {d0_sum + d1_sum}', flush=True)
        # We don't have mother's PRE-divide state here (we stopped at
        # division). But the combined daughters should equal mother
        # pre-divide via divide_share/divide for bulk. If they differ
        # widely, something's off in the bulk divide schema.

    # DIFF: payload (what colony passes in) vs rebuilt (what cell ends up with)
    diff_states(payload_d0, rebuilt_d0,
                'payload_into_cell', 'rebuilt_in_cell')


if __name__ == '__main__':
    main()
