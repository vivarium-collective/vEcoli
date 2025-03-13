from types import FunctionType
from typing import Any, Callable
from dataclasses import dataclass, asdict
import ast
import inspect

from vivarium.core.process import Process as VivariumProcess

from pbg.data_model.ports import ProcessBigraphPorts
from pbg.parse import PortsSchemaAnalyzer, find_defaults, get_process, OutputDictAnalyzer, extract_output_dict

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
            result[key] = type(value).__name__

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


def extract_ports_schema_return(func: Callable[[VivariumProcess], dict]) -> dict:
    return extract_output_dict(func)


def extract_ports_from_update(func: Callable[[VivariumProcess, float, dict], dict]) -> ProcessBigraphPorts:
    """
    Parses a function and extracts:
    - "inputs": variables that are being read
    - "outputs": dictionary keys returned by the function
    """
    source = inspect.getsource(func)
    tree = ast.parse(source)

    tree = ast.parse(source)
    analyzer = PortsSchemaAnalyzer()
    analyzer.visit(tree)

    return ProcessBigraphPorts(inputs=analyzer.inputs, outputs=analyzer.outputs)


def get_update_method(process: VivariumProcess):
    return getattr(process, "update").__func__


def get_ports_schema_method(process: VivariumProcess):
    return getattr(process, "ports_schema").__func__


def get_ports(
        parent_package_name: str,
        module_name: str,
        process_class_name: str,
        package_child_names: list[str] | None = None
):
    process: VivariumProcess = get_process(parent_package_name, module_name, process_class_name, package_child_names)
    updater: FunctionType = get_update_method(process)
    port_names: ProcessBigraphPorts = extract_ports_from_update(updater)

    inputs_schema, outputs_schema = {}, {}

    ports_schema_method: FunctionType = get_ports_schema_method(process)
    ports_schema: dict = extract_ports_schema_return(ports_schema_method)
    ports_mapping: dict = get_port_mapping(ports_schema)

    for name in port_names.inputs:
        inputs_schema[name] = ports_mapping[name]

    for name in port_names.outputs:
        outputs_schema[name] = ports_mapping[name]


def example_next_update(self, interval, states):
    x = states['x']
    return {
        'y': x**x
    }


def test_extraction():
    variables = extract_ports_from_update(example_next_update)
    print(variables)

