"""Isolate daughter init: divide a cell, then rebuild a daughter
directly (lineage-runner style) and tick it. If this succeeds, the
colony's framework-driven _add path is the bug. If it fails, the bug
is in our daughter-payload handoff itself.
"""
import os

os.environ.setdefault('OMP_NUM_THREADS', '1')
os.environ.setdefault('MKL_NUM_THREADS', '1')
os.environ.setdefault('OPENBLAS_NUM_THREADS', '1')
os.environ.setdefault('NUMBA_NUM_THREADS', '1')

import argparse
import json
import time
from copy import deepcopy

from configs import CONFIG_DIR_PATH
from ecoli.library.bigraph_types import ECOLI_TYPES
from ecoli.composites.ecoli_cell_agent import EcoliCellAgent
from ecoli.composites.ecoli_composite import _v2_daughter_payload


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sim-data-path', required=True)
    ap.add_argument('--mother-duration', type=float, default=2700.0,
                    help='How long to run mother before extracting daughter.')
    ap.add_argument('--daughter-duration', type=float, default=600.0,
                    help='How long to tick daughter after rebuild.')
    args = ap.parse_args()

    from ecoli.experiments.ecoli_master_sim import EcoliSim
    from process_bigraph import Composite, allocate_core

    print('[test] building core + EcoliSim preprocessing...', flush=True)
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

    core = allocate_core()
    core.register_types(ECOLI_TYPES)

    # Pre-load sim_data ONCE (lineage-runner pattern). All gens reuse
    # this same object via ``sim_data`` config — both mother and
    # daughter see the same Python instance, which matters because
    # Metabolism's pickle round-trip drops runtime state (see memory
    # metabolism_pickle_patch).
    from ecoli.library.sim_data import LoadSimData
    print('[test] pre-loading sim_data...', flush=True)
    t0 = time.perf_counter()
    base_kwargs = dict(sim_config)
    base_kwargs['seed'] = 0
    base_lsd = LoadSimData(**base_kwargs)
    shared_sim_data = base_lsd.sim_data
    print(f'[test] sim_data loaded in {time.perf_counter()-t0:.1f}s',
          flush=True)

    # Build the mother cell directly (lineage-runner style)
    print('[test] building mother cell EcoliCellAgent(0)...', flush=True)
    t0 = time.perf_counter()
    mother = EcoliCellAgent(
        config={
            'lineage_seed': 0,
            'agent_id': '0',
            'sim_data_path': args.sim_data_path,
            'sim_data': shared_sim_data,
            'sim_config': sim_config,
        },
        core=core,
    )
    print(f'[test] mother built in {time.perf_counter()-t0:.1f}s', flush=True)

    # Tick mother until division using run_to_division — the lineage
    # runner's poll pattern that stops AT division so daughter state is
    # not mutated by ~remaining-interval post-divide process ticks.
    print(f'[test] ticking mother (run_to_division max={args.mother_duration}s)...',
          flush=True)
    from ecoli.composites.ecoli_composite import run_to_division
    t0 = time.perf_counter()
    inner = mother.inner_composite
    inner._halt_after_structural = True
    pre = len(inner.state.get('agents', {}))
    divided, ct = run_to_division(inner, args.mother_duration)
    post = len(inner.state.get('agents', {}))
    print(f'[test] mother done in {time.perf_counter()-t0:.1f}s. '
          f'divided={divided} inner agents: pre={pre} post={post} '
          f'global_time={ct:.1f}', flush=True)
    if not divided:
        print('[test] mother did NOT divide — bailing.')
        return

    if post <= pre:
        print('[test] mother did NOT divide — increase --mother-duration')
        return

    # Extract daughter '00'
    daughter_ids = sorted(inner.state['agents'].keys())
    target = daughter_ids[0]
    d_state = deepcopy(inner.state['agents'][target])
    print(f'[test] daughter target={target}. raw keys={sorted(d_state.keys())[:15]}...',
          flush=True)
    _v2_daughter_payload(d_state)
    print(f'[test] after _v2_daughter_payload: keys={sorted(d_state.keys())[:15]}...',
          flush=True)
    if 'bulk' in d_state and hasattr(d_state['bulk'], 'dtype') \
            and 'count' in d_state['bulk'].dtype.names:
        print(f'[test]   bulk.count.sum={int(d_state["bulk"]["count"].sum())}',
              flush=True)

    # Build daughter directly (lineage-runner style), passing the
    # same shared sim_data object.
    print(f'[test] building daughter cell EcoliCellAgent({target})...',
          flush=True)
    t0 = time.perf_counter()
    daughter = EcoliCellAgent(
        config={
            'lineage_seed': 0,
            'agent_id': target,
            'sim_data_path': args.sim_data_path,
            'sim_data': shared_sim_data,
            'sim_config': sim_config,
            'initial_state': d_state,
        },
        core=core,
    )
    print(f'[test] daughter built in {time.perf_counter()-t0:.1f}s', flush=True)

    # Tick daughter
    print(f'[test] ticking daughter for {args.daughter_duration}s sim time...',
          flush=True)
    t0 = time.perf_counter()
    d_inner = daughter.inner_composite
    pre_d = len(d_inner.state.get('agents', {}))
    d_inner.run(args.daughter_duration)
    post_d = len(d_inner.state.get('agents', {}))
    print(f'[test] daughter done in {time.perf_counter()-t0:.1f}s. '
          f'inner agents: pre={pre_d} post={post_d} '
          f'global_time={d_inner.state.get("global_time")}',
          flush=True)
    if post_d > pre_d:
        print('[test] SUCCESS: daughter divided cleanly via direct construction')
    else:
        print('[test] daughter did not divide within window (may still be growing)')


if __name__ == '__main__':
    main()
