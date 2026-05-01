"""Diagnose save/load fidelity for a composite bundle.

Given a bundle directory, loads it into sim_a, snapshots sim_a's live
state *including every process instance's ``__dict__``*, resaves the
bundle, loads the fresh copy into sim_b, snapshots sim_b, then reports
any field that was dropped, changed, or renamed between the two
snapshots.

This catches fidelity losses the pure bundle-file roundtrip misses,
because it walks the Python ``__dict__`` of each process (including
attributes ``capture_object_state`` intentionally skips — solver
objects, lazy caches, etc.).

Usage:
    python runscripts/diff_bundle_roundtrip.py <bundle_dir> \
        [--resave-dir <dir>] [--max-report 50]
"""
import argparse
import os
import sys
import tempfile

import numpy as np


def load_composite(bundle_dir):
    from ecoli.library.bigraph_types import ECOLI_TYPES
    from process_bigraph import Composite
    from bigraph_schema import allocate_core

    core = allocate_core()
    core.register_types(ECOLI_TYPES)
    return Composite.load_bundle(bundle_dir, core=core), core


def _type_tag(value):
    if isinstance(value, np.ndarray):
        return f"ndarray{list(value.shape)}[{value.dtype}]"
    if isinstance(value, tuple):
        return f"tuple[{len(value)}]"
    if isinstance(value, list):
        return f"list[{len(value)}]"
    return type(value).__name__


def _is_process_instance(v):
    """Heuristic: a process/step instance has a class module under
    ``ecoli.processes`` or is a known framework Process/Step subclass."""
    if v is None or isinstance(v, (dict, list, tuple, np.ndarray, str, int, float, bool, bytes)):
        return False
    cls = type(v)
    mod = getattr(cls, '__module__', '') or ''
    return (mod.startswith('ecoli.processes')
            or mod.startswith('ecoli.library.bigraph_bridge')
            or mod.startswith('ecoli.composites')
            or mod.endswith('.process')
            or mod.endswith('.step')
            or mod.startswith('process_bigraph'))


def walk_state(obj, path=()):
    """Yield (path, value) for every node in the state tree. Unwraps
    tuples of ``(instance,)`` that SharedProcess.realize returns."""
    if isinstance(obj, dict):
        yield path, obj
        for k, v in obj.items():
            yield from walk_state(v, path + (k,))
    elif isinstance(obj, (list, tuple)):
        yield path, obj
        for i, v in enumerate(obj):
            yield from walk_state(v, path + (i,))
    else:
        yield path, obj


def snapshot_instance(inst):
    """Capture an instance's __dict__ plus class identity. Nested
    structures (arrays, dicts, primitives) are kept as-is; other
    Python objects are tagged by type for later equality checks."""
    cls = type(inst)
    snap = {
        '__class__': f'{cls.__module__}.{cls.__name__}',
        '__dict__': dict(getattr(inst, '__dict__', {})),
        '__slots__': {},
    }
    for slot in getattr(inst, '__slots__', ()) or ():
        try:
            snap['__slots__'][slot] = getattr(inst, slot)
        except AttributeError:
            pass
    return snap


def deep_snapshot(composite):
    """Return a dict keyed by state path -> value (for data) or
    instance-snapshot (for process instances).

    Non-instance non-container values land in ``entries[path]`` as-is.
    Process instances land in ``instances[path]`` as ``__dict__`` dumps.
    """
    entries = {}
    instances = {}
    for path, value in walk_state(composite.state):
        if _is_process_instance(value):
            instances[path] = snapshot_instance(value)
        elif isinstance(value, np.ndarray):
            entries[path] = value
        elif isinstance(value, (dict, list, tuple)):
            # container — children walked separately; skip here
            pass
        else:
            entries[path] = value
    return {'entries': entries, 'instances': instances}


def _is_sparse(x):
    try:
        from scipy.sparse import issparse
        return issparse(x)
    except Exception:
        return False


