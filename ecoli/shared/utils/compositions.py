from process_bigraph import pp
from vivarium.core.composer import Composer

from ecoli.experiments.ecoli_master_sim import EcoliSim


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

