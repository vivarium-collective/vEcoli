"""Compare polypeptide_elongation inputs at t=2 between v1 and v2."""

import sys
import os
import subprocess
import pickle
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_pe_state(engine):
    script = f"""
import sys, pickle, hashlib
sys.path.insert(0, '.')
import numpy as np

# Patch PE.calculate_request to capture inputs at t=2
from ecoli.processes.polypeptide_elongation import PolypeptideElongation
_orig = PolypeptideElongation.calculate_request
_call = [0]
captured = [None]

def _logged(self, timestep, states):
    _call[0] += 1
    if _call[0] == 2:  # second call = t=2
        bulk_arr = states['bulk']
        bulk_total_arr = states['bulk_total']
        listeners = states['listeners']
        env = states['environment']
        pe_state = states['polypeptide_elongation']
        active_rib = states['active_ribosome']
        snap = {{
            'rng_pos': self.random_state.get_state()[2],
            'rng_first5': self.random_state.get_state()[1][:5].tolist(),
            'timestep': int(timestep),
            'bulk_hash': hashlib.sha1(bytes(bulk_arr['count'])).hexdigest()[:12],
            'bulk_total_hash': hashlib.sha1(bytes(bulk_total_arr['count'])).hexdigest()[:12],
            'bulk_sum': int(bulk_arr['count'].sum()),
            'bulk_total_sum': int(bulk_total_arr['count'].sum()),
            'cell_mass': float(listeners['mass']['cell_mass']),
            'dry_mass': float(listeners['mass']['dry_mass']),
            'media_id': env['media_id'],
            'pe_aa_count_diff': pe_state['aa_count_diff'].tolist() if hasattr(pe_state['aa_count_diff'], 'tolist') else list(pe_state['aa_count_diff']),
            'pe_aa_exchange_rates': pe_state['aa_exchange_rates'].tolist() if hasattr(pe_state['aa_exchange_rates'], 'tolist') else list(pe_state['aa_exchange_rates']),
            'pe_gtp_to_hydrolyze': float(pe_state['gtp_to_hydrolyze']),
            'active_ribosome_n': int(active_rib['_entryState'].sum()),
            'active_ribosome_hash': hashlib.sha1(active_rib.tobytes()).hexdigest()[:12],
        }}
        captured[0] = snap
    return _orig(self, timestep, states)

PolypeptideElongation.calculate_request = _logged

from ecoli.experiments.ecoli_master_sim import EcoliSim
sim = EcoliSim.from_file()
sim.max_duration = 2
sim.emitter = 'null'
sim.divide = False
{"sim.config['engine'] = 'composite'" if engine == 'v2' else ""}
sim.build_ecoli()
sim.run()

with open(sys.argv[1], 'wb') as f:
    pickle.dump(captured[0], f)
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
v1 = get_pe_state('v1')
print("Running v2...")
v2 = get_pe_state('v2')

if v1 and v2:
    all_keys = set(v1.keys()) | set(v2.keys())
    print()
    for k in sorted(all_keys):
        v1v = v1.get(k)
        v2v = v2.get(k)
        import numpy as np
        if isinstance(v1v, np.ndarray):
            match = '✓' if isinstance(v2v, np.ndarray) and np.array_equal(v1v, v2v) else '✗'
            print(f"{k:30s} {match}  shape={v1v.shape}")
        else:
            match = '✓' if v1v == v2v else '✗'
            print(f"{k:30s} {match}  v1={v1v}  v2={v2v}")
