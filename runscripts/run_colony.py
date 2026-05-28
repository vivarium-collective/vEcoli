"""Colony simulation: one Composite, N cells in an ``agents`` map.

Each cell is an ``EcoliCellAgent`` (subclass of EcoliCellProcess that
adds the _remove/_add divide emission to the outer agents map). On
inner division, the cell emits the sentinel and the framework swaps
the mother for two fresh daughter agents in-place.

Two execution modes:
  --local    address="local:EcoliCellAgent" — single Python process,
             cells run sequentially. Use for the N=2-4 milestone test
             to validate division propagation.
  (default)  address="ray:EcoliCellAgent" — process_bigraph Ray
             protocol with shared shard-actor pool. Use on a Ray
             cluster for the scaled colony runs.

Strategy: pre-provision the cluster for the final-gen cell count
(no autoscaling — final gen ~ 50% of total compute).

Usage (local milestone):
    uv run python runscripts/run_colony.py --local \\
        --sim-data-path out/.../simData.cPickle \\
        --target-doublings 1 --max-duration 3600

Usage (Ray cluster):
    uv run python runscripts/run_colony.py \\
        --sim-data-uri s3://.../simData.cPickle \\
        --target-doublings 8 \\
        --n-shards 64 \\
        --ray-address ray://10.99.x.x:10001
"""
import os

# Pin numerics to single-threaded BEFORE any numpy/scipy/numba/ray
# import. Identical rationale to run_composite_lineage_ray.py.
os.environ.setdefault('OMP_NUM_THREADS', '1')
os.environ.setdefault('MKL_NUM_THREADS', '1')
os.environ.setdefault('OPENBLAS_NUM_THREADS', '1')
os.environ.setdefault('NUMBA_NUM_THREADS', '1')
os.environ.setdefault('NUMEXPR_NUM_THREADS', '1')
os.environ.setdefault('VECLIB_MAXIMUM_THREADS', '1')
os.environ.setdefault('POLARS_MAX_THREADS', '1')
try:
    from threadpoolctl import threadpool_limits as _tpl
    _tpl(limits=1)
except ImportError:
    pass

import argparse
import json
import time

from configs import CONFIG_DIR_PATH
from ecoli.library.bigraph_types import ECOLI_TYPES
from ecoli.composites.ecoli_cell_agent import (
    EcoliCellAgent, set_shared_sim_data)


def build_cell_agent(agent_id, lineage_seed, sim_data_path,
                     sim_config, address):
    """Outer process-node declaration for one cell."""
    return {
        '_type': 'process',
        'address': address,
        'config': {
            'agent_id': agent_id,
            'lineage_seed': lineage_seed,
            'sim_data_path': sim_data_path,
            'sim_config': sim_config,
            'self_address': address,
        },
        'inputs': {},
        'outputs': {
            # Empty wire projects to the parent slot (the outer
            # ``agents`` map). ``['..']`` would lift further to root —
            # only correct for cells nested 3 levels deep (like
            # process_bigraph.growth_division's environment/<id>/cell
            # pattern). Ours are at agents/<id> so the parent slot is
            # already the map we want to mutate.
            'agents': [],
        },
        'interval': 1.0,
    }


def build_initial_state(sim_data_path, sim_config, n_initial_cells,
                        base_seed, address):
    return {
        'agents': {
            str(i): build_cell_agent(
                agent_id=str(i),
                lineage_seed=base_seed + i,
                sim_data_path=sim_data_path,
                sim_config=sim_config,
                address=address,
            )
            for i in range(n_initial_cells)
        },
    }


