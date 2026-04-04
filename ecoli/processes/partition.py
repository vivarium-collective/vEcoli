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
        'process': {'_type': 'quote', '_default': None},
    }

    def inputs(self):
        return _typed_ports(self.ports_schema())

    def outputs(self):
        return {
            'request': {'_type': 'overwrite[map[list[integer]]]', '_default': {}},
            'process': 'quote',
            'next_update_time': 'overwrite[float]',
            'listeners': 'map[map[overwrite[float]]]',
        }

    def __init__(self, parameters=None):
        assert isinstance(parameters["process"], PartitionedProcess)
        if parameters["process"].parallel:
            raise RuntimeError("PartitionedProcess objects cannot be parallelized.")
        parameters["name"] = f"{parameters['process'].name}_requester"
        super().__init__(parameters)

    def update_condition(self, timestep, states):
        """
        Implements variable timestepping for partitioned processes

        Vivarium cycles through all :py:class:~vivarium.core.process.Step`
        instances every time a :py:class:`~vivarium.core.process.Process`
        instance updates the simulation state. When that happens, Vivarium
        will only call the :py:meth:`~.Requester.next_update` method of this
        Requester if ``update_condition`` returns True.

        Each process has access to a process-specific ``next_update_time``
        store and the ``global_time`` store. If the next update time is
        less than or equal to the global time, the process runs. If the
        next update time is ever earlier than the global time, this usually
        indicates that the global clock process is running with too large
        a timestep, preventing accurate timekeeping.
        """
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
        'process': {'_type': 'quote', '_default': None},
    }

    def inputs(self):
        return _typed_ports(self.ports_schema())

    def outputs(self):
        input_only = {'allocate', 'global_time', 'timestep'}
        all_ports = _typed_ports(self.ports_schema())
        # Override specific output semantics
        if 'next_update_time' in all_ports:
            all_ports['next_update_time'] = 'overwrite[float]'
        if 'process' in all_ports:
            all_ports['process'] = 'quote'
        return {k: v for k, v in all_ports.items() if k not in input_only}

    def __init__(self, parameters=None):
        assert isinstance(parameters["process"], PartitionedProcess)
        parameters["name"] = f"{parameters['process'].name}_evolver"
        super().__init__(parameters)

    def update_condition(self, timestep, states):
        """
        See :py:meth:`~.Requester.update_condition`.
        """
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

    process-bigraph interface: Subclasses may define ``_output_ports``
    as a set of port names that appear in the delta returned by
    ``evolve_state()``.  All other ports from ``ports_schema()`` are
    treated as input-only when building the process-bigraph dependency
    graph.  If ``_output_ports`` is None, all ports are assumed to be
    both input and output (conservative default).
    """

    _output_ports = None

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

    @abc.abstractmethod
    def calculate_request(self, timestep, states):
        return {}

    @abc.abstractmethod
    def evolve_state(self, timestep, states):
        return {}

    def next_update(self, timestep, states):
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
