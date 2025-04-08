"""
MIGRATED: Unique Update: Placed after all Steps of each execution layer (see :ref:`partitioning`)
to ensure that unique molecules are completely up-to-date
"""

from process_bigraph import Step
from ecoli.library.schema import numpy_schema


class UniqueUpdate(Step):
    config_schema = {
        "emit_unique": {
            "_type": "boolean",
            "_default": False
        }
    }

    def __init__(self, config=None, core=None):
        super().__init__(config, core)
        # Topology for all unique molecule ports (port: path)
        self.unique_topo = self.config["unique_topo"]

    def outputs(self):
        return {
            unique_mol: numpy_schema(unique_mol, emit=self.config["emit_unique"])
            for unique_mol in self.unique_topo
        }

    def update(self, state):
        return {unique_mol: {"update": True} for unique_mol in self.unique_topo.keys()}
