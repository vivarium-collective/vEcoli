"""Trace when Metabolism.update fires in v1 vs v2 — at what global_time values.

Run from vEcoli root:
    python runscripts/trace_metabolism.py
"""
import sys, os, subprocess, tempfile, pickle
from contextlib import chdir

ROOT_PATH = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def trace_v1():
    """Monkey-patch Metabolism.update BEFORE build_ecoli; run 1s; return call log."""
    from ecoli.processes.metabolism import Metabolism
    calls = []
    _orig = Metabolism.update

    def traced(self, states, interval=None):
        gt = states.get('global_time', None)
        calls.append(('v1', float(gt) if gt is not None else None, interval))
        return _orig(self, states, interval)

    Metabolism.update = traced

    from ecoli.experiments.ecoli_master_sim import EcoliSim
    sim = EcoliSim.from_file()
    sim.max_duration = 1
    sim.emitter = 'null'
    sim.divide = False
    sim.build_ecoli()
    sim.run()
    return calls


def trace_v2():
    from ecoli.processes.metabolism import Metabolism
    calls = []
    _orig = Metabolism.update

    def traced(self, states, interval=None):
        gt = states.get('global_time', None)
        calls.append(('v2', float(gt) if gt is not None else None, interval))
        return _orig(self, states, interval)

    Metabolism.update = traced

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


def run_in_subproc(func_name):
    script = f"""
import pickle, sys
sys.path.insert(0, '.')
from runscripts.trace_metabolism import {func_name}
calls = {func_name}()
with open(sys.argv[1], 'wb') as f:
    pickle.dump(calls, f)
"""
    with tempfile.NamedTemporaryFile(suffix='.pkl', delete=False) as tmp:
        tmp_path = tmp.name
    proc = subprocess.Popen(
        [sys.executable, '-u', '-c', script, tmp_path],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1)
    tail = []
    for line in proc.stdout:
        if '[run_steps]' in line or '[gate]' in line or '[step_run]' in line:
            print(f"    > {line}", end='', flush=True)
        tail.append(line)
        if len(tail) > 40:
            tail.pop(0)
    proc.wait(timeout=900)
    if proc.returncode != 0:
        print("".join(tail))
        raise RuntimeError(f"{func_name} failed rc={proc.returncode}")
    with open(tmp_path, 'rb') as f:
        return pickle.load(f)


if __name__ == '__main__':
    with chdir(ROOT_PATH):
        print("=== Tracing v1 Metabolism.update calls ===", flush=True)
        v1_calls = run_in_subproc('trace_v1')
        for c in v1_calls:
            print(f"  {c}")
        print(f"v1 total calls: {len(v1_calls)}\n", flush=True)

        print("=== Tracing v2 Metabolism.update calls ===", flush=True)
        v2_calls = run_in_subproc('trace_v2')
        for c in v2_calls:
            print(f"  {c}")
        print(f"v2 total calls: {len(v2_calls)}", flush=True)
