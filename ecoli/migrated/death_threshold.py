from ecoli.migrated.partition import PartitionedProcess
from ecoli.shared.registry import ecoli_core
from ecoli.library.schema import bulk_name_to_idx, counts 
from ecoli.shared.utils.schemas import numpy_schema


# Give a unique string name to the process
NAME = "death_threshold"
# Define the stores that each port in the process connects to
TOPOLOGY = {
    # There is a port called bulk connected to a store located
    # at a top-level store in the simulation state also called bulk
    "bulk": ("bulk",),
    # Topologies make our processes modular. If we wish to wire
    # the process differently, all we have to do is change
    # the topology. For example, changing the above to
    # "bulk": ("new_bulk", "sub_bulk") would connect the bulk
    # port of the process to a different store called sub_bulk
    # that is located inside the top-level new_bulk store. It is up
    # to you to ensure that whatever store the port is connected to
    # contains data in a format that the process expects from that
    # port and has an updater that can handle the updates that the
    # process passes through  that port.

    # Most of our current processes are required to run with the same
    # timestep (see "Partitioning" heading in "Stores" documentation).
    # As such, most processes connect their timestep ports to the
    # same top-level timestep store using "timestep": ("timestep",).
    # However, if we wish to run a process with its own timestep,
    # we could connect it to a separate dedicated store as follows.
    "timestep": ("death_threshold", "timestep"),
    # Time stepping for PartitionedProcesses and most Steps in our
    # model requires the process to have a port to the global time store.
    # See the "Time Steps" sub-heading in the "Processes" documentation.
    "global_time": ("global_time",)
}


class DeathThreshold(PartitionedProcess):
    """
    Check the count of a molecule and stop the simulation
    upon reaching a certain threshold.
    """

    # Can optionally define default parameters for process. These will
    # be merged with any user-provided parameter dictionary and passed
    # to the __init__ method of the process. The `time_step` parameter
    # is a special one that, in the absence of a custom `calculate_timestep`
    # method, determines how often to run the process (once every X seconds).
    defaults = {"time_step": 1.0, "molecule_id": "WATER[c]", "threshold": 1e10}

    def __init__(self, config=None, core=None):
        # Run __init__ of base Process class to save all parameters as
        # instance variable self.parameters
        super().__init__(config, core)

        # Can extract and perform calculations on other values in ``parameters``
        # here to prepare process parameters.
        self.molecule_id = self.config["molecule_id"]
        self.threshold = self.config["threshold"]
        # Cache indices into bulk array for molecules of interest by creating
        # instance variable with initial value of None. This will be populated
        # the first time the Requester runs calculate_request.
        self.mol_idx = None

    def inputs(self):
        # Ports must match the ports connected to stores by the topology. Here
        # we make use of the ``numpy_schema`` helper function to standardize
        # the creation of schemas for ports connected to the bulk store. Since
        # ports connected to the same store must have non-conflicting (values
        # for shared keys must be the same) schemas, if you know you are connecting
        # to a store that already exists (already has a schema from a port from
        # in another process), you can just leave the schema as an empty dictionary
        # as we do for the global_time port here.
        return {
            "bulk": numpy_schema("bulk"),
            "global_time": {},
            "timestep": self.timestep_schema,
        }
    
    def outputs(self):
        # Ports must match the ports connected to stores by the topology. Here
        # we make use of the ``numpy_schema`` helper function to standardize
        # the creation of schemas for ports connected to the bulk store. Since
        # ports connected to the same store must have non-conflicting (values
        # for shared keys must be the same) schemas, if you know you are connecting
        # to a store that already exists (already has a schema from a port from
        # in another process), you can just leave the schema as an empty dictionary
        # as we do for the global_time port here.
        return {
            "bulk": numpy_schema("bulk"),
            "global_time": {},
            "timestep": self.timestep_schema,
        }

    def calculate_request(self, state):
        # Since this is a PartitionedProcess, it will be turned into two Steps:
        # a Requester and an Evolver. The Requester Step will call calculate_request.

        # Cache molecule index so that Requester and Evolver can use it
        if self.mol_idx is None:
            self.mol_idx = bulk_name_to_idx(self.molecule_id, state["bulk"]["id"])
        # Request all counts of given bulk molecule. Updates to bulk store are
        # lists of 2-element tuples ``(index, count)``
        return {"bulk": [(self.mol_idx, counts(state["bulk"], self.mol_idx))]}

    def update(self, state, interval):
        # The Evolver Step will call evolve_state after the Requesters in the execution
        # layer have called calculate_request and the Allocator has allocated counts
        # to processes
        if self.mol_idx is None:
            self.mol_idx = bulk_name_to_idx(self.molecule_id, state["bulk"]["id"])

        mol_counts = counts(state["bulk"], self.mol_idx)
        if mol_counts > self.threshold:
            raise RuntimeError(f"Count threshold for {self.molecule_id} exceeded: "
                f"{mol_counts} > {self.threshold}")
        


ecoli_core.processes.register("death_threshold", DeathThreshold)
