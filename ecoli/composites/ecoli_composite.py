"""
==================
E. coli Composite
==================

Builds the vEcoli whole-cell model as a process-bigraph composite,
directly from sim_data — without going through any vivarium composer.

Top-level entrypoint:
- ``build_composite_native(core, sim_config)`` — assemble processes,
  steps, flow, topology, and initial state into a composite document
  ready for ``process_bigraph.Composite``.

Listener seeding (``seed_port_defaults`` / ``_seed_listeners``) is a
stopgap until per-process ``initial_state()`` methods land.
"""

import copy
from copy import deepcopy

import numpy as np

from bigraph_schema import deep_merge
from process_bigraph import wire_step_layers
from vivarium.core.engine import _StepGraph


# ---------------------------------------------------------------------------
# Wire / topology helpers
# ---------------------------------------------------------------------------

def list_paths(path):
    """Convert vivarium topology tuples to process-bigraph wire lists."""
    if isinstance(path, tuple):
        return list(path)
    elif isinstance(path, dict):
        return {key: list_paths(subpath) for key, subpath in path.items()}


# ---------------------------------------------------------------------------
# Array helpers
# ---------------------------------------------------------------------------

def _make_arrays_writeable(state):
    """Recursively make all numpy arrays in state dict writeable."""
    if isinstance(state, dict):
        for key, value in state.items():
            if isinstance(value, np.ndarray):
                if not value.flags.writeable:
                    state[key] = value.copy()
                    state[key].flags.writeable = True
            elif hasattr(value, 'struct_array'):
                arr = value.struct_array
                if isinstance(arr, np.ndarray) and not arr.flags.writeable:
                    value.struct_array = arr.copy()
                    value.struct_array.flags.writeable = True
            elif hasattr(value, 'flags') and hasattr(value.flags, 'writeable'):
                if not value.flags.writeable:
                    try:
                        value.flags.writeable = True
                    except ValueError:
                        state[key] = value.copy()
            elif isinstance(value, dict):
                _make_arrays_writeable(value)


# ---------------------------------------------------------------------------
# State seeding (defaults from ports_schema, listener priming)
# ---------------------------------------------------------------------------

LISTENERS_TO_SEED = ['post-division-mass-listener', 'ecoli-mass-listener']


def seed_state_from_ports(cell_state):
    """Walk each edge's ports_schema and inject _default values along
    its input wire paths into cell_state. Only fills empty/missing slots.

    External replacement for what `instance.initial_state()` would do —
    deliberately kept outside the process classes since the v1 vivarium
    engine also calls `initial_state()` and we don't want to perturb it.
    """
    for edge in list(cell_state.values()):
        if not (isinstance(edge, dict) and 'instance' in edge):
            continue
        instance = edge['instance']
        try:
            ports = instance.ports_schema()
        except (AttributeError, Exception):
            continue
        for port_name, wire_path in edge.get('inputs', {}).items():
            port = ports.get(port_name)
            if isinstance(port, dict) and isinstance(wire_path, list):
                _inject_default(cell_state, wire_path, port)


def _inject_default(state, wire_path, port_schema):
    """Recursively inject port _default values into state along wire_path.

    A port schema dict is either a leaf (has `_default`) or a nested
    namespace (subports keyed by name). Leaves write the default into
    the wire's terminal slot if currently empty/missing. Namespaces
    recurse one level deeper, treating the namespace key as another
    wire segment.
    """
    if '_default' in port_schema:
        default = port_schema['_default']
        if isinstance(default, list):
            default = (np.array(default) if default
                       else np.array([], dtype=np.float64))
        target = state
        for segment in wire_path[:-1]:
            if not isinstance(target, dict):
                return
            target = target.setdefault(segment, {})
        if isinstance(target, dict) and wire_path:
            key = wire_path[-1]
            current = target.get(key)
            # Overwrite if: missing, empty collection, or a zero/false
            # scalar that realize created from the type's default. The
            # ports_schema _default carries the config-specific value
            # (e.g. timestep=1.0) that the type system can't express.
            if isinstance(current, np.ndarray):
                should_write = current.size == 0
            elif current is None:
                should_write = True
            elif isinstance(current, (list, dict, tuple)) and len(current) == 0:
                should_write = True
            elif isinstance(current, (int, float)) and current == 0 and not isinstance(default, np.ndarray) and default != 0:
                should_write = True
            else:
                should_write = False
            if should_write:
                target[key] = default
        return

    target = state
    for segment in wire_path:
        if not isinstance(target, dict):
            return
        target = target.setdefault(segment, {})
    if not isinstance(target, dict):
        return
    for key, subport in port_schema.items():
        if key.startswith('_') or key == '*' or not isinstance(subport, dict):
            continue
        _inject_default(target, [key], subport)


