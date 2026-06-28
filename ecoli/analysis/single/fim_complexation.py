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

    # Could not plot complexation -- switched to monomer_counts

    query = f"""
        SELECT listeners__monomer_counts,time FROM ({history_sql})
        ORDER BY time
    """

    exp_id = list(sim_data_paths.keys())[0]
    sim_data_path = list(sim_data_paths[exp_id].values())[0]
    sim_data = LoadSimData(sim_data_path).sim_data

    fim_complex = sim_data.process.translation.monomer_data
    fim_ids = fim_complex["id"].tolist()

    output_df = conn.sql(query).df()

    matrix = np.stack(output_df["listeners__monomer_counts"].values).astype(int)

    np.savetxt(os.path.join(outdir, "matrix.txt"), matrix)

    time_mins = output_df["time"].values / 60

    mrna = pd.DataFrame(matrix, columns=fim_ids)

    mrna["Time (min)"] = time_mins

    # mrna_interest = [
    #     "metQ[c]",
    #     "fimA[c]"
    # ]

    fimbrial_complex = [
        "ZNUA-MONOMER[p]"  # fimbrial complex name ID
    ]

    for mol in fimbrial_complex:
        if mol in mrna.columns:
            plt.figure(figsize=(8, 8))
            plt.plot(mrna["Time (min)"], mrna[mol], label=mol)
            plt.xlabel("Time (min)")
            plt.ylabel("MRNA counts")
            plt.legend()
            plt.title("mrna counts for {}".format(mol))
            plt.tight_layout()
            plt.savefig(os.path.join(outdir, "{}.png".format(mol)))
            plt.close()
