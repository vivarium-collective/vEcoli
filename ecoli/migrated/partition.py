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

NOTE: use initialize() for defining attrs and logic for composition instantiation, otherwise init

"""

import abc
import copy
import warnings

from bigraph_schema import deep_merge

from ecoli.shared.interface import StepBase, ProcessBase, collapse_defaults
from ecoli.shared.utils.schemas import get_defaults_schema, numpy_schema


class Requester(StepBase):
    """Requester Step

    Accepts a PartitionedProcess as an input, and runs in coordination with an
    Evolver that uses the same PartitionedProcess.
    """

    defaults = {"process": None}

    def initialize(self, config):
        assert isinstance(config["process"], PartitionedProcess)
        if config["process"].parallel:
            raise RuntimeError("PartitionedProcess objects cannot be parallelized.")
        config["name"] = f'{config["process"].name}_requester'

        process = self.config.get("process")

        self.input_ports = process.get_schema()
        self.input_ports["request"] = {
            "bulk": numpy_schema("bulk")
        }
        self.input_ports["process"] = "tuple"
        self.input_ports["global_time"] = "float"
        self.input_ports["timestep"] = {"_default": process.parameters["time_step"], "_type": "float"}
        self.input_ports["next_update_time"] = {
            "_default": process.config["timestep"],
            "_type": "float"
        }

        self.output_ports = process.requester_outputs
        self.cached_bulk_ports = list(self.output_ports["request"].keys())

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
    
    def inputs(self):
        """Requester inputs needs to match the state passed into PartitionedProcess.calculate_request plus process timestep and next_update_time"""
        return get_defaults_schema(self.input_ports)
    
    def outputs(self):
        """Requester outputs should be a union of the outputs of PartitionedProcess.calculate_request and listeners, process"""
        return get_defaults_schema(self.output_ports)

    def update(self, state, interval):
        process = state["process"][0]
        request = process.calculate_request(state, self.config["time_step"])
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


class Evolver(StepBase):
    """Evolver Step

    Accepts a PartitionedProcess as an input, and runs in coordination with an
    Requester that uses the same PartitionedProcess.
    """

    defaults = {"process": None}

    def initialize(self, config):
        assert isinstance(config["process"], PartitionedProcess)
        self.config["name"] = f'{config["process"].name}_evolver'

        process = self.config.get("process")
        self.output_ports = process.get_schema()
        self.output_ports["allocate"] = {
            "bulk": numpy_schema("bulk")  # TODO: is this correct?
        }
        self.output_ports["process"] = tuple()
        self.output_ports["global_time"] = {"_default": 0.0}
        self.output_ports["timestep"] = {"_default": process.config["timestep"]}
        self.output_ports["next_update_time"] = {
            "_default": process.parameters["timestep"],
            "_updater": "set",
            "_divider": "set",
        }

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
    
    def inputs(self):
        process = self.config.get("process")
        return process.evolve_inputs()
    
    def outputs(self):
        return get_defaults_schema(self.output_ports)

    def update(self, state, interval):
        allocations = state.pop("allocate")
        state = deep_merge(state, allocations)
        process = state["process"][0]

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
        update = process.evolve_state(state, interval)
        update["process"] = (process,)
        update["next_update_time"] = state["global_time"] + state["timestep"]

        return update


class PartitionedProcess(ProcessBase):
    """Partitioned Process Base Class

    This is the base class for all processes whose updates can be partitioned.
    Each partitioned process should define the following:

    @property listener_schemas()
    def ports_schema()
    def calculate_request(state, interval)
    def evolve_state(state, interval)
    """

    def initialize(self, config):

        # set partition mode
        self.evolve_only = config.get("evolve_only", False)
        self.request_only = config.get("request_only", False)
        self.request_set = False

        # register topology
        assert self.name
        assert self.topology
        # topology_registry.register(self.name, self.topology)

    @property 
    @abc.abstractmethod
    def listener_schemas(self) -> dict:
        """Return a dict of listener schemas. For example:

        return {
            "complexation_listener": {
                **listener_schema(
                    {
                        "complexation_events": (
                            [0] * len(self.reaction_ids),
                            self.reaction_ids,
                        )
                    }
                )
            }
        """
        pass

    @abc.abstractmethod
    def ports_schema(self):
        # TODO: use this for initial state
        return {}
    
    @abc.abstractmethod
    def calculate_request(self, state, interval):
        return {}

    @abc.abstractmethod
    def evolve_state(self, state, interval):
        return {}

    def initial_state(self):
        # get bidirectional schema and defaults
        ps = self.ports_schema()

        # get output keys only
        output_ports = self.outputs()

        # parse only output keys from bidirectional
        defaults = {}
        for port in output_ports:
            defaults[port] = ps[port]

        return collapse_defaults(defaults)
    
    def evolver_inputs(self):
        return {
            "timestep": "float",
            "bulk": numpy_schema("bulk")
        }
    
    def evolver_outputs(self):
        return {
            "bulk": numpy_schema("bulk"),
            "listeners": self.listener_schemas
        }
    
    def requester_inputs(self):
        return {
            "bulk": numpy_schema("bulk"),
            "timestep": "float",
            "environment": {
                "media_id": "string"
            },
            "listeners": self.listener_schemas
        }
    
    def requester_outputs(self):
        return {
            "request": "tree",
            "listeners": self.listener_schemas,
            "process": "tuple"
        }
    
    def inputs(self):
        """Needs to be a summation of args parsable by both calc request and evolve state!"""
        return self.requester_inputs()
    
    def outputs(self):
        """Returns the schema for one of the following situational outputs:
        calculate_request
        evolve_state

        Evolve outputs is a superset of these and thus is used.
        """
        return self.evolver_outputs()
    
    def update(self, state, interval):
        """
        """
        if self.request_only:
            return self.calculate_request(state, interval)
        if self.evolve_only:
            return self.evolve_state(state, interval)

        requests = self.calculate_request(state, interval)
        bulk_requests = requests.pop("bulk", [])
        if bulk_requests:
            bulk_copy = state["bulk"].copy()
            for bulk_idx, request in bulk_requests:
                bulk_copy[bulk_idx] = request
            state["bulk"] = bulk_copy
        state = deep_merge(state, requests)
        update = self.evolve_state(state, interval)
        if "listeners" in requests:
            update["listeners"] = deep_merge(update["listeners"], requests["listeners"])
        return update
