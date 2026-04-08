"""
======================
E. coli Bigraph Types
======================

Custom bigraph-schema types for the E. coli whole-cell model.  These
allow ``core.infer()`` and ``translate_ports()`` to produce proper
typed schemas from the vivarium ``ports_schema()`` format.

Types provided:

- **Method** — serializable callables (updaters, dividers)
- **UnumUnits** — Unum unit objects
- **Quantity** — pint Quantity (value + units)
- **CSRMatrix** — scipy sparse matrices (stoichiometry)
- **UnitsArray** — wholecell UnitStructArray (structured arrays with units)
- **StepInstance** / **ProcessInstance** — vivarium process instance schemas

Also provides ``translate_ports()`` which converts vivarium
``ports_schema()`` dicts into bigraph-schema type trees.
"""

import copy
import typing
import importlib

import numpy as np
import pint
from plum import dispatch
from dataclasses import dataclass, field
from scipy.sparse._csr import csr_matrix
from unum import Unum

from bigraph_schema.schema import (
    Node, String, Float, Integer, Array, List, Tuple, Link, Overwrite, Protocol,
)
from bigraph_schema.methods import (
    infer, set_default, default, serialize, realize, render,
    wrap_default, resolve, reify_schema, validate, merge_update,
    apply, reconcile,
)
from bigraph_schema.methods.handle_parameters import align_parameters

from vivarium.core.process import Process as VivariumProcess, Step as VivariumStep
from process_bigraph import Step as BigraphStep, Process as BigraphProcess, StepLink, ProcessLink

from wholecell.utils.unit_struct_array import UnitStructArray


# ============================================================================
# Method type — serializable callables
# ============================================================================

@dataclass(kw_only=True)
class Method(Node):
    module: String = field(default_factory=String)
    instance: object = field(default_factory=object)
    attribute: String = field(default_factory=String)


@dispatch
def infer(core, value: typing.Callable, path: tuple = ()):
    if hasattr(value, '__self__'):
        data = {
            'module': value.__module__,
            'instance': value.__self__.__class__.__name__,
            'attribute': value.__func__.__name__}
    else:
        data = {
            'module': value.__module__,
            'instance': None,
            'attribute': value.__name__}
    method = Method(**data)
    return set_default(method, value), []


@dispatch
def serialize(schema: Method, state):
    if isinstance(state, dict):
        return state
    return {
        'module': str(schema.module),
        'instance': str(schema.instance),
        'attribute': schema.attribute}


@dispatch
def realize(core, schema: Method, encode, path=()):
    if callable(encode):
        return schema, encode, []
    elif isinstance(encode, dict):
        module_name = encode.get('module') or str(schema.module)
        instance_name = encode.get('instance') or str(schema.instance)
        attribute_name = encode.get('attribute') or str(schema.attribute)
        mod = importlib.import_module(module_name)
        if instance_name and instance_name != 'None':
            cls = getattr(mod, instance_name)
            func = getattr(cls, attribute_name)
        else:
            func = getattr(mod, attribute_name)
        return schema, func, []
    return schema, encode, []


@dispatch
def render(schema: Method, defaults=False):
    data = {
        '_type': 'method',
        'module': schema.module,
        'instance': str(schema.instance),
        'attribute': schema.attribute}
    return wrap_default(schema, data) if defaults else data


# ============================================================================
# UnumUnits type — Unum unit objects
# ============================================================================

def unum_dimension(value):
    dimension = {}
    for unit, scale in value._unit.items():
        entry = value._unitTable[unit]
        base_unit = {unit: scale}
        if entry[0]:
            dimension_unit = entry[0]._unit
            base_key = list(dimension_unit.keys())[0]
            base_unit = {base_key: scale}
        dimension.update(base_unit)
    return dimension


