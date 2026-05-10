"""Pre-divide Python pickle iteration loop for division-step debug.

Phase 1 (slow, ~10 min, one-time):
    iter_division_pickle.py save --seed 12 --at 2969

    Runs composite_lineage (with composite engine and divide=True)
    from t=0 to t=2969 — one tick before division for seed 12. Pickles
    the EcoliSim object's Composite (or just its state if Composite
    pickle fails) to disk.

Phase 2 (fast, ~15s):
    iter_division_pickle.py run --extra 5

    Loads the pickle, runs forward ``--extra`` more sim seconds
    (covers divide + first 3 daughter ticks), emits parquet to
    ``--out_dir``, and diffs the daughter's first 3 ticks against
    a v1 reference parquet directory.

The pickle preserves exact Python state — RNG states, numpy arrays,
process instances — so this iteration is FASTER and HIGHER-FIDELITY
than the process-bigraph bundle save (which has known sim_data_objects
serialization gaps).

Usage:
    # Build (slow, one-time):
    uv run --no-sync python runscripts/iter_division_pickle.py save \\
        --seed 12 --at 2969

    # Iterate (fast, repeat after each fix):
    uv run --no-sync python runscripts/iter_division_pickle.py run \\
        --extra 5
"""
from __future__ import annotations

import argparse
import os
import pickle as _stdlib_pickle
import sys
import time
from contextlib import chdir

# cloudpickle handles lambdas, eval'd closures, and locally defined
# classes that stdlib pickle chokes on. Metabolism's
# ``self._compiled_enzymes = eval("lambda e: {...}")`` is exactly that
# case — only cloudpickle can dump the live Composite.
# Loading is symmetric — cloudpickle.load uses pickle internals.
import cloudpickle
HIGHEST_PROTOCOL = _stdlib_pickle.HIGHEST_PROTOCOL

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _patch_glpk_pickle():
    """Add __getstate__/__setstate__ to NetworkFlowGLPK so the LP
    problem AND its current basis/solution survive pickle.

    Three classes of state in the FBA solver:
      1. Plain Python (dicts, ints) — survives default pickle.
      2. SWIG-wrapped GLPK problem handle (self._lp) and control
         params (self._smcp) — handled here via GLPK's binary
         serialization (glp_write_prob/glp_read_prob and
         glp_write_sol/glp_read_sol — the latter preserves the
         warm-start basis).
      3. SWIG-wrapped coefficient arrays
         (self._coeff_arrays / self._flow_index_arrays) — dropped
         and rebuilt from ``self._materialCoeffs`` via
         ``_cache_glp_arrays()`` after the LP is restored.

    Verified bit-identical via runscripts/test_pickle_roundtrip.py.
    """
    import tempfile
    import swiglpk as glp
    from wholecell.utils._netflow import nf_glpk as _nf

    Cls = _nf.NetworkFlowGLPK
    if getattr(Cls, '_pickle_patched', False):
        return

    _SMCP_FIELDS = ('msg_lev', 'meth', 'pricing', 'r_test', 'tol_bnd',
                    'tol_dj', 'tol_piv', 'obj_ll', 'obj_ul', 'it_lim',
                    'tm_lim', 'out_frq', 'out_dly', 'presolve')
    _DROP = ('_lp', '_smcp', '_coeff_arrays', '_flow_index_arrays')

    def __getstate__(self):
        state = {k: v for k, v in self.__dict__.items() if k not in _DROP}
        smcp_vals = {}
        for f in _SMCP_FIELDS:
            try: smcp_vals[f] = getattr(self._smcp, f)
            except AttributeError: pass
        state['_smcp_vals'] = smcp_vals
        with tempfile.NamedTemporaryFile(suffix='.glp', delete=False) as f:
            prob_path = f.name
        try:
            rc = glp.glp_write_prob(self._lp, 0, prob_path)
            if rc != 0:
                raise RuntimeError(f'glp_write_prob failed rc={rc}')
            with open(prob_path, 'rb') as f:
                state['_lp_prob_bytes'] = f.read()
        finally:
            try: os.unlink(prob_path)
            except OSError: pass
        if self._solved:
            with tempfile.NamedTemporaryFile(suffix='.sol', delete=False) as f:
                sol_path = f.name
            try:
                rc = glp.glp_write_sol(self._lp, sol_path)
                if rc != 0:
                    raise RuntimeError(f'glp_write_sol failed rc={rc}')
                with open(sol_path, 'rb') as f:
                    state['_lp_sol_bytes'] = f.read()
            finally:
                try: os.unlink(sol_path)
                except OSError: pass
        return state

    def __setstate__(self, state):
        smcp_vals = state.pop('_smcp_vals', {})
        prob_bytes = state.pop('_lp_prob_bytes', None)
        sol_bytes = state.pop('_lp_sol_bytes', None)
        self.__dict__.update(state)
        self._coeff_arrays = {}
        self._flow_index_arrays = {}
        self._lp = glp.glp_create_prob()
        self._smcp = glp.glp_smcp()
        glp.glp_init_smcp(self._smcp)
        for f, v in smcp_vals.items():
            try: setattr(self._smcp, f, v)
            except AttributeError: pass
        if prob_bytes is not None:
            with tempfile.NamedTemporaryFile(suffix='.glp', delete=False) as f:
                f.write(prob_bytes); f.flush()
                prob_path = f.name
            try:
                rc = glp.glp_read_prob(self._lp, 0, prob_path)
                if rc != 0:
                    raise RuntimeError(f'glp_read_prob failed rc={rc}')
            finally:
                try: os.unlink(prob_path)
                except OSError: pass
        if sol_bytes is not None:
            with tempfile.NamedTemporaryFile(suffix='.sol', delete=False) as f:
                f.write(sol_bytes); f.flush()
                sol_path = f.name
            try:
                rc = glp.glp_read_sol(self._lp, sol_path)
                if rc != 0:
                    raise RuntimeError(f'glp_read_sol failed rc={rc}')
            finally:
                try: os.unlink(sol_path)
                except OSError: pass
        if getattr(self, '_eqConstBuilt', False) and self._materialCoeffs:
            self._cache_glp_arrays()

    Cls.__getstate__ = __getstate__
    Cls.__setstate__ = __setstate__
    Cls._pickle_patched = True


