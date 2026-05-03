"""
==================
E. coli Composite
==================

Builds the vEcoli whole-cell model as a process-bigraph composite
document from sim_data config.

The document is a self-contained dict that ``Composite(document)``
can load entirely through ``realize()``. No pre-built instances,
no manual state assembly — everything is declared and realize
handles instantiation, port wiring, and default filling.

Top-level entrypoint:
- ``build_ecoli_document(core, sim_config)`` — produces a composite
  document ready for ``Composite({'state': document}, core=core)``.
"""

import copy
from copy import deepcopy

import numpy as np

from bigraph_schema import (
    deep_merge, class_address as _class_address,
    make_arrays_writeable as _make_arrays_writeable,
    tuples_to_lists as _tuple_to_list,
)
from process_bigraph import wire_step_layers
from vivarium.core.engine import _StepGraph


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fill_schema_defaults(target, schema):
    """Fill target dict with default values from a vivarium ports_schema.

    Walks the schema tree looking for ``_default`` entries and sets them
    in *target* only when the key is missing.  This ensures listeners
    that read their own prior state (e.g. ``ribosome_data`` reading
    ``rRNA_initiated_TU``) have valid values before the first seeding
    ``update()`` call.
    """
    for key, spec in schema.items():
        if key.startswith('_'):
            continue
        if isinstance(spec, dict):
            if '_default' in spec:
                target.setdefault(key, spec['_default'])
            else:
                sub = target.setdefault(key, {})
                if isinstance(sub, dict):
                    _fill_schema_defaults(sub, spec)


# ---------------------------------------------------------------------------
# Document builder
# ---------------------------------------------------------------------------

