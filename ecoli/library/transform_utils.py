import json
import warnings
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

import duckdb
import polars as pl
from duckdb import DuckDBPyConnection
import numpy as np
import math
import itertools

from ecoli.library.parquet_emitter import read_stacked_columns
from ecoli.library.sim_data import LoadSimData


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