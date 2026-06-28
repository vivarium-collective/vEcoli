from typing import Any
import os
from duckdb import DuckDBPyConnection
from ecoli.library.sim_data import LoadSimData

import seaborn as sns
import matplotlib.pyplot as plt
import pandas as pd
import numpy as np


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

    query_df = conn.sql(query).df()

    exp_ids = list(sim_data_paths.keys())[0]
    sim_data_values = list(sim_data_paths[exp_ids].values())[0]
    sim_data = LoadSimData(sim_data_values).sim_data
    sim_data_ids = sim_data.internal_state.bulk_molecules.bulk_data["id"].tolist()

    # Helper function to get clean labels rather than EcoCyc IDs
    def get_common_name_labels(monomer_ids, sim_data):
        labels = []
        for full_id in monomer_ids:
            base_id = full_id.split("[")[0]  # strips the compartment label
            common_name = sim_data.common_names.get_common_name(base_id)
            if common_name is None:
                common_name = base_id

            labels.append(common_name)
        return labels

    ids_as_array = np.stack(query_df["bulk"].values).astype(int)
    time_mins = query_df["time"].values / 60

    bulk_df = pd.DataFrame(ids_as_array, columns=sim_data_ids)
    bulk_df["time"] = time_mins

    def plot_log2FC(
        bulk_df,
        time_mins,
        monomer_ids,
        title,
        filename,
    ):
        """
        This function creates a log2FC heatmap, each normalized to t0, its own initial abundance
        Answers the question: Which proteins increase/decrease relative to their starting abundance?
            Showing dynamic regulation over time

        Blue --> downregulated relative to start value
        White --> unchanged
        Red --> upregulated relative to start value
        """

        valid_monomers = [m for m in monomer_ids if m in bulk_df.columns]
        psuedo = 0.5
        normalized_to_initial = (bulk_df[valid_monomers] + psuedo).div(
            bulk_df[valid_monomers].iloc[0] + psuedo
        )
        log2_change_data = np.log2(normalized_to_initial)
        heat_log2_data = log2_change_data.to_numpy().T

        step_time = max(1, len(time_mins) // 20)
        xticks = np.arange(0, len(time_mins), step_time)
        xtick_labels = np.round(time_mins[xticks], 1)

        plt.figure(figsize=(14, 12))
        ax = sns.heatmap(
            heat_log2_data,
            cmap="coolwarm",
            xticklabels=False,
            yticklabels=get_common_name_labels(valid_monomers, sim_data),
            vmax=1,
            vmin=-1,
            cbar=True,
        )
        ax.set_xticks(xticks)
        ax.set_xticklabels(xtick_labels, rotation=45)
        ax.set_title(title)
        ax.set_xlabel("Time (mins)")
        ax.set_ylabel("Monomers from Bulk")

        plt.tight_layout()
        plt.savefig(os.path.join(outdir, filename), dpi=200)
        plt.close()

    flagella_rxn_monomers = [
        "G370-MONOMER[i]",  # FlhA
        "G7028-MONOMER[i]",  # FlhB
        "EG11224-MONOMER[j]",  # FliO
        "EG11975-MONOMER[i]",  # FliP
        "EG11976-MONOMER[j]",  # FliQ THIS IS WRONG AND SHOULD BE [i]
        "EG11977-MONOMER[i]",  # FliR
        "EG11656-MONOMER[c]",  # FliH
        "G377-MONOMER[c]",  # FliI
        "G378-MONOMER[c]",  # FliJ
        "CPLX0-7451[j]",
        "FLIF-FLAGELLAR-MS-RING[i]",
        "EG11346-MONOMER[p]",  # FliE
        "FLIG-FLAGELLAR-SWITCH-PROTEIN[i]",
        "FLIM-FLAGELLAR-C-RING-SWITCH[i]",
        "FLIN-FLAGELLAR-C-RING-SWITCH[m]",
        "FLGB-FLAGELLAR-MOTOR-ROD-PROTEIN[j]",
        "FLGC-FLAGELLAR-MOTOR-ROD-PROTEIN[j]",
        "FLGF-FLAGELLAR-MOTOR-ROD-PROTEIN[j]",
        "FLGG-FLAGELLAR-MOTOR-ROD-PROTEIN[o]",
        "FLGI-FLAGELLAR-P-RING[j]",
        "FLGH-FLAGELLAR-L-RING[j]",  # should this be [o]
        "FLAGELLAR-MOTOR-COMPLEX[j]",
        "EG10322-MONOMER[j]",  # FliL -  should be p?
        "MOTA-FLAGELLAR-MOTOR-STATOR-PROTEIN[i]",
        "MOTB-FLAGELLAR-MOTOR-STATOR-PROTEIN[i]",
        "G361-MONOMER[c]",  # flgE
        "EG11967-MONOMER[e]",  # FlgK
        "EG11545-MONOMER[e]",  # FlgL
        "EG10321-MONOMER[e]",  # FliC
        "EG10841-MONOMER[e]",  # FliD
        "CPLX0-7452[j]",
    ]

    plot_log2FC(
        bulk_df,
        time_mins,
        flagella_rxn_monomers,
        title="Total Flagella Monomers Over Time (Log2FC and Normalized to t0)",
        filename="total_flagella_heatmap_log2FC.png",
    )

    monomers_of_apparatus_ids = [
        "G370-MONOMER[i]",  # FlhA
        "G7028-MONOMER[i]",  # FlhB
        "EG11224-MONOMER[j]",  # FliO
        "EG11975-MONOMER[i]",  # FliP
        "EG11976-MONOMER[j]",  # FliQ THIS IS WRONG AND SHOULD BE [i]
        "EG11977-MONOMER[i]",  # FliR
        "EG11656-MONOMER[c]",  # FliH
        "G377-MONOMER[c]",  # FliI
        "G378-MONOMER[c]",  # FliJ
        "CPLX0-7451[j]",
    ]

    plot_log2FC(
        bulk_df,
        time_mins,
        monomers_of_apparatus_ids,
        title="Export Apparatus Flagella Monomers Over Time (Log2FC and Normalized to t0)",
        filename="export_flagella_heatmap_log2FC.png",
    )

    monomers_of_motor_complex = [
        "FLIF-FLAGELLAR-MS-RING[i]",
        "EG11346-MONOMER[p]",  # FliE
        "FLIG-FLAGELLAR-SWITCH-PROTEIN[i]",
        "FLIM-FLAGELLAR-C-RING-SWITCH[i]",
        "FLIN-FLAGELLAR-C-RING-SWITCH[m]",
        "FLGB-FLAGELLAR-MOTOR-ROD-PROTEIN[j]",
        "FLGC-FLAGELLAR-MOTOR-ROD-PROTEIN[j]",
        "FLGF-FLAGELLAR-MOTOR-ROD-PROTEIN[j]",
        "FLGG-FLAGELLAR-MOTOR-ROD-PROTEIN[o]",
        "FLGI-FLAGELLAR-P-RING[j]",
        "FLGH-FLAGELLAR-L-RING[j]",  # should this be [o]
        "EG10322-MONOMER[j]",  # FliL -  should be p?
        "MOTA-FLAGELLAR-MOTOR-STATOR-PROTEIN[i]",
        "MOTB-FLAGELLAR-MOTOR-STATOR-PROTEIN[i]",
        "FLAGELLAR-MOTOR-COMPLEX[j]",
    ]

    plot_log2FC(
        bulk_df,
        time_mins,
        monomers_of_motor_complex,
        title="Motor Complex Flagella Monomers Over Time (Log2FC and Normalized to t0)",
        filename="motor_complex_heatmap_log2FC.png",
    )

    monomers_of_flagellum_ids = [
        "FLAGELLAR-MOTOR-COMPLEX[j]",
        "G361-MONOMER[c]",  # flgE
        "EG11967-MONOMER[e]",  # FlgK
        "EG11545-MONOMER[e]",  # FlgL
        "EG10321-MONOMER[e]",  # FliC
        "EG10841-MONOMER[e]",  # FliD
        "CPLX0-7452[j]",
    ]

    plot_log2FC(
        bulk_df,
        time_mins,
        monomers_of_flagellum_ids,
        title="Flagellum RXN Monomers Over Time (Log2FC and Normalized to t0)",
        filename="flagellum_rxn_heatmap_log2FC.png",
    )