def build_ecoli_document(core, sim_config):
    """Build a complete composite document from sim_config.

    The document contains:
    - Initial cell state (bulk, unique, environment) from sim_data
    - Process/step declarations (address + config + wires)
    - Step flow wiring (layer tokens and triggers)
    - Per-process runtime state (next_update_time, request, allocate)

    Returns a state dict shaped as ``{'agents': {<agent_id>: {...}}}``.
    Load with ``Composite({'state': document}, core=core)``.
    """
    from ecoli.library.sim_data import LoadSimData, RAND_MAX

    load_sim_data = LoadSimData(**sim_config)
    agent_id = sim_config.get('agent_id', '0')
    time_step = sim_config.get('time_step', 1.0)

    # 1. Resolve process configs from sim_data
    configs, classes, partitioned, partitioned_configs = _resolve_process_configs(
        load_sim_data, sim_config)

    # 2. Build topology (port → wire path mapping)
    topology = _build_topology(sim_config, partitioned, configs)

    # 3. Build flow graph (step execution order)
    flow, configs, classes = _build_flow(
        sim_config, load_sim_data, configs, classes, partitioned, time_step)

    # 3b. Extract each edge's interface (inputs/outputs) via a temporary
    # instance BEFORE configs are rewritten into serializable refs.
    # Bound-method instances must still be callable for __init__.
    # Also keep the temp instance for the edge-type classification below.
    interfaces = {}
    temp_instances = {}
    for name, cls in classes.items():
        cfg = configs[name]
        try:
            inst = cls(cfg)
            interfaces[name] = inst.interface()
            temp_instances[name] = inst
        except Exception as _err:
            import traceback as _tb
            print(f"[build_ecoli] interface() failed for {name}: {type(_err).__name__}: {_err}", flush=True)
            _tb.print_exc()
            interfaces[name] = {'inputs': {}, 'outputs': {}}
            temp_instances[name] = None

    # 4. Get initial cell state from sim_data
    cell_state = _get_initial_state(load_sim_data, sim_config)
    _make_arrays_writeable(cell_state)

    # 5. Add infrastructure topologies (allocator, unique_update)
    allocator_topology = {
        "request": ("request",),
        "allocate": ("allocate",),
        "bulk": ("bulk",),
    }
    for name in classes:
        if name.startswith("allocator_"):
            topology[name] = allocator_topology.copy()
        elif name.startswith("unique_update_"):
            # UniqueUpdate topology comes from its config's unique_topo
            unique_topology = configs[name].get("unique_topo", {})
            topology[name] = {k: v for k, v in unique_topology.items()}

    # 5b. Build sim_data_objects store FIRST — bound method refs in
    # SharedProcess and step configs need these instances at realize time.
    sd = load_sim_data.sim_data
    sim_data_objects = {}
    # Map from instance id to store key for deduplication
    _instance_to_key = {}
    _sim_data_paths = {
        'external_state': sd.external_state,
        'mass': sd.mass,
        'growth_rate_parameters': sd.growth_rate_parameters,
        'getter': sd.getter,
        'transcription': sd.process.transcription,
        'transcription_regulation': sd.process.transcription_regulation,
        'replication': sd.process.replication,
        'translation': sd.process.translation,
        'metabolism_data': sd.process.metabolism,
        'equilibrium_data': sd.process.equilibrium,
        'two_component_system': sd.process.two_component_system,
        # Nested objects that are also referenced directly in configs
        'concentration_updates': sd.process.metabolism.concentration_updates,
    }
    for key, instance in _sim_data_paths.items():
        if instance is not None:
            sim_data_objects[key] = instance
            _instance_to_key[id(instance)] = key
    sim_data_objects['_type'] = 'sim_data_object_store'
    cell_state['sim_data_objects'] = sim_data_objects

    # Now rewrite configs: replace bound methods and sim_data object
    # instances with references to the sim_data_objects store.
    def _rewrite_refs(config):
        if not isinstance(config, dict):
            return
        for key, val in list(config.items()):
            if callable(val) and hasattr(val, '__self__') and hasattr(val, '__func__'):
                inst_id = id(val.__self__)
                if inst_id in _instance_to_key:
                    config[key] = {
                        '_type': 'method',
                        'instance_path': ['sim_data_objects', _instance_to_key[inst_id]],
                        'attribute': val.__func__.__name__,
                    }
            elif id(val) in _instance_to_key:
                config[key] = {
                    '_type': 'sim_data_object_ref',
                    'store_key': _instance_to_key[id(val)],
                }

    for name, config in configs.items():
        _rewrite_refs(config)
    for name, config in partitioned_configs.items():
        _rewrite_refs(config)

    # 5c. Declare SharedProcess entries in the process store AFTER
    # sim_data_objects so realize() has bound method instances available.
    for proc_name in partitioned:
        proc_class = sim_config["processes"][proc_name]
        proc_config = partitioned_configs.get(proc_name, {})
        cell_state.setdefault('process', {})[proc_name] = {
            '_type': 'shared_process',
            'address': _class_address(proc_class),
            'config': proc_config,
        }

    # 6. Build process declarations and add to cell state
    for name, cls in classes.items():
        config = configs[name]
        edge_wires = topology.get(name, {})
        wires = _tuple_to_list(edge_wires) or {}

        # Use the interface we extracted before _rewrite_refs mutated
        # configs into serializable method refs.
        interface = interfaces.get(name, {'inputs': {}, 'outputs': {}})

        input_ports = set(interface.get('inputs', {}).keys())
        output_ports = set(interface.get('outputs', {}).keys())
        for port_name in input_ports | output_ports:
            if port_name not in wires:
                wires[port_name] = [port_name]

        output_wires = {k: v for k, v in wires.items() if k in output_ports}

        # Determine edge type: Process (continuous-time) vs Step (event-driven).
        # BigraphProcess is the vEcoli bridge; ProcessBigraphProcess is the
        # underlying process-bigraph base class — also accepted in case a
        # process is registered without the bridge.
        from ecoli.library.bigraph_bridge import BigraphProcess
        from process_bigraph import Process as ProcessBigraphProcess
        instance = temp_instances.get(name)
        if instance is not None and isinstance(instance, (BigraphProcess, ProcessBigraphProcess)) and not hasattr(instance, 'triggers'):
            edge_type = 'process'
        else:
            edge_type = 'step'

        # For the document, replace process instances in config with
        # string IDs (SharedProcessRef resolves them at realize time).
        from ecoli.processes.partition import PartitionedProcess
        doc_config = dict(config) if config else {}
        if isinstance(doc_config.get('process'), PartitionedProcess):
            doc_config['process'] = doc_config['process'].name

        decl = {
            '_type': edge_type,
            'address': _class_address(cls),
            'config': doc_config,
            '_inputs': interface.get('inputs', {}),
            '_outputs': interface.get('outputs', {}),
            'inputs': copy.deepcopy(wires),
            'outputs': copy.deepcopy(output_wires),
        }
        if edge_type == 'process':
            decl['interval'] = 1.0
        else:
            decl['priority'] = 1.0

        cell_state[name] = decl

    # global_time default is declared on global_clock.outputs() as
    # 'float{0.0}' for the framework's auto-init at realize time, but
    # the listener-seeding loop below builds views directly from
    # cell_state and bypasses the framework, so we still need the
    # cell_state seed here.
    cell_state.setdefault('global_time', 0.0)
    # timestep is config-derived (no producer process); keep setdefault.
    cell_state.setdefault('timestep', int(time_step))
    # listeners.mass.* defaults are declared in
    # ecoli/processes/listeners/mass_listener.py outputs() so the
    # framework auto-creates them on first read.

    # 8. Seed initial listener values by running all listeners once.
    # In v1, prime_listeners did this. In v2, we run the temporary
    # instances on the initial state and inject their outputs.
    # This ensures metabolism sees correct cell_mass on its first tick
    # and all listener outputs have valid initial values for analysis.
    # Order matters: mass listeners first (other processes read cell_mass),
    # then remaining listeners.
    _seed_listeners = [
        'post-division-mass-listener',
        'ecoli-mass-listener',
        'RNA_counts_listener',
        'rna_synth_prob_listener',
        'monomer_counts_listener',
        'dna_supercoiling_listener',
        'replication_data_listener',
        'rnap_data_listener',
        'ribosome_data_listener',
        'unique_molecule_counts',
    ]
    for listener_name in _seed_listeners:
        if listener_name not in classes:
            continue
        listener_cls = classes[listener_name]
        listener_config = configs[listener_name]
        listener_topo = topology.get(listener_name, {})
        try:
            listener_inst = listener_cls(listener_config)

            # Pre-populate listener sub-dicts with defaults from
            # ports_schema() so listeners that read their own prior
            # state (e.g. ribosome_data reads rRNA_initiated_TU,
            # rnap_data reads rna_init_event) don't crash.
            # Only pre-populate ports that wire into the 'listeners'
            # store — other ports like 'next_update_time' must stay
            # as scalars.
            try:
                schema = listener_inst.ports_schema()
                for port_name, port_schema in schema.items():
                    if not isinstance(port_schema, dict):
                        continue
                    wire_path = listener_topo.get(port_name)
                    if wire_path is None:
                        continue
                    if isinstance(wire_path, tuple):
                        wire_path = list(wire_path)
                    # Only pre-populate ports wiring into listeners
                    if not wire_path or wire_path[0] != 'listeners':
                        continue
                    target = cell_state
                    for seg in wire_path:
                        target = target.setdefault(seg, {})
                    _fill_schema_defaults(target, port_schema)
            except Exception:
                pass  # ports_schema() not available; proceed anyway

            # Build the view from cell_state using the topology wires
            view = {}
            for port_name, wire_path in listener_topo.items():
                if isinstance(wire_path, tuple):
                    wire_path = list(wire_path)
                cur = cell_state
                for seg in wire_path:
                    cur = cur.get(seg) if isinstance(cur, dict) else None
                    if cur is None:
                        break
                if cur is not None:
                    view[port_name] = cur
            # Run the listener once
            update = listener_inst.update(view)
            # Apply the output back to cell_state via topology
            if update:
                for port_name, port_update in update.items():
                    wire_path = listener_topo.get(port_name)
                    if wire_path is None or not isinstance(port_update, dict):
                        continue
                    if isinstance(wire_path, tuple):
                        wire_path = list(wire_path)
                    target = cell_state
                    for seg in wire_path[:-1]:
                        target = target.setdefault(seg, {})
                    if isinstance(target, dict) and wire_path:
                        slot = target.setdefault(wire_path[-1], {})
                        if isinstance(slot, dict) and isinstance(port_update, dict):
                            slot.update(port_update)
        except Exception as e:
            import traceback
            print(f"  [seed_listener] {listener_name} failed: {e}", flush=True)
            traceback.print_exc()

    # 9. Initialize per-process runtime state (except process store,
    # which was already declared in step 5b as SharedProcess entries).
    # `allocate.<proc>.bulk` is a full-size int64 array at runtime; declare
    # that explicitly so bundle() externalizes it to Parquet.
    import numpy as _np
    bulk_store = cell_state.get('bulk')
    if isinstance(bulk_store, _np.ndarray):
        n_bulk = len(bulk_store)
    elif isinstance(bulk_store, dict):
        n_bulk = len(bulk_store.get('id', []))
    else:
        n_bulk = 0
    for proc_name in partitioned:
        cell_state.setdefault('next_update_time', {}).setdefault(
            proc_name, float(time_step))
        cell_state.setdefault('request', {}).setdefault(
            proc_name, {'bulk': []})
        cell_state.setdefault('allocate', {}).setdefault(
            proc_name, {'bulk': _np.zeros(n_bulk, dtype=_np.int64)})

    # Seed allocator_rng deterministically from the Allocator config.
    # Without this, ``realize(NPRandom, None)`` falls back to
    # ``RandomState()`` (system entropy), making gen 1 non-reproducible
    # and producing a non-deterministic saved RNG state at division.
    # The Allocator schema's ``seed`` is ``lineage_seed[integer]``; the
    # config dict here holds the BASE value, so we replicate the same
    # ``(base + lineage) % RAND_MAX`` derivation the framework will
    # apply when constructing the Allocator process — keeping the
    # cell-level store in sync with the process's internal RandomState.
    from bigraph_schema.methods.derive import get_derivation_context
    allocator_seed = next(
        (cfg.get('seed') for nm, cfg in configs.items()
         if nm.startswith('allocator_') and isinstance(cfg, dict)),
        None)
    if allocator_seed is not None:
        derivation_context = get_derivation_context()
        if derivation_context is not None:
            allocator_seed = (
                int(allocator_seed) + int(derivation_context.lineage_seed)
            ) % RAND_MAX
        cell_state['allocator_rng'] = _np.random.RandomState(
            seed=int(allocator_seed))

    # 9b. For non-partitioned Steps whose topology references a
    # next_update_time store (e.g. Metabolism), initialize that store so
    # the perform_update() gate in process-bigraph's run_steps correctly
    # skips them at global_time=0.
    for proc_name, ports in topology.items():
        if proc_name in partitioned:
            continue
        nut_wire = ports.get('next_update_time') if isinstance(ports, dict) else None
        if isinstance(nut_wire, (list, tuple)) and len(nut_wire) >= 2:
            parent, key = nut_wire[0], nut_wire[1]
            cell_state.setdefault(parent, {}).setdefault(key, float(time_step))

    # 9. Wire step layers (flow tokens + triggers)
    if flow:
        wire_step_layers(cell_state, flow)

    return {'agents': {agent_id: cell_state}}


