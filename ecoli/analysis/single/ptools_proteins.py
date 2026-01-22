import os
from typing import Any, cast

from duckdb import DuckDBPyConnection
import polars as pl

from ecoli.library.parquet_emitter import (
    field_metadata,
    read_stacked_columns,
)

from ecoli.analysis.single.ptools_rna import build_query


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
    time_unit = params.get("time_unit", "minutes")
    if time_unit not in ["seconds", "minutes"]:
        time_unit = "minutes"

    monomer_ids = field_metadata(conn, config_sql, "listeners__monomer_counts")
    monomer_subquery = cast(
        str,
        read_stacked_columns(
            history_sql,
            ["listeners__monomer_counts"],
            remove_first=False,
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
    protein_labels = [monomer[:-3] for monomer in monomer_ids]
    counts_df = data.select(
        pl.col("monomer_counts").list.to_struct(fields=protein_labels)
    ).unnest("monomer_counts")

    wide_table = counts_df.transpose(
        include_header=True,
        header_name="$",
        column_names=timepoint_cols,
    )

    wide_table.write_csv(
        os.path.join(outdir, "ptools_proteins.tsv"),
        separator="\t",
        include_header=True,
        float_precision=4,
    )