"""Compare unique molecule state at end of t=1 between v1 and v2."""

import sys
import os
import subprocess
import pickle
import tempfile
import hashlib
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_unique(engine, duration=1):
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

result = {{}}

if '{engine}' == 'v1':
    from ecoli.library.schema import not_a_process
    state = sim.ecoli_experiment.state.get_value(condition=not_a_process)
    unique = state.get('unique', {{}})
else:
    composite = sim._composite
    if 'agents' in composite.state:
        cell = next(iter(composite.state['agents'].values()))
    else:
        cell = composite.state
    unique = cell.get('unique', {{}})

# Hash each unique molecule type
import numpy as np
for name, arr in unique.items():
    if isinstance(arr, np.ndarray):
        h = hashlib.sha1(arr.tobytes()).hexdigest()[:12]
        size = len(arr) if hasattr(arr, '__len__') else '?'
        # Get entry state count if structured
        entry_count = '?'
        if hasattr(arr, 'dtype') and arr.dtype.names and '_entryState' in arr.dtype.names:
            entry_count = int(arr['_entryState'].sum())
        result[name] = {{'hash': h, 'size': size, 'entries': entry_count}}
    else:
        result[name] = {{'type': type(arr).__name__}}

with open(sys.argv[1], 'wb') as f:
    pickle.dump(result, f)
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
v1 = get_unique('v1', 1)
print("Running v2...")
v2 = get_unique('v2', 1)

if v1 and v2:
    all_keys = set(v1.keys()) | set(v2.keys())
    print(f"\nUnique molecule types: {len(all_keys)}")
    print(f"\n{'name':30s} {'v1_hash':15s} {'v2_hash':15s} {'v1_entries':>12s} {'v2_entries':>12s} {'match'}")
    for name in sorted(all_keys):
        v1d = v1.get(name, {})
        v2d = v2.get(name, {})
        v1h = v1d.get('hash', '?')[:12]
        v2h = v2d.get('hash', '?')[:12]
        v1e = v1d.get('entries', '?')
        v2e = v2d.get('entries', '?')
        match = '✓' if v1h == v2h else '✗'
        print(f"{name:30s} {v1h:15s} {v2h:15s} {str(v1e):>12s} {str(v2e):>12s} {match}")
