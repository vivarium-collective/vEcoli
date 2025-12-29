from pathlib import Path
from enum import StrEnum, Enum
import os
from typing import Any

from duckdb import DuckDBPyConnection
import numpy as np
import pandas as pd

from ecoli.library.sim_data import LoadSimData
from ecoli.library.parquet_emitter import dataset_sql, create_duckdb_conn


DEFAULT_OUTPUT_COLUMNS = ["bulk", "listeners__rna_counts__full_mRNA_counts"]


class OutputColumns(Enum):
    PROTEINS = [
        "bulk",
        "listeners__unique_molecule_counts__oriC",
        "listeners__unique_molecule_counts__active_RNAP",
        "listeners__unique_molecule_counts__active_ribosome",
    ]
    RNA = [
        "bulk",
        "listeners__rna_counts__full_mRNA_counts",
        "listeners__unique_molecule_counts__active_ribosome",
    ]
    REACTIONS = ["bulk", "listeners__fba_results__base_reaction_fluxes"]


def build_query(
        columns, history_sql
):  # generates sql query for user specified parquet columns
    query_sql = f"""
        SELECT {",".join(columns)}, time FROM ({history_sql})
        ORDER BY time
    """

    return query_sql


def read_outputs(
    history_sql: str,
    conn: DuckDBPyConnection,
    columns: list[str] | None = None,
    n_threads: int = 4,  # n_cpus available in slurm job
    mem_limit: str = "22GB"
) -> pd.DataFrame:
    # configure limits
    conn.execute(f"SET threads={n_threads}")
    conn.execute("SET preserve_insertion_order=false")
    conn.execute(f"SET memory_limit='{mem_limit}'")

    # retrieves specifc columns from parquet outputs and returns a dataframe
    query_sql = build_query(columns or DEFAULT_OUTPUT_COLUMNS, history_sql)

    outputs_df = conn.sql(query_sql).df()

    outputs_df = outputs_df.groupby("time", as_index=False).sum()

    return outputs_df


def test_read_outputs() -> None:
    expid = "expression_unfit"
    histsql, confsql, succsql = dataset_sql(experiment_ids=[expid], out_dir=str(Path(__file__).parent.parent.parent / "out"))
    conn = create_duckdb_conn()
    df = read_outputs(histsql, conn, OutputColumns.REACTIONS.value)
    print()