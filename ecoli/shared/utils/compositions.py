from process_bigraph import pp
from vivarium.core.composer import Composer


def extract_process_states(composer: Composer):
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


def test_state_extraction():
    from ecoli.shared.registry import ecoli_core as ec
    from ecoli.migrated.chromosome_structure import TestComposer
    from process_bigraph import Composite

    composer = TestComposer()
    composer.generate()['topology']['unique_update']
    state = extract_process_states(composer)
    pp(state)


test_state_extraction()