# Keep backward compat alias
build_composite_native = build_ecoli_document


def _reseed_allocator_rng(state, agent_id='0'):
    """Reset ``allocator_rng`` to a freshly-seeded RandomState after a
    bundle load.

    The bundle stores mother's advanced RandomState in the
    ``allocator_rng`` cell-level store; a daughter that replayed it
    would diverge from v1's per-generation re-seeding. This helper
    re-creates the RandomState from the Allocator's config seed,
    combined with the active ``DerivationContext.lineage_seed`` (so
    each generation gets the v1-equivalent ``(base + cli_seed) %
    RAND_MAX`` derived seed).

    Accepts either an ``{'agents': {...}}`` document or a bare cell
    dict. Mutates ``state`` in place. No-op if no allocator process
    is declared (e.g. no PartitionedProcesses in the sim) or no
    DerivationContext is installed (caller hasn't opted in to
    framework-driven derivation).
    """
    import numpy as _np
    from bigraph_schema.methods.derive import get_derivation_context
    from ecoli.library.sim_data import RAND_MAX
    if isinstance(state, dict) and 'agents' in state:
        cell = state['agents'].get(agent_id)
        if cell is None:
            cell = state['agents'][next(iter(state['agents']))]
    else:
        cell = state
    if not isinstance(cell, dict):
        return
    # Allocator Step declarations live at the top level of the cell
    # (allocator_1, allocator_2, ...), not under 'process' (which holds
    # SharedProcess declarations for PartitionedProcesses). Look at both,
    # preferring top-level since that's where Steps land.
    seed = None
    for name, decl in cell.items():
        if name.startswith('allocator_') and isinstance(decl, dict):
            seed = decl.get('config', {}).get('seed')
            if seed is not None:
                break
    if seed is None:
        for name, decl in cell.get('process', {}).items():
            if name.startswith('allocator_') and isinstance(decl, dict):
                seed = decl.get('config', {}).get('seed')
                if seed is not None:
                    break
    if seed is None:
        return
    # Saved seed is the BASE value (LineageSeed schema). Re-derive
    # against the active context so the cell-level allocator_rng store
    # matches the Allocator process's internal RandomState.
    derivation_context = get_derivation_context()
    if derivation_context is not None:
        seed = (
            int(seed) + int(derivation_context.lineage_seed)
        ) % RAND_MAX
    cell['allocator_rng'] = _np.random.RandomState(seed=int(seed))


