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
    Node, String, Float, Integer, Array, List, Tuple, Link, Overwrite, Wrap, Protocol,
)
from bigraph_schema.methods import (
    infer, set_default, default, serialize, realize, render,
    wrap_default, resolve, reify_schema, validate, merge_update,
    apply, reconcile, divide,
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
        return schema._default if schema._default else default(schema)
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
            # Fallback for other address formats
            return schema, state, []
    else:
        return schema, state, []

    instance = cls(config)

    # Register for lookup by SharedProcessRef
    if process_id is not None:
        _shared_processes[process_id] = instance

    return schema, (instance,), []


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
    'sim_data_ref': SimDataRef,
    'sim_data_method': SimDataMethod,
    'shared_process': SharedProcess,
    'shared_process_ref': SharedProcessRef,
}
