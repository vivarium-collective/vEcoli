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
import os

# Pin numerics to single-threaded BEFORE any numpy/scipy/numba/BLAS
# import. Threading config is read at IMPORT time; setting these
# inside workers after fork is too late because numpy has already
# bound to the threadpool. With N MP workers each spawning N threads,
# total threads = N×M oversubscribes M-core boxes; empirically N=8
# went 104s → 59s (-43%) when we set these (2026-05-10 scaling test).
# ``setdefault`` so user can override on the command line.
os.environ.setdefault('POLARS_MAX_THREADS', '1')
os.environ.setdefault('OMP_NUM_THREADS', '1')
os.environ.setdefault('MKL_NUM_THREADS', '1')
os.environ.setdefault('OPENBLAS_NUM_THREADS', '1')
os.environ.setdefault('NUMBA_NUM_THREADS', '1')
os.environ.setdefault('NUMEXPR_NUM_THREADS', '1')
os.environ.setdefault('VECLIB_MAXIMUM_THREADS', '1')
# scipy's bundled openblas loads before env vars are visible; cap
# all already-loaded threadpools at runtime.
try:
    from threadpoolctl import threadpool_limits as _tpl
    _tpl(limits=1)
except ImportError:
    pass

import argparse
import multiprocessing
import sys
import time

# These are set in the parent before fork so workers inherit them
# via copy-on-write. Workers read these as module-level globals.
def _resolve_inherited_config(config_path, cfg):
    """Walk ``inherit_from`` chain and merge configs, child wins.

    Mirrors ``EcoliSim.from_file``'s loading semantics:
      1. ``configs/default.json`` is the implicit ultimate base (auto-
         loaded for every config; defaults like ``max_duration``,
         ``time_step`` live here).
      2. Configs in ``inherit_from`` are merged on top (parent →
         child order); each parent is itself recursively resolved.
      3. The child config's own keys override.

    No EcoliSim import — keeps this driver-side helper light.
    """
    import json as _json
    cfg_dir = os.path.dirname(os.path.abspath(config_path))
    merged = {}

    # 1. default.json — the implicit base every vEcoli config sits on.
    # Skip if config_path IS default.json (avoid infinite recursion).
    cfg_basename = os.path.basename(os.path.abspath(config_path))
    if cfg_basename != 'default.json':
        default_candidates = [
            os.path.join(cfg_dir, 'default.json'),
            os.path.join(cfg_dir, '..', 'configs', 'default.json'),
        ]
        for cand in default_candidates:
            if os.path.isfile(cand):
                with open(cand) as f:
                    merged.update(_json.load(f))
                break

    # 2. Explicit inherit_from chain
    for parent_rel in cfg.get('inherit_from') or []:
        candidates = [
            os.path.join(cfg_dir, parent_rel),
            os.path.join(cfg_dir, '..', 'configs', parent_rel),
            os.path.join(cfg_dir, parent_rel + '.json'),
        ]
        for cand in candidates:
            if os.path.isfile(cand):
                with open(cand) as f:
                    parent_cfg = _json.load(f)
                merged.update(_resolve_inherited_config(cand, parent_cfg))
                break

    # 3. Child wins
    merged.update(cfg)
    return merged

_PRELOADED_SIM_DATA = None
_WORKER_CONFIG_PATH = None
_WORKER_OUT_DIR = None
_WORKER_OUT_URI = None
_WORKER_GENERATIONS = None
_WORKER_MAX_DURATION = None
_WORKER_SIM_DATA_PATH = None
_WORKER_EXPERIMENT_ID = None


def _run_one_lineage(seed):
    """Worker entry: run a full lineage for one ``lineage_seed``.

    Reads the parent-loaded sim_data from the module-level
    ``_PRELOADED_SIM_DATA`` global (inherited via fork).

    Threading is pinned at module-import time at the top of this file
    (must be set before numpy/BLAS/numba load). See comment there.
    """
    from ecoli.experiments.ecoli_master_sim import EcoliSim

    sim = EcoliSim.from_file(_WORKER_CONFIG_PATH)
    sim.config['sim_data_path'] = _WORKER_SIM_DATA_PATH
    sim.config['lineage_seed'] = seed
    sim.config['seed'] = seed
    sim.config['agent_id'] = '0'
    sim.config['generations'] = _WORKER_GENERATIONS
    sim.config['max_duration'] = _WORKER_MAX_DURATION
    if _WORKER_EXPERIMENT_ID:
        sim.config['experiment_id'] = _WORKER_EXPERIMENT_ID
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
    return seed, t0, time.time()


