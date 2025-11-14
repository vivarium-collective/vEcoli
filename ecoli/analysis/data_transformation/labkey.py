"""
Labkey Data Format Relabeling/Aggregation Transformation (Eco/BioCyc)
"""

from pathlib import Path
from typing import Any

import polars as pl
from duckdb import DuckDBPyConnection

from ecoli.library.transform.data_transformer_labkey import DataTransformerLabkey
from ecoli.library.transform.models import (
    DataTransformExportFormat,
    SimulationConfigData,
)
from ecoli.library.transform.utils import partition_log


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
) -> None:
    # parameterize
    config_df = SimulationConfigData(query=config_sql)
    experiment_id, variant, seed, generation, agent_id, sim_outdir = list(
        map(
            lambda column: config_df.get(column),
            [
                "experiment_id",
                "variant",
                "lineage_seed",
                "generation",
                "agent_id",
                "emitter_arg__out_dir",
            ],
        )
    )
    partition_log(experiment_id, variant, seed, generation, agent_id, __file__)

    # transform
    simdata_path = Path(sim_data_paths[experiment_id][variant])
    transformer = DataTransformerLabkey(sim_data_path=simdata_path)
    formatted_df: pl.LazyFrame = transformer.transform(
        experiment_id=experiment_id,
        simulation_outdir=sim_outdir,
        observable_ids=params["observable_ids"],
        variant=variant,
        seed=seed,
        generation=generation,
        agent_id=agent_id,
        history_sql=history_sql,
        lazy=True,
        conn=conn
    )

    # export
    filename = f"labkey_{experiment_id}"
    transformer.export(
        df=formatted_df,
        outdir=outdir,
        filename=filename,
        variant=variant,
        seed=seed,
        generation=generation,
        agent_id=agent_id,
        io_format=DataTransformExportFormat.PARQUET
    )

