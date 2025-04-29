"""
=========================
MIGRATED: Replication Data Listener
=========================
"""

import numpy as np
from ecoli.library.schema import attrs

from ecoli.shared.registry import ecoli_core
from ecoli.shared.interface import StepBase
from ecoli.shared.utils.schemas import get_defaults_schema, listener_schema, numpy_schema, collapse_defaults


NAME = "replication_data_listener"
TOPOLOGY = {
    "listeners": ("listeners",),
    "oriCs": ("unique", "oriC"),
    "DnaA_boxes": ("unique", "DnaA_box"),
    "active_replisomes": ("unique", "active_replisome"),
    "global_time": ("global_time",),
    "timestep": ("timestep",),
}
ecoli_core.topology.register(NAME, TOPOLOGY)


class ReplicationData(StepBase):
    """
    Listener for replication data.
    """

    name = NAME
    topology = TOPOLOGY

    defaults = {"time_step": 1, "emit_unique": False}

    def initialize(self, config):
        self.input_schema = {
            "oriCs": numpy_schema("oriCs"),
            "active_replisomes": numpy_schema("active_replisomes"),
            "DnaA_boxes": numpy_schema("DnaA_boxes"),
            "global_time": {"_default": 0.0},
            "timestep": self.timestep_schema
        }

        self.output_schema = {
            "listeners": {
                "replication_data": listener_schema(
                    {
                        "fork_coordinates": [],
                        "fork_domains": [],
                        "fork_unique_index": [],
                        "number_of_oric": [],
                        "free_DnaA_boxes": [],
                        "total_DnaA_boxes": [],
                    }
                )
            }   
        }

    def inputs(self):
        return get_defaults_schema(self.input_schema)

    def outputs(self):
        return get_defaults_schema(self.output_schema)
    
    def initial_state(self):
        return collapse_defaults(self.output_schema)
    
    def update_condition(self, timestep, states):
        return (states["global_time"] % states["timestep"]) == 0

    def update(self, state, interval):
        fork_coordinates, fork_domains, fork_unique_index = attrs(
            state["active_replisomes"], ["coordinates", "domain_index", "unique_index"]
        )

        (DnaA_box_bound,) = attrs(state["DnaA_boxes"], ["DnaA_bound"])

        update = {
            "listeners": {
                "replication_data": {
                    "fork_coordinates": fork_coordinates,
                    "fork_domains": fork_domains,
                    "fork_unique_index": fork_unique_index,
                    "number_of_oric": state["oriCs"]["_entryState"].sum(),
                    "total_DnaA_boxes": len(DnaA_box_bound),
                    "free_DnaA_boxes": np.count_nonzero(np.logical_not(DnaA_box_bound)),
                }
            }
        }
        return update
