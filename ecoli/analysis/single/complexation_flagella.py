import os
from typing import Any
import numpy as np
import pandas as pd

from duckdb import DuckDBPyConnection

from ecoli.library.sim_data import LoadSimData
import matplotlib.pyplot as plt

COLORS_256 = [
    [228, 26, 28],
    [55, 126, 184],
    [77, 175, 74],
    [152, 78, 163],
    [255, 127, 0],
    [255, 255, 51],
    [166, 86, 40],
    [247, 129, 191],
]

COLORS = ["#%02x%02x%02x" % (color[0], color[1], color[2]) for color in COLORS_256]


def plot(
    params: dict[str, Any],
    conn: DuckDBPyConnection,
    history_sql: str,
    config_sql: str,
    success_sql: str,
    sim_data_paths: dict[str, dict[int, str]],
    validation_data_paths: list[str],
    outdir: str,
    variant_metadata: dict[str, dict[int, Any]],
    variant_names: dict[str, str],
):
    with open(os.path.join(outdir, "history_sql.txt"), "w") as f:
        f.write(history_sql)

    query = f"""
        SELECT bulk, listeners__complexation_listener__complexation_events, time FROM ({history_sql})
        ORDER BY time
    """

    exp_id = list(sim_data_paths.keys())[0]
    sim_data_path = list(sim_data_paths[exp_id].values())[0]
    sim_data = LoadSimData(sim_data_path).sim_data

    # Get the complexation object
    complexation = sim_data.process.complexation
    # # FIX: Access attribute, not dictionary key
    # # Get reaction IDs to match the listener data columns
    reaction_ids = complexation.ids_reactions

    output_df = conn.sql(query).df()

    # Matrix columns correspond to reactions
    matrix = np.stack(
        output_df["listeners__complexation_listener__complexation_events"].values
    ).astype(int)

    np.savetxt(os.path.join(outdir, "matrix.txt"), matrix)

    time_mins = output_df["time"].values / 60

    # Create DataFrame with reaction IDs as columns
    flg_df = pd.DataFrame(matrix, columns=reaction_ids)
    flg_df["Time (min)"] = time_mins

    # Find flagella-related reactions
    # flagella_reaction_ids = ["FLAGELLAR-MOTOR-COMPLEX_RXN"]
    flagella_reaction_ids = ["CPLX0-7452_RXN"]  # 6/10/26 NEW STOICH - CHANGED SIMDATA

    # 3/4/26 - flhDC creation

    for rxn_id in flagella_reaction_ids:
        if rxn_id in flg_df.columns:
            plt.figure(figsize=(8, 8))
            plt.plot(flg_df["Time (min)"], flg_df[rxn_id], label=rxn_id)
            plt.xlabel("Time (min)")
            plt.ylabel("Reaction Events per Timestep")
            plt.legend()
            plt.title("Complexation Events for {}".format(rxn_id))
            plt.tight_layout()
            plt.savefig(os.path.join(outdir, "{}.png".format(rxn_id)))
            plt.close()
