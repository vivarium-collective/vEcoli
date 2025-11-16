import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
import polars as pl
import numpy as np
import math
import itertools

from reconstruction.ecoli.simulation_data import SimulationDataEcoli

PARTITION_GROUPS = {
    "multiseed": ["experiment_id", "variant"],
    "multigeneration": ["experiment_id", "variant", "lineage_seed"],
    "multidaughter": ["experiment_id", "variant", "lineage_seed", "generation"],
    "single": [
        "experiment_id",
        "variant",
        "lineage_seed",
        "generation",
        "agent_id",
    ],
}


@dataclass
class SimulationConfigData:
    _df: pl.DataFrame

    def __init__(self, config_sql: str):
        self._df = duckdb.sql(config_sql).pl()

    def __getattr__(self, attr):
        if attr != "get":
            return getattr(self._df, attr)
        return getattr(self, attr)

    def get(self, attr: str) -> Any:
        value = self._df[[attr]].to_numpy().flatten()
        return value[0]


class ANSIColors(StrEnum):
    BLACK = "\033[30m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    PURPLE = "\033[0;35m"
    CYAN = "\033[36m"
    WHITE = "\033[37m"
    RESET = "\033[0m"


class MoleculeIdType(StrEnum):
    COMMON = "common name"
    BULK = "bulk id"


def ctext(message: str, color: ANSIColors) -> str:
    return f"\033[1m{color}{message}{ANSIColors.RESET}"


def log_text(message: str, module: str) -> str:
    h = "=" * len(message)
    mid = len(h) // 2
    former = h[:mid]
    latter = h[mid:]
    header = ctext(f"{former} {module} {latter}", ANSIColors.YELLOW)
    msg = ctext(message, ANSIColors.RED)
    return f"\n{header}\n>> {msg}"


def partition_log(experiment_id: str, variant: int, seed: int, generation: int, agent_id: str, module: str) -> None:
    message = f"Experiment_id: {experiment_id}, Variant: {variant}, Seed: {seed}, Generation: {generation}, AgentID: {agent_id}"
    txt = log_text(message, module)
    print(txt)


def downsample(df_long: pl.LazyFrame) -> pl.LazyFrame:
    tp_all = (
        df_long
        .select(pl.col("time").unique().sort())
        .collect()
        .get_column("time")
        .to_numpy()
        .astype(int)
    )
    n_rows = df_long.select(pl.len().alias("n")).collect().item()
    ds_ratio = int(math.ceil(n_rows / 20_000))
    tp_ds = list(itertools.islice(tp_all, 0, tp_all.max(), ds_ratio))
    df_ds = df_long.filter(pl.col("time").is_in(tp_ds))
    return df_ds


def get_cardinality(x: pl.DataFrame, y: pl.DataFrame) -> tuple[float, float]:
    nx, ny = list(map(lambda df: len(df.rows()), [x, y]))
    dx, dy = list(map(lambda df: len(df.columns), [x, y]))
    return (
        (ny / nx), (dy / dx)
    )


def get_ids(sim_data: SimulationDataEcoli) -> tuple[..., ..., ..., ..., ..., ..., ..., ]:
    # === get bulk ids and unique bulk ===
    bulk_ids = sim_data.internal_state.bulk_molecules.bulk_data["id"].tolist()
    bulk_ids_biocyc = [bulk_id[:-3] for bulk_id in bulk_ids]
    bulk_names_unique = list(np.unique(bulk_ids_biocyc))

    # === get common names ===
    bulk_common_names = [sim_data.common_names.get_common_name(name) for name in bulk_names_unique]
    duplicates = []
    for item in bulk_common_names:
        if bulk_common_names.count(item) > 1 and item not in duplicates:
            duplicates.append(item)
    for dup in duplicates:
        sp_idxs = [index for index, item in enumerate(bulk_common_names) if item == dup]
        for sp_idx in sp_idxs:
            bulk_rename = str(bulk_common_names[sp_idx]) + f"[{bulk_names_unique[sp_idx]}]"
            bulk_common_names[sp_idx] = bulk_rename

    # === rxns and genes data (TODO: remove?) ===
    cistron_data = sim_data.process.transcription.cistron_data
    mrna_cistron_ids = cistron_data["id"][cistron_data["is_mRNA"]].tolist()
    mrna_cistron_names = [sim_data.common_names.get_common_name(cistron_id) for cistron_id in mrna_cistron_ids]
    rxn_ids = sim_data.process.metabolism.base_reaction_ids
    return (
        # bulk_ids,
        bulk_ids_biocyc,
        bulk_names_unique,
        bulk_common_names,
        rxn_ids,
        cistron_data,
        mrna_cistron_ids,
        mrna_cistron_names,
    )


def downsample_pd(df_long: pd.DataFrame) -> pd.DataFrame:
    tp_all = np.unique(df_long["time"]).astype(int)
    ds_ratio = int(np.ceil(np.shape(df_long)[0] / 20000))
    tp_ds = list(itertools.islice(tp_all, 0, max(tp_all), ds_ratio))
    df_ds = df_long[np.isin(df_long["time"], tp_ds)]
    return df_ds


def export_metadata(partition_dict: dict[str, int | str], x: pl.DataFrame | pd.DataFrame, y: pl.DataFrame | pd.DataFrame, outdir: str) -> pl.LazyFrame:
    x, y = list(map(
        lambda df: pl.from_pandas(df) if isinstance(df, pd.DataFrame) else df,
        [x, y]
    ))
    metadata = {
        'cardinality': get_cardinality(x, y),
        'type': f"ecocyc_bulk",
        "schemas": {},
        "partitioning": partition_dict
    }
    for df_name, dataframe in dict(zip(['X', 'Y'], [x, y])).items():
        schema = {
            colname: val_type.__name__ for colname, val_type in dataframe.collect_schema().to_python().items()
        }
        metadata['schemas'][df_name] = schema
    with open(Path(outdir) / "transformation_metadata.json", 'w') as fp:
        json.dump(metadata, fp, indent=4)
    return y.lazy()


def cache_transformed(y: pd.DataFrame | pl.DataFrame) -> None:
    # import redis
    # r = redis.Redis(host='localhost', port=6379, db=0)
    raise NotImplementedError("This feature is coming soon.")


def get_ecocyc_transforms(expid: str, outdir_root: Path, **partitioning_params) -> pl.DataFrame:
    outdir = Path(outdir_root) / f"{expid}_ecocyc_transform" / f"experiment_id={expid}"
    return pl.scan_parquet(f"{outdir!s}/**/*.parquet").collect()


def test_get_ecocyc_transforms():
    expid = "sms_multiseed_multigen"
    outdir_root = Path("out/transforms")
    df = get_ecocyc_transforms(expid, outdir_root)
    print(df.head())
