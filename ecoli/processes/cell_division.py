"""
=============
Cell Division
=============
"""

from typing import Any, Dict

import binascii
import os
import numpy as np
from ecoli.library.bigraph_bridge import BigraphProcess as Process, BigraphStep as Step

from ecoli.library.sim_data import RAND_MAX
from ecoli.library.schema import attrs
from wholecell.utils import units

NAME = "ecoli-cell-division"


def daughter_phylogeny_id(mother_id):
    return [str(mother_id) + "0", str(mother_id) + "1"]


class MarkDPeriod(Step):
    """Set division flag after D period has elapsed"""

    name = "mark_d_period"

    config_schema = {}

    def inputs(self):
        return {
            'full_chromosome': 'unique_array',
            'global_time': 'float',
        }

    def outputs(self):
        return {
            'full_chromosome': 'unique_array',
            # divide_reset[boolean] mirrors v1's ``_divider:
            # {set_value: False}`` so daughters start with divide=False
            # rather than inheriting the mother's True from the moment
            # mark_d_period triggered division. Without this the next
            # generation's Division step short-circuits the boolean
            # check (True>=True) and divides the moment chromosome
            # replication completes — half the normal cell cycle.
            'divide': 'overwrite[divide_reset[boolean]]',
        }

    def ports_schema(self):
        return {
            "full_chromosome": {},
            "global_time": {"_default": 0.0},
            "divide": {
                "_default": False,
                "_updater": "set",
                "_divider": {"divider": "set_value", "config": {"value": False}},
            },
        }

    def next_update(self, timestep, states):
        division_time, has_triggered_division = attrs(
            states["full_chromosome"], ["division_time", "has_triggered_division"]
        )
        if len(division_time) < 2:
            return {}
        # All chromosomes already marked: no work to do this tick. The
        # composite-engine division Step (which fires on a separate
        # mass-threshold check) hasn't caught up yet — it will fire in
        # a later tick when mass crosses threshold. Returning {} avoids
        # an empty-array .min() crash from the next line.
        if has_triggered_division.all():
            return {}
        # Set division time to be the minimum division time for a chromosome
        # that has not yet triggered cell division
        divide_at_time = division_time[~has_triggered_division].min()
        if states["global_time"] >= divide_at_time:
            divide_at_time_index = np.where(division_time == divide_at_time)[0][0]
            has_triggered_division = has_triggered_division.copy()
            has_triggered_division[divide_at_time_index] = True
            # Set flag for ensuing division Step to trigger division
            return {
                "full_chromosome": {
                    "set": {"has_triggered_division": has_triggered_division}
                },
                "divide": True,
            }
        return {}