# ---------------------------------------------------------------------------
# Config resolution
# ---------------------------------------------------------------------------

def _resolve_process_configs(load_sim_data, config):
    """Resolve process configs from sim_data without instantiation.

    Returns (configs, classes, partitioned_names) where:
    - configs: {step_name: config_dict}
    - classes: {step_name: class}
    - partitioned_names: [process_name, ...] for PartitionedProcesses
    """
    from ecoli.processes.partition import (
        PartitionedProcess, Requester, Evolver)
    from ecoli.library.sim_data import RAND_MAX

    time_step = config["time_step"]
    process_configs = {}
    for name, cfg in config["process_configs"].items():
        if cfg == "sim_data":
            process_configs[name] = load_sim_data.get_config_by_name(
                name, time_step)
        elif cfg == "default":
            process_configs[name] = None
        elif isinstance(cfg, dict):
            try:
                default = load_sim_data.get_config_by_name(name, time_step)
            except KeyError:
                default = config["processes"][name].defaults
            process_configs[name] = deepcopy(default)
            process_configs[name] = deep_merge(
                process_configs[name], cfg)
            # Per-generation seed derivation
            # ((default + config["seed"]) % RAND_MAX) is now handled at
            # realize time by the framework's LineageSeed type — the
            # stored 'seed' field is the BASE value, and the active
            # DerivationContext.lineage_seed is combined just before
            # the process constructor sees it.

    configs = {}
    classes = {}
    partitioned = []

    partitioned_configs = {}  # original configs for SharedProcess declarations

    for process_name, process_class in config["processes"].items():
        if issubclass(process_class, PartitionedProcess):
            parallel = process_configs[process_name].pop("_parallel", False)
            # Save the config for the SharedProcess declaration — share the
            # reference so bound method instances match the sim_data
            # instances in the sim_data_objects store.
            partitioned_configs[process_name] = process_configs[process_name]
            # Instantiate the PartitionedProcess (needed for Requester/Evolver config)
            process_instance = process_class(process_configs[process_name])
            req_config = {
                "time_step": time_step,
                "process": process_instance,
                "_parallel": parallel,
            }
            evo_config = {
                "time_step": time_step,
                "process": process_instance,
                "_parallel": parallel,
            }
            configs[f"{process_name}_requester"] = req_config
            configs[f"{process_name}_evolver"] = evo_config
            classes[f"{process_name}_requester"] = Requester
            classes[f"{process_name}_evolver"] = Evolver
            partitioned.append(process_name)
        else:
            configs[process_name] = process_configs.get(process_name)
            classes[process_name] = process_class

    return configs, classes, partitioned, partitioned_configs