def _randomstate_equal(a, b):
    try:
        sa = a.get_state()
        sb = b.get_state()
    except Exception:
        return False
    if len(sa) != len(sb):
        return False
    for x, y in zip(sa, sb):
        if isinstance(x, np.ndarray):
            if not np.array_equal(x, y):
                return False
        elif x != y:
            return False
    return True


def _safe_eq(a, b):
    """Best-effort equality for arbitrary Python values. Never raises."""
    try:
        r = (a == b)
    except Exception:
        return a is b
    if isinstance(r, np.ndarray):
        try:
            return bool(r.all())
        except Exception:
            return False
    try:
        return bool(r)
    except Exception:
        return a is b


def _objects_deep_equal(a, b, depth=0, max_depth=6):
    """Compare two opaque Python objects by walking their ``__dict__``.
    Falls back to ``==`` at max depth. Classes with no ``__dict__``
    (e.g. ctypes handles) compare by ``==`` directly."""
    if depth > max_depth:
        return a is b
    if type(a) is not type(b):
        return False
    if hasattr(a, 'get_state') and type(a).__name__ == 'RandomState':
        return _randomstate_equal(a, b)
    da = getattr(a, '__dict__', None)
    db = getattr(b, '__dict__', None)
    if da is None or db is None:
        return _safe_eq(a, b)
    if set(da) != set(db):
        return False
    for k in da:
        va = da[k]
        vb = db[k]
        if not _values_equal(va, vb, _depth=depth + 1):
            return False
    return True


