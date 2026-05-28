"""Colony on Ray — wraps the working greenfield ``run_colony.py``
loop in a single ``@ray.remote`` actor.

This is the minimum step from "colony works locally" to "colony runs
on a Ray cluster". The actor builds the exact same Composite
(``build_ecoli_document`` → ``Composite``) and ticks it to division.
Cells live under ``agents/<id>`` and divide in place via the
schema-driven ``_divide`` sentinel (same path 2-doublings verified
locally). One actor per call here — multi-actor parallel colonies
(M independent seeds) is one extra ``ray.get([fn.remote(seed) ...])``
away.

Env coupling between cells is intentionally NOT done here — each
cell still has its own internal ``environment`` store. The user-
facing semantics match the local greenfield run.

Usage (local Ray runtime, 1 cell → 2 cells):
    .venv/bin/python runscripts/run_colony_ray.py \\
        --sim-data-path out/kb/simData.cPickle \\
        --target-doublings 1 --max-duration 3300

Usage (cluster):
    .venv/bin/python runscripts/run_colony_ray.py \\
        --sim-data-path s3://.../simData.cPickle \\
        --target-doublings 2 --max-duration 6000 \\
        --ray-address ray://head:10001
"""
import os

# Pin numerics single-threaded BEFORE any numpy/scipy/numba/ray
# import. Inherited by the actor process when ``ray start`` was given
# the same env; pinning here covers the local-runtime case.
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
import time

import ray


@ray.remote
def run_colony_actor(sim_data_path, target_doublings, max_duration,
                     base_seed, config_path):
    """Actor entry: build and run the colony Composite.

    Mirrors ``runscripts/run_colony.main`` body exactly — same code
    path, just executed inside a Ray actor process so we can later
    schedule M of these across a cluster (``ray.get([... .remote(s)
    for s in seeds])``).
    """
    # Re-pin in the actor for safety; ``ray start`` doesn't always
    # propagate driver env.
    for k in ('OMP_NUM_THREADS', 'MKL_NUM_THREADS', 'OPENBLAS_NUM_THREADS',
              'NUMBA_NUM_THREADS', 'NUMEXPR_NUM_THREADS',
              'VECLIB_MAXIMUM_THREADS', 'POLARS_MAX_THREADS'):
        os.environ.setdefault(k, '1')
    try:
        from threadpoolctl import threadpool_limits as _tpl
        _tpl(limits=1)
    except ImportError:
        pass

    from process_bigraph import Composite, allocate_core
    from ecoli.library.bigraph_types import ECOLI_TYPES
    from ecoli.library.sim_data import LoadSimData
    from ecoli.composites.ecoli_composite import build_ecoli_document

    # Same sim_config preprocessing as run_colony.py
    from configs import CONFIG_DIR_PATH
    from ecoli.experiments.ecoli_master_sim import EcoliSim
    cfg_path = config_path or os.path.join(CONFIG_DIR_PATH, 'default.json')
    sim = EcoliSim.from_file(cfg_path)
    sim.processes = sim._retrieve_processes(
        sim.processes, sim.add_processes,
        sim.exclude_processes, sim.swap_processes)
    sim.topology = sim._retrieve_topology(
        sim.topology, sim.processes, sim.swap_processes, sim.log_updates)
    sim.process_configs = sim._retrieve_process_configs(
        sim.process_configs, sim.processes)
    sim_config = dict(sim.config)
    sim_config['sim_data_path'] = sim_data_path
    sim_config['seed'] = base_seed
    sim_config['agent_id'] = '0'
    sim_config['divide'] = True

    core = allocate_core()
    core.register_types(ECOLI_TYPES)
    lsd = LoadSimData(**{**sim_config, 'seed': base_seed})

    state = build_ecoli_document(core, sim_config, load_sim_data=lsd)
    composite = Composite(
        {'schema': {}, 'state': state, 'run_steps_on_init': True},
        core=core,
    )

    t0 = time.perf_counter()
    composite.run(float(max_duration))
    wall = time.perf_counter() - t0

    final_agents = composite.state.get('agents', {})
    return {
        'wall_s': wall,
        'sim_time': float(composite.state.get('global_time', 0.0)),
        'final_cells': len(final_agents),
        'ids': sorted(final_agents.keys()),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sim-data-path', required=True)
    ap.add_argument('--config', default=None)
    ap.add_argument('--target-doublings', type=int, default=1)
    ap.add_argument('--base-seed', type=int, default=0)
    ap.add_argument('--max-duration', type=float, default=3300.0)
    ap.add_argument('--ray-address', default=None)
    ap.add_argument('--num-cpus', type=int, default=None,
                    help='Local Ray runtime CPU cap (ignored for cluster).')
    args = ap.parse_args()

    ray_init_kwargs = {'log_to_driver': True}
    if args.ray_address:
        ray_init_kwargs['address'] = args.ray_address
    if args.num_cpus is not None:
        ray_init_kwargs['num_cpus'] = args.num_cpus
    ray.init(**ray_init_kwargs)
    print(f'[ray-colony] ray runtime up '
          f'(address={args.ray_address or "local"})', flush=True)

    sim_data_path = os.path.abspath(args.sim_data_path)
    target_n = 2 ** args.target_doublings
    print(f'[ray-colony] target: 1 -> {target_n} cells '
          f'({args.target_doublings} doublings), '
          f'max_duration={args.max_duration:.0f}s sim time', flush=True)

    t0 = time.perf_counter()
    fut = run_colony_actor.remote(
        sim_data_path=sim_data_path,
        target_doublings=args.target_doublings,
        max_duration=args.max_duration,
        base_seed=args.base_seed,
        config_path=args.config,
    )
    result = ray.get(fut)
    driver_wall = time.perf_counter() - t0

    print(f'[ray-colony] done. driver_wall={driver_wall:.1f}s '
          f'actor_wall={result["wall_s"]:.1f}s '
          f'sim_time={result["sim_time"]:.1f} '
          f'final_cells={result["final_cells"]} '
          f'ids={result["ids"]}', flush=True)

    ray.shutdown()


if __name__ == '__main__':
    main()
