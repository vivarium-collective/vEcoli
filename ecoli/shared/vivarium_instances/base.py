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


def capture_arg(arg_to_capture: str):
    """
    Usage:

    @capture_arg('state')
    def update(self, state):
        # self._captured_state will be available here
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            self_obj = args[0]  # first argument is always `self`

            arg_names = func.__code__.co_varnames[:func.__code__.co_argcount]
            all_args = dict(zip(arg_names, args))
            all_args.update(kwargs)

            captured_value = all_args.get(arg_to_capture)
            setattr(self_obj, f"_captured_{arg_to_capture}", captured_value)

            return func(*args, **kwargs)
        return wrapper
    return decorator


def collapse_defaults(d):
    if isinstance(d, dict):
        if '_default' in d:
            return d['_default'] 
        else:
            return {k: collapse_defaults(v) for k, v in d.items()}
    else:
        return d


def flatten_state(state, parent_key='', sep='.'):
    items = {}
    for k, v in state.items():
        new_key = f"{parent_key}{sep}{k}" if parent_key else k
        if isinstance(v, dict):
            items.update(flatten_state(v, new_key, sep=sep))
        else:
            items[new_key] = v
    return items


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
