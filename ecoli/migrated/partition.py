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
from process_bigraph import Step, Process

from ecoli.shared.interface import StepBase, ProcessBase, collapse_defaults
from ecoli.shared.registry import ecoli_core
from ecoli.shared.utils.schemas import get_defaults_schema, listener_schema, numpy_schema


class PartitionedProcess(ProcessBase):
    """Partitioned Process Base Class

    This class uses its ancestor to parse config schema and overrides inputs and outputs to auto parse.

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
        ecoli_core.topology_registry.register(self.name, self.topology)
        ports = {
            "bulk": numpy_schema("bulk"),
            "listeners": listener_schema({})
        }
        self.input_ports = ports 
        self.output_ports = ports
        
    @abc.abstractmethod
    def calculate_request(self, state, interval):
        return {}

    @abc.abstractmethod
    def evolve_state(self, state, interval):
        return {}
    
    @property
    @abc.abstractmethod
    def listener_schemas(self):
        pass
    
    def inputs(self):
        return {
            "bulk": "bulk",
            "listeners": 
            "timestep": "float",
            "environment": {
                "media_id": "string",
            }
        }
    
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


class Requester(Step):
    """Requester Step

    Accepts a PartitionedProcess as an input, and runs in coordination with an
    Evolver that uses the same PartitionedProcess.
    """

    config_schema = {"process": "tuple", "timestep": "float"}
    name = "requester"
    current_process: PartitionedProcess | None = None

    def initialize(self, config):
        assert isinstance(config["process"], PartitionedProcess)
        if config["process"].parallel:
            raise RuntimeError("PartitionedProcess objects cannot be parallelized.")
        self.name = f'{config["process"].name}_requester'
        self.config["name"] = self.name
        process = self.config.get("process")
        self.current_process = process

        if self.current_process:
            self.config["timestep"] = self.current_process.config["timestep"]

        self.cached_bulk_ports = ["bulk"]

    def update_condition(self, states):
        # TODO: determine if we can change method signature
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
                    f"{self.config["name"]} updated at t="
                    f"{states['global_time']} instead of t="
                    f"{states['next_update_time']}. Decrease the "
                    "timestep of the global_clock process for more "
                    "accurate timekeeping."
                )
            return True
        return False
    
    def initial_state(self):
        state = {
            "process": (),
            "next_update_time": self.config["timestep"],
            "request": {
                "bulk": []
            }
        }
        if self.current_process:
            state.update(self.current_process.initial_state())
        return state
    
    def inputs(self):
        """Requester inputs needs to match the state passed into PartitionedProcess.calculate_request plus process timestep and next_update_time"""
        schema = {
            "process": "tuple[process]",
            "next_update_time": "float",
            "request": "tree[float]"
        }
        if self.current_process:
            schema.update(self.current_process.inputs())
        return schema
    
    def outputs(self):
        schema = {
            "request": "tree[float]",
            "listeners": "tree[float]",
            "process": "tuple[process]"
        }
        if self.current_process:
            schema.update(self.current_process.outputs())
        return schema

    def update(self, state):
        process = state["process"][0]
        request = process.calculate_request(state, self.config["time_step"])
        process.request_set = True

        for port in list(state["request"].keys()):
            if port not in self.cached_bulk_ports:
                self.cached_bulk_ports.append(port)

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
    """

    config_schema = {"process": "tuple"}
    name = "evolver"
    current_process: PartitionedProcess | None = None

    def initialize(self, config):
        assert isinstance(config["process"], PartitionedProcess)
        self.name = f'{config["process"].name}_evolver'
        self.config["name"] = self.name

        process = self.config.get("process")
        self.output_ports = process.get_schema()
        
        self.current_process = process

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
        schema = {
            "process": "tuple[process]",
            "allocate": "tree[float]"
        }

        if self.current_process:
            schema.update(self.current_process.inputs())
        return schema
    
    def outputs(self):
        schema = {
            "process": "tuple[process]",
            "next_update_time": "float"
        }
        if self.current_process:
            schema.update(self.current_process.outputs())
        return schema
        
    def initial_state(self):
        state = {
            "allocate": {
                "bulk": []
            },
            "process": tuple(),
        }
        if self.current_process:
            state.update(self.current_process.initial_state())
        return state
    
    def update(self, state, interval):
        try:
            allocations = state.pop("allocate")
        except KeyError:
            raise KeyError("No allocations could be found")
        
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
