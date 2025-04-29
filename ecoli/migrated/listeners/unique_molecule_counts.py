"""
===============================
MIGRATED: Unique Molecule Counts Listener
===============================

Counts unique molecules
"""


from ecoli.shared.interface import ListenerBase
from ecoli.shared.registry import ecoli_core
from ecoli.shared.utils.schemas import listener_schema, numpy_schema


NAME = "unique_molecule_counts"
TOPOLOGY = {
    "unique": ("unique",),
    "listeners": ("listeners",),
    "global_time": ("global_time",),
    "timestep": ("timestep",),
}
ecoli_core.topology.register(NAME, TOPOLOGY)


class UniqueMoleculeCounts(ListenerBase):
    """UniqueMoleculeCounts"""

    name = NAME
    topology = TOPOLOGY

    def initialize(self, config):
        self.unique_ids = config["unique_ids"]

        self.input_ports = {
            "unique": {
                str(mol_id): numpy_schema(
                    mol_id + "s", emit=config["emit_unique"]
                )
                for mol_id in self.unique_ids
                if mol_id not in ["DnaA_box", "active_ribosome"]
            },
            "global_time": {"_default": 0.0},
            "timestep": {"_default": config["time_step"]},
        }
        self.input_ports["unique"].update(
            {
                "active_ribosome": numpy_schema(
                    "active_ribosome", emit=config["emit_unique"]
                ),
                "DnaA_box": numpy_schema(
                    "DnaA_boxes", emit=config["emit_unique"]
                ),
            }
        )

        self.output_ports = {
            "listeners": {
                "unique_molecule_counts": listener_schema(
                    {str(mol_id): 0 for mol_id in self.unique_ids}
                )
            },
        }

    def update_condition(self, timestep, states):
        return (states["global_time"] % states["timestep"]) == 0

    def update(self, state):
        return {
            "listeners": {
                "unique_molecule_counts": {
                    str(unique_id): state["unique"][unique_id]["_entryState"].sum()
                    for unique_id in self.unique_ids
                }
            }
        }
