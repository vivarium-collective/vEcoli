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

from process_bigraph import Step, Process
# from vivarium.library.dict_utils import deep_merge
# from ecoli.processes.registries import topology_registry

from ecoli.shared import dict_union


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

