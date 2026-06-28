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
    sim_data = LoadSimData(sim_data_path).sim_data

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

    flagella_units = [
        "FLAGELLAR-MOTOR-COMPLEX[j]",
        "FLGB-FLAGELLAR-MOTOR-ROD-PROTEIN[j]",
        "FLGC-FLAGELLAR-MOTOR-ROD-PROTEIN[j]",
        "FLGF-FLAGELLAR-MOTOR-ROD-PROTEIN[j]",
        "FLGG-FLAGELLAR-MOTOR-ROD-PROTEIN[o]",
        "FLGH-FLAGELLAR-L-RING[j]",
        "FLGI-FLAGELLAR-P-RING[j]",
        "FLIF-FLAGELLAR-MS-RING[i]",
        "FLIG-FLAGELLAR-SWITCH-PROTEIN[i]",
        "FLIM-FLAGELLAR-C-RING-SWITCH[i]",
        "FLIN-FLAGELLAR-C-RING-SWITCH[m]",
        "MOTA-FLAGELLAR-MOTOR-STATOR-PROTEIN[i]",
        "MOTB-FLAGELLAR-MOTOR-STATOR-PROTEIN[i]",
        "EG10320-MONOMER[c]",  # flhD - C is not in bulk
        "EG11355-MONOMER[c]",  # FliA
        "G369-MONOMER[c]",  # flgM
        "G379-MONOMER[j]",  # FliK
        "G7028-MONOMER[i]",  # flhB
        "EG10321",  # FliC
        "EG10320",  # flhD
        "MONOMER0-2488[c]",  # FlhC --> got this ID from the complexation_reactions.tsv
        "G361-MONOMER[c]",  # flgE
        "EG11967-MONOMER[e]",  # FlgK
        "EG11545-MONOMER[e]",  # FlgL
        "EG10321-MONOMER[e]",  # FliC
        "EG10841-MONOMER[e]",  # FliD
        "CPLX0-3930[c]",  # flhdc
    ]
    #
    # # Combined plot of all molecules
    # for mol in flagella_units:
    #     if mol in bulk_dfs.columns:
    #         idx = (
    #             bulk_dfs[mol] / bulk_dfs[mol].iloc[0]
    #         )  # Normalize by initial value to be able to plot and see trends on same scale
    #         plt.plot(bulk_dfs["Time (min)"], idx, label=mol)
    #
    # plt.xlabel("Time (min)")
    # plt.ylabel("Relative Abundance")
    # plt.title("Flagella from bulk")
    # plt.legend(bbox_to_anchor=(1.05, 1), loc=2)
    # plt.tight_layout()
    # plt.savefig(os.path.join(outdir, "flagella.png"), dpi=300)
    # plt.show()
    #
    # # Generate and save a separate plot for each molecule - not normalized because each one separate
    # for mol in flagella_units:
    #     if mol in bulk_dfs.columns:
    #         plt.figure(figsize=(8, 5))
    #         plt.plot(bulk_dfs["Time (min)"], bulk_dfs[mol], label=mol)
    #         plt.xlabel("Time (min)")
    #         plt.ylabel("Abundance")
    #         plt.title(f"Timecourse of {mol}")
    #         plt.legend()
    #         plt.tight_layout()
    #         plt.savefig(os.path.join(outdir, f"{mol}_timecourse_plot.png"))
    #         plt.close()

    # Combined plot using RAW counts, not normalized
    fig, ax = plt.subplots(figsize=(10, 6))
    for index, mon in enumerate(flagella_units):
        if mon in bulk_dfs.columns:
            ax.plot(
                time_mins, bulk_dfs[mon], label=mon, color=COLORS[index % len(COLORS)]
            )
    ax.set_xlabel("Time (min)")
    ax.set_ylabel("Count")
    ax.set_title("All Flagella Monomer Counts (RAW)")
    ax.legend()
    fig.tight_layout()
    fig.savefig(os.path.join(outdir, "flagella_rxn_monomers.png"), dpi=300)
    plt.close(fig)

    # Individual plots for reach monomer
    for mon in flagella_units:
        if mon in bulk_dfs.columns:
            fig, ax = plt.subplots(figsize=(10, 6))
            ax.plot(time_mins, bulk_dfs[mon], color=COLORS[0])
            ax.set_xlabel("Time (min)")
            ax.set_ylabel("Count")
            ax.set_title(mon)
            fig.tight_layout()
            fig.savefig(os.path.join(outdir, f"{mon}_timecourse_plot.png"), dpi=300)
            plt.close(fig)

    # Combined plot of FlhDC complex and FliA
    flhdc = "CPLX0-3930[c]"
    flia = "EG11355-MONOMER[c]"
    fig, ax = plt.subplots(figsize=(6.5, 4))
    if flhdc in bulk_dfs.columns:
        ax.plot(time_mins, bulk_dfs[flhdc], label="FlhDC", color=COLORS[0])
    if flia in bulk_dfs.columns:
        ax.plot(time_mins, bulk_dfs[flia], label="FliA", color=COLORS[1])
    ax.set_xlabel("Time (min)")
    ax.set_ylabel("Count")
    ax.set_title("FlhDC + FliA")
    ax.legend(loc="upper right", fontsize=8)
    fig.tight_layout()
    fig.savefig(os.path.join(outdir, "flhDC_fliA_combined.png"), dpi=150)
    plt.close(fig)

    # Combined plot of FlhD, FlhC, and FlhDC complex (CPLX0-3930)
    flhd = "EG10320-MONOMER[c]"
    flhc = "MONOMER0-2488[c]"
    flhdc = "CPLX0-3930[c]"
    fig, ax = plt.subplots(figsize=(6.5, 4))
    if flhd in bulk_dfs.columns:
        ax.plot(time_mins, bulk_dfs[flhd], label="FlhD", color=COLORS[0])
    if flhc in bulk_dfs.columns:
        ax.plot(time_mins, bulk_dfs[flhc], label="FlhC", color=COLORS[1])
    if flhdc in bulk_dfs.columns:
        ax.plot(time_mins, bulk_dfs[flhdc], label="FlhDC", color=COLORS[2])
    ax.set_xlabel("Time (min)")
    ax.set_ylabel("Count")
    ax.set_title("FlhD, FlhC, and FlhDC complex")
    ax.legend(loc="upper right", fontsize=8)
    fig.tight_layout()
    fig.savefig(os.path.join(outdir, "flhD_flhC_combined.png"), dpi=150)
    plt.close(fig)
