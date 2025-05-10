"""
Base vivarium instance with parquet emitter config.

emitter config is as such:

config_schema = {
        'batch_size': {
            '_type': 'integer',
            '_default': 400
        },
        # ONE of the following:
        'out_dir': 'maybe[string]',  # local output directory (absolute/relative),
        'out_uri': 'maybe[string]'  # Google Cloud storage bucket URI
    }

TODO: replace locally encrypted out_dir with secure URI    
"""


from dataclasses import dataclass, field
from functools import wraps
import json
import os

from vivarium import Vivarium 

from ecoli.experiments.ecoli_master_sim import EcoliSim
from ecoli.shared.registry import ecoli_core
from ecoli.shared.utils.schemas import flatten_state
from ecoli.shared.vivarium_instances.factory import EmitterConfig, VivariumFactory


DEFAULT_EMITTER_ADDRESS = "local:parquet-emitter"
DATA_DIR = os.path.join(
    os.path.dirname(
        os.path.dirname(
            os.path.dirname(__file__)
        )
    ),
    "storage",
    "data"
)

vivarium_factory = VivariumFactory()

default_emitter_config = EmitterConfig(
    address=DEFAULT_EMITTER_ADDRESS,
    config={
        "out_dir": DATA_DIR
    }
)

# vivarium_default = vivarium_factory(core=ecoli_core, emitter_config=default_emitter_config)

def generate_vivarium_from_files(state_data_path: str, config_path: str) -> Vivarium:
    """
    1. generate composite from objects
    2. add each process in EcoliSim.processes using process.name and process.topology
    """
    sim = EcoliSim.from_file(config_path)
    processes = {}
    for process_id in sim.processes:
        processes[process_id] = ecoli_core.process_registry.registry[process_id]

    v = vivarium_factory(processes=processes, core=ecoli_core)
    with open(state_data_path, 'r') as fp:
        state = json.load(fp)

    # add objects to state
    flattened = flatten_state(state)
    for obj_name, obj_value in flattened.items():
        print(obj_name)
        obj_path = []
        if '.' not in obj_name:
            obj_path.append(obj_name)
        else:
            obj_path = obj_name.split('.')
            obj_name = obj_path.pop(-1)
        v.add_object(name=obj_name, path=obj_path, value=obj_value)
    
    # add processes to state
    for process_id, process in processes.items():
        v.add_process(
            name=process_id,
            process_id=process_id,
            inputs=process.topology['inputs'],
            outputs=process.topology['outputs']
        )
    return v