def _strip_metabolism_pickle():
    """Strip Metabolism's v1-vivarium custom pickle so default __dict__
    pickle is used — relies on _patch_glpk_pickle() handling the SWIG
    objects deeper in the FBA solver."""
    from ecoli.processes import metabolism as _met
    for attr in ('__getstate__', '__setstate__'):
        if hasattr(_met.Metabolism, attr):
            try: delattr(_met.Metabolism, attr)
            except AttributeError: pass


def _rehydrate_sim_data_registry(ecoli):
    """After cloudpickle.load, populate the module-global
    ``_sim_data_object_instances`` dict from the loaded composite's
    sim_data_objects subtree.

    The registry is normally populated by the SimDataObjectStore
    realize hook at gen-0 build time. Loading from pickle skips
    realize (the state is already realized), so the registry stays
    empty — and the next realize call (e.g. daughter sub-tree at
    divide) can't resolve method refs like
    ``{_type: 'method', instance_path: ['sim_data_objects', 'transcription'], ...}``,
    so the dict reaches the process __init__ unresolved → 'dict
    object is not callable'.
    """
    from ecoli.library import bigraph_types as _bt
    sd = None
    agents = ecoli.state.get('agents', {})
    for ast in agents.values():
        if isinstance(ast, dict) and isinstance(ast.get('sim_data_objects'), dict):
            sd = ast['sim_data_objects']
            break
    if sd is None:
        sd = ecoli.state.get('sim_data_objects')
    if not isinstance(sd, dict):
        return 0
    n = 0
    for k, inst in sd.items():
        if isinstance(k, str) and k.startswith('_'):
            continue
        _bt._sim_data_object_instances[k] = inst
        n += 1
    return n


