import os
from typing import Any
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt


from duckdb import DuckDBPyConnection
from matplotlib.ticker import MaxNLocator

from ecoli.library.sim_data import LoadSimData


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

    # query_dict = {
    #     "experiment_id": "test_installation",
    #     "variant": 0,
    #     "lineage_seed": 0,
    #     "generation": 1,
    # }
    #
    # # Queries all columns from the history parquet files,
    # query = f"""
    #     SELECT bulk,time FROM read_parquet("out/{query_dict["experiment_id"]}/history/*/*/*/*/*/*.pq", hive_partitioning=true)
    #     WHERE variant={query_dict["variant"]}
    #     AND lineage_seed={query_dict["lineage_seed"]}
    #     AND generation={query_dict["generation"]}
    #     ORDER BY time
    # """

    query = f"""
           SELECT bulk,time FROM ({history_sql})
           ORDER BY time
       """

    experiment_id = list(sim_data_paths.keys())[0]
    sim_data_paths = list(sim_data_paths[experiment_id].values())[0]
    sim_data = LoadSimData(sim_data_paths).sim_data

    # Bulk IDs
    bulk_matrix_ids = sim_data.internal_state.bulk_molecules.bulk_data["id"].tolist()

    # complex_data = sim_data.process.complexation
    # complexation_reactions = sim_data.process.complexation.ids_reactions
    # complex_ids = complex_data["ids_complexes"].tolist()
    # matrix = np.stack(output_queries["listeners__complexation_listener__complexation_events"].values).astype(int)

    output_queries = conn.sql(query).df()

    bulk_matrix = np.stack(output_queries["bulk"].values).astype(int)

    np.savetxt(os.path.join(outdir, "bulk_matrix.txt"), bulk_matrix)

    time_mins = output_queries["time"].values / 60

    complex_df = pd.DataFrame(bulk_matrix, columns=bulk_matrix_ids)

    complex_df["Time (min)"] = time_mins

    # This it the flagellum reaction ID - found in bulk
    flagella_complex = [
        #  "CPLX0-7451[j]", 05/05 - commented this out because using this script for prelim figures
        # "FLAGELLAR-MOTOR-COMPLEX[j]",
        "CPLX0-7452[j]"
    ]

    for flg in flagella_complex:
        if flg in complex_df.columns:
            plt.figure(figsize=(8, 5))
            plt.plot(
                complex_df["Time (min)"], complex_df[flg], label="Assembled Flagella"
            )
            plt.xlabel("Time (min)", fontsize=12)
            plt.ylabel("Assembled Flagellum Complexes", fontsize=12)
            plt.legend()
            plt.xticks(fontsize=11)
            plt.yticks(fontsize=11)
            plt.legend(fontsize=11)
            ax = plt.gca()
            ax.yaxis.set_major_locator(MaxNLocator(integer=True))
            plt.title("Manual Perturbation Model", fontsize=14)
            plt.tight_layout()
            plt.savefig(os.path.join(outdir, f"{flg}_timecourse_plot.png"), dpi=300)
            plt.close()


# plot for all complexes on 1 scale
# for flg in flagella_complex:
#     if flg in complex_df.columns:
#             idx = (complex_df[flg] - complex_df[flg].iloc[0]) #how many new complexes of each one did the model assemble overtime
#             plt.plot(complex_df["Time (min)"], idx, label=flg)
#     plt.xlabel("Time (min)")
#     plt.ylabel("Abundance")
#     plt.legend(bbox_to_anchor=(1.05, 1), loc=2)
#     plt.title(f"Timecourse of All Flagella Complexes")
#     plt.tight_layout()
#     plt.savefig(os.path.join(outdir, "all_flagella_complexes.png"), dpi=300)
#     plt.show()
