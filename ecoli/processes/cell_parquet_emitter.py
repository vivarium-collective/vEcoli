"""Per-cell Parquet emitter Step.

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
    }

    def __init__(self, config=None, core=None):
        super().__init__(config, core=core)
        import sys as _sys
        import time as _ti
        _sys.stderr.write(
            f'[cpe-init] t={_ti.time():.3f} agent={config.get("agent_id") if config else "?"}\n')
        _sys.stderr.flush()
        from ecoli.library.parquet_emitter import ParquetEmitter
        self._emitter = ParquetEmitter({
            'out_dir': self.config['out_dir'],
            'batch_size': self.config.get('batch_size', 400),
            'threaded': True,
        })
        # Configuration emit installs the partition path. Minimum
        # metadata keys to satisfy v1's column expectations; analyses
        # may want more later.
        self._emitter.emit({
            'table': 'configuration',
            'data': {
                'metadata': {
                    'experiment_id': self.config['experiment_id'],
                    'variant': self.config.get('variant', 0),
                    'lineage_seed': self.config.get('lineage_seed', 0),
                    'agent_id': str(self.config['agent_id']),
                    'initial_global_time': 0.0,
                }
            }
        })
        self._last_emit_time = None

    def inputs(self):
        # Tree schemas keep this loosely-typed so any cell tree shape
        # passes through. The actual data shape comes from the wires.
        return {
            'global_time': 'float',
            'listeners': 'tree[node]',
            'bulk': 'tree[node]',
            'process_state': 'tree[node]',
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
        # Skip duplicate emits (defensive — triggers() should prevent
        # this but the step may be invoked on init too).
        if self._last_emit_time is not None and global_t <= self._last_emit_time:
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

    def __del__(self):
        # Flush remaining buffered emits on cell shutdown / divide.
        # Without this, the last <batch_size> emits never reach disk.
        try:
            if hasattr(self, '_emitter') and self._emitter is not None:
                self._emitter.success = True
                self._emitter.finalize()
        except Exception:
            pass


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
