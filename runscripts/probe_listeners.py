"""Compare listener state at end of t=1 between v1 and v2."""

import sys
import os
import subprocess
import pickle
import tempfile
import hashlib
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_listeners(engine, duration=1):
    script = f"""
import sys, pickle, hashlib
sys.path.insert(0, '.')
from ecoli.experiments.ecoli_master_sim import EcoliSim

sim = EcoliSim.from_file()
sim.max_duration = int({duration})
sim.emitter = 'null'
sim.divide = False
{"sim.config['engine'] = 'composite'" if engine == 'v2' else ""}
sim.build_ecoli()
sim.run()

if '{engine}' == 'v1':
    from ecoli.library.schema import not_a_process
    state = sim.ecoli_experiment.state.get_value(condition=not_a_process)
    listeners = state.get('listeners', {{}})
else:
    composite = sim._composite
    if 'agents' in composite.state:
        cell = next(iter(composite.state['agents'].values()))
    else:
        cell = composite.state
    listeners = cell.get('listeners', {{}})

# Recursively walk and hash leaves
def walk(d, prefix=''):
    result = {{}}
    if isinstance(d, dict):
        for k, v in d.items():
            sub_prefix = f'{{prefix}}.{{k}}' if prefix else k
            result.update(walk(v, sub_prefix))
    else:
        try:
            if isinstance(d, np.ndarray):
                result[prefix] = ('arr', d.shape, hashlib.sha1(d.tobytes()).hexdigest()[:12])
            elif isinstance(d, (int, float)):
                result[prefix] = ('num', d)
            elif isinstance(d, list):
                arr = np.array(d) if d else None
                if arr is not None and arr.dtype != object:
                    result[prefix] = ('list', len(d), hashlib.sha1(arr.tobytes()).hexdigest()[:12])
                else:
                    result[prefix] = ('list', len(d) if hasattr(d, '__len__') else '?', '?')
            elif isinstance(d, str):
                result[prefix] = ('str', d)
            else:
                result[prefix] = ('?', type(d).__name__)
        except Exception as e:
            result[prefix] = ('err', str(e)[:30])
    return result

flat = walk(listeners)

with open(sys.argv[1], 'wb') as f:
    pickle.dump(flat, f)
"""
    with tempfile.NamedTemporaryFile(suffix='.pkl', delete=False) as tmp:
        tmp_path = tmp.name
    proc = subprocess.run(
        [sys.executable, '-c', script, tmp_path],
        capture_output=True, text=True, timeout=300, cwd=ROOT)
    if proc.returncode != 0:
        print(f"FAILED: {proc.stderr[-500:]}")
        return None
    with open(tmp_path, 'rb') as f:
        return pickle.load(f)


print("Running v1...")
v1 = get_listeners('v1', 1)
print("Running v2...")
v2 = get_listeners('v2', 1)

if v1 and v2:
    all_keys = set(v1.keys()) | set(v2.keys())
    diffs = []
    for k in sorted(all_keys):
        v1v = v1.get(k, ('missing',))
        v2v = v2.get(k, ('missing',))
        if v1v != v2v:
            diffs.append((k, v1v, v2v))
    print(f"\n{len(all_keys)} listener fields total")
    print(f"{len(diffs)} differ\n")
    for k, v1v, v2v in diffs[:30]:
        print(f"  {k}")
        print(f"    v1: {v1v}")
        print(f"    v2: {v2v}")
