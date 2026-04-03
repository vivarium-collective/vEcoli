"""
==================
E. coli Composite
==================

Runs the whole-cell E. coli model using the process-bigraph Composite
engine with implicit step dependencies derived from port wire overlap
and priority-based cycle resolution.

This module provides an alternative to the vivarium-core Engine for
running vEcoli simulations.  It takes the same process instances and
initial state produced by :py:meth:`EcoliSim.build_ecoli` and wraps
them in a process-bigraph :py:class:`Composite`.

Usage::

    sim = EcoliSim.from_file()
    sim.config['engine'] = 'composite'
    sim.build_ecoli()
    sim.run()
"""

import copy
import inspect
import functools

import numpy as np

from vivarium.core.process import Process as VivariumProcess, Step as VivariumStep

from bigraph_schema import deep_merge, Edge as BigraphEdge, get_path, allocate_core
from bigraph_schema.protocols import local_lookup_module
from process_bigraph import Composite, Process as BigraphProcess, Step as BigraphStep
from process_bigraph.composite import (
    empty_front, find_leaves, explode_path, assert_interface,
    find_step_triggers, merge_collections, build_trigger_state,
)

import ecoli.library.schema as ecoli_schema_mod
from ecoli.library.schema import UniqueNumpyUpdater, bulk_numpy_updater

from ecoli.processes.partition import Requester, Evolver


# ---------------------------------------------------------------------------
# Port classification helpers
# ---------------------------------------------------------------------------

def _get_output_ports(instance, step_name):
    """Get the set of output port names for a step instance."""
    if hasattr(instance, '_output_ports') and instance._output_ports is not None:
        return instance._output_ports
    input_only = getattr(instance, '_input_only_ports', None)
    if input_only is not None:
        # Evolver: output = all ports minus input-only
        try:
            all_ports = set(instance.ports_schema().keys())
        except Exception:
            return None
        return all_ports - input_only
    return None


def _split_dep_outputs(step_name, instance, wires):
    """Return narrowed output wires for the dependency graph.

    Full wires are always used for inputs (view building) and outputs
    (update routing).  This returns the subset used by
    build_step_network to determine which paths a step *produces*.
    """
    output_ports = _get_output_ports(instance, step_name)
    if output_ports is not None:
        dep = {k: v for k, v in wires.items() if k in output_ports}
        dep.pop('bulk_total', None)
        return dep
    # Conservative: all ports
    return dict(wires)


# ---------------------------------------------------------------------------
# V1 update helpers
# ---------------------------------------------------------------------------

def _deep_update(target, source):
    for key, value in source.items():
        if key in target and isinstance(target[key], dict) and isinstance(value, dict):
            _deep_update(target[key], value)
        else:
            target[key] = value


def apply_step_update(cell_state, edge, instance, delta, unique_updaters=None):
    """Apply a step's delta to cell_state by following output wire paths."""
    try:
        ports = instance.ports_schema()
    except Exception:
        return

    output_wires = edge.get('outputs', {})

    for port_name, update_value in delta.items():
        wire_path = output_wires.get(port_name)
        if wire_path is None:
            continue

        if isinstance(wire_path, dict):
            _apply_nested_wire_update(cell_state, wire_path, update_value)
            continue

        if not isinstance(wire_path, list) or not wire_path:
            continue

        port = ports.get(port_name, {})
        updater = port.get('_updater') if isinstance(port, dict) else None

        target = cell_state
        for segment in wire_path[:-1]:
            if isinstance(target, dict):
                if segment not in target:
                    target[segment] = {}
                target = target[segment]
            else:
                target = None
                break

        if not isinstance(target, dict):
            continue

        key = wire_path[-1]

        if updater == 'set' or port_name in ('next_update_time', 'process'):
            target[key] = update_value
        elif callable(updater):
            current = target.get(key)
            if current is not None:
                if isinstance(current, np.ndarray):
                    try:
                        current.flags.writeable = True
                    except ValueError:
                        current = current.copy()
                        current.flags.writeable = True
                        target[key] = current
                wire_key = tuple(wire_path) if isinstance(wire_path, list) else None
                if (unique_updaters and wire_key and wire_key in unique_updaters
                        and hasattr(updater, '__self__')
                        and isinstance(updater.__self__, UniqueNumpyUpdater)):
                    shared_updater = unique_updaters[wire_key]
                    result = shared_updater.updater(current, update_value)
                    if result is not current:
                        target[key] = result
                else:
                    updater(current, update_value)
        elif isinstance(update_value, dict):
            current = target.get(key)
            if isinstance(current, dict):
                _deep_update(current, update_value)
            else:
                target[key] = update_value
        elif isinstance(update_value, (int, float)):
            current = target.get(key)
            if isinstance(current, (int, float)):
                target[key] = current + update_value
            else:
                target[key] = update_value
        elif update_value is not None:
            target[key] = update_value


