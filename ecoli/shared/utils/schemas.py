import copy
import dataclasses
from functools import wraps
from typing import Dict, Any

import numpy as np
import unum
from pint import Quantity
from bigraph_schema import deep_merge
from bigraph_schema.type_functions import deserialize_array

from ecoli.library.schema import UniqueNumpyUpdater, get_bulk_counts, bulk_numpy_updater, UNIQUE_DIVIDERS, divide_bulk


DEFAULT_DICT_TYPE = "tree"
PORTS_MAPPER = {
    "int": "integer",
    "bool": "boolean",
    "list": "list",
    "tuple": "tuple",
    "float": "float",
    "ndarray": "list",  # TODO: eventually formalize this to "array",
    "dict": DEFAULT_DICT_TYPE,
    "NoneType": "any",  # TODO: be less general here (attempt further parsing if need be)
    "int64": "integer",
    "float32": "float",
    "float64": "float",
    "int32": "integer",
    "int16": "integer",
    "uint16": "integer",
    "Unum": "unum",
    "str": "string",
    "Quantity": "unit"
}


def get_unique_fields(unique: np.ndarray) -> list[np.ndarray]:
    """
    Args:
        unique: Numpy structured array of attributes for one unique molecule
    Returns:
        List of contiguous (required by orjson) arrays, one for each attribute
    """
    return [np.ascontiguousarray(unique[field]) for field in unique.dtype.names]


def get_defaults_schema(d):
    """Returns a dict whose keys match that of d, except replacing innermost values (v) with their corresponding _default declarations.
    Used for migration.
    """
    if isinstance(d, dict):
        extracted_attrs = ['_divider', '_type', '_default']
        is_inner = any([k in extracted_attrs for k in d])

        # case: is inner dict from which val type is extracted
        if is_inner:
            schema = {}
            if '_divider' in d:
                schema['_divide'] = d['_divider']

            if '_type' in d:
                schema['_type'] = d['_type']
            
            if '_default' in d:
                value = d['_default'] 
                type_id = get_schema_type(value)
                schema['_type'] = type_id
            
            if len(schema.keys()) == 1 and "_type" in list(schema.keys()):
                return schema['_type']
            else:
                return schema
        # case: is outer dict
        else:
            # and empty (return)
            if not len(d.keys()):
                return "tree"
            # or nested (recurse)
            else:
                return {k: get_defaults_schema(v) for k, v in d.items()}
    else:
        return d
    

def collapse_defaults(d):
    """Returns a dict whose keys match that of d, except replacing innermost values (v) with their corresponding _default declarations.
    Used for migration.
    """
    if isinstance(d, dict):
        if '_default' in d:
            return d['_default'] 
        else:
            return {k: collapse_defaults(v) for k, v in d.items()}
    else:
        return d


def flatten_state(state, parent_key='', sep='.'):
    """Returns a flat dict in which the keys express port nesting via dot notation and the values being store values.
    Used for migration.
    """
    items = {}
    for k, v in state.items():
        new_key = f"{parent_key}{sep}{k}" if parent_key else k
        if isinstance(v, dict):
            items.update(flatten_state(v, new_key, sep=sep))
        else:
            items[new_key] = v
    return items


