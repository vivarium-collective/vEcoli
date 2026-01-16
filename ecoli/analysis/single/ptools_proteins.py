import os
from typing import Any, cast

from duckdb import DuckDBPyConnection
import polars as pl
import pickle

from ecoli.library.parquet_emitter import (
    field_metadata,
    open_arbitrary_sim_data,
    read_stacked_columns,
)

from ecoli.analysis.single.ptools_rna import build_query
from reconstruction.ecoli.simulation_data import SimulationDataEcoli


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
    if not params.get("time_unit") or params["time_unit"] not in ["minutes", "seconds"]:
        params["time_unit"] = "minutes"

    with open_arbitrary_sim_data(sim_data_paths) as f:
        sim_data: "SimulationDataEcoli" = pickle.load(f)
    monomer_ids = field_metadata(conn, config_sql, "listeners__monomer_counts")
    monomer_subquery = cast(
        str,
        read_stacked_columns(
            history_sql,
            ["listeners__monomer_counts"],
            remove_first=True,
            order_results=False,
        ),
    )
    monomer_sql = build_query(
        {"listeners__monomer_counts": "monomer_counts"},
        {},
        monomer_subquery,
        params.get("n_tp", 5),
    )
    data = conn.sql(monomer_sql).pl()
    cistron_id_to_gene_id = {
        cistron["id"]: cistron["gene_id"]
        for cistron in sim_data.process.transcription.cistron_data
    }
    monomer_sim_data = sim_data.process.translation.monomer_data.struct_array
    monomer_to_gene_id = cast(
        dict[str, str],
        {
            monomer_id: cistron_id_to_gene_id[cistron_id]
            for cistron_id, monomer_id in zip(
                monomer_sim_data["cistron_id"], monomer_sim_data["id"]
            )
        },
    )
    gene_ids = [monomer_to_gene_id[monomer_id] for monomer_id in monomer_ids]

    time_unit = params["time_unit"]
    if time_unit == "minutes":
        data = data.with_columns(
            [
                (pl.col("bin_start") / 60).alias("bin_start"),
                (pl.col("bin_end") / 60).alias("bin_end"),
            ]
        )

    timepoint_cols = [
        str(int(start_time)) for start_time in data["bin_start"].to_list()
    ]
    counts_df = data.select(
        pl.col("monomer_counts").list.to_struct(fields=gene_ids)
    ).unnest("monomer_counts")

    wide_table = counts_df.transpose(
        include_header=True,
        header_name="gene_id",
        column_names=timepoint_cols,
    )

    wide_table.write_csv(
        os.path.join(outdir, "ptools_proteins.txt"),
        separator="\t",
        include_header=True,
        float_precision=4,
    )
