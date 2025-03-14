import ast
import importlib
from types import ModuleType

import pint
from vivarium.core.process import Process as VivariumProcess


class PortsSchemaAnalyzer(ast.NodeVisitor):
    def __init__(self):
        self.inputs = set()
        self.outputs = set()
        self.update_variable_names = set()

    def visit_Subscript(self, node):
        key_chain = self.get_full_key_chain(node)
        if key_chain and key_chain.startswith("states"):
            key_name = key_chain.replace("states.", "")
            self.inputs.add(key_name)
        self.generic_visit(node)

    def visit_Assign(self, node):
        """Capture assignments to the `update` dictionary, preserving hierarchy."""
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "update":
                self.update_variable_names.add(target.id)

            elif isinstance(target, ast.Subscript):
                if (
                    isinstance(target.value, ast.Name)
                    and target.value.id in self.update_variable_names
                ):
                    key_chain = self.get_full_key_chain(target)
                    if key_chain:
                        self.outputs.add(key_chain)

                        if isinstance(node.value, ast.Dict):
                            self.extract_dict_keys(node.value, parent_key=key_chain)

        self.generic_visit(node)

    def visit_Return(self, node):
        """Ensure return statements containing dictionaries are analyzed."""
        if isinstance(node.value, ast.Name) and node.value.id in self.update_variable_names:
            self.outputs.update(self.update_variable_names)
        elif isinstance(node.value, ast.Dict):
            self.extract_dict_keys(node.value, parent_key="")
        self.generic_visit(node)

    def visit_Dict(self, node):
        """Capture dictionary keys as outputs, including nested structures."""
        self.extract_dict_keys(node, parent_key="")

    def extract_dict_keys(self, node, parent_key=""):
        """Recursively extract dictionary keys while preserving hierarchy."""
        for key, value in zip(node.keys, node.values):
            key_name = self.get_dict_key_name(key)
            if not key_name:
                continue  # Skip non-string keys

            full_key = f"{parent_key}.{key_name}" if parent_key else key_name
            self.outputs.add(full_key)

            # Recursively process nested dictionaries
            if isinstance(value, ast.Dict):
                self.extract_dict_keys(value, parent_key=full_key)

    def get_full_key_chain(self, node):
        """Extracts full key path from nested dictionary accesses like states['x']['y']."""
        keys = []
        while isinstance(node, ast.Subscript):
            key_name = self.get_dict_key_name(node.slice)
            if key_name is not None:
                keys.append(str(key_name))
            node = node.value

        if isinstance(node, ast.Name):
            keys.append(node.id)

        return ".".join(reversed(keys))

    def get_dict_key_name(self, key):
        """Extract dictionary key name, handling Python 3.14's `ast.Str` removal."""
        if isinstance(key, ast.Constant):
            return key.value
        return None


def find_defaults(params: dict) -> dict:
    """Extract inner dict _default values from an arbitrarily-nested `params` input."""
    result = {}
    for key, value in params.items():
        if isinstance(value, dict):
            nested_result = find_defaults(value)
            if "_default" in value and not nested_result:
                val = value["_default"]
                if isinstance(val, pint.Quantity):
                    val = val.to_tuple()[0]
                result[key] = val
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