def capture_arg(arg_to_capture: str):
    """
    Usage:

    @capture_arg('state')
    def update(self, state):
        # self._captured_state will be available here
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            self_obj = args[0]  # first argument is always `self`

            arg_names = func.__code__.co_varnames[:func.__code__.co_argcount]
            all_args = dict(zip(arg_names, args))
            all_args.update(kwargs)

            captured_value = all_args.get(arg_to_capture)
            setattr(self_obj, f"_captured_{arg_to_capture}", captured_value)

            return func(*args, **kwargs)
        return wrapper
    return decorator


def dict_union(a: dict, b: dict, mutate_a: bool = False, secure: bool = False) -> dict:
    """
    Performs `bigraph_schema.deep_merge(a, b)` but returns a new object rather than mutating `a` if
    and only if `mutate_a = True`, otherwise performs a regular call to `deep_merge`. If `secure` is `True`,
    then both `a` and `b` will be explicitly deleted from memory, leaving only this return.
    """
    if not mutate_a:
        a = copy.deepcopy(a)
    c = deep_merge(a, b)
    if secure:
        del a
        del b
    return c


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


def numpy_schema(name: str, emit: bool = True) -> Dict[str, Any]:
    """
    Helper function used to define defaults that get parsed for inputs and outputs and initial states for bulk and unique molecules

    Args:
        name: `bulk` for bulk molecules or one of the keys in :py:data:`UNIQUE_DIVIDERS`
            for unique molecules
        emit: TODO: implement this

    Returns:
        Fully configured and bigraph-schema-compliant ports schema for molecules of type `name`
    """
    from ecoli.shared.registry import ecoli_core

    registered_types = ecoli_core.types()
    if name in registered_types:
        type_schema = registered_types[name]
        type_schema.pop("_type")
        return type_schema
    
    schema = {
        "_default": np.empty((0,), dtype=tuple), 
        "_serialize": get_unique_fields,
        "_deserialize": deserialize_array,
        "_divide": UNIQUE_DIVIDERS.get(name),
        "_description": {
            "emit": emit
        }
    }
    divider = UNIQUE_DIVIDERS.get(name)
    if divider is not None:
        schema["_divide"] = divider
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


def get_schema_type(value: Any) -> str:
    type_name = type(value).__name__
    if isinstance(value, np.ndarray):
        shape = str(value.shape)
        _type = PORTS_MAPPER.get(str(value.dtype), 'any')
        formatted_shape = str(shape).replace(",", "|")
        return SchemaType(f"array[({formatted_shape}),{_type}]").id
    else:
        return SchemaType(PORTS_MAPPER.get(type_name, 'any')).id


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


def get_config_schema(defaults: dict[str, Any]):
    """Translates vivarium.core.Process.defaults into bigraph-schema types to be consumed by pbg.Composite."""
    config_schema = {}
    for k, v in defaults.copy().items():
        if not isinstance(v, dict):
            type_name = type(v).__name__
            if type_name in PORTS_MAPPER.keys():
                # handle type
                _type = PORTS_MAPPER[type_name]
                
                # handle default
                if isinstance(v, Quantity):
                    v = v.magnitude
                elif isinstance(v, np.ndarray):
                    v = v.tolist()
                elif isinstance(v, unum.Unum):
                    v = v.asNumber()

                config_schema[k] = {
                    "_type": _type,  # TODO: provide a more specific lookup
                    "_default": v
                }
        else:
            if "_type" in v.keys():
                # case: already has a bgs-compliant type def
                config_schema[k] = v
            else:
                # case: use type with default if default value assigned
                config_schema[k] = DEFAULT_DICT_TYPE if not len(v.keys()) else {"_type": DEFAULT_DICT_TYPE, "_default": v}

    return config_schema


def test_get_config_schema():
    from ecoli.migrated.transcript_elongation import TranscriptElongation
    defaults = TranscriptElongation.defaults
    config_schema = get_config_schema(defaults)


def export_vivarium_unit_schemas(types_dir: str | None = None):
    import json
    import os
    from vivarium.library.units import units as vivunits
    from ecoli import shared

    if types_dir is None:
        types_dir = shared.__path__.pop() + '/types/definitions'
        
    for item in dir(vivunits):
        v = getattr(vivunits, item)
        if type(v).__name__.lower() == "unit":
            atomic_unit = 1 * v
            type_name = str(atomic_unit.u).replace(' ', '')
            schema_fp = os.path.join(types_dir, f'{type_name}.json')
            if not os.path.exists(schema_fp):
                schema = {
                    '_apply': 'apply_units',
                    '_check': 'check_units',
                    '_serialize': 'serialize_units',
                    '_deserialize': 'deserialize_units',
                    '_description': 'type to represent values with scientific units',
                    '_type': type_name
                }
                with open(schema_fp, 'w') as fp:
                    json.dump(schema, fp, indent=4)


def infer_state_from_composer(composer):
    composition = composer.generate()
    processes = composition.get('processes')
    topology = composition.get('topology')
    
    state = {}
    for process_id, process in processes.items():
        ports = topology.get(process_id)
        state[process_id] = {
            "_type": "process",
            "address": f"local:{process_id}",
            "config": {},  # get config from process
            "inputs": ports,
            "outputs": ports
        }
    return state
