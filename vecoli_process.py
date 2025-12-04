import marimo

__generated_with = "0.17.7"
app = marimo.App(width="full")


@app.cell
def _():
    import sys
    sys.path.insert(0, "../")
    return


@app.cell
def _(traceback):
    from abc import ABCMeta
    import unum
    from pint import Quantity
    from functools import wraps
    from plum import dispatch
    from scipy.sparse._csr import csr_matrix

    from vivarium.core.process import Process as VivariumProcess, Step as VivariumStep

    from bigraph_schema.type_functions import deserialize_array

    import datetime
    import gc
    import json
    import warnings
    from functools import partial
    from pathlib import Path
    from typing import Any
    from urllib import parse
    import pickle

    import xarray as xr
    from process_bigraph import Process as PbgProcess, Composite, ProcessTypes
    import numpy as np
    from vivarium.core.engine import Engine
    from vivarium.core.composer import deep_merge
    from vivarium.core.process import Process
    from vivarium.core.serialize import deserialize_value, serialize_value
    from vivarium.library.dict_utils import deep_merge_check
    from vivarium.library.topology import inverse_topology
    from vivarium.library.topology import assoc_path

    from ecoli.library.logging_tools import write_json
    from ecoli.experiments.ecoli_master_sim import EcoliSim, report_profiling, TimeLimitError, SimConfig

    # Environment composer for spatial environment sim
    import ecoli.composites.environment.lattice
    from ecoli.library.schema import not_a_process

    NONETYPE = type(None)

    def unum_dimension(value):
        dimension = {}
        for unit, scale in value._unit.items():
            entry = value._unitTable[unit]
            base_unit = {
                unit: scale}
            if entry[0]:
                dimension_unit = entry[0]._unit
                base_key = list(dimension_unit.keys())[0]
                base_unit = {base_key: scale}

            dimension.update(
                base_unit)

        return dimension

    def default_unum(schema, core):
        return unum.Unum(
            schema['_dimension'],
            0)

    def serialize_unum(schema, state, core):
        return {
            '_type': 'unum',
            '_dimension': unum_dimension(
                state),
            'units': state._unit,
            'magnitude': state.asNumber()}

    def deserialize_unum(schema, state, core):
        if isinstance(state, unum.Unum):
            return state
        else:
            return unum.Unum(
                state['units'],
                state['magnitude'])

    def check_unum(schema, state, core):
        return isinstance(state, unum.Unum)

    def serialize_csr_matrix(schema, state, core):
        return {
            k: schema[k]
            for k in ['_type', '_shape', '_data']
        } | {
            k: core.serialize(schema[k], getattr(state, f))
            for (k, f) in
            [('data',) * 2, ('indices',) * 2, ('pointers', 'indptr')]
        }

    def deserialize_csr_matrix(schema, state, core):
        match state:
            case csr_matrix():
                return state
            case _:
                return csr_matrix(
                    tuple(core.deserialize(schema[k], state[k])
                          for k in ['data', 'indices', 'pointers']),
                    shape=state.get(
                        '_shape',
                        schema['_shape']))

    def default_env(schema, core):
        return {"env": {"cells": {}}}

    def serialize_env(schema, state, core):
        return pickle.dumps(state)

    def deserialize_env(schema, state, core):
        return pickle.loads(state)

    def check_env(schema, state, core):
        return isinstance(state, bytes) and getattr(state, '__len__')

    ECOLI_TYPES = {
        # 'env': {
        # '_inherit': ['string'],
        # '_default': default_env,
        # '_serialize': serialize_env,
        # '_deserialize': deserialize_env,
        # # '_generate': generate_unum,
        # # '_resolve': resolve_unum,
        # # '_dataclass': dataclass_unum,
        # '_check': check_env},

        'unum': {
            '_inherit': ['number', 'list'],
            '_type_parameters': ['dimension'],
            '_default': default_unum,
            '_serialize': serialize_unum,
            '_deserialize': deserialize_unum,
            # '_generate': generate_unum,
            # '_resolve': resolve_unum,
            # '_dataclass': dataclass_unum,
            '_check': check_unum,
            'units': 'map[float]',
            'magnitude': 'float'},

        'csr_matrix': {
            '_inherit': ['array'],
            '_serialize': serialize_csr_matrix,
            '_deserialize': deserialize_csr_matrix,
            'indices': {
                '_type': 'array',
                '_data': 'integer'},
            'pointers': {
                '_type': 'array',
                '_data': 'integer'}}}

    MISSING_TYPES = {}

    @dispatch
    def infer_representation(value: (int | np.int32 | np.int64 |
                                     np.dtypes.Int32DType | np.dtypes.Int64DType),
                             path: tuple):
        return 'integer'

    @dispatch
    def infer_representation(value: bool, path: tuple):
        return 'boolean'

    @dispatch
    def infer_representation(value: (float | np.float32 | np.float64 |
                                     np.dtypes.Float32DType | np.dtypes.Float64DType),
                             path: tuple):
        return 'float'

    @dispatch
    def infer_representation(value: str, path: tuple):
        return 'string'

    def dtype_schema(d):
        return f'dtype[{d.str}]'

    @dispatch
    def infer_representation(value: np.ndarray, path: tuple):
        shape = '|'.join([str(dimension) for dimension in value.shape])
        data = infer_representation(
            dtype_schema(value.dtype),
            path + ('_data',))

        return f'array[({shape}),{data}]'

    @dispatch
    def infer_representation(value: list, path: tuple):
        element = 'any'
        if len(value) > 0:
            element = infer_representation(
                value[0],
                path + ('_element',))

        return f'list[{element}]'

    def dict_schema(schema):
        parts = []
        for key, subschema in schema.items():
            if isinstance(subschema, dict):
                part = f'({dict_schema(subschema)})'
            else:
                part = subschema
            entry = f'{key}:{part}'
            parts.append(
                entry)

        return '|'.join(
            parts)

    @dispatch
    def infer_representation(value: tuple, path: tuple):
        result = []
        for index, item in enumerate(value):
            key = f'_{index}'
            schema = infer_representation(
                item,
                path + (key,))
            if isinstance(schema, dict):
                schema = dict_schema(schema)
            result.append(schema)

        inner = '|'.join(result)
        return f'({inner})'

    @dispatch
    def infer_representation(value: NONETYPE, path: tuple):
        return 'maybe[any]'

    @dispatch
    def infer_representation(value: set, path: tuple):
        return infer_representation(
            list(value),
            path)

    @dispatch
    def infer_representation(value: unum.Unum, path: tuple):
        dimension = unum_dimension(value)

        return {
            '_type': 'unum',
            '_dimension': dimension,
            'magnitude': infer_representation(
                value.asNumber(),
                path + (value.strUnit(),))}

    class Empty():
        def method(self):
            pass

    FUNCTION_TYPE = type(default_unum)
    METHOD_TYPE = type(Empty().method)

    @dispatch
    def infer_representation(value: FUNCTION_TYPE, path: tuple):
        return 'function'

    @dispatch
    def infer_representation(value: METHOD_TYPE, path: tuple):
        # TODO: add serialize/deserialize for method
        #   by storing where in the state the method is located
        return 'method'

    @dispatch
    def infer_representation(value: ABCMeta, path: tuple):
        return 'meta'

    @dispatch
    def infer_representation(value: csr_matrix, path: tuple):
        return {
            '_type': 'csr_matrix',
            '_shape': value.shape,
            '_data': infer_representation(value.dtype, ()),
            'data': {
                '_type': 'array',
                '_shape': value.data.shape,
                '_data': infer_representation(value.dtype, ())},
            'indices': {
                '_type': 'array',
                '_shape': value.indices.shape,
                '_data': 'integer'},
            'pointers': {
                '_type': 'array',
                '_shape': value.indptr.shape,
                '_data': 'integer'}}

    @dispatch
    def infer_representation(value: dict, path: tuple):
        subvalues = {}
        distinct_subvalues = []
        for key, subvalue in value.items():
            subvalues[key] = infer_representation(
                subvalue,
                path + (key,))

            if subvalues[key] not in distinct_subvalues:
                distinct_subvalues.append(
                    subvalues[key])

        if len(distinct_subvalues) == 1:
            map_value = distinct_subvalues[0]
            if isinstance(map_value, dict):
                map_value = dict_schema(
                    map_value)
            if not map_value:
                map_value = 'any'

            return f'map[{map_value}]'

        else:
            return subvalues

    @dispatch
    def infer_representation(value: VivariumProcess, path: tuple):
        return 'process'

    @dispatch
    def infer_representation(value: VivariumStep, path: tuple):
        return 'step'

    @dispatch
    def infer_representation(value: object, path: object):
        type_name = str(type(value))

        if not hasattr(value, '__dict__'):
            if type_name not in MISSING_TYPES:
                MISSING_TYPES[type_name] = set([])

            MISSING_TYPES[type_name].add(
                path)

            return str(value)

        value_keys = value.__dict__.keys()
        value_schema = {}

        for key in value_keys:
            if not key.startswith('_'):
                try:
                    value_schema[key] = infer_representation(
                        getattr(value, key),
                        path + (key,))
                except Exception as e:
                    traceback.print_exc()
                    print(e)

                    if type_name not in MISSING_TYPES:
                        MISSING_TYPES[type_name] = set([])

                    MISSING_TYPES[type_name].add(
                        path)

                    value_schema[key] = 'any'

        return value_schema

    def infer_schema(config, path=()) -> dict:
        '''Translate default values into corresponding bigraph-schema type declarations.'''
        ports = {}

        for key, value in config.items():
            ports[key] = infer_representation(
                value,
                path + (key,))

        return ports

    def find_defaults(params: dict) -> dict:
        '''Extract inner dict _default values from an arbitrarily-nested `params` input.'''
        result = {}
        for key, value in params.items():
            if isinstance(value, dict):
                nested_result = find_defaults(value)
                if '_default' in value and not nested_result:
                    val = value['_default']
                    # if isinstance(val, Quantity):
                    #     val = val.to_tuple()[0]
                    result[key] = val
                elif nested_result:
                    result[key] = nested_result

        return result

    def collapse_defaults(d):
        '''Returns a dict whose keys match that of d, except replacing innermost values (v) with their corresponding _default declarations.
        Used for migration.
        '''
        if isinstance(d, dict):
            if '_default' in d:
                return d['_default']
            else:
                return {k: collapse_defaults(v) for k, v in d.items()}
        else:
            return d
    return (
        Any,
        Composite,
        ECOLI_TYPES,
        EcoliSim,
        Engine,
        Path,
        PbgProcess,
        ProcessTypes,
        SimConfig,
        datetime,
        gc,
        infer_schema,
        json,
        not_a_process,
        parse,
        pickle,
        warnings,
    )


