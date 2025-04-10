import dataclasses
from typing import Dict, Any

import numpy as np
from pint import Quantity

from ecoli.library.schema import UniqueNumpyUpdater, get_bulk_counts, bulk_numpy_updater, get_unique_fields, UNIQUE_DIVIDERS, divide_bulk

CONFIG_SCHEMA_MAPPER = {
    "integer": int,
    "float": float,
    "string": str,
    "boolean": bool,
    "list": list,
    "tuple": tuple,
}

PORTS_MAPPER = {
    "int": "integer",
    "bool": "boolean",
    "list": "list",
    "tuple": "tuple",
    "float": "float",
    "any": "any",
    "ndarray": "array",
    "dict": "tree",
    "NoneType": "any",
    "int64": "integer",
    "float32": "float",
    "float64": "float",
    "int32": "integer",
    "int16": "integer",
    "uint16": "integer",
}


@dataclasses.dataclass
class SchemaType:
    id: str

    def __repr__(self):
        return self.id


def listener_schema(elements: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Helper function that can be used in ``inputs`` and ``outputs`` to create generic
    schema for a collection of listeners.

    Args:
        elements: Dictionary where keys are listener names and values are the
            defaults for each listener. Alternatively, if the value is a
            tuple, assume that the first element is the default and the second
            is metadata that will be emitted at the beginning of a simulation
            when ``emitter`` is set to ``database`` and ``emit_config`` is
            set to ``True`` (see :py:mod:``ecoli.experiments.ecoli_master_sim``).
            This metadata can then be retrieved later to aid in interpreting
            listener values (see :py:func:``vivarium.core.emitter.data_from_database``
            for sample code to query experiment configuration collection).
            As an example, this metadata might be an array of molecule names
            for a listener whose emits are arrays of counts, where the nth
            molecule name in the metadata corresponds to the nth value in the
            counts that are emitted.

    Returns:
        Ports schemas for all listeners in ``elements``.
    """
    # basic_schema = {"_updater": "set", "_emit": True}
    schema = {}
    for element, default in elements.items():
        # Assume that tuples contain (default, metadata) in that order
        if isinstance(default, tuple):
            schema[element] = {
                "_default": default[0],
                "_type": str(get_schema_type(default[0]))
                # **basic_schema,
                # "_properties": {"metadata": default[1]},
            }
        else:
            schema[element] = {"_default": default, "_type": str(get_schema_type(default))}  # **basic_schema, }
    return schema


def numpy_schema(name: str) -> Dict[str, Any]:
    """Helper function used in ports schemas for bulk and unique molecules

    Args:
        name: `bulk` for bulk molecules or one of the keys in :py:data:`UNIQUE_DIVIDERS`
            for unique molecules

    Returns:
        Fully configured ports schema for molecules of type `name`
    """
    schema = {"_default": [], "_type": "bulk"}
    if name == "bulk":
        schema["_apply"] = bulk_numpy_updater
        # Only pull out counts to be serialized (save space and time)
        schema["_serialize"] = get_bulk_counts
        schema["_divide"] = divide_bulk
    else:
        # schema["_updater"] = UniqueNumpyUpdater().updater
        # Convert to list of contiguous Numpy arrays for faster and more
        # efficient serialization (still do not recommend emitting unique)
        schema["_serialize"] = get_unique_fields
        schema["_divide"] = UNIQUE_DIVIDERS[name]
    return schema


def find_defaults(params: dict) -> dict:
    """Extract inner dict _default values from an arbitrarily-nested `params` input."""
    result = {}
    for key, value in params.items():
        if isinstance(value, dict):
            nested_result = find_defaults(value)
            if "_default" in value and not nested_result:
                val = value["_default"]
                if isinstance(val, Quantity):
                    val = val.to_tuple()[0]
                result[key] = val
            elif nested_result:
                result[key] = nested_result

    return result


def get_schema_type(value: Any) -> SchemaType:
    type_name = type(value).__name__
    if isinstance(value, np.ndarray):
        shape = str(value.shape)
        _type = PORTS_MAPPER[str(value.dtype)]
        return SchemaType(f"array[{_type}|{shape}]")
    else:
        return SchemaType(PORTS_MAPPER[type_name])


def translate_vivarium_types(defaults: dict) -> dict:
    """Translate default values into corresponding bigraph-schema type declarations."""
    ports = {}
    for key, value in defaults.items():
        if isinstance(value, dict):
            ports[key] = translate_vivarium_types(value)
        else:
            type_name = type(value).__name__
            # ports[key] = 'any'
            ports[key] = PORTS_MAPPER[type_name]

    return ports


def get_port_mapping(ports_schema: dict[str, Any]):
    """Translates vivarium.core.Process.defaults into bigraph-schema types to be consumed by pbg.Composite."""
    defaults = find_defaults(ports_schema)
    return translate_vivarium_types(defaults)


def get_config_schema(defaults: dict[str, float | Any]):
    """Translates vivarium.core.Process.defaults into bigraph-schema types to be consumed by pbg.Composite."""
    config_schema = {}
    for k, v in defaults.copy().items():
        if not isinstance(v, dict):
            # handle type
            _type = ""
            for schema_type, python_type in CONFIG_SCHEMA_MAPPER.items():
                # TODO: perhaps use str(Quantity().u()) to define the type (ie, nanometer)
                if isinstance(v, python_type):
                    _type = schema_type
                else:
                    _type = "any"

            # handle value
            if isinstance(v, Quantity):
                v = v.magnitude
            elif isinstance(v, np.ndarray) or isinstance(v, np.float64):
                v = v.tolist()

            config_schema[k] = {
                "_type": _type,  # TODO: provide a more specific lookup
                "_default": v
            }
        else:
            config_schema[k] = get_config_schema(v)

    return config_schema


