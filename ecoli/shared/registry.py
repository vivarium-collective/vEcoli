# TODO: provide clean registration here!

from dataclasses import dataclass, field
import dataclasses
import datetime
import json
import logging
import os
from importlib import import_module
from types import ModuleType
from typing import Any, Dict, List, Optional

from process_bigraph import ProcessTypes, Process
from vivarium.core.registry import Registry as VivRegistry
from bigraph_schema import Registry as BgsRegistry

from ecoli.shared.data_model import BaseClass
from ecoli.shared.types.register import register_type


PROCESS_SUBPACKAGES = [
    'antibiotics',
    'chemotaxis',
    'environment',
    'listeners',
    'membrane',
    'spatiality',
    'stubs'
]
            
            
def get_migration_module_mapping():
    """Collect all the existing (v1) implementation process id's (found in the configs) from the given 
    module_path and return the migration-version. So, 'ecoli.processes.listeners...', becomes 'ecoli.migrated.listeners...'
    """
    from ecoli import processes

    name_mapping = {}
    for imp in dir(processes):
        if imp[0].isupper():
            proc = getattr(processes, imp)
            location_path = f'{proc.__module__}.{imp}'
            name_mapping[proc.name] = location_path.replace("processes", "migrated")
    return name_mapping


def get_primary_process_mapping():
    mapping = get_migration_module_mapping()
    return {
        k: v
        for k, v in mapping.items() \
        if v.split('.')[2] not in PROCESS_SUBPACKAGES
    }


class TopologyRegistry(VivRegistry):
    """Maps process names to topology"""
    pass


class ProcessRegistry(BgsRegistry):
    pass


class ModelProcesses:
    @property
    def all(self):
        return get_migration_module_mapping()
    
    @property
    def primary(self):
        return get_primary_process_mapping()

    @property
    def dict(self):
        return {'all': self.all, 'primary': self.primary}


@dataclass
class Processes:
    _processes: ProcessRegistry | BgsRegistry

    @property
    def model(self):
        return ModelProcesses()

    @property
    def registered(self):
        return list(self._processes.registry.keys())
    


@dataclass
class DataView:
    _core: ProcessTypes
    _processes: ProcessRegistry | BgsRegistry
    _topology: TopologyRegistry | None = None

    @property
    def processes(self):
        return Processes(self._processes)
    
    @property
    def types(self):
        return(self._core.types())
    
    @property
    def topology(self):
        return self._topology.registry if self._topology else {}


@dataclass
class Register:
    _core: Any

    def process(self, name, process):
        return self._core.process_registry.register(name, process)
    
    def type(self, schema):
        return self._core.register_types(schema)


class Core(ProcessTypes):
    def __init__(
            self, 
            topology_registry: TopologyRegistry | None = None, 
            *args, 
            **kwargs):
        super().__init__(*args, **kwargs)
        self.last_update: str = self.timestamp()
        self._topology = topology_registry or TopologyRegistry()
        self._view = DataView(self, self.process_registry, self.topology)
    
    def timestamp(self):
        return str(datetime.datetime.now())
    
    @property
    def add(self):
        return Register(self)
    
    @property
    def topology(self) -> TopologyRegistry:
        return self._topology
    
    @property 
    def get(self) -> DataView:
        """
        Allows for dot notation throughout a get call, ie: Core().view.processes <- gets process names that are registered.
        Possibly view callables are: processes, types, and topology
        """
        return self._view
    
    @get.setter
    def view(self, v):
        raise AssertionError("You cannot set the view.")
    
    @property
    def model_processes(self):
        return ModelProcesses()
    
    def register_type(self, module_name: str):
        return register_type(module_name, core=self)
    
    def register_process_package(self, package_name: str, verbose=False):
        """Assumes there to be an __all__ definition in the referenced package"""
        package = import_module(f"ecoli.{package_name}")
        for process in package.__all__:
            try:
                proc = getattr(package, process)
                # process_id = proc.__module__.split('.')[-1]
                process_id = proc.name
                self.add.process(process_id, proc)
                if verbose:
                    print(f'{process_id} registered to processes')
            except Exception as e:
                print(e) if verbose else None


# ------------------- # 

@dataclasses.dataclass
class Implementation:
    location: str  # source code path
    address: str  # suffix to process bigraph composite document address "<PROTOCOL>:<ADDRESS>"
    dependencies: List[str]  # dependencies to install with implementation
    protocol: str = "local"


@dataclasses.dataclass
class Schema:
    _type: str
    _default: Any
    _apply: Optional[str] = None
    _serialize: Optional[str] = None
    _divide: Optional[str] = None
    params: Optional[Dict[str, Any]] = None

    def __post_init__(self):
        if self.params is not None:
            for k, v in self.params.items():
                setattr(self, k, v)


@dataclasses.dataclass
class Type:
    id: str
    schema: Schema


@dataclasses.dataclass
class ImplementationRegistry:
    id: str
    implementation: ProcessTypes
    primary: bool


class SimulatorDependency(str):
    pass


