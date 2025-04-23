"""
ecoli: registration and configuration

This module should:

A. import all required type functions and register them via core
B. import all ecoli.migrated processes and register them
C. import all required type schemas/defs and register them (or read them via json)
"""


import os
import warnings
import logging
import faulthandler

from ecoli.shared.utils.log import setup_logging

# logger
logger: logging.Logger = setup_logging(__name__)

# suppress \s errors TODO: fix this in offending modules
warnings.filterwarnings("ignore", category=SyntaxWarning)

# Improve performance and reproducibility
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["VECLIB_MAXIMUM_THREADS"] = "1"

# TODO: move/replace this?
faulthandler.enable()

from dataclasses import dataclass

from process_bigraph.processes import TOY_PROCESSES

from wholecell.utils import units
from ecoli.library.units import Quantity
from ecoli.emitters.parquet import ParquetEmitter
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


VERBOSE_REGISTER = eval(os.getenv("VERBOSE_REGISTER", "True"))
PROCESS_PACKAGES = ["migrated"]  # TODO: add more here
TYPE_MODULES = ["unum"]  # TODO: add more here


# import and register types
for modname in TYPE_MODULES:
    ecoli_core.register_type(modname)

# import and register processes
for pkg in PROCESS_PACKAGES:
    ecoli_core.register_process_package(pkg)

# register toy processes
for name, process in TOY_PROCESSES.items():
    ecoli_core.process_registry.register(name.lower(), process)


# NOTE: emitters are delimited with -
ecoli_core.process_registry.register("parquet-emitter", ParquetEmitter)


# register :term:`updaters`
# inverse_updater_registry.register("accumulate", inverse_update_accumulate)
# inverse_updater_registry.register("set", inverse_update_set)
# inverse_updater_registry.register("null", inverse_update_null)
# inverse_updater_registry.register("merge", inverse_update_merge)
ecoli_core.apply_registry.register(
    "nonnegative_accumulate", inverse_update_nonnegative_accumulate
)
ecoli_core.apply_registry.register("bulk_numpy", inverse_update_bulk_numpy)
ecoli_core.apply_registry.register("unique_numpy", inverse_update_unique_numpy)


# register :term:`dividers`
ecoli_core.divide_registry.register("binomial_ecoli", divide_binomial)
ecoli_core.divide_registry.register("bulk_binomial", divide_bulk)
ecoli_core.divide_registry.register("by_domain", divide_by_domain)
ecoli_core.divide_registry.register("rna_by_domain", divide_RNAs_by_domain)
ecoli_core.divide_registry.register("empty_dict", empty_dict_divider)
ecoli_core.divide_registry.register("ribosome_by_RNA", divide_ribosomes_by_RNA)
ecoli_core.divide_registry.register("set_none", divide_set_none)


# register serializers
for serializer_cls in (
    UnumSerializer,
    ParameterSerializer,
    NumpyRandomStateSerializer,
    MethodSerializer,
):
    serializer = serializer_cls()
    ecoli_core.serialize_registry.register(serializer.name, serializer)
