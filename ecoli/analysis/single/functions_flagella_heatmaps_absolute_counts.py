from typing import Any
import os
from duckdb import DuckDBPyConnection
from ecoli.library.sim_data import LoadSimData
import seaborn as sns
import matplotlib.pyplot as plt
import pandas as pd
import numpy as np


""" 
This script is contains two functions that plot heatmaps of flagella monomers in absolute counts and as log2 counts, 
its an update/rework of flagella_heatmaps_absolute_counts.py
"""


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

    def plot_absolute_heatmap(
        bulk_df: pd.DataFrame,
        monomer_ids,
        title,
        filename,
        percentiles_clip=None,
    ):
        """
        Plots a heatmap of absolute monomer counts
        Returns:
        """

        valid_monomers = [m for m in monomer_ids if m in bulk_df.columns]

        if len(valid_monomers) == 0:
            print("No valid monomers found")
            return

        heatmap_monomers = bulk_df[valid_monomers].to_numpy().T

        vmax = None
        if percentiles_clip is not None:
            vmax = np.percentile(heatmap_monomers, percentiles_clip)

        step_time = max(1, len(time_mins) // 20)
        xticks = np.arange(0, len(time_mins), step_time)
        xtick_labels = np.round(time_mins[xticks], 1)

        plt.figure(figsize=(14, 12))
        ax = sns.heatmap(
            heatmap_monomers,
            cmap="YlOrRd",
            vmin=0,
            vmax=vmax,
            yticklabels=get_common_name_labels(valid_monomers, sim_data),
            cbar_kws={"label": "Absolute Counts"},
        )
        ax.set_title(title)
        ax.set_xticks(xticks)
        ax.set_xticklabels(xtick_labels, rotation=45)
        ax.set_ylabel("Monomers")
        ax.set_xlabel("Time")

        plt.tight_layout()
        plt.savefig(os.path.join(outdir, filename), dpi=200)
        plt.close()

    def plot_log_counts_heatmap(
        bulk_df: pd.DataFrame,
        monomer_ids,
        title,
        filename,
        percentiles_clip=None,
        pseudo=1,
    ):
        valid_monomers = [m for m in monomer_ids if m in bulk_df.columns]

        if len(valid_monomers) == 0:
            print("No valid monomers found")
            return

        # Log transformation with a pseudocount so large values don't dominate
        # basically asking how many copies exist at each time point, not dynamic changes
        normalized_log_data = np.log2(bulk_df[valid_monomers] + pseudo)
        heat_log_data = normalized_log_data.to_numpy().T

        vmax = None
        if percentiles_clip is not None:
            vmax = np.percentile(heat_log_data, percentiles_clip)

        step_time = max(1, len(time_mins) // 20)
        xticks = np.arange(0, len(time_mins), step_time)
        xtick_labels = np.round(time_mins[xticks], 1)

        plt.figure(figsize=(14, 12))
        ax = sns.heatmap(
            heat_log_data,
            cmap="YlOrRd",
            vmin=0,
            vmax=vmax,
            yticklabels=get_common_name_labels(valid_monomers, sim_data),
            cbar_kws={"label": "Log2 (Counts + Pseudo)"},
        )
        ax.set_title(title)
        ax.set_xlabel("Time")
        ax.set_ylabel("Monomers")
        ax.set_xticks(xticks)
        ax.set_xticklabels(xtick_labels, rotation=45)

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

    plot_absolute_heatmap(
        bulk_df,
        flagella_rxn_monomers,
        "Total Flagella Monomers Over Time (Absolute Counts)",
        filename="total_flagella_monomers_absolute.png",
        percentiles_clip=99,
    )

    plot_log_counts_heatmap(
        bulk_df,
        flagella_rxn_monomers,
        "Total Flagella Monomers Over Time (Log Counts)",
        filename="total_flagella_monomers_log.png",
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

    plot_absolute_heatmap(
        bulk_df,
        monomers_of_apparatus_ids,
        title="Export Apparatus Monomers Overtime (Absolute Counts)",
        filename="export_flagella_monomers_absolute.png",
        percentiles_clip=99,
    )

    plot_log_counts_heatmap(
        bulk_df,
        monomers_of_apparatus_ids,
        title="Export Apparatus Monomers Overtime (Log Counts)",
        filename="export_flagella_monomers_log.png",
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

    plot_absolute_heatmap(
        bulk_df,
        monomers_of_motor_complex,
        title="Motor Complex Monomers Overtime (Absolute Counts)",
        filename="motor_complex_flagella_monomers_absolute.png",
        percentiles_clip=99,
    )

    plot_log_counts_heatmap(
        bulk_df,
        monomers_of_motor_complex,
        title="Motor Complex Monomers Overtime (Log Counts)",
        filename="motor_complex_flagella_monomers_log.png",
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

    plot_absolute_heatmap(
        bulk_df,
        monomers_of_flagellum_ids,
        title="Flagellum RXN Monomers Overtime (Absolute Counts)",
        filename="flagella_rxn_monomers_absolute.png",
        percentiles_clip=99,
    )

    plot_log_counts_heatmap(
        bulk_df,
        monomers_of_flagellum_ids,
        title="Flagellum RXN Overtime (Log Counts)",
        filename="flagella_rxn_monomers_log.png",
    )
