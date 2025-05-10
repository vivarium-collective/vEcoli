from vivarium.core.process import Step
from ecoli.library.schema import numpy_schema


class UniqueUpdate(Step):
    """Placed after all Steps of each execution layer (see :ref:`partitioning`)
    to ensure that unique molecules are completely up-to-date"""

    name = "unique-update"

    defaults = {"emit_unique": False}

    def __init__(self, parameters=None):
        super().__init__(parameters)
        # Topology for all unique molecule ports (port: path)
        self.unique_topo = self.parameters["unique_topo"]

    def ports_schema(self):
        return {
            unique_mol: numpy_schema(unique_mol, emit=self.parameters["emit_unique"])
            for unique_mol in self.unique_topo
        }

    def next_update(self, timestep, states):
        return {unique_mol: {"update": True} for unique_mol in self.unique_topo.keys()}


"""
Chromo. Structure:
    bidir ->
        *bulk
        *active_replisomes
        *chromo. domains
        *active RNAPs
        *RNAs
        *active ribosome
        *promoters
        *genes
        *dnaA boxes

    inputs() ->
        global time
        timestep
    
    outputs() ->
        "next_update_time"
        "listeners": {
            "rnap_data": {
                "n_total_collisions": n_total_collisions,
                "n_headon_collisions": n_headon_collisions,
                "n_codirectional_collisions": n_codirectional_collisions,
                "headon_collision_coordinates": RNAP_coordinates[
                    RNAP_headon_collision_mask
                ],
                "codirectional_collision_coordinates": RNAP_coordinates[
                    RNAP_codirectional_collision_mask
                ],
            }
        },
        "oriCs": {},
        "full_chromosomes": {},
        "chromosomal_segments": {},

"""