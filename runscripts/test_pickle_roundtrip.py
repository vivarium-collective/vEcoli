"""Fast (≈30s) pickle round-trip parity test.

Reference run:  build composite, run to t=2, record bulk at t=2.
Test run:       build composite, run to t=1, cloudpickle dump+load,
                run to t=2 from the loaded composite, record bulk at t=2.

If reference == test bulk-vector → cloudpickle save/load preserves
the live state bit-faithfully and the iteration loop is sound.
If they differ → some piece of state is lost (RNG, numba state, etc.)
and we know the pickle scaffold itself is wrong, BEFORE spending
8 minutes building a t=2969 pickle.

Usage:
    uv run --no-sync python runscripts/test_pickle_roundtrip.py [--seed 12]
"""
from __future__ import annotations

import argparse
import os
import pickle as _stdlib_pickle
import sys
import tempfile
import time
from contextlib import chdir

import cloudpickle
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _bulk(ecoli):
    """Return mother bulk counts as int64 ndarray (sorted by id)."""
    state = ecoli.state
    bulk = state.get('bulk')
    if bulk is None:
        agents = state.get('agents', {})
        if agents:
            cell = next(iter(agents.values()))
            bulk = cell.get('bulk')
    if isinstance(bulk, np.ndarray) and bulk.dtype.names and 'id' in bulk.dtype.names:
        order = np.argsort(bulk['id'])
        return bulk['count'][order].astype(np.int64).copy()
    raise RuntimeError(f'bulk in unexpected form: type={type(bulk)}')


def _build_and_run(seed, run_to):
    """Fresh composite, run to ``run_to`` sim-time, return composite."""
    from ecoli.experiments.ecoli_master_sim import EcoliSim
    sim = EcoliSim.from_file()
    sim.config['engine'] = 'composite'
    sim.config['lineage_seed'] = seed
    sim.config['seed'] = seed
    sim.config['emitter'] = 'null'
    sim.divide = False  # no division at t=2
    sim.max_duration = run_to
    sim.run()
    return sim._composite


