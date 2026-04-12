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

from bigraph_schema import deep_merge
from process_bigraph import wire_step_layers
from vivarium.core.engine import _StepGraph


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tuple_to_list(path):
    """Convert vivarium topology tuples to process-bigraph wire lists."""
    if isinstance(path, tuple):
        return list(path)
    elif isinstance(path, dict):
        return {key: _tuple_to_list(subpath) for key, subpath in path.items()}


def _make_arrays_writeable(state):
    """Recursively make all numpy arrays in state dict writeable."""
    if isinstance(state, dict):
        for key, value in state.items():
            if isinstance(value, np.ndarray):
                if not value.flags.writeable:
                    state[key] = value.copy()
                    state[key].flags.writeable = True
            elif isinstance(value, dict):
                _make_arrays_writeable(value)


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


def _class_address(cls):
    """Fully-qualified module address for realize to import."""
    return f'local:!{cls.__module__}.{cls.__name__}'


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
    from ecoli.library.sim_data import LoadSimData

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
    for name, config in configs.items():
        if not isinstance(config, dict):
            continue
        for key, val in list(config.items()):
            if callable(val) and hasattr(val, '__self__') and hasattr(val, '__func__'):
                # Bound method → method ref
                inst_id = id(val.__self__)
                if inst_id in _instance_to_key:
                    config[key] = {
                        '_type': 'method',
                        'instance_path': ['sim_data_objects', _instance_to_key[inst_id]],
                        'attribute': val.__func__.__name__,
                    }
            elif id(val) in _instance_to_key:
                # Direct sim_data object instance → object ref
                config[key] = {
                    '_type': 'sim_data_object_ref',
                    'store_key': _instance_to_key[id(val)],
                }

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

        # Determine port schemas from a temporary instance.
        # TODO: make inputs()/outputs() class methods to avoid this.
        try:
            instance = cls(config)
            interface = instance.interface()
        except Exception:
            interface = {'inputs': {}, 'outputs': {}}

        input_ports = set(interface.get('inputs', {}).keys())
        output_ports = set(interface.get('outputs', {}).keys())
        for port_name in input_ports | output_ports:
            if port_name not in wires:
                wires[port_name] = [port_name]

        output_wires = {k: v for k, v in wires.items() if k in output_ports}

        # Determine edge type: Process (continuous-time) vs Step (event-driven)
        from ecoli.library.ecoli_step import EcoliProcess
        from process_bigraph import Process as BigraphProcess
        if isinstance(instance, (EcoliProcess, BigraphProcess)) and not hasattr(instance, 'triggers'):
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

    # 7. Ensure global_time, timestep, and listeners exist for seeding.
    cell_state.setdefault('global_time', 0.0)
    cell_state.setdefault('timestep', int(time_step))
    # Pre-populate listener.mass with zeros so the mass listener
    # can read its own prior values (fold change computations).
    cell_state.setdefault('listeners', {}).setdefault('mass', {
        'cell_mass': 0.0, 'dry_mass': 0.0, 'water_mass': 0.0,
        'volume': 0.0, 'rna_mass': 0.0, 'protein_mass': 0.0,
        'rRna_mass': 0.0, 'tRna_mass': 0.0, 'mRna_mass': 0.0,
        'dna_mass': 0.0, 'smallMolecule_mass': 0.0,
        'growth': 0.0, 'instantaneous_growth_rate': 0.0,
        'protein_mass_fraction': 0.0, 'rna_mass_fraction': 0.0,
        'dry_mass_fold_change': 0.0, 'protein_mass_fold_change': 0.0,
        'rna_mass_fold_change': 0.0, 'small_molecule_fold_change': 0.0,
        'expected_mass_fold_change': 0.0, 'projection_mass': 0.0,
    })

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
    for proc_name in partitioned:
        cell_state.setdefault('next_update_time', {}).setdefault(
            proc_name, float(time_step))
        cell_state.setdefault('request', {}).setdefault(
            proc_name, {'bulk': []})
        cell_state.setdefault('allocate', {}).setdefault(
            proc_name, {'bulk': []})

    # 9. Wire step layers (flow tokens + triggers)
    if flow:
        wire_step_layers(cell_state, flow)

    return {'agents': {agent_id: cell_state}}


# Keep backward compat alias
build_composite_native = build_ecoli_document


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
            if "seed" in process_configs[name]:
                process_configs[name]["seed"] = (
                    process_configs[name]["seed"] + config["seed"]
                ) % RAND_MAX

    configs = {}
    classes = {}
    partitioned = []

    partitioned_configs = {}  # original configs for SharedProcess declarations

    for process_name, process_class in config["processes"].items():
        if issubclass(process_class, PartitionedProcess):
            parallel = process_configs[process_name].pop("_parallel", False)
            # Save the original config for the SharedProcess declaration
            partitioned_configs[process_name] = deepcopy(
                process_configs[process_name])
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
        import ecoli.composites.ecoli_master as _ecoli_master
        division_config = {
            "division_threshold": config["division_threshold"],
            "agent_id": config["agent_id"],
            "composer": _ecoli_master.Ecoli,
            "composer_config": config,
            "dry_mass_inc_dict":
                load_sim_data.sim_data.expectedDryMassIncreaseDict,
            "seed": config["seed"],
        }
        configs["division"] = division_config
        classes["division"] = Division

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
