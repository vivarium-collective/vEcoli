"""
======================
Partitioning Processes
======================

This bundle of processes includes Requester, Evolver, and PartitionedProcess.
PartitionedProcess is the inherited base class for all Processes that can be
partitioned; these processes provide calculate_request or evolve_state methods,
rather than the usual Process next_update.

A PartitionedProcess can be passed into a Requester and Evolver, which call its
calculate_request and evolve_state methods in coordination with an Allocator process,
which reads the requests and allocates molecular counts for the evolve_state.

"""

import abc
import warnings

import numpy as np

from ecoli.library.ecoli_step import EcoliStep as Step, EcoliProcess as Process
from ecoli.library.schema_types import UNIQUE_TYPES
from vivarium.library.dict_utils import deep_merge

from ecoli.processes.registries import topology_registry


def _typed_ports(ports_schema):
    """Convert ports_schema keys to typed port dict using known types."""
    result = {}
    for key in ports_schema:
        if key.startswith('_'):
            continue
        if key in ('bulk', 'bulk_total'):
            result[key] = 'bulk_array'
        elif key in UNIQUE_TYPES:
            result[key] = UNIQUE_TYPES[key]
        elif key == 'global_time':
            result[key] = 'float'
        elif key == 'timestep':
            result[key] = 'integer'
        elif key == 'next_update_time':
            result[key] = 'float'
        elif key in ('request', 'allocate', 'process', 'listeners'):
            result[key] = 'node'
        else:
            result[key] = 'node'
    return result


class Requester(Step):
    """Requester Step

    Accepts a PartitionedProcess as an input, and runs in coordination with an
    Evolver that uses the same PartitionedProcess.

    process-bigraph interface: Requesters read from all ports but only
    write to request, process, next_update_time, and optionally listeners.
    """

    config_schema = {
        'process': 'shared_process_ref',
        'time_step': 'float{1.0}',
        '_parallel': 'boolean{false}',
        'name': 'string',
    }

    def inputs(self):
        process = self.config.get("process")
        ports = process.inputs()
        timestep = process.parameters.get('timestep', 1) if process else 1
        # Requester also reads these control ports
        ports['global_time'] = 'float{0.0}'
        ports['timestep'] = f'integer{{{timestep}}}'
        ports['next_update_time'] = f'float{{{float(timestep)}}}'
        ports['process'] = 'quote'
        return ports

    def outputs(self):
        process = self.config.get("process")
        result = {
            'request': {'_type': 'overwrite[map[list[integer]]]', '_default': {}},
            'process': 'quote',
            'next_update_time': 'overwrite[float]',
        }
        # Pull the actual listener schema from the wrapped process so
        # per-field types are preserved (not flattened to map[...]).
        if process is not None:
            proc_outputs = process.outputs()
            listeners = proc_outputs.get('listeners')
            if listeners:
                result['listeners'] = listeners
            for key in proc_outputs:
                if key not in result and key not in ('bulk', 'bulk_total', 'listeners'):
                    result[key] = proc_outputs[key]
        return result

    def __init__(self, parameters=None, core=None):
        assert isinstance(parameters["process"], PartitionedProcess)
        if parameters["process"].parallel:
            raise RuntimeError("PartitionedProcess objects cannot be parallelized.")
        parameters["name"] = f"{parameters['process'].name}_requester"
        super().__init__(parameters, core=core)
        # Cache the request port keys — always just ['bulk'] for
        # the standard partition setup. Previously set as a side
        # effect of ports_schema(); now initialized eagerly.
        self.cached_bulk_ports = ['bulk']

    def perform_update(self, states):
        """v2 gate: only run when next_update_time <= global_time."""
        next_t = states.get("next_update_time")
        global_t = states.get("global_time")
        if next_t is None or global_t is None:
            return True  # missing ports — run by default
        return next_t <= global_t

    def update_condition(self, timestep, states):
        """v1 gate: same logic as perform_update, with warning."""
        if states["next_update_time"] <= states["global_time"]:
            if states["next_update_time"] < states["global_time"]:
                warnings.warn(
                    f"{self.name} updated at t="
                    f"{states['global_time']} instead of t="
                    f"{states['next_update_time']}. Decrease the "
                    "timestep of the global_clock process for more "
                    "accurate timekeeping."
                )
            return True
        return False

    def ports_schema(self):
        process = self.parameters.get("process")
        ports = process.get_schema()
        ports["request"] = {
            "bulk": {
                "_updater": "set",
                "_divider": "null",
                "_emit": False,
            }
        }
        ports["process"] = {
            "_default": tuple(),
            "_updater": "set",
            "_divider": "null",
            "_emit": False,
        }
        ports["global_time"] = {"_default": 0.0}
        ports["timestep"] = {"_default": process.parameters["timestep"]}
        ports["next_update_time"] = {
            "_default": process.parameters["timestep"],
            "_updater": "set",
            "_divider": "set",
        }
        self.cached_bulk_ports = list(ports["request"].keys())
        return ports

    def update(self, states, interval=None):
        proc_state = states.get("process")
        if proc_state is None or (isinstance(proc_state, (list, tuple)) and len(proc_state) == 0):
            return {}
        process = proc_state[0] if isinstance(proc_state, (list, tuple)) else proc_state
        request = process.calculate_request(states["timestep"], states)
        process.request_set = True

        request["request"] = {}
        # Send bulk requests through request port
        for bulk_port in self.cached_bulk_ports:
            bulk_request = request.pop(bulk_port, None)
            if bulk_request is not None:
                request["request"][bulk_port] = bulk_request

        # Ensure listeners are updated if present
        listeners = request.pop("listeners", None)
        if listeners is not None:
            request["listeners"] = listeners

        # Update shared process instance
        request["process"] = (process,)
        return request


