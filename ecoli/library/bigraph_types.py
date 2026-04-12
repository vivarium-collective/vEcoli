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
import os
import typing
import importlib

import numpy as np
import pint
from plum import dispatch
from dataclasses import dataclass, field
from scipy.sparse._csr import csr_matrix
from unum import Unum

from bigraph_schema.schema import (
    Node, String, Float, Integer, Array, List, Tuple, Map, Link, Overwrite, Wrap, Protocol,
)
from bigraph_schema.methods import (
    infer, set_default, default, serialize, realize, render,
    wrap_default, resolve, reify_schema, validate, merge_update,
    apply, reconcile, divide, bundle, BundleContext,
)
from bigraph_schema.methods.handle_parameters import align_parameters

from vivarium.core.process import Process as VivariumProcess, Step as VivariumStep
from process_bigraph import Step as BigraphStep, Process as BigraphProcess, StepLink, ProcessLink

from wholecell.utils.unit_struct_array import UnitStructArray


# ============================================================================
# Function type — serializable standalone callables (not bound to an instance)
# ============================================================================

@dataclass(kw_only=True)
class Function(Node):
    module: String = field(default_factory=String)
    instance: typing.Optional[str] = None
    attribute: String = field(default_factory=String)


@dispatch
def infer(core, value: typing.Callable, path: tuple = ()):
    if hasattr(value, '__self__') and hasattr(value, '__func__'):
        # Bound method → Method type (references an instance)
        return set_default(Method(), value), []
    elif (hasattr(value, '__code__')
          and getattr(value.__code__, 'co_filename', '') == '<string>'):
        # Dynamic function created via exec (e.g. from build_ode) —
        # can't be imported by name, must be rebuilt from sympy source.
        return set_default(DerivedFunction(), value), []
    elif hasattr(value, '__name__') and hasattr(value, '__module__'):
        # Standalone function → Function type
        data = {
            'module': value.__module__,
            'instance': None,
            'attribute': value.__name__}
        return set_default(Function(**data), value), []
    else:
        # Callable object (e.g. CubicSpline, functors) → Object type
        from bigraph_schema.schema import Object
        cls = type(value)
        class_path = f'{cls.__module__}.{cls.__name__}'
        schema = Object(_class=class_path)
        return set_default(schema, value), []


@dispatch
def serialize(schema: Function, state):
    if isinstance(state, dict):
        return state
    if state is None:
        return None
    if callable(state):
        if hasattr(state, '__self__'):
            return {
                'module': state.__module__,
                'instance': state.__self__.__class__.__name__,
                'attribute': state.__func__.__name__}
        else:
            return {
                'module': state.__module__,
                'instance': None,
                'attribute': state.__name__}
    return {
        'module': str(schema.module),
        'instance': str(schema.instance),
        'attribute': str(schema.attribute)}


@dispatch
def realize(core, schema: Function, encode, path=()):
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
def render(schema: Function, defaults=False):
    result = 'function'
    return wrap_default(schema, result) if defaults else result


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
    """Parameterized Unum type: ``unum[<magnitude_type>,<unit_string>]``.

    Examples::

        unum                          # scalar float magnitude, no unit annotation
        unum[float,fg]                # scalar float, femtograms
        unum[array[16321|9,float],g/mol]  # 2D array magnitude

    The magnitude field carries the schema for the numeric payload
    (Float, Array, etc.). serialize/realize dispatch on it so arrays
    and scalars are handled without branches.
    """
    _schema_keys = Node._schema_keys | frozenset({'_units'})
    _dimension: typing.Dict = field(default_factory=dict)
    units: typing.Dict = field(default_factory=dict)
    magnitude: Node = field(default_factory=lambda: Float())
    _units: str = ''


@dispatch
def infer(core, value: Unum, path: tuple = ()):
    dimension = unum_dimension(value)
    magnitude_schema, _ = infer(core, value.asNumber(), path + (value.strUnit(),))
    schema = UnumUnits(
        _dimension=dimension, units=value._unit, magnitude=magnitude_schema)
    return set_default(schema, value), []


@dispatch
def default(schema: UnumUnits):
    if schema._default:
        return schema._default
    return Unum(schema.units, default(schema.magnitude))


@dispatch
def apply(schema: UnumUnits, state, update, path):
    """Unum quantities use overwrite semantics, but only for actual
    Unum updates — not for bare numeric defaults (0, 0.0)."""
    if update is None:
        return state, []
    if isinstance(update, (int, float)) and update == 0 and state is not None:
        return state, []
    return update, []


@dispatch
def serialize(schema: UnumUnits, state):
    if isinstance(state, dict):
        return state
    if state is None:
        return None
    return {
        'units': state._unit,
        'magnitude': serialize(schema.magnitude, state.asNumber())}


@dispatch
def resolve(schema: UnumUnits, update: UnumUnits, path=()):
    return schema


@dispatch
def resolve(schema: UnumUnits, update: Float, path=()):
    """UnumUnits is more specific than Float — keep Unum."""
    return schema


@dispatch
def resolve(schema: Float, update: UnumUnits, path=()):
    """UnumUnits is more specific than Float — use Unum."""
    return update


