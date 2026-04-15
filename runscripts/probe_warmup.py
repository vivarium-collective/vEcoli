"""Time per-bucket tick wall in v2 fresh build to detect first-N-tick
warmup overhead vs steady-state cost.
"""
import time

from ecoli.experiments.ecoli_master_sim import EcoliSim
from ecoli.library.bigraph_types import ECOLI_TYPES
from ecoli.composites.ecoli_composite import build_composite_native
from process_bigraph import Composite
from process_bigraph.types.process import register_types as register_pb
from bigraph_schema import Core, BASE_TYPES

sim = EcoliSim.from_file(filepath='configs/two_generations_v2.json')
sim.config['engine'] = 'composite'
sim.config['emitter'] = 'null'
sim.processes = sim._retrieve_processes(
    sim.processes, sim.add_processes, sim.exclude_processes, sim.swap_processes)
sim.topology = sim._retrieve_topology(
    sim.topology, sim.processes, sim.swap_processes, sim.log_updates)
sim.process_configs = sim._retrieve_process_configs(
    sim.process_configs, sim.processes)
core = Core(BASE_TYPES); register_pb(core); core.register_types(ECOLI_TYPES)
state = build_composite_native(core, sim.config)
comp = Composite({'schema': {}, 'state': state}, core=core)

print('Running 500 ticks, reporting per-50-tick wall...', flush=True)
bstart = time.monotonic()
for i in range(500):
    comp.run(1.0)
    if (i + 1) % 50 == 0:
        now = time.monotonic()
        print(f'  ticks {i+1-49:>4d}..{i+1:>4d}: {now-bstart:.2f}s '
              f'({(now-bstart)/50*1000:.1f}ms/tick)', flush=True)
        bstart = now
