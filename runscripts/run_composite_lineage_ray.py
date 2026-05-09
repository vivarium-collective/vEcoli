"""Run composite_lineage across N seeds in parallel via Ray actors.

Mirrors run_composite_lineage_mp.py but uses ``@ray.remote`` actors
instead of multiprocessing.Pool. Each actor owns one lineage_seed,
runs all N generations sequentially via the
:py:meth:`~ecoli.experiments.ecoli_master_sim.EcoliSim._run_composite_lineage`
managed loop (which builds each per-gen cell via
:py:class:`~ecoli.composites.ecoli_cell_process.EcoliCellProcess`).

Key differences from MP:
  - Actors live in their own Python processes (just like MP workers)
    but are scheduled by Ray. Single-machine for testing; the same
    runner works on a Ray cluster (EC2 / Kubernetes) without code
    changes.
  - sim_data is loaded ONCE in the driver, placed in Ray's object
    store via ``ray.put``, and each actor pulls it from the store.
    Actor process inherits the deserialized object on first
    ``ray.get`` (zero-copy on same node, otherwise one network
    transfer).
  - On a multi-node cluster, actors get scheduled to nodes; one
    sim_data copy per node lives in shared memory.

Bit parity vs v1 / v2-pergen / single-process composite_lineage by
construction — every actor's per-gen build runs the exact same
``EcoliCellProcess.__init__`` code path.

Usage:
    uv run python runscripts/run_composite_lineage_ray.py \\
        --config configs/composites/lineage_2g_local.json \\
        --sim_data_path out/comparison_10s_16g_v2_local/variant_sim_data/0.cPickle \\
        --out_dir out/lineage_ray_test \\
        --n_seeds 2 --base_seed 0 --generations 2 --max_duration 2700
"""
import argparse
import os
import sys
import time

import ray


@ray.remote
class LineageActor:
    """One actor = one lineage. Holds the cell-line state (current
    cell's Composite, current generation count) across all gens of
    the lineage. Re-instantiates the cell at each gen boundary via
    EcoliCellProcess (managed loop in EcoliSim._run_composite_lineage).
    """

    def __init__(self, sim_data, sim_data_path):
        # sim_data is the deserialized SimulationDataEcoli pulled
        # from Ray's object store. Keep a handle so the EcoliSim
        # below can pass it through into LoadSimData via the
        # ``sim_data=`` kwarg, skipping the per-gen pickle reload.
        self._sim_data = sim_data
        self._sim_data_path = sim_data_path

    def run_lineage(self, config_path, out_dir, out_uri, lineage_seed,
                    generations, max_duration):
        """Run a full N-generation lineage for one ``lineage_seed``."""
        # Polars on every cell for safety (oversubscription on
        # multi-actor boxes).
        os.environ.setdefault('POLARS_MAX_THREADS', '1')

        from ecoli.experiments.ecoli_master_sim import EcoliSim

        sim = EcoliSim.from_file(config_path)
        sim.config['sim_data_path'] = self._sim_data_path
        sim.config['lineage_seed'] = lineage_seed
        sim.config['seed'] = lineage_seed
        sim.config['agent_id'] = '0'
        sim.config['generations'] = generations
        sim.config['max_duration'] = max_duration
        # Prefer out_uri (cloud) when set; ParquetEmitter routes via
        # fsspec. Falls back to local out_dir for tests.
        if out_uri:
            sim.config['emitter_arg'] = {
                'out_uri': out_uri, 'threaded': False}
        else:
            sim.config['emitter_arg'] = {
                'out_dir': out_dir, 'threaded': False}
        sim.config['daughter_outdir'] = None
        # Inject the actor's pre-loaded sim_data so the per-gen
        # LoadSimData wrappers built inside EcoliCellProcess.initialize
        # skip the pickle load.
        sim._preloaded_sim_data = self._sim_data
        t0 = time.time()
        sim.run()
        return lineage_seed, time.time() - t0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    parser.add_argument('--sim_data_path', required=True)
    parser.add_argument('--out_dir', default=None,
                        help='local output directory')
    parser.add_argument('--out_uri', default=None,
                        help='cloud URI (s3://, gs://) for parquet output')
    parser.add_argument('--n_seeds', type=int, default=10)
    parser.add_argument('--base_seed', type=int, default=0)
    parser.add_argument('--generations', type=int, default=16)
    parser.add_argument('--max_duration', type=float, default=3000.0)
    parser.add_argument('--ray_address', default=None,
                        help='Existing Ray cluster address (default: '
                             'spawn local). E.g. ray://head:10001 or "auto"')
    args = parser.parse_args()
    if not args.out_dir and not args.out_uri:
        parser.error("must give one of --out_dir or --out_uri")

    if not args.sim_data_path.startswith(
            ('s3://', 'gs://')) and not os.path.isfile(args.sim_data_path):
        raise SystemExit(f"sim_data_path missing: {args.sim_data_path}")

    # Driver-side: load sim_data once, ray.put into the object
    # store. All actors pull from the store; on a multi-node
    # cluster, this gives one copy per node in shared memory
    # (no network re-transfer per actor).
    print(f"[ray] Loading sim_data once in driver "
          f"({args.sim_data_path})...", flush=True)
    t0 = time.time()
    from ecoli.library.sim_data import LoadSimData
    base = LoadSimData(
        sim_data_path=args.sim_data_path, seed=args.base_seed)
    print(f"[ray]   Loaded in {time.time()-t0:.2f}s. "
          f"Initializing Ray...", flush=True)

    if args.ray_address:
        ray.init(address=args.ray_address)
    else:
        ray.init()

    sd_ref = ray.put(base.sim_data)
    print(f"[ray]   sim_data placed in object store. "
          f"Spawning {args.n_seeds} lineage actors...", flush=True)

    sd_path = (args.sim_data_path
               if args.sim_data_path.startswith(('s3://', 'gs://'))
               else os.path.abspath(args.sim_data_path))
    actors = [
        LineageActor.remote(sd_ref, sd_path)
        for _ in range(args.n_seeds)
    ]

    seeds = list(range(args.base_seed,
                       args.base_seed + args.n_seeds))
    config_abs = os.path.abspath(args.config)
    out_dir_abs = (os.path.abspath(args.out_dir)
                   if args.out_dir else None)
    futures = [
        actor.run_lineage.remote(
            config_abs, out_dir_abs, args.out_uri, seed,
            args.generations, args.max_duration)
        for actor, seed in zip(actors, seeds)
    ]

    t_start = time.time()
    results = ray.get(futures)
    elapsed = time.time() - t_start
    print(f"\n[ray] All {args.n_seeds} lineages done in "
          f"{elapsed:.1f}s wall.", flush=True)
    for seed, dt in results:
        print(f"  seed={seed}: {dt:.1f}s", flush=True)

    ray.shutdown()


if __name__ == '__main__':
    main()
