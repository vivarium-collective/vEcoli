import numpy as np


from ecoli.library.schema import bulk_name_to_idx, counts
from ecoli.shared.interface import StepBase
from ecoli.shared.registry import ecoli_core
from ecoli.shared.utils.schemas import get_defaults_schema, numpy_schema

# Register default topology for this process, associating it with process name
NAME = "murein-division"
TOPOLOGY = {
    "bulk": ("bulk",),
    "murein_state": ("murein_state",),
    "wall_state": ("wall_state",),
    "first_update": (
        "first_update",
        "murein_division",
    ),
}
ecoli_core.topology.register(NAME, TOPOLOGY)


class MureinDivision(StepBase):
    """
    Ensures that total murein count in bulk store matches that from division of
    murein_state store before running mass listener
    """

    name = NAME
    topology = TOPOLOGY

    defaults = {
        "murein_name": "CPD-12261[p]",
    }

    def initialize(self, config):
        self.murein = config["murein_name"]

        # Helper indices for Numpy array
        self.murein_idx = None
        self.ports = {
            "bulk": numpy_schema("bulk"),
            "murein_state": {
                "incorporated_murein": {
                    "_default": 0,
                },
                "unincorporated_murein": {
                    "_default": 0,
                },
                "shadow_murein": {"_default": 0},
            },
            "wall_state": {
                "lattice": {
                    "_default": None,
                }
            },
            "first_update": {
                "_default": True,
                "_divider": {"divider": "set_value", "config": {"value": True}},
            },
        }
    
    def inputs(self):
        return get_defaults_schema(self.ports)
    
    def outputs(self):
        return get_defaults_schema(self.ports)

    def update(self, state):
        if self.murein_idx is None:
            self.murein_idx = bulk_name_to_idx(
                self.config["murein_name"], state["bulk"]["id"]
            )

        update = {"murein_state": {}, "bulk": []}
        # Ensure that lattice is a numpy array so divider works properly.
        # Used when loading from a saved state.
        if (not isinstance(state["wall_state"]["lattice"], np.ndarray)) and (
            state["wall_state"]["lattice"] is not None
        ):
            update["wall_state"] = {
                "lattice": np.array(state["wall_state"]["lattice"])
            }
        # Only run right after division (cell has half of mother lattice)
        # TODO: Calculate porosity, hole size/strand length dists
        # Note: This mechanism does not perfectly conserve murein mass between
        # mother and daughter cells (can at most gain the mass of 1 CPD-12261).
        if state["first_update"] and state["wall_state"]["lattice"] is not None:
            accounted_murein_monomers = sum(state["murein_state"].values())
            # When run in an EngineProcess, this Step sets the incorporated
            # murein count before CellWall or PBPBinding run after division
            if state["murein_state"]["incorporated_murein"] == 0:
                incorporated_murein = np.sum(state["wall_state"]["lattice"])
                update["murein_state"]["incorporated_murein"] = incorporated_murein
                accounted_murein_monomers += incorporated_murein
            remainder = accounted_murein_monomers % 4
            if remainder != 0:
                # Bulk murein is a tetramer. Add extra unincorporated murein
                # monomers until divisible by 4
                update["murein_state"]["unincorporated_murein"] = 4 - remainder
                accounted_murein_monomers += 4 - remainder
            accounted_murein = accounted_murein_monomers // 4
            total_murein = counts(state["bulk"], self.murein_idx)
            if accounted_murein != total_murein:
                update["bulk"].append(
                    (self.murein_idx, (accounted_murein - total_murein))
                )
        update["first_update"] = False
        return update
