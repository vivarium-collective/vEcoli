#!/usr/bin/env python3
"""Cell-as-Composite colony on Ray. Production runscript.

Architecture (verified end-to-end on local Ray, 1→2→4):
    outer Composite (driver) holds N cells at outer.agents.<id>.cell,
    each cell wrapped as a ``ray:Composite`` (mother) or
    ``ray:EcoliCellComposite`` (daughter). All cells share one actor
    pool keyed by class_name. Mother's actor builds its Composite
    from a pre-built cell_doc; daughter actors rebuild from
    sim_data via the type-provider — divided bulk/unique/... are
    shipped as a small overlay through the divide event.

See:
  - ``probe_cell_as_composite.py`` — dev/debug version
  - ``ecoli/composites/ecoli_cell_process.py:EcoliCellComposite``
  - ``ecoli/processes/cell_division.py:CompositeDivision.update``
  - ``ecoli/library/bigraph_types.py:load_sim_data_provider``
  - ``process_bigraph/protocols/ray.py`` — pool keying, init_cell flow

Usage (local Ray runtime, 1 cell → 2 cells):
    .venv/bin/python runscripts/run_colony_cac_ray.py \\
        --sim-data-path out/kb/simData.cPickle \\
        --target-doublings 1 --max-duration 3300

Usage (cluster):
    .venv/bin/python runscripts/run_colony_cac_ray.py \\
        --sim-data-path s3://.../simData.cPickle \\
        --config configs/.../foo.json \\
        --ray-address ray://head:10001 \\
        --experiment-id my-run-1
"""
import os
# Pin numerics single-threaded BEFORE any numpy/scipy/numba/ray
# import. Inherited by spawned Ray actors when the cluster's
# ``ray start`` was given the same env.
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


def _patch_s3fs_skip_create_bucket() -> None:
    """Monkey-patch ``s3fs.S3FileSystem._mkdir`` to never call
    ``CreateBucket``. Same GovCloud + aiobotocore quirk handled in
    run_colony_ray.py. All buckets pre-exist; makedirs-on-S3 is a
    no-op."""
    try:
        import s3fs
    except ImportError:
        return

    async def _mkdir_no_create_bucket(self, path, acl=False,
                                       create_parents=True, **kwargs):
        return

    s3fs.S3FileSystem._mkdir = _mkdir_no_create_bucket


_patch_s3fs_skip_create_bucket()


def _resolve_inherited_config(config_path, cfg=None):
    """Walk the inherited-config chain and merge. Same semantics as
    EcoliSim.from_file (default.json → inherit_from chain → child)."""
    if cfg is None:
        with open(config_path) as f:
            cfg = json.load(f)
    cfg_dir = os.path.dirname(os.path.abspath(config_path))
    merged = {}
    if os.path.basename(os.path.abspath(config_path)) != 'default.json':
        for cand in (os.path.join(cfg_dir, 'default.json'),
                     os.path.join(cfg_dir, '..', 'configs', 'default.json')):
            if os.path.isfile(cand):
                with open(cand) as f:
                    merged.update(_resolve_inherited_config(
                        cand, json.load(f)))
                break
    for parent_rel in cfg.get('inherit_from') or []:
        for cand in (os.path.join(cfg_dir, parent_rel),
                     os.path.join(cfg_dir, '..', 'configs', parent_rel),
                     os.path.join(cfg_dir, parent_rel + '.json')):
            if os.path.isfile(cand):
                with open(cand) as f:
                    merged.update(_resolve_inherited_config(
                        cand, json.load(f)))
                break
    merged.update(cfg)
    return merged


