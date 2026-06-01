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
import os
from copy import deepcopy
from typing import Any, Optional

from process_bigraph import Composite, Process

from ecoli.library.bigraph_types import (
    ECOLI_TYPES, get_cached_load_sim_data)
from ecoli.library.sim_data import LoadSimData


class EcoliCellComposite(Composite):
    """Cell-as-Composite that builds its cell tree from sim_data on
    the receiving process instead of accepting a pre-built state.

    USE CASE: daughter cell-Composites in the Ray cell-as-Composite
    architecture. The mother's CompositeDivision emits a SMALL data-
    only overlay (divided bulk + unique molecules + per-cell seeds
    + division_threshold) as the daughter declaration. The receiving
    actor builds an ``EcoliCellComposite``, which calls
    ``build_ecoli_document(core, sim_config, lsd)`` to construct a
    fresh process tree (no un-pickleable Process instances carried
    across the boundary) and overlays the divided data.

    This avoids the cloudpickle failure on Process instances with
    scipy lsoda's ``_queue.SimpleQueue``, threading locks, etc. —
    those instances are built locally on each actor from sim_data.

    Required config (in addition to standard Composite fields):
        ``cell_build_config``: dict with:
            - ``sim_config``: vEcoli sim config dict
            - ``agent_id``: daughter's lineage id (e.g. ``'00'``)
            - ``sim_data_path``: path to ``simData.cPickle``
        Existing ``config['state']['agents'][<agent_id>]`` (if any)
        is used as the ``initial_state`` overlay for that agent.
    """

    config_schema = {
        **Composite.config_schema,
        # ``maybe[tree[node]]`` keeps the field optional and opaque
        # to the type system (it's only consumed by our own init).
        'cell_build_config': 'maybe[tree[node]]',
    }

    def initialize(self, config: Optional[dict] = None) -> None:
        import sys as _sys
        import time as _ti
        cell_build = self.config.get('cell_build_config') if isinstance(
            self.config, dict) else None
        if cell_build:
            from ecoli.composites.ecoli_composite import build_ecoli_document

            sim_config = dict(cell_build.get('sim_config', {}))
            agent_id = cell_build.get('agent_id', '0')
            sim_data_path = cell_build.get('sim_data_path')
            sim_config['agent_id'] = agent_id
            sim_config.setdefault('seed', 0)
            sim_config['sim_data_path'] = sim_data_path

            # Use shipped state[agents][agent_id] as the data overlay
            # (divided bulk/unique/threshold/...). _get_initial_state
            # in build_ecoli_document picks this up via
            # ``initial_state``.
            agents_state = (
                self.config.get('state', {}).get('agents')
                if isinstance(self.config.get('state'), dict) else None)
            divided = (agents_state.get(agent_id)
                       if isinstance(agents_state, dict) else None)
            if divided:
                sim_config['initial_state'] = {
                    'agents': {agent_id: divided}
                }
            _t0 = _ti.perf_counter()
            _sys.stderr.write(
                f'[ecc-init] agent={agent_id} start '
                f'(overlay_keys={len(divided) if isinstance(divided, dict) else 0})\n')
            _sys.stderr.flush()

            lsd = get_cached_load_sim_data(sim_data_path, sim_config)
            _sys.stderr.write(
                f'[ecc-init] agent={agent_id} '
                f'lsd-loaded ({_ti.perf_counter()-_t0:.1f}s)\n')
            _sys.stderr.flush()

            cell_doc = build_ecoli_document(
                self.core, sim_config, load_sim_data=lsd)
            _sys.stderr.write(
                f'[ecc-init] agent={agent_id} '
                f'build_ecoli_document done ({_ti.perf_counter()-_t0:.1f}s)\n')
            _sys.stderr.flush()

            # Replace the placeholder state with the freshly-built
            # cell_doc. Now super().initialize sees the full cell
            # tree as if we'd built it locally from scratch.
            self.config['state'] = cell_doc

        super().initialize(config)
        if cell_build:
            _sys.stderr.write(
                f'[ecc-init] agent={agent_id} '
                f'composite-ready ({_ti.perf_counter()-_t0:.1f}s)\n')
            _sys.stderr.flush()


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
        if os.environ.get('VECOLI_DEBUG_DIVIDE'):
            import sys as _sys
            try:
                bulk = state.get('agents', {}).get(self.agent_id, {}).get('bulk')
                if bulk is not None and hasattr(bulk, 'dtype') and bulk.dtype.names and 'count' in bulk.dtype.names:
                    print(f'[divide-debug] _build_inner_composite agent_id={self.agent_id} '
                          f'BEFORE Composite() bulk.count.sum={int(bulk["count"].sum())}',
                          file=_sys.stderr, flush=True)
            except Exception as e:
                print(f'[divide-debug] _build_inner_composite check err: {e}',
                      file=_sys.stderr, flush=True)
        composite = Composite(
            {'schema': {}, 'state': state,
             'run_steps_on_init': True},
            core=self.core)
        if os.environ.get('VECOLI_DEBUG_DIVIDE'):
            import sys as _sys
            try:
                bulk = composite.state.get('agents', {}).get(self.agent_id, {}).get('bulk')
                if bulk is not None and hasattr(bulk, 'dtype') and bulk.dtype.names and 'count' in bulk.dtype.names:
                    print(f'[divide-debug] _build_inner_composite agent_id={self.agent_id} '
                          f'AFTER Composite() bulk.count.sum={int(bulk["count"].sum())}',
                          file=_sys.stderr, flush=True)
                else:
                    print(f'[divide-debug] _build_inner_composite AFTER Composite() bulk={type(bulk).__name__}',
                          file=_sys.stderr, flush=True)
            except Exception as e:
                print(f'[divide-debug] _build_inner_composite AFTER err: {e}',
                      file=_sys.stderr, flush=True)
        return composite
