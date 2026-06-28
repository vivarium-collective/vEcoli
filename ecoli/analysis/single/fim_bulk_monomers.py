import os
from typing import Any
import numpy as np
import pandas as pd

from duckdb import DuckDBPyConnection

from ecoli.library.sim_data import LoadSimData
import matplotlib.pyplot as plt


COLORS_256 = [  # From colorbrewer2.org, qualitative 8-class set 1
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
    history_sql: str,  # query in the plot function nested
    config_sql: str,  #
    success_sql: str,
    sim_data_paths: dict[str, dict[int, str]],  # first level is experiment id,
    validation_data_paths: list[str],
    outdir: str,
    variant_metadata: dict[str, dict[int, Any]],
    variant_names: dict[str, str],
):
    with open(os.path.join(outdir, "history_sql.txt"), "w") as f:
        f.write(history_sql)

    query = f"""
        SELECT bulk,time FROM ({history_sql})
        ORDER BY time
    """

    exp_id = list(sim_data_paths.keys())[0]
    sim_data_path = list(sim_data_paths[exp_id].values())[0]
    sim_data = LoadSimData(
        sim_data_path
    ).sim_data  # now have functional sim data object to work with
    bulk_matrix_ids = sim_data.internal_state.bulk_molecules.bulk_data["id"].tolist()

    # Execute SQL query by DuckDB and converts to a Pandas Dataframe
    # The resulting DataFrame contains 'bulk' and 'time' (from the query)
    output_df = conn.sql(query).df()

    # Converts the bulk column into a 2D NumPy array
    # Each row is a different timepoint and each column corresponds to a molecule, also ensure the data type is integer
    bulk_matrix = np.stack(output_df["bulk"].values).astype(int)

    # Save the resulting NumPy array (bulk_matrix) to a text file
    # Each row of the file is a timepoint, each column corresponds to a molecule
    np.savetxt(os.path.join(outdir, "bulk_matrix.txt"), bulk_matrix)

    # Convert time from seconds to minutes for better interpretability
    time_mins = output_df["time"].values / 60

    # Create a dataframe from bulk_matrix, labeling each column by the bulk_matrix_ids
    bulk_dfs = pd.DataFrame(bulk_matrix, columns=bulk_matrix_ids)

    # Add the time column to the Dataframe for plotting
    bulk_dfs["Time (min)"] = time_mins

    # Monomers and RNA from bulk column these are fimbrial subunits

    fim_units = [
        "EG10308-MONOMER[e]",
        "EG10309-MONOMER[c]",
        "EG10310-MONOMER[p]",
        "EG10310_RNA[c]",
        "EG10311-MONOMER[o]",
        "EG10311_RNA[c]",
        "EG10312-MONOMER[c]",
        "EG10313-MONOMER[l]",
        "EG10313_RNA[c]",
        "EG10314-MONOMER[l]",
        "EG10314_RNA[c]",
        "EG10315-MONOMER[l]",
        "EG10315_RNA[c]",
    ]

    # Combined plot of all molecules
    for mol in fim_units:
        if mol in bulk_dfs.columns:  # Ensure molecule exists
            idx = (
                bulk_dfs[mol] / bulk_dfs[mol].iloc[0]
            )  # Normalize by initial value to be able to plot and see trends on same scale
            plt.plot(
                bulk_dfs["Time (min)"], idx, label=mol
            )  # Plot normalized abundance
    # Set plot labels, titles, legend
    plt.xlabel("Time (min)")
    plt.ylabel("Normalized Abundance")
    plt.title("UPEC Positively Upregulated Genes")
    plt.legend(bbox_to_anchor=(1.05, 1), loc=2)
    plt.tight_layout()
    plt.savefig(os.path.join(outdir, "UPEC.png"), dpi=300)
    plt.show()

    # Generate and save a separate plot for each molecule
    for mol in fim_units:
        if mol in bulk_dfs.columns:  # Check if molecule exists
            plt.figure(figsize=(8, 5))  # Create a new figure for each molecule
            plt.plot(bulk_dfs["Time (min)"], bulk_dfs[mol], label=mol)
            plt.xlabel("Time (min)")
            plt.ylabel("Abundance")
            plt.title(f"Timecourse of {mol}")
            plt.legend()
            plt.tight_layout()
            plt.savefig(os.path.join(outdir, f"{mol}_timecourse_plot.png"))
            plt.close()
