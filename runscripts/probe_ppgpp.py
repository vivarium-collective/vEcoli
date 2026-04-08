"""Probe ppGpp synthesis at t=1 in v1 and v2 to find the source of off-by-one."""

import sys
import os
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run_engine(engine):
    script = f"""
import sys
sys.path.insert(0, '.')

# Patch stochasticRound at the module that polypeptide_elongation imports from
from wholecell.utils import random as _wcrand
_orig_round = _wcrand.stochasticRound
_round_calls = [0]

def _logged_round(random_state, value):
    _round_calls[0] += 1
    pos = random_state.get_state()[2]
    rs_id = id(random_state)
    # Identify caller via stack frame
    import traceback
    stack = traceback.extract_stack()
    caller = '?'
    for frame in reversed(stack[:-1]):
        if 'ecoli/processes/' in frame.filename:
            caller = f'{{frame.filename.split("/processes/")[-1]}}:{{frame.lineno}}'
            break
    try:
        if hasattr(value, '__len__'):
            sys.stderr.write(f'[ROUND #{{_round_calls[0]}}] rs={{rs_id % 100000}} pos={{pos}} val[0]={{float(value[0]):.18g}} from={{caller}}\\n')
        else:
            sys.stderr.write(f'[ROUND #{{_round_calls[0]}}] rs={{rs_id % 100000}} pos={{pos}} val={{float(value):.18g}} from={{caller}}\\n')
        sys.stderr.flush()
    except Exception as e:
        sys.stderr.write(f'[ROUND #{{_round_calls[0]}}] err={{e}}\\n'); sys.stderr.flush()
    result = _orig_round(random_state, value)
    return result

_wcrand.stochasticRound = _logged_round

# Also patch the symbol that was imported into polypeptide_elongation already
from ecoli.processes import polypeptide_elongation as pe_mod
pe_mod.stochasticRound = _logged_round

# Count calculate_request and evolve_state calls
from ecoli.processes.polypeptide_elongation import PolypeptideElongation
_cr_count = [0]
_es_count = [0]
_orig_cr = PolypeptideElongation.calculate_request
_orig_es = PolypeptideElongation.evolve_state

def _counted_cr(self, timestep, states):
    _cr_count[0] += 1
    sys.stderr.write(f'[CR #{{_cr_count[0]}}] rng_pos={{self.random_state.get_state()[2]}}\\n'); sys.stderr.flush()
    return _orig_cr(self, timestep, states)

def _counted_es(self, timestep, states):
    _es_count[0] += 1
    pos = self.random_state.get_state()[2]
    # Hash the bulk counts to detect any difference
    import hashlib
    bulk = states.get('bulk')
    if bulk is not None:
        if hasattr(bulk, 'dtype') and bulk.dtype.names and 'count' in bulk.dtype.names:
            bulk_counts = bulk['count']
        else:
            bulk_counts = bulk
        h = hashlib.sha1(bytes(bulk_counts)).hexdigest()[:12]
        sys.stderr.write(f'[ES #{{_es_count[0]}}] rng_pos={{pos}} bulk_hash={{h}} bulk_sum={{int(bulk_counts.sum())}}\\n')
    else:
        sys.stderr.write(f'[ES #{{_es_count[0]}}] rng_pos={{pos}}\\n')
    sys.stderr.flush()
    return _orig_es(self, timestep, states)

PolypeptideElongation.calculate_request = _counted_cr
PolypeptideElongation.evolve_state = _counted_es

# Trace the allocator
from ecoli.processes.allocator import Allocator
_orig_alloc = Allocator.update
def _logged_alloc(self, states, interval=None):
    rng = states.get('allocator_rng')
    if rng is not None:
        pos = rng.get_state()[2]
        sys.stderr.write(f'[ALLOC] rng_pos={{pos}} rng_id={{id(rng) % 100000}}\\n'); sys.stderr.flush()
    # Hash request matrix
    import hashlib
    bulk_total_req = 0
    for proc, port in states['request'].items():
        for req_idx, req in port['bulk']:
            bulk_total_req += int(req)
    sys.stderr.write(f'[ALLOC] total_req={{bulk_total_req}} n_procs_with_reqs={{sum(1 for p, port in states["request"].items() if len(port["bulk"]) > 0)}}\\n'); sys.stderr.flush()
    return _orig_alloc(self, states, interval)
Allocator.update = _logged_alloc

# Run sim
from ecoli.experiments.ecoli_master_sim import EcoliSim
sim = EcoliSim.from_file()
sim.max_duration = 1
sim.emitter = 'null'
sim.divide = False
{"sim.config['engine'] = 'composite'" if engine == 'v2' else ""}
sim.build_ecoli()
sim.run()

sys.stderr.write(f'[FINAL] total_round_calls={{_round_calls[0]}}\\n')
sys.stderr.flush()
"""
    proc = subprocess.run(
        [sys.executable, '-c', script],
        capture_output=True, text=True, timeout=180,
        cwd=ROOT)
    return proc.stderr


for engine in ('v1', 'v2'):
    print(f'\n=== {engine} ===', flush=True)
    out = run_engine(engine)
    for line in out.splitlines():
        if '[ROUND' in line or '[FINAL]' in line or '[CR' in line or '[ES' in line or '[ALLOC' in line:
            print(line)