def _apply_nested_wire_update(cell_state, wire_dict, update_value):
    if not isinstance(update_value, dict):
        base_path = wire_dict.get('_path')
        if base_path and isinstance(base_path, list):
            _set_at_path(cell_state, base_path, update_value)
        return

    base_path = wire_dict.get('_path')
    for sub_key, sub_value in update_value.items():
        sub_wire = wire_dict.get(sub_key)
        if sub_wire is not None:
            if isinstance(sub_wire, list):
                _set_at_path(cell_state, sub_wire, sub_value)
            elif isinstance(sub_wire, dict):
                _apply_nested_wire_update(cell_state, sub_wire, sub_value)
        elif base_path and isinstance(base_path, list):
            _set_at_path(cell_state, base_path + [sub_key], sub_value)


def _set_at_path(state, path, value):
    target = state
    for segment in path[:-1]:
        if isinstance(target, dict):
            if segment not in target:
                target[segment] = {}
            target = target[segment]
        else:
            return
    if isinstance(target, dict) and path:
        key = path[-1]
        current = target.get(key)
        if isinstance(value, dict) and isinstance(current, dict):
            _deep_update(current, value)
        else:
            target[key] = value


# ---------------------------------------------------------------------------
# State view helpers
# ---------------------------------------------------------------------------

def _resolve_wire(cell_state, wire_path):
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


def fill_missing_state(state, process):
    try:
        ports = process.ports_schema()
    except Exception:
        return state
    for key, port in ports.items():
        if key.startswith('_'):
            continue
        if key not in state and isinstance(port, dict):
            if '_default' in port:
                state[key] = port['_default']
            else:
                defaults = _extract_defaults(port)
                if defaults:
                    state[key] = defaults
    return state


def _extract_defaults(schema):
    result = {}
    if not isinstance(schema, dict):
        return result
    for key, value in schema.items():
        if key.startswith('_'):
            continue
        if isinstance(value, dict):
            if '_default' in value:
                default = value['_default']
                try:
                    is_empty = default is None or (isinstance(default, (list, dict, tuple)) and len(default) == 0)
                except (TypeError, ValueError):
                    is_empty = False
                if not is_empty:
                    result[key] = default
            else:
                sub = _extract_defaults(value)
                if sub:
                    result[key] = sub
    return result


# ---------------------------------------------------------------------------
# Array / readonly helpers
# ---------------------------------------------------------------------------

def _make_arrays_writeable(state):
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


def _share_unique_updaters(cell_state, step_order):
    shared = {}
    for step_name in step_order:
        edge = cell_state.get(step_name)
        if not isinstance(edge, dict) or 'instance' not in edge:
            continue
        instance = edge['instance']
        if not hasattr(instance, 'ports_schema'):
            continue
        try:
            ports = instance.ports_schema()
        except Exception:
            continue
        output_wires = edge.get('outputs', {})
        for port_name, port in ports.items():
            if not isinstance(port, dict):
                continue
            updater = port.get('_updater')
            if updater is None or not hasattr(updater, '__self__'):
                continue
            if not isinstance(updater.__self__, UniqueNumpyUpdater):
                continue
            wire_path = output_wires.get(port_name)
            if not isinstance(wire_path, list):
                continue
            wire_key = tuple(wire_path)
            if wire_key not in shared:
                shared[wire_key] = UniqueNumpyUpdater()
    return shared