def prime_listeners(cell_state):
    """Run the mass listener processes once to populate initial listener
    fields (cell_mass, dry_mass, etc.) that downstream processes read
    on the first tick. Other listeners auto-populate from defaults.
    """
    for step_name in LISTENERS_TO_SEED:
        edge = cell_state.get(step_name)
        if not (isinstance(edge, dict) and 'instance' in edge):
            continue
        instance = edge['instance']
        if not hasattr(instance, 'next_update'):
            continue
        view = {}
        for port_name, wire_path in edge.get('inputs', {}).items():
            if not isinstance(wire_path, list):
                continue
            cur = cell_state
            for seg in wire_path:
                cur = cur.get(seg) if isinstance(cur, dict) else None
                if cur is None:
                    break
            if cur is not None:
                view[port_name] = cur
        try:
            timestep = instance.parameters.get('timestep', 1.0)
            update = instance.next_update(timestep, view)
        except Exception as e:
            import traceback
            print(f"  [prime_listeners] {step_name} failed: {e}", flush=True)
            traceback.print_exc()
            continue
        for port_name, value in update.items():
            if not isinstance(value, dict):
                continue
            wire_path = edge.get('outputs', {}).get(port_name)
            if not isinstance(wire_path, list) or not wire_path:
                continue
            target = cell_state
            for seg in wire_path[:-1]:
                target = target.setdefault(seg, {})
                if not isinstance(target, dict):
                    break
            if isinstance(target, dict):
                slot = target.setdefault(wire_path[-1], {})
                if isinstance(slot, dict):
                    slot.update(value)


# ---------------------------------------------------------------------------
# Native composite builder (no vivarium composer / engine appeal)
# ---------------------------------------------------------------------------

def build_composite_native(core, sim_config):
    """Build a process-bigraph composite document directly from sim_data.

    Does **not** instantiate :py:class:`ecoli.composites.ecoli_master.Ecoli`
    or any vivarium composer. Walks the registries and ``LoadSimData``
    directly to assemble the same processes/steps/flow/topology/state
    that the v1 composer would have built — but emits a process-bigraph
    composite document, not a vivarium composite.

    Returns a state dict shaped as ``{'agents': {<agent_id>: {...}}}``,
    ready for ``Composite({'schema': {}, 'state': state}, core=core)``.
    """
    from ecoli.library.sim_data import LoadSimData

    load_sim_data = LoadSimData(**sim_config)

    processes, steps, flow, partitioned_processes = (
        _build_processes_steps_flow(load_sim_data, sim_config))
    topology = _build_full_topology(sim_config, partitioned_processes, steps)
    initial_cell_state = _build_initial_state(
        load_sim_data, sim_config, steps)

    # If multi-cell or spatial, the initial state may already be wrapped.
    agent_id = sim_config.get('agent_id', '0')
    if 'agents' in initial_cell_state:
        cell_inner = initial_cell_state['agents'][agent_id]
    else:
        cell_inner = initial_cell_state

    cell_state = {}
    cell_state.update(cell_inner)

    for name, instance in {**processes, **steps}.items():
        edge_topology = topology.get(name)
        edge_type = 'process' if name in processes else 'step'
        cell_state[name] = _build_edge(
            core, instance, edge_topology, edge_type=edge_type, step_name=name)

    _make_arrays_writeable(cell_state)

    # Initialize per-process state dicts that are read on the first tick.
    time_step = sim_config.get('time_step', 1.0)
    cell_state.setdefault('next_update_time', {})
    cell_state.setdefault('request', {})
    cell_state.setdefault('allocate', {})
    cell_state.setdefault('process', {})
    for proc_name in partitioned_processes:
        cell_state['next_update_time'].setdefault(
            proc_name, float(time_step))
        cell_state['request'].setdefault(proc_name, {'bulk': []})
        cell_state['allocate'].setdefault(proc_name, {'bulk': []})
        cell_state['process'].setdefault(proc_name, tuple())

    if flow:
        wire_step_layers(cell_state, flow)

    return {'agents': {agent_id: cell_state}}


