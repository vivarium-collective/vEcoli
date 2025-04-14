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

import numpy as np
from process_bigraph import Process, Step

from ecoli.library.schema import numpy_schema
from ecoli.shared.schemas import get_config_schema


class Requester(Step):
    config_schema = {}

    def initialize(self, config):
        pass

    def inputs(self):
        return self.instance.requester_inputs()

    def outputs(self):
        return self.instance.requester_outputs()

    def update(self, state):
        return self.instance.calculate_request(state)


# TODO(wcEcoli):
# - allow for shuffling when appropriate (maybe in another process)
# - handle protein complex dissociation


class PartitionedProcess(Process):
    """Partitioned Process which acts as a base type which should be inherited by any process that needs to
    be linked with a Requester. This replaces the previous PartitionedProcess. These processes are
    distinctly marked by their interaction/dependence on the "bulk" type.
    """
    defaults = {}
    config_schema = {}

    def __init__(self, config=None, core=None):
        # parsing the defaults and setting the config schema as an instance attribute
        self.timestep_schema = {"_default": 1.0, "_type": "float"}
        self.config_schema = get_config_schema(self.defaults)
        self.config_schema['time_step'] = self.timestep_schema

        super().__init__(config, core)

        self.timestep = self.config["time_step"]

    def initial_state(self):
        return {
            "bulk": [()],
        }

    @abc.abstractmethod
    def inputs(self):
        pass

    @abc.abstractmethod
    def outputs(self):
        pass

    def requester_inputs(self):
        """Input port schemas needing to be available to the Requester."""
        return self.inputs()

    def requester_outputs(self):
        """Output port schemas needing to be available to the Requester."""
        # these need to match that returned by calculate_request, right?
        return {
            "bulk": numpy_schema("bulk")
        }

    @abc.abstractmethod
    def calculate_request(self, state):
        """This is just like self.update, but formatted for a Step, as this is the method
        called by the Requester.

        :param state: The schema of this state should match `self.requester_inputs()`.
        :returns: `dict` whose schema should match `self.requester_outputs()`.
        """
        pass

    @abc.abstractmethod
    def update(self, state, interval):
        """The previous was the `evolve_state` method in PartitionedProcess."""
        pass

