from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from reconstruction.ecoli.simulation_data import SimulationDataEcoli

"""
Spatial Enforcement Variant is a variant in which we apply topological constraints on molecules of interest that move through the cellular envelope
"""


def constraints(
    sim_data: "SimulationDataEcoli",
) -> tuple[list[str], list[str]]:
    # TODO: implement spatial topological constraints on molecules crossing
    # the cellular envelope; return (constrained_molecule_ids, allow_list)
    return [], []