@dataclass(kw_only=True)
class UnumUnits(Node):
    """Wraps a Unum (or Pint) Quantity. Function bodies receive the
    Quantity unchanged so dimensional arithmetic in process internals
    keeps working — `_units` is documentary metadata describing the
    expected pint-parseable unit string for the slot, validated at
    wire build time but not converted at runtime."""
    _schema_keys = Node._schema_keys | frozenset({'_units'})
    _dimension: typing.Dict = field(default_factory=dict)
    units: typing.Dict = field(default_factory=dict)
    magnitude: Node = field(default_factory=Node)
    _units: str = ''


@dispatch
def infer(core, value: Unum, path: tuple = ()):
    dimension = unum_dimension(value)
    magnitude, _ = infer(core, value.asNumber(), path + (value.strUnit(),))
    schema = UnumUnits(_dimension=dimension, units=value._unit, magnitude=magnitude)
    return set_default(schema, value), []


@dispatch
def default(schema: UnumUnits):
    if schema._default:
        return schema._default
    return Unum(schema.units, default(schema.magnitude))


@dispatch
def serialize(schema: UnumUnits, state):
    if isinstance(state, dict):
        return state
    if state is None:
        return schema._default if schema._default else default(schema)
    return {
        'units': state._unit,
        'magnitude': serialize(schema.magnitude, state.asNumber())}


@dispatch
def resolve(schema: UnumUnits, update: UnumUnits, path=()):
    return schema


@dispatch
def realize(core, schema: UnumUnits, encode, path=()):
    if isinstance(encode, Unum):
        return schema, encode, []
    _, magnitude, _ = realize(core, schema.magnitude, encode['magnitude'], path=path)
    return schema, Unum(encode['units'], magnitude), []


@dispatch
def render(schema: UnumUnits, defaults=False):
    data = {
        '_type': 'unum',
        '_dimension': schema._dimension,
        'units': schema.units,
        'magnitude': render(schema.magnitude)}
    if schema._units:
        data['_units'] = schema._units
    return wrap_default(schema, data) if defaults else data


@dispatch
def align_parameters(schema: UnumUnits, parameters):
    """unum[g/L] — single parameter is the documented unit string."""
    if len(parameters) == 1:
        return {'_units': parameters[0]}
    return {}


@dispatch
def reify_schema(core, schema: UnumUnits, parameters):
    """Set documented unit string verbatim — does not enforce conversion.

    Function bodies receive the Quantity unchanged. The unit string is
    metadata that lets tooling and analyses know what dimension the
    slot expects.
    """
    if '_units' in parameters:
        units_param = parameters['_units']
        if isinstance(units_param, str):
            schema._units = units_param
    return schema


# ============================================================================
# Quantity type — pint Quantity (value + units)
# ============================================================================

ureg = pint.UnitRegistry()


def units_dict(value):
    return {key: subvalue for key, subvalue in value.unit_items()}


@dataclass(kw_only=True)
class Quantity(Node):
    units: typing.Dict = field(default_factory=dict)
    magnitude: Node = field(default_factory=Node)


@dispatch
def infer(core, value: pint.Quantity, path: tuple = ()):
    units = units_dict(value)
    magnitude, _ = infer(core, value.magnitude, path + ('magnitude',))
    schema = Quantity(units=units, magnitude=magnitude)
    return set_default(schema, value), []


@dispatch
def default(schema: Quantity):
    if schema._default:
        return schema._default
    return {'units': schema.units, 'magnitude': default(schema.magnitude)}


@dispatch
def serialize(schema: Quantity, state):
    if isinstance(state, dict):
        return state
    if isinstance(state, int):
        return {'units': schema.units, 'magnitude': serialize(schema.magnitude, state)}
    return {
        'units': schema.units,
        'magnitude': serialize(schema.magnitude, state.magnitude)}


@dispatch
def resolve(schema: Integer, update: Array, path=()):
    return update


@dispatch
def resolve(schema: Quantity, update: Quantity, path=()):
    if schema.units == update.units:
        return update


@dispatch
def resolve(schema: Quantity, update: Integer, path=()):
    return schema


@dispatch
def resolve(schema: Tuple, update: List, path=()):
    return schema


