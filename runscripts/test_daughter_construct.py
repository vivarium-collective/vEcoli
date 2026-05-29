"""Load /tmp/daughter_snapshot.pkl + try to construct the daughter
cell-Composite from it. Reproduces the hang in seconds (not minutes)
because mother already ran its 2700 sim sec; we just instantiate the
daughter directly.
"""
import os
os.environ.setdefault('OMP_NUM_THREADS', '1')
os.environ.setdefault('MKL_NUM_THREADS', '1')

import pickle
import signal
import faulthandler
import sys
import time

faulthandler.register(signal.SIGUSR1, all_threads=True)


def main():
    print('[test] loading daughter snapshot...', flush=True)
    with open('/tmp/daughter_snapshot.pkl', 'rb') as f:
        snap = pickle.load(f)
    print(f'[test]   daughter_id: {snap["daughter_id"]}', flush=True)
    print(f'[test]   daughter_state top keys: '
          f'{sorted(snap["daughter_state"].keys())[:10]}', flush=True)
    print(f'[test]   wrapped keys: {sorted(snap["wrapped"].keys())}',
          flush=True)
    print(f'[test]   cell_tree_schema type: '
          f'{type(snap["cell_tree_schema"]).__name__}', flush=True)

    # Install module-level caches (probe normally does this)
    from ecoli.processes.cell_division import (
        set_cell_tree_schema, set_daughter_wrap_template)
    set_cell_tree_schema(snap['cell_tree_schema'])
    set_daughter_wrap_template(snap['wrapped'])

    # Build core (matches probe setup)
    from ecoli.library.bigraph_types import ECOLI_TYPES
    from ecoli.library import bigraph_types as _bt
    from process_bigraph import Composite, allocate_core

    core = allocate_core()
    core.register_types(ECOLI_TYPES)
    core.register_link('Composite', Composite)

    # Construct daughter Composite directly
    # The wrapped decl has _type=process, address=local:Composite,
    # config={state: {agents: {daughter_id: daughter_state}}, ...}
    daughter_config = snap['wrapped']['config']
    print(f'[test] daughter_config keys: {sorted(daughter_config.keys())}',
          flush=True)
    print(f'[test] config.state keys: {sorted(daughter_config["state"].keys())}',
          flush=True)

    print(f'[test] PID={os.getpid()} — SIGUSR1 me if I hang', flush=True)
    print('[test] instantiating daughter Composite...', flush=True)
    t0 = time.perf_counter()
    daughter = Composite(daughter_config, core=core)
    print(f'[test] ✅ daughter built in {time.perf_counter()-t0:.1f}s',
          flush=True)
    print(f'[test] daughter.state keys: {sorted(daughter.state.keys())}',
          flush=True)
    print(f'[test] daughter.process_paths: '
          f'{list(daughter.process_paths.keys())[:5]}', flush=True)


if __name__ == '__main__':
    main()
