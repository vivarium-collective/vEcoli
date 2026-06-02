"""Per-cell Parquet emitter Step.

Registered via topology_registry so build_ecoli_document can add it
as a regular step alongside mass_listener etc. — same paradigm,
schemas merge cleanly. Post-injection into a built cell_doc
corrupts sibling steps' precompile_link because the schema merge
isn't part of the co-build pass.

Wires inside the cell-Composite's state tree as a Step. Triggers
every tick (its ``triggers()`` declares only ``global_time``, which
changes once per tick). On fire, reads listeners/bulk/process_state
from sibling paths in the cell tree, projects ``bulk['count']`` to
plain int64 (parquet column parity with v1), and calls
``ParquetEmitter.emit(...)`` with a row matching v1's history schema.

Each cell-Composite instance gets its OWN emitter instance (created
in ``__init__`` from config). After divide, each daughter cell-
Composite gets a fresh instance with a fresh emitter writing under
its own ``agent_id`` partition — the same multi-stream pattern v1
uses for colony Parquet output.

Output layout::

    {out_dir}/{experiment_id}/history/<hive partition path>/<batch>.pq

Tail by watching the directory: file count grows every
``batch_size`` (default 400) emits.
"""
from typing import Any, Dict

import numpy as np
from process_bigraph.composite import Step

from ecoli.processes.registries import topology_registry


# Register topology so build_ecoli_document can pick this step up
# alongside the rest of the cell tree (same paradigm as mass_listener,
# dna_supercoiling, etc.). Wires are relative to the agent root —
# the framework prepends ``agents/<id>/`` when realizing.
NAME = "parquet_emitter"
TOPOLOGY = {
    "global_time": ("global_time",),
    "bulk": ("bulk",),
    "listeners": ("listeners",),
    "process_state": ("process_state",),
}
topology_registry.register(NAME, TOPOLOGY)


