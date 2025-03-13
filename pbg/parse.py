import ast
import importlib
import inspect
import textwrap
from typing import Callable, Any, LiteralString, Union, Optional, Type
from types import ModuleType

from vivarium.core.process import Process as VivariumProcess


class OutputDictAnalyzer(ast.NodeVisitor):
    """Parses an AST tree and extracts the return dictionary from a method."""

    def __init__(self):
        self.output_dict = {}

    def visit_Return(self, node):
        """Capture the full dictionary from a return statement."""
        if isinstance(node.value, ast.Dict):
            self.output_dict = self._extract_dict(node.value)
        self.generic_visit(node)

    def _extract_dict(self, node: ast.Dict):
        """Extract key-value pairs from an AST dictionary node."""
        output_dict = {}
        for key, value in zip(node.keys, node.values):
            if isinstance(key, ast.Constant):  # Python 3.8+
                dict_key = key.value
            elif isinstance(key, ast.Str):  # Python 3.7 support
                dict_key = key.s
            else:
                continue  # Skip non-constant keys

            # Extract value type
            dict_value = self._extract_value(value)
            output_dict[dict_key] = dict_value

        return output_dict

    def _extract_value(self, node: ast.AST) -> Any:
        """Extracts the value from AST nodes (e.g., literals, expressions)."""
        if isinstance(node, ast.Constant):  # Handles direct literals
            return node.value
        elif isinstance(node, ast.BinOp):  # Handles expressions like x**2
            return "expression"
        elif isinstance(node, ast.Call):  # Handles function calls
            return "function_call"
        elif isinstance(node, ast.Name):  # Handles variable names
            return node.id
        elif isinstance(node, ast.Dict):  # Handles nested dictionaries
            return self._extract_dict(node)
        return "unknown"


class PortsSchemaAnalyzer(ast.NodeVisitor):
    inputs: set
    outputs: set

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


def get_process(
        process_class_name: str,
        import_path: str | None = None,
        *path_components
) -> VivariumProcess:
    if not import_path:
        import_path = '.'.join(path_components)

    module: ModuleType = importlib.import_module(import_path)
    return getattr(module, process_class_name)()


def get_processes(package_path: str):
    # finish this
    module = importlib.import_module(package_path)
    mod_attrs = dir(module)
    processes = [
        get_process(process_class_name=name, import_path=module.__name__)
        for name in mod_attrs if name.startswith(name[0].upper())
    ]


def extract_ports_schema(
        process_class_name: str,
        import_path: str | None = None,
        *path_components) -> dict:
    """Extracts the dictionary returned by a class method without instantiating the class."""
    process: VivariumProcess = get_process(process_class_name, import_path, *path_components)
    return getattr(process.__init__(), 'ports_schema')()


def test_extract_output_dict():
    from ecoli.processes.antibiotics import death
    name = 'DeathFreezeState'
    path = death.__name__
    output_ports = extract_ports_schema(name, path)
    print(output_ports)
