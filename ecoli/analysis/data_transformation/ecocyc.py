import json
import warnings
from pathlib import Path
from typing import Any, Callable

import pandas as pd
import polars as pl
from duckdb import DuckDBPyConnection
import numpy as np

from ecoli.library.parquet_emitter import read_stacked_columns
from ecoli.library.sim_data import LoadSimData
from ecoli.library.transform_utils import ANSIColors, SimulationConfigData, partition_log, downsample, ctext, \
    get_cardinality, get_ids, MoleculeIdType, downsample_pd, cache_transformed, export_metadata
from reconstruction.ecoli.simulation_data import SimulationDataEcoli


PartitionDictType = dict[str, int | str]


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
        # iterate over the outermost "request" param attribute
        for request in requested_transformations:
            # extract requested config/parameters
            transformation_type = request["type"]
            observable_ids = request.get("observable_ids", [])
            sim_data, experiment_id, partition_dict = _initialize_data(
                config_sql=config_sql,
                sim_data_paths=sim_data_paths,
                observable_ids=observable_ids
            )
            # dynamically get and run transformer type
            data_transformer: Callable = _genes_transform if transformation_type == "genes" \
                else _bulk_transform if transformation_type == "bulk" \
                else _reactions_transform if transformation_type == "reactions" \
                else None
            if data_transformer is None:
                raise ValueError('You must pass an analysis type as config parameter!')
            data_transformer(
                sim_data=sim_data,
                experiment_id=experiment_id,
                partition_dict=partition_dict,
                observable_ids=observable_ids,
                conn=conn,
                history_sql=history_sql,
                success_sql=success_sql,
                outdir=outdir
            )
    else:
        # if no specific request, then perform all of the ecocyc transformations
        sim_data, experiment_id, partition_dict = _initialize_data(config_sql=config_sql, sim_data_paths=sim_data_paths)
        _transform_all(
            sim_data=sim_data,
            experiment_id=experiment_id,
            partition_dict=partition_dict,
            conn=conn,
            history_sql=history_sql,
            success_sql=success_sql,
            outdir=outdir
        )


# ============= Genes Transformation ============= #

def _genes_transform(
    sim_data: SimulationDataEcoli,
    experiment_id: str,
    partition_dict: dict[str, int | str],
    observable_ids: list[str] | None,
    conn: DuckDBPyConnection,
    history_sql: str,
    success_sql: str,
    outdir: str,
    params: dict | None = None
) -> None:
    # define a transformation callback to occur duing IO
    def callback(*outputs) -> pl.DataFrame:
        outputs_loaded = outputs[0]
        cistron_data = sim_data.process.transcription.cistron_data
        mrna_cistron_ids = cistron_data["id"][cistron_data["is_mRNA"]].tolist()
        mrna_cistron_names = [sim_data.common_names.get_common_name(cistron_id) for cistron_id in mrna_cistron_ids]
        mrna_select = mrna_cistron_names
        mrna_mtx = np.stack(outputs_loaded["listeners__rna_counts__full_mRNA_cistron_counts"])
        mrna_idxs = [mrna_cistron_names.index(gene_id) for gene_id in mrna_select]
        mrna_trajs = [mrna_mtx[:, mrna_idx] for mrna_idx in mrna_idxs]
        mrna_plot_dict = {key: val for (key, val) in zip(mrna_select, mrna_trajs, strict=False)}
        mrna_plot_dict["time"] = outputs_loaded["time"]
        # construct mapping df
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
        export_metadata(partition_dict, x, y, outdir)
        return y

    # read the data while performing the transformation
    required_columns = ["listeners__rna_counts__full_mRNA_cistron_counts"]
    lf = pl.LazyFrame(
        read_stacked_columns(history_sql, required_columns, conn=conn, success_sql=success_sql, func=callback)
    )
    # export to requested format
    filename = f"genes_{experiment_id}"
    export_format = "parquet"
    out_path = Path(outdir) / f"{filename}.{export_format}"
    data_exporter = getattr(lf, f'sink_{export_format}')
    data_exporter(out_path)


# ============= Bulk Transformation ============= #

