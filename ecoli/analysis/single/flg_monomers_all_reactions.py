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

    # 29 monomers of interest
    flagella_rxn_monomers = [
        "FLAGELLAR-MOTOR-COMPLEX[j]",
        "FLGB-FLAGELLAR-MOTOR-ROD-PROTEIN[j]",
        "FLGC-FLAGELLAR-MOTOR-ROD-PROTEIN[j]",
        "FLGF-FLAGELLAR-MOTOR-ROD-PROTEIN[j]",
        "FLGG-FLAGELLAR-MOTOR-ROD-PROTEIN[o]",
        "FLGH-FLAGELLAR-L-RING[j]",  # should this be [o]
        "FLGI-FLAGELLAR-P-RING[j]",
        "FLIF-FLAGELLAR-MS-RING[i]",
        "FLIG-FLAGELLAR-SWITCH-PROTEIN[i]",
        "FLIM-FLAGELLAR-C-RING-SWITCH[i]",
        "FLIN-FLAGELLAR-C-RING-SWITCH[m]",
        "MOTA-FLAGELLAR-MOTOR-STATOR-PROTEIN[i]",
        "MOTB-FLAGELLAR-MOTOR-STATOR-PROTEIN[i]",
        "G7028-MONOMER[i]",  # FlhB
        "G378-MONOMER[c]",  # FliJ
        "G377-MONOMER[c]",  # FliI (double check if this is I or L )
        "EG11656-MONOMER[c]",  # FliH
        "G361-MONOMER[c]",  # flgE
        "G370-MONOMER[i]",  # FlhA
        "EG11977-MONOMER[i]",  # FliR
        "EG11975-MONOMER[i]",  # FliP
        "EG11976-MONOMER[j]",  # FliQ THIS IS WRONG AND SHOULD BE [i]
        "EG11224-MONOMER[j]",  # FliO
        "EG10322-MONOMER[j]",  # FliL          # should be p?
        "EG11346-MONOMER[p]",  # FliE
        "EG11967-MONOMER[e]",  # FlgK
        "EG11545-MONOMER[e]",  # FlgL
        "EG10321-MONOMER[e]",  # FliC
        "EG10841-MONOMER[e]",  # FliD
    ]

    for mon in flagella_rxn_monomers:
        if mon in bulk_ids_df.columns:
            idx = bulk_ids_df[mon] / bulk_ids_df[mon].iloc[0]
            plt.plot(time_mins, idx, label=mon)

        plt.xlabel("time (min)")
        plt.ylabel("All Flagella Monomers Normalized Abundance")
        plt.legend(bbox_to_anchor=(1.05, 1), loc=2)
        plt.tight_layout()
        plt.savefig(os.path.join(outdir, "flagella_reaction_monomers.png"), dpi=300)

    for mon in flagella_rxn_monomers:
        if mon in bulk_ids_df.columns:
            plt.figure(figsize=(8, 6))
            plt.plot(time_mins, bulk_ids_df[mon], label=mon)
            plt.xlabel("time (min)")
            plt.ylabel("Flagella Monomer Normalized Abundance")
            plt.legend(bbox_to_anchor=(1.05, 1), loc=2)
            plt.tight_layout()
            plt.savefig(os.path.join(outdir, f"{mon}_timecourse_plot.png"))
            plt.close()