def _patch_metabolism_pickle():
    """Apply both the GLPK pickle patch and strip Metabolism's
    custom pickle. Call before save AND load."""
    _patch_glpk_pickle()
    _strip_metabolism_pickle()
DEFAULT_PICKLE = 'out/predivide_pickle_seed12/state.pkl'
DEFAULT_V1_REF = (
    'out/iter_test_v1_seed12/gen2/EXPERIMENT_ID_PLACEHOLDER/history/'
    'experiment_id=EXPERIMENT_ID_PLACEHOLDER/variant=0/'
    'lineage_seed=12/generation=2/agent_id=00')


def cmd_save(args):
    """Run composite_lineage to t=at, emit parquet at every tick,
    pickle the live composite at the end.

    Save emits a NORMAL parquet stream during the mother's run-up to
    division. That's necessary for after-the-fact divergence checks
    (full-column diff vs v1 reference at any tick t<=at) without
    rerunning the whole sim. Earlier versions used `emitter='null'`
    to save a few seconds — the trade-off was that any divergence
    investigation needed an extra full rerun. Not worth it.
    """
    with chdir(ROOT):
        os.makedirs(os.path.dirname(args.pickle) or '.', exist_ok=True)
        from ecoli.experiments.ecoli_master_sim import EcoliSim
        _patch_metabolism_pickle()
        sim = EcoliSim.from_file()
        sim.config['engine'] = 'composite'
        if args.seed is not None:
            sim.config['lineage_seed'] = args.seed
            sim.config['seed'] = args.seed
        sim.max_duration = int(args.at)
        # Emit parquet at every tick — same shape as a normal sim
        # run, so post-hoc full-column parity diffs work without
        # any rerun. Output goes next to the pickle for easy
        # cross-reference.
        parquet_dir = (args.parquet_out or
                       os.path.join(os.path.dirname(args.pickle) or '.',
                                    'mother_history'))
        sim.config['emitter'] = 'parquet'
        sim.config['emitter_arg'] = {
            'out_dir': os.path.abspath(parquet_dir),
            'threaded': False,
        }
        sim.divide = True

        print(f'[save] building composite to t={args.at}, seed={args.seed}',
              flush=True)
        print(f'[save]   parquet → {parquet_dir}', flush=True)
        t0 = time.monotonic()
        sim.run()
        # composite engine stores the live Composite on self._composite,
        # NOT self.ecoli (which is the v1 vivarium composer's output).
        ecoli = sim._composite
        print(f'[save] reached t={ecoli.state.get("global_time")} '
              f'in {time.monotonic()-t0:.1f}s wall', flush=True)

        # Pickle the live Composite — exact Python state, no JSON
        # round-trip, no schema re-inference. If pickle raises, the
        # error is informative; don't swallow it with a fallback.
        t0 = time.monotonic()
        with open(args.pickle, 'wb') as f:
            cloudpickle.dump(ecoli, f, protocol=HIGHEST_PROTOCOL)
        size = os.path.getsize(args.pickle) / 1e6
        print(f'[save] ✓ pickled Composite → {args.pickle} '
              f'({size:.1f} MB) in {time.monotonic()-t0:.1f}s', flush=True)
        return 0


