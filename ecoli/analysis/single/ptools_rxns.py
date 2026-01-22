import os
from typing import Any

from duckdb import DuckDBPyConnection
import polars as pl

from ecoli.library.parquet_emitter import field_metadata

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

    rxn_ids = field_metadata(
        conn, config_sql, "listeners__fba_results__base_reaction_fluxes"
    )
    rxns_query = build_query(
        {"listeners__fba_results__base_reaction_fluxes": "reaction_fluxes"},
        {},
        history_sql,
        params.get("n_tp", 5),
    )
    data = conn.sql(rxns_query).pl()

    if time_unit == "minutes":
        data = data.with_columns(
            [
                (pl.col("bin_start") / 60).alias("bin_start"),
                (pl.col("bin_end") / 60).alias("bin_end"),
            ]
        )

    data = data.with_columns(
        pl.col("reaction_fluxes").list.eval(pl.element().abs()).alias("reaction_fluxes")
    )

    timepoint_cols = [
        str(int(start_time)) for start_time in data["bin_start"].to_list()
    ]
    counts_df = data.select(
        pl.col("reaction_fluxes").list.to_struct(fields=rxn_ids)
    ).unnest("reaction_fluxes")

    wide_table = counts_df.transpose(
        include_header=True,
        header_name="$",
        column_names=timepoint_cols,
    )

    wide_table.write_csv(
        os.path.join(outdir, "ptools_rxns.tsv"),
        separator="\t",
        include_header=True,
        float_precision=4,
    )