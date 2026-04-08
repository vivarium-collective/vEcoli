"""
==================
E. coli Composite
==================

Migration utilities for running the vEcoli whole-cell model with the
process-bigraph Composite engine.

Provides:
- ``migrate_composite(core, sim)`` — convert a built EcoliSim into
  composite state
- ``seed_port_defaults(cell_state)`` — inject port defaults from
  process instances
- ``_seed_listeners(cell_state)`` — run listener steps to populate
  initial listener data
- ``_make_arrays_writeable(state)`` — ensure numpy arrays are writeable
"""

import copy
from copy import deepcopy

import numpy as np

from bigraph_schema import deep_merge, Edge as BigraphEdge
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


def _resolve_wire(cell_state, wire_path):
    """Follow a wire path through cell state to get the value."""
    if isinstance(wire_path, list) and wire_path:
        current = cell_state
        for segment in wire_path:
            if isinstance(current, dict):
                current = current.get(segment)
            else:
                return None
        return current
    elif isinstance(wire_path, dict):
        base_path = wire_path.get('_path')
        if base_path:
            result = _resolve_wire(cell_state, base_path)
            if result is not None and isinstance(result, dict):
                result = copy.copy(result)
            else:
                result = {}
        else:
            result = {}
        for sub_key, sub_path in wire_path.items():
            if sub_key == '_path':
                continue
            sub_val = _resolve_wire(cell_state, sub_path)
            if sub_val is not None:
                result[sub_key] = sub_val
        return result
    return None


def _build_view(cell_state, edge, instance):
    """Build an input view for a step from cell state and wires."""
    try:
        ports = instance.ports_schema()
    except AttributeError:
        ports = {}
    view = {}
    wires = edge.get('inputs', {})
    for port_name, wire_path in wires.items():
        resolved = _resolve_wire(cell_state, wire_path)
        if resolved is not None:
            view[port_name] = resolved
        elif port_name in ports and isinstance(ports[port_name], dict) and '_default' in ports[port_name]:
            view[port_name] = ports[port_name]['_default']
    return view


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
# State seeding
# ---------------------------------------------------------------------------

SCALAR_STATE_KEYS = {'global_time', 'timestep', 'next_update_time'}
LISTENERS_TO_SEED = ['post-division-mass-listener', 'ecoli-mass-listener']


def seed_port_defaults(cell_state):
    """Inject runtime port defaults from process instances into cell state."""
    edges = [
        (name, edge) for name, edge in list(cell_state.items())
        if isinstance(edge, dict) and 'instance' in edge
    ]
    for step_name, edge in edges:
        instance = edge['instance']
        if not hasattr(instance, 'ports_schema'):
            continue
        try:
            ports = instance.ports_schema()
        except Exception:
            continue
        wires = edge.get('inputs', {})
        for port_name, wire_path in wires.items():
            port = ports.get(port_name)
            if not isinstance(port, dict) or not isinstance(wire_path, list):
                continue
            _inject_port_default(cell_state, wire_path, port)


def _inject_port_default(state, wire_path, port_schema):
    """Recursively inject a port default along a wire path."""
    if '_default' in port_schema:
        default = port_schema['_default']
        if isinstance(default, list) and default and isinstance(default[0], list):
            try:
                default = np.array(default)
            except (ValueError, TypeError):
                pass
        target = state
        for segment in wire_path[:-1]:
            if isinstance(target, dict):
                target.setdefault(segment, {})
                target = target[segment]
            else:
                return
        if isinstance(target, dict) and wire_path:
            key = wire_path[-1]
            current = target.get(key)
            if _should_replace(current, default):
                # Convert list defaults to numpy arrays for type stability
                # in the v2 engine (processes return ndarrays, so initial
                # state should also be ndarrays to avoid numba recompilation)
                if isinstance(default, list):
                    default = np.array(default) if default else np.array([], dtype=np.float64)
                target[key] = default
    else:
        target = state
        for segment in wire_path:
            if isinstance(target, dict):
                target.setdefault(segment, {})
                target = target[segment]
            else:
                return
        if not isinstance(target, dict):
            return
        for key, subport in port_schema.items():
            if key.startswith('_') or key == '*':
                continue
            if isinstance(subport, dict):
                _inject_port_default(target, [key], subport)


def _should_replace(current, default):
    """Should we replace current state value with a port default?"""
    if current is None:
        return default is not None
    if isinstance(current, (list, tuple)) and len(current) == 0:
        return default is not None and (
            not isinstance(default, (list, tuple)) or len(default) > 0)
    if isinstance(current, dict) and len(current) == 0:
        return default is not None and (
            not isinstance(default, dict) or len(default) > 0)
    return False


