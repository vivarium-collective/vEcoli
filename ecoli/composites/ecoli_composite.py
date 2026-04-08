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

import numpy as np

from bigraph_schema import deep_merge, Edge as BigraphEdge


# ---------------------------------------------------------------------------
# Port classification helpers
# ---------------------------------------------------------------------------

def _get_output_ports(instance, step_name):
    """Get the set of output port names for a step instance."""
    if hasattr(instance, '_output_ports') and instance._output_ports is not None:
        return instance._output_ports
    input_only = getattr(instance, '_input_only_ports', None)
    if input_only is not None:
        try:
            all_ports = set(instance.ports_schema().keys())
        except Exception:
            return None
        return all_ports - input_only
    return None


def _split_dep_outputs(step_name, instance, wires):
    """Return narrowed output wires for the dependency graph."""
    output_ports = _get_output_ports(instance, step_name)
    if output_ports is not None:
        dep = {k: v for k, v in wires.items() if k in output_ports}
        dep.pop('bulk_total', None)
        return dep
    return dict(wires)


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
        for port_key in ['inputs', 'outputs']:
            for port_name in interface.get(port_key, {}):
                if port_name not in wires:
                    wires[port_name] = [port_name]

        dep_output_wires = _split_dep_outputs(step_name or cls.__name__, tree, wires)

        state.update({
            '_type': type_name,
            'address': f'local:{cls.__name__}',
            'config': tree.parameters,
            '_inputs': tree.inputs(),
            '_outputs': tree.outputs(),
            'instance': tree,
            'inputs': copy.deepcopy(wires),
            'outputs': copy.deepcopy(wires),
            '_dep_outputs': copy.deepcopy(dep_output_wires)})
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

        # Layer-based flow chain (mirrors v1's vivarium execution layers).
        #
        # Steps with no remaining unsatisfied deps form layer 0; each subsequent
        # layer contains steps whose deps are all in earlier layers. Steps in
        # the same layer read from the SAME incoming token and write to the
        # SAME outgoing token, so the dep graph shows them as independent
        # within a layer.
        #
        # Why this matters: process-bigraph's run_steps() batches all
        # currently-runnable steps, computes their updates without applying
        # in between, and then reconciles + applies the whole batch atomically.
        # That gives us v1's per-layer atomicity (every step in a layer sees
        # the same starting state). A linear chain defeats this because each
        # step is ready only after the previous one finishes.
        flow_levels = {}  # step_name -> integer level
        for step_name in flat_flow.keys():
            deps = flat_flow.get(step_name) or []
            if not deps:
                flow_levels[step_name] = 0
                continue
            max_dep_level = -1
            for dep_path in deps:
                dep_name = dep_path[-1] if isinstance(dep_path, (list, tuple)) else dep_path
                if dep_name in flow_levels:
                    max_dep_level = max(max_dep_level, flow_levels[dep_name])
            flow_levels[step_name] = max_dep_level + 1

        layers = {}  # level -> list of step names
        for step_name, level in flow_levels.items():
            layers.setdefault(level, []).append(step_name)

        target['_flow'] = {}
        for level in sorted(layers.keys()):
            target['_flow'][f'_layer_{level}'] = 0

        for level in sorted(layers.keys()):
            in_token = f'_layer_{level - 1}' if level > 0 else None
            out_token = f'_layer_{level}'
            for name in layers[level]:
                step = target.get(name)
                if not isinstance(step, dict) or 'instance' not in step:
                    continue
                if in_token is not None:
                    step['inputs']['_flow_in'] = ['_flow', in_token]
                    step['_triggers'] = {'_flow_in': 'integer'}
                else:
                    step['_triggers'] = {'global_time': 'float'}
                step['outputs']['_flow_out'] = ['_flow', out_token]
                step['_dep_outputs']['_flow_out'] = ['_flow', out_token]

    if 'agents' not in cell_state:
        cell_state = {'agents': {'0': cell_state}}

    return cell_state
