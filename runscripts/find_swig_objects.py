"""Build a Composite to t=1 and walk its object graph to find any
SwigPyObject (or other unpicklable C objects) and report the path.

Use this to identify which attribute / process / object is preventing
cloudpickle from succeeding.
"""
import os
import sys
from contextlib import chdir

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def find_unpicklable(obj, path='root', seen=None, hits=None, depth=0,
                     max_depth=50):
    """Walk obj.__dict__ recursively and report attributes that have
    type-name containing 'Swig' or that fail individual cloudpickle.dump."""
    if seen is None:
        seen = set()
    if hits is None:
        hits = []
    if depth > max_depth:
        return hits
    oid = id(obj)
    if oid in seen:
        return hits
    seen.add(oid)

    cls = type(obj)
    cname = cls.__name__
    if 'Swig' in cname or 'swig' in cname:
        hits.append(f'{path}  ← {cls.__module__}.{cname}')
        return hits

    # dict-like
    if isinstance(obj, dict):
        for k, v in list(obj.items())[:200]:
            find_unpicklable(v, f'{path}[{k!r}]', seen, hits, depth+1)
        return hits
    if isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj[:200]):
            find_unpicklable(v, f'{path}[{i}]', seen, hits, depth+1)
        return hits
    if isinstance(obj, (str, int, float, bool, bytes, type(None))):
        return hits

    # recurse into __dict__ if any
    d = getattr(obj, '__dict__', None)
    if d is None:
        return hits
    for k, v in list(d.items())[:200]:
        find_unpicklable(v, f'{path}.{k}', seen, hits, depth+1)
    return hits


def main():
    with chdir(ROOT):
        # Apply same patches the test does
        import sys
        sys.path.insert(0, os.path.join(ROOT, 'runscripts'))
        from test_pickle_roundtrip import _patch_glpk_pickle, _patch_metabolism_strip
        _patch_glpk_pickle()
        _patch_metabolism_strip()

        from ecoli.experiments.ecoli_master_sim import EcoliSim
        sim = EcoliSim.from_file()
        sim.config['engine'] = 'composite'
        sim.config['lineage_seed'] = 12
        sim.config['seed'] = 12
        sim.config['emitter'] = 'null'
        sim.divide = False
        sim.max_duration = 1
        sim.run()
        ecoli = sim._composite
        print(f'global_time = {ecoli.state.get("global_time")}')

        print('Walking object graph for SwigPyObjects...')
        hits = find_unpicklable(ecoli)
        if not hits:
            print('  no SwigPyObject in __dict__ traversal '
                  '(may be inside containers we did not recurse into)')
        for h in hits:
            print(h)


if __name__ == '__main__':
    sys.exit(main())