# ---------------------------------------------------------------------------
# Topology
# ---------------------------------------------------------------------------

def _build_topology(config, partitioned, configs):
    """Build port→wire topology from config."""
    topology = {}
    for process_id, ports in config["topology"].items():
        if process_id in partitioned:
            topology[f"{process_id}_requester"] = deepcopy(ports)
            topology[f"{process_id}_evolver"] = deepcopy(ports)
            topology[f"{process_id}_requester"]["request"] = (
                "request", process_id)
            topology[f"{process_id}_evolver"]["allocate"] = (
                "allocate", process_id)
            topology[f"{process_id}_requester"]["next_update_time"] = (
                "next_update_time", process_id)
            topology[f"{process_id}_evolver"]["next_update_time"] = (
                "next_update_time", process_id)
            topology[f"{process_id}_requester"]["process"] = (
                "process", process_id)
            topology[f"{process_id}_evolver"]["process"] = (
                "process", process_id)
            topology[f"{process_id}_requester"]["global_time"] = ("global_time",)
            topology[f"{process_id}_evolver"]["global_time"] = ("global_time",)
        else:
            topology[process_id] = deepcopy(ports)

    if config.get("divide"):
        if config.get("d_period"):
            topology["mark_d_period"] = {
                "full_chromosome": tuple(config["chromosome_path"]),
                "global_time": ("global_time",),
                "divide": ("divide",),
            }
        topology["division"] = {
            "division_variable": tuple(config["division_variable"]),
            "full_chromosome": tuple(config["chromosome_path"]),
            "agents": ("..", "..", "agents"),
            "media_id": ("environment", "media_id"),
            "division_threshold": ("division_threshold",),
        }

    return topology


# ---------------------------------------------------------------------------
# Flow graph
# ---------------------------------------------------------------------------

def _build_flow(config, load_sim_data, configs, classes, partitioned, time_step):
    """Build step execution flow and add infrastructure steps."""
    from ecoli.processes.allocator import Allocator
    from ecoli.processes.unique_update import UniqueUpdate
    from ecoli.processes.cell_division import Division, MarkDPeriod

    step_graph = _StepGraph()
    step_classes = dict(classes)  # will be extended with infra steps

    for process in config["processes"]:
        deps = config["flow"].get(process, [])
        tuplified_deps = []
        for dep_path in deps:
            if dep_path[-1] in partitioned:
                tuplified_deps.append(
                    tuple(dep_path[:-1]) + (f"{dep_path[-1]}_evolver",))
            else:
                tuplified_deps.append(tuple(dep_path))
        if process in partitioned:
            step_graph.add((f"{process}_requester",), tuplified_deps)
            step_graph.add((f"{process}_evolver",),
                           [(f"{process}_requester",)])
        elif process in classes:
            step_graph.add((process,), tuplified_deps)

    layers = step_graph.get_execution_layers()
    flow = {}
    allocator_counter = 1
    unique_update_counter = 1

    for layer_steps in layers:
        requesters = False
        for step_path in layer_steps:
            if "evolver" in step_path[-1]:
                flow[step_path[-1]] = [(f"allocator_{allocator_counter - 1}",)]
            elif unique_update_counter > 1:
                flow[step_path[-1]] = [
                    (f"unique_update_{unique_update_counter - 1}",)]
                if "requester" in step_path[-1]:
                    requesters = True
            else:
                flow[step_path[-1]] = []
        if requesters:
            flow[f"allocator_{allocator_counter}"] = layer_steps
            allocator_counter += 1
        else:
            flow[f"unique_update_{unique_update_counter}"] = [step_path]
            unique_update_counter += 1

    # Allocator configs and classes
    allocator_config = load_sim_data.get_allocator_config(
        time_step, process_names=partitioned)
    allocator_topology = {
        "request": ("request",),
        "allocate": ("allocate",),
        "bulk": ("bulk",),
    }
    for i in range(1, allocator_counter):
        name = f"allocator_{i}"
        configs[name] = allocator_config
        classes[name] = Allocator

    # UniqueUpdate configs and classes
    unique_mols = (
        load_sim_data.sim_data.internal_state
    ).unique_molecule.unique_molecule_definitions.keys()
    unique_topology = {
        unique_mol + "s": ("unique", unique_mol)
        for unique_mol in unique_mols
        if unique_mol not in ["active_ribosome", "DnaA_box"]
    }
    unique_topology["active_ribosome"] = ("unique", "active_ribosome")
    unique_topology["DnaA_boxes"] = ("unique", "DnaA_box")
    unique_params = {
        "unique_topo": unique_topology,  # key matches UniqueUpdate's config
        "emit_unique": config["emit_unique"],
    }
    for i in range(1, unique_update_counter):
        name = f"unique_update_{i}"
        configs[name] = unique_params
        classes[name] = UniqueUpdate

    # Division steps
    if config.get("divide"):
        from ecoli.processes.cell_division import (
            CompositeDivision, daughter_phylogeny_id)
        # v2 uses CompositeDivision (skips the v1 Composer roundtrip —
        # the framework handles daughter state reconstruction via
        # type-driven _divide_state and Link instantiation).
        division_config = {
            "division_threshold": config["division_threshold"],
            "agent_id": config["agent_id"],
            "dry_mass_inc_dict":
                load_sim_data.sim_data.expectedDryMassIncreaseDict,
            # Base seed is 0; the framework's LineageSeed type adds the
            # active DerivationContext.lineage_seed at realize time so the
            # constructor sees the per-generation value (matches v1's
            # division.seed = config["seed"] without pre-deriving here).
            "seed": 0,
            "daughter_ids_function": daughter_phylogeny_id,
        }
        configs["division"] = division_config
        classes["division"] = CompositeDivision

        if config.get("d_period"):
            configs["mark_d_period"] = {}
            classes["mark_d_period"] = MarkDPeriod
            flow["mark_d_period"] = [
                (f"unique_update_{unique_update_counter - 1}",)]
            # Extra UniqueUpdate after MarkDPeriod
            uu_name = f"unique_update_{unique_update_counter}"
            configs[uu_name] = unique_params
            classes[uu_name] = UniqueUpdate
            flow[uu_name] = [("mark_d_period",)]
            flow["division"] = [(uu_name,)]
        else:
            flow["division"] = [
                (f"unique_update_{unique_update_counter - 1}",)]

    return flow, configs, classes


