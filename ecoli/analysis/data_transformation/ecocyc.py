import json
import warnings
from pathlib import Path
from typing import Any

import polars as pl
from duckdb import DuckDBPyConnection
import numpy as np

from ecoli.library.parquet_emitter import read_stacked_columns
from ecoli.library.sim_data import LoadSimData
from ecoli.library.transform_utils import ANSIColors, SimulationConfigData, partition_log, downsample, ctext, get_cardinality


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
    requested_transformations = params.get("request", [])
    if requested_transformations:
        for request in requested_transformations:
            transformation_type = request["type"]
            observable_ids = request.get("observable_ids", [])
            data_transformer = genes_transform if transformation_type == "genes" \
                else bulk_transform if transformation_type == "bulk" \
                else reactions_transform if transformation_type == "reactions" \
                else None

            if data_transformer is None:
                raise ValueError('You must pass an analysis type as config parameter!')
            data_transformer(observable_ids, conn, history_sql, config_sql, success_sql, sim_data_paths, outdir)
    else:
        full_transformation([], conn, history_sql, config_sql, success_sql, sim_data_paths, outdir)


def genes_transform(
    observable_ids: list[str],
    conn: DuckDBPyConnection,
    history_sql: str,
    config_sql: str,
    success_sql: str,
    sim_data_paths: dict[str, dict[int, str]],
    outdir: str
) -> None:
    warnings.warn(
        ctext('You requested {} num observables!'.format(len(observable_ids)), color=ANSIColors.RED)
    )
    # extract exposed params
    config_df = SimulationConfigData(config_sql)
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
    sim_data_path = Path(sim_data_paths[experiment_id][variant])
    sim_data = LoadSimData(str(sim_data_path)).sim_data

    partition_log(experiment_id, variant, seed, generation, agent_id, __file__)

    # define a transformation callback to occur duing IO
    def transformer(*args) -> pl.DataFrame:
        outputs_loaded = args[0]
        cistron_data = sim_data.process.transcription.cistron_data
        mrna_cistron_ids = cistron_data["id"][cistron_data["is_mRNA"]].tolist()
        mrna_cistron_names = [sim_data.common_names.get_common_name(cistron_id) for cistron_id in mrna_cistron_ids]
        mrna_select = mrna_cistron_names
        mrna_mtx = np.stack(outputs_loaded["listeners__rna_counts__full_mRNA_cistron_counts"])
        mrna_idxs = [mrna_cistron_names.index(gene_id) for gene_id in mrna_select]
        mrna_trajs = [mrna_mtx[:, mrna_idx] for mrna_idx in mrna_idxs]
        mrna_plot_dict = {key: val for (key, val) in zip(mrna_select, mrna_trajs, strict=False)}
        mrna_plot_dict["time"] = outputs_loaded["time"]

        mrna_df_long = pl.LazyFrame(mrna_plot_dict).unpivot(
            index=["time"],
            on=None,
            variable_name="gene names",
            value_name="counts"
        )

        mrna_df: pl.LazyFrame = downsample(mrna_df_long)
        genes_data: pl.LazyFrame = mrna_df.filter(pl.col("gene names").is_in(observable_ids))

        # export reported metadata about transform, including cardinality
        x = outputs_loaded
        y = genes_data.collect()
        metadata = {'cardinality': get_cardinality(x, y), 'type': "ecocyc_genes", "schemas": {}}
        for df_name, dataframe in dict(zip(['X', 'Y'], [x, y])).items():
            schema = {
                colname: val_type.__name__ for colname, val_type in dataframe.collect_schema().to_python().items()
            }
            metadata['schemas'][df_name] = schema
        with open(Path(outdir) / "transformation_metadata.json", 'w') as fp:
            json.dump(metadata, fp, indent=4)

        return y

    # read the data while performing the transformation
    required_columns = ["listeners__rna_counts__full_mRNA_cistron_counts"]
    lf = pl.LazyFrame(
        read_stacked_columns(history_sql, required_columns, conn=conn, success_sql=success_sql, func=transformer)
    )

    # export to requested format
    filename = f"genes_{experiment_id}"
    export_format = "parquet"
    out_path = Path(outdir) / f"{filename}.{export_format}"
    data_exporter = getattr(lf, f'sink_{export_format}')
    data_exporter(out_path)


def bulk_transform(
    observable_ids: list[str],
    conn: DuckDBPyConnection,
    history_sql: str,
    config_sql: str,
    success_sql: str,
    sim_data_paths: dict[str, dict[int, str]],
    outdir: str
) -> None:
    warnings.warn(
        ctext('You requested {} num observables!'.format(len(observable_ids)), color=ANSIColors.RED)
    )


def reactions_transform(
    observable_ids: list[str],
    conn: DuckDBPyConnection,
    history_sql: str,
    config_sql: str,
    success_sql: str,
    sim_data_paths: dict[str, dict[int, str]],
    outdir: str
) -> None:
    warnings.warn(
        ctext('You requested {} num observables!'.format(len(observable_ids)), color=ANSIColors.RED)
    )


def full_transformation(
    observable_ids: list[str],
    conn: DuckDBPyConnection,
    history_sql: str,
    config_sql: str,
    success_sql: str,
    sim_data_paths: dict[str, dict[int, str]],
    outdir: str
) -> None:
    for _ in map(
            lambda f: f(observable_ids, conn, history_sql, config_sql, success_sql, sim_data_paths, outdir),
            [genes_transform, bulk_transform, reactions_transform]
    ):
        pass

