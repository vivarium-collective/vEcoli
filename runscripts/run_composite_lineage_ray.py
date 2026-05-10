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

# Re-use the inherit-walking helper from the MP runner so config-driven
# behavior matches between the two engines.
from runscripts.run_composite_lineage_mp import _resolve_inherited_config


def _patch_s3fs_skip_create_bucket() -> None:
    """Monkey-patch ``s3fs.S3FileSystem._mkdir`` to never call
    ``CreateBucket``.

    Why: ``parquet_emitter.finalize`` calls
    ``self.filesystem.makedirs(...)`` which on S3 cascades into
    ``s3fs._mkdir``, which calls ``_exists(bucket)`` to check if the
    bucket exists, and falls back to ``CreateBucket`` if both
    ``HeadBucket`` and ``GetBucketLocation`` silently fail.

    On GovCloud with our role's perms, ``HeadBucket`` /
    ``GetBucketLocation`` apparently return errors that aiobotocore
    treats as "bucket doesn't exist", so s3fs tries ``CreateBucket``
    which fails with ``InvalidToken`` — terminating every actor.

    But in our deployment ALL S3 buckets pre-exist (the SMS API VPC
    shared bucket). We never need ``CreateBucket``. Skipping the
    call entirely is semantically correct: makedirs on S3 has no
    real meaning (S3 is flat-keys), and the actual parquet write
    proceeds without parent dirs.

    This patch is loaded ONLY by the composite_lineage_ray runner;
    it does NOT touch ``parquet_emitter.py`` (shared with v1
    nextflow) and does NOT affect any other code path. The Batch
    nextflow path runs in a different Python process with no patch.
    """
    try:
        import s3fs
    except ImportError:
        return  # No s3fs installed; nothing to patch.

    async def _mkdir_no_create_bucket(self, path, acl=False,
                                       create_parents=True, **kwargs):
        # All buckets in our deployment pre-exist. S3 has no real
        # directories — writing the key directly is sufficient.
        # Returning without calling _call_s3 means CreateBucket
        # never runs, even when _exists() falsely reports the bucket
        # missing (the GovCloud + aiobotocore quirk).
        return

    s3fs.S3FileSystem._mkdir = _mkdir_no_create_bucket
    print(
        "[ray] patched s3fs.S3FileSystem._mkdir to skip CreateBucket "
        "(see _patch_s3fs_skip_create_bucket docstring)",
        flush=True,
    )


# Patch BEFORE any import that might construct an S3FileSystem.
_patch_s3fs_skip_create_bucket()