class Registrar(object):
    registries: List[ImplementationRegistry]
    core: ProcessTypes
    implementation_dependencies: Dict[str, List[SimulatorDependency]]

    def __init__(self, core: ProcessTypes):
        self.core = core
        self.registries = []

        default_reg = ImplementationRegistry(
            id="default",
            implementation=ProcessTypes(),
            primary=True
        )
        self.add_registry(default_reg)
        self.set_primary(default_reg.id)
        self.core.type_registry = self.core.types()
        self.initial_registration_complete = False
        self.implementation_dependencies = {}

    @property
    def registered_addresses(self) -> List[str]:
        return list(self.core.process_registry.registry.keys())

    def add_registry(self, registry: ImplementationRegistry):
        if registry.primary:
            for registry in self.registries:
                if registry.primary is not None:
                    registry.primary = False

            self.core = registry.implementation

        self.registries.append(registry)

    def set_primary(self, registry_id: str):
        for registry in self.registries:
            # take away existing primary
            if registry.primary:
                registry.primary = False

            # set ref as primary
            if registry.id == registry_id:
                registry.primary = True
                self.core = registry.implementation

    def register_type(self, type_id: str, type_schema: Dict):
        self.core.register_types({type_id: type_schema})

    def register_process(self, address: str, implementation: object, verbose=False) -> None:
        try:
            type_registry = self.core
            type_registry.process_registry.register(address, implementation)
        except Exception as e:
            if verbose:
                print(f"Cannot register {implementation} to {address}. Error:\n**\n{e}\n**")

    def register_module(self, implementation: Implementation, verbose=False) -> None:
        library, module_name, class_name = implementation.location.rsplit('.', 3)
        try:
            # library = 'steps' if 'process' not in path else 'processes'
            import_statement = f'bsp.{library}.{module_name}'
            module = __import__(
                 import_statement, fromlist=[class_name])
            bigraph_class = getattr(module, class_name)
            self.core.process_registry.register(implementation.address, bigraph_class)
        except Exception as e:
            if verbose:
                print(f"Cannot register {class_name}. Error:\n**\n{e}\n**")

    def register_initial_modules(
            self,
            items_to_register: List[Implementation],
            package: str = "ecoli",
            verbose=False
    ) -> None:
        if not self.initial_registration_complete:
            for implementation in items_to_register:
                self.register_module(implementation=implementation, verbose=verbose)
                process_deps = [SimulatorDependency(dep) for dep in implementation.dependencies]
                self.implementation_dependencies[implementation.address] = process_deps
            self.initial_registration_complete = True

    def register_type_module(self, module: ModuleType, verbose=False) -> None:
        try:
            for schema_name in module.__all__:
                schema = getattr(module, schema_name)
                self.register_type(schema_name, schema)
        except Exception as e:
            if verbose:
                print(f"Cannot register {module}. Error:\n**\n{e}\n**")

    def register_initial_types(self, config: ModuleType, types: ModuleType, verbose=False) -> None:
        for module in [config, types]:
            self.register_type_module(module, verbose=verbose)


# ------------------- # 

class SchemaAttributes:
    @property
    def required(self): return {"_default", "_apply", "_check", "_serialize", "_deserialize", "_fold"}

    @property
    def optional(self): return {
        "_type",
        "_value",
        "_description",
        "_type_parameters",
        "_inherit",
        "_divide",
    }

    @property
    def all(self) -> set:
        return self.required.union(self.optional)

    @required.setter
    @optional.setter 
    def setter(self, v):
        raise ValueError()


@dataclass
class TypeDefinition:
    # required bgs keys: {'_default', '_apply', '_check', '_serialize', '_deserialize', '_fold'}
    # optional bgs keys: {'_type', '_value', '_description', '_type_parameters', '_inherit', '_divide'}s
    type_id: str
    protocol: str = "local"
    schema_attributes: SchemaAttributes = SchemaAttributes()
    _value: dict = field(default_factory=dict)

    def set(self, schema: dict):
        for attribute, definition in schema.items():
            self._value[attribute] = definition

    @property 
    def value(self):
        return self._value

    @property
    def schema(self):
        return dict(zip(
            self.schema_attributes.all,
            [None for key in self.schema_attributes.all]
        ))


def get_type_filepaths(dirpath: str) -> set[str]:
    paths: set = set()
    for filename in os.listdir(dirpath):
        if filename.endswith(".json"):
            paths.add(os.path.join(dirpath, filename))
    return paths


def register_types(core: ProcessTypes, types_dir: str, verbose: bool, logger: logging.Logger) -> None:
    function_keys = ["_serialize", "_deserialize", "_fold", "_check", "_apply"]
    types_to_register: set[str] = get_type_filepaths(types_dir)
    for spec_path in types_to_register:
        try:
            with open(spec_path, "r") as f:
                spec: dict = json.load(f)
            for type_id, type_spec in spec.items():
                if isinstance(type_spec, dict):
                    for key, spec_definition in type_spec.items():
                        if key in function_keys:
                            spec[type_id][key] = eval(spec_definition)
                core.register_types({type_id: type_spec})
                if verbose:
                    logger.info(f"Type ID: {type_id} has been registered.\n")
        except:
            if verbose:
                logger.error(f"{spec_path} cannot be registered.\n")
            continue


def get_process_module_names(subpackage: str = "migrated") -> list[str]:
    mod_names = []
    pkg = import_module(f"ecoli.{subpackage}")
    for item in pkg.__all__:
        proc = getattr(pkg, item)
        proc_module = proc.__module__
        mod_names.append(proc_module)
    return mod_names


# ------- main ---------- # 

topology_registry = TopologyRegistry()
ecoli_core = Core(topology_registry=topology_registry)
