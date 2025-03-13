import ast
import inspect
from typing import Callable, Any
from types import ModuleType

from vivarium.core.process import Process as VivariumProcess


class OutputDictAnalyzer(ast.NodeVisitor):
    output_dict: dict

    def __init__(self):
        self.output_dict = {}

    def visit_Return(self, node):
        if isinstance(node.value, ast.Dict):
            self.output_dict = self._extract_dict(node.value)
        self.generic_visit(node)

    def _extract_dict(self, node: ast.Dict):
        output_dict = {}
        for key, value in zip(node.keys, node.values):
            if isinstance(key, ast.Constant):
                dict_key = key.value
            elif isinstance(key, ast.Str):
                dict_key = key.s
            else:
                continue

            dict_value = self._extract_value(value)
            output_dict[dict_key] = dict_value

        return output_dict

    def _extract_value(self, node: ast.AST) -> Any:
        if isinstance(node, ast.Constant):
            return node.value
        elif isinstance(node, ast.BinOp):
            return "expression"
        elif isinstance(node, ast.Call):
            return "function_call"
        elif isinstance(node, ast.Name):
            return node.id
        elif isinstance(node, ast.Dict):
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


def extract_output_dict(func: Callable[[VivariumProcess], dict]) -> dict:
    """Extracts the dictionary returned by the function."""
    source = inspect.getsource(func)
    tree = ast.parse(source)

    analyzer = OutputDictAnalyzer()
    analyzer.visit(tree)

    return analyzer.output_dict


def get_process(
        parent_package_name: str,
        module_name: str,
        process_class_name: str,
        package_child_names: list[str] | None = None,
) -> VivariumProcess:
    import_statement: str = parent_package_name
    if package_child_names:
        for child_pkg in package_child_names:
            import_statement += f".{child_pkg}"
    import_statement += f".{module_name}"

    module: ModuleType = __import__(
         import_statement, fromlist=[process_class_name])
    return getattr(module, process_class_name)
