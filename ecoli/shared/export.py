import simdjson as json
import copy
import os 
import unum

import numpy as np
from process_bigraph import pp
from vivarium.core.composer import Composer

from ecoli.experiments.ecoli_master_sim import EcoliSim
from ecoli import ROOT


def format_config(d: dict, instance=None):
    if isinstance(d, dict):
        for key, val in d.items():
            if isinstance(val, np.ndarray): 
                d[key] = val.tolist()
            if isinstance(val, set):
                d[key] = list(val)
            if isinstance(val, unum.Unum):
                d[key] = str(val)
            if type(val).__name__ == "function":
                if instance is not None:
                    setattr(instance, key, val)
                d[key] = key
        else:
            return {k: format_config(v, instance) for k, v in d.items()}
    else:
        return d 
    

def extract_process_states(export: bool = False):
    sim = EcoliSim.from_file()
    sim.build_ecoli()
    processes = sim.processes

    data_dir = os.path.join(ROOT, 'data', 'model')
    topology_fp = os.path.join(data_dir, 'single_topology.json')
    with open(topology_fp, 'r') as f:
        raw_state = json.load(f)
    
    state = {}
    for process_id, process_ports in raw_state.items():
        process_spec = {
            "address": f"local:{process_id}",
            "inputs": process_ports,
            "outputs": process_ports
        }

        # attempt config extract if instance is available
        if process_id in processes:
            instance = processes[process_id]
            process_config = copy.deepcopy(instance.defaults)
            process_config = format_config(
                process_config, 
                instance
            )
            process_spec["config"] = process_config

        state[process_id] = process_spec
    
    if export:
        export_state(data_dir, state)
    return state


def export_state(dirpath: str, state, filename: str | None = None):
    state_fp = os.path.join(dirpath, filename or 'single_state.json')
    with open(state_fp, 'w') as f:
        json.dump(state, f, indent=4)
