"""
Interface for configuring and running **single-cell** E. coli simulations.

.. note::
    Simulations can be configured to divide through this interface, but
    full colony-scale simulations are best run using the
    :py:mod:`~ecoli.experiments.ecoli_engine_process` module for efficient
    multiprocessing.
"""
# mypy: disable-error-code=attr-defined

import argparse
import copy
import os
import pstats
import subprocess
import sys
import json
import warnings
from datetime import datetime
from typing import Optional, Dict, Any
from urllib import parse

import numpy as np
from fsspec import open as fsspec_open
from vivarium.core.engine import Engine
from vivarium.core.composer import deep_merge
from vivarium.core.process import Process
from vivarium.core.serialize import deserialize_value, serialize_value
from vivarium.library.dict_utils import deep_merge_check
from vivarium.library.topology import inverse_topology
from vivarium.library.topology import assoc_path, get_in
from ecoli.library.logging_tools import write_json
from wholecell.utils.filepath import cloud_path_join
import ecoli.composites.ecoli_master

# Environment composer for spatial environment sim
import ecoli.composites.environment.lattice

from ecoli.processes import process_registry
from ecoli.processes.cell_division import DivisionDetected
from ecoli.processes.registries import topology_registry

from configs import CONFIG_DIR_PATH
from ecoli.library.parquet_emitter import ParquetEmitter
from ecoli.library.schema import not_a_process

from wholecell.utils.filepath import ROOT_PATH

from runscripts.workflow import LIST_KEYS_TO_MERGE


class TimeLimitError(RuntimeError):
    """Error raised when ``fail_at_max_duration`` is True and simulation
    reaches ``max_duration``."""

    pass


def tuplify_topology(topology: dict[str, Any]) -> dict[str, Any]:
    """JSON files allow lists but do not allow tuples. This function
    transforms the list paths in topologies loaded from JSON into
    standard tuple paths.

    Args:
        topology: Topology to recursively iterate over, converting
            all paths to tuples

    Returns:
        Topology with tuple paths (e.g. ``['bulk']`` turns into ``('bulk',)``)
    """
    tuplified_topology: dict[str, Any] = {}
    for k, v in topology.items():
        if isinstance(v, dict):
            tuplified_topology[k] = tuplify_topology(v)
        elif isinstance(v, str):
            tuplified_topology[k] = (v,)
        else:
            tuplified_topology[k] = tuple(v)
    return tuplified_topology


