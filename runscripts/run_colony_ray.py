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


def _patch_s3fs_skip_create_bucket() -> None:
    """Monkey-patch ``s3fs.S3FileSystem._mkdir`` to never call
    ``CreateBucket``. See the long-form rationale in
    ``run_composite_lineage_ray._patch_s3fs_skip_create_bucket`` — same
    GovCloud + aiobotocore quirk applies to the colony parquet emitter
    path. All buckets in this deployment pre-exist, so makedirs-on-S3
    is a no-op and we can safely short-circuit ``_mkdir``.
    """
    try:
        import s3fs
    except ImportError:
        return

    async def _mkdir_no_create_bucket(self, path, acl=False,
                                       create_parents=True, **kwargs):
        return

    s3fs.S3FileSystem._mkdir = _mkdir_no_create_bucket


# Patch BEFORE any import that might construct an S3FileSystem.
_patch_s3fs_skip_create_bucket()


@ray.remote
def run_colony_actor(sim_data_path, target_doublings, max_duration,
                     base_seed, config_path,
                     experiment_id=None, out_uri=None):
    """Actor entry: build and run the colony Composite.

    Mirrors ``runscripts/run_colony.main`` body exactly — same code
    path, just executed inside a Ray actor process so we can later
    schedule M of these across a cluster (``ray.get([... .remote(s)
    for s in seeds])``).

    When ``out_uri`` is provided, one
    :py:class:`~ecoli.library.parquet_emitter.ParquetEmitter` is
    lazily constructed per ``agent_id`` (mother + each daughter as it
    appears via in-place division) and a history row is emitted for
    each surviving cell at every poll-step. The existing single-agent
    ParquetEmitter drops emits whose ``data['agents']`` map has >1
    entry, so the per-cell pool fans out each tick into N single-agent
    emit payloads, one per emitter. All emitters get finalized at run
    end so their pending buffer flushes to S3.
    """
    # Re-apply s3fs patch inside the actor: Ray spawns actors as
    # separate processes that don't re-execute module-level code, so
    # the module-level patch doesn't propagate automatically.
    _patch_s3fs_skip_create_bucket()

    # Polars / object-store Rust SDK reads AWS_REGION at the process
    # level, NOT from boto3's config or storage_options. Ray actor
    # processes are spawned by ``ray start`` on cluster workers and do
    # NOT inherit env from the driver. Without these, boto3/polars
    # default to us-east-1 and a HeadObject against a GovCloud bucket
    # returns 400 Bad Request. setdefault preserves any explicit
    # override (e.g. running this code outside GovCloud).
    os.environ.setdefault("AWS_REGION", "us-gov-west-1")
    os.environ.setdefault("AWS_DEFAULT_REGION", "us-gov-west-1")

    # Re-pin numerics in the actor for safety; ``ray start`` doesn't
    # always propagate driver env.
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

    # --- per-cell ParquetEmitter pool ---------------------------------
    # See class docstring above. ``agent_emitters`` is keyed by string
    # agent_id and grows lazily as new cells appear (mother + daughters).
    import numpy as np
    agent_emitters: dict = {}
    EMIT_KEYS = ('listeners', 'bulk', 'process_state')

    def _get_emitter(agent_id: str):
        em = agent_emitters.get(agent_id)
        if em is not None:
            return em
        from ecoli.library.parquet_emitter import ParquetEmitter
        em = ParquetEmitter({'out_uri': out_uri, 'threaded': False})
        # Configuration emit sets the agent's hive partition path
        # (experiment_id/variant=0/lineage_seed/generation/agent_id).
        # ``generation`` is derived from len(agent_id) so daughters
        # ('00', '01') land at generation=2, granddaughters at 3, etc.
        em.emit({
            'table': 'configuration',
            'data': {
                'metadata': {
                    'experiment_id': experiment_id or 'colony_run',
                    'variant': 0,
                    'lineage_seed': base_seed,
                    'agent_id': agent_id,
                    'initial_global_time': float(
                        composite.state.get('global_time', 0.0)),
                },
            },
        })
        agent_emitters[agent_id] = em
        return em

    def _emit_tick():
        """Emit one history row per surviving agent. No-op without out_uri."""
        if not out_uri:
            return
        global_t = float(composite.state.get('global_time', 0.0))
        agents = composite.state.get('agents', {})
        if not isinstance(agents, dict):
            return
        for agent_id, agent_state in agents.items():
            if not isinstance(agent_state, dict):
                continue
            subtree = {}
            for k in EMIT_KEYS:
                v = agent_state.get(k)
                if v is None:
                    continue
                # ``bulk`` is a structured numpy array; project counts
                # out to match the per-tick column shape v1 / lineage
                # parquet rows use (a plain Int64 list).
                if (k == 'bulk' and isinstance(v, np.ndarray)
                        and v.dtype.names
                        and 'count' in v.dtype.names):
                    v = np.asarray(v['count'], dtype=np.int64)
                subtree[k] = v
            if not subtree:
                continue
            em = _get_emitter(str(agent_id))
            em.emit({
                'table': 'history',
                'data': {
                    'agents': {str(agent_id): subtree},
                    'time': global_t,
                },
            })

    # --- run loop: tick-by-tick so we can emit each step --------------
    # Original code called ``composite.run(max_duration)`` once. That
    # blocks for the full duration with no opportunity to emit between
    # ticks — final parquet would be one row at finalize. The
    # tick-by-tick pattern matches what EcoliSim.run_to_division does
    # for the lineage path (poll_s=1.0 → one emit per sim-second).
    POLL_S = 1.0
    import threading
    holder: dict = {}

    def _drive():
        try:
            _emit_tick()  # initial state row, mirroring lineage emit cadence
            end_t = (float(composite.state.get('global_time', 0.0))
                     + float(max_duration))
            while float(composite.state.get('global_time', 0.0)) < end_t:
                remaining = end_t - float(composite.state.get('global_time', 0.0))
                step = min(POLL_S, remaining)
                composite.run(step)
                _emit_tick()
            holder['ok'] = True
        except BaseException as e:  # noqa: BLE001 — propagate to actor
            holder['exc'] = e

    t0 = time.perf_counter()
    runner = threading.Thread(target=_drive, daemon=True)
    runner.start()
    # Poll loop: log sim_time every HEARTBEAT_SECS of wall clock. Reads
    # of composite.state['global_time'] race the sim loop's writes but
    # at worst we see a stale value (no crash) — fine for a heartbeat.
    HEARTBEAT_SECS = 60.0
    while runner.is_alive():
        runner.join(timeout=HEARTBEAT_SECS)
        if runner.is_alive():
            gt = composite.state.get('global_time', 0.0)
            agents = composite.state.get('agents', {})
            n_cells = len(agents) if isinstance(agents, dict) else 0
            print(
                f"[ray-colony] heartbeat: sim_time={gt:.1f}s  "
                f"cells={n_cells}  wall={time.perf_counter() - t0:.0f}s",
                flush=True,
            )
    wall = time.perf_counter() - t0
    # Flush pending buffers + write per-agent ``_success.pq`` markers.
    # ParquetEmitter holds up to ``batch_size`` (400) rows in memory
    # between writes; without finalize, the last partial batch never
    # reaches S3. Finalize each agent's emitter individually so a
    # failure for one cell doesn't drop the others.
    if out_uri and agent_emitters:
        print(f"[ray-colony] finalizing {len(agent_emitters)} per-cell "
              f"emitter(s)...", flush=True)
        for aid, em in agent_emitters.items():
            try:
                em.success = 'ok' in holder
                em.finalize()
            except Exception as e:
                print(f"[ray-colony] emitter finalize warning (agent={aid}): {e}",
                      flush=True)
    if 'exc' in holder:
        raise holder['exc']

    final_agents = composite.state.get('agents', {})
    return {
        'wall_s': wall,
        'sim_time': float(composite.state.get('global_time', 0.0)),
        'final_cells': len(final_agents),
        'ids': sorted(final_agents.keys()),
    }