@dispatch
def realize(core, schema: UnumUnits, encode, path=()):
    if isinstance(encode, Unum):
        return schema, encode, []
    if isinstance(encode, (int, float)):
        return schema, Unum(schema.units, encode), []
    if isinstance(encode, dict) and 'units' in encode:
        # Dict form from serialize: {'units': {...}, 'magnitude': ...}
        _, magnitude, _ = realize(core, schema.magnitude, encode['magnitude'], path)
        return schema, Unum(encode['units'], magnitude), []
    return schema, encode, []


@dispatch
def render(schema: UnumUnits, defaults=False):
    mag_render = render(schema.magnitude)
    if schema._units:
        result = f'unum[{mag_render},{schema._units}]'
    elif mag_render != 'float':
        result = f'unum[{mag_render}]'
    else:
        result = 'unum'
    return wrap_default(schema, result) if defaults else result


@dispatch
def align_parameters(schema: UnumUnits, parameters):
    """Handle unum[magnitude_type,unit_string] or unum[unit_string].

    If a single parameter is a Node (schema type), it's the magnitude type.
    If it's a string, it's the unit annotation.
    Two parameters: first is magnitude, second is units.
    """
    if len(parameters) == 2:
        return {'magnitude': parameters[0], '_units': parameters[1]}
    if len(parameters) == 1:
        p = parameters[0]
        if isinstance(p, Node):
            return {'magnitude': p}
        return {'_units': p}
    return {}


@dispatch
def reify_schema(core, schema: UnumUnits, parameters):
    """Reify magnitude type and unit string from parameters."""
    if 'magnitude' in parameters:
        mag_param = parameters['magnitude']
        if isinstance(mag_param, str):
            schema.magnitude = core.access(mag_param)
        elif isinstance(mag_param, Node):
            schema.magnitude = mag_param
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
    magnitude: Float = field(default_factory=Float)


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
    if isinstance(state, (int, float)):
        return {'units': schema.units, 'magnitude': state}
    if isinstance(state, pint.Quantity):
        units = dict(state.unit_items()) or schema.units
        return {'units': units, 'magnitude': float(state.magnitude)}
    return {'units': schema.units, 'magnitude': state}


@dispatch
def resolve(schema: Integer, update: Array, path=()):
    return update


@dispatch
def resolve(schema: Quantity, update: Quantity, path=()):
    if not schema.units or schema.units == update.units:
        return update
    return schema


@dispatch
def resolve(schema: Quantity, update: Integer, path=()):
    return schema


@dispatch
def resolve(schema: Tuple, update: List, path=()):
    return schema


@dispatch
def apply(schema: Quantity, state, update, path):
    """Quantities use overwrite semantics, but only for actual
    Quantity updates — not for bare numeric defaults (0, 0.0)
    which would erase a valid Quantity."""
    if update is None:
        return state, []
    if isinstance(update, (int, float)) and update == 0 and state is not None:
        return state, []
    return update, []


@dispatch
def realize(core, schema: Quantity, encode, path=()):
    if isinstance(encode, pint.Quantity):
        return schema, encode, []
    if isinstance(encode, dict) and 'magnitude' in encode:
        magnitude = encode['magnitude']
        if isinstance(magnitude, str):
            magnitude = float(magnitude)
        decode = (magnitude, tuple(encode.get('units', schema.units).items()))
        return schema, ureg.Quantity.from_tuple(decode), []
    if isinstance(encode, (int, float)):
        decode = (encode, tuple(schema.units.items()))
        return schema, ureg.Quantity.from_tuple(decode), []
    if isinstance(encode, str):
        # Parse string form: "2.1 millimolar"
        try:
            return schema, ureg.parse_expression(encode), []
        except Exception:
            pass
    return schema, encode, []


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
    if state is None:
        return None
    if isinstance(state, csr_matrix):
        return {
            'data': np.asarray(state.data).tolist(),
            'indices': np.asarray(state.indices).tolist(),
            'pointers': np.asarray(state.indptr).tolist()}
    # Already a plain array or other form — convert to list
    if isinstance(state, np.ndarray):
        return state.tolist()
    return state


@dispatch
def realize(core, schema: CSRMatrix, encode, path=()):
    if isinstance(encode, csr_matrix):
        return schema, encode, []
    # Dense ndarray — the process stores it as dense, convert to CSR
    if isinstance(encode, np.ndarray):
        return schema, csr_matrix(encode), []
    # List of lists — dense matrix from JSON
    if isinstance(encode, list):
        return schema, csr_matrix(np.array(encode)), []
    # Dict form with data/indices/pointers (from serialize)
    if isinstance(encode, dict) and 'data' in encode:
        inner = tuple(
            realize(core, getattr(schema, key), encode[key], path + (key,))[1]
            for key in ['data', 'indices', 'pointers'])
        data, indices, pointers = inner
        shape = tuple(schema._shape) if schema._shape else (
            len(pointers) - 1,
            int(indices.max()) + 1 if len(indices) > 0 else 0)
        return schema, csr_matrix(inner, shape=shape), []
    return schema, encode, []


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
    units: Map = field(default_factory=lambda: Map(_value=UnumUnits()))


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


