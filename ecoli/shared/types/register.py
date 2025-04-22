import importlib
import json
from logging import getLogger
import os
from types import ModuleType


TYPE_METHODS = ["apply", "check", "divide", "serialize", "deserialize", "slice", "fold"]


def import_function_module(module_name: str):
    root = f"ecoli.shared.types.functions.{module_name}"
    return importlib.import_module(root)


def verify_interface(module_name: str) -> ModuleType:
    """
    Verifies that module_name module at least contains the required functions.
    """
    from ecoli.shared.types.functions import interface
    module = import_function_module(module_name)
    module_funcs = ["type_name", "default"]
    for export in module.__all__:
        if export not in module_funcs:
            name_parts = export.split("_")
            func_type = name_parts[0]
            module_funcs.append(func_type)
    assert all([export in module_funcs for export in interface.__all__]), "Interface not fulfilled."
    return module


def get_functions(mod) -> list:
    # get function pointers
    mod_functions = []
    modname = mod.__name__.split('.')[-1]
    for method in TYPE_METHODS:
        try:
            method_name = f"{method}_{modname}"
            func = getattr(mod, method_name) 
            mod_functions.append(func)
        except:
            continue
    return mod_functions


def register_functions(functions: list, core) -> list[str]:
    registered_functions = []
    for func in functions:
        func_name = func.__name__
        core.process_registry.register_function(func_name, func)
        registered_functions.append(func_name)
    return registered_functions
    

# construct schema and fill in methods
def construct_schema(modname: str):
    logger = getLogger()

    # get module
    mod = verify_interface(modname)

    # get function pointers
    mod_functions = get_functions(mod)

    # TODO: register each type function pointer with its name and populate new list with string ids

    # get type name
    type_name = mod.__name__.split('.')[-1]

    # TODO: instead of mod functions, do func ids
    schema = dict(zip(
        [f"_{method}" for method in TYPE_METHODS],
        mod_functions
    ))
    schema["_type"] = type_name
    schema["_default"] = mod.default()
    return schema


def register(schema, core):
    type_name = schema.get("_type")
    return core.register_types({type_name: schema})


def register_type(module_name: str, core, export: bool = True):
    schema = construct_schema(module_name)
    register(schema, core)

    def_path = os.path.join(
        os.path.dirname(__file__),
        "definitions",
        f"{module_name}.json"
    )
    if not os.path.exists(def_path):
        definition = core.types().get(module_name)
        definition.pop("_default", None)
        with open(def_path, "w") as f:
            json.dump(definition, f, indent=4)


def test_register_type(ecore):
    modname = "unum"
    register_type(modname, ecore)
    assert ecore.types().get('unum') is not None
