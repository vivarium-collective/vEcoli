"""Test the spatio-flux-style architecture for vEcoli colony:
each cell is a ``Composite``-as-Process (no wrapper class), inner
``CompositeDivision`` propagates daughters UP to the outer agents
map via the bridge — mirroring the working ``grow_divide_agent``
pattern in ``process-bigraph/processes/growth_division.py``.

Outer composite shape:
    agents:
      '0':
        _type: process
        address: local:Composite        # ← swap to ray:Composite later
        config:
          state:                        # cell-Composite's INNER state
            agents:
              '0': <full vEcoli cell tree from build_ecoli_document>
          bridge:
            outputs:
              agents: ['agents']        # inner cell.agents slot → bridge out
          run_steps_on_init: True
        outputs:
          agents: ['..']                # bridge out → outer agents map

Inner CompositeDivision emits ``{agents: {_divide: {...}}}`` to its
inner agents map (via its ``('..', '..', 'agents')`` wire). The
inner apply applies the divide → mother removed, daughters added in
the cell-Composite's inner agents. The cell-Composite's bridge
projects inner.agents value out as an OUTPUT update. The outer wire
``['..']`` lifts it to the outer agents map. If the framework
recognizes this as a structural change on the outer map (the way it
does in ``test_grow_divide``), we get daughters as new outer Process
nodes — no wrapper, no per-cell class.

This script verifies the pattern works locally. If yes, swapping
``local:Composite`` for ``ray:Composite`` (plus making the actor's
core have vEcoli types registered) gives us the spatio-flux pattern
with division for free.
"""
import os
os.environ.setdefault('OMP_NUM_THREADS', '1')
os.environ.setdefault('MKL_NUM_THREADS', '1')
os.environ.setdefault('OPENBLAS_NUM_THREADS', '1')
os.environ.setdefault('NUMBA_NUM_THREADS', '1')
try:
    from threadpoolctl import threadpool_limits as _tpl
    _tpl(limits=1)
except ImportError:
    pass

import argparse
import time

from configs import CONFIG_DIR_PATH