def _values_equal(a, b, _depth=0):
    if a is None and b is None:
        return True
    if a is b:
        return True
    # scipy sparse matrices need dense conversion for comparison
    if _is_sparse(a) or _is_sparse(b):
        if not (_is_sparse(a) and _is_sparse(b)):
            return False
        if a.shape != b.shape or a.dtype != b.dtype:
            return False
        return (a != b).nnz == 0
    if isinstance(a, np.ndarray) or isinstance(b, np.ndarray):
        if not (isinstance(a, np.ndarray) and isinstance(b, np.ndarray)):
            return False
        if a.shape != b.shape or a.dtype != b.dtype:
            return False
        if a.dtype.names:
            return all(_values_equal(a[n], b[n]) for n in a.dtype.names)
        if np.issubdtype(a.dtype, np.floating):
            return np.allclose(a, b, equal_nan=True)
        return np.array_equal(a, b)
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        if len(a) != len(b):
            return False
        return all(_values_equal(x, y, _depth) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        if set(a) != set(b):
            return False
        return all(_values_equal(a[k], b[k], _depth) for k in a)
    if isinstance(a, float) and isinstance(b, float):
        return a == b or (a != a and b != b)
    if isinstance(a, (int, float, bool, str, bytes, complex)) and isinstance(
            b, (int, float, bool, str, bytes, complex)):
        try:
            return a == b
        except Exception:
            return False
    # Opaque Python objects: walk __dict__ recursively (depth-limited)
    return _objects_deep_equal(a, b, depth=_depth)


def diff_instance_dicts(a, b, path, diffs):
    """Compare two instance snapshots (__dict__ + __slots__ + __class__)."""
    if a['__class__'] != b['__class__']:
        diffs.append(('class_change', path, a['__class__'], b['__class__']))
        return
    for side_key in ('__dict__', '__slots__'):
        only_a = set(a[side_key]) - set(b[side_key])
        only_b = set(b[side_key]) - set(a[side_key])
        for attr in sorted(only_a):
            diffs.append(('attr_only_in_a', f'{path}.{side_key}.{attr}',
                          _type_tag(a[side_key][attr]), None))
        for attr in sorted(only_b):
            diffs.append(('attr_only_in_b', f'{path}.{side_key}.{attr}',
                          None, _type_tag(b[side_key][attr])))
        for attr in sorted(set(a[side_key]) & set(b[side_key])):
            va, vb = a[side_key][attr], b[side_key][attr]
            if not _values_equal(va, vb):
                diffs.append(('attr_value_diff',
                              f'{path}.{side_key}.{attr}',
                              _type_tag(va), _type_tag(vb)))


def diff_snapshots(a, b):
    diffs = []
    # Entries (state-dict leaves)
    only_a = set(a['entries']) - set(b['entries'])
    only_b = set(b['entries']) - set(a['entries'])
    for p in sorted(only_a):
        diffs.append(('entry_only_in_a', '/'.join(map(str, p)),
                      _type_tag(a['entries'][p]), None))
    for p in sorted(only_b):
        diffs.append(('entry_only_in_b', '/'.join(map(str, p)),
                      None, _type_tag(b['entries'][p])))
    for p in sorted(set(a['entries']) & set(b['entries'])):
        va, vb = a['entries'][p], b['entries'][p]
        if not _values_equal(va, vb):
            diffs.append(('entry_value_diff', '/'.join(map(str, p)),
                          _type_tag(va), _type_tag(vb)))
    # Instances
    only_a_inst = set(a['instances']) - set(b['instances'])
    only_b_inst = set(b['instances']) - set(a['instances'])
    for p in sorted(only_a_inst):
        diffs.append(('instance_only_in_a', '/'.join(map(str, p)),
                      a['instances'][p]['__class__'], None))
    for p in sorted(only_b_inst):
        diffs.append(('instance_only_in_b', '/'.join(map(str, p)),
                      None, b['instances'][p]['__class__']))
    for p in sorted(set(a['instances']) & set(b['instances'])):
        diff_instance_dicts(a['instances'][p], b['instances'][p],
                            '/'.join(map(str, p)), diffs)
    return diffs


def main():
    p = argparse.ArgumentParser()
    p.add_argument('bundle_dir')
    p.add_argument('--resave-dir', default=None)
    p.add_argument('--max-report', type=int, default=50)
    args = p.parse_args()

    if not os.path.isdir(args.bundle_dir):
        print(f'ERROR: {args.bundle_dir} not a dir', file=sys.stderr)
        sys.exit(1)

    print(f'sim_a: loading {args.bundle_dir}', flush=True)
    sim_a, _ = load_composite(args.bundle_dir)
    print(f'  top-level state keys: {list(sim_a.state.keys())[:10]}', flush=True)

    print('sim_a: snapshotting pre-save state...', flush=True)
    snap_pre = deep_snapshot(sim_a)
    print(f'  entries: {len(snap_pre["entries"])}, '
          f'instances: {len(snap_pre["instances"])}', flush=True)

    resave_dir = args.resave_dir or tempfile.mkdtemp(prefix='bundle_diag_')
    print(f'sim_a: save_bundle -> {resave_dir}', flush=True)
    sim_a.save_bundle(resave_dir)

    print(f'sim_b: load_bundle({resave_dir})', flush=True)
    sim_b, _ = load_composite(resave_dir)

    print('sim_b: snapshotting post-load state...', flush=True)
    snap_post = deep_snapshot(sim_b)
    print(f'  entries: {len(snap_post["entries"])}, '
          f'instances: {len(snap_post["instances"])}', flush=True)

    print('Diffing...', flush=True)
    diffs = diff_snapshots(snap_pre, snap_post)

    by_kind = {}
    for d in diffs:
        by_kind.setdefault(d[0], []).append(d[1:])

    print(f'\n=== Total diffs: {len(diffs)} ===')
    for kind in sorted(by_kind):
        items = by_kind[kind]
        print(f'\n-- {kind} ({len(items)}) --')
        for entry in items[:args.max_report]:
            print('  ' + ' | '.join(str(x) for x in entry))
        if len(items) > args.max_report:
            print(f'  ... {len(items) - args.max_report} more')


if __name__ == '__main__':
    main()
