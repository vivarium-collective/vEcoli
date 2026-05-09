"""``EcoliCellProcess``: one E. coli cell as a self-contained Process.

Wraps the vEcoli simulation for one cell at one generation:

  * ``__init__`` builds the inner :py:class:`Composite` via
    :py:func:`~ecoli.composites.ecoli_composite.build_ecoli_document`
    with the per-gen seed (``lineage_seed + gen``, derived from
    ``len(agent_id)``) and the daughter's split bulk/unique state
    (passed in via ``initial_state``).
  * ``update(state, interval)`` ticks the inner Composite for
    ``interval`` seconds and returns the cell's externally-visible
    state.
  * (TODO — second pass) When the inner ``CompositeDivision`` step
    fires, emits ``_divide`` to the outer composite so the framework
    creates daughter ``EcoliCellProcess`` instances via the standard
    Process divider (strip ``instance`` from the daughter decl,
    re-instantiate via ``__init__`` with the daughter's config).

Used as the unit of work in:

  * **composite_lineage** (single-process): one EcoliCellProcess
    per generation (managed loop in
    :py:meth:`~ecoli.experiments.ecoli_master_sim.EcoliSim._run_composite_lineage`).
  * **composite_lineage_mp** (multiprocessing): one MP worker per
    lineage_seed; worker spawns sequential EcoliCellProcess
    instances (managed loop) per gen, sharing parent's pre-loaded
    sim_data via fork copy-on-write.
  * **composite_lineage_ray** (Ray actors): same as MP but with
    ``@ray.remote`` actors. Pickle stays in the actor's address
    space across gens.
  * **colony sims** (future): outer composite has many
    EcoliCellProcess agents in a ``map[EcoliCellProcess]``;
    division at the outer level naturally creates more.
"""
from copy import deepcopy
from typing import Any, Optional

from process_bigraph import Composite, Process

from ecoli.library.bigraph_types import ECOLI_TYPES
from ecoli.library.sim_data import LoadSimData