class Evolver(Step):
    """Evolver Step

    Accepts a PartitionedProcess as an input, and runs in coordination with an
    Requester that uses the same PartitionedProcess.

    process-bigraph interface: Evolvers read from all ports but only
    write to everything except allocate, global_time, and timestep.
    """

    _input_only_ports = {'allocate', 'global_time', 'timestep'}

    config_schema = {
        'process': 'shared_process_ref',
        'time_step': 'float{1.0}',
        '_parallel': 'boolean{false}',
        'name': 'string',
    }

    def inputs(self):
        process = self.config.get("process")
        ports = process.inputs()
        timestep = process.parameters.get('timestep', 1) if process else 1
        # Evolver also reads these control ports
        ports['allocate'] = 'node'
        ports['global_time'] = 'float{0.0}'
        ports['timestep'] = f'integer{{{timestep}}}'
        ports['next_update_time'] = f'float{{{float(timestep)}}}'
        ports['process'] = 'quote'
        return ports

    def outputs(self):
        process = self.parameters.get("process")
        ports = process.outputs()
        # Evolver writes next_update_time and process in addition to
        # whatever the wrapped process declares.
        ports['next_update_time'] = 'overwrite[float]'
        ports['process'] = 'quote'
        # Evolver doesn't write to allocate, global_time, timestep
        for k in ('allocate', 'global_time', 'timestep'):
            ports.pop(k, None)
        return ports

    def __init__(self, parameters=None, core=None):
        assert isinstance(parameters["process"], PartitionedProcess)
        parameters["name"] = f"{parameters['process'].name}_evolver"
        super().__init__(parameters, core=core)

    def perform_update(self, states):
        """v2 gate: only run when next_update_time <= global_time."""
        next_t = states.get("next_update_time")
        global_t = states.get("global_time")
        if next_t is None or global_t is None:
            return True  # missing ports — run by default
        return next_t <= global_t

    def update_condition(self, timestep, states):
        """v1 gate: same logic as perform_update, with warning."""
        if states["next_update_time"] <= states["global_time"]:
            if states["next_update_time"] < states["global_time"]:
                warnings.warn(
                    f"{self.name} updated at t="
                    f"{states['global_time']} instead of t="
                    f"{states['next_update_time']}. Decrease the "
                    "timestep for the global clock process for more "
                    "accurate timekeeping."
                )
            return True
        return False

    def ports_schema(self):
        process = self.parameters.get("process")
        ports = process.get_schema()
        ports["allocate"] = {
            "bulk": {
                "_updater": "set",
                "_divider": "null",
                "_emit": False,
            }
        }
        ports["process"] = {
            "_default": tuple(),
            "_updater": "set",
            "_divider": "null",
            "_emit": False,
        }
        ports["global_time"] = {"_default": 0.0}
        ports["timestep"] = {"_default": process.parameters["timestep"]}
        ports["next_update_time"] = {
            "_default": process.parameters["timestep"],
            "_updater": "set",
            "_divider": "set",
        }
        return ports

    def update(self, states, interval=None):
        allocations = states.pop("allocate")
        for key, value in allocations.items():
            if isinstance(value, list):
                value = np.array(value)
            states[key] = value
        proc_state = states.get("process")
        if proc_state is None or (isinstance(proc_state, (list, tuple)) and len(proc_state) == 0):
            return {}
        process = proc_state[0] if isinstance(proc_state, (list, tuple)) else proc_state

        # If the Requester has not run yet, skip the Evolver's update to
        # let the Requester run in the next time step.
        if not process.request_set:
            return {}

        update = process.evolve_state(states["timestep"], states)
        update["process"] = (process,)
        update["next_update_time"] = states["global_time"] + states["timestep"]
        return update