# ---------------------------------------------------------------------------
# Initial state
# ---------------------------------------------------------------------------

def _get_initial_state(load_sim_data, config):
    """Get initial cell state from sim_data."""
    from ecoli.library.json_state import get_state_from_file
    from wholecell.utils.filepath import is_cloud_uri

    full_state = config.get("initial_state", None)
    if not full_state:
        initial_state_file = config.get("initial_state_file", None)
        if not initial_state_file:
            full_state = load_sim_data.generate_initial_state()
        else:
            if is_cloud_uri(initial_state_file) or initial_state_file.startswith("/"):
                state_path = initial_state_file
            else:
                state_path = f"data/{initial_state_file}.json"
            full_state = get_state_from_file(path=state_path)

    if "agents" in full_state:
        cell_state = full_state["agents"][config.get("agent_id", "0")]
    else:
        cell_state = full_state

    # Apply overrides
    overrides = config.get("initial_state_overrides", [])
    if overrides:
        bulk_map = {
            bulk_id: row_id
            for row_id, bulk_id in enumerate(cell_state["bulk"]["id"])
        }
    for override_file in overrides:
        override = get_state_from_file(path=f"data/{override_file}.json")
        bulk_overrides = override.pop("bulk", {})
        cell_state["bulk"].flags.writeable = True
        for molecule, count in bulk_overrides.items():
            cell_state["bulk"]["count"][bulk_map[molecule]] = count
        cell_state["bulk"].flags.writeable = False
        deep_merge(cell_state, override)

    return cell_state


# ---------------------------------------------------------------------------
# Whole-cell process wrapper
# ---------------------------------------------------------------------------
#
# EcoliProcess wraps the entire vEcoli composite as a single process so it
# can be embedded in a larger simulation. The class subclasses Composite
# and uses the standard process-bigraph bridge mechanism: external inputs
# are projected into internal stores, the inner sim runs for the requested
# interval, and bridge outputs are read back out at the end.
#
# Boundary (what an outer simulation sees):
#   inputs:  external (mM concentrations), media_id (str), global_time (s)
#   outputs: exchange (counts), cell_mass (fg), dry_mass (fg), volume (L),
#            agent_id (str)
#
# Inside, the cell state is nested under ``agents.<agent_id>`` so the v2
# division machinery (CompositeDivision adds new agents under the same key)
# continues to work without changes. For multi-cell outer sims, instantiate
# multiple EcoliProcess objects with distinct ``agent_id`` values.


_DEFAULT_CONFIG_PATH = 'configs/default.json'


def _load_default_sim_config():
    """Load the vEcoli default sim config (configs/default.json).

    Cached because every EcoliProcess instance reads the same defaults.
    """
    import json
    import os
    if not hasattr(_load_default_sim_config, '_cache'):
        repo_root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        path = os.path.join(repo_root, _DEFAULT_CONFIG_PATH)
        with open(path, 'r') as f:
            _load_default_sim_config._cache = json.load(f)
    return copy.deepcopy(_load_default_sim_config._cache)