class EcoliCellProcess(Process):
    """One E. coli cell, wrapped as a Process.

    See module docstring for design notes.

    Required config:
      ``lineage_seed``: experiment-level base seed (CLI ``--seed``).
        Per-cell seed is derived as ``lineage_seed + (len(agent_id) - 1)``,
        matching ``runscripts/nextflow/sim.nf``'s
        ``seed_d0 = sim_seed + 1`` per-gen scheme.
      ``agent_id``: cell's lineage path (``"0"`` for gen 0, ``"00"``
        for gen 1 daughter 0, ``"000"`` for gen 2, etc.). Drives both
        the per-gen seed and the parquet partition path.
      ``sim_data_path``: path to ``simData.cPickle``.

    Optional config:
      ``initial_state``: daughter's split bulk/unique cell state
        from a previous gen's divide. Overlaid onto the freshly-
        built per-gen cell state. ``None`` for gen 0 (which uses
        sim_data's generated initial state).
      ``sim_data``: pre-loaded sim_data object. When provided,
        skips the pickle load (used by MP/Ray paths to share a
        parent-loaded pickle across many cells).
      ``sim_config``: full vEcoli sim config dict (process configs,
        topology, etc.). Required at instantiation; defaults to a
        copy of ``configs/default.json`` if not provided.
    """

    config_schema = {
        'lineage_seed': 'integer',
        'agent_id': 'string',
        'sim_data_path': 'string',
        # ``tree[node]`` matches EcoliProcess's ``state`` schema; it
        # accepts the heterogeneous nested cell-state dict produced
        # by daughter handoff (bulk arrays, unique molecules,
        # listeners, environment, etc.).
        'initial_state': 'tree[node]',
        # ``sim_data`` is the pre-loaded SimulationDataEcoli object;
        # ``maybe[tree[node]]`` keeps it optional and opaque.
        'sim_data': 'maybe[tree[node]]',
        # ``sim_config`` is the user-resolved full sim config that
        # would normally be passed to ``build_ecoli_document``
        # (process classes registered, topology resolved, etc.).
        'sim_config': 'tree[node]',
    }

    def initialize(self, config=None):
        """Build the inner per-cell Composite from the config.

        Called by ``Edge.__init__`` after the framework has filled in
        defaults from ``config_schema``. ``self.config`` is the
        filled config dict; ``self.core`` is the framework's type
        registry.
        """
        cfg = self.config
        self.agent_id = str(cfg['agent_id'])
        self.lineage_seed = int(cfg['lineage_seed'])
        # Per-gen seed: gen 0 (agent_id="0") → seed = lineage_seed+0;
        # gen N daughter (agent_id length N+1) → seed = lineage_seed+N.
        self.gen = max(0, len(self.agent_id) - 1)
        self.cell_seed = self.lineage_seed + self.gen

        # Build the inner Composite for this cell at this generation.
        # All per-gen state (process configs with new RNG seeds,
        # allocator_rng, next_update_time defaults, sim_data_objects
        # store, step flow) is set up here. If ``initial_state`` is
        # provided, daughter's split bulk/unique are overlaid onto
        # the fresh build. Bit-parity with the per-gen Nextflow path
        # by construction (same code, same seed, same overlay).
        self._inner = self._build_inner_composite()
        self._inner.to_run = []

    # ----- Process interface --------------------------------------

    def inputs(self):
        # No external inputs for the lineage / MP / Ray cases — each
        # cell is self-contained. Colony sims will add env / boundary
        # ports here.
        return {}

    def outputs(self):
        # Minimum state the outer can observe. ``divided`` flips on
        # the tick where CompositeDivision fires inside; outer will
        # use this to spawn daughter EcoliCellProcess instances.
        # (Wired in the second-pass divide-emission work.)
        return {
            'global_time': 'float',
            'divided': 'boolean',
        }

    def update(self, state, interval=None):
        if interval is None:
            interval = 1.0
        pre_agent_count = len(self._inner.state.get('agents', {}))
        self._inner.run(float(interval))
        post_agent_count = len(self._inner.state.get('agents', {}))
        divided = post_agent_count > pre_agent_count
        return {
            'global_time': float(
                self._inner.state.get('global_time', 0.0)),
            'divided': bool(divided),
        }

    # ----- Read-only accessors for an outer runner ----------------

    @property
    def inner_state(self):
        """Live state dict of the inner Composite. Used by an outer
        runner to read bulk / unique / listeners for parquet emit."""
        return self._inner.state

    @property
    def inner_composite(self):
        """The inner :py:class:`Composite` instance. Used by an outer
        runner that needs to call ``.run()`` directly (e.g. the
        existing ``run_to_division`` helper)."""
        return self._inner

    # ----- Internal -----------------------------------------------

    def _build_inner_composite(self) -> Composite:
        """Build the inner per-cell Composite from sim_data + config.

        Mirrors the gen-N branch of
        :py:meth:`~ecoli.experiments.ecoli_master_sim.EcoliSim._run_composite_lineage`:
        construct a per-gen ``LoadSimData`` (sharing
        ``self.config['sim_data']`` if provided), call
        ``build_ecoli_document`` with the per-gen seed and agent_id,
        wrap in a ``Composite``.
        """
        from ecoli.composites.ecoli_composite import build_ecoli_document

        cfg = self.config
        sim_config = deepcopy(cfg.get('sim_config') or {})
        sim_config['seed'] = self.cell_seed
        sim_config['agent_id'] = self.agent_id
        sim_config['sim_data_path'] = cfg['sim_data_path']
        initial_state = cfg.get('initial_state')
        if initial_state:
            sim_config['initial_state'] = initial_state
            sim_config['initial_state_file'] = None

        # Per-gen LoadSimData. ``sim_data=`` reuses the shared pickle
        # when provided (MP/Ray case); otherwise pickle is loaded
        # from ``sim_data_path``.
        lsd_kwargs = dict(sim_config)
        shared = cfg.get('sim_data')
        if shared is not None:
            lsd_kwargs['sim_data'] = shared
        gen_lsd = LoadSimData(**lsd_kwargs)

        state = build_ecoli_document(
            self.core, sim_config, load_sim_data=gen_lsd)
        return Composite(
            {'schema': {}, 'state': state,
             'run_steps_on_init': True},
            core=self.core)