@app.cell
def _(
    Any,
    Composite,
    ECOLI_TYPES,
    EcoliSim,
    Engine,
    Path,
    PbgProcess,
    ProcessTypes,
    SimConfig,
    datetime,
    gc,
    json,
    not_a_process,
    parse,
    pickle,
    warnings,
):
    def create_config(experiment_id: str | None = None, path: Path | None = None, **parameters) -> SimConfig:
        config = SimConfig()

        if path is not None:
            with open(path, 'r') as fp:
                existing = json.load(fp)
            existing.update(parameters)
            config.update_from_dict(existing)
            return config.to_dict()

        assert experiment_id is not None, "You must either pass a path or create a new experiment"
        config.update_from_dict({
            "experiment_id": experiment_id,
            "fail_at_max_duration": False,
            "emitter": "timeseries",
            "log_updates": True,
            "raw_output": True,
            "sim_data_path": "kb/simData.cPickle"
        })
        return config

    DEFAULT_CONFIG = create_config(path=str(
        Path(__file__).parent.parent / "ecoli_configs/single_cell.json"
    ))

    class VEcoliProcess(PbgProcess):
        config_schema = {
            "config_path": "maybe[string]",
            "cell_id": "maybe[string]",
            "sim_config": {
                "_type": "tree[any]",
                "_default": DEFAULT_CONFIG
            }
        }

        def initialize(self, config) -> None:
            # config_path = config.get('config_path', None)
            # sim_config = config.get("sim_config", None)
            # self.sim: EcoliSim = initialize_ecoli(config_path=config_path) if config_path is not None \
            #     else initialize_ecoli(sim_config=SimConfig(config=config["sim_config"]))

            config_path = config['config_path']
            self.sim: EcoliSim = initialize_ecoli(config_path=config_path)
            self.cell_id = config.get("cell_id", self.sim.agent_id)
            # self.ports_schema = infer_schema(self.initial_state(), path=())
            self.ports_schema = {
                "environment": {
                    "cells": {
                        f"{self.cell_id}": "any"
                    }
                }
            }
            self.ports_schema = {"environment": "any"}
            print(f'created ports schema!:\n{self.ports_schema}')

        def initial_state(self):
            state = {
                "environment": {
                    "cells": {
                        f"{self.cell_id}": query_engine(self.sim)  # self.sim.generated_initial_state
                    }
                }
            }
            print(f'Exported initial state!')

        def inputs(self) -> dict[str, Any]:
            return self.ports_schema

        def outputs(self) -> dict[str, Any]:
            return self.ports_schema

        def update(self, state, interval) -> dict[str, Any]:
            print(f'Running update on {interval}')
            # TODO: option A or B?:
            # option A: export format to xarray
            # output_i = update_vecoli(sim=self.sim, interval=interval)
            # cell_datatree: xr.DataTree = export_metabolism(self.sim)
            # output_i = cell_datatree.to_dict()

            # option B: return the unformatted data directly
            engine: Engine = self.sim.ecoli_experiment
            if engine is None:
                raise RuntimeError(
                    "Build the composite by calling build_ecoli() \
                    before updating!"
                )

            # run the simulation (TODO: 1A or 1B?)
            # option 1A
            self.sim.update_experiment(interval)
            print(f'Running with {self.sim.ecoli_experiment.global_time}')

            # option 1B
            # engine.update(interval)
            # return/set new state (TODO: 2A or 2B or 2C?)
            # option 2A:
            def query_reduced(sim: EcoliSim):
                query = []
                agents = sim.query()["agents"].keys()
                for agent in agents:
                    query.extend(
                        [
                            ("agents", agent, "listeners", "fba_results"),  # Do we want to expose more?
                            ("agents", agent, "listeners", "mass"),
                            ("agents", agent, "bulk"),
                        ]
                    )
                # return self.query() for all data
                return sim.query(query)

            getter = query_engine

            # return serialize_port(y_i)
            return {
                "environment": {
                    "cells": {
                        f"{self.cell_id}": getter(self.sim)
                    }
                }
            }

    def query_engine(sim: EcoliSim):
        return sim.ecoli_experiment.state.get_value(condition=not_a_process)

    def query_all(sim: EcoliSim):
        return sim.query()

    def serialize_port(unserializable):
        return pickle.dumps(unserializable)

    def deserialize_port(serialized):
        return pickle.loads(serialized)

    def initialize_ecoli(config_path: str | None = None, sim_config: SimConfig | None = None) -> EcoliSim:
        self: EcoliSim = new_simulation(config_path=config_path, config=sim_config)

        # validate initialization
        if self.ecoli is None:
            raise RuntimeError(
                "Build the composite by calling build_ecoli() \
                before calling run()."
            )

        # initialize experiment config
        metadata = self.get_metadata()
        metadata["output_metadata"] = self.output_metadata()
        # make the experiment
        if isinstance(self.emitter, str):
            self.emitter_config = {"type": self.emitter}
            if self.emitter_arg is not None:
                for key, value in self.emitter_arg.items():
                    self.emitter_config[key] = value
            if self.emitter == "parquet":
                raise RuntimeError(
                    "You cannot specify a parquet emitter for now..."
                )
        experiment_config = {
            "description": self.description,
            "metadata": metadata,
            "processes": self.ecoli.processes,
            "steps": self.ecoli.steps,
            "flow": self.ecoli.flow,
            "topology": self.ecoli.topology,
            "initial_state": self.generated_initial_state,
            "progress_bar": self.progress_bar,
            "emit_topology": self.emit_topology,
            "emit_processes": self.emit_processes,
            "emit_config": self.emit_config,
            "emitter": self.emitter_config,
            "initial_global_time": self.initial_global_time,
        }
        if self.experiment_id:
            # Store backup of base experiment ID,
            # in case multiple experiments are run in a row
            # with suffix_time = True.
            if not self.experiment_id_base:
                self.experiment_id_base = self.experiment_id
            if self.suffix_time:
                self.experiment_id = datetime.now().strftime(
                    f"{self.experiment_id_base}_%Y%m%d-%H%M%S"
                )
            # Special characters can break Hive partitioning so do not allow them
            if self.experiment_id != parse.quote_plus(self.experiment_id):
                raise TypeError(
                    "Experiment ID cannot contain special characters"
                    f"that change the string when URL quoted: {self.experiment_id}"
                    f" != {parse.quote_plus(self.experiment_id)}"
                )
            experiment_config["experiment_id"] = self.experiment_id
        experiment_config["profile"] = self.profile

        # configure Engine
        # Since unique numpy updater is an class method, internal
        # deepcopying in vivarium-core causes this warning to appear
        warnings.filterwarnings(
            "ignore",
            message="Incompatible schema "
                    "assignment at .+ Trying to assign the value <bound method "
                    r"UniqueNumpyUpdater\.updater .+ to key updater, which already "
                    r"has the value <bound method UniqueNumpyUpdater\.updater",
        )
        self.ecoli_experiment = Engine(**experiment_config)
        # Only emit designated stores if specified
        if self.config["emit_paths"]:
            self.ecoli_experiment.state.set_emit_values([tuple()], False)
            self.ecoli_experiment.state.set_emit_values(
                self.config["emit_paths"],
                True,
            )
        # Clean up unnecessary references
        # self.generated_initial_state = None
        # self.ecoli_experiment.initial_state = None
        # del metadata, experiment_config
        # self.ecoli = None
        return self

    def update_vecoli(sim: EcoliSim, interval: float) -> dict:
        # ensure proper initialization
        engine: Engine = sim.ecoli_experiment
        if engine is None:
            raise RuntimeError(
                "Build the composite by calling build_ecoli() \
                before updating!"
            )
        # refresh memory (possibly)
        gc.collect()

        # run the simulation (TODO: 1A or 1B?)

        # option 1A
        sim.update_experiment(interval)

        # option 1B
        # engine.update(interval)

        # return/set new state (TODO: 2A or 2B?)

        # option 2A:
        def get_reduced(sim: EcoliSim):
            query = []
            agents = sim.query()["agents"].keys()
            for agent in agents:
                query.extend(
                    [
                        ("agents", agent, "listeners", "fba_results"),  # Do we want to expose more?
                        ("agents", agent, "listeners", "mass"),
                        ("agents", agent, "bulk"),
                    ]
                )
            # return self.query() for all data
            return sim.query(query)

        # option 2B:
        def get_all(sim: EcoliSim):
            return sim.state.get_value(condition=not_a_process)

        getter = get_reduced
        return getter(sim)

    def new_simulation(config_path: str | None = None, config: SimConfig | None = None, **config_overrides) -> EcoliSim:
        def getsim(config, config_path):
            if config_path is not None:
                if not Path(config_path).exists():
                    raise ValueError(f'You must pass a valid config path, not: {config_path}')
                return EcoliSim.from_file(filepath=config_path)
            if config is not None:
                return EcoliSim(config.to_dict())
            return None

        sim: EcoliSim | None = getsim(config=config, config_path=config_path)
        if sim is None:
            raise RuntimeError("You must pass either a valid config path or config instance")

        # parameterize sim config
        if len(config_overrides):
            sim.config.update(config_overrides)

        # build vivarium ecoli
        sim.build_ecoli()
        print('Ecoli has been built!')
        return sim

    def vecoli_process() -> None:
        core = ProcessTypes()
        config_path = "configs/single_cell.json"
        cell_id = "ecoli0"
        config = {
            "config_path": config_path,
            "cell_id": cell_id
        }

        vecoli = VEcoliProcess(
            config=config,
            core=core
        )
        x = vecoli.initial_state()
        assert "env" in x.keys()

        y = vecoli.update(state=x, interval=1.0)
        assert "ecoli0" in y['env']['cells'].keys()

    def compose_vecoli() -> None:
        from process_bigraph.emitter import gather_emitter_results
        def get_core():
            core = ProcessTypes()
            core.register_multiple(ECOLI_TYPES)
            return core

        core = get_core()
        core.process_registry.register('vecoli-process', VEcoliProcess)
        config_path = str(
            Path(__file__).parent.parent / "ecoli_configs/single_cell.json"
        )
        cell_id = "ecoli0"
        config = {
            "config_path": config_path,
            "cell_id": cell_id
        }
        state = {
            "ecoli0": {
                "_type": 'process',
                "address": "local:vecoli-process",
                "config": config,
                "inputs": {
                    "environment": ["env0"]
                },
                "outputs": {
                    "environment": ["env0"]
                }
            },
            "ecoli1": {
                "_type": 'process',
                "address": "local:vecoli-process",
                "config": config,
                "inputs": {
                    "environment": ["env1"]
                },
                "outputs": {
                    "environment": ["env1"]
                }
            }
        }
        composite = Composite(
            config={'state': state},
            core=core
        )
        state = composite.state
        composite.update(state=state, interval=1)
    return compose_vecoli, query_engine


