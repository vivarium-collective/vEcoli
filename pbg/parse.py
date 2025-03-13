from types import FunctionType
from typing import Any, Callable
from dataclasses import dataclass, asdict
import ast
import inspect

from vivarium.core.process import Process as VivariumProcess


SCHEMA_MAPPER = {
    "integer": int,
    "float": float,
    "string": str,
    "boolean": bool,
    "list": list,
    "tuple": tuple,
}


class PortsSchemaAnalyzer(ast.NodeVisitor):
    def __init__(self):
        self.inputs = set()
        self.outputs = set()

    def visit_Subscript(self, node):
        if isinstance(node.value, ast.Name) and node.value.id == "states":
            if isinstance(node.slice, ast.Constant):
                self.inputs.add(node.slice.value)
            elif isinstance(node.slice, ast.Str):
                self.inputs.add(node.slice.s)
        self.generic_visit(node)

    def visit_Dict(self, node):
        for key in node.keys:
            if isinstance(key, ast.Constant):
                self.outputs.add(key.value)
            elif isinstance(key, ast.Str):
                self.outputs.add(key.s)
        self.generic_visit(node)


@dataclass
class Ports:
    inputs: set
    outputs: set

    def serialize(self):
        return asdict(self)


def find_defaults(params: dict) -> dict:
    """Extract inner dict _default values from an arbitrarily-nested `params` input."""
    result = {}
    for key, value in params.items():
        if isinstance(value, dict):
            nested_result = find_defaults(value)
            if "_default" in value and not nested_result:
                result[key] = value["_default"]
            elif nested_result:
                result[key] = nested_result

    return result


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


def extract_ports_from_update(func: Callable[[VivariumProcess, float, dict], dict]):
    """
    Parses a function and extracts:
    - "inputs": variables that are being read
    - "outputs": dictionary keys returned by the function
    """
    source = inspect.getsource(func)
    tree = ast.parse(source)

    # inputs = set()
    # outputs = set()

    tree = ast.parse(source)
    analyzer = PortsSchemaAnalyzer()
    analyzer.visit(tree)

    return Ports(inputs=analyzer.inputs, outputs=analyzer.outputs)


def example_next_update(self, interval, states):
    x = states['x']
    return {
        'y': x**x
    }


def test_extraction():
    variables = extract_ports_from_update(example_next_update)
    print(variables)
