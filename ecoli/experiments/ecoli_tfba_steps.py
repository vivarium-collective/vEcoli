"""
Export tFBA time steps and relevant metadata to a Zarr store.

This prototype implementation executes simulations with the `RAMEmitter`, and
subsequently converts a selection of the trajectory schema into an
`xarray.DataTree`. This requires unbounded memory, and will be replaced by a new
`Emitter`, once schemata for the intended use cases have been established.

The schema selection is defined via the newly introduced `SchemaTransform`.
"""

from dataclasses import dataclass, field
from itertools import chain, starmap
from functools import cached_property
from typing import Any, Tuple, List, Set, Dict, Optional, Iterable
from pathlib import Path
import os, os.path
import warnings

from unum import Unum
import numpy as np
import xarray as xr
import zarr as zr

from vivarium.core.types import HierarchyPath
from vivarium.core.store import Store
from vivarium.core.process import Process
from vivarium.core.engine import Engine
from vivarium.library.topology import get_in, dict_to_paths
from ecoli.experiments.ecoli_master_sim import EcoliSim, CONFIG_DIR_PATH
from ecoli.processes.metabolism import \
    Metabolism as vMetabolism, FluxBalanceAnalysisModel as vFBA, \
    TIME_UNITS, CONC_UNITS, GDCW_BASIS, CONVERSION_UNITS
from reconstruction.ecoli.dataclasses.process.metabolism import \
    Metabolism as wcMetabolism, DRY_MASS_UNITS
from wholecell.utils.filepath import OUT_DIR
from wholecell.utils.modular_fba import FluxBalanceAnalysis as wcFBA
from wholecell.utils._netflow.nf_glpk import NetworkFlowGLPK


# ==============================================================================


@dataclass
class FieldSpec:
    """
    Specification for how an individual Vivarium schema entry should be mapped
    onto an `xr.DataArray` inside an (eventual) `xr.DataTree` hierarchy.

    Attributes
    ----------
        group: Name of a group/node within an (eventual) `xr.DataTree`.
        name:  Name of a data variable within the group.
        unit:  Unit to store as an attribute inside the group.
        dtype: Numeric type of the target `xr.DataArray`.

    Example
    -------
        FieldSpec("/gauges:cell_mass", DRY_MASS_UNITS, np.float64)
    """
    group: str = field(init=False)
    name: str
    unit: Optional[Unum]
    dtype: type

    def __post_init__(self) -> None:
        assert isinstance(self.name, str)
        assert self.name.startswith("/") and self.name.count(":") == 1
        self.group, self.name = self.name.split(":")
        if self.unit is not None:
            assert isinstance(self.unit, Unum) and self.unit.asNumber() == 1.0
        assert issubclass(self.dtype, np.number)


