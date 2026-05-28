"""vEcoli cell as ``ray:Composite`` — mirror of ``grow_divide_agent``
pattern but with a real vEcoli cell tree inside.

Structure (matches grow_divide test exactly):
    outer.state = {
        'agents': {
            '0': {
                'cell': {                           # the cell-Composite node
                    '_type': 'process',
                    'address': 'local:Composite',   # local first; ray after
                    'config': {
                        'state': cell_doc,           # = {agents: {0: cell_tree}}
                        'bridge': {
                            'outputs': {
                                'agents': ['agents'],  # cell.inner.agents → bridge port
                            },
                        },
                        'run_steps_on_init': True,
                    },
                    'outputs': {
                        'agents': ['..'],            # bridge port → outer.agents (siblings)
                    },
                }
            }
        }
    }

Wire trace:
  - Inner: CompositeDivision at ``inner.agents.0.division`` wires
    ``agents → ('..', '..', 'agents')`` → resolves to ``inner.agents``.
    ✓ (same as greenfield, just inside cell-Composite).
  - Bridge: ``outputs.agents = ['agents']`` reads cell.inner.agents.
  - Outer: cell node at ``outer.agents.0.cell``, link_path = ``('agents',
    '0', 'cell')``, link_path[:-1] = ``('agents', '0')``, wire
    ``['..']`` → ``('agents',)`` = outer.agents. Daughters land as
    siblings to '0'.

Starts with ``local:Composite`` (no Ray). Once divides propagate
locally, swap to ``ray:Composite``.
"""
import os
os.environ.setdefault('OMP_NUM_THREADS', '1')
os.environ.setdefault('MKL_NUM_THREADS', '1')
os.environ.setdefault('OPENBLAS_NUM_THREADS', '1')
os.environ.setdefault('NUMBA_NUM_THREADS', '1')
try:
    from threadpoolctl import threadpool_limits as _tpl
    _tpl(limits=1)
except ImportError:
    pass

