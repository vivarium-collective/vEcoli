import os
from typing import Any
from duckdb import DuckDBPyConnection


import matplotlib.pyplot as plt


import polars as pl


from ecoli.library.parquet_emitter import num_cells, read_stacked_columns


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

    assert num_cells(conn, config_sql) == 1, "Listeners Total"

    listener_columns = {
        "projection": "listeners__mass__projection_mass",
        "cytosols": "listeners__mass__cytosol_mass",
        "extracellular": "listeners__mass__extracellular_mass",
        "flagellum": "listeners__mass__flagellum_mass",
        "membrane": "listeners__mass__membrane_mass",
        "outer membrane": "listeners__mass__outer_membrane_mass",
        "periplasm": "listeners__mass__periplasm_mass",
        "pilus": "listeners__mass__pilus_mass",
        "inner membrane": "listeners__mass__inner_membrane_mass",
    }

    # Dataframe with listener columns (shape: 2529, 14)
    # read_stacked_columns --> loads columns of interest from parquet files and stack them into table to analyze/plot
    # converting to a polars dataframe to analyze and plot the masses overtime
    listener_data = pl.DataFrame(
        read_stacked_columns(history_sql, list(listener_columns.values()), conn=conn)
    )
    # print(listener_data)
    # print(listener_columns)

    # Time
    x = listener_data["time"].to_numpy()

    # .items() on a dictionary, to return both the key and value as a pair (tuple)
    plt.figure(figsize=(10, 6))

    for i, (common_name, raw_name) in enumerate(listener_columns.items()):
        if raw_name in listener_data.columns:
            y = listener_data[raw_name].to_numpy()
            plt.plot(
                x, y, label=common_name, color=COLORS[i % len(COLORS)], linewidth=2
            )

    plt.xlabel("Time (s)")
    plt.ylabel("Mass (fg)")
    plt.title("Mass components over time (seconds)")
    plt.legend(ncol=2, fontsize=9)
    plt.tight_layout()
    # plt.show()
    plt.savefig("mass_plot.png")
    plt.savefig(os.path.join(outdir, "mass_plot.png"))

    for i, (common_name, raw_name) in enumerate(listener_columns.items()):
        if raw_name in listener_data.columns:
            y = listener_data[raw_name].to_numpy()

            plt.figure(figsize=(8, 8))  # this is what resets the canvas every iteration
            plt.plot(
                x, y, label=common_name, color=COLORS[i % len(COLORS)], linewidth=2
            )
            plt.xlabel("Time (s)")
            plt.ylabel("Mass (fg)")
            plt.title(f"{common_name} over time (seconds)")

            plt.tight_layout()
            plt.legend(fontsize=9)
            safe = common_name.lower().replace(
                " ", "_"
            )  # string cleanup step so each PNG is tidy and same
            plt.savefig(os.path.join(outdir, f"{safe}_mass_over_time.png"), dpi=200)
            # plt.show()
            plt.close()