def get_git_revision_hash() -> str:
    """Returns current Git hash for model repository to include in metadata
    that is emitted when starting a simulation.

    First tries to run git command if git is installed.
    If that fails, tries to get the value from IMAGE_GIT_HASH environment variable.
    Raises an error if both methods fail.
    """
    # Try to run git command
    try:
        return (
            subprocess.check_output(["git", "-C", CONFIG_DIR_PATH, "rev-parse", "HEAD"])
            .decode("utf-8")
            .strip()
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass  # Continue to next method if git command fails

    # Try to get from environment variable
    env_hash = os.environ.get("IMAGE_GIT_HASH")
    if env_hash:
        return env_hash.strip()

    # Raise error if both methods fail
    raise RuntimeError(
        "Could not determine Git hash: git command failed and IMAGE_GIT_HASH "
        "environment variable is not set. Either install git, set the environment "
        "variable, or run from a container with this information."
    )


def get_git_diff() -> str:
    """Returns Git diff of model repository to include in metadata that is
    emitted when starting a simulation.

    First tries to run git command if git is installed.
    If that fails, tries to read the diff from source-info/git-diff.txt file.
    Raises an error if both methods fail.
    """
    try:
        return (
            subprocess.check_output(["git", "-C", CONFIG_DIR_PATH, "diff", "HEAD"])
            .decode("utf-8")
            .strip()
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass  # Continue to next method if git command fails

    # Try to read from git-diff.txt file
    diff_file_path = os.path.join(ROOT_PATH, "source-info", "git_diff.txt")
    if os.path.exists(diff_file_path):
        try:
            with open(diff_file_path, "r") as f:
                return f.read().strip()
        except IOError:
            pass  # Continue to next method if file read fails

    # Raise error if both methods fail
    raise RuntimeError(
        "Could not determine Git diff: git command failed and "
        f"{diff_file_path} does not exist or cannot be read. "
        "Either install git, create the git-diff.txt file, "
        "or run from a container with this information."
    )


def report_profiling(stats: pstats.Stats) -> None:
    """Prints out a summary of profiling statistics when ``profile`` option
    is ``True`` in the config given to
    :py:class:`~ecoli.experiments.ecoli_master_sim.EcoliSim`

    Args:
        stats: Profiling statistics."""
    _, stats_keys = stats.get_print_list(
        ("(next_update)|(calculate_request)|(evolve_state)",)
    )
    summed_stats: dict[tuple[str, str, str], int] = {}
    for key in stats_keys:
        key_stats = stats.stats[key]
        _, _, _, cumtime, _ = key_stats
        path, line, func = key.split(" ")
        path = os.path.basename(path)
        summed_stats[(path, line, func)] = (
            summed_stats.get((path, line, func), 0) + cumtime
        )
    summed_stats_inverse_map = {time: key for key, time in summed_stats.items()}
    print("\nPer-process profiling:\n")
    for time in sorted(summed_stats_inverse_map.keys())[::-1]:
        path, line, func = summed_stats_inverse_map[time]
        print(f"{path}:{line} {func}(): {time}")
    print("\nOverall Profile:\n")
    stats.sort_stats("cumtime").print_stats(20)


def parse_key_value_args(args_list: list[str]) -> dict[str, str]:
    """Parses key-value pairs specified as strings of the form ``key=value``
    via CLI. See ``emitter_arg`` option in
    :py:class:`~ecoli.experiments.ecoli_master_sim.SimConfig`.

    Args:
        argument_string: Key-value pair as a string of the form ``key=value``

    Returns:
        ``[key, value]``
    """
    # Create an empty dictionary to store the parsed key-value pairs
    parsed_dict = {}
    for item in args_list:
        if "=" in item:
            key, value = item.split("=", 1)
            parsed_dict[key] = value
        else:
            raise ValueError(f"Argument '{item}' is not in the form key=value")
    return parsed_dict


def prepare_save_state(state: dict[str, Any]) -> None:
    """Prepares simulation state to be saved to a JSON file by pruning
    unsaveable values and adding necessary metadata. Mutates in-place.
    """
    # Processes can't be serialized
    del state["process"]
    # Bulk random state can't be serialized
    del state["allocator_rng"]
    # Save bulk and unique dtypes
    state["bulk_dtypes"] = str(state["bulk"].dtype)
    state["unique_dtypes"] = {}
    for name, mols in state["unique"].items():
        state["unique"][name] = np.asarray(mols)
        state["unique_dtypes"][name] = str(mols.dtype)


class SimConfig:
    #: Path to default JSON configuration file.
    default_config_path = os.path.join(CONFIG_DIR_PATH, "default.json")

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        parser: Optional[argparse.ArgumentParser] = None,
    ):
        """Stores configuration options for a simulation. Has dictionary-like
        interface (e.g. bracket indexing, get, keys).

        Attributes:
            config: Current configuration.
            parser: Argument parser for the command-line interface.

        Args:
            config: Configuration options. If not provided, the default
                configuration is loaded from the file path
                :py:data:`~ecoli.experiments.ecoli_master_sim.SimConfig.default_config_path`.
            parser: Useful for scripts that leverage the inheritance features
                of the JSON config files but want to have their own CLI args
                for clarity.
        """
        self._config = config or {}
        if not self._config:
            self.update_from_json(self.default_config_path)

        self.parser = parser
        if self.parser is None:
            self.parser = argparse.ArgumentParser(description="ecoli_master")
            self.parser.add_argument(
                "--config",
                action="store",
                default=self.default_config_path,
                help=(
                    "Path to configuration file for the simulation. "
                    "All key-value pairs in this file will be applied on top "
                    f"of the options defined in {self.default_config_path}."
                ),
            )
            self.parser.add_argument(
                "--experiment_id",
                action="store",
                help=(
                    "ID for this experiment. A UUID will be generated if "
                    'this argument is not used and "experiment_id" is null '
                    "in the configuration file."
                ),
            )
            self.parser.add_argument(
                "--emitter",
                action="store",
                choices=["timeseries", "print", "parquet", "null"],
                help=(
                    "Emitter to use. Timeseries uses RAMEmitter, print emits to"
                    " stdout, and parquet (recommended) saves output to a"
                    " directory on disk specified using --emitter-arg (e.g."
                    " --emitter-arg out_dir='out')"
                ),
            )
            self.parser.add_argument(
                "--emitter_arg",
                action="store",
                nargs="*",
                help=(
                    "Key-value pairs, separated by `=`, to include in emitter config."
                ),
            )
            self.parser.add_argument(
                "--seed", action="store", type=int, help="Random seed."
            )
            self.parser.add_argument(
                "--max_duration",
                action="store",
                type=float,
                help="Time to run the simulation for.",
            )
            self.parser.add_argument(
                "--generations",
                action="store",
                type=int,
                help="Number of generations to run the simulation for.",
            )
            self.parser.add_argument(
                "--log_updates",
                action=argparse.BooleanOptionalAction,
                help=(
                    "Save updates from each process if this flag is set, "
                    "e.g. for use with blame plot."
                ),
            )
            self.parser.add_argument(
                "--raw_output",
                action=argparse.BooleanOptionalAction,
                help=(
                    "Whether to return data in raw format (dictionary"
                    " where keys are times, values are states). Requires"
                    " timeseries emitter (RAMEmitter)."
                ),
            )
            self.parser.add_argument(
                "--agent_id", action="store", type=str, help="Agent ID."
            )
            self.parser.add_argument(
                "--sim_data_path",
                help="Path to the sim_data (pickle from ParCa) to use for this experiment.",
            )
            self.parser.add_argument(
                "--profile",
                action=argparse.BooleanOptionalAction,
                help="Print profiling information at the end.",
            )
            self.parser.add_argument(
                "--initial_state_file",
                action="store",
                help='Name of initial state file (omit ".json" extension) under data/',
            )
            self.parser.add_argument(
                "--initial_state_overrides",
                action="store",
                nargs="*",
                help='Name of initial state overrides (omit ".json" extension) under '
                "data/overrides",
            )
            self.parser.add_argument(
                "--daughter_outdir",
                action="store",
                help="Directory in which to store daughter cell state JSONs.",
            )
            self.parser.add_argument(
                "--variant", action="store", type=int, help="Name of variant."
            )
            self.parser.add_argument(
                "--lineage_seed",
                action="store",
                type=int,
                help="Seed used for first cell in lineage.",
            )
            self.parser.add_argument(
                "--initial_global_time",
                type=float,
                action="store",
                help="Initial time in context of whole lineage.",
            )
            self.parser.add_argument(
                "--fail_at_max_duration",
                action=argparse.BooleanOptionalAction,
                help="Simulation will raise TimeLimitException upon reaching max_duration.",
            )
            self.parser.add_argument(
                "--composite_checkpoint_at",
                type=float,
                action="store",
                help="Run composite to this absolute sim-time, save bundle "
                     "to --composite_checkpoint_dir, then exit. Used for "
                     "pre-division iteration.",
            )
            self.parser.add_argument(
                "--composite_checkpoint_dir",
                action="store",
                help="Directory for the composite checkpoint bundle (used "
                     "with --composite_checkpoint_at).",
            )

    @staticmethod
    def merge_config_dicts(d1: dict[str, Any], d2: dict[str, Any]) -> None:
        """Helper function to safely merge two config dictionaries. Config
        options whose values are lists (e.g. ``save_times``, ``add_processes``,
        etc.) are handled separately so that the lists from each config are
        concatenated in the merged output.

        Args:
            d1: Config to mutate by merging in ``d2``.
            d2: Config to merge into ``d1``.
        """
        for key in LIST_KEYS_TO_MERGE:
            d2.setdefault(key, [])
            d2[key].extend(d1.get(key, []))
            if key == "engine_process_reports":
                d2[key] = [tuple(path) for path in d2[key]]
            # Ensures there are no duplicates in d2
            d2[key] = list(set(d2[key]))
            d2[key].sort()
        deep_merge(d1, d2)

    def update_from_json(self, path: str) -> None:
        """Loads config dictionary from file path ``path`` and merges it into
        the currently loaded config.

        Args:
            path: The file path of the JSON config to merge in. Supports
                local paths and cloud URIs (s3://, gs://).
        """
        with fsspec_open(path, "r") as f:
            new_config = json.load(f)
        new_config = deserialize_value(new_config)
        for config_name in new_config.get("inherit_from", []):
            config_path = os.path.join(CONFIG_DIR_PATH, config_name)
            self.update_from_json(config_path)
        self.merge_config_dicts(self._config, new_config)

    def update_from_cli(self):
        """Parses command-line options defined in ``__init__`` and
        updates config.
        """
        args = self.parser.parse_args()
        if args.emitter_arg is not None:
            args.emitter_arg = parse_key_value_args(args.emitter_arg)
        # First load in a configuration file, if one was specified.
        config_path = getattr(args, "config", None)
        if config_path:
            self.update_from_json(config_path)
        # Then override the configuration file with any command-line
        # options.
        cli_config = {
            key: value
            for key, value in vars(args).items()
            if value is not None and key != "config"
        }
        self.merge_config_dicts(self._config, cli_config)

    def update_from_dict(self, dict_config: dict[str, Any]):
        """Updates loaded config with user-specified dictionary."""
        self.merge_config_dicts(self._config, dict_config)

    def __getitem__(self, key):
        return self._config[key]

    def get(self, key, default=None):
        return self._config.get(key, default)

    def __setitem__(self, key, val):
        self._config[key] = val

    def keys(self):
        return self._config.keys()

    def to_dict(self):
        return copy.deepcopy(self._config)


class EcoliSim:
    def __init__(self, config: dict[str, Any]):
        """Main interface for running single-cell E. coli simulations. Typically
        instantiated using one of two methods:

        1. :py:meth:`~ecoli.experiments.ecoli_master_sim.EcoliSim.from_file`
        2. :py:meth:`~ecoli.experiments.ecoli_master_sim.EcoliSim.from_cli`

        Config options can be modified after the creation of an
        :py:class:`~ecoli.experiments.ecoli_master_sim.EcoliSim` object
        in one of two ways.

        1. ``sim.max_duration = 100``
        2. ``sim.config['max_duration'] = 100``

        Args:
            config: Automatically generated from
                :py:class:`~ecoli.experiments.ecoli_master_sim.SimConfig` when
                :py:class:`~ecoli.experiments.ecoli_master_sim.EcoliSim` is
                instantiated using
                :py:meth:`~ecoli.experiments.ecoli_master_sim.EcoliSim.from_file`
                or :py:meth:`~ecoli.experiments.ecoli_master_sim.EcoliSim.from_cli`
        """
        # Do some datatype pre-processesing
        config["processes"] = {process: None for process in config["processes"]}

        # Keep track of base experiment id
        # in case multiple simulations are run with suffix_time = True.
        self.experiment_id_base = config["experiment_id"]
        self.config = config
        self.ecoli = None
        """vivarium.core.composer.Composite: Contains the fully instantiated 
        processes, steps, topologies, and flow necessary to run simulation. 
        Generated by 
        :py:meth:`~ecoli.experiments.ecoli_master_sim.EcoliSim.build_ecoli` and 
        cleared when :py:meth:`~ecoli.experiments.ecoli_master_sim.EcoliSim.run` 
        is called to potentially free up memory after division."""
        self.generated_initial_state = None
        """dict: Fully populated initial state for simulation. Generated by 
        :py:meth:`~ecoli.experiments.ecoli_master_sim.EcoliSim.build_ecoli` and 
        cleared when :py:meth:`~ecoli.experiments.ecoli_master_sim.EcoliSim.run` 
        is called to potentially free up memory after division."""
        self.ecoli_experiment = None
        """vivarium.core.engine.Engine: Engine that runs the simulation. 
        Instantiated by 
        :py:meth:`~ecoli.experiments.ecoli_master_sim.EcoliSim.run`."""

        # Unpack config using Descriptor protocol:
        # All of the entries in config are translated to properties
        # (of EcoliSim class) that get/set an entry in self.config.
        #
        # For example:
        #
        # >> sim = EcoliSim.from_file()
        # >> sim.max_duration
        #    10
        # >> sim.config['max_duration']
        #    10
        # >> sim.max_duration = 100
        # >> sim.config['max_duration']
        #    100

        class ConfigEntry:
            def __init__(self, name):
                self.name = name

            def __get__(self, sim, type=None):
                return sim.config[self.name]

            def __set__(self, sim, value):
                sim.config[self.name] = value

        for attr in self.config.keys():
            config_entry = ConfigEntry(attr)
            setattr(EcoliSim, attr, config_entry)

    @staticmethod
    def from_file(filepath=CONFIG_DIR_PATH + "default.json") -> "EcoliSim":
        """Used to instantiate
        :py:class:`~ecoli.experiments.ecoli_master_sim.EcoliSim` with
        a config loaded from the JSON at ``filepath`` by
        :py:class:`~ecoli.experiments.ecoli_master_sim.SimConfig`.

        Args:
            filepath: String filepath of JSON file with config options to
                apply on top of the options laid out in the default JSON
                located at the default value for ``filepath``.
        """
        config = SimConfig()
        config.update_from_json(filepath)
        return EcoliSim(config.to_dict())

    @staticmethod
    def from_cli() -> "EcoliSim":
        """Used to instantiate
        :py:class:`~ecoli.experiments.ecoli_master_sim.EcoliSim` with
        a config loaded from the command-line arguments parsed by
        :py:class:`~ecoli.experiments.ecoli_master_sim.SimConfig`.
        """
        config = SimConfig()
        config.update_from_cli()
        return EcoliSim(config.to_dict())

    def _retrieve_processes(
        self,
        processes: dict[str, str],
        add_processes: list[str],
        exclude_processes: list[str],
        swap_processes: dict[str, str],
    ) -> dict[str, Process]:
        """
        Retrieve process classes from
        :py:data:`~vivarium.core.registry.process_registry` (processes are
        registered in ``ecoli/processes/__init__.py``).

        Args:
            processes: Base list of process names to retrieve classes for
            add_processes: Additional process names to retrieve classes for
            exclude_processes: Process names to not retrieve classes for
            swap_processes: Mapping of process names to the names of the
                processes they should be swapped for. It is assumed that
                the swapped processes share the same topologies.

        Returns:
            Mapping of process names to process classes.
        """
        result = {}
        for process_name in list(processes.keys()) + list(add_processes):
            if process_name in exclude_processes:
                continue
            if process_name in swap_processes:
                process_name = swap_processes[process_name]
            process_class = process_registry.access(process_name)
            if not process_class:
                raise ValueError(
                    f"Unknown process with name {process_name}. "
                    "Did you call process_registry.register() in "
                    "ecoli/processes/__init__.py?"
                )
            result[process_name] = process_class

        return result

    def _retrieve_topology(
        self,
        topology: dict[str, dict[str, tuple[str]]],
        processes: list[str],
        swap_processes: dict[str, str],
        log_updates: bool,
    ) -> dict[str, dict[str, tuple[str]]]:
        """
        Retrieves topologies for processes from
        :py:data:`~ecoli.processes.registries.topology_registry`.

        Args:
            topology: Mapping of process names to user-specified topologies.
                Will be merged with topology from topology_registry, if exists.
            processes: List of process names for which to retrive topologies.
            swap_processes: Mapping of process names to the names of processes
                to swap them for. By default, the new processes are assumed to
                have the same topology as the processes they replaced. When
                this is not the case, users can add/modify the original process
                topology with custom values in ``topology`` under either the new
                or the old process name.
            log_updates: Whether to emit process updates. Adds topology for
                ``log_update`` port.

        Returns:
            Mapping of process names to process topologies.
        """
        result = {}
        original_processes = {v: k for k, v in swap_processes.items()}
        for process in processes:
            # Start from default topology if it exists
            original_process = (
                process
                if process not in swap_processes.values()
                else original_processes[process]
            )
            process_topology = topology_registry.access(original_process)
            if process_topology:
                process_topology = copy.deepcopy(process_topology)
            else:
                process_topology = {}
            # Allow the user to override default topology
            if original_process in topology.keys():
                deep_merge(
                    process_topology, tuplify_topology(topology[original_process])
                )
            # For swapped processes, do additional overrides if provided
            if process != original_process and process in topology.keys():
                deep_merge(process_topology, tuplify_topology(topology[process]))
            result[process] = process_topology

        return result

    def _retrieve_process_configs(
        self, process_configs: dict[str, dict[str, Any]], processes: list[str]
    ) -> dict[str, Any]:
        """
        Sets up process configs to be interpreted by
        :py:meth:`~ecoli.composites.ecoli_master.Ecoli.generate_processes_and_steps`.

        Args:
            process_configs: Mapping of process names to user-specified process
                configuration dictionaries.
            processes: List of process names to set up process config for.

        Returns:
            Mapping of process names to process configs.
        """
        result: dict[str, Any] = {}
        for process in processes:
            result[process] = process_configs.get(process)
            if result[process] is None:
                result[process] = "sim_data"
        return result

    def build_ecoli(self):
        """
        Creates the E. coli composite. **MUST** be called before calling
        :py:meth:`~ecoli.experiments.ecoli_master_sim.EcoliSim.run`.

        For all processes in ``config['processes']``:

        1. Retrieves process class from
        :py:data:`~vivarium.core.registry.process_registry`, which is
        populated in ``ecoli/processes/__init__.py``.

        2. Retrieves process topology from
        :py:data:`~ecoli.processes.registries.topology_registry` and merge
        with user-specified topology from ``config['topology']``, if applicable

        3. Retrieves process configs from ``config['process_configs']``
        if present, else indicate that process config should be loaded from
        pickled simulation data using
        :py:meth:`~ecoli.library.sim_data.LoadSimData.get_config_by_name`

        Adds spatial environment if ``config['spatial_environment']`` is
        ``True``. Spatial environment config options are loaded from
        ``config['spatial_environment_config`]``. See
        ``configs/spatial.json`` for an example.
        """
        # build processes, topology, configs
        self.processes = self._retrieve_processes(
            self.processes,
            self.add_processes,
            self.exclude_processes,
            self.swap_processes,
        )
        self.topology = self._retrieve_topology(
            self.topology, self.processes, self.swap_processes, self.log_updates
        )
        self.process_configs = self._retrieve_process_configs(
            self.process_configs, self.processes
        )

        # initialize the ecoli composer
        ecoli_composer = ecoli.composites.ecoli_master.Ecoli(self.config)

        # set path at which agent is initialized
        path = tuple()
        if self.divide or self.spatial_environment:
            path = (
                "agents",
                self.agent_id,
            )

        # get initial state
        initial_cell_state = ecoli_composer.initial_state()
        # If division or spatial environment is enabled,
        # ensure that inner cell state is nested at correct path.
        # Note: cell states loaded from saved JSONs may already
        # have the correct nesting (e.g. saved daughter cell states
        # from sims with spatial environment).
        if get_in(initial_cell_state, path) is None:
            initial_cell_state = assoc_path({}, path, initial_cell_state)

        # generate the composite at the path
        self.ecoli = ecoli_composer.generate(path=path)
        # Some processes define their own initial_state methods
        # Incoporate them into the generated initial state
        self.generated_initial_state = self.ecoli.initial_state(
            {"initial_state": initial_cell_state}
        )

        # merge a lattice composite for the spatial environment
        if self.spatial_environment:
            initial_state_config = self.spatial_environment_config.get(
                "initial_state_config"
            )
            environment_composite = ecoli.composites.environment.lattice.Lattice(
                self.spatial_environment_config
            ).generate()
            initial_environment = environment_composite.initial_state(
                initial_state_config
            )
            self.ecoli.merge(environment_composite)
            # In case initial state already contains environment state
            # (e.g. from a daughter cell state saved after division),
            # give priority to existing environment state
            self.generated_initial_state = deep_merge(
                initial_environment, self.generated_initial_state
            )

    def update_experiment(self, time_to_update: float = 0.0):
        """
        Runs the E. coli simulation for a specified amount of time. If the
        simulation reaches a division event and ``config['generations']`` is set,
        it will save the daughter cell states to JSON files in the directory
        specified by ``config['daughter_outdir']``. Also creates a file
        ``division_time.sh`` that, when executed, sets the environment variable
        ``division_time`` to the time at which division occurred (used in
        Nextflow workflow runs).
        """
        try:
            self.ecoli_experiment.update(time_to_update)
        except DivisionDetected:
            state = self.ecoli_experiment.state.get_value(condition=not_a_process)
            assert len(state["agents"]) == 2
            # Daughter state should include all of the additional
            # non-agent state (e.g. environment state)
            non_agent_state = {k: v for k, v in state.items() if k != "agents"}
            for i, (agent_id, agent_state) in enumerate(state["agents"].items()):
                prepare_save_state(agent_state)
                daughter_filename = f"daughter_state_{i}.json"
                daughter_path = cloud_path_join(self.daughter_outdir, daughter_filename)
                write_json(
                    daughter_path,
                    {**non_agent_state, "agents": {agent_id: agent_state}},
                )
                # Write daughter state URI to local file for Nextflow to read
                with open(f"daughter_state_{i}_uri.txt", "w") as f:
                    f.write(daughter_path)
            print(
                f"Divided at t = {self.ecoli_experiment.global_time} after "
                f"{self.ecoli_experiment.global_time - self.initial_global_time} sec."
            )
            # Nextflow workflows will source division time to determine
            # initial global time to use for daughter cells
            with open("division_time.sh", "w") as f:
                f.write(f"export division_time={self.ecoli_experiment.global_time}")
            # Tell Parquet emitter that simulation was successful
            if isinstance(self.ecoli_experiment.emitter, ParquetEmitter):
                self.ecoli_experiment.emitter.success = True
                self.ecoli_experiment.emitter.finalize()
            # Exit so that EcoliSim.run() does not raise TimeLimitError
            sys.exit()
        except:  # noqa: E722
            # Finish writing any buffered emits to Parquet files if the simulation
            # encounters any error (including KeyboardInterrupt)
            # We use a bare except instead of finally because we don't want to
            # run finalize() every time update_experiment is called to advance to
            # save times in save_states()
            if isinstance(self.ecoli_experiment.emitter, ParquetEmitter):
                self.ecoli_experiment.emitter.finalize()
            raise

    def save_states(self):
        """
        Runs the simulation while saving the states of specific
        timesteps to files named ``data/vivecoli_t{time}.json``. Invoked by
        :py:meth:`~ecoli.experiments.ecoli_master_sim.EcoliSim.run`
        if ``config['save'] == True``. State is saved as a JSON that
        can be reloaded into a simulation as described in
        :py:meth:`~ecoli.composites.ecoli_master.Ecoli.initial_state`.
        """
        for time in self.save_times:
            if time > self.max_duration:
                raise ValueError(
                    f"Config contains save_time ({time}) > total "
                    f"time ({self.max_duration})"
                )

        for i in range(len(self.save_times)):
            if i == 0:
                time_to_next_save = self.save_times[i]
            else:
                time_to_next_save = self.save_times[i] - self.save_times[i - 1]
            self.update_experiment(time_to_next_save)
            time_elapsed = self.save_times[i]
            state = self.ecoli_experiment.state.get_value(condition=not_a_process)
            if self.divide:
                for agent_state in state["agents"].values():
                    prepare_save_state(agent_state)
            else:
                prepare_save_state(state)
            write_json("data/vivecoli_t" + str(time_elapsed) + ".json", state)
            print("Finished saving the state at t = " + str(time_elapsed))
        time_remaining = self.max_duration - self.save_times[-1]
        if time_remaining:
            self.update_experiment(time_remaining)

    def _run_composite(self):
        """Run via process-bigraph Composite, built directly from sim_data.

        Does NOT require ``build_ecoli()`` to have been called first; the
        composite document is assembled by
        :py:func:`ecoli.composites.ecoli_composite.build_composite_native`
        without instantiating the v1 vivarium composer.

        Division and daughter handoff mirror v1: if
        ``config['initial_state_file']`` is set, ``_get_initial_state``
        loads the daughter cell state from a v1-style single JSON via
        fsspec (cloud-aware) and overlays it onto the freshly-built
        document. On division, one ``daughter_state_{i}.json`` per
        daughter is written under ``config['daughter_outdir']`` via
        fsspec for the next workflow generation to pick up.
        """
        self._run_composite_inner()

    def _run_composite_inner(self):
        from ecoli.composites.ecoli_composite import build_composite_native
        from ecoli.library.bigraph_types import ECOLI_TYPES
        from ecoli.library.parquet_emitter import ParquetEmitter
        from process_bigraph import Composite
        from process_bigraph.types.process import (
            register_types as register_process_bigraph_types)
        from bigraph_schema import Core, BASE_TYPES
        import time as _time

        # Resolve process classes / topologies / configs from registries.
        # These cheap helpers populate self.config[...] in place via the
        # ConfigEntry descriptors and are required by build_composite_native.
        self.processes = self._retrieve_processes(
            self.processes,
            self.add_processes,
            self.exclude_processes,
            self.swap_processes,
        )
        self.topology = self._retrieve_topology(
            self.topology, self.processes, self.swap_processes, self.log_updates
        )
        self.process_configs = self._retrieve_process_configs(
            self.process_configs, self.processes
        )

        # Build the core directly with exactly the types this sim
        # needs, instead of going through ``allocate_core`` whose
        # ``discover_packages`` scans every installed distribution
        # for process libraries (~0.6s/sim wasted on cold start).
        core = Core(BASE_TYPES)
        register_process_bigraph_types(core)
        core.register_types(ECOLI_TYPES)

        # ``initial_state_file`` may point at either a v1-style single
        # JSON (daughter handoff between gens) or a local v2 bundle
        # directory (pre-division iteration via composite_checkpoint_at).
        # Bundles are detected by the presence of document.json; cloud
        # URIs always go through the JSON path (save_bundle is local-
        # only and bundle handoff to S3 is unsupported).
        initial_state_file = self.config.get("initial_state_file")
        is_local_bundle = (
            initial_state_file
            and os.path.isdir(initial_state_file)
            and os.path.isfile(
                os.path.join(initial_state_file, 'document.json')))

        if is_local_bundle:
            from process_bigraph.bundle import load_bundle
            from ecoli.composites.ecoli_composite import (
                reseed_loaded_bundle, _reseed_allocator_rng)
            print(f"Loading composite from bundle {initial_state_file}...",
                  flush=True)
            t0 = _time.time()
            document = load_bundle(initial_state_file, as_numpy=True)
            agent_id = self.config.get('agent_id', '0')
            cli_seed = int(self.config.get('seed', 0))
            sim_data_path = self.config['sim_data_path']
            # ``skip_reseed_on_load=True`` for mid-cycle (within-gen)
            # resume: preserve the saved rng_state on every process
            # and the saved allocator_rng so the resumed run advances
            # the SAME RNG sequence the continuous run would have. The
            # default (False) is correct for daughter handoff between
            # generations — v1 reseeds per-process at each gen start.
            skip = bool(self.config.get('skip_reseed_on_load', False))
            if not skip:
                reseed_loaded_bundle(
                    document, sim_data_path, cli_seed, agent_id=agent_id)
            ecoli = Composite(
                {'skip_process_state': True,
                 'run_steps_on_init': False,
                 **document},
                core=core)
            if not skip:
                _reseed_allocator_rng(
                    ecoli.state, sim_data_path, cli_seed, agent_id=agent_id)
            else:
                print('  [skip_reseed_on_load=True] preserved saved '
                      'rng_state and allocator_rng (mid-cycle resume)',
                      flush=True)
            print(f"  Loaded in {_time.time()-t0:.2f}s", flush=True)
        else:
            # Build from sim_data. If initial_state_file points at a
            # v1-style JSON, _get_initial_state loads it via fsspec
            # (cloud-aware) and overlays it onto the document; processes
            # / allocator / etc. are rebuilt from per-gen
            # LoadSimData(seed=cli_seed), matching v1's per-gen reset.
            print("Building composite document from sim_data...", flush=True)
            t0 = _time.time()
            state = build_composite_native(core, self.config)
            print(f"  Built in {_time.time()-t0:.2f}s", flush=True)

            print("Creating composite (with realize)...", flush=True)
            t0 = _time.time()
            # v1-emulation: run Steps on init so listener outputs are
            # populated before the t=0 emit (mass, fold_change, etc.).
            # Side effect: global_clock (classified as a Step) advances
            # global_time by one tick, so v2 produces one extra emit at
            # the end of the run. This doesn't affect parity comparisons
            # (which align on common timesteps) but is a known v1-compat
            # cut — see memory:v1_compat_debt.
            ecoli = Composite({'schema': {}, 'state': state,
                               'run_steps_on_init': True}, core=core)
            print(f"  Composite created in {_time.time()-t0:.2f}s", flush=True)

        # Steps should only run when triggered by global_clock,
        # not from initial state. Clear to_run so the first cycle
        # starts from global_clock's update of global_time.
        ecoli.to_run = []

        # Sync the composite's top-level global_time to the loaded
        # daughter cell's global_time so gen N+1 emits at absolute
        # time (matching v1). Without this, build_ecoli_document
        # constructs a fresh global_clock that starts at 0, even when
        # the daughter cell carries mother's division-time clock —
        # daughter parquet would emit at t=1, 2, ... instead of
        # t=division+1, division+2, ...
        agent_id = self.config.get('agent_id', '0')
        agent_t = ecoli.state.get('agents', {}).get(
            agent_id, {}).get('global_time')
        if agent_t is not None and float(agent_t) > 0:
            new_t = float(agent_t)
            ecoli.state['global_time'] = new_t
            # Sync self.front[path]['time'] for all processes — Composite
            # populated front with time=0 at construction (when global_time
            # was still the schema default), so without this resync,
            # run_process computes future = 0 + interval = 1 and ends up
            # rewinding state.global_time to 1.0 on first tick.
            for path in list(ecoli.front.keys()):
                ecoli.front[path]['time'] = new_t

        self._composite = ecoli
        self.generated_initial_state = None
        self.ecoli = None

        # If `composite_checkpoint_at` is set, run to that absolute
        # sim-time, save a bundle to `composite_checkpoint_dir`, then
        # stop. Used for pre-division iteration: run once to
        # near-division, then reload + short-run repeatedly to
        # exercise division logic without paying the full cell-cycle
        # wall time each iteration. When combined with
        # `initial_state_file`, picks up from the loaded bundle and
        # only runs the remaining interval.
        checkpoint_at = self.config.get('composite_checkpoint_at')
        checkpoint_dir = self.config.get('composite_checkpoint_dir')
        if checkpoint_at is not None and checkpoint_dir:
            current_t = float(ecoli.state.get('global_time', 0.0))
            interval = float(checkpoint_at) - current_t
            if interval < 0:
                raise ValueError(
                    f"composite_checkpoint_at={checkpoint_at} is earlier "
                    f"than current global_time={current_t}")
            print(f"Running composite from t={current_t} to "
                  f"sim-t={checkpoint_at}s ({interval}s interval)...",
                  flush=True)
            t0 = _time.time()
            # Save the SINGLE-CELL pre-division state. If division
            # fires before checkpoint_at we want to halt and save the
            # mother so the checkpoint can be reused for iteration.
            # Poll agent count between short runs; stop when >1 agent.
            if interval > 0:
                poll_s = min(60.0, interval)
                end_t = current_t + interval
                while float(ecoli.state.get('global_time', 0.0)) < end_t:
                    remaining = end_t - float(
                        ecoli.state.get('global_time', 0.0))
                    step = min(poll_s, remaining)
                    ecoli.run(step)
                    if len(ecoli.state.get('agents', {})) > 1:
                        print(f"  division fired before checkpoint_at; "
                              f"halting at t="
                              f"{ecoli.state.get('global_time', 0.0)}s",
                              flush=True)
                        break
            elapsed = _time.time() - t0
            print(f"  reached t={ecoli.state.get('global_time', 0.0)} "
                  f"in {elapsed:.2f}s wall; "
                  f"saving bundle → {checkpoint_dir}/", flush=True)
            ecoli.save_bundle(checkpoint_dir)
            return

        # Set up parquet emitter so per-tick listener data feeds the
        # analysis step. Mirrors the vivarium engine's emit pattern:
        # one ``configuration`` emit up front (with metadata that sets
        # the hive partition path) then a ``history`` emit after each
        # tick with flattened listener state. Only wired for composite
        # engine here; the v1 vivarium path emits via engine.update.
        emitter = None
        if self.emitter == 'parquet':
            emitter = ParquetEmitter(self.emitter_arg)
            # Build the configuration emit. Mirror v1 ``get_metadata()``
            # so the configuration table has the same column set —
            # downstream analyses look up things like ``git_hash``,
            # full config keys, and ``output_metadata__*`` columns.
            cfg_metadata = self.get_metadata()
            cfg_metadata['experiment_id'] = self.experiment_id
            cfg_metadata['variant'] = self.config.get('variant', 0)
            cfg_metadata['lineage_seed'] = self.lineage_seed
            cfg_metadata['agent_id'] = str(self.agent_id)
            cfg_metadata['initial_global_time'] = float(
                ecoli.state.get('global_time', 0.0))
            cfg_metadata['output_metadata'] = self._collect_output_metadata()
            emitter.emit({
                'table': 'configuration',
                'data': {'metadata': cfg_metadata},
            })

        from ecoli.composites.ecoli_composite import run_to_division

        # Emit initial state BEFORE any tick fires, matching v1
        # vivarium's emit cadence (vivarium engine.update emits the
        # initial state at t=initial then post-tick at t=initial+1, ...).
        # Without this, v2 emits one tick later than v1 across the
        # board, producing a +1 alignment offset in every parquet
        # comparison.
        if emitter is not None:
            self._emit_composite_history(emitter, ecoli)

        print(f"Running composite for {self.max_duration}s...", flush=True)
        t0 = _time.time()
        # Emit per-tick history (parquet column parity with v1) by
        # threading the emitter into run_to_division as a callback.
        on_tick = (
            (lambda eco: self._emit_composite_history(emitter, eco))
            if emitter is not None else None)
        divided, _ = run_to_division(
            ecoli,
            max_duration=self.max_duration,
            daughter_outdir=self.daughter_outdir,
            on_tick=on_tick)
        elapsed = _time.time() - t0
        print(f"Completed in {elapsed:.2f} seconds, divided={divided}", flush=True)

        if emitter is not None:
            emitter.success = divided or not self.fail_at_max_duration
            emitter.finalize()

    def _run_composite_lineage(self):
        """Run a single lineage forward by rebuilding a fresh per-gen
        Composite at every division boundary.

        Each generation builds its own :py:class:`Composite` via
        :py:func:`~ecoli.composites.ecoli_composite.build_ecoli_document`
        with ``seed = lineage_seed + gen``, just like the per-gen
        Nextflow path does. Daughter cell state (split bulk + unique
        from mother's pre-divide composite) is overlaid onto the
        fresh build via ``initial_state``. This guarantees per-gen
        byte parity vs the per-gen path because the same code runs
        in both, just inside one Python interpreter instead of N.

        Inputs vs the per-gen Nextflow path that this saves on each
        gen boundary:
          - sim_data pickle reload (~5–30 s) — the loaded pickle is
            shared across all per-gen LoadSimData wrappers via the
            new ``sim_data=`` kwarg.
          - Python interpreter start + import (~10–30 s on fresh pod)
          - numba JIT cache reload (~0–10 s)

        What persists across gens:
          - ``self._shared_sim_data`` — pickle stays loaded.
          - ``core`` (Core registry) — type system stays warm.
          - ``emitter`` (ParquetEmitter) — single emitter; we emit a
            new ``configuration`` row at each gen boundary so the
            partition path advances to ``generation=N/agent_id=AID``.

        What's rebuilt fresh per gen (matching per-gen path):
          - ``LoadSimData`` wrapper (cheap; just a new RandomState
            seeded with ``lineage_seed + gen``).
          - The cell's full state via ``build_ecoli_document``:
            process configs (with new RNG seeds), allocator_rng,
            next_update_time defaults, sim_data_objects store, step
            flow, all of it. Daughter's split bulk/unique come in via
            ``initial_state``; everything else is fresh.
          - The :py:class:`Composite` instance.
        """
        from ecoli.composites.ecoli_composite import (
            build_composite_native, run_to_division)
        from ecoli.library.bigraph_types import ECOLI_TYPES
        from ecoli.library.parquet_emitter import ParquetEmitter
        from ecoli.library.sim_data import LoadSimData
        from process_bigraph import Composite
        from process_bigraph.types.process import (
            register_types as register_process_bigraph_types)
        from bigraph_schema import Core, BASE_TYPES
        from copy import deepcopy
        import time as _time

        n_generations = int(self.config.get("generations") or 1)
        if not self.config.get("single_daughters", True):
            raise NotImplementedError(
                "composite_lineage engine currently only supports "
                "single_daughters=True. Tree-mode multi-cell composite "
                "is a future phase.")
        if self.config.get("composite_checkpoint_at") is not None:
            raise NotImplementedError(
                "composite_checkpoint_at is incompatible with "
                "composite_lineage engine.")

        # ---- One-time setup ---------------------------------------
        self.processes = self._retrieve_processes(
            self.processes, self.add_processes,
            self.exclude_processes, self.swap_processes)
        self.topology = self._retrieve_topology(
            self.topology, self.processes, self.swap_processes,
            self.log_updates)
        self.process_configs = self._retrieve_process_configs(
            self.process_configs, self.processes)

        core = Core(BASE_TYPES)
        register_process_bigraph_types(core)
        core.register_types(ECOLI_TYPES)

        base_lineage_seed = int(self.config.get('lineage_seed', 0))
        base_agent_id = str(self.config.get('agent_id', '0'))

        # Load sim_data once. All per-gen LoadSimData wrappers below
        # reuse this loaded pickle via the ``sim_data=`` kwarg, so
        # only this one call hits disk / fsspec.
        #
        # MP / Ray paths can pre-load the pickle in the parent /
        # actor and pass it in via ``self._preloaded_sim_data`` so
        # this call costs ~0.0s rather than 5-30s per worker.
        print("Loading sim_data...", flush=True)
        t0 = _time.time()
        base_kwargs = dict(self.config)
        base_kwargs['seed'] = base_lineage_seed
        if getattr(self, '_preloaded_sim_data', None) is not None:
            base_kwargs['sim_data'] = self._preloaded_sim_data
        base_load_sim_data = LoadSimData(**base_kwargs)
        self._shared_sim_data = base_load_sim_data.sim_data
        print(f"  Loaded in {_time.time()-t0:.2f}s", flush=True)

        # Single shared emitter across all gens. Each gen's
        # ``configuration`` emit advances ``partitioning_path`` so
        # subsequent history rows land in the new
        # ``generation=N/agent_id=AID/`` partition.
        emitter = None
        if self.emitter == 'parquet':
            emitter = ParquetEmitter(self.emitter_arg)

        # ---- Drive each generation --------------------------------
        # ``daughter_state`` is the (deep-copied, payload-stripped)
        # cell state from the previous gen's daughter-0 cell, used as
        # ``initial_state`` for the next gen's build. ``None`` for
        # gen 0 (uses sim_data initial state via the standard path).
        daughter_state = None
        any_divided = False
        total_t0 = _time.time()
        try:
            for gen in range(n_generations):
                gen_seed = base_lineage_seed + gen
                # agent_id grows by one '0' per division, single-
                # daughter lineage; matches sim.nf's encoding.
                gen_agent_id = base_agent_id + ("0" * gen)

                print(
                    f"\n=== Lineage gen {gen}/{n_generations - 1} "
                    f"(agent_id={gen_agent_id}, seed={gen_seed}) ===",
                    flush=True,
                )

                # Build per-gen config locally (don't mutate self.config
                # — get_metadata() reads it on every emit).
                gen_config = deepcopy(self.config)
                gen_config['seed'] = gen_seed
                gen_config['agent_id'] = gen_agent_id
                if daughter_state is not None:
                    gen_config['initial_state'] = daughter_state
                    gen_config['initial_state_file'] = None

                # Gen 0 may be loaded from a pre-saved bundle (e.g.
                # iter_test_division.py uses this to skip the ~5 min
                # tick-up to the first division). The bundle is the
                # full Composite document captured by
                # ``composite_checkpoint_at``; it short-circuits both
                # the build_composite_native and the realize-from-
                # decls passes. Subsequent gens always rebuild from
                # daughter_state (initial_state overlay).
                bundle_path = (
                    gen_config.get('initial_state_file')
                    if gen == 0 and daughter_state is None
                    else None)
                is_local_bundle = (
                    bundle_path
                    and os.path.isdir(bundle_path)
                    and os.path.isfile(
                        os.path.join(bundle_path, 'document.json')))

                if is_local_bundle:
                    from process_bigraph.bundle import load_bundle
                    print(f"Loading composite from bundle "
                          f"{bundle_path}...", flush=True)
                    t0 = _time.time()
                    document = load_bundle(bundle_path, as_numpy=True)
                    # IMPORTANT: do NOT call reseed_loaded_bundle /
                    # _reseed_allocator_rng here. Those are for the
                    # per-gen Nextflow path where each gen loads
                    # MOTHER's daughter JSON and needs fresh
                    # per-gen-seed RNGs. Our bundle is the cell at
                    # t=checkpoint_at, mid-generation — the RNG state
                    # has been advanced by N ticks and re-seeding
                    # would erase that history (gen 0 at t=2530 then
                    # diverges by ~10M counts vs the no-bundle path).
                    # Reseeding still happens at gen N>=1 daughter
                    # transitions via the build_composite_native call
                    # in the else branch below.
                    ecoli = Composite(
                        {'skip_process_state': True,
                         'run_steps_on_init': False,
                         **document},
                        core=core)
                    print(f"  Loaded in {_time.time()-t0:.2f}s",
                          flush=True)
                else:
                    # Build the cell via EcoliCellProcess: a real
                    # process-bigraph Process whose ``initialize``
                    # runs build_ecoli_document with the per-gen
                    # seed and overlays daughter_state onto the
                    # fresh build. The same class is used by the
                    # MP / Ray runners so the lineage and parallel
                    # paths share one cell-construction code path.
                    # Validated by iter_test_ecoli_cell.py to be
                    # byte-identical to the per-gen path's direct
                    # build_composite_native + Composite call.
                    from ecoli.composites.ecoli_cell_process import (
                        EcoliCellProcess)
                    print("Building (via EcoliCellProcess)...",
                          flush=True)
                    t0 = _time.time()
                    cell = EcoliCellProcess(
                        config={
                            'lineage_seed': base_lineage_seed,
                            'agent_id': gen_agent_id,
                            'sim_data_path':
                                gen_config['sim_data_path'],
                            'initial_state': daughter_state or {},
                            'sim_data': self._shared_sim_data,
                            'sim_config': gen_config,
                        },
                        core=core,
                    )
                    ecoli = cell.inner_composite
                    print(f"  Built + Composite created in "
                          f"{_time.time()-t0:.2f}s", flush=True)

                # Sync top-level global_time from the daughter cell's
                # local global_time when starting from a daughter
                # handoff (gen >= 1). Same logic as
                # _run_composite_inner.
                agent_t = ecoli.state.get('agents', {}).get(
                    gen_agent_id, {}).get('global_time')
                if agent_t is not None and float(agent_t) > 0:
                    new_t = float(agent_t)
                    ecoli.state['global_time'] = new_t
                    for path in list(ecoli.front.keys()):
                        ecoli.front[path]['time'] = new_t

                self._composite = ecoli

                # Emit configuration (advances parquet partition path
                # for this gen) and the initial history row at the
                # current global_time. Gen 0 starts at t=0; gen N
                # starts at the previous division's global_time.
                if emitter is not None:
                    self._emit_lineage_configuration(emitter, ecoli)
                    self._emit_composite_history(emitter, ecoli)

                # Run to division. on_tick streams history rows to
                # the shared emitter.
                on_tick = (
                    (lambda eco: self._emit_composite_history(
                        emitter, eco))
                    if emitter is not None else None)
                divided, _t = run_to_division(
                    ecoli,
                    max_duration=self.max_duration,
                    daughter_outdir=None,
                    on_tick=on_tick)

                if divided:
                    any_divided = True
                if not divided:
                    print(f"  Gen {gen}: did not divide within "
                          f"max_duration; halting lineage.",
                          flush=True)
                    break

                # Capture daughter for next gen.
                if gen + 1 >= n_generations:
                    break
                daughter_state = self._extract_lineage_daughter(
                    ecoli, daughter_idx=0)
                if daughter_state is None:
                    print(f"  Gen {gen}: no daughter in state; "
                          f"halting lineage.", flush=True)
                    break

            print(f"\nLineage completed in "
                  f"{_time.time() - total_t0:.2f}s wall.", flush=True)
        finally:
            if emitter is not None:
                emitter.success = (
                    any_divided or not self.fail_at_max_duration)
                emitter.finalize()

    def _extract_lineage_daughter(self, ecoli, daughter_idx=0):
        """Pull daughter-0's cell state from a divided composite for
        in-process handoff to the next gen's build.

        Mirrors :py:func:`~ecoli.composites.ecoli_composite.save_v2_daughters`'s
        payload prep: deep-copies the agent subtree and runs
        ``_v2_daughter_payload`` to drop edges, process refs,
        allocator RNG, etc., and add bulk/unique dtype metadata.

        Returns ``None`` if the composite did not divide.
        """
        import os
        from copy import deepcopy
        from ecoli.composites.ecoli_composite import _v2_daughter_payload

        agents = ecoli.state.get('agents', {})
        sorted_ids = sorted(agents.keys())
        if len(sorted_ids) < 2:
            return None
        target_id = sorted_ids[daughter_idx]
        cell_copy = deepcopy(agents[target_id])
        if os.environ.get('VECOLI_DEBUG_DIVIDE'):
            import sys as _sys
            try:
                bulk = cell_copy.get('bulk')
                if bulk is not None and hasattr(bulk, 'dtype'):
                    if bulk.dtype.names and 'count' in bulk.dtype.names:
                        print(f'[divide-debug] _extract_lineage_daughter target={target_id!r} '
                              f'bulk.count.sum={int(bulk["count"].sum())}',
                              file=_sys.stderr, flush=True)
                    else:
                        print(f'[divide-debug] _extract_lineage_daughter target={target_id!r} '
                              f'bulk dtype={bulk.dtype} (no count field)',
                              file=_sys.stderr, flush=True)
            except Exception as e:
                print(f'[divide-debug] _extract_lineage_daughter err: {e}',
                      file=_sys.stderr, flush=True)
        _v2_daughter_payload(cell_copy)
        if os.environ.get('VECOLI_DEBUG_DIVIDE'):
            import sys as _sys
            try:
                bulk = cell_copy.get('bulk')
                if bulk is not None and hasattr(bulk, 'dtype') and bulk.dtype.names:
                    if 'count' in bulk.dtype.names:
                        print(f'[divide-debug] _extract_lineage_daughter AFTER payload '
                              f'bulk.count.sum={int(bulk["count"].sum())}',
                              file=_sys.stderr, flush=True)
            except Exception:
                pass
        return cell_copy

    def _lineage_agent_id(self, ecoli):
        """Return the (single) active agent_id under single_daughters mode.

        Returns the first key in ``ecoli.state['agents']``; ``None``
        if no agents are present.
        """
        agents = ecoli.state.get('agents', {})
        if not agents:
            return None
        # In single_daughters mode there is only one agent at a time.
        # Use sorted to get a stable choice if a transient multi-agent
        # state ever appears (shouldn't happen with single_daughters
        # but defensive).
        return sorted(agents.keys())[0]

    def _emit_lineage_configuration(self, emitter, ecoli):
        """Emit a parquet ``configuration`` row for the current agent_id.

        Called at gen 0 startup and after each division (when agent_id
        changes), to set ``emitter.partitioning_path`` so subsequent
        history rows land in the new ``generation=N/agent_id=AID/``
        partition.

        Uses the composite's *current* agent_id (read from state),
        not ``self.agent_id`` which still reflects gen 0's value.
        """
        agent_id = self._lineage_agent_id(ecoli)
        if agent_id is None:
            return
        cfg_metadata = self.get_metadata()
        cfg_metadata['experiment_id'] = self.experiment_id
        cfg_metadata['variant'] = self.config.get('variant', 0)
        cfg_metadata['lineage_seed'] = self.lineage_seed
        cfg_metadata['agent_id'] = str(agent_id)
        cfg_metadata['initial_global_time'] = float(
            ecoli.state.get('global_time', 0.0))
        cfg_metadata['output_metadata'] = self._collect_output_metadata()
        emitter.emit({
            'table': 'configuration',
            'data': {'metadata': cfg_metadata},
        })

    def _collect_output_metadata(self):
        """Walk the live composite's process/step instances and build
        the ``output_metadata`` dict v1 emits into ``configuration``.

        Uses each instance's ``ports_schema()`` to pull out
        ``_properties.metadata`` (e.g. cistron IDs that match listener
        array columns), and remaps port names to wire paths via the
        topology — so the parquet columns come out as
        ``output_metadata__listeners__rna_counts__mRNA_cistron_counts``
        etc., matching v1 and satisfying ``field_metadata`` lookups.
        """
        from vivarium.library.topology import inverse_topology
        from vivarium.library.dict_utils import deep_merge_check

        ecoli = self._composite
        output_metadata: dict[str, Any] = {}
        instance_paths = {**getattr(ecoli, 'step_paths', {}),
                          **getattr(ecoli, 'process_paths', {})}
        for path, entry in instance_paths.items():
            # step_paths/process_paths values are the Link state dicts
            # with ``instance`` holding the realized object; unwrap to
            # the live instance for ports_schema() access.
            if isinstance(entry, dict):
                instance = entry.get('instance')
            elif isinstance(entry, tuple) and entry:
                instance = entry[0]
            else:
                instance = entry
            if instance is None or not hasattr(instance, 'ports_schema'):
                continue
            try:
                ports = instance.ports_schema()
            except Exception:
                continue
            extracted = extract_metadata(ports)
            if not extracted:
                continue
            proc_name = path[-1] if path else None
            topology = self.topology.get(proc_name) if proc_name else None
            if topology:
                extracted = inverse_topology((), extracted, topology)
            try:
                output_metadata = deep_merge_check(
                    output_metadata, extracted, check_equality=True)
            except Exception:
                # Different processes occasionally overlap in listener
                # paths but with equal metadata — fall back to a
                # forgiving merge that keeps the first value seen.
                output_metadata = {**extracted, **output_metadata}
        return output_metadata

    def _emit_composite_history(self, emitter, ecoli):
        """Emit a history row for the current tick.

        ParquetEmitter.emit expects ``data['data']['agents']`` to be a
        dict keyed by agent_id; it skips emission while more than one
        agent is present (division cleanup is handled by the workflow's
        handoff model), and flattens each surviving agent's subtree
        individually. This mirrors how the vivarium Engine emits — the
        agents-map shape is what the analysis pipeline queries.

        Emits ``listeners``, ``bulk``, and ``process_state`` so the
        per-column set matches v1's Parquet output (analyses and the
        ``dummy`` column-name check rely on that schema parity).
        """
        global_t = float(ecoli.state.get('global_time', 0.0))
        agents = ecoli.state.get('agents', {})
        if os.environ.get('VECOLI_DEBUG_DIVIDE'):
            import sys as _sys
            for aid, ag in agents.items():
                if not isinstance(ag, dict):
                    continue
                bulk = ag.get('bulk')
                if bulk is not None and hasattr(bulk, 'dtype') \
                        and bulk.dtype.names and 'count' in bulk.dtype.names:
                    print(f'[divide-debug] _emit_composite_history t={global_t} '
                          f'agent_id={aid!r} bulk.count.sum={int(bulk["count"].sum())}',
                          file=_sys.stderr, flush=True)
        emit_agents = {}
        # Only these agent subtrees are emitted. Anything else (process
        # nodes, infrastructure stores) is not time-series data.
        EMIT_KEYS = ('listeners', 'bulk', 'process_state')
        for agent_id, agent_state in agents.items():
            if not isinstance(agent_state, dict):
                continue
            subtree = {}
            for k in EMIT_KEYS:
                v = agent_state.get(k)
                if v is None:
                    continue
                # ``bulk`` is a structured numpy array at runtime;
                # v1's Parquet column is a plain ``List[Int64]`` of
                # counts, so project the ``count`` field out here.
                if k == 'bulk' and isinstance(v, np.ndarray) and v.dtype.names \
                        and 'count' in v.dtype.names:
                    v = np.asarray(v['count'], dtype=np.int64)
                subtree[k] = v
            if not subtree:
                continue
            emit_agents[str(agent_id)] = subtree
        if not emit_agents:
            return
        emitter.emit({
            'table': 'history',
            'data': {
                'agents': emit_agents,
                'time': global_t,
            },
        })

    def run(self):
        """Create and run an EcoliSim experiment. If the simulation reaches
        the maximum duration specified by ``config['max_duration']``, it will
        raise a :py:class:`~ecoli.experiments.ecoli_master_sim.TimeLimitError`
        if ``config['fail_at_max_duration']`` is ``True``.

        .. WARNING::
            Run :py:meth:`~ecoli.experiments.ecoli_master_sim.EcoliSim.build_ecoli`
            before calling :py:meth:`~ecoli.experiments.ecoli_master_sim.EcoliSim.run`!
        """
        engine = self.config.get("engine")

        if engine == "composite":
            # process-bigraph composite path: builds directly from sim_data
            # and does not require build_ecoli().
            self._run_composite()
            return

        if engine == "composite_lineage":
            # In-process multi-generation runner: loops _run_composite_inner
            # with daughter 0's state passed forward as the next gen's
            # initial_state. Skips JSON daughter handoff between gens —
            # everything stays in memory. One Python interpreter, one
            # set of imports, one JIT cache, one sim_data pickle load.
            self._run_composite_lineage()
            return

        if self.ecoli is None:
            raise RuntimeError(
                "Build the composite by calling build_ecoli() \
                before calling run()."
            )

        metadata = self.get_metadata()
        metadata["output_metadata"] = self.output_metadata()
        # make the experiment
        if isinstance(self.emitter, str):
            self.emitter_config = {"type": self.emitter}
            if self.emitter_arg is not None:
                for key, value in self.emitter_arg.items():
                    self.emitter_config[key] = value
            if self.emitter == "parquet":
                if ("out_dir" not in self.emitter_config) and (
                    "out_uri" not in self.emitter_config
                ):
                    raise RuntimeError(
                        "Must provide out_dir or out_uri"
                        " as emitter argument for parquet emitter."
                    )
        else:
            raise RuntimeError(
                "Emitter option must be a string"
                " representing the emitter type with any additional config"
                " options under the emitter_arg key."
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
        self.generated_initial_state = None
        self.ecoli_experiment.initial_state = None
        del metadata, experiment_config
        self.ecoli = None

        # run the experiment
        if self.save:
            self.save_states()
        else:
            self.update_experiment(self.max_duration)
        self.ecoli_experiment.end()
        if self.profile:
            report_profiling(self.ecoli_experiment.stats)
        if self.fail_at_max_duration:
            raise TimeLimitError(
                f"Exceeded maximum simulation time: {self.max_duration}"
            )

    def query(self, query: Optional[list[tuple[str]]] = None):
        """
        Query data that was emitted to RAMEmitter (``config['emitter'] == 'timeseries'``).
        For the Parquet emitter, query sim output with an analysis script run using
        :py:mod:`runscripts.analysis` or with ad-hoc DuckDB SQL queries built using
        :py:func:`~ecoli.library.parquet_emitter.dataset_sql` as a base.

        Args:
            query: List of tuple-style paths in the simulation state to
                retrieve emitted values for. Returns all emitted data
                if ``None``.

        Returns:
            Dictionary of emitted data in one of two forms.

            * Raw data (if ``self.raw_output``): Data is keyed by time
              (e.g. ``{0: {'data': ...}, 1: {'data': ...}, ...}``)

            * Timeseries: Data is reorganized to match the structure of the
              simulation state. Leaf values in the returned dictionary are
              lists of the simulation state value over time (e.g.
              ``{'data': [..., ..., ...]}``).
        """
        if self.emitter_config["type"] != "timeseries":
            raise RuntimeError(
                "Query method only works for timeseries emitter."
                " For Parquet emitter, either write an analysis script to be run"
                " using runscripts/analysis.py or build off the DuckDB SQL query"
                " returned by ecoli.library.parquet_emitter.dataset_sql."
            )
        # Retrieve queried data (all if not specified)
        if self.raw_output:
            return self.ecoli_experiment.emitter.get_data(query)
        else:
            return self.ecoli_experiment.emitter.get_timeseries(query)

    def merge(self, other: "EcoliSim"):
        """
        Combine settings from this EcoliSim with another, overriding
        current settings with those from the other EcoliSim.

        Args:
            other: Simulation with settings to override current simulation.
        """
        deep_merge(self.config, other.config)

    def get_metadata(self) -> dict[str, Any]:
        """
        Compiles all simulation settings, git hash, and process list into a single
        dictionary.
        """
        # create metadata of this experiment to be emitted,
        # namely the config of this EcoliSim object
        # with an additional key for the current git hash.
        # Goal is to save enough information to reproduce the experiment.
        metadata = dict(self.config)
        metadata["git_hash"] = get_git_revision_hash()
        metadata["git_diff"] = get_git_diff()
        metadata["processes"] = [k for k in metadata["processes"].keys()]
        metadata["time"] = datetime.now()
        # Needed for data types that Polars cannot serialize
        # (e.g. Pint Quantities inside a list)
        metadata = serialize_value(metadata)
        return metadata

    def output_metadata(self) -> dict[str, Any]:
        """
        Filters all ports schemas to include only output metadata
        located at the path ``('_properties', 'metadata')`` for each schema by
        invoking :py:func:`~.extract_metadata`.
        See :py:meth:`~ecoli.library.schema.listener_schema` for usage details.

        This dictionary of output metadata is flattened (see :py:func:`~ecoli.library.parquet_emitter.flatten_dict`)
        into columns with prefix :py:data:`~ecoli.library.parquet_emitter.METADATA_PREFIX`
        and emitted as part of the simulation config by the Parquet emitter. It can
        be retrieved later using :py:func:`~ecoli.library.parquet_emitter.field_metadata`.
        """
        if self.divide:
            processes_and_steps = self.ecoli.processes["agents"][self.agent_id]
            processes_and_steps.update(self.ecoli.steps["agents"][self.agent_id])
            topologies = self.ecoli.topology["agents"][self.agent_id]
        else:
            processes_and_steps = self.ecoli.processes
            processes_and_steps.update(self.ecoli.steps)
            topologies = self.ecoli.topology
        output_metadata: dict[str, Any] = {}
        for proc_name, proc in processes_and_steps.items():
            proc_ports_schema = proc.get_schema()
            extracted = extract_metadata(proc_ports_schema)
            if extracted:
                extracted = inverse_topology((), extracted, topologies[proc_name])
                output_metadata = deep_merge_check(
                    output_metadata, extracted, check_equality=True
                )
        return output_metadata

    def export_json(self, filename: str = CONFIG_DIR_PATH + "export.json"):
        """
        Saves current simulation settings along with git hash and final list
        of process names as a JSON that can be reloaded using
        :py:meth:`~ecoli.experiments.ecoli_master_sim.EcoliSim.from_file`.

        Args:
            filename: Filepath and name for saved JSON (include ``.json``).
        """
        with open(filename, "w") as f:
            json.dump(serialize_value(self.get_metadata()), f)


def extract_metadata(ports_schema: dict[str, Any], properties: bool = False):
    """
    Filters ports schema to contain only a mapping of ports to user-supplied
    metadata (pulled from path `('_properties', 'metadata')` for each schema).
    See :py:meth:`~ecoli.library.schema.listener_schema` for usage details.

    Args:
        ports_schema: Ports schema to filter and compile metadata for
        properties: Flag used internally during recursive filtering
    Returns:
        Dictionary with same structure as ports schema but with only metadata
        as leaf nodes instead of complete schema
    """
    extracted = {}

    if "_properties" in ports_schema and isinstance(ports_schema["_properties"], dict):
        return extract_metadata(ports_schema["_properties"], True)

    if properties and "metadata" in ports_schema:
        metadata = ports_schema["metadata"]
        if isinstance(metadata, np.ndarray):
            metadata = metadata.tolist()
        return metadata

    for port, schema in ports_schema.items():
        if isinstance(schema, dict):
            subextracted = extract_metadata(schema)
            if subextracted is not None:
                extracted[port] = subextracted

    return extracted or None


def main():
    """
    Runs a simulation with CLI options.
    """
    ecoli_sim = EcoliSim.from_cli()
    # build_ecoli() constructs the vivarium engine; it's only needed
    # for the v1 path. The composite engine (v2) builds its state in
    # _run_composite directly from sim_data or a bundle, skipping the
    # v1 composer entirely.
    if ecoli_sim.config.get("engine") != "composite":
        ecoli_sim.build_ecoli()
    ecoli_sim.run()


if __name__ == "__main__":
    main()