def _serialize_structured_array(state):
    """Serialize a structured numpy array to a JSON-safe dict."""
    if not isinstance(state, np.ndarray) or not state.dtype.names:
        return state
    rows = []
    for record in state:
        row = []
        for name in state.dtype.names:
            val = record[name]
            if isinstance(val, np.ndarray):
                val = val.tolist()
            elif hasattr(val, 'item'):
                val = val.item()
            row.append(val)
        rows.append(row)
    return {
        '__structured_array__': True,
        'dtype': str(state.dtype),
        'data': rows,
    }


def _realize_structured_array(state):
    """Realize a structured array from its serialized dict form."""
    import ast
    if isinstance(state, np.ndarray):
        return state
    if isinstance(state, dict) and state.get('__structured_array__'):
        dtype = np.dtype(ast.literal_eval(state['dtype']))
        return np.array([tuple(r) for r in state['data']], dtype=dtype)
    return state


@dispatch
def serialize(schema: BulkArray, state):
    return _serialize_structured_array(state)


@dispatch
def render(schema: BulkArray, defaults=False):
    result = 'bulk_array'
    return wrap_default(schema, result) if defaults else result


@realize.dispatch
def realize(core, schema: BulkArray, state, path=()):
    if isinstance(state, np.ndarray):
        return schema, state, []
    state = _realize_structured_array(state)
    return schema, state, []


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


@dispatch
def divide(schema: BulkArray, state, context=None, path=(), rng=None):
    """Binomial split of bulk molecule counts. Delegates to v1's
    `divide_bulk` so v1 and v2 use bit-identical logic.

    v1's divide_bulk freezes the daughter arrays read-only as a
    defensive measure for vivarium's tree. v2 mutates state in place
    via apply(BulkArray) — we need them writeable.
    """
    if state is None:
        return None, None
    from ecoli.library.schema import divide_bulk
    a, b = divide_bulk(state)
    a = a.copy() if not a.flags.writeable else a
    b = b.copy() if not b.flags.writeable else b
    a.flags.writeable = True
    b.flags.writeable = True
    return a, b


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


@dispatch
def serialize(schema: UniqueArray, state):
    return _serialize_structured_array(state)


@dispatch
def render(schema: UniqueArray, defaults=False):
    result = 'unique_array'
    return wrap_default(schema, result) if defaults else result


@realize.dispatch
def realize(core, schema: UniqueArray, state, path=()):
    if isinstance(state, np.ndarray):
        return schema, state, []
    state = _realize_structured_array(state)
    if isinstance(state, np.ndarray):
        from ecoli.library.schema import MetadataArray
        if not isinstance(state, MetadataArray):
            next_idx = int(state['unique_index'].max()) + 1 if len(state) > 0 else 0
            state = MetadataArray(state, next_idx)
    return schema, state, []


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


def _as_op_list(value, leaf_check=None):
    """Normalize a unique-molecule operation value into a list.

    A single operation can be passed as either a dict (one op) or a
    list of dicts (batched ops). Reconcile produces lists; the
    reconcile fast-path passes single updates through unchanged, so
    apply must accept both forms. ``leaf_check`` (used for delete ops)
    decides whether a list is itself a single op or a batch — for
    deletes, a list of ints/np.integer IS the single op, while a list
    whose first element is a list/array is a batch.
    """
    if value is None:
        return ()
    if isinstance(value, dict):
        return (value,)
    if isinstance(value, list):
        if leaf_check is not None and len(value) > 0 and leaf_check(value[0]):
            return (value,)
        return tuple(value)
    if isinstance(value, np.ndarray):
        return (value,)
    return ()


@dispatch
def apply(schema: UniqueArray, state, update, path):
    """Apply batched unique molecule operations: set → add → delete.

    Tolerates both reconciled (lists of ops) and raw (single op) forms
    so the top-level reconcile fast-path can pass single updates through.
    """
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
    for set_update in _as_op_list(update.get('set')):
        for col, col_values in set_update.items():
            result[col][active_mask] = col_values

    # 2. Add operations: activate inactive rows with new data
    for add_update in _as_op_list(update.get('add')):
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

    # 3. Delete operations: deactivate rows.
    # A delete payload is either a single list/array of indices (one op)
    # or a list of such (batched). Distinguish by checking the first
    # element's type.
    def _is_index_leaf(x):
        return isinstance(x, (int, np.integer))
    if initially_active_idx is not None:
        for delete_indices in _as_op_list(
                update.get('delete'), leaf_check=_is_index_leaf):
            rows_to_delete = initially_active_idx[delete_indices]
            result[rows_to_delete] = np.zeros(1, dtype=result.dtype)

    result.flags.writeable = False
    return result, []


# Map v1 divider name → divider function. v1's divider_registry is the
# source of truth at runtime, but importing the functions directly avoids
# a registry round-trip and lets divide() be self-contained.
def _get_unique_divider_fn(divider_name):
    from ecoli.library.schema import (
        divide_by_domain, divide_RNAs_by_domain, divide_ribosomes_by_RNA,
        empty_dict_divider, divide_set_none, divide_binomial,
    )
    return {
        'by_domain': divide_by_domain,
        'rna_by_domain': divide_RNAs_by_domain,
        'ribosome_by_RNA': divide_ribosomes_by_RNA,
        'empty_dict': empty_dict_divider,
        'set_none': divide_set_none,
        'binomial_ecoli': divide_binomial,
    }.get(divider_name)


