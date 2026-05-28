"""Compare mother-at-divide vs each daughter-post-divide in the greenfield
colony. Targets the fields that historically cause GLP_NOFEAS: mass
listener fields, volume, bulk count sums, unique molecule counts,
environment, and the presence of the per-cell stores (process,
sim_data_objects, allocator_rng, request/allocate/next_update_time).

Outputs:
  - mass conservation check: sum(daughters[k]) == mother[k] (for bulk
    integer counts)
  - mass listener snapshot per cell
  - presence/absence of critical keys per cell
  - division step's agent_id per cell (catches the stale-id bug)
"""
import os
os.environ.setdefault('OMP_NUM_THREADS', '1')
os.environ.setdefault('MKL_NUM_THREADS', '1')
os.environ.setdefault('OPENBLAS_NUM_THREADS', '1')
os.environ.setdefault('NUMBA_NUM_THREADS', '1')

import sys
from copy import deepcopy
import numpy as np

from configs import CONFIG_DIR_PATH
from ecoli.library.bigraph_types import ECOLI_TYPES
from ecoli.composites.ecoli_composite import build_ecoli_document, run_to_division
from ecoli.experiments.ecoli_master_sim import EcoliSim
from ecoli.library.sim_data import LoadSimData
from process_bigraph import Composite, allocate_core


def _bulk_sum(cell):
    """Return total count in cell.bulk, or None if absent/wrong type."""
    b = cell.get('bulk')
    if b is None or not hasattr(b, 'dtype') or not b.dtype.names:
        return None
    if 'count' not in b.dtype.names:
        return None
    return int(b['count'].sum())


def _unique_counts(cell):
    """Return {mol_name: n_active} mapping for cell.unique."""
    u = cell.get('unique', {})
    if not isinstance(u, dict):
        return {}
    out = {}
    for name, arr in u.items():
        if hasattr(arr, 'dtype') and arr.dtype.names and '_entryState' in arr.dtype.names:
            out[name] = int(arr['_entryState'].sum())
        elif hasattr(arr, '__len__'):
            out[name] = len(arr)
    return out


def _mass_snapshot(cell):
    listeners = cell.get('listeners') or {}
    if not isinstance(listeners, dict):
        return {}
    mass = listeners.get('mass') or {}
    if not isinstance(mass, dict):
        return {}
    out = {}
    for k in ('cell_mass', 'dry_mass', 'volume', 'protein_mass',
              'rna_mass', 'water_mass', 'instantaneous_growth_rate'):
        v = mass.get(k)
        if v is None:
            continue
        if isinstance(v, np.ndarray):
            v = float(v.item()) if v.size == 1 else f'shape={v.shape}'
        out[k] = v
    return out


def _agent_id_in_division(cell):
    div = cell.get('division') or {}
    if isinstance(div, dict):
        cfg = div.get('config') or {}
        return cfg.get('agent_id')
    return None


def _key_presence(cell):
    keys = ('bulk', 'unique', 'listeners', 'environment', 'global_time',
            'timestep', 'process', 'sim_data_objects', 'allocator_rng',
            'request', 'allocate', 'next_update_time', 'boundary',
            'division', 'division_threshold')
    return {k: (k in cell) for k in keys}


def main():
    sim_data_path = sys.argv[1] if len(sys.argv) > 1 else 'out/kb/simData.cPickle'

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

    state = build_ecoli_document(core, sim_config, load_sim_data=lsd)
    composite = Composite(
        {'schema': {}, 'state': state, 'run_steps_on_init': True},
        core=core)

    print(f'[diff] gen0 mother built. agents={sorted(composite.state["agents"].keys())}',
          flush=True)
    mother = deepcopy(composite.state['agents']['0'])
    print(f'[diff]   mother@t=0 bulk.count.sum={_bulk_sum(mother)}', flush=True)
    print(f'[diff]   mother@t=0 mass={_mass_snapshot(mother)}', flush=True)
    print(f'[diff]   mother@t=0 division.config.agent_id={_agent_id_in_division(mother)!r}',
          flush=True)

    # Run to first divide
    divided, ct = run_to_division(composite, max_duration=3000)
    print(f'\n[diff] divided={divided} at t={ct:.1f}', flush=True)
    if not divided:
        print('[diff] NO DIVIDE — increase max_duration', flush=True)
        return

    daughters = composite.state['agents']
    print(f'[diff] post-divide agents={sorted(daughters.keys())}', flush=True)

    # Mother snapshot at division (we don't have it; the divide already
    # ran). The PRE-divide mother bulk sum equals the SUM of daughters
    # post-divide if mass is conserved.
    daughter_bulk_sums = {k: _bulk_sum(c) for k, c in daughters.items()
                          if isinstance(c, dict)}
    combined = sum(v for v in daughter_bulk_sums.values() if v is not None)
    print(f'\n[diff] bulk.count.sum per daughter: {daughter_bulk_sums}',
          flush=True)
    print(f'[diff]   combined daughters = {combined} (expect ≈ mother pre-divide)',
          flush=True)

    for did in sorted(daughters.keys()):
        cell = daughters[did]
        if not isinstance(cell, dict):
            print(f'[diff] {did}: not a dict ({type(cell).__name__})', flush=True)
            continue
        print(f'\n[diff] === DAUGHTER {did!r} ===', flush=True)
        print(f'[diff]   key_presence: {_key_presence(cell)}', flush=True)
        print(f'[diff]   division.config.agent_id={_agent_id_in_division(cell)!r}',
              flush=True)
        print(f'[diff]   mass={_mass_snapshot(cell)}', flush=True)
        print(f'[diff]   unique={_unique_counts(cell)}', flush=True)
        # Allocator
        print(f'[diff]   has allocator_rng: {cell.get("allocator_rng") is not None}',
              flush=True)
        # Process store (SharedProcess instances)
        proc_store = cell.get('process') or {}
        if isinstance(proc_store, dict):
            n_procs = sum(1 for v in proc_store.values()
                          if isinstance(v, (tuple, dict)))
            print(f'[diff]   process store entries: {n_procs}', flush=True)
        # Per-process runtime state
        nut = cell.get('next_update_time') or {}
        req = cell.get('request') or {}
        alloc = cell.get('allocate') or {}
        if isinstance(nut, dict):
            print(f'[diff]   next_update_time entries: {len(nut)} '
                  f'sample={dict(list(nut.items())[:3])}', flush=True)
        if isinstance(req, dict):
            print(f'[diff]   request entries: {len(req)}', flush=True)
        if isinstance(alloc, dict):
            print(f'[diff]   allocate entries: {len(alloc)}', flush=True)


if __name__ == '__main__':
    main()