def load_sim_config(extra_config_path=None):
    """Load default.json (+ optional overrides) and run EcoliSim's
    preprocessing so ``processes`` is a {name: Class} dict, topology /
    process_configs are resolved, etc. — the shape ``build_ecoli_document``
    expects."""
    from ecoli.experiments.ecoli_master_sim import EcoliSim
    cfg_path = os.path.join(CONFIG_DIR_PATH, 'default.json')
    sim = EcoliSim.from_file(cfg_path)
    if extra_config_path:
        with open(extra_config_path) as f:
            sim.config.update(json.load(f))
    # Run EcoliSim's normal pre-build preprocessing so the config dict
    # has resolved process classes / topology / process_configs (the
    # ConfigEntry descriptor mirrors writes to self.config).
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
    ap.add_argument('--sim-data-path', required=False, default=None,
                    help='Local sim_data path (for --local mode).')
    ap.add_argument('--sim-data-uri', required=False, default=None,
                    help='S3 sim_data URI (for Ray cluster mode).')
    ap.add_argument('--config', default=None,
                    help='Extra config JSON merged onto default.json.')
    ap.add_argument('--initial-cells', type=int, default=1)
    ap.add_argument('--target-doublings', type=int, default=1,
                    help='Final-gen cell target = 2^this. Default 1 -> 2 cells '
                         '(the milestone test).')
    ap.add_argument('--base-seed', type=int, default=0)
    ap.add_argument('--max-duration', type=float, default=3600.0,
                    help='Sim-time cap (s). Default 3600s ~ one division.')
    ap.add_argument('--local', action='store_true',
                    help='Single-process mode (address=local:EcoliCellAgent). '
                         'Use for the small-N validation milestone.')
    ap.add_argument('--n-shards', type=int, default=None,
                    help='Ray shard-actor pool size (cluster mode only).')
    ap.add_argument('--ray-address', default=None,
                    help='ray://host:port (cluster mode).')
    args = ap.parse_args()

    sim_data_path = args.sim_data_path or args.sim_data_uri
    if sim_data_path is None:
        raise SystemExit('Need either --sim-data-path or --sim-data-uri.')

    target_n = 2 ** args.target_doublings
    print(f'[colony] target: {args.initial_cells} -> {target_n} cells '
          f'({args.target_doublings} doublings)', flush=True)

    # Address prefix selects the execution protocol. Local mode uses
    # the ``local:!module.Class`` form so the framework auto-imports
    # without a separate registry call.
    if args.local:
        address = 'local:!ecoli.composites.ecoli_cell_agent.EcoliCellAgent'
    else:
        address = 'ray:EcoliCellAgent'
    print(f'[colony] cell address: {address}', flush=True)

    # Build the core type registry with vEcoli types.
    from process_bigraph import Composite, allocate_core
    core = allocate_core()
    core.register_types(ECOLI_TYPES)

    # In cluster mode also register the process class with the Ray
    # protocol so shard actors can find it.
    if not args.local:
        from runscripts.run_composite_lineage_ray import _patch_s3fs_skip_create_bucket
        _patch_s3fs_skip_create_bucket()
        if args.n_shards is not None:
            os.environ['RAY_SHARDS_DEFAULT'] = str(args.n_shards)
            print(f'[colony] RAY_SHARDS_DEFAULT={args.n_shards}', flush=True)
        import ray
        from process_bigraph.protocols.ray import register_process_class
        register_process_class('EcoliCellAgent', EcoliCellAgent)
        ray_address = args.ray_address or os.environ.get('RAY_ADDRESS')
        if ray_address:
            ray.init(address=ray_address, log_to_driver=False)
            print(f'[colony] connected to Ray cluster: {ray_address}',
                  flush=True)
        else:
            ray.init()
            print('[colony] local Ray runtime', flush=True)

    sim_config = load_sim_config(args.config)
    sim_config['sim_data_path'] = sim_data_path

    # Pre-load sim_data ONCE and register it module-globally so every
    # EcoliCellAgent (mother + all daughters) shares one in-memory
    # Metabolism runtime state — avoids per-cell pickle reload that
    # caused FBA GLP_NOFEAS in daughters (mother's bulk produced
    # against state A, daughter FBA against fresh-load state A').
    from ecoli.library.sim_data import LoadSimData
    print('[colony] pre-loading sim_data once for shared use...',
          flush=True)
    t0 = time.perf_counter()
    base_kwargs = dict(sim_config)
    base_kwargs['seed'] = args.base_seed
    base_lsd = LoadSimData(**base_kwargs)
    set_shared_sim_data(base_lsd.sim_data)
    print(f'[colony] sim_data loaded in {time.perf_counter()-t0:.1f}s',
          flush=True)

    state = build_initial_state(
        sim_data_path=sim_data_path,
        sim_config=sim_config,
        n_initial_cells=args.initial_cells,
        base_seed=args.base_seed,
        address=address,
    )

    # Schema tells the framework `agents` is a map of process
    # declarations — stops Tree.realize from recursing into the cell's
    # config (which contains the entire resolved sim_config, hundreds
    # of process classes + topology + flow, and would otherwise be
    # traversed key-by-key by the leaf-probing realize loop).
    schema = {'agents': {'_type': 'map', '_value': 'process'}}
    sim = Composite(
        {'state': state, 'schema': schema,
         'parallel_processes': not args.local},
        core=core,
    )

    print(f'[colony] process_paths: {len(sim.process_paths)} '
          f'({sorted(sim.process_paths.keys())})', flush=True)
    # Show the cell's realized outputs to confirm 'agents' is wired
    for cid, val in sim.state.get('agents', {}).items():
        if isinstance(val, dict):
            print(f'[colony]   cell {cid} outputs wire: {val.get("outputs")}',
                  flush=True)
            print(f'[colony]   cell {cid} _outputs schema keys: '
                  f'{list(val.get("_outputs", {}).keys()) if isinstance(val.get("_outputs"), dict) else type(val.get("_outputs")).__name__}',
                  flush=True)
    print(f'[colony] running for max {args.max_duration:.0f}s sim time...',
          flush=True)
    t0 = time.perf_counter()
    sim.run(args.max_duration)
    wall = time.perf_counter() - t0

    final_n = len(sim.state.get('agents', {}))
    final_ids = sorted(sim.state.get('agents', {}).keys())
    print(f'[colony] done. wall={wall:.1f}s sim_time={sim.state.get("global_time")} '
          f'final_cells={final_n} ids={final_ids}', flush=True)
    # Check that daughters were realized (have instance), not just decl dicts.
    for cid, val in sim.state.get('agents', {}).items():
        inst = val.get('instance') if isinstance(val, dict) else None
        inst_type = type(inst).__name__ if inst is not None else 'NO_INSTANCE'
        print(f'[colony]   agent {cid}: instance={inst_type}', flush=True)
    print(f'[colony] process_paths after run: '
          f'{sorted(sim.process_paths.keys())}', flush=True)


if __name__ == '__main__':
    main()