def _resolve_topology_path(context, base_path, rel_path):
    """Resolve a vivarium-style relative path (with leading '..' segments)
    against a context dict, starting from base_path. Returns the value at
    the resolved path or None if any segment is missing.
    """
    # Walk base_path up for each '..' in rel_path
    abs_path = list(base_path)
    for seg in rel_path:
        if seg == '..':
            if abs_path:
                abs_path.pop()
        else:
            abs_path.append(seg)
    cur = context
    for seg in abs_path:
        if isinstance(cur, dict) and seg in cur:
            cur = cur[seg]
        else:
            return None
    return cur


@dispatch
def divide(schema: UniqueArray, state, context=None, path=(), rng=None):
    """Divide a unique molecule structured array using v1's molecule-specific
    divider. The molecule name comes from the last segment of `path`
    (e.g. ('unique', 'full_chromosome') → 'full_chromosome'). Contextual
    dividers (by_domain, rna_by_domain, ribosome_by_RNA) need sibling
    unique molecule arrays, which we resolve from `context` using the
    relative paths declared in v1's UNIQUE_DIVIDERS topology.
    """
    if state is None:
        return None, None
    if not path:
        # No molecule identity — share by reference. Caller should
        # always pass the path so the right divider can be selected.
        return state, state

    from ecoli.library.schema import UNIQUE_DIVIDERS

    # The unique store uses SINGULAR molecule names (e.g. 'RNA',
    # 'active_RNAP', 'full_chromosome') but vEcoli's UNIQUE_DIVIDERS
    # dict mostly uses PLURAL names ('RNAs', 'active_RNAPs', etc.).
    # active_ribosome is the one exception that's singular in both.
    # Try several common plural forms.
    mol_name = path[-1]
    divider_info = UNIQUE_DIVIDERS.get(mol_name)
    if divider_info is None:
        # +s plural (most cases): RNA→RNAs, active_RNAP→active_RNAPs
        divider_info = UNIQUE_DIVIDERS.get(mol_name + 's')
    if divider_info is None:
        # +es plural (words ending in -x): DnaA_box→DnaA_boxes
        divider_info = UNIQUE_DIVIDERS.get(mol_name + 'es')
    if divider_info is None:
        # No v1 divider registered for this molecule. Default: share.
        return state, state

    divider_fn = _get_unique_divider_fn(divider_info['divider'])
    if divider_fn is None:
        return state, state

    # Resolve topology fields against the parent context. Vivarium
    # `..` goes up from the FULL port path (not one segment above), so
    # pass `path` directly as the base.
    topology = divider_info.get('topology', {})
    divider_state_arg = {}
    if context is not None:
        for port_name, port_path in topology.items():
            resolved = _resolve_topology_path(context, path, port_path)
            if resolved is not None:
                divider_state_arg[port_name] = resolved

    return divider_fn(state, divider_state_arg)


# ============================================================================
# SimData references — resolve at realize time from a sim_data pickle
# ============================================================================

# Global sim_data instance, set once at document load time via
# set_sim_data(). Realize methods read from this.
_sim_data = None
_sim_data_path = None


def set_sim_data(sim_data, path=None):
    """Set the global sim_data instance for SimDataRef resolution."""
    global _sim_data, _sim_data_path
    _sim_data = sim_data
    _sim_data_path = path


def get_sim_data():
    """Get the global sim_data instance, loading from path if needed."""
    global _sim_data, _sim_data_path
    if _sim_data is None and _sim_data_path is not None:
        import pickle
        with open(_sim_data_path, 'rb') as f:
            _sim_data = pickle.load(f)
    return _sim_data


def _resolve_dotted_path(obj, path_str):
    """Resolve a dotted attribute path with optional bracket indexing.

    Examples:
        'process.metabolism.stoichMatrix'
        'internal_state.bulk_molecules.bulk_data["id"]'
        'external_state.saved_media["minimal"]'
    """
    import re
    current = obj
    # Split on dots, but keep bracket expressions attached to their segment
    segments = re.split(r'\.(?![^[]*\])', path_str)
    for segment in segments:
        # Check for bracket indexing: 'attr["key"]' or 'attr[0]'
        bracket_match = re.match(r'([^[]+)\[(.+)\]$', segment)
        if bracket_match:
            attr_name = bracket_match.group(1)
            key_str = bracket_match.group(2).strip('"\'')
            # Resolve attribute first
            if isinstance(current, dict):
                current = current[attr_name]
            else:
                current = getattr(current, attr_name)
            # Then index
            if isinstance(current, dict):
                current = current[key_str]
            elif isinstance(current, np.ndarray) and current.dtype.names:
                current = current[key_str]
            else:
                try:
                    current = current[int(key_str)]
                except (ValueError, TypeError):
                    current = current[key_str]
        elif isinstance(current, dict):
            current = current[segment]
        elif hasattr(current, segment):
            current = getattr(current, segment)
        else:
            raise AttributeError(
                f"Cannot resolve '{segment}' in path '{path_str}' "
                f"on {type(current).__name__}")
    return current


