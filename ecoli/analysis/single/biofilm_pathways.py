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

    # List of biofilm molecules of interest to plot
    # molecules_bs = [
    # "BASR-MONOMER[c]",
    # "PHOSPHO-BASR[c]"
    #    "G7575-MONOMER[c]", #QseB
    #    "EG12658-MONOMER[i]" #QseC
    # ]

    # Ids from the KEGG figure mapping ecoli biofilms
    # molecules_path = [
    # "CRR-MONOMER[c]",
    #  "CAMP[c]", #this is not being graphed for some reason
    # "CAMP[p]",
    #  "CAMP[e]",
    # "EG10320-MONOMER[c]", #FLHD
    # "EG11355-MONOMER[c]", #fliA
    #  "G369-MONOMER[c]", #flgM
    # "EG10230-MONOMER[c]", #dksA
    #  "EG11356-MONOMER[c]", #fliA
    # "EG12252-MONOMER[i]", #pdeH
    # "G6623-MONOMER[c]", #ycgR
    # "RPOS-MONOMER[c]", #rpos
    # "G6639-MONOMER[i]", #pdeR
    # "G6673-MONOMER[c]", #ydaM
    # "EG12008-MONOMER[c]", #mlrA
    # "G6543-MONOMER[o]", #csgG
    # "C-DI-GMP[c]",#ci-di-GMP #not being graphed, this one is for cytosol there is GMP for extracellular and plasmid
    # for this molecule check the sim data compartment levels and see where GMP is expressed in biofilm formation
    # "C-DI-GMP[e]",
    # "C-DI-GMP[p]",
    # "EG12252-MONOMER[i]", #PdeH
    # "EG12396-MONOMER[i]", #dgcE yegE
    # "G7049-MONOMER[i]",#dgcQ  #yedQ
    # "GDP-TP[c]", #pppGpp,
    # "GDP-TP[p]",  # pppGpp
    # "GDP-TP[e]", # pppGpp
    # "EG12712-MONOMER[c]", #LuxS
    # "G6799-MONOMER[c]", #LsrR
    # "", #sdiA
    # "", #AI-2
    # "", #AdrB
    #  "EG11257-MONOMER[i]", #AdrA also called dgcC
    #  "EG12260-MONOMER[i]", #bcsA
    #  "G7107-MONOMER[o]", #Wza
    #  "G7080-MONOMER[o]", #Ag43 #flu
    # "", #GlgC
    # "", #GlgA
    # "", #GlgP
    #  "BARA-MONOMER[i]", #BarA #theres a BarA UvrY two-component signal transduction system
    # "EG11140-MONOMER[c]", #Uvry
    # "CSRB-RNA[c]", #CsrB
    # "CSRC-RNA[c]", #csrC
    # "EG11447-MONOMER[c]", #csrA
    # "G6531-MONOMER[o]", #pgaA
    #  "G6530-MONOMER[p]", #pgaB
    #  "G6529-MONOMER[i]", #pgaC
    #  "G6528-MONOMER[i]", #pgaD
    #  "CPLX0-7994[i]" #Component of poly-acetlyd- PGAC and PGAD
    # ]

    kegg_qs = [
        "EG12712-MONOMER[c]",  # LuxS
        "YNEA-MONOMER[p]",  # lsrB
        "YDEY-MONOMER[i]",  # lsrC
        "YDEZ-MONOMER[i]",  # lsrD
        "G6798-MONOMER[c]",  # lsrK
        "YDEX-MONOMER[i]",  # LsrA
        "G6804-MONOMER[c]",  # LsrF
        "G6805G-MONOMER[c]",  # LsrG
        "G6799-MONOMER[c]",  # LsrR
    ]

    # Combined plot of all molecules
    # Loop through each QS molecule and normalize abundance to value at the first timepoint (index 0)
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
    plt.title("Biofilm Genes")
    plt.legend(bbox_to_anchor=(1.05, 1), loc=2)
    plt.tight_layout()
    plt.savefig(os.path.join(outdir, "All_Molecules_plot.png"), dpi=300)
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
