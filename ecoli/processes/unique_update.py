from ecoli.library.ecoli_step import EcoliStep as Step
from ecoli.library.schema import numpy_schema
from ecoli.library.schema_types import UNIQUE_TYPES


class UniqueUpdate(Step):
    """Placed after all Steps of each execution layer (see :ref:`partitioning`)
    to ensure that unique molecules are completely up-to-date"""

    name = "unique-update"

    config_schema = {
        'emit_unique': 'boolean{false}',
        # unique_topo values are path tuples like ('unique', 'DnaA_box'),
        # NOT scalar strings. Declaring them as 'map[string]' stringifies
        # the tuples on save → 'map[tuple[string]]' preserves structure.
        'unique_topo': 'map[tuple[string]]',
    }

    def inputs(self):
        return {mol: UNIQUE_TYPES.get(mol, 'unique_array') for mol in self.unique_topo}

    def outputs(self):
        return {mol: UNIQUE_TYPES.get(mol, 'unique_array') for mol in self.unique_topo}

    def __init__(self, parameters=None):
        super().__init__(parameters)
        self.unique_topo = self.parameters["unique_topo"]

    def ports_schema(self):
        return {
            unique_mol: numpy_schema(unique_mol, emit=self.parameters["emit_unique"])
            for unique_mol in self.unique_topo
        }

    def next_update(self, timestep, states):
        return {unique_mol: {"update": True} for unique_mol in self.unique_topo.keys()}
