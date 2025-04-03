"""
======================
MIGRATED: Partitioning Processes
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
import copy
import warnings

from process_bigraph import Step, Process
from bigraph_schema import deep_merge
# from vivarium.library.dict_utils import deep_merge
# from ecoli.processes.registries import topology_registry

from ecoli.shared import dict_union


class Requester(Step):
    """Requester Step

    Accepts a PartitionedProcess as an input, and runs in coordination with an
    Evolver that uses the same PartitionedProcess.
    """

    defaults = {"process": None}

    def __init__(self, parameters=None):
        assert isinstance(parameters["process"], PartitionedProcess)
        if parameters["process"].parallel:
            raise RuntimeError("PartitionedProcess objects cannot be parallelized.")
        parameters["name"] = f'{parameters["process"].name}_requester'
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
        ports["timestep"] = {"_default": process.parameters["time_step"]}
        ports["next_update_time"] = {
            "_default": process.parameters["timestep"],
            "_updater": "set",
            "_divider": "set",
        }
        self.cached_bulk_ports = list(ports["request"].keys())
        return ports

    def next_update(self, timestep, states):
        process = states["process"][0]
        request = process.calculate_request(self.parameters["time_step"], states)
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

    Accepts a PartitionedProcess as an input, and runs in coordination with a
    Requester that uses the same PartitionedProcess.
    """

    config_schema = {
        "process_name": "string",  # TODO: either we explicitly define the linked process' ports and process name or we dynamically instantiate and infer
        "process_input_ports": "tree[string]",  # these should be the schema!
        "process_output_ports": "tree[string]"
    }

    def __init__(self, config=None, core=None):
        # assert isinstance(parameters["process"], PartitionedProcess)
        super().__init__(config, core)
        self.name = f'{config["process_name"]}_evolver'
        self.input_ports = self.config.get("process_input_ports")
        self.output_ports = self.config.get("process_output_ports")

    def update_condition(self, timestep, states):
        # TODO: parse usages of this and determine if it's feasible to switch these positional arguments
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

    def inputs(self):
        # TODO: the input ports of this process should simply be a superset of the referenced process ports and the rest
        ports = self.input_ports.copy()
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

    def ports_schema(self):
        # TODO: output ports of this process should simply be a superset of the referenced process ports and the rest
        ports = {}
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

    def next_update(self, timestep, states):
        allocations = states.pop("allocate")
        states = deep_merge(states, allocations)
        process = states["process"][0]

        # If the Requester has not run yet, skip the Evolver's update to
        # let the Requester run in the next time step. This problem
        # often arises after division because after the step divider
        # runs, Vivarium wants to run the Evolvers instead of re-running
        # the Requesters. Skipping the Evolvers in this case means our
        # timesteps are slightly off. However, the alternative is to run
        # self.process.calculate_request and discard the result before
        # running the Evolver this timestep, which means we skip the
        # Allocator. Skipping the Allocator can cause the simulation to
        # crash, so having a slightly off timestep is preferable.
        if not process.request_set:
            return {}

        update = process.evolve_state(timestep, states)
        update["process"] = (process,)
        update["next_update_time"] = states["global_time"] + states["timestep"]
        return update


class PartitionedProcess(Process):
    """Partitioned Process Base Class

    This is the base class for all processes whose updates can be partitioned.

    NOTE: anything related to Vivarium "topology" should be removed, not just refactored for
    process bigraph. The pbg.Composite class actually performs the wiring logic required as
    per the user's config parameter ({state: , composition: , etc...}). TODO: how will this effect workflows?
    """

    config_schema = {
        'evolve_only': 'boolean',
        'request_only': 'boolean',
        'name': 'string',

    }

    def __init__(self, config=None, core=None):
        super().__init__(config, core)

        # set partition mode
        self.evolve_only = self.config.get("evolve_only", False)
        self.request_only = self.config.get("request_only", False)
        self.request_set = False

        self.name = self.config.get("name")
        assert self.name

        # assert self.topology
        # topology_registry.register(self.name, self.topology)

    @abc.abstractmethod
    def inputs(self):
        return {}

    @abc.abstractmethod
    def outputs(self):
        return {}

    @abc.abstractmethod
    def calculate_request(self, timestep, states):
        # TODO: parse usages of this and determine if it's feasible to switch these positional arguments
        return {}

    @abc.abstractmethod
    def evolve_state(self, timestep, states):
        # TODO: parse usages of this and determine if it's feasible to switch these positional arguments
        return {}

    def update(self, state, interval):
        if self.request_only:
            return self.calculate_request(interval, state)
        if self.evolve_only:
            return self.evolve_state(interval, state)

        requests = self.calculate_request(interval, state)
        bulk_requests = requests.pop("bulk", [])
        if bulk_requests:
            bulk_copy = state["bulk"].copy()
            for bulk_idx, request in bulk_requests:
                bulk_copy[bulk_idx] = request
            state["bulk"] = bulk_copy
        state = deep_merge(state, requests)
        update = self.evolve_state(interval, state)
        if "listeners" in requests:
            update["listeners"] = deep_merge(update["listeners"], requests["listeners"])
        return update
