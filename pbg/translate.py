import textwrap
from types import FunctionType
from typing import Any, Callable
from dataclasses import dataclass, asdict
import ast
import inspect

from vivarium.core.process import Process as VivariumProcess

from pbg.data_model.ports import ProcessBigraphPorts
from pbg.parse import PortsSchemaAnalyzer, find_defaults, get_process


SCHEMA_MAPPER = {
    "integer": int,
    "float": float,
    "string": str,
    "boolean": bool,
    "list": list,
    "tuple": tuple,
}


def translate_vivarium_types(defaults: dict) -> dict:
    """Translate default values into corresponding bigraph-schema type declarations."""
    result = {}
    for key, value in defaults.items():
        if isinstance(value, dict):
            result[key] = translate_vivarium_types(value)
        else:
            type_name = type(value).__name__
            if type_name == 'NoneType':
                type_name = 'any'
            result[key] = type_name

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
            _type = ""
            for schema_type, python_type in SCHEMA_MAPPER.items():
                if isinstance(v, python_type):
                    _type = schema_type
                else:
                    _type = "any"

            config_schema[k] = {
                "_type": _type,  # TODO: provide a more specific lookup
                "_default": v,
            }
        else:
            config_schema[k] = get_config_schema(v)

    return config_schema

