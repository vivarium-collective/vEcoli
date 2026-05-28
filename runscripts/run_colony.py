"""Colony simulation: one Composite, agents map of E. coli cells.

This driver is intentionally minimal: it builds a single :py:class:`Composite`
whose state is just ``{'agents': {'0': <cell_state>}}`` produced by
:py:func:`~ecoli.composites.ecoli_composite.build_ecoli_document`, then runs
it. Division happens *inside* the Composite, exactly the way it already
works inside the lineage runner's per-gen inner Composite:

1. ``CompositeDivision`` (at ``agents/<id>/division``) detects mass +
   chromosome threshold and emits ``{'agents': {'_divide': {mother,
   daughters}}}``. Its ``agents`` port is wired to ``('..', '..',
   'agents')`` — the root agents map.
2. The framework's ``_handle_divide_sentinel`` walks the cell's typed
   schema, splitting bulk (binomial), unique molecules (domain-aware),
   sharing listeners, re-realizing process links with fresh instances.
3. The mother key is popped, two daughter keys are installed in
   ``agents``. ``_realize_structural_subtrees`` instantiates the new
   daughter processes; ``_apply_structural_events`` updates
   ``process_paths`` / ``step_paths`` incrementally.
4. On the next tick the daughters run alongside any siblings; if either
   crosses the mass threshold it divides again. Population grows
   exponentially with no driver-side bookkeeping.

No ``EcoliCellAgent`` wrapper, no manual ``_remove`` + ``_add``, no
outer/inner boundary — the engine does all the work because the cell is
*already* a process tree directly under ``agents/<id>``.

Usage (local milestone, one division):
    uv run python runscripts/run_colony.py \\
        --sim-data-path out/kb/simData.cPickle \\
        --target-doublings 1 --max-duration 3600

Usage (Ray cluster, eight doublings):
    uv run python runscripts/run_colony.py \\
        --sim-data-path s3://.../simData.cPickle \\
        --target-doublings 8 --max-duration 28800 \\
        --ray-address ray://10.99.x.x:10001 \\
        --parallel
"""
import os

# Pin numerics single-threaded BEFORE any numpy/scipy/numba/ray import.
# Threading config is read at import time; setting these after numpy is
# already bound is too late. ``setdefault`` so the caller can override.
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


def _load_sim_config(extra_config_path=None):
    """Load default.json (+ optional overrides) and run EcoliSim's
    preprocessing so ``processes`` is ``{name: Class}``, topology and
    process_configs are resolved — the shape ``build_ecoli_document``
    expects."""
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
    ap.add_argument('--sim-data-path', required=True,
                    help='Local sim_data path or s3:// URI.')
    ap.add_argument('--config', default=None,
                    help='Extra config JSON merged onto default.json.')
    ap.add_argument('--target-doublings', type=int, default=1,
                    help='Final-gen cell target = 2^this. Default 1 -> 2 cells.')
    ap.add_argument('--base-seed', type=int, default=0)
    ap.add_argument('--max-duration', type=float, default=3600.0,
                    help='Sim-time cap (s). Default 3600s ~ one division.')
    ap.add_argument('--parallel', action='store_true',
                    help='Pass parallel_processes=True to Composite so '
                         'sibling cells run on separate workers.')
    ap.add_argument('--ray-address', default=None,
                    help='ray://host:port. Required for shared-cluster '
                         'execution; otherwise local Ray is started.')
    args = ap.parse_args()

    target_n = 2 ** args.target_doublings
    print(f'[colony] target: 1 -> {target_n} cells '
          f'({args.target_doublings} doublings)', flush=True)

    # --- type registry ----------------------------------------------
    from process_bigraph import Composite, allocate_core
    from ecoli.library.bigraph_types import ECOLI_TYPES
    core = allocate_core()
    core.register_types(ECOLI_TYPES)

    # --- ray (cluster mode) -----------------------------------------
    if args.ray_address or args.parallel:
        try:
            import ray
        except ImportError:
            ray = None
        if ray is not None:
            if args.ray_address:
                ray.init(address=args.ray_address, log_to_driver=False)
                print(f'[colony] connected to Ray cluster: '
                      f'{args.ray_address}', flush=True)
            elif not ray.is_initialized():
                ray.init()
                print('[colony] local Ray runtime started', flush=True)

    # --- sim_config -------------------------------------------------
    sim_config = _load_sim_config(args.config)
    sim_config['sim_data_path'] = args.sim_data_path
    sim_config['seed'] = args.base_seed
    sim_config['agent_id'] = '0'
    # divide=True is already on default.json; this is just belt-and-
    # suspenders — without it CompositeDivision is not added.
    sim_config['divide'] = True

    # --- sim_data (loaded once) -------------------------------------
    # Per-gen LoadSimData wrappers reuse this loaded pickle by setting
    # ``sim_data=`` on the call. This keeps mother and daughters on the
    # same Metabolism runtime state (eval'd lambdas live on the
    # sim_data instance — see memory:metabolism_pickle_patch).
    from ecoli.library.sim_data import LoadSimData
    from ecoli.composites.ecoli_composite import build_ecoli_document
    print(f'[colony] loading sim_data from {args.sim_data_path}...',
          flush=True)
    t0 = time.perf_counter()
    lsd = LoadSimData(**{**sim_config, 'seed': args.base_seed})
    print(f'[colony] sim_data loaded in {time.perf_counter()-t0:.1f}s',
          flush=True)

    # --- build the cell document -----------------------------------
    # ``build_ecoli_document`` returns
    # ``{'agents': {'0': <full_cell_state>}}`` where cell_state contains
    # bulk + unique + listeners + environment + every process/step
    # declaration (with address/config — realize re-instantiates).
    # CompositeDivision lives inside this same cell tree at
    # ``agents/0/division`` and its ``agents`` port is wired up to the
    # root agents map by ``_build_topology``.
    print('[colony] building gen-0 cell document...', flush=True)
    t0 = time.perf_counter()
    state = build_ecoli_document(core, sim_config, load_sim_data=lsd)
    print(f'[colony] document built in {time.perf_counter()-t0:.1f}s; '
          f'initial agents: {sorted(state["agents"].keys())}',
          flush=True)

    # --- Composite ---------------------------------------------------
    # ``run_steps_on_init=True`` matches what EcoliCellProcess does
    # internally (primes listeners on init). ``parallel_processes``
    # lets sibling cells run on separate workers once the colony grows
    # past a handful of cells.
    composite = Composite(
        {'schema': {},
         'state': state,
         'run_steps_on_init': True,
         'parallel_processes': args.parallel},
        core=core,
    )
    print(f'[colony] composite ready. process_paths='
          f'{len(composite.process_paths)} step_paths='
          f'{len(composite.step_paths)}', flush=True)

    # --- run ---------------------------------------------------------
    print(f'[colony] running for max {args.max_duration:.0f}s sim time...',
          flush=True)
    t0 = time.perf_counter()
    composite.run(args.max_duration)
    wall = time.perf_counter() - t0

    final_agents = composite.state.get('agents', {})
    print(f'[colony] done. wall={wall:.1f}s '
          f'sim_time={composite.state.get("global_time"):.1f} '
          f'final_cells={len(final_agents)} '
          f'ids={sorted(final_agents.keys())}', flush=True)


if __name__ == '__main__':
    main()
