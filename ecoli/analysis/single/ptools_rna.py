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
from reconstruction.ecoli.simulation_data import SimulationDataEcoli


def build_query(
    list_col: dict[str, str],
    add_cols: dict[str, str],
    history_sql: str,
    time_bins: int = 5,
):
    """
    Builds an SQL query to aggregate simulation history data into time bins.

    Args:
        list_col: Dictionary mapping from list column name (ONLY ONE) to desired output name.
        add_cols: Dictionary mapping additional columns to their desired output names.
        history_sql: SQL string to retrieve the simulation history data.
        time_bins: Number of time bins to aggregate data into.
    """
    assert len(list_col) == 1, "Only one list column can be processed at a time."
    list_col_name = list(list_col.keys())[0]
    list_col_output_name = list_col[list_col_name]
    retrieve_other_cols = ", ".join(
        [f"h.{col} AS {alias}" for col, alias in add_cols.items()]
    )
    avg_other_cols = ", ".join(
        [f"AVG({alias}) AS {alias}" for alias in add_cols.values()]
    )
    query_sql = f"""
        WITH history AS ({history_sql}),
        limits AS (
        SELECT min(time) AS min_t, max(time) AS max_t FROM history
        ),
        windows AS (
        SELECT
            r.bin_idx,
            min_t + r.bin_idx * ((max_t - min_t) / {time_bins}) AS bin_start,
            min_t + (r.bin_idx + 1) * ((max_t - min_t) / {time_bins}) AS bin_end
        FROM limits
        CROSS JOIN range({time_bins}) AS r(bin_idx)
        ),
        exploded AS (
        SELECT
            w.bin_idx,
            w.bin_start,
            w.bin_end,
            generate_subscripts(h.{list_col_name}, 1) AS list_idx,
            unnest(h.{list_col_name}) AS list_val,
            {retrieve_other_cols}
        FROM history h, windows w
        WHERE h.time >= w.bin_start AND h.time < w.bin_end
        ),
        agg AS (
        SELECT
            bin_idx,
            list_idx,
            AVG(bin_start) AS bin_start,
            AVG(bin_end) AS bin_end,
            AVG(list_val) AS list_avg,
            {avg_other_cols}
        FROM exploded
        GROUP BY bin_idx, list_idx
        ),
        rebuilt AS (
        SELECT
            bin_idx,
            AVG(bin_start) AS bin_start,
            AVG(bin_end) AS bin_end,
            list(list_avg ORDER BY list_idx) AS list_avg,
            {avg_other_cols}
        FROM agg
        GROUP BY bin_idx
        )
        SELECT
        bin_idx,
        bin_start,
        bin_end,
        list_avg AS {list_col_output_name},
        {", ".join(add_cols.values())}
        FROM rebuilt
        ORDER BY bin_idx;
    """

    return query_sql


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
    # Load tables and attributes for mRNAs
    mRNA_ids = field_metadata(
        conn, config_sql, "listeners__rna_counts__mRNA_cistron_counts"
    )

    with open_arbitrary_sim_data(sim_data_paths) as f:
        sim_data: "SimulationDataEcoli" = pickle.load(f)

    # Load tables and attributes for tRNAs and rRNAs
    bulk_ids = field_metadata(conn, config_sql, "bulk")
    bulk_id_to_idx = {bulk_id: i + 1 for i, bulk_id in enumerate(bulk_ids)}
    uncharged_tRNA_ids = sim_data.process.transcription.uncharged_trna_names
    uncharged_tRNA_idx = [bulk_id_to_idx[trna] for trna in uncharged_tRNA_ids]
    charged_tRNA_ids = sim_data.process.transcription.charged_trna_names
    charged_tRNA_idx = [bulk_id_to_idx[trna] for trna in charged_tRNA_ids]
    tRNA_cistron_ids = [tRNA_id[:-3] for tRNA_id in uncharged_tRNA_ids]
    rRNA_ids = [
        sim_data.molecule_groups.s30_16s_rRNA[0],
        sim_data.molecule_groups.s50_23s_rRNA[0],
        sim_data.molecule_groups.s50_5s_rRNA[0],
    ]
    rRNA_idx = [bulk_id_to_idx[trna] for trna in rRNA_ids]
    rRNA_cistron_ids = [rRNA_id[:-3] for rRNA_id in rRNA_ids]
    ribosomal_subunit_ids = [
        sim_data.molecule_ids.s30_full_complex,
        sim_data.molecule_ids.s50_full_complex,
    ]
    ribo_subunit_idx = [bulk_id_to_idx[ribo] for ribo in ribosomal_subunit_ids]
    rna_subquery = cast(
        str,
        read_stacked_columns(
            history_sql,
            [
                # Extract only necessary bulk counts to reduce RAM usage
                f"list_select(bulk, {charged_tRNA_idx}) AS charged_tRNAs, "
                f"list_select(bulk, {uncharged_tRNA_idx}) AS uncharged_tRNAs, "
                f"list_select(bulk, {rRNA_idx}) AS rRNAs, "
                f"list_select(bulk, {ribo_subunit_idx}) AS ribo_subunits",
                "listeners__unique_molecule_counts__active_ribosome",
                "listeners__rna_counts__mRNA_cistron_counts",
            ],
            remove_first=False,
            order_results=False,
        ),
    )
    rna_query = f"""SELECT
        -- Create RNA counts list of mRNAs, tRNAs, and rRNAs (in order)
        (
            -- mRNA
            listeners__rna_counts__mRNA_cistron_counts +
            -- tRNA = charged + uncharged
            [
                trna[1] + trna[2]
                FOR trna IN list_zip(charged_tRNAs, uncharged_tRNAs)
            ] +
            -- First rRNA = bulk + active ribosome + small subunit
            [
                rRNAs[1] +
                listeners__unique_molecule_counts__active_ribosome +
                ribo_subunits[1]
            ] +
            -- Remaining rRNAs = bulk + active ribosome + large subunit
            [
                rrna_count +
                listeners__unique_molecule_counts__active_ribosome +
                ribo_subunits[2]
                FOR rrna_count IN rRNAs[2:]
            ]
        ) AS rna_counts, time
        FROM ({rna_subquery})
    """
    # specify parquet columns
    list_col = {"rna_counts": "rna_counts"}
    query_sql = build_query(list_col, {}, rna_query, params.get("n_tp", 5))
    data = conn.sql(query_sql).pl()

    # Retrieve gene copy numbers in order of RNA counts
    cistron_id_to_gene_id: dict[str, str] = cast(
        dict[str, str],
        {
            cistron["id"]: cistron["gene_id"]
            for cistron in sim_data.process.transcription.cistron_data
        },
    )
    gene_ids = [
        cistron_id_to_gene_id[rna_id]
        for rna_id in mRNA_ids + tRNA_cistron_ids + rRNA_cistron_ids
    ]

    # Pivot aggregated counts into gene-by-time matrix via Polars operations
    time_unit = params.get("time_unit", "minutes")
    if time_unit not in ["seconds", "minutes"]:
        time_unit = "minutes"
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
        pl.col("rna_counts").list.to_struct(fields=gene_ids)
    ).unnest("rna_counts")

    wide_table = counts_df.transpose(
        include_header=True,
        header_name="$",
        column_names=timepoint_cols,
    )

    wide_table.write_csv(
        os.path.join(outdir, "ptools_rna.tsv"),
        separator="\t",
        include_header=True,
        float_precision=4,
    )