@dispatch
def realize(core, schema: Quantity, encode, path=()):
    if isinstance(encode, pint.Quantity):
        return schema, encode, []
    if isinstance(encode, dict):
        _, magnitude, _ = realize(
            core, schema.magnitude, encode['magnitude'], path + ('magnitude',))
        decode = (magnitude, tuple(schema.units.items()))
    else:
        decode = (encode, tuple(schema.units.items()))
    return schema, ureg.Quantity.from_tuple(decode), []


@dispatch
def render(schema: Quantity, defaults=False):
    data = {
        '_type': 'quantity',
        'units': schema.units,
        'magnitude': render(schema.magnitude)}
    return wrap_default(schema, data) if defaults else data


# ============================================================================
# CSRMatrix type — scipy sparse matrices
# ============================================================================

@dataclass(kw_only=True)
class CSRMatrix(Node):
    _shape: typing.Tuple[int] = field(default_factory=tuple)
    _data: np.dtype = field(default_factory=lambda: np.dtype('float64'))
    data: Array = field(default_factory=Array)
    indices: Array = field(default_factory=Array)
    pointers: Array = field(default_factory=Array)


@dispatch
def infer(core, value: csr_matrix, path: tuple = ()):
    data = {
        '_shape': value.shape,
        '_data': infer(core, value.dtype, path=path + ('_data',))[0],
        'data': infer(core, value.data, path=path + ('data',))[0],
        'indices': infer(core, value.indices, path=path + ('indices',))[0],
        'pointers': infer(core, value.indptr, path=path + ('pointers',))[0]}
    schema = CSRMatrix(**data)
    return set_default(schema, value), []


@dispatch
def serialize(schema: CSRMatrix, state):
    if isinstance(state, dict):
        return state
    return {
        'data': serialize(schema.data, state.data),
        'indices': serialize(schema.indices, state.indices),
        'pointers': serialize(schema.pointers, state.indptr)}


@dispatch
def realize(core, schema: CSRMatrix, encode, path=()):
    if isinstance(encode, csr_matrix):
        return schema, encode, []
    inner = tuple(
        realize(core, getattr(schema, key), encode[key], path + (key,))[1]
        for key in ['data', 'indices', 'pointers'])
    return schema, csr_matrix(inner, shape=schema._shape), []


@dispatch
def reify_schema(core, schema: CSRMatrix, parameters):
    for key, parameter in parameters.items():
        subkey = core.access(parameter)
        setattr(schema, key, subkey)
    return schema


@dispatch
def render(schema: CSRMatrix, defaults=False):
    data = {
        '_type': 'csr_matrix',
        '_shape': schema._shape,
        '_data': render(schema._data),
        'data': render(schema.data),
        'indices': render(schema.indices),
        'pointers': render(schema.pointers)}
    return wrap_default(schema, data) if defaults else data


@dispatch
def validate(core, schema: CSRMatrix, state):
    return


# ============================================================================
# UnitsArray type — wholecell UnitStructArray
# ============================================================================

@dataclass(kw_only=True)
class UnitsArray(Node):
    struct: Array = field(default_factory=Array)
    units: UnumUnits = field(default_factory=UnumUnits)


@dispatch
def infer(core, value: UnitStructArray, path: tuple = ()):
    data = {
        'struct': infer(core, value.struct_array, path=path + ('struct',))[0],
        'units': infer(core, value.units, path=path + ('units',))[0]}
    schema = UnitsArray(**data)
    return set_default(schema, value), []


@dispatch
def serialize(schema: UnitsArray, state):
    if isinstance(state, dict):
        return state
    return {
        'struct': serialize(schema.struct, state.struct_array),
        'units': serialize(schema.units, state.units)}


@dispatch
def realize(core, schema: UnitsArray, encode, path=()):
    if isinstance(encode, UnitStructArray):
        return schema, encode, []
    inner = tuple(
        realize(core, getattr(schema, key), encode[key], path + (key,))[1]
        for key in ['struct', 'units'])
    return schema, UnitStructArray(*inner), []