@app.cell
def _(compose_vecoli):
    compose_vecoli()
    return


app._unparsable_cell(
    r"""
    :def get_core():
        core = ProcessTypes()
        core.register_multiple(ECOLI_TYPES)
        return core 

    core = get_core()
    config_path = str(
        Path(__file__).parent.parent / \"ecoli_configs/single_cell.json\"
    )
    cell_id = \"ecoli0\"
    config = {
        \"config_path\": config_path,
        \"cell_id\": cell_id
    }

    vecoli = VEcoliProcess(config=config, core=core)
    """,
    name="_"
)


@app.cell
def _(infer_schema, vecoli):
    vecoli_port_schema = infer_schema(vecoli.initial_state(), path=())
    return


@app.cell
def _(vecoli):
    x = vecoli.initial_state()
    y = vecoli.update(state=x, interval=0.5)
    return (y,)


@app.cell
def _(infer_schema, vecoli):
    infer_schema(vecoli.initial_state(), path=())
    return


@app.cell
def _(infer_schema, vecoli):
    infer_schema(vecoli.sim.generated_initial_state, path=())
    return


@app.cell
def _(vecoli):
    vecoli.sim.ecoli_experiment.global_time
    return


@app.cell
def _(vecoli, y):
    y2 = vecoli.update(state=y, interval=0.5)
    return