def _resolve_inherited_config(config_path, cfg=None):
    """Walk ``inherit_from`` chain + implicit default.json — same logic
    bootstrap_head_ray.sh uses to read per-run knobs from the colony
    config without needing EcoliSim imported on the driver."""
    import json as _json
    if cfg is None:
        with open(config_path) as f:
            cfg = _json.load(f)
    cfg_dir = os.path.dirname(os.path.abspath(config_path))
    merged = {}
    if os.path.basename(os.path.abspath(config_path)) != 'default.json':
        for cand in (os.path.join(cfg_dir, 'default.json'),
                     os.path.join(cfg_dir, '..', 'configs', 'default.json')):
            if os.path.isfile(cand):
                with open(cand) as f:
                    merged.update(_resolve_inherited_config(cand, _json.load(f)))
                break
    for parent_rel in cfg.get('inherit_from') or []:
        for cand in (os.path.join(cfg_dir, parent_rel),
                     os.path.join(cfg_dir, '..', 'configs', parent_rel),
                     os.path.join(cfg_dir, parent_rel + '.json')):
            if os.path.isfile(cand):
                with open(cand) as f:
                    merged.update(_resolve_inherited_config(cand, _json.load(f)))
                break
    merged.update(cfg)
    return merged


def main():
    ap = argparse.ArgumentParser()
    # Two CLI shapes supported:
    #   1. Config-driven (used by EC2 ec2_cluster_ray_colony.py):
    #        --config <path.json> --ray_address auto --experiment-id <id>
    #      All sim knobs (sim_data_path, target_doublings, max_duration,
    #      base_seed) read from the config JSON. CLI args override.
    #   2. CLI-only (used for local smoke tests):
    #        --sim-data-path ... --target-doublings ... --max-duration ...
    ap.add_argument('--sim-data-path', default=None)
    ap.add_argument('--config', default=None)
    ap.add_argument('--target-doublings', type=int, default=None)
    ap.add_argument('--base-seed', type=int, default=None)
    ap.add_argument('--max-duration', type=float, default=None)
    # Both --ray-address (hyphen) and --ray_address (underscore) accepted
    # so the script slots into ec2_cluster_ray_colony.py's invocation
    # which uses underscore (matches lineage runner convention).
    ap.add_argument('--ray-address', '--ray_address',
                    dest='ray_address', default=None)
    ap.add_argument('--experiment-id', '--experiment_id',
                    dest='experiment_id', default=None,
                    help='Override config experiment_id. Used by '
                         'vecoli_aws.sh to inject auto-generated unique '
                         'IDs per launch.')
    ap.add_argument('--num-cpus', type=int, default=None,
                    help='Local Ray runtime CPU cap (ignored for cluster).')
    args = ap.parse_args()

    # Read config (if supplied), then let CLI args override.
    cfg = {}
    if args.config:
        import json as _json
        with open(args.config) as f:
            cfg = _json.load(f)
        cfg = _resolve_inherited_config(args.config, cfg)

    sim_data_path = args.sim_data_path or cfg.get('sim_data_path')
    target_doublings = (args.target_doublings
                        if args.target_doublings is not None
                        else int(cfg.get('target_doublings', 1)))
    base_seed = (args.base_seed
                 if args.base_seed is not None
                 else int(cfg.get('lineage_seed', cfg.get('base_seed', 0))))
    experiment_id = args.experiment_id or cfg.get('experiment_id')
    # Out URI for parquet emission. Falls through to None when running
    # locally with no emitter configured — the actor's emit loop is a
    # no-op in that case.
    out_uri = (cfg.get('emitter_arg') or {}).get('out_uri') \
        or (cfg.get('emitter_arg') or {}).get('out_dir')

    # max_duration: explicit (CLI > config) wins. Otherwise auto-derive
    # from target_doublings: one cell cycle ≈ ``CYCLE_TIME`` sim-sec
    # plus a buffer to cover the final gen's growth + divide. The
    # composite then ticks for that many sim-seconds and stops. If
    # auto-derived is too short for the actual cycle, you'll see
    # fewer cells than target; bump CYCLE_TIME / buffer in that case.
    CYCLE_TIME = 2700.0   # approximate sim-sec per generation
    BUFFER = 600.0        # extra sim-sec past the last divide
    if args.max_duration is not None:
        max_duration = args.max_duration
        max_duration_source = 'CLI'
    elif cfg.get('max_duration') is not None:
        max_duration = float(cfg['max_duration'])
        max_duration_source = 'config'
    else:
        max_duration = target_doublings * CYCLE_TIME + BUFFER
        max_duration_source = (f'auto-derived: {target_doublings} × '
                                f'{CYCLE_TIME:.0f}s + {BUFFER:.0f}s')

    if not sim_data_path:
        ap.error('--sim-data-path required (or sim_data_path in --config)')

    ray_init_kwargs = {'log_to_driver': True}
    if args.ray_address:
        ray_init_kwargs['address'] = args.ray_address
    if args.num_cpus is not None:
        ray_init_kwargs['num_cpus'] = args.num_cpus
    ray.init(**ray_init_kwargs)
    print(f'[ray-colony] ray runtime up '
          f'(address={args.ray_address or "local"})', flush=True)

    sim_data_path_abs = (sim_data_path if sim_data_path.startswith(('s3://', 'gs://'))
                         else os.path.abspath(sim_data_path))
    target_n = 2 ** target_doublings
    print(f'[ray-colony] experiment_id={experiment_id!r} '
          f'target: 1 -> {target_n} cells '
          f'({target_doublings} doublings), '
          f'max_duration={max_duration:.0f}s sim time '
          f'[{max_duration_source}], '
          f'base_seed={base_seed}', flush=True)

    t0 = time.perf_counter()
    fut = run_colony_actor.remote(
        sim_data_path=sim_data_path_abs,
        target_doublings=target_doublings,
        max_duration=max_duration,
        base_seed=base_seed,
        config_path=args.config,
        experiment_id=experiment_id,
        out_uri=out_uri,
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