def build_and_run(sim_data_path, target_doublings, max_duration,
                   base_seed, out_uri, experiment_id, ray_address,
                   num_cpus):
    """Build the outer Composite + mother cell-Composite, run for
    ``max_duration`` sim-sec. Daughters spawn at outer.agents as
    divide events propagate."""

    # Imports here so the script's top-level threading-pin env vars
    # take effect before numpy/scipy load.
    from configs import CONFIG_DIR_PATH
    from ecoli.experiments.ecoli_master_sim import EcoliSim
    from ecoli.library import bigraph_types as _bt
    from ecoli.library.bigraph_types import ECOLI_TYPES
    from ecoli.library.sim_data import LoadSimData
    from ecoli.composites.ecoli_composite import build_ecoli_document
    from ecoli.composites.ecoli_cell_process import EcoliCellComposite
    from process_bigraph import Composite, allocate_core

    # Ray runtime + cell-as-Composite plumbing
    import ray
    from process_bigraph.protocols.ray import (
        register_process_class, register_type_provider)

    if not ray.is_initialized():
        ray_init_kwargs = {'log_to_driver': True}
        if ray_address:
            ray_init_kwargs['address'] = ray_address
        if num_cpus is not None:
            ray_init_kwargs['num_cpus'] = num_cpus
        ray.init(**ray_init_kwargs)
    register_process_class('Composite', Composite)
    register_process_class('EcoliCellComposite', EcoliCellComposite)
    # Type providers run on each actor before its first Composite is
    # built. ECOLI_TYPES registers Cython types (Bulk, etc.);
    # load_sim_data_provider populates ``_sim_data_object_instances``
    # so SimDataObjectRef resolves without needing sim_data to ride
    # through per-cell config.
    register_type_provider(
        'ecoli.library.bigraph_types', 'register_ecoli_types')
    register_type_provider(
        'ecoli.library.bigraph_types', 'load_sim_data_provider',
        kwargs={'sim_data_path': sim_data_path})
    print(f'[colony-cac] ray runtime up '
          f'(address={ray_address or "local"}); '
          f'providers registered', flush=True)

    sim = EcoliSim.from_file(os.path.join(CONFIG_DIR_PATH, 'default.json'))
    sim.processes = sim._retrieve_processes(
        sim.processes, sim.add_processes,
        sim.exclude_processes, sim.swap_processes)
    sim.topology = sim._retrieve_topology(
        sim.topology, sim.processes, sim.swap_processes, sim.log_updates)
    sim.process_configs = sim._retrieve_process_configs(
        sim.process_configs, sim.processes)
    sim_config = dict(sim.config)
    sim_config['sim_data_path'] = sim_data_path
    sim_config['agent_id'] = '0'
    sim_config['seed'] = int(base_seed)
    sim_config['divide'] = True

    core = allocate_core()
    core.register_types(ECOLI_TYPES)
    core.register_link('Composite', Composite)
    core.register_link('EcoliCellComposite', EcoliCellComposite)

    # PASS 1: build a throwaway local cell to extract the resolved
    # per-agent schema. The mother's cell_node will declare schema as
    # ``agents: map[<this schema>]`` so the actor's Composite has the
    # right ``self.config['schema']['agents']['_value']`` when
    # CompositeDivision needs to rebuild the daughter wrap_template
    # from parent.config.
    t0 = time.perf_counter()
    lsd = LoadSimData(**{**sim_config, 'seed': base_seed})
    cell_doc_probe = build_ecoli_document(
        core, sim_config, load_sim_data=lsd, flat=False)
    sd_store = cell_doc_probe.get('sim_data_objects', {})
    for k, v in sd_store.items():
        if not k.startswith('_'):
            _bt._sim_data_object_instances[k] = v
    probe_cell = Composite(
        {'state': cell_doc_probe, 'schema': {}}, core=core)
    cell_tree_node = probe_cell.schema['agents']['0']
    core.register_type('ecoli', cell_tree_node)
    print(f'[colony-cac]   pass 1 schema extract: '
          f'{time.perf_counter()-t0:.1f}s ({len(cell_tree_node)} keys)',
          flush=True)

    # PASS 2: build the mother cell tree with cell_as_composite_mode
    # so build_ecoli_document adds the ``divide_emit`` slot and
    # CompositeDivision routes its output there (bridge conduit
    # propagates to outer; mother's local agents map stays clean).
    from ecoli.processes.cell_division import (
        set_cell_tree_schema, set_daughter_wrap_template,
        set_cell_composite_instance)
    set_cell_tree_schema(cell_tree_node)

    daughter_address = 'ray:EcoliCellComposite'
    sim_config_full = {
        **sim_config,
        'cell_as_composite_mode': True,
        'daughter_address': daughter_address,
    }
    daughter_wrap_template = {
        '_type': 'process',
        'address': daughter_address,
        'config': {
            'schema': {
                'agents': {'_type': 'map', '_value': cell_tree_node},
                'global_time': 'float',
            },
            'bridge': {
                'conduits': {'agents': ['divide_emit']},
            },
            'interface': {
                'outputs': {'agents': 'map[node]'},
            },
            'run_steps_on_init': True,
            'parallel_processes': False,
            'cell_build_config': {
                'sim_config': sim_config_full,
                'sim_data_path': sim_data_path,
                'agent_id': '0',  # overridden per daughter
            },
        },
        'inputs': {},
        'outputs': {'agents': ['..']},
        'interval': 60.0,
    }
    set_daughter_wrap_template(daughter_wrap_template)

    t0 = time.perf_counter()
    cell_doc = build_ecoli_document(
        core, sim_config_full, load_sim_data=lsd, flat=False)
    # Strip sim_data_objects: the actor's load_sim_data_provider
    # type-provider populates _sim_data_object_instances from disk,
    # so SimDataObjectRef resolves locally. Shipping sim_data through
    # config doubles wire transfer per pool spawn.
    if 'sim_data_objects' in cell_doc:
        del cell_doc['sim_data_objects']

    # Optional per-cell parquet emitter inside each cell tree.
    if out_uri:
        from ecoli.library.parquet_emitter import (  # noqa: F401
            ParquetEmitter)
        agent_id = sim_config['agent_id']
        parquet_step = {
            '_type': 'step',
            'address': (
                'local:!ecoli.processes.cell_parquet_emitter.'
                'CellParquetEmitter'),
            'config': {
                'out_dir': out_uri,
                'experiment_id': experiment_id or f'cac_{int(time.time())}',
                'agent_id': agent_id,
                'variant': 0,
                'lineage_seed': base_seed,
                'batch_size': 400,
            },
            'inputs': {
                'global_time': ['..', '..', 'global_time'],
                'listeners': ['..', 'listeners'],
                'bulk': ['..', 'bulk'],
                'process_state': ['..', 'process_state'],
            },
        }
        cell_doc['agents'][agent_id]['parquet_emitter'] = parquet_step

    cell_node = {
        '_type': 'process',
        'address': 'ray:Composite',
        'config': {
            'state': cell_doc,
            'schema': {
                'agents': {'_type': 'map', '_value': cell_tree_node},
                'global_time': 'float',
            },
            'bridge': {
                'conduits': {'agents': ['divide_emit']},
            },
            'interface': {
                'outputs': {'agents': 'map[node]'},
            },
            'run_steps_on_init': True,
            'parallel_processes': False,
            # cell_build_config rides through CompositeDivision's
            # wrap_template reconstruction on the actor so daughters
            # use EcoliCellComposite's build-from-sim_data path.
            'cell_build_config': {
                'sim_config': sim_config_full,
                'sim_data_path': sim_data_path,
                'agent_id': sim_config['agent_id'],
            },
        },
        'inputs': {},
        'outputs': {'agents': ['..']},
        'interval': 60.0,
    }
    print(f'[colony-cac]   pass 2 mother build: '
          f'{time.perf_counter()-t0:.1f}s', flush=True)

    # Outer Composite holds the colony of cell-as-Composite agents.
    t0 = time.perf_counter()
    outer = Composite(
        {'state': {'agents': {sim_config['agent_id']: {'cell': cell_node}}},
         'schema': {'agents': {'_type': 'map',
                                '_value': {'cell': 'process'}}},
         'parallel_processes': False},
        core=core)
    cell_instance = outer.state['agents'][sim_config['agent_id']]['cell']\
        .get('instance')
    if cell_instance is not None:
        set_cell_composite_instance(cell_instance)
    print(f'[colony-cac]   outer build: '
          f'{time.perf_counter()-t0:.1f}s', flush=True)

    # Run.
    target_n = 2 ** target_doublings
    print(f'[colony-cac] running for {max_duration:.0f}s sim time '
          f'(target: 1 → {target_n} cells, '
          f'experiment_id={experiment_id!r})...', flush=True)
    t0 = time.perf_counter()
    outer.run(max_duration)
    wall = time.perf_counter() - t0

    agents_pre = ['0']
    agents_post = sorted(outer.state.get('agents', {}).keys())
    print(f'[colony-cac] done. wall={wall:.1f}s '
          f'sim_time={outer.state.get("global_time", 0):.1f} '
          f'agents: pre={agents_pre} → post={agents_post}',
          flush=True)
    if len(agents_post) >= target_n:
        print(f'[colony-cac] ✅ target reached: {target_n} cells',
              flush=True)
    else:
        print(f'[colony-cac] ❌ short of target: '
              f'{len(agents_post)} / {target_n}', flush=True)

    return {
        'wall_s': wall,
        'sim_time': float(outer.state.get('global_time', 0)),
        'final_cells': len(agents_post),
        'ids': agents_post,
    }