def cmd_run(args):
    """Load mother pickle, drive to divide, extract daughter,
    build gen-1 composite via EcoliCellProcess, run gen-1 forward.

    Mirrors ``EcoliSim._run_composite_lineage`` for the gen-0 → gen-1
    transition, but starts from a pickled gen-0 mother (saved at
    ``--at`` sim-seconds before division) rather than running gen 0
    from t=0. This is the fast iteration path: each /run cycle is
    ~30-60s instead of ~10 min of fresh gen 0.
    """
    with chdir(ROOT):
        if not os.path.isfile(args.pickle):
            print(f'no pickle at {args.pickle}; run `save` first',
                  file=sys.stderr)
            return 2

        os.makedirs(args.out_dir, exist_ok=True)
        _patch_metabolism_pickle()

        # ---- Load mother pickle ----
        print(f'[run] loading pickle {args.pickle}', flush=True)
        t0 = time.monotonic()
        with open(args.pickle, 'rb') as f:
            obj = cloudpickle.load(f)
        print(f'[run] loaded in {time.monotonic()-t0:.1f}s', flush=True)

        # Re-populate the module-global sim_data_objects registry
        # (otherwise daughter realize at divide can't resolve method
        # refs like {_type: 'method', instance_path: [...]})
        n = _rehydrate_sim_data_registry(obj)
        print(f'[run] rehydrated sim_data_objects registry ({n} entries)',
              flush=True)

        # ---- Build EcoliSim (only for metadata + emit helpers) ----
        from copy import deepcopy
        from ecoli.experiments.ecoli_master_sim import EcoliSim
        from ecoli.library.parquet_emitter import ParquetEmitter
        from ecoli.library.sim_data import LoadSimData
        from ecoli.library.bigraph_types import ECOLI_TYPES
        from ecoli.composites.ecoli_composite import run_to_division
        from ecoli.composites.ecoli_cell_process import EcoliCellProcess
        from process_bigraph.types.process import (
            register_types as register_process_bigraph_types)
        from bigraph_schema import Core, BASE_TYPES

        sim = EcoliSim.from_file()
        sim.config['engine'] = 'composite_lineage'
        sim.config['lineage_seed'] = args.seed
        sim.config['seed'] = args.seed
        sim.config['agent_id'] = '0'
        sim.config['emitter'] = 'parquet'
        sim.config['emitter_arg'] = {
            'out_dir': os.path.abspath(args.out_dir),
            'threaded': False,
        }
        # Defaults that ParquetEmitter needs via sim.experiment_id etc.
        if not getattr(sim, 'experiment_id', None):
            sim.experiment_id = 'EXPERIMENT_ID_PLACEHOLDER'

        # Populate processes/topology/process_configs on sim.config —
        # EcoliCellProcess (gen-1 build path) reads these when calling
        # build_ecoli_document. Same calls _run_composite_lineage makes.
        sim.processes = sim._retrieve_processes(
            sim.processes, sim.add_processes,
            sim.exclude_processes, sim.swap_processes)
        sim.topology = sim._retrieve_topology(
            sim.topology, sim.processes, sim.swap_processes,
            sim.log_updates)
        sim.process_configs = sim._retrieve_process_configs(
            sim.process_configs, sim.processes)

        # Build core (same registry as composite_lineage)
        core = Core(BASE_TYPES)
        register_process_bigraph_types(core)
        core.register_types(ECOLI_TYPES)

        # Load sim_data fresh — gen-1 EcoliCellProcess build needs
        # the live sim_data instance, not the pickled refs.
        print('[run] loading sim_data...', flush=True)
        t0 = time.monotonic()
        base_kwargs = dict(sim.config)
        base_kwargs['seed'] = args.seed
        base_load_sim_data = LoadSimData(**base_kwargs)
        shared_sim_data = base_load_sim_data.sim_data
        sim._shared_sim_data = shared_sim_data
        print(f'[run] sim_data loaded in {time.monotonic()-t0:.1f}s',
              flush=True)

        # Single shared emitter across both gens.
        emitter = ParquetEmitter(sim.config['emitter_arg'])

        # =========================================================
        # GEN 0: drive loaded mother forward to divide.
        #
        # Don't emit anything for gen 0 — it'd pollute the parquet
        # partition (the emitter buffers history rows under the
        # most-recent configuration emit's partition path, so any
        # gen-0 emit gets retagged to gen-1's path on flush). For
        # our iteration purpose we only care about gen-1 ticks vs
        # v1 reference.
        # =========================================================
        gen0_agent_id = '0'
        sim._composite = obj

        cur_t = float(obj.state.get('global_time', 0.0))
        gen0_max = max(60.0, float(args.divide_window))
        print(f'\n=== gen 0: driving mother from t={cur_t} for up to '
              f'{gen0_max}s (until divide) ===', flush=True)
        t0 = time.monotonic()
        divided, end_t = run_to_division(
            obj, max_duration=gen0_max,
            daughter_outdir=None, on_tick=None)
        print(f'[gen 0] {time.monotonic()-t0:.1f}s wall; divided={divided} '
              f'at t={end_t}', flush=True)
        if not divided:
            print('[run] mother did not divide — bailing', flush=True)
            emitter.success = False
            emitter.finalize()
            return 1

        # =========================================================
        # GEN 1: extract daughter, build fresh composite via
        # EcoliCellProcess (mirrors composite_lineage exactly)
        # =========================================================
        daughter_state = sim._extract_lineage_daughter(obj, daughter_idx=0)
        if daughter_state is None:
            print('[run] no daughter in state — bailing', flush=True)
            emitter.success = False
            emitter.finalize()
            return 1

        gen1_seed = args.seed + 1
        gen1_agent_id = gen0_agent_id + '0'  # '00' for single-daughter
        gen1_config = deepcopy(sim.config)
        gen1_config['seed'] = gen1_seed
        gen1_config['agent_id'] = gen1_agent_id
        gen1_config['initial_state'] = daughter_state
        gen1_config['initial_state_file'] = None

        print(f'\n=== gen 1: building from daughter (agent_id='
              f'{gen1_agent_id}, seed={gen1_seed}) ===', flush=True)
        t0 = time.monotonic()
        cell = EcoliCellProcess(
            config={
                'lineage_seed': args.seed,
                'agent_id': gen1_agent_id,
                'sim_data_path': gen1_config['sim_data_path'],
                'initial_state': daughter_state,
                'sim_data': shared_sim_data,
                'sim_config': gen1_config,
            },
            core=core,
        )
        gen1_ecoli = cell.inner_composite
        print(f'[gen 1] built in {time.monotonic()-t0:.1f}s', flush=True)

        # Sync top-level global_time from daughter's local global_time
        agent_t = gen1_ecoli.state.get('agents', {}).get(
            gen1_agent_id, {}).get('global_time')
        if agent_t is not None and float(agent_t) > 0:
            new_t = float(agent_t)
            gen1_ecoli.state['global_time'] = new_t
            for path in list(gen1_ecoli.front.keys()):
                gen1_ecoli.front[path]['time'] = new_t

        sim._composite = gen1_ecoli
        sim._emit_lineage_configuration(emitter, gen1_ecoli)
        sim._emit_composite_history(emitter, gen1_ecoli)

        n_ticks = int(args.n_ticks)
        print(f'[gen 1] running forward {n_ticks} ticks...', flush=True)
        t0 = time.monotonic()
        on_tick = (lambda eco: sim._emit_composite_history(emitter, eco))
        run_to_division(
            gen1_ecoli, max_duration=float(n_ticks),
            daughter_outdir=None, on_tick=on_tick)
        print(f'[gen 1] {time.monotonic()-t0:.1f}s wall', flush=True)

        emitter.success = True
        emitter.finalize()

        # ---- Diff against v1 reference ----
        if args.reference and os.path.isdir(args.reference):
            _diff_against_v1(args.out_dir, args.reference, args.n_ticks)
        return 0