def _patch_glpk_pickle():
    """Add __getstate__/__setstate__ to NetworkFlowGLPK so the LP
    problem AND its current basis/solution survive pickle.

    Uses GLPK's native binary serialization:
      - glp_write_prob/glp_read_prob  → LP problem (rows, cols,
        coefficients, bounds, objective)
      - glp_write_sol/glp_read_sol    → basic solution (variable
        values, basis statuses) — preserves warm-start

    The plain-Python state (dicts, ints) survives default pickle.
    Only the SWIG-wrapped _lp and _smcp need round-trip via files.
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

    # Drop these — all SWIG-wrapped or holding SwigPyObjects that
    # cloudpickle can't serialize. Rebuilt in __setstate__.
    _DROP = ('_lp', '_smcp', '_coeff_arrays', '_flow_index_arrays')

    def __getstate__(self):
        state = {k: v for k, v in self.__dict__.items() if k not in _DROP}
        # smcp control params: scalar fields only
        smcp_vals = {}
        for f in _SMCP_FIELDS:
            try:
                smcp_vals[f] = getattr(self._smcp, f)
            except AttributeError:
                pass
        state['_smcp_vals'] = smcp_vals
        # LP problem (rows/cols/bounds/coeffs/objective): native GLPK
        # binary format via temp file
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
        # Solution + basis status — preserves warm-start across pickle
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
        # Initialize empty caches; _cache_glp_arrays() rebuilds below
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
        # Rebuild SWIG-wrapped coefficient/index arrays from the
        # Python-side _materialCoeffs that survived pickle.
        if getattr(self, '_eqConstBuilt', False) and self._materialCoeffs:
            self._cache_glp_arrays()

    Cls.__getstate__ = __getstate__
    Cls.__setstate__ = __setstate__
    Cls._pickle_patched = True


def _patch_metabolism_strip():
    """Strip the v1-vivarium-MP custom pickle on Metabolism so default
    __dict__ pickle is used. Relies on NetworkFlowGLPK having its own
    pickle support — see _patch_glpk_pickle."""
    from ecoli.processes import metabolism as _met
    for attr in ('__getstate__', '__setstate__'):
        if hasattr(_met.Metabolism, attr):
            try:
                delattr(_met.Metabolism, attr)
            except AttributeError:
                pass


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--seed', type=int, default=12)
    p.add_argument('--patch-metabolism', action='store_true',
                   help='Patch the FBA solver to preserve full state '
                   '(including basis) across pickle')
    p.add_argument('--n-steps', type=int, default=1,
                   help='How many ticks to step both copies; we check '
                   'parity at each tick')
    args = p.parse_args()

    with chdir(ROOT):
        if args.patch_metabolism:
            _patch_glpk_pickle()
            _patch_metabolism_strip()
            print('[patch] GLPK pickle support installed; Metabolism '
                  'custom pickle stripped', flush=True)
        # ---- Build + run to t=1, snapshot bulk ----
        print('[setup] building + running to t=1', flush=True)
        t0 = time.monotonic()
        ecoli_a = _build_and_run(args.seed, 1)
        bulk_at_t1 = _bulk(ecoli_a)
        print(f'[setup] phase 1 done in {time.monotonic()-t0:.1f}s; '
              f'bulk sum at t=1 = {bulk_at_t1.sum()}', flush=True)

        # ---- Pickle dump + load ----
        with tempfile.NamedTemporaryFile(suffix='.pkl', delete=False) as f:
            pkl_path = f.name
        try:
            print('[pkl] cloudpickle dump', flush=True)
            t0 = time.monotonic()
            with open(pkl_path, 'wb') as f:
                cloudpickle.dump(ecoli_a, f,
                                 protocol=_stdlib_pickle.HIGHEST_PROTOCOL)
            print(f'[pkl] dumped {os.path.getsize(pkl_path)/1e6:.1f} MB in '
                  f'{time.monotonic()-t0:.1f}s', flush=True)

            print('[pkl] cloudpickle load', flush=True)
            t0 = time.monotonic()
            with open(pkl_path, 'rb') as f:
                ecoli_b = cloudpickle.load(f)
            print(f'[pkl] loaded in {time.monotonic()-t0:.1f}s', flush=True)
        finally:
            try: os.unlink(pkl_path)
            except OSError: pass

        # ---- State equality at t=1 (before stepping further) ----
        bulk_loaded_at_t1 = _bulk(ecoli_b)
        diff0 = np.abs(bulk_at_t1 - bulk_loaded_at_t1)
        if int((diff0 > 0).sum()) > 0:
            n_d = int((diff0 > 0).sum())
            print(f'  ✗ STATE MISMATCH at t=1 (before stepping): '
                  f'{n_d} differ, max={int(diff0.max())}', flush=True)
            return 1
        print('  ✓ bulk state at t=1 is identical between original '
              'and loaded copies', flush=True)

        # ---- Step BOTH forward N ticks, parity-check each tick ----
        print(f'\n[step] running both copies forward {args.n_steps} ticks',
              flush=True)
        all_ok = True
        for k in range(args.n_steps):
            ecoli_a.run(1.0)
            ecoli_b.run(1.0)
            bulk_a = _bulk(ecoli_a)
            bulk_b = _bulk(ecoli_b)
            diff = np.abs(bulk_a - bulk_b)
            n_diff = int((diff > 0).sum())
            mark = '✓' if n_diff == 0 else '✗'
            print(f'  {mark} t={k+2}  n_diff={n_diff}/{len(bulk_a)}'
                  + ('' if n_diff == 0 else
                     f'  max={int(diff.max())}  sum={int(diff.sum())}'))
            if n_diff > 0:
                all_ok = False
                idx = np.argsort(-diff)[:5]
                for i in idx:
                    if diff[i] == 0: break
                    print(f'      idx={i}: orig={bulk_a[i]} '
                          f'loaded={bulk_b[i]} delta={int(bulk_a[i])-int(bulk_b[i])}')
        if all_ok:
            print(f'\n  ✓ BIT-IDENTICAL across all {args.n_steps} ticks — '
                  'pickle round-trip is bit-faithful')
            return 0
        return 1


if __name__ == '__main__':
    sys.exit(main())