def _load_sim_config(extra_config_path=None):
    import json
    from ecoli.experiments.ecoli_master_sim import EcoliSim
    cfg_path = os.path.join(CONFIG_DIR_PATH, 'default.json')
    sim = EcoliSim.from_file(cfg_path)
    if extra_config_path:
        with open(extra_config_path) as f:
            sim.config.update(json.load(f))
    sim.processes = sim._retrieve_processes(
        sim.processes, sim.add_processes,
        sim.exclude_processes, sim.swap_processes)
    sim.topology = sim._retrieve_topology(
        sim.topology, sim.processes, sim.swap_processes, sim.log_updates)
    sim.process_configs = sim._retrieve_process_configs(
        sim.process_configs, sim.processes)
    return dict(sim.config)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sim-data-path', default='out/kb/simData.cPickle')
    ap.add_argument('--max-duration', type=float, default=3000.0)
    ap.add_argument('--base-seed', type=int, default=0)
    args = ap.parse_args()

    sim_data_path = os.path.abspath(args.sim_data_path)

    from process_bigraph import Composite, allocate_core
    from ecoli.library.bigraph_types import ECOLI_TYPES
    from ecoli.library.sim_data import LoadSimData
    from ecoli.composites.ecoli_composite import build_ecoli_document

    core = allocate_core()
    core.register_types(ECOLI_TYPES)

    sim_config = _load_sim_config()
    sim_config['sim_data_path'] = sim_data_path
    sim_config['agent_id'] = '0'
    sim_config['seed'] = args.base_seed
    sim_config['divide'] = True

    # Build the cell tree (same as our greenfield colony).
    print('[probe] building cell tree via build_ecoli_document...',
          flush=True)
    t0 = time.perf_counter()
    lsd = LoadSimData(**{**sim_config, 'seed': args.base_seed})
    cell_doc = build_ecoli_document(core, sim_config, load_sim_data=lsd)
    # cell_doc = {'agents': {'0': <cell_tree>}}
    print(f'[probe]   built in {time.perf_counter()-t0:.1f}s; '
          f'inner agents={sorted(cell_doc["agents"].keys())}', flush=True)

    # Pre-register sim_data_object instances and HARDEN the registry:
    # the SimDataObjectStore.realize handler calls
    # ``_sim_data_object_instances.clear()`` unconditionally, which
    # wipes any pre-registration if it fires with an empty state
    # (which can happen during Composite config_schema realize for a
    # wrapped Composite-as-Process). Monkey-patch clear() to no-op
    # for this test — confirms whether the bridge-propagation pattern
    # itself works, separate from the registry-lifecycle plumbing.
    from ecoli.library import bigraph_types as _bt
    cell_state = cell_doc['agents']['0']
    sd_store = cell_state.get('sim_data_objects', {})
    if isinstance(sd_store, dict):
        for k, v in sd_store.items():
            if not k.startswith('_'):
                _bt._sim_data_object_instances[k] = v
    print(f'[probe] pre-registered {len(_bt._sim_data_object_instances)} '
          f'sim_data_object instances', flush=True)
    # NOTE: SimDataObjectStore.realize used to clear the global
    # registry — that's been removed in this repo (now add-only).
    # Pre-register survives outer Composite's config_schema realize.

    # Wrap the cell tree as a Composite-as-Process. The CONFIG for that
    # process IS a Composite config: state, bridge, run_steps_on_init.
    # We give the cell-Composite an `agents` output port that wires to
    # its inner agents slot via the bridge. The outer wire ['..']
    # lifts to the outer agents map (one level up from the cell node).
    cell_node = {
        '_type': 'process',
        'address': 'local:Composite',
        'config': {
            'state': cell_doc,            # inner state = {agents: {0: cell}}
            'bridge': {
                'outputs': {
                    'agents': ['agents'],  # bridge out → inner agents slot
                },
            },
            'run_steps_on_init': True,
            # Composite's interface needs to declare the output port type
            # so the framework knows what shape flows out. The inner
            # agents map's value type is the cell tree — opaque to the
            # outer's apply, so declare it as a Map of nodes.
            'interface': {
                'inputs': {},
                'outputs': {
                    'agents': {'_type': 'map', '_value': 'node'},
                },
            },
        },
        'outputs': {
            # Bridge output 'agents' wired one level UP from this cell
            # node's position. If outer state has agents.0.cell, ['..']
            # from cell lifts to agents.0 — but we want agents itself.
            # Try ['..',  '..'] which lifts two levels (cell → agents.0
            # → agents).
            'agents': ['..'],
        },
        'interval': 1.0,
    }

    # Outer composite: one cell at agents.0, structured so the cell's
    # output port lifts to the outer agents map.
    outer_state = {
        'agents': {
            '0': cell_node,
        },
    }

    print('[probe] building OUTER composite (cell-as-process)...',
          flush=True)
    t0 = time.perf_counter()
    # Schema: agents is map[process]. Each cell IS a Composite-as-
    # Process. Declaring the value type as `process` (rather than
    # leaving inference or using `node`) tells realize_link to
    # instantiate each map entry via load_protocol — exactly the
    # path that turns a `{_type: process, address: ..., config: ...}`
    # decl into a live Process.
    outer = Composite(
        {'state': outer_state,
         'schema': {'agents': {'_type': 'map', '_value': 'process'}}},
        core=core,
    )
    print(f'[probe]   built in {time.perf_counter()-t0:.1f}s; '
          f'outer process_paths={len(outer.process_paths)} '
          f'agents={sorted(outer.state.get("agents", {}).keys())}',
          flush=True)

    # Run until division should occur.
    print(f'[probe] running for {args.max_duration:.0f}s sim time...',
          flush=True)
    t0 = time.perf_counter()
    outer.run(args.max_duration)
    wall = time.perf_counter() - t0
    final = outer.state.get('agents', {})
    print(f'[probe] done. wall={wall:.1f}s '
          f'sim_time={outer.state.get("global_time")} '
          f'outer agents={sorted(final.keys())}', flush=True)
    print('[probe] success criteria: outer agents has more than just "0" '
          '(daughters propagated UP via bridge).', flush=True)


if __name__ == '__main__':
    main()
