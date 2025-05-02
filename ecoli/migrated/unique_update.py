from ecoli.shared.interface import MigrateStep as Step
from ecoli.library.schema import numpy_schema


class UniqueUpdate(Step):
    """Placed after all Steps of each execution layer (see :ref:`partitioning`)
    to ensure that unique molecules are completely up-to-date"""

    name = "unique-update"

    defaults = {"emit_unique": False, "unique_topo": {}}

    def __init__(self, parameters=None, core=None):
        super().__init__(parameters, core)
        # Topology for all unique molecule ports (port: path)
        self.unique_topo = self.parameters["unique_topo"]

    def ports_schema(self):
        return {
            unique_mol: numpy_schema(unique_mol, emit=self.parameters["emit_unique"])
            for unique_mol in self.parameters["unique_topo"]
        }

    def next_update(self, timestep, states):
        return {unique_mol: {"update": True} for unique_mol in self.unique_topo.keys()}
