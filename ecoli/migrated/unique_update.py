"""
MIGRATED: Unique Update
"""


from process_bigraph import Step
from ecoli.shared.interface import StepBase
from ecoli.shared.utils.schemas import numpy_schema


class UniqueUpdate(StepBase):
    """Placed after all Steps of each execution layer (see :ref:`partitioning`)
    to ensure that unique molecules are completely up-to-date"""

    name = "unique-update"

    defaults = {
        "emit_unique": False,
        "unique_topo": {"_default": {}}
    }

    def initialize(self, config):
        # Topology for all unique molecule ports (port: path)
        self.unique_topo = config["unique_topo"]
        self.output_ports = {
            unique_mol: numpy_schema(unique_mol, emit=self.config["emit_unique"])
            for unique_mol in self.unique_topo
        }

    def update(self, state):
        return {unique_mol: {"update": True} for unique_mol in self.unique_topo.keys()}