@app.cell
def _(vecoli):
    vecoli.sim.generated_initial_state
    return


@app.cell
def _(core):
    core.types()['env']
    return


@app.cell
def _(vecoli):
    vecoli.sim.build_ecoli()
    return


@app.cell
def _(vecoli):
    vecoli.sim.generated_initial_state
    return


@app.cell
def _(vecoli):
    vecoli.sim.ecoli_experiment.state.get_values(paths={"agents": ("agents",)})
    return


@app.cell
def _(query_engine, vecoli):
    query_engine(vecoli.sim)
    return


@app.cell
def _(Composite, PbgProcess, get_core):
    class Summation(PbgProcess):
        config_schema = {
            "k": "float"
        }

        def initialize(self, config):
            self.k = config['k']
            self.port_schema = {
                "env": {"x": "float", "y": "float"}
            }

        def inputs(self):
            return self.port_schema

        def outputs(self):
            return self.port_schema

        def update(self, state, interval):
            x = state['env']['x']
            return {
                "x": x,
                "y": x + interval
            }

    corereg = get_core()
    corereg.process_registry.register('summation-process', Summation)
    conf = {"k": 0.221111}
    state = {
        "adder": {
            "_type": 'process',
            "address": "local:summation-process",
            "config": conf,
            "inputs": {
                "env": ["env_store"]
            },
            "outputs": {
                "env": ["env_store"]
            }
        }
    }
    composite = Composite(
        config={'state': state},
        core=corereg
    )
    return (composite,)


@app.cell
def _(composite):
    import marimo as mo

    get_state, set_state = mo.state(composite.state)
    return get_state, set_state


@app.cell
def _(get_state):
    comp_state = get_state()
    comp_state
    return


@app.cell
def _(set_state):
    def run(comp):
        comp.run(1)
        set_state(comp.state)
    return (run,)


@app.cell
def _(composite, run):
    for t in range(1, 12):
        run(composite)
    return


@app.cell
def _():
    return


if __name__ == "__main__":
    app.run()