class Division(Step):
    """
    Division Deriver
     * Uses dry mass threshold that can be set in config via division_threshold
     * Samples division threshold from normal distribution centered around what
       is expected for a medium when division_threshold == mass_distribution
     * If flag d_period is set to true (default), mass thresholds are ignored and
       the same D period mechanism as wcEcoli is used.
    """

    name = NAME

    config_schema = {
        'agent_id': 'string',
        # composer is a class reference (e.g. Ecoli composer) that
        # Division instantiates on divide. Declared as Function so the
        # import path round-trips through bundle.
        'composer': 'function',
        # composer_config is the per-run sim config used to rebuild
        # daughter composites. A shallow map[string → node] keeps
        # the top-level string keys typed while leaving the
        # heterogeneous sub-values opaque.
        'composer_config': 'map[node]',
        # division_threshold can be:
        #   - boolean (config default placeholder)
        #   - string "mass_distribution" (dynamic-mass mode flag)
        #   - float (explicit mass threshold, set after first tick in
        #     mass_distribution mode)
        'division_threshold': 'union[boolean,string,float]',
        # dry_mass_inc_dict maps media_id → Unum[fg] mass increase.
        'dry_mass_inc_dict': 'map[unum[fg]]',
        'seed': 'lineage_seed[integer]',
        # daughter_ids_function generates daughter agent_ids at
        # division time.
        'daughter_ids_function': 'function',
    }

    defaults: Dict[str, Any] = {
        "daughter_ids_function": daughter_phylogeny_id,
        "threshold": None,
        "seed": 0,
    }

    def __init__(self, parameters=None):
        super().__init__(parameters)

        self.agent_id = self.parameters["agent_id"]
        self.composer = self.parameters["composer"]
        self.composer_config = self.parameters["composer_config"]
        self.random_state = np.random.RandomState(seed=self.parameters["seed"])

        self.division_mass_multiplier = 1
        if self.parameters["division_threshold"] == "mass_distribution":
            division_random_seed = (
                binascii.crc32(b"CellDivision", self.parameters["seed"]) & 0xFFFFFFFF
            )
            division_random_state = np.random.RandomState(seed=division_random_seed)
            self.division_mass_multiplier = division_random_state.normal(
                loc=1.0, scale=0.1
            )
        self.dry_mass_inc_dict = self.parameters["dry_mass_inc_dict"]

    def inputs(self):
        return {
            # division_variable is either the dry_mass (float, when
            # wired to 'dry_mass') or the divide flag (boolean, when
            # wired to 'divide' via MarkDPeriod's output).
            'division_variable': 'union[boolean,float]',
            'full_chromosome': 'unique_array',
            'media_id': 'string',
            'division_threshold': 'union[boolean,string,float]',
        }

    def outputs(self):
        return {
            'agents': {},  # _divide sentinel writes here; agents schema already exists
            # Same polymorphism as the input (see config_schema).
            'division_threshold': 'overwrite[union[boolean,string,float]]',
        }

    def ports_schema(self):
        return {
            "division_variable": {},
            "full_chromosome": {},
            "agents": {"*": {}},
            "media_id": {},
            "division_threshold": {
                "_default": self.parameters["division_threshold"],
                "_updater": "set",
                "_divider": {
                    "divider": "set_value",
                    "config": {"value": self.parameters["division_threshold"]},
                },
            },
        }

    def next_update(self, timestep, states):
        if states["division_threshold"] == "mass_distribution":
            current_media_id = states["media_id"]
            return {
                "division_threshold": (
                    states["division_variable"]
                    + self.dry_mass_inc_dict[current_media_id].asNumber(units.fg)
                    * self.division_mass_multiplier
                )
            }

        division_variable = states["division_variable"]

        if (division_variable >= states["division_threshold"]) and (
            states["full_chromosome"]["_entryState"].sum() >= 2
        ):
            daughter_ids = self.parameters["daughter_ids_function"](self.agent_id)
            daughter_updates = []
            for daughter_id in daughter_ids:
                config = dict(self.composer_config)
                config["agent_id"] = daughter_id
                config["seed"] = self.random_state.randint(0, RAND_MAX)
                # Regenerate composite to avoid unforeseen shared states
                composite = self.composer(config).generate()
                # Get shared process instances for partitioned processes
                process_states = {
                    process.parameters["process"].name: (process.parameters["process"],)
                    for process in composite.steps.values()
                    if "process" in process.parameters
                }
                initial_state = {
                    "process": process_states,
                }
                daughter_updates.append(
                    {
                        "key": daughter_id,
                        "processes": composite["processes"],
                        "steps": composite["steps"],
                        "flow": composite["flow"],
                        "topology": composite["topology"],
                        "initial_state": initial_state,
                    }
                )

            print(f"DIVIDE! MOTHER {self.agent_id} -> DAUGHTERS {daughter_ids}")

            return {
                "agents": {
                    "_divide": {"mother": self.agent_id, "daughters": daughter_updates}
                }
            }
        return {}


# Module-level cache for cell-as-Composite mode. Set by the caller
# (probe / driver) BEFORE divide fires. Bypasses the type system,
# which would otherwise try to walk these values as state and
# either (a) infer nonsense schemas from a schema Node tree, or
# (b) recursively realize the wrap_template as if it were a live
# process declaration in the cell tree.
_CELL_TREE_SCHEMA = None
_DAUGHTER_WRAP_TEMPLATE = None
# Reference to the live cell-Composite instance so CompositeDivision
# can read mother state directly at divide time without going through
# the wire system (which corrupts numpy struct arrays into dicts even
# with a precise port schema, AND introduces resolve_merges conflicts
# from wrapping the destination schema in Maybe at init).
_CELL_COMPOSITE_INSTANCE = None


def set_cell_composite_instance(instance):
    """Install the cell-Composite instance so CompositeDivision can
    read its own parent cell tree state at divide time. Single global
    slot — assumes one cell-Composite per process for now. For
    multi-cell setups (post-divide), each daughter would need its
    own slot keyed by agent_id."""
    global _CELL_COMPOSITE_INSTANCE
    _CELL_COMPOSITE_INSTANCE = instance


def set_cell_tree_schema(schema):
    """Install the cell tree schema for use by CompositeDivision at
    divide time."""
    global _CELL_TREE_SCHEMA
    _CELL_TREE_SCHEMA = schema


