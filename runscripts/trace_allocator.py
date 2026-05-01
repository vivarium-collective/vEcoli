"""Trace Allocator and non-partitioned Step outputs in v1 and v2.

Records: Allocator.update first-call inputs (bulk sum, request per process)
and outputs (allocate per process). Also traces tf_binding / tf_unbinding /
chromosome_structure if present.

Run from vEcoli root:
    python runscripts/trace_allocator.py > /tmp/trace_alloc.log 2>&1
"""
import sys, os, subprocess, tempfile, pickle
import numpy as np
from contextlib import chdir

ROOT_PATH = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _bulk_count_sum(arr):
    try:
        if arr.dtype.names and 'count' in arr.dtype.names:
            return float(arr['count'].sum())
        if np.issubdtype(arr.dtype, np.number):
            return float(arr.sum())
    except Exception:
        pass
    return None


def summarize(v):
    if isinstance(v, np.ndarray):
        return ('ndarray', tuple(v.shape), str(v.dtype), _bulk_count_sum(v))
    if isinstance(v, dict):
        # Summarize per-key for request/allocate dicts
        return {k: summarize(val) for k, val in v.items()}
    if isinstance(v, (list, tuple)):
        if len(v) == 0:
            return (type(v).__name__, 0)
        if all(isinstance(x, (int, float, np.integer, np.floating)) for x in v):
            return (type(v).__name__, len(v), float(sum(v)))
        return (type(v).__name__, len(v))
    if isinstance(v, (int, float, np.integer, np.floating)):
        return ('scalar', float(v))
    return (type(v).__name__, None)


def install_patches():
    import importlib, pkgutil
    import ecoli.processes as ep_pkg
    for _, mod_name, _ in pkgutil.walk_packages(ep_pkg.__path__, prefix=ep_pkg.__name__ + '.'):
        try: importlib.import_module(mod_name)
        except Exception: pass

    from ecoli.processes.allocator import Allocator
    calls = []

    orig = Allocator.update
    def traced(self, states, interval=None):
        # Record inputs
        entry = {
            'process': getattr(self, 'name', type(self).__name__),
            'bulk_sum': _bulk_count_sum(np.asarray(states['bulk'])) if 'bulk' in states else None,
            'request': summarize(states.get('request', {})),
            'instance_id': id(self),
        }
        result = orig(self, states, interval)
        entry['allocate_out'] = summarize(result.get('allocate', {})) if isinstance(result, dict) else None
        entry['request_out'] = summarize(result.get('request', {})) if isinstance(result, dict) else None
        calls.append(entry)
        return result
    Allocator.update = traced

    # Also trace tf_binding / tf_unbinding / chromosome_structure
    from ecoli.library.bigraph_bridge import BigraphStep
    extra_names = ('tf-binding', 'tf-unbinding', 'chromosome-structure')
    import ecoli.processes.tf_binding as tfb_mod
    import ecoli.processes.tf_unbinding as tfu_mod
    import ecoli.processes.chromosome_structure as cs_mod
    for mod in (tfb_mod, tfu_mod, cs_mod):
        for attr_name in dir(mod):
            cls = getattr(mod, attr_name)
            if isinstance(cls, type) and issubclass(cls, BigraphStep) and cls is not BigraphStep:
                if hasattr(cls, 'update') and 'update' in cls.__dict__:
                    orig_m = cls.__dict__['update']
                    def make(orig_m=orig_m):
                        def tr(self, states, interval=None):
                            bs = None
                            try:
                                bs = _bulk_count_sum(np.asarray(states['bulk'])) if 'bulk' in states else None
                            except Exception:
                                pass
                            res = orig_m(self, states, interval)
                            bulk_delta = None
                            if isinstance(res, dict) and 'bulk' in res:
                                try:
                                    bulk_delta = summarize(res['bulk'])
                                except Exception:
                                    pass
                            calls.append({
                                'process': getattr(self, 'name', type(self).__name__),
                                'bulk_in_sum': bs,
                                'bulk_out': bulk_delta,
                            })
                            return res
                        return tr
                    cls.update = make()
    return calls


def run_v1():
    calls = install_patches()
    from ecoli.experiments.ecoli_master_sim import EcoliSim
    sim = EcoliSim.from_file()
    sim.max_duration = 1
    sim.emitter = 'null'
    sim.divide = False
    sim.build_ecoli()
    sim.run()
    return calls


def run_v2():
    calls = install_patches()
    from ecoli.experiments.ecoli_master_sim import EcoliSim
    from ecoli.composites.ecoli_composite import build_composite_native
    from ecoli.library.bigraph_types import ECOLI_TYPES
    from process_bigraph import Composite
    from bigraph_schema import allocate_core

    sim = EcoliSim.from_file()
    sim.max_duration = 1
    sim.emitter = 'null'
    sim.divide = False
    sim.processes = sim._retrieve_processes(
        sim.processes, sim.add_processes, sim.exclude_processes, sim.swap_processes)
    sim.topology = sim._retrieve_topology(
        sim.topology, sim.processes, sim.swap_processes, sim.log_updates)
    sim.process_configs = sim._retrieve_process_configs(
        sim.process_configs, sim.processes)

    core = allocate_core()
    core.register_types(ECOLI_TYPES)
    state = build_composite_native(core, sim.config)
    composite = Composite({'schema': {}, 'state': state}, core=core)
    composite.run(1.0)
    return calls


if __name__ == '__main__':
    with chdir(ROOT_PATH):
        label = sys.argv[1] if len(sys.argv) > 1 else 'v2'
        if label == 'v1':
            calls = run_v1()
            out = '/tmp/alloc_v1.pkl'
        else:
            calls = run_v2()
            out = '/tmp/alloc_v2.pkl'
        with open(out, 'wb') as f:
            pickle.dump(calls, f)
        print(f'{label}: {len(calls)} calls saved to {out}')