def _build_processes_steps_flow(load_sim_data, config):
    """Build process/step instances and the flow dep graph from sim_data.

    Direct port of the build logic that lives in
    ``Ecoli.generate_processes_and_steps``, with vivarium-specific bits
    (``schema_override``, ``self.partitioned_processes``) removed.
    Returns ``(processes, steps, flow, partitioned_processes)``.
    """
    from ecoli.processes.partition import (
        PartitionedProcess, Requester, Evolver, Step,
    )
    from ecoli.processes.allocator import Allocator
    from ecoli.processes.unique_update import UniqueUpdate
    from ecoli.processes.cell_division import (
        Division, MarkDPeriod, StopAfterDivision)
    from ecoli.library.logging_tools import make_logging_process
    from ecoli.library.sim_data import RAND_MAX
    from reconstruction.ecoli.dataclasses.process.replication import MAX_TIMESTEP

    time_step = config["time_step"]
    if time_step > MAX_TIMESTEP:
        raise ValueError(
            f"Time step {time_step} is greater than the maximum time step "
            f"{MAX_TIMESTEP} defined in reconstruction/ecoli/dataclasses/"
            f"process/replication.py. Edit and re-run ParCa with a larger "
            f"maximum or use a smaller time step.")

    # Resolve "sim_data" / "default" / dict process configs into actual configs.
    # Mirrors the in-place mutation that v1 does, but on a local copy so
    # repeated calls remain idempotent.
    process_configs = {
        name: cfg for name, cfg in config["process_configs"].items()
    }
    for process in process_configs.keys():
        cfg = process_configs[process]
        if cfg == "sim_data":
            process_configs[process] = load_sim_data.get_config_by_name(
                process, time_step)
        elif cfg == "default":
            process_configs[process] = None
        elif isinstance(cfg, dict):
            try:
                default = load_sim_data.get_config_by_name(process, time_step)
            except KeyError:
                default = config["processes"][process].defaults
            process_configs[process] = deepcopy(default)
            process_configs[process] = deep_merge(
                process_configs[process], cfg)
            if "seed" in process_configs[process]:
                process_configs[process]["seed"] = (
                    process_configs[process]["seed"] + config["seed"]
                ) % RAND_MAX

    processes = {}
    steps = {}
    flow = {}
    partitioned_processes = []

    for process_name, process_class in config["processes"].items():
        if issubclass(process_class, PartitionedProcess):
            parallel = process_configs[process_name].pop("_parallel", False)
            if parallel and process_name == "ecoli-transcript-initiation":
                raise ValueError(
                    "Transcript initiation cannot be run in parallel due to "
                    "creation of unique indices in the process.")
            process = process_class(process_configs[process_name])
            evo_cls = Evolver
            req_cls = Requester
            if config["log_updates"]:
                evo_cls = make_logging_process(Evolver)
                req_cls = make_logging_process(Requester)
            steps[f"{process_name}_evolver"] = evo_cls({
                "time_step": time_step,
                "process": process,
                "_parallel": parallel,
            })
            steps[f"{process_name}_requester"] = req_cls({
                "time_step": time_step,
                "process": process,
                "_parallel": parallel,
            })
            partitioned_processes.append(process_name)
        elif issubclass(process_class, Step):
            cls = process_class
            if config["log_updates"]:
                cls = make_logging_process(cls)
            steps[process_name] = cls(process_configs[process_name])
        else:
            cls = process_class
            if config["log_updates"]:
                cls = make_logging_process(cls)
            processes[process_name] = cls(process_configs[process_name])

    # Parse flow into execution layers, inserting Allocator + UniqueUpdate.
    step_graph = _StepGraph()
    for process in config["processes"]:
        deps = config["flow"].get(process, [])
        tuplified_deps = []
        for dep_path in deps:
            if dep_path[-1] in partitioned_processes:
                tuplified_deps.append(
                    tuple(dep_path[:-1]) + (f"{dep_path[-1]}_evolver",))
            else:
                tuplified_deps.append(tuple(dep_path))
        if process in partitioned_processes:
            step_graph.add((f"{process}_requester",), tuplified_deps)
            step_graph.add((f"{process}_evolver",),
                           [(f"{process}_requester",)])
        elif process in steps:
            step_graph.add((process,), tuplified_deps)

    layers = step_graph.get_execution_layers()
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

    # Add Allocator Steps
    allocator_config = load_sim_data.get_allocator_config(
        time_step, process_names=partitioned_processes)
    for i in range(1, allocator_counter):
        steps[f"allocator_{i}"] = Allocator(allocator_config)

    # Add UniqueUpdate Steps
    unique_mols = (
        load_sim_data.sim_data.internal_state
    ).unique_molecule.unique_molecule_definitions.keys()
    unique_topo = {
        unique_mol + "s": ("unique", unique_mol)
        for unique_mol in unique_mols
        if unique_mol not in ["active_ribosome", "DnaA_box"]
    }
    unique_topo["active_ribosome"] = ("unique", "active_ribosome")
    unique_topo["DnaA_boxes"] = ("unique", "DnaA_box")
    params = {"unique_topo": unique_topo, "emit_unique": config["emit_unique"]}
    for i in range(1, unique_update_counter):
        steps[f"unique_update_{i}"] = UniqueUpdate(params)

    # Division steps
    if config["divide"]:
        # Native v2 division references the v1 Ecoli composer for daughter
        # cell construction. That's a v1-only path; if you need composite
        # division, this needs to be ported separately.
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
        steps["division"] = Division(division_config)
        if config["d_period"]:
            steps["mark_d_period"] = MarkDPeriod()
            flow["mark_d_period"] = [
                (f"unique_update_{unique_update_counter - 1}",)]
            steps[f"unique_update_{unique_update_counter}"] = UniqueUpdate(params)
            flow[f"unique_update_{unique_update_counter}"] = [("mark_d_period",)]
            flow["division"] = [(f"unique_update_{unique_update_counter}",)]
        else:
            flow["division"] = [(f"unique_update_{unique_update_counter - 1}",)]
        if config["generations"] is not None:
            processes["stop-after-division"] = StopAfterDivision()

    return processes, steps, flow, partitioned_processes


