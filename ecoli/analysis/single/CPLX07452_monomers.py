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
        SELECT bulk,time FROM ({history_sql})
        ORDER BY time
    """

    out_put = conn.sql(query).df()

    exp_id = list(sim_data_paths.keys())[0]
    sim_data_path = list(sim_data_paths[exp_id].values())[0]
    sim_data = LoadSimData(sim_data_path).sim_data

    simdata_bulk_ids = sim_data.internal_state.bulk_molecules.bulk_data["id"].tolist()

    bulk_ids_array = np.stack(out_put["bulk"].values).astype(int)
    time_mins = out_put["time"].values / 60

    bulk_ids_df = pd.DataFrame(bulk_ids_array, columns=simdata_bulk_ids)
    bulk_ids_df["time (min)"] = time_mins

    # Flagella monomers of interest
    flagella_rxn_monomers = [
        "FLAGELLAR-MOTOR-COMPLEX[j]",
        "G361-MONOMER[j]",
        "EG11967-MONOMER[e]",  # FlgK
        "EG11545-MONOMER[e]",  # FlgL
        "EG10321-MONOMER[e]",  # FliC
        "EG10841-MONOMER[e]",  # FliD
        "CPLX0-7452[j]",
    ]

    # Combined plot using RAW counts, not normalized
    fig, ax = plt.subplots(figsize=(10, 6))
    for index, mon in enumerate(flagella_rxn_monomers):
        if mon in bulk_ids_df.columns:
            ax.plot(
                time_mins,
                bulk_ids_df[mon],
                label=mon,
                color=COLORS[index % len(COLORS)],
            )
            ax.set_xlabel("Time (min)")
            ax.set_ylabel("Count (molecules)")
            ax.set_title("All Flagella Monomer Counts (RAW)")
            ax.legend()
            fig.tight_layout()
            fig.savefig(os.path.join(outdir, "flagella_rxn_monomers.png"), dpi=300)
            plt.close(fig)

    # Individual plots for reach monomer
    for mon in flagella_rxn_monomers:
        if mon in bulk_ids_df.columns:
            fig, ax = plt.subplots(figsize=(10, 6))
            ax.plot(time_mins, bulk_ids_df[mon], color=COLORS[0])
            ax.set_xlabel("Time (min)")
            ax.set_ylabel("Count (molecules)")
            ax.set_title(mon)
            fig.tight_layout()
            fig.savefig(os.path.join(outdir, f"{mon}_timecourse_plot.png"), dpi=300)
            plt.close(fig)