class CellParquetEmitter(Step):
    """Per-cell ParquetEmitter wired as a Step inside the cell tree."""

    config_schema = {
        # Required parquet-emitter config (mirrors EcoliSim.emitter_arg)
        'out_dir': 'string',
        'experiment_id': 'string',
        # Identifies this cell — sets the hive partition path for its
        # parquet output. Daughters get fresh agent_ids via the wrap
        # template / divide override, so each writes to its own
        # subdirectory automatically.
        'agent_id': 'string',
        # Optional metadata (passed through to v1's configuration row)
        'variant': 'integer{0}',
        'lineage_seed': 'integer{0}',
        # Tuning
        'batch_size': 'integer{400}',
        # Nested-dict input schemas. Build these at probe-time from
        # the cell tree's actual schema using ``schema_to_plain``:
        # walk the Schema Node tree, render leaves to type-strings,
        # keep branches as plain dicts. The framework's wire
        # projection requires NESTED DICTS (not string types) to
        # include the keys in the projected state passed to
        # ``update`` — see memory: wire-projection-requires-nested-dicts.
        'listeners_schema': 'tree[node]',
        'process_state_schema': 'tree[node]',
        # Per-listener metadata (cistron IDs, gene names, reaction
        # lists, etc.) collected at colony probe time via
        # collect_output_metadata_from_composite. Embedded into the
        # configuration parquet row as output_metadata__<path>
        # columns. Required for multiseed / multigeneration analyses
        # that query field_metadata (ecocyc_table,
        # subgenerational_expression_table, ribosome_components,
        # selected_fluxes).
        #
        # IMPORTANT: serialized as a JSON STRING here (not a nested
        # dict) because the bigraph-schema realize walker recurses
        # into ``tree[node]`` config fields and hits leaves like
        # ``np.str_('4FE-4S[c]')`` for which no ``realize`` method is
        # registered (plum dispatch fails with NotFoundLookupError).
        # Shipping as a single string makes the schema opaque to the
        # walker; we json.loads it back in __init__ before passing
        # the actual dict to ParquetEmitter.emit.
        'output_metadata_json': 'maybe[string]',
    }

    def __init__(self, config=None, core=None):
        super().__init__(config, core=core)
        from ecoli.library.parquet_emitter import ParquetEmitter
        # Route to ``out_uri`` for cloud (s3://, gs://) so
        # ParquetEmitter uses fsspec; else local ``out_dir`` (which
        # gets ``os.path.abspath``'d). Without this branch, an
        # ``s3://...`` path passed as ``out_dir`` silently writes
        # to a bogus local path like ``/.../s3:/...``.
        _path = self.config['out_dir']
        _emitter_cfg = {
            'batch_size': self.config.get('batch_size', 400),
            'threaded': True,
        }
        if isinstance(_path, str) and (
                _path.startswith('s3://') or _path.startswith('gs://')):
            _emitter_cfg['out_uri'] = _path
        else:
            _emitter_cfg['out_dir'] = _path
        self._emitter = ParquetEmitter(_emitter_cfg)
        # Register an atexit handler to flush the emitter buffer at
        # interpreter shutdown. More reliable than ``__del__``: __del__
        # may never fire on Ray actor exit (abrupt teardown), but
        # atexit handlers run on normal shutdown including
        # SIGTERM-handled Ray actor stops. The handler is idempotent
        # — if __del__ flushed first, the second flush is a no-op.
        import atexit as _atexit
        _atexit.register(self._safe_finalize)
        # Configuration emit installs the partition path. Minimum
        # metadata keys to satisfy v1's column expectations; the
        # ``output_metadata`` block (when present) is what gets
        # flattened into ``output_metadata__listeners__...`` columns
        # downstream — see ``parquet_emitter.flatten_dict`` +
        # ``METADATA_PREFIX``. Without it, ``field_metadata`` lookups
        # in multiseed / multigeneration analyses fail with Binder
        # Errors.
        config_data = {
            'metadata': {
                'experiment_id': self.config['experiment_id'],
                'variant': self.config.get('variant', 0),
                'lineage_seed': self.config.get('lineage_seed', 0),
                'agent_id': str(self.config['agent_id']),
                'initial_global_time': 0.0,
            }
        }
        out_meta_json = self.config.get('output_metadata_json')
        if out_meta_json:
            import json as _json
            config_data['metadata']['output_metadata'] = _json.loads(
                out_meta_json)
        self._emitter.emit({
            'table': 'configuration',
            'data': config_data,
        })
        self._last_emit_time = None

    def inputs(self):
        # Standard ecoli pattern (cf tf_binding, mass_listener):
        # ``bulk`` uses the registered ``bulk_array`` type; the
        # listeners / process_state subtrees are passed in as
        # nested-dict schemas built from the cell tree's own
        # schema at probe time (see schema_types.schema_node_to_plain_dict).
        return {
            'global_time': 'float',
            'bulk': 'bulk_array',
            'listeners': self.config['listeners_schema'],
            'process_state': self.config['process_state_schema'],
        }

    def triggers(self):
        # Only re-fire on global_time changes (every tick), not on
        # every listener update. Without this, the step would fire
        # many times per tick and produce duplicate rows.
        return {'global_time': 'float'}

    def outputs(self):
        # Emitter writes to disk; nothing flows back into state.
        return {}

    def update(self, state):
        global_t = float(state.get('global_time', 0.0))
        # Skip the run-steps-on-init fire: the cell composite is built
        # with ``run_steps_on_init=True`` (so mass_listener etc. populate
        # initial state for downstream consumers), but on that FIRST
        # invocation the parquet_emitter sees default-zero listener
        # values that aren't real cell state. Emitting them produces
        # a row 0 with mass=0 etc., which breaks every analysis that
        # normalizes by row 0 (mass_fraction_summary divides by
        # mass[v][0] → inf/nan → blank Vega chart).
        if self._last_emit_time is None:
            self._last_emit_time = global_t
            return {}
        # Skip duplicate emits (defensive — triggers() should prevent
        # this but the step may be invoked again with the same time).
        if global_t <= self._last_emit_time:
            return {}
        self._last_emit_time = global_t

        # Project bulk to a flat count array (matches v1's parquet
        # column: ``List[Int64]`` of molecule counts).
        bulk = state.get('bulk')
        if (bulk is not None and isinstance(bulk, np.ndarray)
                and bulk.dtype.names
                and 'count' in bulk.dtype.names):
            bulk_out = np.asarray(bulk['count'], dtype=np.int64)
        else:
            bulk_out = bulk

        # Wrap in the same ``agents`` shape EcoliSim emits so
        # downstream analysis queries unchanged. Single agent per
        # cell-Composite (this cell is the agent).
        agent_subtree = {}
        if state.get('listeners') is not None:
            agent_subtree['listeners'] = state['listeners']
        if bulk_out is not None:
            agent_subtree['bulk'] = bulk_out
        if state.get('process_state') is not None:
            agent_subtree['process_state'] = state['process_state']
        if not agent_subtree:
            return {}

        self._emitter.emit({
            'table': 'history',
            'data': {
                'agents': {str(self.config['agent_id']): agent_subtree},
                'time': global_t,
            }
        })
        return {}

    def _safe_finalize(self) -> None:
        """Idempotent finalize: flush buffer + write _success.pq marker.
        Safe to call multiple times (from __del__ AND atexit)."""
        em = getattr(self, '_emitter', None)
        if em is None:
            return
        try:
            em.success = True
            em.finalize()
        except Exception:
            pass
        self._emitter = None  # mark flushed

    def __del__(self):
        # Backup path: __del__ fires when a cell-Composite is GC'd
        # (e.g. on actor restart). atexit covers normal shutdown.
        self._safe_finalize()


class TickHeartbeat(Step):
    """Lightweight per-tick visibility — sibling of CellParquetEmitter.

    Triggers every tick (only on ``global_time`` change). Writes a
    single line per tick to ``log_path`` containing wall timestamp,
    agent_id, sim_time, and the wall-time delta since the last tick.
    Tail the log to watch live progress AND per-tick wall cost
    without waiting for parquet's buffered batches.

    Each cell-Composite instance has its own heartbeat (fresh
    instance per daughter via realize); multiple cells append to the
    same shared ``log_path`` distinguishing themselves by
    ``agent_id``.
    """

    config_schema = {
        'log_path': 'string',
        'agent_id': 'string',
    }

    def __init__(self, config=None, core=None):
        super().__init__(config, core=core)
        import time as _t
        self._log_path = self.config['log_path']
        self._agent_id = str(self.config['agent_id'])
        self._last_wall = _t.perf_counter()
        # Append-mode so multiple daughters can share one log file.
        self._log_fh = open(self._log_path, 'a', buffering=1)

    def inputs(self):
        return {'global_time': 'float'}

    def triggers(self):
        # Re-fire only on global_time change → once per tick.
        return {'global_time': 'float'}

    def outputs(self):
        return {}

    def update(self, state):
        import time as _t
        sim_t = float(state.get('global_time', 0.0))
        now = _t.perf_counter()
        dt = now - self._last_wall
        self._last_wall = now
        self._log_fh.write(
            f'[{_t.time():.3f}] agent={self._agent_id} '
            f'sim_t={sim_t:7.1f} dt={dt:.3f}s\n')
        return {}

    def __del__(self):
        try:
            if hasattr(self, '_log_fh') and self._log_fh is not None:
                self._log_fh.close()
        except Exception:
            pass