def _disable_readonly_arrays():
    if hasattr(ecoli_schema_mod.bulk_numpy_updater, '_patched'):
        return

    @functools.wraps(bulk_numpy_updater)
    def writeable_updater(current, update):
        current.flags.writeable = True
        for idx, value in update:
            current["count"][idx] += value
        return current

    writeable_updater._patched = True
    ecoli_schema_mod.bulk_numpy_updater = writeable_updater


# ---------------------------------------------------------------------------
# V1 adapter classes
# ---------------------------------------------------------------------------

class OmniStep(BigraphStep):
    """Allows v1 steps to run as v2 steps."""
    config_schema = {}
    _ports = {"inputs": [], "outputs": []}

    def __init__(self, parameters=None, config=None, core=None):
        parameters = parameters or config
        config = config or parameters
        super().__init__(config=config, core=core)

    def inputs(self):
        try:
            return {k: 'node' for k in self.ports_schema() if not k.startswith('_')}
        except Exception:
            return {}

    def outputs(self):
        try:
            return {k: 'node' for k in self.ports_schema() if not k.startswith('_')}
        except Exception:
            return {}

    def initial_state(self, config=None):
        return {}

    def update(self, state, interval=None):
        if hasattr(self, 'next_update'):
            timestep = interval if interval and interval > 0 else self.parameters.get('timestep', 1.0)
            state = fill_missing_state(state, self)
            self.next_update(timestep, state)
        return {}


class OmniProcess(BigraphProcess):
    """Allows v1 processes to run as v2 processes."""
    config_schema = {}
    _ports = {"inputs": [], "outputs": []}

    def __init__(self, parameters=None, config=None, core=None):
        parameters = parameters or config
        config = config or parameters
        super().__init__(config=config, core=core)

    def inputs(self):
        try:
            return {k: 'node' for k in self.ports_schema() if not k.startswith('_')}
        except Exception:
            return {}

    def outputs(self):
        try:
            return {k: 'node' for k in self.ports_schema() if not k.startswith('_')}
        except Exception:
            return {}

    def initial_state(self, config=None):
        return {}

    def calculate_timestep(self, interval_or_state, state=None):
        if state is None:
            return self.parameters.get('timestep', 1.0)
        return interval_or_state

    def update(self, state, interval):
        state = fill_missing_state(state, self)
        delta = self.next_update(interval, state)
        return delta if delta else {}


# ---------------------------------------------------------------------------
# Migration pipeline
# ---------------------------------------------------------------------------

def update_inheritance(cls, new_base, core):
    if new_base in cls.__bases__:
        return
    cls.__bases__ = cls.__bases__ + (new_base,)
    original_init = cls.__init__
    captured_core = core

    def new_init(self, config=None, core=None, parameters=None):
        config = config or parameters
        parameters = parameters or config
        if core is None:
            core = captured_core
        try:
            original_init(self, parameters=parameters)
        except Exception as e:
            raise RuntimeError(f"Failed to initialize {cls.__name__}: {e}") from e
        self._config = config or {}
        new_base.__init__(self, config=config, parameters=parameters, core=core)

    cls.__init__ = new_init

    if not hasattr(cls, '_omni_patched'):
        cls.initial_state = new_base.initial_state
        cls.update = new_base.update
        if hasattr(new_base, 'calculate_timestep'):
            cls.calculate_timestep = new_base.calculate_timestep
        cls._omni_patched = True


