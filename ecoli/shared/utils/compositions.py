from process_bigraph import pp
from vivarium.core.composer import Composer

from ecoli.experiments.ecoli_master_sim import EcoliSim


def extract_process_states(config_path: str | None = None):
    args = []
    if config_path is not None:
        args.append(config_path)

    # instantiate sim
    sim = EcoliSim.from_file(*args)
    
    # initialize sim, thereby filling in the holes
    sim.build_ecoli()

    topology = sim.ecoli.topology['agents']['0'] # type: ignore
    processes = sim.processes



def _extract_process_states(composer: Composer):
    """Converts composer (v1) to process bigraph compliant states"""
    composition = composer.generate()
    topology = composition['topology']
    processes = composition['processes']
    
    state = {}
    for processid, ports in topology.items():
        proc = processes[processid]
        process_state = {
            "address": f"local:{processid}",
            "config": proc.config,
            "inputs": {},
            "outputs": {}
        }
        state[processid] = process_state

        inputs = proc.inputs()
        outputs = proc.outputs()
        for portname, store in ports.items():
            if portname in inputs:
                process_state["inputs"][portname] = store
            if portname in outputs:
                process_state["outputs"][portname] = store
                
    return state


def extract_store(proc, process_state: dict, topology_ports: dict, port_type: str):
    schema = getattr(proc, port_type)()
    for portname, store in topology_ports.items():
        if portname in schema:
            process_state[port_type][portname] = store


def get_port_paths(d: dict, parent_key='') -> set:
    paths = set()
    for k, v in d.items():
        full_key = f"{parent_key}.{k}" if parent_key else k
        if isinstance(v, dict):
            paths |= get_port_paths(v, full_key)
        else:
            paths.add(full_key)
    return paths


def test_get_port_paths():
    import os
    import json 
    from ecoli import DEFAULT_TOPOLOGY_PATH, pp
    
    with open(DEFAULT_TOPOLOGY_PATH, 'r') as f:
        d = json.load(f)
    paths = get_port_paths(d)
    pp(paths)


def test_state_extraction():
    from ecoli.shared.registry import ecoli_core as ec
    from ecoli.migrated.chromosome_structure import TestComposer
    from process_bigraph import Composite

    composer = TestComposer()
    composer.generate()['topology']['unique_update']
    state = extract_process_states(composer)
    pp(state)