import argparse
import time
from pprint import pformat


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sim-data-path', default='out/kb/simData.cPickle')
    ap.add_argument('--max-duration', type=float, default=5.0,
                    help='Start small to just verify construction works.')
    ap.add_argument('--address', default='local:Composite',
                    help='Cell-Composite address (local:Composite or ray:Composite).')
    args = ap.parse_args()

    sim_data_path = os.path.abspath(args.sim_data_path)

    from configs import CONFIG_DIR_PATH
    from ecoli.experiments.ecoli_master_sim import EcoliSim
    from ecoli.library import bigraph_types as _bt
    from ecoli.library.bigraph_types import ECOLI_TYPES
    from ecoli.library.sim_data import LoadSimData
    from ecoli.composites.ecoli_composite import build_ecoli_document
    from process_bigraph import Composite, allocate_core

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
    sim_config['seed'] = 0
    sim_config['divide'] = True

    core = allocate_core()
    core.register_types(ECOLI_TYPES)
    # Register Composite for cell-Composite address resolution.
    core.register_link('Composite', Composite)

    # ===========================================================
    # Two-pass build to handle the chicken-and-egg:
    #   - CompositeDivision needs ``daughter_wrap_template`` (the
    #     cell-Composite process decl shape) + ``cell_schema`` to
    #     build properly-divided + properly-wrapped daughters.
    #   - The wrap template's static parts (address, bridge, ports)
    #     we can declare directly. But ``cell_schema`` is the
    #     RESOLVED schema of the inner cell tree — only known AFTER
    #     a Composite is built around the cell tree.
    #
    # Pass 1: build cell tree without division wrap config →
    #         instantiate a throwaway Composite → grab ``.schema``.
    # Pass 2: rebuild with ``division_wrap_template`` +
    #         ``division_cell_schema`` injected into sim_config.
    #         The rebuilt cell tree's CompositeDivision now has
    #         what it needs at divide time.
    # ===========================================================
    print('[probe] PASS 1: build cell tree to extract schema...',
          flush=True)
    t0 = time.perf_counter()
    lsd = LoadSimData(**{**sim_config, 'seed': 0})
    cell_doc_probe = build_ecoli_document(core, sim_config, load_sim_data=lsd,
                                           flat=False)
    sd_store = cell_doc_probe.get('sim_data_objects', {})
    for k, v in sd_store.items():
        if not k.startswith('_'):
            _bt._sim_data_object_instances[k] = v
    probe_cell = Composite({'state': cell_doc_probe, 'schema': {}}, core=core)
    cell_schema_resolved = probe_cell.schema
    print(f'[probe]   pass 1 built + instantiated in '
          f'{time.perf_counter()-t0:.1f}s; cell_schema has '
          f'{len(cell_schema_resolved) if hasattr(cell_schema_resolved, "__len__") else "?"} top-level entries',
          flush=True)

    # Build the daughter wrap template — same shape the outer uses
    # to wrap the mother. Note ``config.state`` is left out; the
    # patched CompositeDivision substitutes each daughter's tree
    # there before emitting _add.
    #
    # ``interface.outputs.agents = map[any]`` declares a NARROW
    # output port schema, overriding the (huge) schema wire_schema
    # would derive from cell.state.agents = {0: cell_tree}. Without
    # this, port_merges propagates the entire cell_tree shape up to
    # outer.agents._value, which materializes the cell's internal
    # processes (bulk-timeline, global_clock, etc.) as duplicates at
    # the outer level during realize. With ``map[any]``, the bridge
    # only propagates the divide-event payload shape.
    daughter_wrap_template = {
        '_type': 'process',
        'address': args.address,
        'config': {
            # 'state': <CompositeDivision substitutes daughter_state here>
            'bridge': {
                'outputs': {
                    # Read divide events from a DEDICATED slot that
                    # only CompositeDivision touches — not from
                    # ``agents`` (which mother lives in and every
                    # inner sub-process write reflects up through).
                    # _build_topology routes division.outputs.agents
                    # to ``divide_emit`` when ``division_wrap_template``
                    # is configured.
                    'agents': ['divide_emit'],
                },
            },
            'interface': {
                'outputs': {'agents': 'map[node]'},
            },
            'run_steps_on_init': True,
            'parallel_processes': True,
        },
        'inputs': {},
        'outputs': {
            'agents': ['..'],
        },
        'interval': 1.0,
    }

    # ===========================================================
    # Visibility: one TickHeartbeat at the outer level (lightweight
    # per-tick log to /tmp/probe_heartbeat.log) + one CellParquetEmitter
    # inside each cell tree (real data, buffered batches of 400).
    # The `!path.to.Class` form bypasses the process registry —
    # local_lookup resolves the class directly.
    # ===========================================================
    experiment_id = f'cell_as_composite_probe_{int(time.time())}'
    heartbeat_log = '/tmp/probe_heartbeat.log'
    parquet_out_dir = f'/tmp/cell_parquet_{experiment_id}'
    # Truncate the heartbeat log so we start clean.
    open(heartbeat_log, 'w').close()
    print(f'[probe] heartbeat log: {heartbeat_log}', flush=True)
    print(f'[probe] parquet out: {parquet_out_dir}', flush=True)

    heartbeat_step = {
        '_type': 'step',
        'address': 'local:!ecoli.processes.cell_parquet_emitter.TickHeartbeat',
        'config': {
            'log_path': heartbeat_log,
            'agent_id': 'OUTER',
        },
        'inputs': {'global_time': ['global_time']},
    }
    parquet_emitter_step = {
        '_type': 'step',
        'address': 'local:!ecoli.processes.cell_parquet_emitter.CellParquetEmitter',
        'config': {
            'out_dir': parquet_out_dir,
            'experiment_id': experiment_id,
            'agent_id': sim_config['agent_id'],
            'variant': 0,
            'lineage_seed': sim_config.get('seed', 0),
            'batch_size': 400,
        },
        # Wired from inside cell.state.agents.<id>.parquet_emitter:
        #   - ['..', 'listeners']  → cell.state.agents.<id>.listeners
        #   - ['..', 'bulk']        → cell.state.agents.<id>.bulk
        #   - ['..', 'process_state'] → cell.state.agents.<id>.process_state
        #   - ['..', '..', '..', 'global_time'] → cell.state.global_time
        'inputs': {
            'global_time': ['..', '..', 'global_time'],
            'listeners': ['..', 'listeners'],
            'bulk': ['..', 'bulk'],
            'process_state': ['..', 'process_state'],
        },
    }

    print('[probe] PASS 2: rebuild cell tree with wrap_template + '
          'cell_schema in division config...', flush=True)
    t0 = time.perf_counter()
    sim_config_full = {
        **sim_config,
        'division_wrap_template': daughter_wrap_template,
        'division_cell_schema': cell_schema_resolved,
    }
    cell_doc = build_ecoli_document(core, sim_config_full,
                                     load_sim_data=lsd, flat=False)
    # Inject the per-cell parquet emitter step as a sibling of
    # division/allocators inside the cell's agents map. It travels
    # with the cell tree through divide (path_copy_merge preserves
    # it) and gets a fresh instance per daughter via realize. All
    # daughters currently share the mother's agent_id in their
    # emitter config — fix per-daughter agent_id later by adding
    # 'parquet_emitter.config.agent_id' to CompositeDivision's
    # override.
    agent_id = sim_config['agent_id']
    cell_doc['agents'][agent_id]['parquet_emitter'] = parquet_emitter_step
    print(f'[probe]   pass 2 built in {time.perf_counter()-t0:.1f}s; '
          f'cell_doc keys: {sorted(cell_doc.keys())[:8]}...',
          flush=True)

    # Cell node, identical shape to the wrap template.
    cell_node = {
        '_type': 'process',
        'address': args.address,
        'config': {
            'state': cell_doc,
            'bridge': {
                'outputs': {
                    # Read divide events from a DEDICATED slot that
                    # only CompositeDivision touches — not from
                    # ``agents`` (which mother lives in and every
                    # inner sub-process write reflects up through).
                    # _build_topology routes division.outputs.agents
                    # to ``divide_emit`` when ``division_wrap_template``
                    # is configured.
                    'agents': ['divide_emit'],
                },
            },
            'interface': {
                'outputs': {'agents': 'map[node]'},
            },
            'run_steps_on_init': True,
            'parallel_processes': True,
        },
        'inputs': {},
        'outputs': {
            'agents': ['..'],
        },
        'interval': 1.0,
    }

    # Outer structure: agents.0.cell = cell-Composite. The depth-3
    # nesting (with cell.<id>.cell instead of agents.<id> directly)
    # is what makes the ['..'] wire land at outer.agents siblings,
    # the same way grow_divide gets daughters at environment siblings.
    # Plus a single TickHeartbeat at the outer level for lightweight
    # per-tick visibility — fires on outer's global_time changes.
    outer_state = {
        'agents': {
            '0': {
                'cell': cell_node,
            },
        },
        'heartbeat': heartbeat_step,
    }

    print('[probe] building OUTER composite...', flush=True)
    t0 = time.perf_counter()
    # Schema: outer.agents is a map; each value contains a 'cell' field
    # which is process-typed. Declare so realize_link instantiates the
    # cell-Composite.
    outer = Composite(
        {'state': outer_state,
         'schema': {'agents': {'_type': 'map', '_value': {'cell': 'process'}}},
         'parallel_processes': True},
        core=core,
    )
    print(f'[probe]   built in {time.perf_counter()-t0:.1f}s', flush=True)
    print(f'[probe] outer process_paths: {sorted(outer.process_paths.keys())}',
          flush=True)
    print(f'[probe] outer step_paths   : {sorted(outer.step_paths.keys())[:5]}...',
          flush=True)
    print(f'[probe] outer agents keys  : {sorted(outer.state.get("agents", {}).keys())}',
          flush=True)
    # CRITICAL: inspect outer.state.agents.0 — does it have just 'cell',
    # or did the cell-Composite's inner state leak into it?
    agents_0 = outer.state.get('agents', {}).get('0', {})
    print(f'[probe] outer.state.agents.0 keys ({len(agents_0)}): '
          f'{sorted(agents_0.keys())[:15]}...', flush=True)
    cell_entry = outer.state['agents']['0'].get('cell')
    if not isinstance(cell_entry, dict):
        print(f'[probe] !!! outer.state.agents.0.cell is {type(cell_entry).__name__} — '
              f'expected dict with cell-Composite decl', flush=True)
        cell_entry = None
    if cell_entry is not None and isinstance(cell_entry, dict):
        inst = cell_entry.get('instance')
        if inst is not None:
            print(f'[probe] cell instance type: {type(inst).__name__}', flush=True)
            print(f'[probe] cell.state keys: {sorted(inst.state.keys())}', flush=True)
            print(f'[probe] cell.process_paths: {sorted(inst.process_paths.keys())[:5]}',
                  flush=True)
            print(f'[probe] cell.step_paths (first 5): '
                  f'{sorted(inst.step_paths.keys())[:5]}', flush=True)

    # Run as a normal composite: one call, no manual tick driving.
    # parallel_processes=True is set on outer + inner so FBA and other
    # heavy processes can parallelize. Report only at the end.
    print(f'[probe] running for {args.max_duration:.0f}s sim time...',
          flush=True)
    overall_t0 = time.perf_counter()
    pre_agents = sorted(outer.state.get('agents', {}).keys())
    outer.run(args.max_duration)
    overall_wall = time.perf_counter() - overall_t0
    post_agents = sorted(outer.state.get('agents', {}).keys())
    print(f'[probe] done. wall={overall_wall:.1f}s '
          f'sim_time={outer.state.get("global_time")}', flush=True)
    print(f'[probe] outer agents: pre={pre_agents} → post={post_agents}',
          flush=True)
    if post_agents != pre_agents:
        print(f'[probe] ✅ DIVIDE PROPAGATED — agents changed', flush=True)
        # Inspect each daughter's structure
        for aid in post_agents:
            entry = outer.state['agents'].get(aid, {})
            keys = sorted(entry.keys()) if isinstance(entry, dict) else type(entry).__name__
            print(f'  agents.{aid!r}: keys={keys[:5]}', flush=True)
    else:
        print(f'[probe] ❌ no divide propagation (mass threshold not reached?)',
              flush=True)

    # Profile breakdown: where did the wall go?
    print('\n=== TIMING (outer composite) ===', flush=True)
    s_outer = outer.timing_summary()
    print(f'  total:     {s_outer.total:7.3f}s', flush=True)
    print(f'  process:   {s_outer.process_time:7.3f}s ({100*s_outer.process_time/s_outer.total:.1f}%)',
          flush=True)
    print(f'  framework: {s_outer.framework_time:7.3f}s ({100*s_outer.framework_time/s_outer.total:.1f}%)',
          flush=True)
    print('  per-process invoke time:', flush=True)
    for path, t in sorted(s_outer.per_process.items(), key=lambda kv: -kv[1])[:10]:
        print(f'    {t:7.3f}s  {path}', flush=True)

    # Inner cell's timing — note this only reflects LAST cell.run() call
    # because timing accumulators reset on each run(). Still useful to
    # see relative process costs inside the cell.
    cell_inst = outer.state['agents']['0']['cell'].get('instance')
    if cell_inst is not None:
        print('\n=== TIMING (inner cell — last tick only) ===', flush=True)
        s_cell = cell_inst.timing_summary()
        print(f'  total:     {s_cell.total:7.3f}s', flush=True)
        print(f'  process:   {s_cell.process_time:7.3f}s ({100*s_cell.process_time/max(s_cell.total,1e-9):.1f}%)',
              flush=True)
        print(f'  framework: {s_cell.framework_time:7.3f}s ({100*s_cell.framework_time/max(s_cell.total,1e-9):.1f}%)',
              flush=True)
        print('  per-process invoke time (last tick):', flush=True)
        for path, t in sorted(s_cell.per_process.items(), key=lambda kv: -kv[1])[:10]:
            print(f'    {t:7.3f}s  {path}', flush=True)


if __name__ == '__main__':
    main()