@dataclass
class SchemaTransformLocal:
    """
    Specification for how a collection of Vivarium schema entries should be
    mapped onto a collection of `xr.DataArray`s inside an (eventual)
    `xr.DataTree` hierarchy, assuming that this schema subset shares not only a
    common root path, but also a common mechanism for accessing the associated
    metadata.

    Attributes
    ----------
        root:      Path w.r.t. which schema entries are located.
        process:   If metadata is to be accessed, then this is a pair of:
            - a `Store | Process` in which to locate metadata,
            - the relative path from `root` to the `Store | Process` location,
              allowing data and metadata to share the paths in `transform`.
        transform: Vivarium schema with `FieldSpec` leaves.
    """
    root: HierarchyPath
    process: Optional[Tuple[Store | Process, HierarchyPath]]
    transform: dict
    rel_paths: List[HierarchyPath] = field(init=False)
    specs: List[FieldSpec] = field(init=False)

    def __post_init__(self) -> None:
        self._check_path(self.root)
        if self.process is not None:
            assert isinstance(self.process, tuple)
            proc, proc_path = self.process
            assert isinstance(proc, (Store, Process))
            self._check_path(proc_path)
        assert isinstance(self.transform, dict)
        paths, specs = map(list, zip(*dict_to_paths((), self.transform)))
        assert len(paths) and all(isinstance(p, tuple) for p in paths)
        assert all(map(len, paths))
        self.rel_paths = paths
        self.specs = list(starmap(FieldSpec, specs))

    @staticmethod
    def _check_path(path) -> None:
        assert isinstance(path, tuple) and all(isinstance(p, str) for p in path)

    @property
    def root_path(self) -> HierarchyPath:
        return self.root + self.proc_path

    @property
    def proc_path(self) -> HierarchyPath:
        return () if self.process is None else self.process[1]

    def __iter__(self) -> Iterable[FieldSpec]:
        return iter(self.specs)

    @cached_property
    def metadata(self) -> List[Optional[Any]]:
        if self.process is None:
            return [None for _ in self.rel_paths]
        else:
            proc = self.process[0]
            if isinstance(proc, Process):
                schema = proc.get_schema()
            else:
                schema = proc.get_config()
            raw = (
                get_in(schema, self.proc_path + p + ("_properties", "metadata"))
                for p in self.rel_paths)
            return [np.array(r) if isinstance(r, (tuple, list)) else r
                    for r in raw]


@dataclass
class SchemaTransform:
    """
    Specification for how a collection of Vivarium schema entries and their
    metadata should be mapped onto an `xr.DataTree`, by appropriately combining
    local transforms on both ends, i.e., both in the Vivarium API and in the
    Xarray API.

    The main user-facing method is `SchemaTransform.export(sim: EcoliSim)`.

    Attributes
    ----------
        transforms: A collection of `SchemaTransformLocal`s.
    """
    transforms: List[SchemaTransformLocal]

    def __post_init__(self) -> None:
        assert isinstance(self.transforms, list)
        assert all(isinstance(tx, SchemaTransformLocal)
                   for tx in self.transforms)

    @property
    def query(self) -> List[HierarchyPath]:
        return list(chain.from_iterable(
            [tx.root_path + p for p in tx.rel_paths] for tx in self.transforms))

    def __iter__(self) -> Iterable[FieldSpec]:
        return chain.from_iterable(self.transforms) # type: ignore[arg-type]

    def alloc_datatree(self, time: np.ndarray) -> xr.DataTree:
        # aggregate `FieldSpec`s by their position in group hierarchy
        groups: Dict[str, Dict[str, Tuple[Optional[Unum], type, Any]]] = {}
        for tx in self.transforms:
            for (spec, meta) in zip(tx.__iter__(), tx.metadata):
                groups.setdefault(spec.group, {})[spec.name] = (
                    spec.unit, spec.dtype, meta)
        # create global time coordinate
        time_coo = xr.Dataset(
            coords={"time": np.array(time, dtype=np.float32)},
            attrs={"time": TIME_UNITS.strUnit()})
        # create time-indexed field variables, add metadata where appropriate
        var_coo = {
            group: xr.Dataset(
                data_vars={
                    name: (
                        ("time",) + (() if meta is None else (f"id_{name}",)),
                        np.zeros(
                            (len(time),) + (
                                () if meta is None else (len(meta),)),
                            dtype=dtype))
                    for (name, (_, dtype, meta)) in coo.items()},
                coords={
                    f"id_{name}": meta
                    for (name, (_, _, meta)) in coo.items()
                    if meta is not None},
                attrs={
                    name: unit.strUnit() for (name, (unit, _, _))
                    in coo.items() if unit is not None})
            for (group, coo) in groups.items()}
        # create group hierarchy
        return xr.DataTree.from_dict({"/": time_coo} | var_coo)

    def fill_datatree(self, traj: dict, output: xr.DataTree,
                      skip_times: Set[int]) -> None:
        assert isinstance(skip_times, set)
        assert all(isinstance(i, int) for i in skip_times)
        for (i, state) in enumerate(traj.values()):
            time_ix = dict(time=i)
            for (path, sp) in zip(self.query, self.__iter__()):
                data = get_in(state, path)
                if data is None and i not in skip_times:
                    raise KeyError(f"Path not found in emit trajectory: {path}")
                output[sp.group][sp.name][time_ix] = data # type: ignore[index]

    def export(self, sim: EcoliSim,
               skip_times: Optional[Set[int]] = None) -> xr.DataTree:
        assert sim.emitter == "timeseries" # type: ignore[attr-defined]
        assert sim.raw_output # type: ignore[attr-defined]
        # avoid `vivarium.core.emitter.timeseries_from_data()`
        traj: dict = sim.query(self.query)
        # full_traj: dict = sim.ecoli_experiment.emitter.saved_data
        ts = np.fromiter(traj.keys(), float, count=len(traj))
        assert np.allclose(
            sim.time_step, np.diff(ts)) # type: ignore[attr-defined]
        output = self.alloc_datatree(ts)
        self.fill_datatree(traj, output,
                           set() if skip_times is None else skip_times)
        return output