@dispatch
def render(schema: UnitsArray, defaults=False):
    data = {
        'struct': render(schema.struct),
        'units': render(schema.units)}
    return wrap_default(schema, data) if defaults else data


# ============================================================================
# Process instance types — for schema inference on vivarium instances
# ============================================================================

@dataclass(kw_only=True)
class FunctionInstance(Node):
    _inputs: Node = field(default_factory=Node)
    _outputs: Node = field(default_factory=Node)
    address: String = field(default_factory=String)
    config: Node = field(default_factory=Node)


@dataclass(kw_only=True)
class StepInstance(FunctionInstance):
    priority: Float = field(default_factory=Float)


@dataclass(kw_only=True)
class ProcessInstance(FunctionInstance):
    interval: Float = field(default_factory=Float)


def function_instance_data(core, value, path):
    if not hasattr(value, 'core'):
        value.core = core

    config = value.parameters
    if hasattr(value, 'config_schema') and value.config_schema:
        config_schema = value.config_schema
    else:
        # Don't infer config — it's expensive and contains opaque
        # simData objects. Use Quote to pass it through untouched.
        from bigraph_schema.schema import Quote, Node
        config_schema = Quote(_value=Node())

    ports_schema = translate_ports(
        core, value.ports_schema(), path=path + ('ports',))

    return {
        '_inputs': ports_schema,
        '_outputs': ports_schema,
        'address': Protocol(_default=f'local:{value.name}'),
        'config': config_schema}


@dispatch
def infer(core, value: VivariumStep, path: tuple = ()):
    data = function_instance_data(core, value, path)
    data['priority'] = Float(_default=value.parameters.get('priority', 0.0))
    instance = StepInstance(**data)
    return set_default(instance, value), []


@dispatch
def infer(core, value: VivariumProcess, path: tuple = ()):
    data = function_instance_data(core, value, path)
    data['interval'] = Float(_default=value.parameters.get('timestep', 1.0))
    instance = ProcessInstance(**data)
    return set_default(instance, value), []


# ============================================================================
# translate_ports — convert v1 ports_schema to bigraph schema
# ============================================================================

def translate_ports(core, ports, path=()):
    """Convert a vivarium ports_schema dict into a bigraph-schema type tree.

    Args:
        core: bigraph-schema Core instance (used for type inference).
        ports: Dict from ports_schema() with _default, _updater, etc.
        path: Current path for recursive descent.

    Returns:
        A bigraph-schema type tree (Node, Overwrite, dict of sub-schemas).
    """
    if isinstance(ports, dict):
        if not ports:
            return Node()

        if '_default' in ports:
            state = ports['_default']
            if isinstance(state, tuple) and state == ():
                state = []
            schema = core.infer(state)

            if '_updater' in ports and ports['_updater'] == 'set':
                schema = Overwrite(_value=schema)

            schema._default = state
            return schema

        elif '_updater' in ports:
            schema = Node()
            if ports['_updater'] == 'set':
                schema = Overwrite(_value=schema)
            return schema

        else:
            result = {}
            for key, subports in ports.items():
                if not key.startswith('_'):
                    result[key] = translate_ports(core, subports)
            return result

    return Node()


# ============================================================================
# BulkArray — structured array with sparse count updates
# ============================================================================

@dataclass(kw_only=True)
class BulkArray(Array):
    """Structured numpy array where sparse [(index, delta)] updates
    target the 'count' field specifically."""
    pass


_BULK_APPLY_COUNT = [0]
_BULK_TOTAL_DELTA = [0]

@dispatch
def apply(schema: BulkArray, state, update, path):
    """Apply sparse index updates to the 'count' field of a bulk array."""
    if isinstance(update, list):
        # Sparse index updates: [(index_array, count_delta), ...]
        for idx, delta in update:
            _BULK_APPLY_COUNT[0] += 1
            _BULK_TOTAL_DELTA[0] += int(delta.sum()) if hasattr(delta, 'sum') else int(delta)
            state['count'][idx] += delta
        return state, []

    # Delegate to standard Array apply for non-sparse updates
    return apply(Array(_shape=schema._shape, _data=schema._data),
                 state, update, path)