@dataclass(kw_only=True)
class SimDataRef(Wrap):
    """Reference to a value in sim_data, resolved at realize time.

    Parameterized: ``sim_data_ref[array[float]]`` carries the inner type
    so the schema knows what the resolved value will be.

    The state value is a dotted attribute path string, e.g.:
        'process.metabolism.stoichMatrix'
        'internal_state.bulk_molecules.bulk_data["id"]'

    At realize time, the path is resolved against the global sim_data
    instance and the actual value (ndarray, dict, etc.) is returned.
    """
    pass


@realize.dispatch
def realize(core, schema: SimDataRef, state, path=()):
    if isinstance(state, dict) and 'path' in state:
        ref_path = state['path']
    elif isinstance(state, str):
        ref_path = state
    else:
        return schema, state, []

    sim_data = get_sim_data()
    if sim_data is None:
        raise RuntimeError(
            f"SimDataRef at {path}: sim_data not loaded. "
            f"Call set_sim_data() before realize.")

    value = _resolve_dotted_path(sim_data, ref_path)
    return schema, value, []


@dataclass(kw_only=True)
class SimDataMethod(Node):
    """Reference to a callable method on sim_data, resolved at realize time.

    Similar to SimDataRef but the resolved value is expected to be a
    callable (bound method or function). No type parameter needed —
    the resolved value is always callable.
    """
    pass


@realize.dispatch
def realize(core, schema: SimDataMethod, state, path=()):
    if isinstance(state, dict) and 'path' in state:
        ref_path = state['path']
    elif isinstance(state, str):
        ref_path = state
    else:
        return schema, state, []

    sim_data = get_sim_data()
    if sim_data is None:
        raise RuntimeError(
            f"SimDataMethod at {path}: sim_data not loaded.")

    value = _resolve_dotted_path(sim_data, ref_path)
    if not callable(value):
        raise TypeError(
            f"SimDataMethod at {path}: resolved value is "
            f"{type(value).__name__}, not callable")
    return schema, value, []


# ============================================================================
# SharedProcess — shared process instances referenced by ID
# ============================================================================

# Global registry mapping process_id → instantiated PartitionedProcess.
# Populated by realize(SharedProcess), read by realize(SharedProcessRef).
_shared_processes: dict[str, object] = {}


def clear_shared_processes():
    """Clear the shared process registry (e.g. between simulations)."""
    _shared_processes.clear()


@dataclass(kw_only=True)
class SharedProcess(Node):
    """A shared process instance declared in the document's ``process`` store.

    Document form::

        {"_type": "shared_process",
         "address": "local:!ecoli.processes.equilibrium.Equilibrium",
         "config": { ... }}

    On ``realize()``, the class is imported from ``address``, instantiated
    with ``config``, wrapped as ``(instance,)`` (the tuple format that
    Requester/Evolver ``update()`` expects), and registered in the global
    ``_shared_processes`` dict keyed by the last path segment (the
    process name).
    """
    pass


@realize.dispatch
def realize(core, schema: SharedProcess, state, path=()):
    # Already realized — (instance,) tuple or bare instance
    if isinstance(state, tuple) and len(state) > 0:
        return schema, state, []
    if not isinstance(state, dict):
        return schema, state, []

    address = state.get('address')
    config = state.get('config', {})
    process_id = path[-1] if path else state.get('process_id')

    if address is None:
        return schema, state, []

    # Import the class from address (same protocol as realize_link)
    if isinstance(address, str):
        if address.startswith('local:!'):
            module_path, class_name = address[7:].rsplit('.', 1)
            mod = importlib.import_module(module_path)
            cls = getattr(mod, class_name)
        else:
            return schema, state, []
    else:
        return schema, state, []

    instance = cls(config)

    # Set core so serialize can access config_schema
    if not hasattr(instance, 'core') or instance.core is None:
        instance.core = core

    # Register for lookup by SharedProcessRef
    if process_id is not None:
        _shared_processes[process_id] = instance

    # Store the address on the instance so serialize can recover it
    instance._shared_address = address if isinstance(address, str) else f"local:!{cls.__module__}.{cls.__name__}"

    return schema, (instance,), []


@dispatch
def serialize(schema: SharedProcess, state):
    """Serialize a SharedProcess back to its declaration dict."""
    if isinstance(state, tuple) and len(state) > 0:
        instance = state[0]
        address = getattr(instance, '_shared_address', _class_address_from_instance(instance))
        # Serialize config through the instance's config_schema
        config = instance.parameters if hasattr(instance, 'parameters') else {}
        instance_core = getattr(instance, 'core', None)
        raw_schema = getattr(instance, 'config_schema', None)
        if instance_core and raw_schema:
            config_schema = instance_core.access(raw_schema)
            config = serialize(config_schema, config)
        return {
            '_type': 'shared_process',
            'address': address,
            'config': config,
        }
    if isinstance(state, dict):
        return state
    return state


@dispatch
def render(schema: SharedProcess, defaults=False):
    return 'shared_process'


