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
        SELECT listeners__rna_counts__mRNA_cistron_counts,time FROM ({history_sql})
        ORDER BY time
    """

    exp_id = list(sim_data_paths.keys())[0]
    sim_data_path = list(sim_data_paths[exp_id].values())[0]
    sim_data = LoadSimData(sim_data_path).sim_data

    cistron_data = sim_data.process.transcription.cistron_data
    mrna_cistron_ids = cistron_data["id"][cistron_data["is_mRNA"]].tolist()

    output_df = conn.sql(query).df()

    matrix = np.stack(
        output_df["listeners__rna_counts__mRNA_cistron_counts"].values
    ).astype(int)

    np.savetxt(os.path.join(outdir, "matrix.txt"), matrix)

    time_mins = output_df["time"].values / 60

    mrna = pd.DataFrame(matrix, columns=mrna_cistron_ids)
    mrna["Time (min)"] = time_mins

    gene_id_to_cistron_id = {
        row["gene_id"]: row["id"] for row in cistron_data if row["is_mRNA"]
    }

    flagella_gene_names = {
        "FLHD": "EG10320",
        "FLHC": "EG10319",
        "FLIA": "EG11355",
        "FLGM": "G369",
        "FLIS": "EG11388",
        "FLIK": "G379",
        "FLHA": "G370",
        "FLHB": "G7028",
        "FlIO": "EG11224",
        "FlIP": "EG11975",
        "FlIQ": "EG11976",
        "FlIR": "EG11977",
        "FlIH": "EG11656",
        "FlII": "G377",
        "FLIJ": "G378",
        "FLIF": "EG11347",
        "FLIE": "EG11346",
        "FLIG": "EG11654",
        "FLIM": "EG10323",
        "FLIN": "EG10324",
        "FLGB": "G358",
        "FLGC": "G359",
        "FLGF": "G362",
        "FLGG": "G363",
        "FLGI": "G365",
        "FLGH": "G364",
        "FLIL": "EG10322",
        "MOTA": "EG10601",
        "MOTB": "EG10602",
        "FLGE": "G361",
        "FLGK": "EG11967",
        "FLGL": "EG11545",
        "FLIC": "EG10321",
        "FLID": "EG10841",
    }

    flagella_cistron_rna_ids = {}
    for gene_name, eg_id in flagella_gene_names.items():
        rna_id = gene_id_to_cistron_id.get(eg_id)
        if rna_id:
            flagella_cistron_rna_ids[gene_name] = rna_id
        else:
            print(f"{gene_name}not found in cistron data")

    for gene_name, rna_id in flagella_cistron_rna_ids.items():
        if rna_id not in mrna.columns:
            print(f"{gene_name}not found in mrna data")
            continue

        plt.figure(figsize=(8, 8))
        plt.plot(mrna["Time (min)"], mrna[rna_id], label=gene_name)
        plt.xlabel("Time (min)")
        plt.ylabel("MRNA counts")
        plt.legend()
        plt.title(f"mrna counts for {gene_name}({rna_id})")
        plt.tight_layout()
        plt.savefig(os.path.join(outdir, f"{gene_name}.png"))
        plt.close()