def _build_full_topology(config, partitioned_processes, steps):
    """Build the augmented topology dict from config and the step set.

    Direct port of ``Ecoli.generate_topology``.
    """
    topology = {}
    for process_id, ports in config["topology"].items():
        if process_id in partitioned_processes:
            topology[f"{process_id}_requester"] = deepcopy(ports)
            topology[f"{process_id}_evolver"] = deepcopy(ports)
            if config["log_updates"]:
                topology[f"{process_id}_evolver"]["log_update"] = (
                    "log_update", f"{process_id}_evolver")
                topology[f"{process_id}_requester"]["log_update"] = (
                    "log_update", f"{process_id}_requester")
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
            if config["log_updates"]:
                topology[process_id]["log_update"] = ("log_update", process_id)

    if config["divide"]:
        if config["d_period"]:
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
        if config["generations"] is not None:
            topology["stop-after-division"] = {"agents": ("..", "..", "agents")}

    # Add Allocator and UniqueUpdate topologies
    allocator_topo = {
        "request": ("request",),
        "allocate": ("allocate",),
        "bulk": ("bulk",),
    }
    for step_name, step in steps.items():
        if "unique_update" in step_name:
            topology[step_name] = step.unique_topo.copy()
        elif "allocator" in step_name:
            topology[step_name] = allocator_topo.copy()

    return topology