def _resolve_sim_data(sim_data_path, parca_options):
    """Ensure a sim_data pickle exists; run parca if necessary.

    Returns the path to a usable simData.cPickle.

    Resolution order:
    1. If ``sim_data_path`` is set and the file exists, return it as-is.
    2. Else if ``parca_options`` is provided, run parca with those options
       and return the path to the resulting simData.cPickle (under
       ``parca_options['outdir']/kb/``).
    3. Else raise — caller must supply one or the other.
    """
    import os
    from wholecell.utils import constants
    from wholecell.utils.filepath import is_cloud_uri

    if sim_data_path:
        if is_cloud_uri(sim_data_path) or os.path.exists(sim_data_path):
            return sim_data_path

    if not parca_options:
        raise ValueError(
            "EcoliProcess: either 'sim_data_path' must point at an "
            "existing simData.cPickle, or 'parca_options' must be set "
            "so parca can be run.")

    # Run parca via the runscript helper. It writes simData.cPickle under
    # ``<outdir>/kb/`` and returns a content hash. We don't use the hash
    # here — first cut trusts the user's outdir as the cache key.
    from runscripts.parca import run_parca

    outdir = parca_options.get('outdir', 'out')
    if not is_cloud_uri(outdir):
        outdir = os.path.abspath(outdir)
        os.makedirs(outdir, exist_ok=True)
    parca_options = dict(parca_options)
    parca_options['outdir'] = outdir
    parca_options.setdefault(
        'cache_dir',
        os.path.join(outdir, 'cache')
        if not is_cloud_uri(outdir) else os.path.join(os.getcwd(), 'parca_cache'))
    if not is_cloud_uri(parca_options['cache_dir']):
        os.makedirs(parca_options['cache_dir'], exist_ok=True)

    resolved_path = os.path.join(
        outdir, 'kb', constants.SERIALIZED_SIM_DATA_FILENAME)
    if os.path.exists(resolved_path):
        # Outdir already has a parca output — reuse it.
        return resolved_path

    print(f"[EcoliProcess] Running parca → {resolved_path}", flush=True)
    run_parca(parca_options)
    return resolved_path


def _build_inner_sim_config(user_config, sim_data_path):
    """Merge user config onto configs/default.json and resolve registries.

    Produces the dict consumed by ``build_ecoli_document``. Equivalent to
    what ``EcoliSim._run_composite`` does just before calling
    ``build_composite_native``: process names → classes, default topology,
    process_configs sourced from sim_data, etc.
    """
    sim_config = _load_default_sim_config()

    # Merge user-provided overrides on top. ``deep_merge`` mutates the
    # first arg, so we work on the defaults copy.
    for key, value in user_config.items():
        if value is None:
            continue
        if key in sim_config and isinstance(sim_config[key], dict) and isinstance(value, dict):
            deep_merge(sim_config[key], value)
        else:
            sim_config[key] = value

    sim_config['sim_data_path'] = sim_data_path

    # Resolve process names → classes, topology overrides, process configs.
    # Reuse EcoliSim's helpers so registry lookup logic stays in one place.
    from ecoli.experiments.ecoli_master_sim import EcoliSim
    sim = EcoliSim(sim_config)
    sim.processes = sim._retrieve_processes(
        sim.processes, sim.add_processes, sim.exclude_processes,
        sim.swap_processes)
    sim.topology = sim._retrieve_topology(
        sim.topology, sim.processes, sim.swap_processes, sim.log_updates)
    sim.process_configs = sim._retrieve_process_configs(
        sim.process_configs, sim.processes)
    return sim.config


def _ecoli_bridge(agent_id):
    """Bridge wires connecting external ports to internal cell stores.

    The cell state lives under ``agents.<agent_id>`` inside this Composite.
    """
    return {
        'inputs': {
            'external': ['agents', agent_id, 'boundary', 'external'],
            'media_id': ['agents', agent_id, 'environment', 'media_id'],
            'global_time': ['global_time'],
        },
        'outputs': {
            'exchange': ['agents', agent_id, 'environment', 'exchange'],
            'cell_mass': ['agents', agent_id, 'listeners', 'mass', 'cell_mass'],
            'dry_mass': ['agents', agent_id, 'listeners', 'mass', 'dry_mass'],
            'volume': ['agents', agent_id, 'listeners', 'mass', 'volume'],
        },
    }


def _ecoli_interface():
    """Declared input/output schema for the wrapped cell.

    These are the ports an outer Composite wires up. Types match the
    internal stores they bridge to (see ExchangeData and Metabolism
    schemas in ecoli/processes/{environment/exchange_data,metabolism}.py).
    """
    return {
        'inputs': {
            'external': 'map[quantity[millimolar]]',
            'media_id': 'string',
            'global_time': 'float',
        },
        'outputs': {
            'exchange': 'map[integer]',
            'cell_mass': 'float[fg]',
            'dry_mass': 'float[fg]',
            'volume': 'float[L]',
        },
    }