def find_instances(module, visited=None):
    steps = {}
    processes = {}
    visited = visited or set()
    for key in dir(module):
        value = getattr(module, key)
        if not isinstance(value, type):
            if inspect.ismodule(value) and value.__name__.startswith('ecoli') and value not in visited:
                visited.add(value)
                substeps, subprocesses = find_instances(value, visited)
                steps.update(substeps)
                processes.update(subprocesses)
            continue
        if value in (VivariumStep, VivariumProcess):
            continue
        if issubclass(value, VivariumStep):
            steps[key] = value
        elif issubclass(value, VivariumProcess):
            processes[key] = value
    return steps, processes


def scan_processes(path):
    module = local_lookup_module(path)
    steps, processes = find_instances(module)
    return {'processes': processes, 'steps': steps}


def update_processes(core, processes):
    for name, cls in processes.get('processes', {}).items():
        update_inheritance(cls, OmniProcess, core)
        cls.core = core
        core.register_link(name, cls)
    for name, cls in processes.get('steps', {}).items():
        update_inheritance(cls, OmniStep, core)
        cls.core = core
        core.register_link(name, cls)
    return core


def scan_update(core, path):
    processes = scan_processes(path)
    return update_processes(core, processes)


def list_paths(path):
    if isinstance(path, tuple):
        return list(path)
    elif isinstance(path, dict):
        return {key: list_paths(subpath) for key, subpath in path.items()}


def translate_processes(core, tree, topology=None, edge_type=None, step_name=None):
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

        # Ensure all ports from inputs()/outputs() have wires.
        # Ports missing from the composer topology get default wires
        # (port name -> same-named state path).
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
# Initial state seeding
# ---------------------------------------------------------------------------

SCALAR_STATE_KEYS = {'global_time', 'timestep', 'next_update_time'}
LISTENERS_TO_SEED = ['post-division-mass-listener', 'ecoli-mass-listener']


def seed_port_defaults(cell_state):
    """Inject runtime port defaults from process instances into cell state.

    Walks each step/process instance's ports_schema(), follows the
    wires, and injects _default values into the state wherever
    the current value is empty or missing. This ensures that state
    paths created by realize() have the correct shapes from simData.
    """
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
        # Convert nested lists to numpy arrays to avoid object dtype issues
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
                target[key] = default
    else:
        # Nested port — recurse into sub-keys
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
                # Overwrite if missing, None, or empty (realize fills
                # with empty defaults that lack proper shapes)
                if current is None or (
                    isinstance(current, (list, dict)) and len(current) == 0
                    and default is not None and (
                        not isinstance(default, (list, dict)) or len(default) > 0
                    )
                ):
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
            # Fill if missing, None, or empty with a non-empty default
            should_fill = (
                current is None
                or key not in target
                or (isinstance(current, (list, dict)) and len(current) == 0
                    and default is not None
                    and (not isinstance(default, (list, dict)) or len(default) > 0))
            )
            if '_default' in value and should_fill:
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
    order = list(flow.keys())
    n = len(order)
    return {step_name: float(n - i) for i, step_name in enumerate(order)}


# ---------------------------------------------------------------------------
# EcoliComposite
# ---------------------------------------------------------------------------