def set_daughter_wrap_template(template):
    """Install the cell-Composite process decl shape used to wrap
    each daughter cell tree before emitting ``_add`` through the
    bridge. Single global slot — assumes one cell-Composite type
    per process."""
    global _DAUGHTER_WRAP_TEMPLATE
    _DAUGHTER_WRAP_TEMPLATE = template


class CompositeDivision(Division):
    """v2-native subclass of Division.

    In process-bigraph, the ``_divide`` sentinel triggers a
    type-driven state split (``_handle_divide_sentinel`` →
    ``_divide_state``) that regenerates daughter stores and re-wires
    Link edges from the schema itself. The v1 Composer machinery
    (``self.composer(config).generate()``) is not needed — the
    framework pulls fresh instances directly from the schema.

    So this subclass:
      - drops ``composer`` and ``composer_config`` from its config
      - doesn't call ``generate()`` or build daughter composite dicts
      - emits a minimal ``_divide`` sentinel with just daughter keys,
        which is the format the v2 framework actually consumes
    """

    name = "ecoli-cell-division"

    config_schema = {
        'agent_id': 'string',
        'division_threshold': 'union[boolean,string,float]',
        'dry_mass_inc_dict': 'map[unum[fg]]',
        'seed': 'lineage_seed[integer]',
        'daughter_ids_function': 'function',
        # Single-lineage mode: emit only daughter 0 in the _divide
        # sentinel so the composite has exactly one agent at all
        # times. The agent_id grows by one '0' per division and the
        # cell line is followed in-place — no daughter proliferation,
        # no per-gen Python rebuild, no JSON shuttle.
        # Used by the composite_lineage engine.
        'single_daughters': 'boolean',
        # Override paths to per-process seed fields inside a cell.
        # Populated at build time. Each entry is the path from the
        # cell root down to a process's ``config.seed`` field, e.g.
        # ``['process', 'ecoli-chromosome-replication', 'config',
        # 'seed']`` for a SharedProcess-stored partitioned process,
        # or ``['allocator_1', 'config', 'seed']`` for a non-
        # partitioned step at the cell root. CompositeDivision uses
        # these to emit per-daughter seed overrides via
        # ``crc32(path[-2], daughter_seed)`` (path[-2] is the unique
        # process name) so each daughter ends up with independent
        # process RNGs (mirrors v1's per-gen Composer regeneration,
        # deterministically per lineage). Empty list disables per-
        # process seed reseeding (daughters share mother's seeds —
        # works but cells are correlated).
        'seed_paths': 'list[list[string]]',
        # Cell-as-Composite-as-Process mode. When set, divide:
        #   1. calls ``core.divide(cell_schema, mother_state)`` —
        #      the same type-driven walk ``_handle_divide_sentinel``
        #      uses, so daughters get the proper binomial bulk split,
        #      unique-molecule divide, divide_share / divide_reset
        #      semantics, etc.
        #   2. applies per-daughter overrides (``agent_id``, seeds)
        #      via ``_path_copy_merge``
        #   3. substitutes each daughter's resulting cell tree into
        #      ``daughter_wrap_template.config.state`` to produce a
        #      Composite-Process declaration of the same shape the
        #      probe/outer used for the mother
        #   4. emits ``_add``/``_remove`` sentinels — outer's
        #      ``realize()`` instantiates the daughter cell-Composites,
        #      same code path as the mother's initial instantiation
        #
        # Without these set, falls back to the legacy ``_divide``
        # sentinel path (mother lives at parent map, daughters land
        # in-place at the same map — vEcoli's standard architecture).
        #
        # ``daughter_wrap_template``: the cell-Composite process decl
        # shape (e.g. ``{'_type': 'process', 'address': 'local:Composite',
        # 'config': {'state': <REPLACED>, 'bridge': {...}, ...}, ...}``)
        # with the ``config.state`` slot reserved for substitution.
        #
        # ``cell_schema``: the cell tree's schema, needed for ``divide``
        # dispatch. Pass the same schema the cell-Composite's inner
        # state uses (e.g. ``composite.schema`` of the inner Composite).
        # ``cell_as_composite_mode`` is a simple boolean flag — when
        # True, divide uses the type-driven walk + wraps daughters
        # via the module-level template. The actual template and
        # schema come from ``set_daughter_wrap_template`` /
        # ``set_cell_tree_schema`` — NOT config — because storing
        # either in config would trigger the type system to walk
        # them as state (a schema Node tree would produce nonsense
        # inferred types; a process decl would get realized as a
        # live process inside the cell tree).
        'cell_as_composite_mode': 'boolean',
        # When in cell-as-Composite mode, the address used to wrap
        # daughter cell-Composites (``local:Composite`` or
        # ``ray:Composite``). Inherited by daughters via
        # _path_copy_merge — so changing this on the mother flows
        # transparently into every subsequent generation.
        'daughter_address': 'string',
    }

    def outputs(self):
        # Override the parent's outputs() to seed division_threshold
        # with the configured value. Without this, v2's framework leaves
        # state.division_threshold = None at startup, and the boolean
        # comparison ``division_variable >= None`` (or ``False >= False``
        # if the framework substitutes a typed default) short-circuits
        # to True — division then fires the moment chromosome
        # replication completes, halving the cell cycle.
        threshold = self.parameters.get("division_threshold")
        return {
            'agents': {},
            'division_threshold': {
                '_type': 'overwrite[union[boolean,string,float]]',
                '_default': threshold,
            },
        }

    def inputs(self):
        # Same default seeding for the input side so the read sees the
        # configured threshold, not None, when no upstream write has
        # happened yet. The reset semantics live on the outputs side
        # (the divider), not here.
        threshold = self.parameters.get("division_threshold")
        result = {
            'division_variable': 'union[boolean,float]',
            'full_chromosome': 'unique_array',
            'media_id': 'string',
            'division_threshold': {
                '_type': 'union[boolean,string,float]',
                '_default': threshold,
            },
        }
        # In cell-as-Composite mode the cell tree (parent of this step
        # in wrapped mode) must be readable so we can pass it through
        # ``core.divide(cell_schema, mother_state)`` and into the
        # daughter wrap template. Caller wires this to ``('..',)``.
        #
        # ``maybe[node]`` (rather than ``tree[node]``) is the loosest
        # acceptable schema — ``tree[node]`` triggers a recursive
        # serialization walk that converts the cell tree's numpy
        # struct array fields (bulk, unique molecules) into nested
        # dicts, breaking everything downstream that does
        # ``bulk['id']`` numpy column access.
        # NOTE: cell_as_composite_mode does NOT add a mother_state
        # input wire. The wire would project the cell tree's schema
        # via the type system, which (a) wraps the destination in
        # Maybe causing resolve conflicts at init AND (b) corrupts
        # numpy struct arrays into dicts via the view walk. Instead,
        # CompositeDivision reads mother state directly from the
        # ``_CELL_COMPOSITE_INSTANCE`` global at divide time —
        # bypassing the type system entirely. See
        # ``set_cell_composite_instance`` and update().
        return result

    def __init__(self, parameters=None):
        # Bypass Division.__init__ (which requires composer /
        # composer_config). Do Step.__init__ directly and populate
        # just the v2-relevant attributes.
        parameters = parameters or {}
        Step.__init__(self, parameters)
        self.agent_id = self.parameters["agent_id"]
        self.random_state = np.random.RandomState(seed=self.parameters["seed"])

        self.division_mass_multiplier = 1
        if self.parameters["division_threshold"] == "mass_distribution":
            division_random_seed = (
                binascii.crc32(b"CellDivision", self.parameters["seed"]) & 0xFFFFFFFF
            )
            division_random_state = np.random.RandomState(seed=division_random_seed)
            self.division_mass_multiplier = division_random_state.normal(
                loc=1.0, scale=0.1
            )
        self.dry_mass_inc_dict = self.parameters["dry_mass_inc_dict"]

    def update(self, states, interval=None):
        if states["division_threshold"] == "mass_distribution":
            current_media_id = states["media_id"]
            new_threshold = (
                states["division_variable"]
                + self.dry_mass_inc_dict[current_media_id].asNumber(units.fg)
                * self.division_mass_multiplier
            )
            # DEBUG: print the anchor when the threshold first
            # converts from "mass_distribution" string to a float.
            # Compare with v1's parquet cell_mass at the same tick.
            if os.environ.get('VECOLI_DEBUG_DIVISION'):
                import sys as _sys
                print(f"[div-debug] agent={self.agent_id} "
                      f"seed={self.parameters['seed']} "
                      f"anchor_mass={states['division_variable']:.10f} "
                      f"dry_mass_inc={self.dry_mass_inc_dict[current_media_id].asNumber(units.fg):.10f} "
                      f"multiplier={self.division_mass_multiplier:.10f} "
                      f"threshold={new_threshold:.10f}",
                      file=_sys.stderr, flush=True)
            return {"division_threshold": new_threshold}

        division_variable = states["division_variable"]
        if (division_variable >= states["division_threshold"]) and (
            states["full_chromosome"]["_entryState"].sum() >= 2
        ):
            daughter_ids = self.parameters["daughter_ids_function"](self.agent_id)
            # Single-lineage mode: drop daughter 1 so the composite
            # tracks one cell forward through divisions in-place.
            if self.parameters.get("single_daughters", False):
                daughter_ids = daughter_ids[:1]
            print(f"DIVIDE! MOTHER {self.agent_id} -> DAUGHTERS {daughter_ids}")

            # Per-daughter overrides: identity (agent_id) and seeds.
            # Identity must differ — without it, daughters re-emit
            # _divide for "mother {original}" forever (the key isn't
            # in agents anymore). Per-process seeds must differ so
            # the daughters' RNGs diverge (mirrors v1, where
            # composer.generate() reseeds every process from a
            # daughter-specific cli_seed). We use crc32(proc_name,
            # daughter_seed) — deterministic per lineage, not byte-
            # identical to v1's CamelCase _seedFromName scheme but
            # semantically the same.
            #
            # path-copy merge in _handle_divide_sentinel (see
            # bigraph_schema/methods/apply.py:_path_copy_merge)
            # allocates only the dict spines touched by these
            # overrides; everything else (sim_data refs, process
            # parameters, etc.) is shared by reference between
            # daughters and with the mother. So this is cheap.
            seed_paths = self.parameters.get("seed_paths", []) or []
            daughter_specs = []
            for daughter_id in daughter_ids:
                daughter_seed = int(self.random_state.randint(0, RAND_MAX))
                override = {
                    "division": {
                        "config": {
                            "agent_id": daughter_id,
                            "seed": daughter_seed,
                        },
                    },
                }
                for path in seed_paths:
                    # path = [..., '<proc_name>', 'config', 'seed']
                    if len(path) < 2:
                        continue
                    proc_name = path[-3] if len(path) >= 3 else path[0]
                    per_proc_seed = (
                        binascii.crc32(proc_name.encode("utf-8"),
                                       daughter_seed)
                        & 0xFFFFFFFF
                    )
                    cursor = override
                    for segment in path[:-1]:
                        cursor = cursor.setdefault(segment, {})
                    cursor[path[-1]] = per_proc_seed
                daughter_specs.append((daughter_id, override))

            cell_as_composite = self.parameters.get('cell_as_composite_mode')
            wrap_template = None
            if cell_as_composite:
                # First try the module-level template (set by the
                # driver). On Ray actors this is None because module
                # globals don't propagate to actor processes — fall
                # through to building the template from the parent
                # Composite's own config below.
                wrap_template = _DAUGHTER_WRAP_TEMPLATE
                if wrap_template is None:
                    # Build from parent Composite's config. The
                    # daughter cell-Composite has the SAME shape as
                    # the parent — schema, bridge, interface,
                    # parallel_processes — so we mirror them.
                    from process_bigraph.composite import (
                        get_current_composite)
                    parent = get_current_composite()
                    if parent is not None:
                        pcfg = getattr(parent, 'config', {}) or {}
                        # daughter_address: per-cell address override
                        # ('local:Composite', 'ray:Composite', or
                        # 'ray:EcoliCellComposite'). Set via the cell
                        # tree's division config; falls back to
                        # ``local:Composite``.
                        addr = self.parameters.get(
                            'daughter_address', 'local:Composite')
                        # cell_build_config: when present (set by
                        # probe on mother's cell_node config so it
                        # rides through here on the actor), include
                        # it so daughters can use the rebuild-from-
                        # sim_data path (EcoliCellComposite). Without
                        # this, daughter ship payload tries to carry
                        # live Process instances (with scipy lsoda's
                        # un-pickleable _queue.SimpleQueue) — and
                        # cloudpickle dies.
                        cbc = pcfg.get('cell_build_config')
                        daughter_config = {
                            'schema': pcfg.get('schema'),
                            'bridge': pcfg.get('bridge'),
                            'interface': pcfg.get('interface'),
                            'run_steps_on_init': pcfg.get(
                                'run_steps_on_init', True),
                            'parallel_processes': pcfg.get(
                                'parallel_processes', False),
                        }
                        if cbc:
                            daughter_config['cell_build_config'] = cbc
                        wrap_template = {
                            '_type': 'process',
                            'address': addr,
                            'config': daughter_config,
                            'inputs': {},
                            'outputs': {'agents': ['..']},
                            'interval': 1.0,
                        }
            if wrap_template:
                # Cell-as-Composite mode: do the divide here (so we
                # can build a fully-wrapped Composite-Process decl
                # for each daughter), then emit ``_add``/``_remove``
                # with those decls. Outer's realize() instantiates
                # the daughter Composites — same path as the mother's
                # initial instantiation.
                from copy import deepcopy
                from bigraph_schema.methods.divide import divide as _divide_walk
                from bigraph_schema.methods.apply import _path_copy_merge

                cell_schema = _CELL_TREE_SCHEMA
                if cell_schema is None:
                    # Module global not set (typical for Ray actors —
                    # globals don't propagate from driver). Extract
                    # from parent Composite's config.schema which has
                    # the cell tree shape inlined at
                    # ``agents._value`` (or, when the agent was
                    # already realized into ``self.schema``, look there
                    # too — it carries the same dict).
                    import sys as _dbg_sys
                    from process_bigraph.composite import (
                        get_current_composite)
                    parent_c = get_current_composite()
                    _dbg_sys.stderr.write(
                        f'[div-schema] parent_c={type(parent_c).__name__ if parent_c else None}\n')
                    if parent_c is not None:
                        cfg = getattr(parent_c, 'config', {}) or {}
                        psch = cfg.get('schema', {}) if isinstance(cfg, dict) else {}
                        _dbg_sys.stderr.write(
                            f'[div-schema] config.schema keys: '
                            f'{list(psch.keys()) if isinstance(psch, dict) else type(psch).__name__}\n')
                        if isinstance(psch, dict):
                            agents_field = psch.get('agents', {})
                            _dbg_sys.stderr.write(
                                f'[div-schema] config.schema[agents] type: {type(agents_field).__name__}, '
                                f'keys: {list(agents_field.keys()) if isinstance(agents_field, dict) else "?"}\n')
                            if isinstance(agents_field, dict):
                                cell_schema = agents_field.get('_value')
                                _dbg_sys.stderr.write(
                                    f'[div-schema] _value type: {type(cell_schema).__name__}, '
                                    f'len: {len(cell_schema) if hasattr(cell_schema, "__len__") else "?"}\n')
                        # Fallback to self.schema (realized) — same
                        # dict, but on the realized tree.
                        if cell_schema is None or (
                                isinstance(cell_schema, dict) and len(cell_schema) < 5):
                            rsch = getattr(parent_c, 'schema', {}) or {}
                            _dbg_sys.stderr.write(
                                f'[div-schema] self.schema keys: '
                                f'{list(rsch.keys()) if isinstance(rsch, dict) else type(rsch).__name__}\n')
                            if isinstance(rsch, dict):
                                ragents = rsch.get('agents', {})
                                _dbg_sys.stderr.write(
                                    f'[div-schema] self.schema[agents] type: '
                                    f'{type(ragents).__name__}, keys: '
                                    f'{list(ragents.keys()) if isinstance(ragents, dict) else "?"}\n')
                                if isinstance(ragents, dict):
                                    # Pull the realized per-entry
                                    # schema at the mother's agent_id.
                                    if self.agent_id in ragents:
                                        cell_schema = ragents[self.agent_id]
                                        _dbg_sys.stderr.write(
                                            f'[div-schema] used self.schema[agents][{self.agent_id}], '
                                            f'len={len(cell_schema) if hasattr(cell_schema, "__len__") else "?"}\n')
                                    elif '_value' in ragents:
                                        cell_schema = ragents['_value']
                                        _dbg_sys.stderr.write(
                                            f'[div-schema] used self.schema[agents][_value], '
                                            f'len={len(cell_schema) if hasattr(cell_schema, "__len__") else "?"}\n')
                    _dbg_sys.stderr.flush()
                if cell_schema is None:
                    raise RuntimeError(
                        "cell_as_composite_mode requires the cell tree "
                        "schema to be available — either via "
                        "set_cell_tree_schema(...) (module global on "
                        "the driver) or inlined at "
                        "parent.config.schema['agents']['_value'] "
                        "(typical for Ray actors).")
                # Read mother state via the ``current_composite_var``
                # contextvar set by ``Composite.run``. Works for local
                # AND for Ray actors (each actor's Composite sets its
                # own contextvar — no need to propagate a module global
                # across process boundaries). Falls back to the
                # module-level ``_CELL_COMPOSITE_INSTANCE`` for
                # backward compatibility with non-Ray drivers.
                from process_bigraph.composite import (
                    get_current_composite)
                parent_composite = get_current_composite() or _CELL_COMPOSITE_INSTANCE
                if parent_composite is None:
                    raise RuntimeError(
                        "cell_as_composite_mode requires the parent "
                        "Composite to be set on the ``current_composite_var`` "
                        "contextvar — either by running inside its ``run`` "
                        "(automatic) or by calling "
                        "set_cell_composite_instance() explicitly.")
                mother_state = parent_composite.state['agents'][
                    self.agent_id]

                # Type-driven divide walk produces 2 baseline daughters
                # (binomial bulk split, unique-molecule divide,
                # divide_share/divide_reset semantics — same as
                # _handle_divide_sentinel).
                baselines = _divide_walk(
                    cell_schema, mother_state,
                    context=mother_state, path=())

                # For single-lineage mode we still keep len(baselines)
                # == 2 from the walk; just take the first when only
                # one daughter requested.
                daughter_add_entries = []
                # DEBUG: dump first daughter state + wrap_template to
                # disk so we can test daughter Composite construction
                # in isolation (without re-running mother for 7 wall
                # min each time). Triggered by env var
                # ``VECOLI_DUMP_DAUGHTER`` = output path.
                import os as _os
                _dump_path = _os.environ.get('VECOLI_DUMP_DAUGHTER')
                import sys as _sys
                _DEBUG_DIVIDE = _os.environ.get('VECOLI_DEBUG_DIVIDE')
                for i, (daughter_id, override) in enumerate(daughter_specs):
                    baseline = baselines[i] if i < len(baselines) else baselines[0]
                    daughter_state = (
                        _path_copy_merge(baseline, override)
                        if override else baseline)
                    if _DEBUG_DIVIDE:
                        _sys.stderr.write(
                            f'[divide-debug] daughter={daughter_id} '
                            f'state top keys: {sorted(daughter_state.keys())[:8]}\n')
                        bulk = daughter_state.get('bulk')
                        if hasattr(bulk, 'dtype'):
                            _sys.stderr.write(
                                f'[divide-debug]   bulk.dtype.names={bulk.dtype.names}\n')
                        _sys.stderr.flush()
                    # Substitute daughter state into the wrap template's
                    # config.state slot. deepcopy so we don't mutate
                    # the shared template between daughters.
                    #
                    # Wrap under ``{agents: {daughter_id: daughter_state}}``
                    # so the daughter Composite's state has the same
                    # wrapped-mode shape as the mother: wires inside
                    # the cell tree were designed for ``agents.<id>``
                    # paths (e.g. division.outputs.agents wires to
                    # ``('..', '..', 'agents')``). Without the wrap,
                    # daughter state would be flat at root and these
                    # wires would resolve to wrong / out-of-bounds
                    # paths.
                    # Project daughter_state through a DATA-ONLY
                    # whitelist. A "process key" is identified by
                    # the VALUE being a dict that contains either
                    # ``address`` or ``instance`` — that's the
                    # universal shape of a process declaration in
                    # state. The schema is also resolved (a Schema
                    # Node, not a plain dict) so checking via the
                    # data is more robust than via the schema.
                    # Process subtrees get re-instantiated on the
                    # driver via the wrap_template's config.schema.
                    # Pure-data keys (bulk, boundary, listeners,
                    # division_threshold, ...) carry the per-cell
                    # state through divide.
                    def _looks_like_process(v):
                        if not isinstance(v, dict):
                            return False
                        # Process/Step declarations always have one
                        # of these keys when they appear in state.
                        return 'address' in v or 'instance' in v
                    # DATA-ONLY projection: ship only the divided data
                    # fields that the daughter EcoliCellComposite uses
                    # as ``initial_state`` overlay. Drop all process
                    # declarations at TOP LEVEL — the daughter actor
                    # rebuilds the process tree fresh via
                    # ``build_ecoli_document(core, sim_config, lsd)``.
                    # No live Process instances ever cross the actor
                    # boundary; pickling is trivial.
                    _DAUGHTER_NEVER_SHIP = frozenset({
                        # Re-populated on the daughter actor by
                        # ``load_sim_data_provider`` type-provider.
                        'sim_data_objects',
                        # Framework runtime, rebuilt by Composite
                        # initialize on the daughter side.
                        'process',
                        'process_state',
                        'step_flow',
                        'next_update_time',
                    })
                    def _is_process_decl(v):
                        return isinstance(v, dict) and (
                            'address' in v or 'instance' in v)
                    daughter_state_ship = {
                        k: v for k, v in daughter_state.items()
                        if not _is_process_decl(v)
                        and k not in _DAUGHTER_NEVER_SHIP
                    } if isinstance(daughter_state, dict) else daughter_state
                    if i == 0 and _DEBUG_DIVIDE:
                        mother_keys = sorted(daughter_state.keys()) if isinstance(daughter_state, dict) else []
                        shipped_keys = sorted(daughter_state_ship.keys()) if isinstance(daughter_state_ship, dict) else []
                        dropped = sorted(set(mother_keys) - set(shipped_keys))
                        _sys.stderr.write(
                            f'[whitelist] daughter={daughter_id} '
                            f'mother_keys={len(mother_keys)} '
                            f'shipped_keys={len(shipped_keys)} '
                            f'dropped={len(dropped)}\n')
                        _sys.stderr.flush()
                    wrapped = deepcopy(wrap_template)
                    wrapped.setdefault('config', {})['state'] = {
                        'agents': {daughter_id: daughter_state_ship},
                    }
                    # Per-daughter agent_id in cell_build_config so
                    # EcoliCellComposite builds the right shape for
                    # this daughter (not the mother's '0').
                    if 'cell_build_config' in wrapped['config']:
                        cbc = dict(wrapped['config']['cell_build_config'])
                        cbc['agent_id'] = daughter_id
                        wrapped['config']['cell_build_config'] = cbc
                    daughter_add_entries.append(
                        (daughter_id, {'cell': wrapped}))
                    # In-process daughter-construct test: when env var
                    # ``VECOLI_TEST_DAUGHTER`` is set, try to instantiate
                    # the daughter Composite RIGHT HERE (instead of
                    # propagating through outer.realize). Lets us SIGUSR1
                    # the live python immediately to find the hang
                    # without waiting for OUTER's apply path.
                    if _os.environ.get('VECOLI_TEST_DAUGHTER') and i == 0:
                        import sys as _ds, time as _dt
                        import faulthandler as _fh
                        from process_bigraph import Composite as _C
                        _ds.stderr.write(
                            f'[in-proc] starting daughter Composite '
                            f'instantiation PID={_os.getpid()}\n')
                        _ds.stderr.flush()
                        # Auto-dump traceback after 60s if still hanging.
                        # No external signal needed.
                        _fh.dump_traceback_later(60, repeat=True)
                        _t = _dt.perf_counter()
                        try:
                            _dC = _C(wrapped['config'], core=self.core)
                            _ds.stderr.write(
                                f'[in-proc] ✅ daughter built in '
                                f'{_dt.perf_counter()-_t:.1f}s; '
                                f'state keys: {sorted(_dC.state.keys())[:8]}\n')
                        except Exception as _e:
                            _ds.stderr.write(
                                f'[in-proc] ❌ daughter init failed '
                                f'after {_dt.perf_counter()-_t:.1f}s: '
                                f'{type(_e).__name__}: {str(_e)[:300]}\n')
                        _fh.cancel_dump_traceback_later()
                        _ds.stderr.flush()
                        raise SystemExit(0)

                return {
                    "agents": {
                        "_remove": [self.agent_id],
                        "_add": daughter_add_entries,
                    }
                }

            return {
                "agents": {
                    "_divide": {
                        "mother": self.agent_id,
                        "daughters": daughter_specs,
                    }
                }
            }
        return {}


class DivisionDetected(Exception):
    pass


class StopAfterDivision(Process):
    """
    Detect division and raise an exception that must be caught.

    NOTE: This is a vivarium-only process. In the composite engine,
    division detection is handled by the driver loop checking agent count.
    """

    name = "stop-after-division"

    config_schema = {}

    def inputs(self):
        return {
            'agents': 'map[node]',
        }

    def outputs(self):
        return {}

    def ports_schema(self):
        return {
            "agents": {"*": {}},
        }

    def calculate_timestep(self, interval_or_state, state=None):
        if state is None:
            return 0
        return self.parameters.get('time_step', 1.0)

    def update_condition(self, timestep, states):
        if len(states["agents"]) > 1:
            raise DivisionDetected("More than one cell in agents store.")
        return False

    def next_update(self, timestep, states):
        raise RuntimeError("This should never be called.")