def _build_initial_state(load_sim_data, config, steps):
    """Build the cell initial state dict from sim_data.

    Direct port of ``Ecoli.initial_state``. Pulls bulk + unique molecule +
    environment + boundary state from ``LoadSimData.generate_initial_state``
    (or a saved JSON if specified), applies any user overrides, and adds
    the shared partitioned-process instance dict under ``process``.
    """
    from ecoli.library.json_state import get_state_from_file
    from wholecell.utils.filepath import is_cloud_uri

    full_initial_state = config.get("initial_state", None)
    if not full_initial_state:
        initial_state_file = config.get("initial_state_file", None)
        if not initial_state_file:
            full_initial_state = load_sim_data.generate_initial_state()
        else:
            if is_cloud_uri(initial_state_file) or initial_state_file.startswith("/"):
                state_path = initial_state_file
            else:
                state_path = f"data/{initial_state_file}.json"
            full_initial_state = get_state_from_file(path=state_path)

    if "agents" in full_initial_state:
        agent_initial_state = full_initial_state["agents"][
            config.get("agent_id", "0")]
    else:
        agent_initial_state = full_initial_state

    initial_state_overrides = config.get("initial_state_overrides", [])
    if initial_state_overrides:
        bulk_map = {
            bulk_id: row_id
            for row_id, bulk_id in enumerate(agent_initial_state["bulk"]["id"])
        }
    for override_file in initial_state_overrides:
        override = get_state_from_file(path=f"data/{override_file}.json")
        bulk_overrides = override.pop("bulk", {})
        agent_initial_state["bulk"].flags.writeable = True
        for molecule, count in bulk_overrides.items():
            agent_initial_state["bulk"]["count"][bulk_map[molecule]] = count
        agent_initial_state["bulk"].flags.writeable = False
        deep_merge(agent_initial_state, override)

    # Place shared PartitionedProcess instances under ('process',) so the
    # Requester and Evolver pair can both reach the same parameter object.
    agent_initial_state["process"] = {
        step.parameters["process"].name: (step.parameters["process"],)
        for step in steps.values()
        if "process" in step.parameters
    }
    return full_initial_state


def _build_edge(core, instance, topology, edge_type='step', step_name=None):
    """Construct a process-bigraph edge DECLARATION for one process/step.

    Returns a dict with address, config, and wires — but NO 'instance'.
    The framework's realize() step will instantiate the class from the
    address and config at Composite creation time (and at division time
    for daughter cells).

    The address uses the '!module.Class' format so local_lookup_module
    can import it directly.
    """
    cls = type(instance)
    if not hasattr(cls, 'config_schema'):
        cls.config_schema = {}

    if edge_type == 'process':
        type_name = 'process'
        state = {'interval': 1.0}
    else:
        type_name = 'step'
        state = {'priority': 1.0}

    if topology is None:
        topology = getattr(instance, 'topology', {}) or {}
    wires = list_paths(topology) or {}

    interface = instance.interface()
    input_port_names = set(interface.get('inputs', {}).keys())
    output_port_names = set(interface.get('outputs', {}).keys())
    for port_name in input_port_names | output_port_names:
        if port_name not in wires:
            wires[port_name] = [port_name]

    outputs_wires = {k: v for k, v in wires.items() if k in output_port_names}

    # Fully-qualified module path so realize() can import the class.
    module = cls.__module__
    address = f'local:!{module}.{cls.__name__}'

    state.update({
        '_type': type_name,
        'address': address,
        'config': instance.parameters,
        '_inputs': instance.inputs(),
        '_outputs': instance.outputs(),
        'inputs': copy.deepcopy(wires),
        'outputs': copy.deepcopy(outputs_wires),
    })
    return state