class EcoliComposite(Composite):
    """Composite subclass with v1-compatible step execution."""

    def __init__(self, config=None, core=None):
        self._unique_updaters = None
        self._cell_path = None

        # Standard Composite init: calls core.realize() which populates
        # state from port defaults along wires, then builds step network.
        super().__init__(config, core=core)

        _make_arrays_writeable(self.state)
        _disable_readonly_arrays()

        # Find the cell state path (agents/0)
        for path_key, substates in self.state.items():
            if isinstance(substates, dict) and path_key != 'global_time':
                for subkey in substates:
                    if isinstance(substates[subkey], dict) and len(substates[subkey]) > 10:
                        self._cell_path = (path_key, subkey)
                        break
            if self._cell_path:
                break

        if self._cell_path:
            _seed_listeners(get_path(self.state, self._cell_path))

    def _setup_emitter(self):
        from process_bigraph.emitter import RAMEmitter

        if not self._cell_path:
            return

        cell_state = get_path(self.state, self._cell_path)

        emit_wires = {
            'global_time': ['global_time'],
            'protein_mass': ['listeners', 'mass', 'protein_mass'],
            'dry_mass': ['listeners', 'mass', 'dry_mass'],
            'rRna_mass': ['listeners', 'mass', 'rRna_mass'],
            'tRna_mass': ['listeners', 'mass', 'tRna_mass'],
            'mRna_mass': ['listeners', 'mass', 'mRna_mass'],
            'dna_mass': ['listeners', 'mass', 'dna_mass'],
            'smallMolecule_mass': ['listeners', 'mass', 'smallMolecule_mass'],
        }

        emit_schema = {port: 'node' for port in emit_wires}
        emitter = RAMEmitter({'emit': emit_schema}, self.core)

        cell_state['emitter'] = {
            '_type': 'step',
            'address': 'local:RAMEmitter',
            'config': {'emit': emit_schema},
            'inputs': emit_wires,
            'outputs': {},
            '_dep_outputs': {},
            'instance': emitter,
            'priority': 0.0,
        }

        self.emitter = emitter
        self.find_instance_paths(self.state)
        self.build_step_network()

    def _init_from_migrated(self, config, core):
        self.core = core
        self._config = {}
        self._composition = 'edge'
        self._state = {}

        self.schema = config.get('schema', {})
        self.state = config.get('state', {})
        self.bridge = config.get('bridge', {})

        if 'global_time' not in self.state:
            self.state['global_time'] = 0.0

        self.front = {}
        self.find_instance_paths(self.state)
        self.edge_paths = {**self.process_paths, **self.step_paths}

        self.process_schema = {port: {} for port in ['inputs', 'outputs']}
        self.global_time_precision = config.get('global_time_precision')

        self.front = {
            path: empty_front(self.state['global_time'])
            for path in self.process_paths}

        self.bridge_updates = []

        self._view_cache = {}
        self._project_cache = {}
        self._fast_view_paths = {}
        self._fast_project_paths = {}
        self._compiled_projects = {}
        self._compiled_links = {}

        self.build_step_network()
        self._reset_process_state()

    def _reset_process_state(self):
        if not self._cell_path:
            return
        cell_state = get_path(self.state, self._cell_path)
        for key, val in cell_state.items():
            if not isinstance(val, dict) or 'instance' not in val:
                continue
            instance = val['instance']
            for attr in list(vars(instance).keys()):
                if attr.endswith('_idx') or attr == 'molecule_idx' or attr == 'bulk_idx':
                    setattr(instance, attr, None)
            if hasattr(instance, 'parameters') and 'process' in instance.parameters:
                proc = instance.parameters['process']
                if hasattr(proc, 'request_set'):
                    proc.request_set = False
                for attr in list(vars(proc).keys()):
                    if attr.endswith('_idx') or attr == 'molecule_idx' or attr == 'bulk_idx':
                        setattr(proc, attr, None)

    def build_step_network(self):
        """Build step network with self-loop dependencies included."""
        self.step_triggers = {}
        self.star_triggers = {}

        for step_path, step in self.step_paths.items():
            step_triggers = find_step_triggers(step_path, step)
            self.step_triggers = merge_collections(self.step_triggers, step_triggers)

        for trigger_path in self.step_triggers:
            if "*" in trigger_path:
                self.star_triggers[trigger_path] = self.step_triggers[trigger_path]

        self.steps_run = set()

        ancestors = {
            step_key: {'input_paths': None, 'output_paths': None, 'priority': None}
            for step_key in self.step_paths
        }
        nodes = {}

        for step_key, step in self.step_paths.items():
            schema = step['instance'].interface()
            assert_interface(schema)

            if ancestors[step_key]['input_paths'] is None:
                ancestors[step_key]['input_paths'] = find_leaves(
                    step['inputs'], path=step_key[:-1])
            if ancestors[step_key]['output_paths'] is None:
                dep_out = step.get('_dep_outputs', step.get('outputs', {}))
                ancestors[step_key]['output_paths'] = find_leaves(
                    dep_out, path=step_key[:-1])
            if ancestors[step_key]['priority'] is None:
                ancestors[step_key]['priority'] = step.get('priority', 0.0)

            input_paths = ancestors[step_key]['input_paths'] or []
            output_paths = ancestors[step_key]['output_paths'] or []

            for input_path in input_paths:
                path = tuple(input_path)
                nodes.setdefault(path, {'before': set(), 'after': set()})
                nodes[path]['after'].add(step_key)

            for output_path in output_paths:
                exploded_path = explode_path(output_path)[1:]
                for explode in exploded_path:
                    path = tuple(explode)
                    nodes.setdefault(path, {'before': set(), 'after': set()})
                    nodes[path]['before'].add(step_key)

        self.step_dependencies = ancestors
        self.node_dependencies = nodes

        self.reset_step_state(self.step_paths)
        self.to_run = self.cycle_step_state()
        self.clean_front(self.state)

    def _ensure_unique_updaters(self):
        if self._unique_updaters is not None:
            return
        if self._cell_path:
            cell_state = get_path(self.state, self._cell_path)
            step_names = [k for k, v in cell_state.items()
                          if isinstance(v, dict) and 'instance' in v]
            self._unique_updaters = _share_unique_updaters(cell_state, step_names)
        else:
            self._unique_updaters = {}

    def expire_process_paths(self, update_paths):
        pass

    def apply_updates(self, updates):
        if self._cell_path:
            cell_state = get_path(self.state, self._cell_path)
            cell_state['global_time'] = self.state.get('global_time', 0.0)
            return [self._cell_path + ('global_time',)]
        return []

    def run_steps(self, step_paths):
        print(f'  [EcoliComposite.run_steps] {len(step_paths) if step_paths else 0} steps', flush=True)
        if not step_paths:
            self.steps_run = set()
            return

        self._ensure_unique_updaters()

        update_paths = []
        for step_path in step_paths:
            step = get_path(self.state, step_path)
            if not isinstance(step, dict) or 'instance' not in step:
                continue

            instance = step['instance']
            cell_state_path = step_path[:-1]
            cell_state = get_path(self.state, cell_state_path)
            _make_arrays_writeable(cell_state)

            try:
                view = _build_view(cell_state, step, instance)
                if hasattr(instance, 'next_update'):
                    view = fill_missing_state(view, instance)
                    timestep = instance.parameters.get('timestep', 1.0)
                    delta = instance.next_update(timestep, view)
                else:
                    delta = instance.update(view)

                if delta:
                    apply_step_update(cell_state, step, instance, delta,
                                      unique_updaters=self._unique_updaters)

                output_wires = step.get('outputs', {})
                for port_name, wire_path in output_wires.items():
                    if isinstance(wire_path, list) and wire_path:
                        full_path = tuple(list(cell_state_path) + wire_path)
                        update_paths.append(full_path)
                    elif isinstance(wire_path, dict):
                        base = wire_path.get('_path', [])
                        if base:
                            full_path = tuple(list(cell_state_path) + base)
                            update_paths.append(full_path)
            except Exception:
                pass

        self.expire_process_paths(update_paths)
        to_run = self.cycle_step_state()

        if to_run:
            self.run_steps(to_run)
        else:
            self.steps_run = set()


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

    # The flow may be nested (flow['agents']['0'][step_name]) or flat
    # (flow[step_name]).  Normalise to a flat step→deps dict.
    flat_flow = flow
    if 'agents' in flow and isinstance(flow['agents'], dict):
        agent_flow = flow['agents']
        first_key = next(iter(agent_flow), None)
        if first_key and isinstance(agent_flow[first_key], dict):
            flat_flow = agent_flow[first_key]

    # Similarly, the cell state may already be nested under agents/0 or flat.
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

    # Ensure agents/0 nesting exists for EcoliComposite._cell_path detection
    if 'agents' not in cell_state:
        cell_state = {'agents': {'0': cell_state}}

    return cell_state
