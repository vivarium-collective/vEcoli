"""Confirm or refute: after the first colony division, do the daughter
cells' ``division`` step configs carry the *mother's* agent_id (stale)
instead of their own new key?

If so, that explains the runaway ``DIVIDE! MOTHER 0`` loop we see at
the 2nd doubling.
"""
import os
os.environ.setdefault('OMP_NUM_THREADS', '1')
os.environ.setdefault('MKL_NUM_THREADS', '1')
os.environ.setdefault('OPENBLAS_NUM_THREADS', '1')
os.environ.setdefault('NUMBA_NUM_THREADS', '1')

import sys
from configs import CONFIG_DIR_PATH
from ecoli.library.bigraph_types import ECOLI_TYPES
from ecoli.composites.ecoli_composite import build_ecoli_document, run_to_division
from ecoli.experiments.ecoli_master_sim import EcoliSim
from ecoli.library.sim_data import LoadSimData
from process_bigraph import Composite, allocate_core


def main():
    sim_data_path = sys.argv[1] if len(sys.argv) > 1 else 'out/kb/simData.cPickle'

    sim = EcoliSim.from_file(os.path.join(CONFIG_DIR_PATH, 'default.json'))
    sim.processes = sim._retrieve_processes(
        sim.processes, sim.add_processes,
        sim.exclude_processes, sim.swap_processes)
    sim.topology = sim._retrieve_topology(
        sim.topology, sim.processes, sim.swap_processes, sim.log_updates)
    sim.process_configs = sim._retrieve_process_configs(
        sim.process_configs, sim.processes)
    sim_config = dict(sim.config)
    sim_config['sim_data_path'] = sim_data_path
    sim_config['agent_id'] = '0'
    sim_config['seed'] = 0
    sim_config['divide'] = True

    core = allocate_core()
    core.register_types(ECOLI_TYPES)
    lsd = LoadSimData(**{**sim_config, 'seed': 0})

    state = build_ecoli_document(core, sim_config, load_sim_data=lsd)
    composite = Composite(
        {'schema': {}, 'state': state, 'run_steps_on_init': True},
        core=core)

    print(f'[probe] pre-run agents: {sorted(composite.state["agents"].keys())}',
          flush=True)
    pre_div = composite.state['agents']['0'].get('division', {})
    print(f'[probe]   mother division: type={pre_div.get("_type")} '
          f'config.agent_id={pre_div.get("config", {}).get("agent_id")!r}',
          flush=True)

    # Drive to first division
    divided, ct = run_to_division(composite, max_duration=3000)
    print(f'\n[probe] divided={divided} at t={ct:.1f}', flush=True)
    print(f'[probe] post-divide agents: '
          f'{sorted(composite.state["agents"].keys())}', flush=True)

    for did in sorted(composite.state['agents'].keys()):
        cell = composite.state['agents'][did]
        if not isinstance(cell, dict):
            continue
        div = cell.get('division', {})
        cfg = div.get('config', {}) if isinstance(div, dict) else {}
        inst = div.get('instance') if isinstance(div, dict) else None
        inst_agent_id = getattr(inst, 'agent_id', None) if inst is not None else None
        print(f'[probe] daughter key={did!r}: '
              f'config.agent_id={cfg.get("agent_id")!r} '
              f'instance.agent_id={inst_agent_id!r}',
              flush=True)


if __name__ == '__main__':
    main()
