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
        SELECT bulk, listeners__monomer_counts,time FROM ({history_sql})
        ORDER BY time
    """

    # sim data
    exp_id = list(sim_data_paths.keys())[0]
    sim_data_path = list(sim_data_paths[exp_id].values())[0]
    sim_data = LoadSimData(sim_data_path).sim_data

    # translation monomer data
    monomer_data = sim_data.process.translation.monomer_data
    monomer_id = monomer_data["id"].tolist()

    out = conn.sql(query).df()
    mono_matrix = np.stack(out["listeners__monomer_counts"].values).astype(int)

    # make DF and get time
    monomer = pd.DataFrame(mono_matrix, columns=monomer_id)
    monomer["time_s"] = out["time"].values
    time_mins = out["time"].values / 60

    monomer_labels = [
        "EG10308-MONOMER[c]",  # not found
        "EG10308_RNA",  # not found
        "EG10308-MONOMER[e]",
        "EG10309-MONOMER[c]",
        "EG10310-MONOMER[p]",
        "EG10310_RNA[c]",  # not found
        "EG10311-MONOMER[o]",
        "EG10311_RNA[c]",  # not found
        "EG10312-MONOMER[c]",
        "EG10313-MONOMER[l]",
        "EG10313_RNA[c]",  # not found
        "EG10314-MONOMER[l]",
        "EG10314_RNA[c]",  # not found
        "EG10315-MONOMER[l]",
        "EG10315_RNA[c]",  # not found
    ]

    plt.figure(figsize=(10, 6))

    present = [m for m in monomer_labels if m in monomer.columns]
    for i, m in enumerate(present):
        x = time_mins
        y = monomer[m].to_numpy()
        plt.plot(x, y, label=m, linewidth=2, color=COLORS[i % len(COLORS)])

    plt.xlabel("Time (s)")
    plt.ylabel("Monomer count (molecules)")
    plt.title("Selected monomer counts over time")
    plt.legend(ncol=2, fontsize=9)
    plt.tight_layout()
    os.makedirs(outdir, exist_ok=True)
    plt.savefig(os.path.join(outdir, "monomers_selected_all.png"), dpi=200)
    plt.close()

    # figure to generate a plot per fim monomer from monomer_counts
    for i, m in enumerate(present):
        if m in monomer.columns:
            x = time_mins
            y = monomer[m].to_numpy()
            plt.figure(figsize=(8, 8))
            plt.plot(x, y, label=m, linewidth=2, color=COLORS[i % len(COLORS)])
            plt.xlabel("Time (s)")
            plt.ylabel("Monomer count")
            plt.tight_layout()
            plt.savefig(os.path.join(outdir, f"{m}_monomers.png"))
