import abc
from dataclasses import dataclass, field
from functools import wraps
import json

from process_bigraph import Step, Process
from vivarium.vivarium import Vivarium

from ecoli.experiments.ecoli_master_sim import EcoliSim
from ecoli.shared.datamods import BaseClass
from ecoli.shared.schemas import get_config_schema
from ecoli.shared.registration import Core, ecoli_core


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


@dataclass 
class Topology(BaseClass):
    inputs: dict = field(default_factory=dict)
    outputs: dict = field(default_factory=dict)
    

class EdgeBase(abc.ABC):
    """We keep the following attrs from the 1.0 implementations:
        - name
        - topology
        - defaults
        - ports_schema
    """
    name = "base"
    topology = {}  # topology will already be nested
    directed_topology = Topology()
    _captured_state = {}

    def ports_schema(self):
        return {}
    
    def initial_state(self):
        return collapse_defaults(self.ports_schema())

    @classmethod
    def verify_ports(cls, schema: dict) -> bool:
        """
        Checks ports schema (to be used for either inputs or outputs) against the defined topology
        """
        return all([key in list(cls.topology.keys()) for key in schema.keys()])
    

class StepBase(EdgeBase, Step):
    defaults = {}
    config_schema = {}

    def __init__(self, config=None, core=None):
        self.timestep_schema = {"_default": 1.0, "_type": "float"}
        self.config_schema = get_config_schema(self.defaults)
        self.config_schema['time_step'] = self.timestep_schema
        super().__init__(config, core)
        self.timestep = self.config["time_step"]


class ProcessBase(EdgeBase, Process):
    defaults = {}
    config_schema = {}

    def __init__(self, config=None, core=None):
        self.timestep_schema = {"_default": 1.0, "_type": "float"}
        self.config_schema = get_config_schema(self.defaults)
        self.config_schema['time_step'] = self.timestep_schema
        super().__init__(config, core)
        self.timestep = self.config["time_step"]


@dataclass
class EmitterConfig(BaseClass):
    address: str 
    config: dict = field(default_factory=dict)  # config schema
    mode: str = "all"
    path: tuple[str] = ("emitter",)


class VivariumFactory:
    _default_core: Core = ecoli_core

    def __init__(self, default_protocol: str | None = None) -> None:
        self.default_protocol = default_protocol or "community"

    @property
    def default_core(self):
        return self._default_core
    
    def new(
            self, 
            document: dict | None = None, 
            processes: dict | None = None,
            core=None,
            emitter_config: EmitterConfig | None = None
    ) -> Vivarium:
        c = core or self.default_core
        return Vivarium(
            core=c, 
            processes=processes or c.process_registry.registry, 
            types=c.types(), 
            document=document,
            emitter_config=emitter_config.as_dict() if emitter_config else None
        )
    
    def __call__(
            self, 
            document: dict | None = None, 
            processes: dict | None = None, 
            core=None,
            emitter_config: EmitterConfig | None = None
    ) -> Vivarium:
        return self.new(document, core, emitter_config)


def flatten_state(state, parent_key='', sep='.'):
    items = {}
    for k, v in state.items():
        new_key = f"{parent_key}{sep}{k}" if parent_key else k
        if isinstance(v, dict):
            items.update(flatten_state(v, new_key, sep=sep))
        else:
            items[new_key] = v
    return items


vivarium_factory = VivariumFactory()


def generate_vivarium_from_objects(datapath: str, config_path: str) -> Vivarium:
    sim = EcoliSim.from_file(config_path)
    processes = {}
    for process_id in sim.processes:
        processes[process_id] = ecoli_core.process_registry.registry[process_id]

    v = vivarium_factory(processes=processes, core=ecoli_core)
    with open(datapath, 'r') as fp:
        state = json.load(fp)

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
    return v




