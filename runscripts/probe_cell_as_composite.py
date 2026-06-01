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
import signal
import faulthandler
from pprint import pformat

# Dump tracebacks for ALL threads to stderr on SIGUSR1. Use to
# diagnose hangs: ``kill -USR1 <pid>``.
faulthandler.register(signal.SIGUSR1, all_threads=True)


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

    # Ray init when the address indicates ray:Composite. Daughter
    # cell-Composites then spawn on Ray actors (one per shard pool
    # entry). RAY_SHARDS_DEFAULT caps the shard pool — without this
    # the runtime would spawn one actor per outer process_path which
    # over a divided colony would exceed available CPUs fast.
    if args.address.startswith('ray:'):
        import ray
        from process_bigraph.protocols.ray import (
            register_process_class, register_type_provider,
            get_or_create_runtime)
        os.environ.setdefault('RAY_SHARDS_DEFAULT', '2')
        if not ray.is_initialized():
            ray.init(num_cpus=4, log_to_driver=False)
        register_process_class('Composite', Composite)
        # Daughter cell-Composites use EcoliCellComposite, which
        # builds its cell tree from sim_data on the actor side
        # (avoids cloudpickle failure on Process instances that
        # carry scipy lsoda's _queue.SimpleQueue across actors).
        from ecoli.composites.ecoli_cell_process import EcoliCellComposite
        register_process_class('EcoliCellComposite', EcoliCellComposite)
        # Each Ray actor allocates its OWN core, which doesn't
        # inherit driver-side type registrations. Register a
        # type-provider so every actor calls ``register_ecoli_types``
        # on its core before instantiating the cell-Composite. Without
        # this the actor's realize hits unknown types
        # (``sim_data_object_store``) and crashes.
        register_type_provider(
            'ecoli.library.bigraph_types', 'register_ecoli_types')
        # Register the sim_data loader on the actor's core so
        # ``_sim_data_object_instances`` is populated BEFORE the
        # actor's cell-Composite is built. This lets us strip
        # ``sim_data_objects`` from per-cell config['state'] —
        # daughters then don't drag ~700MB of sim_data through
        # every pool spawn.
        register_type_provider(
            'ecoli.library.bigraph_types', 'load_sim_data_provider',
            kwargs={'sim_data_path': sim_data_path})
        print(f'[probe] Ray runtime up '
              f'(shards={os.environ["RAY_SHARDS_DEFAULT"]}); '
              f'Composite + ECOLI + sim_data providers registered',
              flush=True)

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
    # Register EcoliCellComposite on the driver too — load_protocol
    # (called for ray:EcoliCellComposite addresses on the driver
    # side when realizing daughter cell declarations) looks up the
    # class via the driver's link_registry.
    from ecoli.composites.ecoli_cell_process import EcoliCellComposite
    core.register_link('EcoliCellComposite', EcoliCellComposite)

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
    # EARLY extraction of the per-entry cell tree schema. The mother's
    # cell_node config NEEDS this so the Ray actor's CompositeDivision
    # can read parent.config.schema['agents']['_value'] at divide time.
    # Without it the mother's cell_node carries no schema at all and
    # the actor falls off the schema-extraction fallback path.
    # ``_value`` on the resolved schema is the emit-target schema (4
    # keys: bulk/listeners/process_state). The PER-ENTRY schema lives
    # at ``cell.schema['agents']['<agent_id>']`` and has the FULL
    # set of 75+ keys the cell tree actually contains.
    _early_agents_sch = probe_cell.schema['agents']
    if '0' in _early_agents_sch:
        cell_tree_node_early = _early_agents_sch['0']
        print(f'[probe]   EARLY cell_tree_node from agents[0]: '
              f'{len(cell_tree_node_early)} keys', flush=True)
    else:
        # Fallback: _value (sparse).
        cell_tree_node_early = getattr(
            _early_agents_sch, '_value',
            _early_agents_sch.get('_value', {}) if isinstance(
                _early_agents_sch, dict) else {})
        print(f'[probe]   EARLY fallback cell_tree_node from _value: '
              f'{len(cell_tree_node_early) if isinstance(cell_tree_node_early, dict) else "?"} keys',
              flush=True)
    # Extract the inner cell tree's schema (the per-agent shape) from
    # the full composite schema. The full schema has ``{agents: Map[
    # cell_tree], global_time: ..., sim_data_objects: ...}`` at top.
    # CompositeDivision's ``mother_state`` wire ``('..',)`` reads
    # ``cell.state.agents.<id>`` which IS the cell tree itself — so
    # ``_divide_walk`` needs the cell tree schema, not the full
    # composite schema (mismatch produces nonsense daughter state
    # with '0' as a top key plus mixed cell tree keys).
    agents_node = cell_schema_resolved.get('agents') if hasattr(cell_schema_resolved, 'get') else None
    cell_tree_schema = getattr(agents_node, '_value', None) if agents_node is not None else None
    if cell_tree_schema is None:
        # Fallback: walk the resolved schema for the agents map's value.
        try:
            cell_tree_schema = cell_schema_resolved['agents']._value
        except Exception:
            cell_tree_schema = cell_schema_resolved
    print(f'[probe]   pass 1 built + instantiated in '
          f'{time.perf_counter()-t0:.1f}s; cell_tree_schema type: '
          f'{type(cell_tree_schema).__name__}', flush=True)
    # Dump cell_tree_schema's keys so we can verify it covers all cell
    # tree fields (bulk, unique, environment, boundary, sim_data_objects).
    if isinstance(cell_tree_schema, dict):
        print(f'[probe]   cell_tree_schema keys ({len(cell_tree_schema)}): '
              f'{sorted(cell_tree_schema.keys())[:25]}', flush=True)
    else:
        # Resolved Node — has fields as attributes; print all attrs
        # that look like cell tree top-level keys.
        all_attrs = [a for a in dir(cell_tree_schema)
                     if not a.startswith('_')]
        print(f'[probe]   cell_tree_schema attrs ({len(all_attrs)}): '
              f'{sorted(all_attrs)[:25]}', flush=True)
    # Compare to mother cell's actual state keys
    mother_inst = probe_cell.state.get('agents', {}).get('0', {})
    print(f'[probe]   mother cell state keys ({len(mother_inst)}): '
          f'{sorted(mother_inst.keys())[:25]}', flush=True)

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
            # Pass the resolved cell_schema so the daughter Composite
            # doesn't re-infer from its (huge, Object-laden) state at
            # realize time. Without this, infer walks the daughter's
            # cell tree, hits a Python object (sim_data ref, pint
            # Quantity, etc.), and dispatches to serialize(Object,
            # value) which recursively calls infer(None, value) —
            # passing None for core because that serialize handler
            # has no signature for it — and explodes on
            # ``core.access_type``.
            # Daughter schema matches mother: ``agents`` is a map whose
            # _value is the per-entry cell tree schema we pulled from
            # PASS 1's ``probe_cell.schema['agents']['0']`` (~75 keys
            # — bulk, unique, listeners, environment, boundary,
            # sim_data_objects, and every process_state slot). Passing
            # the dict literal here means it survives pickling to the
            # Ray actor; CompositeDivision on the actor reads
            # ``parent.config['schema']['agents']['_value']`` to drive
            # ``_divide_walk`` over mother state at divide time.
            'schema': {
                'agents': {'_type': 'map', '_value': cell_tree_node_early},
                'global_time': 'float',
            },
            'bridge': {
                # CONDUIT (not outputs): divide events from
                # ``divide_emit`` propagate to bridge_updates AND
                # are stripped from local apply. The daughter
                # _add/_remove sentinels reach outer.agents (correct)
                # without instantiating nested daughter cell-
                # Composites locally (which would cascade re-divide).
                'conduits': {
                    'agents': ['divide_emit'],
                },
            },
            'interface': {
                'outputs': {'agents': 'map[node]'},
            },
            'run_steps_on_init': True,
            # parallel_processes False because scipy lsoda integrator
            # (used by Equilibrium) is not thread-safe across parallel
            # daughter Composites. Each daughter ticks sequentially.
            'parallel_processes': False,
        },
        'inputs': {},
        'outputs': {
            'agents': ['..'],
        },
        # Outer-tick interval. Sets how often the outer Composite
        # invokes ``cell.update(state, interval)``. The cell-Composite's
        # own engine runs internally at its inner-process intervals
        # regardless. For Ray, every outer tick is an RPC roundtrip
        # (~400ms overhead) — so coarser intervals amortize the RPC.
        # For local, the cost is small either way. 60 sim_sec gives
        # ~50 RPCs per doubling and keeps env-feedback latency well
        # under a cell-cycle. Bridge updates (divide events) propagate
        # at the END of each outer tick — daughter cell-Composites
        # spawn at outer.agents whenever the actor returns _add events.
        'interval': 60.0,
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
    # Install cell tree schema + daughter wrap template into the
    # cell_division module's cache. CompositeDivision reads from
    # there at divide time. Bypassing config for these — see
    # cell_division module for why. Cell-Composite instance is
    # installed below AFTER the outer is built.
    from ecoli.processes.cell_division import (
        set_cell_tree_schema, set_daughter_wrap_template,
        set_cell_composite_instance)
    set_cell_tree_schema(cell_tree_schema)
    set_daughter_wrap_template(daughter_wrap_template)

    # Daughter address: for Ray, use EcoliCellComposite which rebuilds
    # its cell tree from sim_data on the actor side (no live Process
    # instances cross the boundary). For local, plain Composite works
    # since pickling isn't involved.
    if args.address.startswith('ray:'):
        daughter_address = 'ray:EcoliCellComposite'
    else:
        daughter_address = args.address
    sim_config_full = {
        **sim_config,
        'cell_as_composite_mode': True,
        'daughter_address': daughter_address,
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
    # Strip ``sim_data_objects`` from the cell document for Ray runs.
    # The actor's ``load_sim_data_provider`` type-provider already
    # populates ``_sim_data_object_instances`` from disk, so any
    # SimDataObjectRef in the cell tree resolves against the
    # actor-local instances. Sending sim_data through config doubles
    # the wire transfer per pool spawn (~700MB) and per-daughter
    # cloudpickle work. For local runs the driver already populated
    # the instance store via _bt._sim_data_object_instances above —
    # also safe to drop. The mother's already-driver-side instance
    # store still satisfies driver-side realize for the outer.
    if args.address.startswith('ray:') and 'sim_data_objects' in cell_doc:
        del cell_doc['sim_data_objects']
        print(f'[probe]   stripped sim_data_objects from cell_doc '
              f'(actor loads via type-provider)', flush=True)
    print(f'[probe]   pass 2 built in {time.perf_counter()-t0:.1f}s; '
          f'cell_doc keys: {sorted(cell_doc.keys())[:8]}...',
          flush=True)

    # Cell node, identical shape to the wrap template.
    # CRITICAL for Ray: include ``schema`` so the actor's Composite
    # has ``self.config['schema']['agents']['_value']`` populated.
    # CompositeDivision on the actor reads this at divide time (the
    # module-global ``_CELL_TREE_SCHEMA`` is None on actors —
    # process-isolated, no shared module state with the driver).
    cell_node = {
        '_type': 'process',
        'address': args.address,
        'config': {
            'state': cell_doc,
            'schema': {
                'agents': {'_type': 'map', '_value': cell_tree_node_early},
                'global_time': 'float',
            },
            'bridge': {
                # CONDUIT (not outputs): divide events from
                # ``divide_emit`` propagate to bridge_updates AND
                # are stripped from local apply. The daughter
                # _add/_remove sentinels reach outer.agents (correct)
                # without instantiating nested daughter cell-
                # Composites locally (which would cascade re-divide).
                'conduits': {
                    'agents': ['divide_emit'],
                },
            },
            'interface': {
                'outputs': {'agents': 'map[node]'},
            },
            'run_steps_on_init': True,
            # parallel_processes False because scipy lsoda integrator
            # (used by Equilibrium) is not thread-safe across parallel
            # daughter Composites. Each daughter ticks sequentially.
            'parallel_processes': False,
            # cell_build_config: rides through CompositeDivision's
            # wrap_template reconstruction on the actor so daughters
            # can use EcoliCellComposite's rebuild-from-sim_data
            # path. Composite ignores unknown config keys;
            # EcoliCellComposite consumes them.
            'cell_build_config': {
                'sim_config': sim_config_full,
                'sim_data_path': sim_data_path,
                'agent_id': sim_config['agent_id'],
            },
        },
        'inputs': {},
        'outputs': {
            'agents': ['..'],
        },
        # Outer-tick interval. Sets how often the outer Composite
        # invokes ``cell.update(state, interval)``. The cell-Composite's
        # own engine runs internally at its inner-process intervals
        # regardless. For Ray, every outer tick is an RPC roundtrip
        # (~400ms overhead) — so coarser intervals amortize the RPC.
        # For local, the cost is small either way. 60 sim_sec gives
        # ~50 RPCs per doubling and keeps env-feedback latency well
        # under a cell-cycle. Bridge updates (divide events) propagate
        # at the END of each outer tick — daughter cell-Composites
        # spawn at outer.agents whenever the actor returns _add events.
        'interval': 60.0,
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
         # parallel_processes False so 2 daughter cell-Composites don't
         # run concurrently — scipy.integrate.lsoda inside Equilibrium
         # isn't thread-safe. Daughters tick sequentially. For Ray
         # distribution this would be revisited (Ray actors are
         # process-isolated so lsoda is safe across them).
         'parallel_processes': False},
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
    # Install the cell-Composite instance so CompositeDivision can
    # read mother state at divide time without going through the
    # wire system.
    cell_instance = outer.state['agents']['0']['cell'].get('instance')
    if cell_instance is not None:
        set_cell_composite_instance(cell_instance)
        # Register the cell tree schema as a NAMED TYPE ``ecoli`` in
        # the core's type registry. Then the daughter Composite's
        # state schema can reference it as ``map[ecoli]`` — realize
        # looks up the registered schema by name and uses it by
        # reference, avoiding re-derivation per daughter. This is
        # the schema-sharing the user pointed out: we already know
        # the cell tree shape, no need to recompute it for every
        # division event.
        # Use PASS 1's LOCAL probe_cell schema. With ray:Composite,
        # the outer's cell instance is a RayShadow proxy without
        # direct ``.schema`` access — the schema lives on the actor.
        # probe_cell (PASS 1) is local and has the same schema since
        # both were built from the same sim_config.
        _agents_sch = probe_cell.schema['agents']
        if hasattr(_agents_sch, '_value'):
            cell_tree_node = _agents_sch._value
        elif isinstance(_agents_sch, dict):
            cell_tree_node = _agents_sch.get('_value', _agents_sch)
        else:
            cell_tree_node = _agents_sch
        print(f'[probe] cell tree schema type: {type(cell_tree_node).__name__}, '
              f'has {len(cell_tree_node) if hasattr(cell_tree_node, "__len__") else "?"} '
              f'keys/fields', flush=True)
        # _value's dict has only a partial schema (emit-targets like
        # bulk/listeners/process_state). The FULL schema for a
        # specific agent entry is stored separately at the entry's
        # exact path — for mother that's ``cell.schema['agents']['0']``.
        # This contains ALL the fields the state actually has.
        if '0' in _agents_sch:
            cell_tree_node = _agents_sch['0']
            print(f'[probe] using cell.schema[agents][0] for full per-entry '
                  f'schema (better than _value which only has emit targets)',
                  flush=True)
        if isinstance(cell_tree_node, dict):
            print(f'[probe] cell tree schema keys ({len(cell_tree_node)}): '
                  f'{sorted(cell_tree_node.keys())[:40]}', flush=True)
        # Register on the DRIVER's core for local-mode runs.
        core.register_type('ecoli', cell_tree_node)
        set_cell_tree_schema(cell_tree_node)
        # INLINE the cell tree schema dict directly in the wrap
        # template's config.schema (not via the registered name
        # 'ecoli'). Ray actors get the schema through their pickled
        # config and don't need the 'ecoli' name registered on
        # their core. The schema dict pickles cleanly.
        daughter_wrap_template['config']['schema'] = {
            'agents': {'_type': 'map', '_value': cell_tree_node},
            'global_time': 'float',
        }
        set_daughter_wrap_template(daughter_wrap_template)
        print(f'[probe] registered cell tree as `ecoli` type; '
              f'wrap_template uses map[ecoli]', flush=True)

    cell_entry = outer.state['agents']['0'].get('cell')
    if not isinstance(cell_entry, dict):
        print(f'[probe] !!! outer.state.agents.0.cell is {type(cell_entry).__name__} — '
              f'expected dict with cell-Composite decl', flush=True)
        cell_entry = None
    if cell_entry is not None and isinstance(cell_entry, dict):
        inst = cell_entry.get('instance')
        if inst is not None:
            print(f'[probe] cell instance type: {type(inst).__name__}', flush=True)
            if hasattr(inst, 'process_paths'):
                print(f'[probe] cell.state keys: {sorted(inst.state.keys())}', flush=True)
                print(f'[probe] cell.process_paths: {sorted(inst.process_paths.keys())[:5]}',
                      flush=True)
                print(f'[probe] cell.step_paths (first 5): '
                      f'{sorted(inst.step_paths.keys())[:5]}', flush=True)
            else:
                print(f'[probe] (RayShadow — skipping internal-attr dump)',
                      flush=True)

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
    # After divide, mother '0' is removed and daughters take her place.
    # Pick whichever agent is around (mother pre-divide, OR first
    # daughter post-divide) for the inner-cell timing report.
    _agents_now = outer.state.get('agents', {})
    if not _agents_now:
        print('[probe] no agents in outer.state — skipping inner timing',
              flush=True)
        return
    _first_aid = sorted(_agents_now.keys())[0]
    cell_inst = outer.state['agents'][_first_aid].get('cell', {}).get('instance')
    if cell_inst is not None and hasattr(cell_inst, 'timing_summary'):
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
