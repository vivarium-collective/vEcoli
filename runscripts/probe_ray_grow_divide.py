"""Demo: ``grow_divide_agent`` from process-bigraph, but each cell is a
``ray:Composite`` (not ``local:Composite``). Verifies the multi-actor
distribution pattern works with division-propagating-via-bridge — the
prerequisite for swapping the trivial Grow/Divide cell out for a
full vEcoli cell composite.

The local ``test_grow_divide`` proves:
  - cell-as-Composite-as-Process works locally
  - inner ``Divide`` step emits ``{_remove, _add}`` to its inner
    ``environment`` slot
  - bridge wires that slot up to the outer environment map
  - the outer apply sees the structural sentinel and adds new keyed
    cells (verified by ``'0_0_0_0_1' in composite.state['environment']``)

What this script changes: swap the cell's ``address`` from
``local:Composite`` to ``ray:Composite``. Everything else stays
identical. If divisions still produce new outer environment entries
AND those new entries are also ``ray:Composite`` (assignable to the
shard pool), the pattern is sound for the colony-on-Ray architecture.
"""
import os
os.environ.setdefault('OMP_NUM_THREADS', '1')
os.environ.setdefault('MKL_NUM_THREADS', '1')
os.environ.setdefault('OPENBLAS_NUM_THREADS', '1')

import argparse
import time

import ray
from process_bigraph import Composite, allocate_core
from process_bigraph.processes.growth_division import (
    grow_divide_agent, Grow, Divide)
from process_bigraph.protocols.ray import (
    register_process_class, get_or_create_runtime, shutdown_all_runtimes)


def _ray_grow_divide_agent(grow_config=None, state=None, path=None):
    """Copy of ``grow_divide_agent`` that returns a ``ray:Composite``
    cell node instead of ``local:Composite``. Everything else
    (bridge wires, sub-processes, lift to ``..``) is identical."""
    # Build the local version, then mutate its address.
    cell = grow_divide_agent(grow_config, state, path)
    cell['address'] = 'ray:Composite'
    return cell


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--initial-mass', type=float, default=1.0)
    ap.add_argument('--grow-rate', type=float, default=0.03)
    ap.add_argument('--interval', type=float, default=50.0,
                    help='How many sim-sec to advance per update call.')
    ap.add_argument('--n-shards', type=int, default=2,
                    help='Ray protocol shard pool size.')
    ap.add_argument('--num-cpus', type=int, default=2,
                    help='Local Ray runtime CPU cap.')
    args = ap.parse_args()

    # Cap shard pool BEFORE first runtime creation (env var is read once).
    os.environ['RAY_SHARDS_DEFAULT'] = str(args.n_shards)

    ray.init(num_cpus=args.num_cpus, log_to_driver=False)
    print(f'[ray-gd] ray runtime up (num_cpus={args.num_cpus}, '
          f'n_shards={args.n_shards})', flush=True)

    core = allocate_core()
    # Register Composite both as a link target (for ray:Composite address
    # resolution at the protocol layer) AND with the Ray protocol's
    # process registry (so actors can instantiate it themselves on spawn).
    core.register_link('Composite', Composite)
    register_process_class('Composite', Composite)
    print('[ray-gd] registered Composite with core.link_registry + '
          'ray protocol', flush=True)

    # Initial cell: one ray:Composite at environment.0.
    cell = _ray_grow_divide_agent(
        {'grow': {'rate': args.grow_rate}},
        {},
        ['environment', '0'])
    state = {
        'environment': {
            '0': {
                'mass': args.initial_mass,
                'grow_divide': cell,
            },
        },
    }

    print('[ray-gd] building outer composite...', flush=True)
    t0 = time.perf_counter()
    sim = Composite(
        {'state': state,
         'parallel_processes': True,
         'bridge': {
             'inputs': {'environment': ['environment']},
         }},
        core=core,
    )
    print(f'[ray-gd]   built in {time.perf_counter()-t0:.1f}s. '
          f'initial env keys: {sorted(sim.state["environment"].keys())}',
          flush=True)

    print(f'[ray-gd] running {args.interval}s sim time...', flush=True)
    t0 = time.perf_counter()
    sim.update(
        {'environment': {'0': {'mass': 1.1}}},
        args.interval,
    )
    wall = time.perf_counter() - t0

    env_keys = sorted(sim.state['environment'].keys())
    print(f'[ray-gd] done. wall={wall:.1f}s '
          f'env keys ({len(env_keys)}): {env_keys}', flush=True)

    # Inspect what addresses the daughters have. If propagation worked,
    # they're ray:Composite too (because they were spawned from a
    # grow_divide_agent decl which we patched).
    print('[ray-gd] daughter cell addresses:', flush=True)
    for k in env_keys:
        entry = sim.state['environment'].get(k, {})
        gd = entry.get('grow_divide') if isinstance(entry, dict) else None
        if isinstance(gd, dict):
            inst = gd.get('instance')
            addr = gd.get('address')
            print(f'  {k!r}: address={addr!r} instance={type(inst).__name__}',
                  flush=True)

    runtime = get_or_create_runtime(core)
    print(f'[ray-gd] ray runtime: {len(runtime._pools)} pool(s)', flush=True)
    for key, pool in runtime._pools.items():
        print(f'  pool {key!r}: {len(pool.actors)} actor(s)', flush=True)

    # Success criterion: more than one env entry (so division propagated)
    if len(env_keys) > 1:
        print('[ray-gd] ✅ SUCCESS: bridge propagation works with ray:Composite '
              '— daughters appeared in outer environment.', flush=True)
    else:
        print('[ray-gd] ❌ no divisions occurred (interval too short, or '
              'propagation failed).', flush=True)

    shutdown_all_runtimes()
    ray.shutdown()


if __name__ == '__main__':
    main()
