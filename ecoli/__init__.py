"""
ecoli: registration and configuration

This module should:

A. import all required type functions and register them via core
B. import all ecoli.migrated processes and register them
C. import all required type schemas/defs and register them (or read them via json)
"""


import os
import warnings
import faulthandler

# suppress \s errors TODO: fix this in offending modules
warnings.filterwarnings("ignore", category=SyntaxWarning)

# Improve performance and reproducibility
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["VECLIB_MAXIMUM_THREADS"] = "1"

# TODO: move/replace this?
faulthandler.enable()

import pickle
from typing import Any
import unum
import json
import logging
from dataclasses import dataclass

from process_bigraph import ProcessTypes
from process_bigraph.processes import TOY_PROCESSES
from vivarium.core.registry import (
    divider_registry,
    emitter_registry,
    serializer_registry,
)

from wholecell.utils import units
from ecoli.library.units import Quantity
from ecoli.library.parquet_emitter import ParquetEmitter
from ecoli.library.schema import (
    divide_binomial,
    divide_bulk,
    divide_by_domain,
    divide_ribosomes_by_RNA,
    divide_RNAs_by_domain,
    divide_set_none,
    empty_dict_divider,
)
from ecoli.library.serialize import (
    MethodSerializer,
    NumpyRandomStateSerializer,
    ParameterSerializer,
    UnumSerializer,
)
from ecoli.library.updaters import (
    inverse_update_accumulate,
    inverse_update_bulk_numpy,
    inverse_update_merge,
    inverse_update_nonnegative_accumulate,
    inverse_update_null,
    inverse_update_set,
    inverse_update_unique_numpy,
    inverse_updater_registry,
)
from ecoli.shared.registration import ecoli_core
from ecoli.shared.utils.log import setup_logging


logger: logging.Logger = setup_logging(__name__)


emitter_registry.register("parquet", ParquetEmitter)

# register :term:`updaters`
inverse_updater_registry.register("accumulate", inverse_update_accumulate)
inverse_updater_registry.register("set", inverse_update_set)
inverse_updater_registry.register("null", inverse_update_null)
inverse_updater_registry.register("merge", inverse_update_merge)
inverse_updater_registry.register(
    "nonnegative_accumulate", inverse_update_nonnegative_accumulate
)
inverse_updater_registry.register("bulk_numpy", inverse_update_bulk_numpy)
inverse_updater_registry.register("unique_numpy", inverse_update_unique_numpy)


# register :term:`dividers`
divider_registry.register("binomial_ecoli", divide_binomial)
divider_registry.register("bulk_binomial", divide_bulk)
divider_registry.register("by_domain", divide_by_domain)
divider_registry.register("rna_by_domain", divide_RNAs_by_domain)
divider_registry.register("empty_dict", empty_dict_divider)
divider_registry.register("ribosome_by_RNA", divide_ribosomes_by_RNA)
divider_registry.register("set_none", divide_set_none)

# register serializers
for serializer_cls in (
    UnumSerializer,
    ParameterSerializer,
    NumpyRandomStateSerializer,
    MethodSerializer,
):
    serializer = serializer_cls()
    serializer_registry.register(serializer.name, serializer)


VERBOSE_REGISTER = eval(os.getenv("VERBOSE_REGISTER", "True"))


# register types
types_dir: str = os.path.join(os.path.dirname(__file__), "types")
# register_types(ecoli_core, types_dir, bool(VERBOSE_REGISTER))

# register processes
# TODO: register processes here (explicitly or implicitly via interface)
ecoli_core.register_processes(TOY_PROCESSES)