def _class_address_from_instance(instance):
    """Generate a local:! address from an instance's class."""
    cls = type(instance)
    return f'local:!{cls.__module__}.{cls.__name__}'


@dataclass(kw_only=True)
class SharedProcessRef(Node):
    """Reference to a shared process instance by ID.

    Used in Requester/Evolver ``config_schema`` so that ``realize()``
    resolves the reference to the actual ``PartitionedProcess`` instance
    before ``__init__`` is called.

    Document form (in config)::

        {"process": {"_type": "shared_process_ref",
                      "process_id": "ecoli-equilibrium"}}

    Or as a bare string when the config_schema declares the type::

        {"process": "ecoli-equilibrium"}
    """
    pass


@dispatch
def serialize(schema: SharedProcessRef, state):
    """Serialize a SharedProcessRef: extract the process name from the instance."""
    if isinstance(state, str):
        return state
    if isinstance(state, dict):
        return state
    # It's a PartitionedProcess instance — return its name
    if hasattr(state, 'name'):
        return state.name
    return str(state)


@dispatch
def bundle(schema: SharedProcessRef, state, context: typing.Optional[BundleContext] = None):
    """Bundle a SharedProcessRef: same as serialize — just the process name."""
    return serialize(schema, state)


@realize.dispatch
def realize(core, schema: SharedProcessRef, state, path=()):
    # Already resolved to an instance
    if not isinstance(state, (str, dict)):
        return schema, state, []

    if isinstance(state, dict):
        process_id = state.get('process_id')
    else:
        process_id = state

    if process_id is None:
        return schema, state, []

    instance = _shared_processes.get(process_id)
    if instance is None:
        raise RuntimeError(
            f"SharedProcessRef at {path}: process_id '{process_id}' "
            f"not found. Available: {sorted(_shared_processes.keys())}. "
            f"Ensure SharedProcess entries in the 'process' store are "
            f"realized before step declarations.")
    return schema, instance, []


# ============================================================================
# Method — pointer to a method on a sim_data_objects instance
# ============================================================================

# Global registry of sim_data object instances, populated during realize
# of the sim_data_objects store.
_sim_data_object_instances: typing.Dict[str, typing.Any] = {}


@dataclass(kw_only=True)
class SimDataObjectStore(Node):
    """Store for sim_data object instances used by bound method references.

    On realize, each entry is realized as an Object type (reconstructing
    the Python instance from serialized __dict__), then registered in
    ``_sim_data_object_instances`` so Method can find them.
    """
    pass


@dispatch
def render(schema: SimDataObjectStore, defaults=False):
    return 'sim_data_object_store'


# ============================================================================
# SympyMatrix type — language-agnostic serialization of sympy matrices
# ============================================================================

@dataclass(kw_only=True)
class SympyMatrix(Node):
    """A sympy Matrix serialized as element-wise srepr strings.

    Serialized form::

        {"rows": 39, "cols": 1,
         "elements": ["Add(Mul(Indexed(...), ...), ...)", ...]}

    On realize, reconstructs the sympy Matrix from the srepr strings.
    """
    pass


@dispatch
def render(schema: SympyMatrix, defaults=False):
    return 'sympy_matrix'


@dispatch
def serialize(schema: SympyMatrix, state):
    if state is None:
        return None
    if isinstance(state, dict):
        return state
    import sympy as sp
    rows, cols = state.shape
    elements = [sp.srepr(state[i, j]) for i in range(rows) for j in range(cols)]
    return {'rows': rows, 'cols': cols, 'elements': elements}


@dispatch
def bundle(schema: SympyMatrix, state, context: typing.Optional[BundleContext] = None):
    return serialize(schema, state)


@realize.dispatch
def realize(core, schema: SympyMatrix, state, path=()):
    if state is None:
        return schema, None, []
    if not isinstance(state, dict):
        return schema, state, []
    import sympy
    ns = vars(sympy)
    rows = state['rows']
    cols = state['cols']
    elements = [eval(e, {'__builtins__': {}}, ns) for e in state['elements']]
    matrix = sympy.Matrix(rows, cols, elements)
    return schema, matrix, []


import sympy as _sympy

@dispatch
def infer(core, value: _sympy.MatrixBase, path: tuple = ()):
    """Infer a sympy Matrix as a SympyMatrix type."""
    return set_default(SympyMatrix(), value), []


# ============================================================================
# DerivedFunction — a function derived from a sibling field
# ============================================================================

@dataclass(kw_only=True)
class DerivedFunction(Node):
    """A function that is derived from another field via a builder function.

    Serialized form::

        {"source_field": "symbolic_rates",
         "builder": "wholecell.utils.build_ode.rates",
         "index": 0}

    On realize (handled by Object realize), the builder is imported,
    called with the realized source field value, and the result (or
    element at index) replaces this placeholder.
    """
    _schema_keys = Node._schema_keys | frozenset({'_source_field', '_builder', '_index'})
    _source_field: str = ''
    _builder: str = ''
    _index: int = -1  # -1 means use full result, >=0 means index into tuple


@dispatch
def render(schema: DerivedFunction, defaults=False):
    return 'derived_function'


