from typing import Any
import os
from duckdb import DuckDBPyConnection
from ecoli.library.sim_data import LoadSimData
import seaborn as sns
import matplotlib.pyplot as plt
import pandas as pd
import numpy as np


"""
This script manually plotted the absolute counts of flagella monomers both in absolute counts and by log2 counts, 
refer to functions_flagella_heatmaps_absolute_counts.py for the same code in function/modular form
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

    ids_as_array = np.stack(query_df["bulk"].values).astype(int)
    time_mins = query_df["time"].values / 60

    bulk_df = pd.DataFrame(ids_as_array, columns=sim_data_ids)
    bulk_df["time"] = time_mins

    # Flagella monomers of interest to plot in heatmap
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

    valid_monomers = [m for m in flagella_rxn_monomers if m in bulk_df.columns]
    all_monomers_heatmap_data = bulk_df[valid_monomers].to_numpy().T

    # Because of the data variation in scales, we should make with log absolute counts
    normalized_log_all = np.log2(bulk_df[valid_monomers] + 1)
    heat_normalized_log_absolute = normalized_log_all.to_numpy().T

    # Heatmap production, non normalized absolute counts
    step_time = max(
        1, len(time_mins) // 20
    )  # Too many timepoints --> need to take steps
    xticks = np.arange(0, len(time_mins), step_time)
    plt.figure(figsize=(20, max(6, len(valid_monomers) * 0.3)))
    ax = sns.heatmap(
        all_monomers_heatmap_data,
        cmap="YlOrRd",
        xticklabels=False,
        # vmin=-1, vmax=1,
        yticklabels=valid_monomers,
        cbar=True,
    )
    ax.set_xticks(xticks)
    ax.set_xticklabels([f"{time_mins[i]:.1f}" for i in xticks], rotation=45)
    plt.xlabel("Time (mins)")
    plt.ylabel("Monomers")
    plt.title("Total Flagella Monomers Over Time (Absolute Counts)")
    plt.xticks(rotation=45)
    plt.tight_layout()
    plt.savefig(os.path.join(outdir, "flagella_heatmap_abs_counts.png"), dpi=200)
    plt.close()

    # Heatmap production, all monomers log normalized absolute counts
    step_time = max(
        1, len(time_mins) // 20
    )  # Too many timepoints --> need to take steps
    xticks = np.arange(0, len(time_mins), step_time)
    plt.figure(figsize=(20, max(6, len(valid_monomers) * 0.3)))
    ax = sns.heatmap(
        heat_normalized_log_absolute,
        cmap="YlOrRd",
        xticklabels=False,
        vmin=0,
        vmax=np.percentile(
            heat_normalized_log_absolute, 99
        ),  # sets upper color limit of the 99th percentile of the data
        yticklabels=valid_monomers,
        cbar=True,
    )
    ax.set_xticks(xticks)
    ax.set_xticklabels([f"{time_mins[i]:.1f}" for i in xticks], rotation=45)
    plt.xlabel("Time (mins)")
    plt.ylabel("Monomers")
    plt.title("Total Flagella Monomers Over Time (Log Absolute Counts)")
    plt.xticks(rotation=45)
    plt.tight_layout()
    plt.savefig(os.path.join(outdir, "flagella_heatmap_log.png"), dpi=200)
    plt.close()

    """
    HeatMap of the Export Apparatus Only CPLX074-51
    TODO: make a current state of stoichiometry vs. expected/corrected state to compare the or see the ratios
    """

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

    monomers_apparatus = [m for m in monomers_of_apparatus_ids if m in bulk_df.columns]
    apparatus_heatmap_counts = bulk_df[monomers_apparatus].to_numpy().T

    normalized_apparatus_log = np.log2(bulk_df[monomers_apparatus] + 1)
    heat_normalized_apparatus_log = normalized_apparatus_log.to_numpy().T

    # Heatmap production of export apparatus, non normalized absolute counts
    step_time = max(1, len(time_mins) // 20)  # Too many timepoints need to take steps
    xticks = np.arange(0, len(time_mins), step_time)
    plt.figure(figsize=(20, max(6, len(monomers_apparatus) * 0.3)))
    ax = sns.heatmap(
        apparatus_heatmap_counts,
        cmap="YlOrRd",
        xticklabels=False,
        yticklabels=monomers_apparatus,
        cbar=True,
    )
    ax.set_xticks(xticks)
    ax.set_xticklabels([f"{time_mins[i]:.1f}" for i in xticks], rotation=45)
    plt.xlabel("Time (mins)")
    plt.ylabel("Monomers")
    plt.title("Flagella Export Apparatus Monomers Over Time (Absolute Counts)")
    plt.xticks(rotation=45)
    plt.tight_layout()
    plt.savefig(os.path.join(outdir, "flg_apparatus_abs_counts.png"), dpi=200)
    plt.close()

    # Heatmap production of export apparatus, normalized absolute counts
    step_time = max(1, len(time_mins) // 20)  # Too many timepoints need to take steps
    xticks = np.arange(0, len(time_mins), step_time)
    plt.figure(figsize=(20, max(6, len(monomers_apparatus) * 0.3)))
    ax = sns.heatmap(
        heat_normalized_apparatus_log,
        cmap="YlOrRd",
        xticklabels=False,
        yticklabels=monomers_apparatus,
        vmin=0,
        vmax=np.percentile(heat_normalized_apparatus_log, 99),
        cbar=True,
    )
    ax.set_xticks(xticks)
    ax.set_xticklabels([f"{time_mins[i]:.1f}" for i in xticks], rotation=45)
    plt.xlabel("Time (mins)")
    plt.ylabel("Monomers")
    plt.title("Flagella Export Apparatus Monomers Over Time (Log Absolute Counts)")
    plt.xticks(rotation=45)
    plt.tight_layout()
    plt.savefig(os.path.join(outdir, "flg_apparatus_log.png"), dpi=200)
    plt.close()

    """
    HeatMap of the Flagellum Motor Complex 
    """

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

    monomers_motor = [m for m in monomers_of_motor_complex if m in bulk_df.columns]
    motor_complex_counts = bulk_df[monomers_motor].to_numpy().T

    normalized_motor_log = np.log2(bulk_df[monomers_motor] + 1)
    heat_normalized_motor_log = normalized_motor_log.to_numpy().T

    # Heatmap production of motor complex, non normalized absolute counts
    step_time = max(1, len(time_mins) // 20)  # Too many timepoints need to take steps
    xticks = np.arange(0, len(time_mins), step_time)
    plt.figure(figsize=(20, max(6, len(monomers_motor) * 0.3)))
    ax = sns.heatmap(
        motor_complex_counts,
        cmap="YlOrRd",
        xticklabels=False,
        yticklabels=monomers_motor,
        cbar=True,
    )
    ax.set_xticks(xticks)
    ax.set_xticklabels([f"{time_mins[i]:.1f}" for i in xticks], rotation=45)
    plt.xlabel("Time (mins)")
    plt.ylabel("Monomers")
    plt.title("Flagella Motor Complex Monomers Over Time (Absolute Counts)")
    plt.xticks(rotation=45)
    plt.tight_layout()
    plt.savefig(os.path.join(outdir, "flg_motor_abs_counts.png"), dpi=200)
    plt.close()

    # Heatmap production of motor complex, normalized absolute counts
    step_time = max(1, len(time_mins) // 20)  # Too many timepoints need to take steps
    xticks = np.arange(0, len(time_mins), step_time)
    plt.figure(figsize=(20, max(6, len(monomers_motor) * 0.3)))
    ax = sns.heatmap(
        heat_normalized_motor_log,
        cmap="YlOrRd",
        xticklabels=False,
        yticklabels=monomers_motor,
        cbar=True,
    )
    ax.set_xticks(xticks)
    ax.set_xticklabels([f"{time_mins[i]:.1f}" for i in xticks], rotation=45)
    plt.xlabel("Time (mins)")
    plt.ylabel("Monomers")
    plt.title("Flagella Motor Complex Monomers Over Time (Log Absolute Counts)")
    plt.xticks(rotation=45)
    plt.tight_layout()
    plt.savefig(os.path.join(outdir, "flg_motor_log.png"), dpi=200)
    plt.close()

    """
    Heatmap for the Flagellum Reaction (CPLX0-7452)
    """

    monomers_of_flagellum_ids = [
        "FLAGELLAR-MOTOR-COMPLEX[j]",
        "G361-MONOMER[c]",  # flgE
        "EG11967-MONOMER[e]",  # FlgK
        "EG11545-MONOMER[e]",  # FlgL
        "EG10321-MONOMER[e]",  # FliC
        "EG10841-MONOMER[e]",  # FliD
        "CPLX0-7452[j]",
    ]

    monomers_of_flagellum = [
        m for m in monomers_of_flagellum_ids if m in bulk_df.columns
    ]
    flagellum_counts = bulk_df[monomers_of_flagellum].to_numpy().T

    normalized_flagellum_log = np.log2(bulk_df[monomers_of_flagellum] + 1)
    heat_normalized_flagellum_log = normalized_flagellum_log.to_numpy().T

    # Heatmap production of flagellum, non normalized absolute counts
    step_time = max(1, len(time_mins) // 20)
    xticks = np.arange(0, len(time_mins), step_time)
    plt.figure(figsize=(20, max(6, len(monomers_of_flagellum) * 0.3)))
    ax = sns.heatmap(
        flagellum_counts,
        cmap="YlOrRd",
        xticklabels=False,
        yticklabels=monomers_of_flagellum,
        cbar=True,
    )
    ax.set_xticks(xticks)
    ax.set_xticklabels([f"{time_mins[i]:.1f}" for i in xticks], rotation=45)
    plt.xlabel("Time (mins)")
    plt.ylabel("Monomers")
    plt.title("Flagellum RXN Monomers Over Time (Absolute Counts)")
    plt.tight_layout()
    plt.savefig(os.path.join(outdir, "flg_flagellum_abs_counts.png"), dpi=200)
    plt.close()

    # Heatmap production of flagellum, normalized absolute counts
    step_time = max(1, len(time_mins) // 20)
    xticks = np.arange(0, len(time_mins), step_time)
    plt.figure(figsize=(20, max(6, len(monomers_of_flagellum) * 0.3)))
    ax = sns.heatmap(
        heat_normalized_flagellum_log,
        cmap="YlOrRd",
        xticklabels=False,
        yticklabels=monomers_of_flagellum,
        cbar=True,
    )
    ax.set_xticks(xticks)
    ax.set_xticklabels([f"{time_mins[i]:.1f}" for i in xticks], rotation=45)
    plt.xlabel("Time (mins)")
    plt.ylabel("Monomers")
    plt.title("Flagellum RXN Monomers Over Time (Log Absolute Counts)")
    plt.tight_layout()
    plt.savefig(os.path.join(outdir, "flg_flagellum_log.png"), dpi=200)
    plt.close()