def _diff_against_v1(v2_dir, v1_dir, n_ticks):
    """Compare v2 daughter parquet first n ticks to v1's."""
    import polars as pl
    import pyarrow.dataset as pa_ds
    import numpy as np

    # Find the daughter parquet under v2_dir's history subtree.
    # gen 1 in our composite_lineage path = generation=1 in parquet
    # (the loaded mother is gen 0; the daughter is gen 1, NOT gen 2).
    # v1 reference path uses generation=2 because v1 lineage_seed=12
    # gen-2 file matches the FIRST DIVIDE event for that seed.
    v2_daughter = None
    for root, _, files in os.walk(v2_dir):
        if (any(f.endswith('.pq') for f in files)
                and '/history/' in root + '/'
                and ('generation=1' in root or 'generation=2' in root)
                and ('agent_id=00' in root or 'agent_id=0' in root.split('/')[-1])):
            v2_daughter = root
            break
    if v2_daughter is None:
        print('[diff] no v2 daughter parquet found', flush=True)
        return

    print(f'\n=== diff v1 vs v2 daughter (first {n_ticks} ticks) ===')
    print(f'  v1: {v1_dir}')
    print(f'  v2: {v2_daughter}')

    v1 = pl.from_arrow(pa_ds.dataset(v1_dir, format='parquet')
                       .to_table(columns=['time', 'bulk'])).sort('time')
    v2 = pl.from_arrow(pa_ds.dataset(v2_daughter, format='parquet')
                       .to_table(columns=['time', 'bulk'])).sort('time')
    v1 = v1.with_columns((pl.col('time') - v1['time'].min()).alias('rt'))
    v2 = v2.with_columns((pl.col('time') - v2['time'].min()).alias('rt'))
    m = v1.join(v2, on='rt', suffix='_v2', how='inner').sort('rt')
    common = min(n_ticks, len(m))
    if common == 0:
        print('[diff] no common timesteps', flush=True)
        return
    b1 = np.array(m['bulk'][:common].to_list())
    b2 = np.array(m['bulk_v2'][:common].to_list())
    diffs = np.abs(b1.astype(np.int64) - b2.astype(np.int64))
    n_diff = (diffs > 0).sum(axis=1)
    for i in range(common):
        rt = int(m['rt'][i])
        marker = '✓' if n_diff[i] == 0 else '✗'
        print(f'  {marker} rt={rt:>3}  n_diff={n_diff[i]:>5}/16321  '
              f'max|delta|={int(diffs[i].max())}')


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest='cmd', required=True)

    ps = sub.add_parser('save')
    ps.add_argument('--seed', type=int, default=12)
    ps.add_argument('--at', type=int, default=2969,
                    help='sim-time to checkpoint at — default 2969 = '
                    'one tick before seed-12 divide at 2970')
    ps.add_argument('--pickle', default=DEFAULT_PICKLE)
    ps.add_argument('--parquet_out', default=None,
                    help='Where to emit per-tick mother parquet '
                    '(default: <pickle_dir>/mother_history). Used '
                    'for post-hoc full-column parity diff vs v1 ref.')

    pr = sub.add_parser('run')
    pr.add_argument('--pickle', default=DEFAULT_PICKLE)
    pr.add_argument('--seed', type=int, default=12,
                    help='lineage_seed (must match the saved pickle)')
    pr.add_argument('--divide_window', type=float, default=60.0,
                    help='Max sim-seconds past pickle time to wait for '
                    'mother divide (seed-dependent; seed 12 ≈ 1s past '
                    't=2969 → divide at 2970)')
    pr.add_argument('--out_dir', default='out/iter_division_seed12')
    pr.add_argument('--reference', default=DEFAULT_V1_REF)
    pr.add_argument('--n_ticks', type=int, default=3,
                    help='How many gen-1 ticks to run + diff vs v1')

    args = p.parse_args()
    if args.cmd == 'save':
        return cmd_save(args)
    return cmd_run(args)


if __name__ == '__main__':
    sys.exit(main())