@dispatch
def serialize(schema: DerivedFunction, state):
    """Serialize: store the derivation recipe, not the function."""
    if isinstance(state, dict):
        return state
    return {
        'source_field': schema._source_field,
        'builder': schema._builder,
        'index': schema._index,
    }


@dispatch
def bundle(schema: DerivedFunction, state, context: typing.Optional[BundleContext] = None):
    return serialize(schema, state)


@realize.dispatch
def realize(core, schema: DerivedFunction, state, path=()):
    """Realize returns the recipe dict — Object realize handles the actual rebuild."""
    if callable(state):
        return schema, state, []
    return schema, state, []


@dispatch
def serialize(schema: SimDataObjectStore, state):
    """Serialize the store — each value as an Object."""
    if not isinstance(state, dict):
        return state
    from bigraph_schema.schema import Object
    result = {}
    for key, instance in state.items():
        if isinstance(key, str) and key.startswith('_'):
            continue
        result[key] = serialize(Object(), instance)
    return result


@dispatch
def bundle(schema: SimDataObjectStore, state, context: typing.Optional[BundleContext] = None):
    """Bundle the store — each value as an Object."""
    if not isinstance(state, dict):
        return state
    from bigraph_schema.schema import Object
    result = {}
    for key, instance in state.items():
        if isinstance(key, str) and key.startswith('_'):
            continue
        result[key] = bundle(Object(), instance, context)
    return result


@realize.dispatch
def realize(core, schema: SimDataObjectStore, state, path=()):
    """Realize each entry as an Object and register in the global registry."""
    if not isinstance(state, dict):
        return schema, state, []
    from bigraph_schema.schema import Object
    result = {}
    _sim_data_object_instances.clear()
    for key, encoded in state.items():
        _, instance = core.realize(Object(), encoded)
        result[key] = instance
        _sim_data_object_instances[key] = instance
    return schema, result, []


@dataclass(kw_only=True)
@dataclass(kw_only=True)
class SimDataObjectRef(Node):
    """Reference to a sim_data object instance by store key.

    Document form::

        {"_type": "sim_data_object_ref",
         "store_key": "external_state"}

    On realize(), looks up the instance in ``_sim_data_object_instances``.
    """
    pass


@dispatch
def render(schema: SimDataObjectRef, defaults=False):
    return 'sim_data_object_ref'


@dispatch
def serialize(schema: SimDataObjectRef, state):
    if isinstance(state, dict):
        return state
    # Find the instance in the registry
    inst_id = id(state)
    for key, inst in _sim_data_object_instances.items():
        if id(inst) == inst_id:
            return {'store_key': key}
    return state


@dispatch
def bundle(schema: SimDataObjectRef, state, context: typing.Optional[BundleContext] = None):
    return serialize(schema, state)


@realize.dispatch
def realize(core, schema: SimDataObjectRef, state, path=()):
    if isinstance(state, dict):
        store_key = state.get('store_key')
        if store_key:
            instance = _sim_data_object_instances.get(store_key)
            if instance is None:
                raise RuntimeError(
                    f"SimDataObjectRef at {path}: store_key '{store_key}' "
                    f"not found. Available: {sorted(_sim_data_object_instances.keys())}")
            return schema, instance, []
    # Already an instance
    return schema, state, []


class Method(Node):
    """Reference to a bound method on a sim_data_objects instance.

    Document form::

        {"_type": "method",
         "instance_path": ["sim_data_objects", "transcription"],
         "attribute": "make_elongation_rates"}

    On realize(), looks up the instance in ``_sim_data_object_instances``
    and returns ``getattr(instance, attribute)`` — a bound method.
    """
    pass


@dispatch
def serialize(schema: Method, state):
    """Serialize: if it's a bound method, extract path + attribute."""
    if isinstance(state, dict):
        return state
    if callable(state) and hasattr(state, '__self__'):
        # Find the instance in the registry
        inst_id = id(state.__self__)
        for key, inst in _sim_data_object_instances.items():
            if id(inst) == inst_id:
                return {
                    'instance_path': ['sim_data_objects', key],
                    'attribute': state.__func__.__name__,
                }
        # Not found in registry — fall back to Function serialize
        return serialize(Function(), state)
    if callable(state):
        return serialize(Function(), state)
    return state


@dispatch
def bundle(schema: Method, state, context: typing.Optional[BundleContext] = None):
    return serialize(schema, state)


@dispatch
def render(schema: Method, defaults=False):
    return 'method'


@realize.dispatch
def realize(core, schema: Method, state, path=()):
    """Resolve a bound method ref by looking up the instance and binding."""
    if callable(state):
        return schema, state, []
    if isinstance(state, dict):
        instance_path = state.get('instance_path', [])
        attribute = state.get('attribute')
        if len(instance_path) >= 2 and attribute:
            store_key = instance_path[1]
            instance = _sim_data_object_instances.get(store_key)
            if instance is None:
                raise RuntimeError(
                    f"Method at {path}: instance '{store_key}' "
                    f"not found in sim_data_objects. "
                    f"Available: {sorted(_sim_data_object_instances.keys())}")
            method = getattr(instance, attribute)
            return schema, method, []
    return schema, state, []


