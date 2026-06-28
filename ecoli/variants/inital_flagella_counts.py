"""
This is a variant that sets the initial number of flagella to 4 rather than 30

"""

from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from reconstruction.ecoli.simulation_data import SimulationDataEcoli
import numpy as np


def apply_variant(
    sim_data: "SimulationDataEcoli", params: dict[str, Any]
) -> "SimulationDataEcoli":
    flagella_final_complex = params["molecule_id"]
    new_initial_count = params["count"]

    bulk_data = sim_data.internal_state.bulk_molecules.bulk_data
    bulk_ids = bulk_data["id"]

    idx = np.where(bulk_ids == flagella_final_complex)[0]
    if len(idx) == 0:
        raise ValueError("No flagella complex found")

    idx = idx[0]
    bulk_data["count"][idx] = new_initial_count

    return sim_data
