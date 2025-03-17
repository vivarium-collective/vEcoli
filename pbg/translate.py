from typing import Any

import numpy as np
from pint import Quantity

from pbg.parse import find_defaults


SCHEMA_MAPPER = {
    "integer": int,
    "float": float,
    "string": str,
    "boolean": bool,
    "list": list,
    "tuple": tuple,
}

MAPPER = {
    "int": "integer",
    "bool": "boolean",
    "list": "list",
    "tuple": "tuple",
    "float": "float",
    "any": "any",
    "ndarray": "array",
}


def translate_vivarium_types(defaults: dict) -> dict:
    """Translate default values into corresponding bigraph-schema type declarations."""
    result = {}
    for key, value in defaults.items():
        if isinstance(value, dict):
            result[key] = translate_vivarium_types(value)
        else:
            type_name = type(value).__name__
            if type_name == "NoneType":
                type_name = "any"
            result[key] = MAPPER[type_name]

    return result


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
            for schema_type, python_type in SCHEMA_MAPPER.items():
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