# ============================================================================
# Type registry
# ============================================================================

ECOLI_TYPES = {
    'unum': UnumUnits,
    'quantity': Quantity,
    'csr_matrix': CSRMatrix,
    'units_array': UnitsArray,
    'function': Function,
    'method': Method,
    'step_instance': StepInstance,
    'process_instance': ProcessInstance,
    'step': StepLink,
    'process': ProcessLink,
    'bulk_array': BulkArray,
    'unique_array': UniqueArray,
    'sim_data_ref': SimDataRef,
    'sim_data_method': SimDataMethod,
    'shared_process': SharedProcess,
    'shared_process_ref': SharedProcessRef,
    'sympy_matrix': SympyMatrix,
    'derived_function': DerivedFunction,
    'sim_data_object_store': SimDataObjectStore,
    'sim_data_object_ref': SimDataObjectRef,
}


# ---------------------------------------------------------------------------
# Bundle overrides — write large data directly to Parquet
# ---------------------------------------------------------------------------

@dispatch
def bundle(schema: BulkArray, state, context: typing.Optional[BundleContext] = None):
    """Bundle a BulkArray: write the structured array directly to Parquet."""
    if not isinstance(state, np.ndarray) or not state.dtype.names:
        return state
    if context is not None and state.nbytes >= context.min_bytes:
        return context.save_array(state, 'bulk')
    return _serialize_structured_array(state)


@dispatch
def bundle(schema: UniqueArray, state, context: typing.Optional[BundleContext] = None):
    """Bundle a UniqueArray: write the structured array directly to Parquet."""
    if not isinstance(state, np.ndarray):
        # MetadataArray wraps ndarray
        if hasattr(state, 'base') and isinstance(
                getattr(state, 'base', None), np.ndarray):
            state = np.asarray(state)
        elif hasattr(state, '__array__'):
            state = np.asarray(state)
        else:
            return state
    if not state.dtype.names:
        return state
    if context is not None and state.nbytes >= context.min_bytes:
        return context.save_array(state, 'unique')
    return _serialize_structured_array(state)


@dispatch
def bundle(schema: CSRMatrix, state, context: typing.Optional[BundleContext] = None):
    """Bundle a CSRMatrix: write the sparse matrix data to Parquet."""
    if isinstance(state, dict):
        return state
    if state is None:
        return None
    # Dense ndarray with CSRMatrix schema — save directly as array
    if isinstance(state, np.ndarray):
        if context is not None and state.nbytes >= context.min_bytes:
            return context.save_array(state, 'csr_matrix')
        return state.tolist()
    if isinstance(state, csr_matrix):
        # Convert CSR components to a single structured array for Parquet
        total_bytes = state.data.nbytes + state.indices.nbytes + state.indptr.nbytes
        if context is not None and total_bytes >= context.min_bytes:
            # Store shape info in the marker and save data/indices/pointers
            # as separate arrays in one combined array
            combined = np.zeros(len(state.data) + len(state.indices) + len(state.indptr),
                                dtype=np.int64)
            d_end = len(state.data)
            i_end = d_end + len(state.indices)
            combined[:d_end] = state.data
            combined[d_end:i_end] = state.indices
            combined[i_end:] = state.indptr
            marker = context.save_array(combined, 'csr_matrix')
            marker['csr'] = True
            marker['data_len'] = d_end
            marker['indices_len'] = len(state.indices)
            marker['matrix_shape'] = list(state.shape)
            return marker
        return {
            'data': np.asarray(state.data).tolist(),
            'indices': np.asarray(state.indices).tolist(),
            'pointers': np.asarray(state.indptr).tolist()}
    return serialize(schema, state)


@dispatch
def bundle(schema: UnumUnits, state, context: typing.Optional[BundleContext] = None):
    """Bundle a Unum — same as serialize (scalars stay inline)."""
    return serialize(schema, state)


@dispatch
def bundle(schema: Quantity, state, context: typing.Optional[BundleContext] = None):
    """Bundle a Quantity — same as serialize (scalars stay inline)."""
    return serialize(schema, state)


@dispatch
def bundle(schema: UnitsArray, state, context: typing.Optional[BundleContext] = None):
    """Bundle a UnitStructArray — extract the underlying structured array."""
    if isinstance(state, UnitStructArray):
        inner = state.struct_array
        if context is not None and inner.nbytes >= context.min_bytes:
            marker = context.save_array(inner, 'units_array')
            marker['units'] = str(state.units) if hasattr(state, 'units') else None
            return marker
        return serialize(schema, state)
    return serialize(schema, state)


@dispatch
def bundle(schema: Function, state, context: typing.Optional[BundleContext] = None):
    """Bundle a Function — same as serialize (small metadata dict)."""
    return serialize(schema, state)


@dispatch
def bundle(schema: SharedProcess, state, context: typing.Optional[BundleContext] = None):
    """Bundle a SharedProcess — recurse with bundle context."""
    if state is None:
        return None
    if isinstance(state, dict):
        result = {}
        for key in schema.__dataclass_fields__:
            if is_schema_field(schema, key) and key in state:
                result[key] = bundle(
                    getattr(schema, key), state[key], context)
        return result
    return str(state)