# ============================================================================
# UniqueArray — structured array for unique molecules (set/add/delete ops)
# ============================================================================

@dataclass(kw_only=True)
class UniqueArray(Array):
    """Structured numpy array for unique molecule populations.

    Updates use dict format with 'set', 'add', and 'delete' keys:
      - set: list of {col: values} dicts — overwrite active rows
      - add: list of {col: values} dicts — activate inactive rows
      - delete: list of index arrays — deactivate rows

    The reconciler batches operations from multiple steps and applies
    them in order: set → add → delete.
    """
    pass


def _get_free_indices(array, n_new):
    """Find inactive slots in a unique molecule array, extending if needed."""
    from ecoli.library.schema import get_free_indices
    return get_free_indices(array, n_new)


@dispatch
def reconcile(schema: UniqueArray, updates: list):
    """Batch set/add/delete operations across all updates."""
    sets = []
    adds = []
    deletes = []

    for update in updates:
        if update is None or not isinstance(update, dict):
            continue
        if 'set' in update:
            val = update['set']
            if isinstance(val, list):
                sets.extend(val)
            elif isinstance(val, dict):
                sets.append(val)
        if 'add' in update:
            val = update['add']
            if isinstance(val, list):
                adds.extend(val)
            elif isinstance(val, dict):
                adds.append(val)
        if 'delete' in update:
            val = update['delete']
            if isinstance(val, list):
                if len(val) > 0:
                    if isinstance(val[0], (list, np.ndarray)):
                        deletes.extend(val)
                    elif isinstance(val[0], (int, np.integer)):
                        deletes.append(val)
            elif isinstance(val, np.ndarray):
                deletes.append(val)

    result = {}
    if sets:
        result['set'] = sets
    if adds:
        result['add'] = adds
    if deletes:
        result['delete'] = deletes
    return result if result else None


@dispatch
def apply(schema: UniqueArray, state, update, path):
    """Apply batched unique molecule operations: set → add → delete."""
    if update is None or not isinstance(update, dict) or len(update) == 0:
        return state, []

    if not state.flags.owndata:
        result = state.copy()
    else:
        result = state
    result.flags.writeable = True

    active_mask = result['_entryState'].view(np.bool_)

    # Save initial active indices for delete operations
    initially_active_idx = None
    if 'delete' in update:
        initially_active_idx = np.nonzero(active_mask)[0]

    # 1. Set operations: overwrite columns for active rows
    for set_update in update.get('set', []):
        for col, col_values in set_update.items():
            result[col][active_mask] = col_values

    # 2. Add operations: activate inactive rows with new data
    for add_update in update.get('add', []):
        n_new = len(next(iter(add_update.values())))
        result, free_indices = _get_free_indices(result, n_new)
        if 'unique_index' not in add_update:
            result['unique_index'][free_indices] = (
                np.arange(n_new) + result.metadata
            )
            result.metadata += n_new
        for col, col_values in add_update.items():
            result[col][free_indices] = col_values
        result['_entryState'][free_indices] = 1

    # 3. Delete operations: deactivate rows
    if initially_active_idx is not None:
        for delete_indices in update.get('delete', []):
            rows_to_delete = initially_active_idx[delete_indices]
            result[rows_to_delete] = np.zeros(1, dtype=result.dtype)

    result.flags.writeable = False
    return result, []


# ============================================================================
# Type registry
# ============================================================================

ECOLI_TYPES = {
    'unum': UnumUnits,
    'quantity': Quantity,
    'csr_matrix': CSRMatrix,
    'units_array': UnitsArray,
    'method': Method,
    'step_instance': StepInstance,
    'process_instance': ProcessInstance,
    'step': StepLink,
    'process': ProcessLink,
    'bulk_array': BulkArray,
    'unique_array': UniqueArray,
}