# ==============================================================================


def simulate(config_path: Path) -> EcoliSim:

    sim = EcoliSim.from_file(filepath=str(config_path))
    sim.build_ecoli()
    sim.run()
    return sim


def export_metabolism(sim: EcoliSim) -> xr.DataTree:

    # access simulation objects
    engine: Engine = sim.ecoli_experiment
    agent_id: str = sim.agent_id # type: ignore[attr-defined]
    procs = engine.processes["agents"][agent_id]
    metab: vMetabolism = procs["ecoli-metabolism"]
    fba: vFBA = metab.model
    fba_wc: wcFBA = fba.fba
    assert fba_wc.objectiveType == "homeostatic_kinetics_mixed"
    assert not len(fba_wc._oneSidedReactions)

    # export metadata from process parameters
    time_step = TIME_UNITS * metab.parameters["time_step"]
    doubling_time = metab.nutrientToDoublingTime[metab.media_id]
    cell_density = metab.parameters["cell_density"]
    params = xr.DataTree.from_dict({
        "/medium": xr.Dataset(
            attrs={
                "medium_id": metab.media_id}),
        "/gauges": xr.Dataset(
            data_vars={
                "time_step": time_step.asNumber(),
                "doubling_time": doubling_time.asNumber(),
                "cell_density": cell_density.asNumber()},
            attrs={
                "time_step": time_step.strUnit(),
                "doubling_time": doubling_time.strUnit(),
                "cell_density": cell_density.strUnit()}),
        "/objectives/homeostatic": xr.Dataset(
            data_vars={
                "weight": fba_wc.homeostaticObjectiveWeight}),
        "/objectives/kinetic": xr.Dataset(
            data_vars={
                "weight_target": fba_wc.kineticObjectiveWeight,
                "weight_range": fba_wc.kinetic_objective_weight_in_range})})

    # export trajectory data from process/store schemata
    assert sim.log_updates # type: ignore[attr-defined]
    view = SchemaTransform([
        SchemaTransformLocal(
            # access bulk molecule numbers & identifiers via the agent's `Store`
            ("agents", agent_id),
            (engine.state.get_path(("agents", agent_id)), ()),
            {
                "bulk": (
                    "/bulk:bulk_molecule",
                    None, np.int64),
            }),
        SchemaTransformLocal(
            # access mass growth via the agent's listener `Process`
            ("agents", agent_id, "listeners", "mass"),
            None,
            {
                "cell_mass": (
                    "/gauges:cell_mass",
                    DRY_MASS_UNITS, np.float64),
                "dry_mass": (
                    "/gauges:dry_mass",
                    DRY_MASS_UNITS, np.float64)
            }),
        SchemaTransformLocal(
            # access FBA outputs via the agent's `log_update` schema
            ("agents", agent_id, "log_update", "ecoli-metabolism"),
            (metab, ("listeners",)),
            {
                "fba_results": {
                    "coefficient": (
                        "/gauges:mass|vol",
                        CONVERSION_UNITS / time_step, np.float64),
                    "reaction_fluxes": (
                        "/metabolism/fluxes/internal:rxn",
                        CONC_UNITS / TIME_UNITS, np.float64),
                    "external_exchange_fluxes": (
                        "/metabolism/fluxes/exchange:molecule",
                        GDCW_BASIS, np.float64)},
                "enzyme_kinetics": {
                    "counts_to_molar": (
                        "/gauges:conc|count",
                        CONC_UNITS, np.float64),
                    "actual_fluxes": (
                        "/metabolism/fluxes/internal:rxn_constrained",
                        CONC_UNITS / TIME_UNITS, np.float64),
                    "target_fluxes": (
                        "/metabolism/fluxes/internal:rxn_target",
                        CONC_UNITS / TIME_UNITS, np.float64)}
            })])
    traj = view.export(sim, skip_times=set([0]))

    # combine exports
    return xr.DataTree.from_dict({
        "/parameters": params, "/trajectory": traj,
    })