# Late import — Composite lives in process-bigraph and doesn't itself
# trigger any vEcoli-specific machinery, so it's safe to import at module
# top, but kept local to keep the module's import-time footprint similar
# to before this class was added.
from process_bigraph import Composite as _Composite


class EcoliProcess(_Composite):
    """Whole vEcoli model wrapped as a single process.

    Use this to embed E. coli in a larger simulation: instantiate it with
    a ``sim_data_path`` (or ``parca_options`` to run parca on demand) and
    wire its declared input/output ports to your outer composite's stores.

    Example::

        from ecoli.composites.ecoli_composite import EcoliProcess
        from process_bigraph import Composite
        from bigraph_schema import Core, BASE_TYPES

        core = Core(BASE_TYPES)
        # ... register process_bigraph + ECOLI_TYPES ...

        ecoli = EcoliProcess(
            {'sim_data_path': 'out/kb/simData.cPickle',
             'agent_id': '0', 'seed': 0},
            core=core)
        ecoli.run(60.0)  # advance 60 simulated seconds

    For embedding in a parent Composite, reference by class address
    (``local:!ecoli.composites.ecoli_composite.EcoliProcess``) and supply
    the same config_schema fields under the child's ``config`` key.
    """

    config_schema = {
        # --- sim_data sourcing (one of these is required at initialize) ---
        'sim_data_path': 'maybe[string]',
        'parca_options': 'maybe[tree[any]]',

        # --- cell identity ---
        'agent_id': 'string',
        'seed': 'integer',
        'time_step': 'float',
        'initial_global_time': 'float',

        # --- media / condition ---
        'media_id': 'string',
        'fixed_media': 'string',
        'condition': 'string',
        'mar_regulon': 'boolean',
        'amp_lysis': 'boolean',

        # --- bulk overrides on the loaded sim config ---
        # Anything in here is deep-merged onto configs/default.json before
        # build_ecoli_document is called. Use this to add antibiotics
        # processes, custom topology, etc. (mirrors the JSON-config path).
        'sim_config': 'tree[any]',

        # --- initial state options ---
        'initial_state': 'tree[any]',
        'initial_state_file': 'maybe[string]',
        'initial_state_overrides': 'list[string]',

        # --- division (handled internally if true) ---
        'divide': 'boolean',

        # --- pass-throughs to Composite ---
        'parallel_steps': 'boolean',
        'parallel_workers': 'maybe[integer]',
        'global_time_precision': 'maybe[float]',

        # --- filled by ``initialize`` before delegating to Composite ---
        'state': 'tree[node]',
        'schema': 'schema',
        'interface': {'inputs': 'schema', 'outputs': 'schema'},
        'bridge': {'inputs': 'wires', 'outputs': 'wires'},
        'run_steps_on_init': 'boolean',
    }

    def initialize(self, config=None):
        """Resolve sim_data, build the inner cell, configure the bridge."""
        cfg = self._config

        # Collapse the user-facing keys into a sim_config dict that
        # build_ecoli_document understands.
        user_config = dict(cfg.get('sim_config') or {})
        for key in (
                'agent_id', 'seed', 'time_step', 'initial_global_time',
                'fixed_media', 'condition', 'mar_regulon', 'amp_lysis',
                'initial_state', 'initial_state_file',
                'initial_state_overrides', 'divide'):
            value = cfg.get(key)
            if value is not None:
                user_config[key] = value

        sim_data_path = _resolve_sim_data(
            cfg.get('sim_data_path'), cfg.get('parca_options'))

        agent_id = str(cfg.get('agent_id') or '0')

        # Build (or load) the inner state and stuff it into self._config so
        # Composite.initialize picks it up.
        initial_state_file = cfg.get('initial_state_file')
        if initial_state_file and os.path.isdir(initial_state_file):
            # Bundle reload path. Composite.load_bundle handles document
            # parsing; we replicate a minimal subset here so realize() runs
            # against the bundle's saved state.
            from process_bigraph.bundle import load_bundle
            document = load_bundle(initial_state_file, as_numpy=True)
            loaded_state = document.get('state', {})
            # Match v1: re-seed allocator_rng at each generation start.
            _reseed_allocator_rng(loaded_state, agent_id)
            cfg['state'] = loaded_state
            cfg.setdefault('schema', document.get('schema', {}))
        else:
            inner_sim_config = _build_inner_sim_config(
                user_config, sim_data_path)
            cfg['state'] = build_ecoli_document(self.core, inner_sim_config)

        cfg['bridge'] = _ecoli_bridge(agent_id)
        cfg['interface'] = _ecoli_interface()

        super().initialize(config)


# Late import: ``os`` is used by the bundle-reload branch above.
import os

