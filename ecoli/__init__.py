import os
import pickle
import sys
from typing import Any
import warnings

import process_bigraph
import unum

# suppress \s errors TODO: fix this in offending modules
warnings.filterwarnings("ignore", category=SyntaxWarning)

# Improve performance and reproducibility
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["VECLIB_MAXIMUM_THREADS"] = "1"

import json
import logging
from dataclasses import dataclass

from process_bigraph import ProcessTypes
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
import faulthandler

faulthandler.enable()


def setup_logging(name: str) -> logging.Logger:
    # Create a root logger
    root_logger = logging.getLogger(name)
    root_logger.setLevel(logging.INFO)

    console_handler = logging.StreamHandler(stream=sys.stdout)
    console_handler.setLevel(logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    return root_logger


logger: logging.Logger = setup_logging("ecoli.init")


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


# required bgs keys: {'_default', '_apply', '_check', '_serialize', '_deserialize', '_fold'}
# optional bgs keys: {'_type', '_value', '_description', '_type_parameters', '_inherit', '_divide'}
@dataclass
class TypeSchema:
    type_id: str
    protocol: str = "local"

    @property
    def attributes(self) -> set:
        return self.required_keys.union(self.optional_keys)

    @property
    def required_keys(self) -> set:
        return {"_default", "_apply", "_check", "_serialize", "_deserialize", "_fold"}

    @property
    def optional_keys(self) -> set:
        return {
            "_type",
            "_value",
            "_description",
            "_type_parameters",
            "_inherit",
            "_divide",
        }


def get_type_filepaths(dirpath: str) -> set[str]:
    paths: set = set()
    for filename in os.listdir(dirpath):
        if filename.endswith(".json"):
            paths.add(os.path.join(dirpath, filename))
    return paths


def register_types(core: ProcessTypes, types_dir: str, verbose: bool) -> None:
    function_keys = ["_serialize", "_deserialize", "_fold", "_check", "_apply"]
    types_to_register: set[str] = get_type_filepaths(types_dir)
    for spec_path in types_to_register:
        try:
            with open(spec_path, "r") as f:
                spec: dict = json.load(f)
            for type_id, type_spec in spec.items():
                if isinstance(type_spec, dict):
                    for key, spec_definition in type_spec.items():
                        if key in function_keys:
                            spec[type_id][key] = eval(spec_definition)
                core.register_types({type_id: type_spec})
                if verbose:
                    logger.info(f"Type ID: {type_id} has been registered.\n")
        except:
            if verbose:
                logger.error(f"{spec_path} cannot be registered.\n")
            continue


# -- bytes -- #

def check_bytes(schema, state, core=None):
    return isinstance(state, bytes)


def apply_bytes(schema, current, update: bytes, core=None):
    return current + update


def deserialize_bytes(schema, encoded, core=None):
    return pickle.loads(encoded)


# -- quantity -- #

def deserialize_quantity(value, core=None):
    return str(Quantity(value))


def serialize_quantity(value, core=None):
    return pickle.dumps(value.m)


def apply_quantity(schema, current: Quantity, update: Quantity, core=None):
    return current + update


# -- unum -- #

def check_unum(schema, state, core=None):
    return isinstance(state, unum.Unum)


def serialize_unum(schema, value: unum.Unum, core=None):
    return str(value)


def deserialize_unum(schema, state, core=None):
    return unum.Unum(state)


VERBOSE_REGISTER = os.getenv("VERBOSE_REGISTER", False)


@dataclass.dataclass
class Get:
    core: process_bigraph.ProcessTypes | Any

    @property
    def processes(self):
        return list(self.core.process_registry.registry.keys())
    
    @property
    def types(self):
        return(self.core.process_registry.registry.keys())
    

class Core(process_bigraph.ProcessTypes):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
    
    @property
    def get(self):
        return Get(core=self)
    

# project core singleton
ecoli_core = Core()

# register types
types_dir: str = os.path.join(os.path.dirname(__file__), "types")
register_types(ecoli_core, types_dir, bool(VERBOSE_REGISTER))

# register processes
# TODO: register processes here (explicitly or implicitly via interface)