class PartitionedProcess(Process):
    """Partitioned Process Base Class

    This is the base class for all processes whose updates can be partitioned.

    Subclasses must implement:
      - ``ports_schema()``: v1 bidirectional port schema
      - ``calculate_request(timestep, states)``: compute resource requests
      - ``evolve_state(timestep, states)``: compute state updates

    Subclasses may define ``_output_ports`` as a set of port names that
    appear in the delta returned by ``evolve_state()``.  All other ports
    are treated as input-only for the dependency graph.

    For v2, subclasses should override ``inputs()`` and ``outputs()`` to
    declare typed ports. The default implementations derive from
    ``ports_schema()`` using ``_typed_ports()``.
    """

    _output_ports = None
    _input_only_ports = None

    def __init__(self, parameters=None):
        super().__init__(parameters)

        # set partition mode
        self.evolve_only = self.parameters.get("evolve_only", False)
        self.request_only = self.parameters.get("request_only", False)
        self.request_set = False

        # register topology
        assert self.name
        assert self.topology
        topology_registry.register(self.name, self.topology)

    @abc.abstractmethod
    def ports_schema(self):
        return {}

    def inputs(self):
        """All ports are inputs (process reads from all of them)."""
        return _typed_ports(self.ports_schema())

    def outputs(self):
        """Output ports — what evolve_state actually writes to.

        Uses _output_ports if defined, otherwise derives from
        _input_only_ports. Falls back to all ports.
        """
        typed = _typed_ports(self.ports_schema())
        if self._output_ports is not None:
            return {k: v for k, v in typed.items() if k in self._output_ports}
        if self._input_only_ports is not None:
            return {k: v for k, v in typed.items() if k not in self._input_only_ports}
        return typed

    @abc.abstractmethod
    def calculate_request(self, timestep, states):
        return {}

    @abc.abstractmethod
    def evolve_state(self, timestep, states):
        return {}

    def update(self, states, interval=None):
        timestep = states.get('timestep', interval or 1)
        return self._do_update(timestep, states)

    def _do_update(self, timestep, states):
        """Combined request + evolve for standalone (non-partitioned) use."""
        if self.request_only:
            return self.calculate_request(timestep, states)
        if self.evolve_only:
            return self.evolve_state(timestep, states)

        requests = self.calculate_request(timestep, states)
        bulk_requests = requests.pop("bulk", [])
        if bulk_requests:
            bulk_copy = states["bulk"].copy()
            for bulk_idx, request in bulk_requests:
                bulk_copy[bulk_idx] = request
            states["bulk"] = bulk_copy
        states = deep_merge(states, requests)
        update = self.evolve_state(timestep, states)
        if "listeners" in requests:
            update["listeners"] = deep_merge(update["listeners"], requests["listeners"])
        return update