@ray.remote
class LineageActor:
    """One actor = one lineage. Holds the cell-line state (current
    cell's Composite, current generation count) across all gens of
    the lineage. Re-instantiates the cell at each gen boundary via
    EcoliCellProcess (managed loop in EcoliSim._run_composite_lineage).
    """

    def __init__(self, sim_data, sim_data_path):
        # Re-apply s3fs patch inside the actor process: Ray spawns
        # actors as separate processes that pickle the class but
        # don't re-execute module-level code, so the module-level
        # _patch_s3fs_skip_create_bucket() call doesn't propagate
        # here automatically.
        _patch_s3fs_skip_create_bucket()

        # Polars / object-store Rust SDK reads AWS_REGION at the
        # process level, NOT from boto3's config or storage_options.
        # Ray actor processes are spawned by ``ray start`` on cluster
        # workers and do NOT inherit env from the driver. Without
        # this, polars' parquet S3 PUT defaults to the commercial
        # us-east-1 endpoint and gets ``InvalidToken`` from a
        # GovCloud bucket. setdefault preserves any explicit override.
        os.environ.setdefault("AWS_REGION", "us-gov-west-1")
        os.environ.setdefault("AWS_DEFAULT_REGION", "us-gov-west-1")

        # Ray's object store uses zero-copy plasma shared memory for
        # numpy arrays, which deserializes them as READ-ONLY in actor
        # processes. ``stochastic_arrow.evolve`` (called by
        # complexation.calculate_request) takes Cython memoryviews
        # which REQUIRE writable arrays — hits ``ValueError: buffer
        # source array is read-only`` on the first complexation tick
        # otherwise. Deep-copying once at actor startup gives this
        # actor its own writable instance. Memory cost is ~one
        # sim_data per actor (hundreds of MB) — fine on m5.4xlarge
        # workers with 64 GB RAM and 10 lineages spread across 5
        # workers (2 actors / worker).
        import copy
        self._sim_data = copy.deepcopy(sim_data)
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
    """Drive a composite_lineage Ray run from a config file alone.

    Every per-run setting (sim_data_path, out_uri, n_seeds, generations,
    max_duration, base_seed) lives in the config — the only optional
    flag is ``--ray_address`` to attach to an existing cluster.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    parser.add_argument('--ray_address', default=None,
                        help='Existing Ray cluster address (default: '
                             'spawn local). E.g. ray://head:10001 or "auto"')
    args = parser.parse_args()

    # Read every other knob from the config (with inherited parents
    # walked manually so we don't import EcoliSim here).
    import json
    with open(args.config) as f:
        cfg = json.load(f)
    cfg = _resolve_inherited_config(args.config, cfg)

    sim_data_path = cfg.get('sim_data_path')
    if not sim_data_path:
        parser.error("config must set 'sim_data_path'")
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
        raise SystemExit(f"sim_data_path missing: {sim_data_path}")

    # Driver-side: load sim_data once, ray.put into the object store.
    print(f"[ray] Loading sim_data once in driver ({sim_data_path})...",
          flush=True)
    t0 = time.time()
    from ecoli.library.sim_data import LoadSimData
    base = LoadSimData(sim_data_path=sim_data_path, seed=base_seed)
    print(f"[ray]   Loaded in {time.time()-t0:.2f}s. "
          f"Initializing Ray...", flush=True)

    if args.ray_address:
        ray.init(address=args.ray_address)
    else:
        ray.init()

    sd_ref = ray.put(base.sim_data)
    print(f"[ray]   sim_data placed in object store. "
          f"Spawning {n_seeds} lineage actors "
          f"(generations={generations}, max_duration={max_duration})...",
          flush=True)

    sd_path = (sim_data_path
               if sim_data_path.startswith(('s3://', 'gs://'))
               else os.path.abspath(sim_data_path))
    actors = [LineageActor.remote(sd_ref, sd_path) for _ in range(n_seeds)]

    seeds = list(range(base_seed, base_seed + n_seeds))
    config_abs = os.path.abspath(args.config)
    out_dir_abs = (os.path.abspath(out_dir) if out_dir else None)
    futures = [
        actor.run_lineage.remote(
            config_abs, out_dir_abs, out_uri, seed,
            generations, max_duration)
        for actor, seed in zip(actors, seeds)
    ]

    t_start = time.time()
    results = ray.get(futures)
    t_end = time.time()
    elapsed = t_end - t_start
    print(f"\n[ray] All {n_seeds} lineages done in "
          f"{elapsed:.1f}s wall.", flush=True)

    per_seed_norm = []
    for seed, dt in results:
        per_seed_norm.append((seed, t_start, t_start + dt))
        print(f"  seed={seed}: {dt:.1f}s", flush=True)

    try:
        nodes = ray.nodes()
        n_workers = max(1, len(nodes) - 1)
    except Exception:
        n_workers = n_seeds

    ray.shutdown()

    if out_uri:
        exp_id = cfg.get('experiment_id', 'ray_run')
        from runscripts.synthetic_trace import emit_synthetic_trace
        emit_synthetic_trace(
            out_uri=out_uri,
            exp_id=exp_id,
            workflow_t_start=t_start,
            workflow_t_end=t_end,
            per_seed=per_seed_norm,
            deploy_meta={
                'deploy_mode': 'ray_cluster',
                'head_instance': os.environ.get(
                    'VECOLI_HEAD_INSTANCE', 't4g.large'),
                'worker_instance': os.environ.get(
                    'VECOLI_WORKER_INSTANCE', 'c7g.metal'),
                'n_workers': n_workers,
                'head_on_demand': True,
                'worker_on_demand': True,
            })


if __name__ == '__main__':
    main()