def main():
    ap = argparse.ArgumentParser()
    # Two CLI shapes (matches run_colony_ray.py):
    #   1. Config-driven (ec2_cluster_ray_colony.py):
    #        --config <path.json> --ray_address auto --experiment-id <id>
    #   2. CLI-only (local smoke tests):
    #        --sim-data-path ... --target-doublings ... --max-duration ...
    ap.add_argument('--sim-data-path', default=None)
    ap.add_argument('--config', default=None)
    ap.add_argument('--target-doublings', type=int, default=None)
    ap.add_argument('--base-seed', type=int, default=None)
    ap.add_argument('--max-duration', type=float, default=None)
    ap.add_argument('--ray-address', '--ray_address',
                    dest='ray_address', default=None)
    ap.add_argument('--experiment-id', '--experiment_id',
                    dest='experiment_id', default=None)
    ap.add_argument('--num-cpus', type=int, default=None)
    args = ap.parse_args()

    cfg = {}
    if args.config:
        with open(args.config) as f:
            cfg = json.load(f)
        cfg = _resolve_inherited_config(args.config, cfg)

    sim_data_path = args.sim_data_path or cfg.get('sim_data_path')
    target_doublings = (args.target_doublings
                        if args.target_doublings is not None
                        else int(cfg.get('target_doublings', 1)))
    base_seed = (args.base_seed
                 if args.base_seed is not None
                 else int(cfg.get('lineage_seed',
                                  cfg.get('base_seed', 0))))
    experiment_id = args.experiment_id or cfg.get('experiment_id')
    out_uri = ((cfg.get('emitter_arg') or {}).get('out_uri')
               or (cfg.get('emitter_arg') or {}).get('out_dir'))

    CYCLE_TIME = 2700.0
    BUFFER = 600.0
    if args.max_duration is not None:
        max_duration = args.max_duration
    elif cfg.get('max_duration') is not None:
        max_duration = float(cfg['max_duration'])
    else:
        max_duration = target_doublings * CYCLE_TIME + BUFFER

    if not sim_data_path:
        ap.error('--sim-data-path required (or sim_data_path in --config)')

    sim_data_path_abs = (sim_data_path
                          if sim_data_path.startswith(('s3://', 'gs://'))
                          else os.path.abspath(sim_data_path))

    t0 = time.perf_counter()
    result = build_and_run(
        sim_data_path=sim_data_path_abs,
        target_doublings=target_doublings,
        max_duration=max_duration,
        base_seed=base_seed,
        out_uri=out_uri,
        experiment_id=experiment_id,
        ray_address=args.ray_address,
        num_cpus=args.num_cpus,
    )
    driver_wall = time.perf_counter() - t0
    print(f'[colony-cac] driver_wall={driver_wall:.1f}s '
          f'cell_wall={result["wall_s"]:.1f}s '
          f'sim_time={result["sim_time"]:.1f} '
          f'final_cells={result["final_cells"]} '
          f'ids={result["ids"]}', flush=True)


if __name__ == '__main__':
    main()
