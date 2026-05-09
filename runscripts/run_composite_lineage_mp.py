"""Run composite_lineage across N seeds in parallel via multiprocessing.

Loads the sim_data pickle ONCE in the parent process. Worker processes
are spawned via Linux ``fork`` (the multiprocessing default) so they
inherit the loaded pickle through copy-on-write — no repeated reads
from disk / S3. Each worker runs a full lineage (build -> tick to
division -> rebuild gen N+1 -> ... ) for one ``lineage_seed``,
emitting parquet to its own ``lineage_seed=K`` partition.

This is the multiprocessing equivalent of the per-gen Nextflow path:
- Per-gen Nextflow: 16 generations of 10 seeds = 160 K8s pods,
  each with its own Python interpreter + pickle reload + JIT cache.
- This script: 10 worker processes, each loads sim_data once (well,
  inherits from parent), runs all 16 gens in-process. Same parquet
  outputs.

Usage:
    uv run python runscripts/run_composite_lineage_mp.py \\
        --config configs/composites/lineage_2g_local.json \\
        --sim_data_path out/comparison_10s_16g_v2_local/variant_sim_data/0.cPickle \\
        --out_dir out/lineage_mp_test \\
        --n_seeds 10 --base_seed 0 --generations 16 --max_duration 3000

Bit parity with v1 / v2-pergen / single-process composite_lineage by
construction — every worker uses the same code path as
``EcoliSim._run_composite_lineage`` for its assigned seed.
"""
import argparse
import multiprocessing
import os
import sys
import time

# These are set in the parent before fork so workers inherit them
# via copy-on-write. Workers read these as module-level globals.
_PRELOADED_SIM_DATA = None
_WORKER_CONFIG_PATH = None
_WORKER_OUT_DIR = None
_WORKER_OUT_URI = None
_WORKER_GENERATIONS = None
_WORKER_MAX_DURATION = None
_WORKER_SIM_DATA_PATH = None


def _run_one_lineage(seed):
    """Worker entry: run a full lineage for one ``lineage_seed``.

    Reads the parent-loaded sim_data from the module-level
    ``_PRELOADED_SIM_DATA`` global (inherited via fork). Pinning
    POLARS_MAX_THREADS=1 keeps polars from oversubscribing on
    multi-worker boxes.
    """
    os.environ.setdefault('POLARS_MAX_THREADS', '1')
    from ecoli.experiments.ecoli_master_sim import EcoliSim

    sim = EcoliSim.from_file(_WORKER_CONFIG_PATH)
    sim.config['sim_data_path'] = _WORKER_SIM_DATA_PATH
    sim.config['lineage_seed'] = seed
    sim.config['seed'] = seed
    sim.config['agent_id'] = '0'
    sim.config['generations'] = _WORKER_GENERATIONS
    sim.config['max_duration'] = _WORKER_MAX_DURATION
    # ParquetEmitter prefers out_uri when set (fsspec-aware S3/GCS),
    # falls back to out_dir for local paths.
    if _WORKER_OUT_URI:
        sim.config['emitter_arg'] = {
            'out_uri': _WORKER_OUT_URI, 'threaded': False}
    else:
        sim.config['emitter_arg'] = {
            'out_dir': _WORKER_OUT_DIR, 'threaded': False}
    sim.config['daughter_outdir'] = None
    # Inject parent-loaded sim_data so the worker doesn't re-pickle
    # (saves 5-30s per worker on cold start).
    sim._preloaded_sim_data = _PRELOADED_SIM_DATA
    t0 = time.time()
    sim.run()
    return seed, time.time() - t0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    parser.add_argument('--sim_data_path', required=True)
    # Output destination — give exactly one. ``--out_uri`` for cloud
    # (s3:// or gs://); ``--out_dir`` for local. ParquetEmitter
    # routes through fsspec when out_uri is set.
    parser.add_argument('--out_dir', default=None,
                        help='local output directory')
    parser.add_argument('--out_uri', default=None,
                        help='cloud URI (s3:// / gs://) for parquet output')
    parser.add_argument('--n_seeds', type=int, default=10)
    parser.add_argument('--base_seed', type=int, default=0)
    parser.add_argument('--generations', type=int, default=16)
    parser.add_argument('--max_duration', type=float, default=3000.0)
    parser.add_argument('--n_workers', type=int, default=None,
                        help='Default: --n_seeds')
    args = parser.parse_args()
    if not args.out_dir and not args.out_uri:
        parser.error("must give one of --out_dir or --out_uri")

    if not args.sim_data_path.startswith(
            ('s3://', 'gs://')) and not os.path.isfile(args.sim_data_path):
        print(f"sim_data_path not found: {args.sim_data_path}",
              file=sys.stderr)
        sys.exit(1)

    n_workers = args.n_workers or args.n_seeds

    # Pre-load sim_data in parent — workers inherit via fork (COW).
    print(f"[mp] Loading sim_data once in parent "
          f"({args.sim_data_path})...", flush=True)
    t0 = time.time()
    from ecoli.library.sim_data import LoadSimData
    base = LoadSimData(sim_data_path=args.sim_data_path, seed=args.base_seed)
    global _PRELOADED_SIM_DATA, _WORKER_CONFIG_PATH, _WORKER_OUT_DIR
    global _WORKER_OUT_URI, _WORKER_GENERATIONS, _WORKER_MAX_DURATION
    global _WORKER_SIM_DATA_PATH
    _PRELOADED_SIM_DATA = base.sim_data
    _WORKER_CONFIG_PATH = os.path.abspath(args.config)
    _WORKER_OUT_DIR = os.path.abspath(args.out_dir) if args.out_dir else None
    _WORKER_OUT_URI = args.out_uri  # cloud URI as-is, no abspath
    _WORKER_GENERATIONS = args.generations
    _WORKER_MAX_DURATION = args.max_duration
    _WORKER_SIM_DATA_PATH = (
        args.sim_data_path
        if args.sim_data_path.startswith(('s3://', 'gs://'))
        else os.path.abspath(args.sim_data_path))
    print(f"[mp]   Loaded in {time.time()-t0:.2f}s. "
          f"Spawning {n_workers} workers for "
          f"{args.n_seeds} seeds...", flush=True)

    seeds = list(range(args.base_seed,
                       args.base_seed + args.n_seeds))

    # Use 'fork' explicitly — Linux default; required for the
    # _PRELOADED_SIM_DATA copy-on-write inheritance to work. 'spawn'
    # would force re-pickling.
    ctx = multiprocessing.get_context('fork')
    t_start = time.time()
    with ctx.Pool(processes=n_workers) as pool:
        results = pool.map(_run_one_lineage, seeds)
    elapsed = time.time() - t_start
    print(f"\n[mp] All {args.n_seeds} lineages done in "
          f"{elapsed:.1f}s wall.", flush=True)
    for seed, dt in results:
        print(f"  seed={seed}: {dt:.1f}s", flush=True)


if __name__ == '__main__':
    main()
