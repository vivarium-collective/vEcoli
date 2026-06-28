import os
from typing import Any
import numpy as np
import pandas as pd

from duckdb import DuckDBPyConnection

from ecoli.library.sim_data import LoadSimData
import matplotlib.pyplot as plt

# from maya_testing import bulk_df

# from ecoli.analysis.single.quorum_sensing_protein import bulk_molecule_ids
# from ecoli.library.parquet_emitter import num_cells, read_stacked_columns

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

    # These are genes that are + regulated across from the common core genes
    # not genes that are UPEC specific, but just increased from common core

    kegg_qs = [
        "G6936-MONOMER[c]",  # cedA
        "EG11750-MONOMER[c]",
        "EG11712-MONOMER[o]",
        "FDNG-MONOMER[m]",
        "G7436-MONOMER[i]",
        "CYCA-MONOMER[i]",
        "G0-17012_RNA[c]",
        "G7945-MONOMER[c]",
        "FRUA-MONOMER[i]",
        "G7931-MONOMER[c]",
        "EG11932-MONOMER[c]",
        "EG11014-MONOMER[c]",
        "EG11072-MONOMER[c]",
        "EG10825-MONOMER[c]",
        "EG10297-MONOMER[i]",
        "EG10168-MONOMER[p]",
        "EG10671-MONOMER[o]",
        "EG10670-MONOMER[o]",
        "EG12850-MONOMER[p]",
        "EG10302-MONOMER[o]",
        "ENTD-MONOMER[m]",
        "ENTF-MONOMER[c]",
        "YOJI-MONOMER[i]",
        "G6532-MONOMER[i]",
        # amiA, mdtC ad one more i think argL are not listed in bulk
    ]

    # Combined plot of all molecules
    for mol in kegg_qs:
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
    for mol in kegg_qs:
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
