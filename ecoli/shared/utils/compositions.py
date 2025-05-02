from dataclasses import dataclass, field
from process_bigraph import pp, Process as PbgProcess
from vivarium.core.composer import Composer
from vivarium.core.process import Process as VivariumProcess 
from ecoli.experiments.ecoli_master_sim import EcoliSim
from ecoli.shared.data_model import BaseClass


@dataclass
class CompositionData:
    """Contains all of the information needed to extract the pbg document."""
    config_path: str | None = None
    _args = [config_path] if config_path is not None else []
    simulation = EcoliSim.from_file(*_args)
    _generator = (( lambda sim: sim.build_ecoli() ))(simulation)
    topology = simulation.ecoli.topology['agents']['0'] # type: ignore
    processes = simulation.processes


def initialize_ecoli_data(config_path: str | None = None):
    args = []
    if config_path is not None:
        args.append(config_path)

    # instantiate sim
    sim = EcoliSim.from_file(*args)
    
    # initialize sim, thereby filling in the holes
    sim.build_ecoli()

    topology = sim.ecoli.topology['agents']['0'] # type: ignore
    processes = sim.processes


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