def validate_export(sim: EcoliSim, export: xr.DataTree) -> None:

    # access simulation objects
    engine: Engine = sim.ecoli_experiment
    agent_id: str = sim.agent_id # type: ignore[attr-defined]
    procs = engine.processes["agents"][agent_id]
    metab: vMetabolism = procs["ecoli-metabolism"]
    metab_wc: wcMetabolism = metab.parameters["metabolism"]
    fba: vFBA = metab.model
    fba_wc: wcFBA = fba.fba
    fba_lp: NetworkFlowGLPK = fba_wc._solver
    assert isinstance(fba_lp, NetworkFlowGLPK)

    # validate identifiers
    traj = export.trajectory.metabolism
    assert np.array_equal(
        traj.fluxes.internal.id_rxn,
        fba_wc._reactionIDs)
    assert np.array_equal(
        traj.fluxes.exchange.id_molecule,
        metab.externalMoleculeIDs)
    sel_rxn_constr = dict(id_rxn=np.array(list(map(
        fba_lp._flows.__getitem__, metab_wc.kinetic_constraint_reactions))))
    sel_rxn_active = dict(id_rxn=fba.active_constraints_mask)
    assert np.array_equal(
        traj.fluxes.internal.id_rxn_constrained,
        traj.fluxes.internal.id_rxn[sel_rxn_constr][sel_rxn_active])
    assert np.array_equal(
        traj.fluxes.internal.id_rxn_constrained,
        traj.fluxes.internal.id_rxn_target)

    # validate values
    gauges = export.trajectory.gauges
    assert np.array_equal(
        traj.fluxes.internal.rxn_constrained,
        traj.fluxes.internal.rxn[sel_rxn_constr][sel_rxn_active],
        equal_nan=True)
    dry_frac = gauges.dry_mass / gauges.cell_mass
    assert np.all(dry_frac < 1)
    assert np.allclose(
        gauges["mass|vol"][1:],
        export.parameters.gauges.cell_density.item() * dry_frac[:-1],
        rtol=1e-10, atol=.0)


# ==============================================================================


if __name__ == "__main__":

    config_path = Path(CONFIG_DIR_PATH) / "tfba_steps.json"
    store_path = Path(OUT_DIR) / "tfba_steps.zip"

    sim = simulate(config_path)
    export = export_metabolism(sim)
    validate_export(sim, export)

    if os.path.exists(store_path):
        os.remove(store_path)
    store = zr.storage.ZipStore(store_path, mode="w")
    with warnings.catch_warnings():
        warnings.filterwarnings(
            message=".*Duplicate name",
            action="ignore", category=UserWarning)
        warnings.filterwarnings(
            message=".*Zarr V3 specification",
            action="ignore", category=Warning)
        export.to_zarr(store, zarr_format=3, consolidated=False)
    store.close()