def main():
    """Drive a composite_lineage MP run from a config file alone.

    Every per-run setting (sim_data_path, out_uri, n_seeds, generations,
    max_duration, base_seed) lives in the config — the only optional
    flag is ``--n_workers`` to override pool size when you want fewer
    workers than seeds (otherwise defaults to one worker per seed).
    """
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    parser.add_argument('--n_workers', type=int, default=None,
                        help='Pool size; defaults to n_init_sims from config')
    parser.add_argument('--experiment-id', dest='experiment_id', default=None,
                        help='Override config experiment_id. Used by '
                             'vecoli_aws.sh to inject auto-generated unique '
                             'IDs per launch.')
    args = parser.parse_args()

    # Read every other knob from the config.
    import json
    with open(args.config) as f:
        cfg = json.load(f)
    # Inherit-from is a vEcoli convention; the in-memory EcoliSim handles
    # it, but here we just need a few top-level values. Walk inherits
    # manually so the runner doesn't depend on importing EcoliSim.
    cfg = _resolve_inherited_config(args.config, cfg)
    if args.experiment_id:
        cfg['experiment_id'] = args.experiment_id

    sim_data_path = cfg.get('sim_data_path')
    if not sim_data_path:
        parser.error("config must set 'sim_data_path' "
                     "(local file or s3://… / gs://…)")
    emitter_arg = cfg.get('emitter_arg', {}) or {}
    out_uri = emitter_arg.get('out_uri')
    out_dir = emitter_arg.get('out_dir')
    if not out_uri and not out_dir:
        parser.error("config emitter_arg must set 'out_uri' or 'out_dir'")
    n_seeds = int(cfg.get('n_init_sims') or 1)
    base_seed = int(cfg.get('lineage_seed') or 0)
    generations = int(cfg.get('generations') or 1)
    max_duration = float(cfg.get('max_duration') or 10800.0)

    if not sim_data_path.startswith(('s3://', 'gs://')) \
            and not os.path.isfile(sim_data_path):
        print(f"sim_data_path not found: {sim_data_path}", file=sys.stderr)
        sys.exit(1)

    n_workers = args.n_workers or n_seeds

    # Pre-load sim_data in parent — workers inherit via fork (COW).
    print(f"[mp] Loading sim_data once in parent ({sim_data_path})...",
          flush=True)
    t0 = time.time()
    from ecoli.library.sim_data import LoadSimData
    base = LoadSimData(sim_data_path=sim_data_path, seed=base_seed)
    global _PRELOADED_SIM_DATA, _WORKER_CONFIG_PATH, _WORKER_OUT_DIR
    global _WORKER_OUT_URI, _WORKER_GENERATIONS, _WORKER_MAX_DURATION
    global _WORKER_SIM_DATA_PATH, _WORKER_EXPERIMENT_ID
    _PRELOADED_SIM_DATA = base.sim_data
    _WORKER_CONFIG_PATH = os.path.abspath(args.config)
    _WORKER_OUT_DIR = os.path.abspath(out_dir) if out_dir else None
    _WORKER_OUT_URI = out_uri
    _WORKER_GENERATIONS = generations
    _WORKER_MAX_DURATION = max_duration
    _WORKER_SIM_DATA_PATH = (
        sim_data_path
        if sim_data_path.startswith(('s3://', 'gs://'))
        else os.path.abspath(sim_data_path))
    _WORKER_EXPERIMENT_ID = args.experiment_id
    print(f"[mp]   Loaded in {time.time()-t0:.2f}s. "
          f"Spawning {n_workers} workers for {n_seeds} seeds "
          f"(generations={generations}, max_duration={max_duration})...",
          flush=True)

    seeds = list(range(base_seed, base_seed + n_seeds))

    # Use 'fork' explicitly — Linux default; required for the
    # _PRELOADED_SIM_DATA copy-on-write inheritance to work. 'spawn'
    # would force re-pickling.
    ctx = multiprocessing.get_context('fork')
    t_start = time.time()
    with ctx.Pool(processes=n_workers) as pool:
        results = pool.map(_run_one_lineage, seeds)
    t_end = time.time()
    elapsed = t_end - t_start
    print(f"\n[mp] All {n_seeds} lineages done in "
          f"{elapsed:.1f}s wall.", flush=True)
    for seed, t0_s, t1_s in results:
        print(f"  seed={seed}: {t1_s-t0_s:.1f}s", flush=True)

    # Emit a Nextflow-shaped synthetic trace + cost_meta sidecar so
    # runscripts/v1_v2_report.py can include this run in the
    # workflow-wall-clock + cost tables.
    if out_uri:
        exp_id = cfg.get('experiment_id', 'mp_run')
        from runscripts.synthetic_trace import emit_synthetic_trace
        emit_synthetic_trace(
            out_uri=out_uri,
            exp_id=exp_id,
            workflow_t_start=t_start,
            workflow_t_end=t_end,
            per_seed=results,
            deploy_meta={
                'deploy_mode': 'mp_single_node',
                'instance': os.environ.get('VECOLI_INSTANCE', 'c7g.metal'),
                'n_instances': 1,
                'on_demand': True,
            })


if __name__ == '__main__':
    main()
