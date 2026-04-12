"""Trace every PartitionedProcess.calculate_request / evolve_state call
in v1 and v2, capture first-call input summaries, and diff them.

Run from vEcoli root:
    python runscripts/trace_processes.py > /tmp/trace_processes.log 2>&1
"""
import sys, os, subprocess, tempfile, pickle
import numpy as np
from contextlib import chdir

ROOT_PATH = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _summarize(v):
    """Summarize a value for stable comparison."""
    if isinstance(v, np.ndarray):
        return ('ndarray', tuple(v.shape), str(v.dtype),
                float(v.sum()) if v.size and np.issubdtype(v.dtype, np.number) else None,
                int(v.size))
    if isinstance(v, (list, tuple)):
        if len(v) == 0:
            return (type(v).__name__, 0, None)
        if all(isinstance(x, (int, float, np.integer, np.floating)) for x in v):
            return (type(v).__name__, len(v), float(sum(v)))
        return (type(v).__name__, len(v), None)
    if isinstance(v, dict):
        return ('dict', sorted(v.keys()))
    if isinstance(v, (int, float, np.integer, np.floating)):
        return ('scalar', float(v))
    if isinstance(v, str):
        return ('str', v[:60])
    if isinstance(v, bool):
        return ('bool', v)
    return (type(v).__name__, None)


def summarize_states(states):
    """Recursively summarize a states dict to shallow depth 2."""
    out = {}
    for k, v in states.items():
        if isinstance(v, dict):
            out[k] = {k2: _summarize(v2) for k2, v2 in v.items()}
        else:
            out[k] = _summarize(v)
    return out


def install_patches(label):
    """Monkey-patch every concrete PartitionedProcess subclass (and
    Metabolism as a standalone Step) to capture first-call inputs on
    evolve_state / calculate_request / update."""
    # Import all process modules so __subclasses__ is populated.
    import importlib, pkgutil
    import ecoli.processes as ep_pkg
    for _, mod_name, ispkg in pkgutil.walk_packages(
            ep_pkg.__path__, prefix=ep_pkg.__name__ + '.'):
        try:
            importlib.import_module(mod_name)
        except Exception:
            pass

    from ecoli.processes.partition import PartitionedProcess
    from ecoli.processes.metabolism import Metabolism

    calls = {}

    def make_traced(orig, method_suffix):
        def traced(self, timestep, states):
            key = f"{self.name}.{method_suffix}"
            if key not in calls:
                gt = states.get('global_time')
                calls[key] = {
                    'global_time': float(gt) if isinstance(gt, (int, float, np.integer, np.floating)) else None,
                    'timestep': timestep,
                    'inputs': summarize_states(states),
                }
            return orig(self, timestep, states)
        return traced

    def patch_class(cls):
        if 'evolve_state' in cls.__dict__:
            cls.evolve_state = make_traced(cls.__dict__['evolve_state'], 'evolve_state')
        if 'calculate_request' in cls.__dict__:
            cls.calculate_request = make_traced(cls.__dict__['calculate_request'], 'calculate_request')

    def walk(cls):
        patch_class(cls)
        for sub in cls.__subclasses__():
            walk(sub)

    walk(PartitionedProcess)

    # Metabolism: patch _do_update (v1 calls update→_do_update; v2 also).
    orig_do = Metabolism._do_update
    def traced_meta(self, timestep, states):
        key = f"{self.name}._do_update"
        if key not in calls:
            gt = states.get('global_time')
            calls[key] = {
                'global_time': float(gt) if isinstance(gt, (int, float, np.integer, np.floating)) else None,
                'timestep': timestep,
                'inputs': summarize_states(states),
            }
        return orig_do(self, timestep, states)
    Metabolism._do_update = traced_meta

    return calls


def run_v1():
    calls = install_patches('v1')
    from ecoli.experiments.ecoli_master_sim import EcoliSim
    sim = EcoliSim.from_file()
    sim.max_duration = 1
    sim.emitter = 'null'
    sim.divide = False
    sim.build_ecoli()
    sim.run()
    return calls


def run_v2():
    calls = install_patches('v2')
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


def run_in_subproc(func_name, out_path):
    script = f"""
import pickle, sys
sys.path.insert(0, '.')
from runscripts.trace_processes import {func_name}
calls = {func_name}()
with open(sys.argv[1], 'wb') as f:
    pickle.dump(calls, f)
"""
    log_path = out_path + '.log'
    with open(log_path, 'w') as log:
        proc = subprocess.Popen(
            [sys.executable, '-u', '-c', script, out_path],
            stdout=log, stderr=subprocess.STDOUT)
        proc.wait(timeout=900)
    if proc.returncode != 0:
        with open(log_path) as f:
            tail = f.readlines()[-30:]
        raise RuntimeError(f"{func_name} failed rc={proc.returncode}:\n{''.join(tail)}")
    with open(out_path, 'rb') as f:
        return pickle.load(f)


def diff_calls(v1_calls, v2_calls):
    common = sorted(set(v1_calls) & set(v2_calls))
    v1_only = sorted(set(v1_calls) - set(v2_calls))
    v2_only = sorted(set(v2_calls) - set(v1_calls))

    print(f"Processes in v1 only: {v1_only}")
    print(f"Processes in v2 only: {v2_only}")
    print(f"Common: {len(common)} processes\n")

    for name in common:
        v1 = v1_calls[name]
        v2 = v2_calls[name]
        diffs = []
        if v1['global_time'] != v2['global_time']:
            diffs.append(f"global_time: v1={v1['global_time']} v2={v2['global_time']}")
        if v1['timestep'] != v2['timestep']:
            diffs.append(f"timestep: v1={v1['timestep']} v2={v2['timestep']}")

        v1_in = v1['inputs']
        v2_in = v2['inputs']
        all_keys = sorted(set(v1_in) | set(v2_in))
        for k in all_keys:
            vv1 = v1_in.get(k, '<missing>')
            vv2 = v2_in.get(k, '<missing>')
            if vv1 != vv2:
                diffs.append(f"  {k}: v1={vv1!r} v2={vv2!r}")

        if diffs:
            print(f"=== {name} ===")
            for d in diffs:
                print(f"  {d}")
            print()


if __name__ == '__main__':
    with chdir(ROOT_PATH):
        v1_path = tempfile.mktemp(suffix='.pkl')
        v2_path = tempfile.mktemp(suffix='.pkl')
        print("Running v1...", flush=True)
        v1_calls = run_in_subproc('run_v1', v1_path)
        print(f"  v1 captured {len(v1_calls)} first-calls\n", flush=True)
        print("Running v2...", flush=True)
        v2_calls = run_in_subproc('run_v2', v2_path)
        print(f"  v2 captured {len(v2_calls)} first-calls\n", flush=True)
        diff_calls(v1_calls, v2_calls)
