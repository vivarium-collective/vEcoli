import os
from typing import Any

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from duckdb import DuckDBPyConnection

from ecoli.library.sim_data import LoadSimData


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
    os.makedirs(outdir, exist_ok=True)

    query = f"""
        SELECT listeners__rna_synth_prob__actual_rna_synth_prob, time
        FROM ({history_sql})
        ORDER BY time
    """
    output_queries = conn.sql(query).df()
    TU_synth_matrix = np.stack(
        output_queries["listeners__rna_synth_prob__actual_rna_synth_prob"].values
    ).astype(float)
    time_mins = output_queries["time"].values / 60.0

    experiment_id = list(sim_data_paths.keys())[0]
    sim_data_path = list(sim_data_paths[experiment_id].values())[0]
    sim_data = LoadSimData(sim_data_path).sim_data

    rna_data = sim_data.process.transcription.rna_data
    rna_ids = rna_data["id"].tolist()

    df = pd.DataFrame(TU_synth_matrix, columns=rna_ids)
    df["Time (min)"] = time_mins

    TU_of_interest = [
        "TU0-14785[c]",  # flhDC
        "TU-8411[c]",  # fliFGHIJK
        "TU0-1441[c]",  # fliLMNOPQR
        # "TU0-1545[c]", #flgK (FlgKL but L is missing from TU)
        "TU0-14278[c]",  # fliD
        #  "TU0-1521[c]", #fliC
        "TU00273[c]",  # FlgBCDEFGHIJ
    ]

    first_row = df.iloc[0]
    last_row = df.iloc[-1]
    rows = []

    for id in TU_of_interest:
        if id in df.columns:
            first_synth_rate = first_row[id]
            last_synth_rate = last_row[id]

            rows.append(
                {
                    "TU_ID": id,
                    "first_synth_prob": first_synth_rate,
                    "last_synth_prob": last_synth_rate,
                }
            )
    synth_rates_table = pd.DataFrame(rows)
    synth_rates_table.to_csv(os.path.join(outdir, "synth_rates.csv"), index=False)

    plt.figure(figsize=(8, 5))

    for tu in TU_of_interest:
        plt.plot(df["Time (min)"], df[tu], label=tu)

    plt.xlabel("Time (min)")
    plt.ylabel("Actual RNA synth prob")
    plt.title("TU-level actual RNA synthesis probabilities")
    plt.legend()
    plt.tight_layout()
    plt.savefig(os.path.join(outdir, "tu_actual_rna_synth_prob.png"), dpi=300)
    plt.close()