def _bulk_transform(
    sim_data: SimulationDataEcoli,
    experiment_id: str,
    partition_dict: dict,
    observable_ids: list[str] | None,
    conn: DuckDBPyConnection,
    history_sql: str,
    success_sql: str,
    outdir: str,
    params: dict
) -> None:
    """
    params:
        - common_labels: bool = False (if true, use academic names otherwise use EcoCycIDs)
        - cache: bool = False (if true, cache computed data)
    """
    (
        # bulk_ids,
        bulk_ids_biocyc,
        bulk_names_unique,
        bulk_common_names,
        rxn_ids,
        cistron_data,
        mrna_cistron_ids,
        mrna_cistron_names,
    ) = get_ids(sim_data)
    molecule_id_type = MoleculeIdType.BULK if not params.get('common_labels', False) else MoleculeIdType.COMMON

    def callback(*outputs):
        outputs_loaded = outputs[0]
        bulk_mtx = np.stack(outputs_loaded["bulk"].values)

        # get bulk sp traj
        sp_trajs = []
        for bulk_id in bulk_names_unique:
            if molecule_id_type == "common name":
                sp_name = bulk_names_unique[bulk_common_names.index(bulk_id)]
            elif molecule_id_type.value == "bulk id":
                sp_name = bulk_id
            sp_idxs = [index for index, item in enumerate(bulk_ids_biocyc) if item == sp_name]
            bulk_sp_traj = np.sum(bulk_mtx[:, sp_idxs], 1)
            traj = bulk_sp_traj
            sp_trajs.append(traj)

        plot_dict = dict(zip(bulk_names_unique, sp_trajs))
        plot_dict["time"] = outputs_loaded["time"]
        df_long = pd.DataFrame(plot_dict).melt(
            id_vars=["time"],
            var_name="bulk_molecules",
            value_name="counts",
        )
        # plot_df = pd.DataFrame(plot_dict)
        # df_long = plot_df.melt(
        #     id_vars=["time"],
        #     var_name="bulk_molecules",
        #     value_name="counts",
        # )
        # df_long = plot_df.unpivot(
        #     index=["time"],  # Columns to keep as identifier variables
        #     variable_name="bulk_molecules",  # Name for the new column containing original column headers
        #     value_name="counts",  # Name for the new column containing original column values
        # )
        df: pd.DataFrame = downsample_pd(df_long)
        y = pl.from_pandas(df[df["bulk_molecules"].isin(observable_ids)] if observable_ids is not None else df)
        if params.get('cache', False):
            cache_transformed(y)

        # export metadata
        x = pl.from_pandas(outputs_loaded)
        export_metadata(partition_dict, x, y, outdir)
        return y

    # read the data while performing the transformation
    required_columns = ["bulk"]
    lf = pl.LazyFrame(
        read_stacked_columns(history_sql, required_columns, conn=conn, success_sql=success_sql, func=callback)
    )

    # export to requested format
    filename = f"bulk_{experiment_id}"
    export_format = "parquet"
    out_path = Path(outdir) / f"{filename}.{export_format}"
    data_exporter = getattr(lf, f'sink_{export_format}')
    data_exporter(out_path)


# ============= Reactions Transformation ============= #

def _reactions_transform(
    sim_data: SimulationDataEcoli,
    experiment_id: str,
    # partition_dict: PartitionDictType,
    observable_ids: list[str] | None,
    conn: DuckDBPyConnection,
    history_sql: str,
    success_sql: str,
    outdir: str,
    params: dict | None = None
) -> None:
    def callback(*outputs) -> pl.DataFrame:
        pass


# ============= All Transformations ============= #

def _transform_all(
    sim_data: SimulationDataEcoli,
    experiment_id: str,
    partition_dict: PartitionDictType,
    conn: DuckDBPyConnection,
    history_sql: str,
    success_sql: str,
    outdir: str,
    params: dict | None = None
) -> None:
    for _ in map(
        lambda f: f(
            sim_data=sim_data,
            experiment_id=experiment_id,
            partition_dict=partition_dict,
            observable_ids=None,
            conn=conn,
            history_sql=history_sql,
            success_sql=success_sql,
            outdir=outdir,
            params=params
        ),
        [_genes_transform, _bulk_transform, _reactions_transform]
    ):
        pass


def _initialize_data(
    config_sql: str,
    sim_data_paths: dict[str, dict[int, str]],
    observable_ids: list[str] | None = None
) -> tuple[SimulationDataEcoli, str, PartitionDictType]:
    if observable_ids is not None:
        warnings.warn(
            ctext('You requested {} num observables!'.format(len(observable_ids)), color=ANSIColors.RED)
        )

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

    partitions_selected = {
        "experiment_id": experiment_id,
        "variant": int(variant),
        "lineage_seed": int(seed),
        "generation": int(generation),
        "agent_id": agent_id,
    }
    return sim_data, experiment_id, partitions_selected