def _seed_listeners(cell_state):
    """Run listener steps to populate initial listener data."""
    for step_name in LISTENERS_TO_SEED:
        edge = cell_state.get(step_name)
        if not isinstance(edge, dict) or 'instance' not in edge:
            continue
        instance = edge['instance']
        if not hasattr(instance, 'next_update'):
            continue
        _ensure_wired_paths(cell_state, edge)
        _populate_port_defaults(cell_state, edge, instance)
        try:
            view = _build_view(cell_state, edge, instance)
            timestep = instance.parameters.get('timestep', 1.0)
            update = instance.next_update(timestep, view)
            _apply_dict_updates(cell_state, edge.get('outputs', {}), update)
        except Exception:
            continue


def _ensure_wired_paths(cell_state, edge):
    wires = edge.get('outputs', {})
    for port_name, wire_path in wires.items():
        if isinstance(wire_path, list) and len(wire_path) == 1:
            key = wire_path[0]
            if key in SCALAR_STATE_KEYS:
                continue
            if key not in cell_state or cell_state[key] is None:
                cell_state[key] = {}


def _populate_port_defaults(cell_state, edge, instance):
    try:
        ports = instance.ports_schema()
    except Exception:
        return
    wires = edge.get('inputs', {})
    for port_name, wire_path in wires.items():
        if not isinstance(wire_path, list) or not wire_path:
            continue
        port = ports.get(port_name)
        if not isinstance(port, dict):
            continue
        if '_default' in port:
            target = cell_state
            for segment in wire_path[:-1]:
                if isinstance(target, dict):
                    if segment not in target or target[segment] is None:
                        target[segment] = {}
                    target = target[segment]
                else:
                    break
            if isinstance(target, dict):
                last = wire_path[-1]
                current = target.get(last)
                default = port['_default']
                if current is None or (
                    isinstance(current, (list, dict)) and len(current) == 0
                    and default is not None and (
                        not isinstance(default, (list, dict)) or len(default) > 0
                    )
                ):
                    if isinstance(default, list):
                        default = np.array(default) if default else np.array([], dtype=np.float64)
                    target[last] = default
        else:
            _inject_nested_defaults(cell_state, wire_path, port)


def _inject_nested_defaults(state, wire_path, port_schema):
    target = state
    for segment in wire_path:
        if isinstance(target, dict):
            if segment not in target or target[segment] is None:
                target[segment] = {}
            target = target[segment]
        else:
            return
    if not isinstance(target, dict):
        return
    for key, value in port_schema.items():
        if key.startswith('_'):
            continue
        if isinstance(value, dict):
            default = value.get('_default')
            current = target.get(key)
            should_fill = (
                current is None
                or key not in target
                or (isinstance(current, (list, dict)) and len(current) == 0
                    and default is not None
                    and (not isinstance(default, (list, dict)) or len(default) > 0))
            )
            if '_default' in value and should_fill:
                if isinstance(default, list):
                    default = np.array(default) if default else np.array([], dtype=np.float64)
                target[key] = default
            elif key not in target:
                target[key] = {}
                _inject_nested_defaults(target, [key], value)
            elif isinstance(target[key], dict):
                _inject_nested_defaults(target, [key], value)


def _apply_dict_updates(cell_state, output_wires, update):
    for port_name, value in update.items():
        if not isinstance(value, dict):
            continue
        wire_path = output_wires.get(port_name)
        if not isinstance(wire_path, list) or not wire_path:
            continue
        target = cell_state
        for segment in wire_path[:-1]:
            if isinstance(target, dict):
                if segment not in target:
                    target[segment] = {}
                target = target[segment]
            else:
                break
        if isinstance(target, dict):
            last = wire_path[-1]
            if last not in target:
                target[last] = {}
            if isinstance(target[last], dict):
                target[last].update(value)


# ---------------------------------------------------------------------------
# Flow ordering
# ---------------------------------------------------------------------------

def extract_flow_priorities(flow):
    """Convert v1 flow order to priority values (higher = runs first)."""
    order = list(flow.keys())
    n = len(order)
    return {step_name: float(n - i) for i, step_name in enumerate(order)}


# ---------------------------------------------------------------------------
# Process translation
# ---------------------------------------------------------------------------

def translate_processes(core, tree, topology=None, edge_type=None, step_name=None):
    """Convert vivarium process/step instances to composite edge state."""
    if isinstance(tree, BigraphEdge):
        cls = type(tree)
        tree.core = core
        if not hasattr(tree, '_config'):
            tree._config = tree.parameters
        if not hasattr(cls, 'config_schema'):
            cls.config_schema = {}

        if edge_type == 'process':
            type_name = 'process'
            state = {'interval': 1.0}
        else:
            type_name = 'step'
            state = {'priority': 1.0}

        if topology is None:
            topology = getattr(tree, 'topology', {}) or {}
        wires = list_paths(topology)

        interface = tree.interface()
        input_port_names = set(interface.get('inputs', {}).keys())
        output_port_names = set(interface.get('outputs', {}).keys())
        for port_name in input_port_names | output_port_names:
            if port_name not in wires:
                wires[port_name] = [port_name]

        # Filter outputs to only declared output ports so the dep graph
        # (which reads `outputs`) doesn't think the step writes to ports
        # it only reads. process-bigraph's build_step_network falls back
        # to `outputs` when `_dep_outputs` is unset, so we no longer need
        # a separate `_dep_outputs` key.
        # Inputs stay as all wires — processes may read from any wired
        # port, even ports that are technically declared as outputs (e.g.
        # the Allocator's read-modify-write of listeners.atp).
        outputs_wires = {k: v for k, v in wires.items() if k in output_port_names}

        state.update({
            '_type': type_name,
            'address': f'local:{cls.__name__}',
            'config': tree.parameters,
            '_inputs': tree.inputs(),
            '_outputs': tree.outputs(),
            'instance': tree,
            'inputs': copy.deepcopy(wires),
            'outputs': copy.deepcopy(outputs_wires),
        })
        return state

    elif isinstance(tree, dict):
        return {key: translate_processes(core, subtree,
                    topology[key] if topology else None,
                    edge_type=edge_type,
                    step_name=key)
                for key, subtree in tree.items()}
    else:
        return tree


# ---------------------------------------------------------------------------
# Top-level migration
# ---------------------------------------------------------------------------

def migrate_composite(core, sim):
    """Convert a built EcoliSim into a process-bigraph composite state.

    Wraps the migrated processes, steps, and initial state under
    ``{'agents': {'0': {...}}}`` so that the EcoliComposite can find the
    cell state at a known path.
    """
    processes = translate_processes(
        core, sim.ecoli.processes, sim.ecoli.topology, edge_type='process')
    steps = translate_processes(
        core, sim.ecoli.steps, sim.ecoli.topology, edge_type='step')

    cell_state = deep_merge(processes, steps)
    cell_state = deep_merge(cell_state, sim.generated_initial_state)

    # Assign priorities from the v1 flow ordering.
    flow = sim.ecoli.flow
    flat_flow = flow
    if 'agents' in flow and isinstance(flow['agents'], dict):
        agent_flow = flow['agents']
        first_key = next(iter(agent_flow), None)
        if first_key and isinstance(agent_flow[first_key], dict):
            flat_flow = agent_flow[first_key]

    if 'agents' in cell_state and isinstance(cell_state['agents'], dict):
        agent_state = cell_state['agents']
        first_key = next(iter(agent_state), None)
        if first_key and isinstance(agent_state[first_key], dict):
            target = agent_state[first_key]
        else:
            target = cell_state
    else:
        target = cell_state

    if flat_flow:
        # Assign legacy priorities (some framework code paths still consume
        # this; remove once verified unused).
        priorities = extract_flow_priorities(flat_flow)
        for step_name, priority in priorities.items():
            if isinstance(target.get(step_name), dict):
                target[step_name]['priority'] = priority
            req_name = f'{step_name}_requester'
            evo_name = f'{step_name}_evolver'
            if isinstance(target.get(req_name), dict):
                target[req_name]['priority'] = priority * 2 + 1
            if isinstance(target.get(evo_name), dict):
                target[evo_name]['priority'] = priority * 2

        # Wire steps for layer-batched execution. wire_step_layers computes
        # the topological depth of each step from the dep graph and gives
        # all steps in a layer a shared incoming/outgoing trigger token, so
        # process-bigraph's run_steps batches them and apply_updates
        # reconciles their writes atomically (matching v1 vivarium's
        # per-layer execution semantics).
        wire_step_layers(target, flat_flow)

    if 'agents' not in cell_state:
        cell_state = {'agents': {'0': cell_state}}

    return cell_state


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

    # Listener seeding: still needed until per-process initial_state()
    # methods replace the seed_port_defaults stopgap.
    _make_arrays_writeable(cell_state)
    seed_port_defaults(cell_state)
    _seed_listeners(cell_state)

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
    """Construct a process-bigraph edge dict for one process/step instance."""
    cls = type(instance)
    instance.core = core
    if not hasattr(instance, '_config'):
        instance._config = instance.parameters
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

    state.update({
        '_type': type_name,
        'address': f'local:{cls.__name__}',
        'config': instance.parameters,
        '_inputs': instance.inputs(),
        '_outputs': instance.outputs(),
        'instance': instance,
        'inputs': copy.deepcopy(wires),
        'outputs': copy.deepcopy(outputs_wires),
    })
    return state
