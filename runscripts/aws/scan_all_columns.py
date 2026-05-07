"""Column-by-column divergence scan for v1 vs v2 history at one cell.

For a single (seed, gen) cell, downloads both runs' parquet to local and
compares EVERY column at every common timestep. Buckets results into:

  identical          numerically equal at all timesteps (incl NaN==NaN)
  diverged           values differ at some timestep
  nan_only_in_v2     v2 has NaN/inf where v1 does not
  shape_mismatch     list lengths differ
  v1_only / v2_only  column missing in one schema
  unsupported        polars/pyarrow can't compare directly (rare nested types)

Prints a category-grouped breakdown so you can see which listener
families are broken (e.g., listeners.mass.*, listeners.fba_results.*).

Designed for the head node where in-region S3 sync is fast.
"""
import argparse
import math
import os
import shutil
import subprocess
import sys
import tempfile
from collections import defaultdict

import numpy as np
import polars as pl
import pyarrow.dataset as pa_ds


def sync(s3_uri, dest):
    os.makedirs(dest, exist_ok=True)
    subprocess.run(
        ['aws', 's3', 'sync', s3_uri, dest,
         '--no-progress', '--only-show-errors',
         '--exclude', '*', '--include', '*.pq'],
        check=True)


def col_status_t0_only(a, b):
    """Return True if a and b differ ONLY at index 0 (the t=0 emit) and
    match identically from index 1 onwards. Helps distinguish listener-
    priming emit artifacts from real per-tick divergences.
    """
    if len(a) != len(b) or len(a) < 2:
        return False
    if a.dtype == object or b.dtype == object:
        try:
            for i in range(1, len(a)):
                if a[i] != b[i]:
                    return False
            return a[0] != b[0]
        except Exception:
            return False
    if a.dtype.kind == 'f':
        rest_eq = np.array_equal(a[1:], b[1:], equal_nan=True)
    else:
        rest_eq = np.array_equal(a[1:], b[1:])
    return rest_eq and bool((a[0] != b[0]) if a.dtype != object else a[0] != b[0])


def col_status(a, b):
    """Compare two same-length numpy arrays; return one of:
    'identical', 't0_only', 'diverged', 'nan_only_in_v2',
    'shape_mismatch', 'unsupported'.
    """
    if a.shape != b.shape:
        return 'shape_mismatch'
    # object dtype = list/struct: row-by-row scalar/list compare
    if a.dtype == object or b.dtype == object:
        try:
            diffs = []
            for i, (x, y) in enumerate(zip(a, b)):
                eq = True
                if isinstance(x, np.ndarray) and isinstance(y, np.ndarray):
                    eq = x.shape == y.shape and np.array_equal(x, y)
                elif isinstance(x, list) and isinstance(y, list):
                    eq = len(x) == len(y) and not any(xi != yi for xi, yi in zip(x, y))
                else:
                    eq = (x == y) or (
                        isinstance(x, float) and isinstance(y, float)
                        and math.isnan(x) and math.isnan(y))
                if not eq:
                    diffs.append(i)
            if not diffs:
                return 'identical'
            if diffs == [0] and len(a) > 1:
                return 't0_only'
            return 'diverged'
        except Exception:
            return 'unsupported'
    # numeric float
    if a.dtype.kind == 'f':
        a_nan = np.isnan(a)
        b_nan = np.isnan(b)
        if (b_nan & ~a_nan).any():
            return 'nan_only_in_v2'
        if np.array_equal(a, b, equal_nan=True):
            return 'identical'
        # Check t=0-only
        if len(a) > 1 and np.array_equal(a[1:], b[1:], equal_nan=True):
            return 't0_only'
        return 'diverged'
    # int / bool / other primitive
    if np.array_equal(a, b):
        return 'identical'
    if len(a) > 1 and np.array_equal(a[1:], b[1:]):
        return 't0_only'
    return 'diverged'


def family(col):
    """First two dotted segments — listeners.mass, listeners.fba_results, etc."""
    parts = col.split('.')
    if len(parts) <= 1:
        return col
    return '.'.join(parts[:2])


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--seed', type=int, default=0)
    p.add_argument('--gen', type=int, default=1)
    p.add_argument('--v1-id', default='sim35-comparison_test_6-4b7b')
    p.add_argument('--v2-id', default='comparison_10s_16g_v2_aws')
    p.add_argument('--bucket',
                   default='smsvpctest-shared-sharedbucket60d199d6-abfvwv0day91')
    p.add_argument('--prefix', default='vecoli-output')
    p.add_argument('--scratch', default=os.path.expanduser('~/scan_cols_scratch'),
                   help='scratch dir for parquet sync (NOT tmpfs)')
    p.add_argument('--v1-path', default=None,
                   help='local v1 history dir (skip S3 sync)')
    p.add_argument('--v2-path', default=None,
                   help='local v2 history dir (skip S3 sync)')
    args = p.parse_args()

    if args.v1_path and args.v2_path:
        # Local mode — no sync, no scratch dir
        v1_dir = os.path.abspath(args.v1_path)
        v2_dir = os.path.abspath(args.v2_path)
        tmp = None
    else:
        agent_id = '0' * args.gen
        os.makedirs(args.scratch, exist_ok=True)
        tmp = tempfile.mkdtemp(prefix=f's{args.seed}g{args.gen}_', dir=args.scratch)
        for tag, exp in [('v1', args.v1_id), ('v2', args.v2_id)]:
            uri = (f's3://{args.bucket}/{args.prefix}/{exp}/{exp}/history/'
                   f'experiment_id={exp}/variant=0/lineage_seed={args.seed}/'
                   f'generation={args.gen}/agent_id={agent_id}/')
            print(f'syncing {tag} ...', flush=True)
            sync(uri, os.path.join(tmp, tag))
        v1_dir = os.path.join(tmp, 'v1')
        v2_dir = os.path.join(tmp, 'v2')

    try:
        v1_ds = pa_ds.dataset(v1_dir, format='parquet')
        v2_ds = pa_ds.dataset(v2_dir, format='parquet')
        v1_cols = set(v1_ds.schema.names)
        v2_cols = set(v2_ds.schema.names)
        print(f'\nschema  v1={len(v1_cols)}  v2={len(v2_cols)}')
        print(f'  v1-only: {len(v1_cols - v2_cols)}')
        print(f'  v2-only: {len(v2_cols - v1_cols)}')
        common = sorted(v1_cols & v2_cols)
        print(f'  common:  {len(common)}\n')

        # Per-column reads keep memory bounded on small heads (8 GB t4g.large
        # OOMs trying to load both full tables — bulk + listener arrays = ~6 GB).
        # Build a time map once so we can align without a full join.
        v1_t = pl.from_arrow(v1_ds.to_table(columns=['time']))['time'].to_numpy()
        v2_t = pl.from_arrow(v2_ds.to_table(columns=['time']))['time'].to_numpy()
        v2_idx = {int(t): i for i, t in enumerate(v2_t)}
        v1_keep, v2_keep = [], []
        for i, t in enumerate(v1_t):
            j = v2_idx.get(int(t))
            if j is not None:
                v1_keep.append(i); v2_keep.append(j)
        v1_keep = np.array(v1_keep, dtype=np.int64)
        v2_keep = np.array(v2_keep, dtype=np.int64)
        times = v1_t[v1_keep].astype(np.int64)
        print(f'common timesteps: {len(v1_keep)}\n', flush=True)

        # Skip columns we already know match (bulk) or aren't worth comparing.
        skip_cols = {'time', 'bulk'}

        results = {}
        first_diff_t = {}
        scanned = 0
        for col in common:
            if col in skip_cols:
                continue
            scanned += 1
            try:
                a_full = pl.from_arrow(v1_ds.to_table(columns=[col]))[col].to_numpy()
                b_full = pl.from_arrow(v2_ds.to_table(columns=[col]))[col].to_numpy()
                a = a_full[v1_keep]
                b = b_full[v2_keep]
                status = col_status(a, b)
                results[col] = status
                if status in ('diverged', 'nan_only_in_v2') and a.dtype.kind in 'fiu':
                    if a.dtype.kind == 'f':
                        bad = (a != b) & ~(np.isnan(a) & np.isnan(b))
                    else:
                        bad = a != b
                    if bad.any():
                        first_diff_t[col] = int(times[np.argmax(bad)])
                del a_full, b_full, a, b
            except Exception as e:
                results[col] = f'unsupported:{type(e).__name__}'
            if scanned % 25 == 0:
                print(f'  {scanned}/{len(common) - len(skip_cols)}', flush=True)

        # Aggregate
        by_status = defaultdict(list)
        for c, s in results.items():
            by_status[s].append(c)

        print('=== columns by status ===')
        for s in ['identical', 'diverged', 'nan_only_in_v2',
                  'shape_mismatch']:
            n = len(by_status.get(s, []))
            print(f'  {s:>18}  {n}')
        other = [s for s in by_status if s not in
                 ('identical', 'diverged', 'nan_only_in_v2', 'shape_mismatch')]
        for s in other:
            print(f'  {s:>18}  {len(by_status[s])}')

        # Family rollup of non-identical columns
        bad = (by_status.get('diverged', [])
               + by_status.get('nan_only_in_v2', [])
               + by_status.get('shape_mismatch', []))
        if bad:
            fam_count = defaultdict(int)
            for c in bad:
                fam_count[family(c)] += 1
            print('\n=== non-identical columns by family ===')
            for fam, n in sorted(fam_count.items(), key=lambda x: -x[1]):
                print(f'  {n:>4}  {fam}')

            print('\n=== first 30 diverged columns ===')
            for c in by_status.get('diverged', [])[:30]:
                print(f'  {c}')
            print('\n=== first 20 nan-only-in-v2 columns ===')
            for c in by_status.get('nan_only_in_v2', [])[:20]:
                print(f'  {c}')

        if v1_cols - v2_cols:
            print(f'\n=== v1-only columns ({len(v1_cols - v2_cols)}) ===')
            for c in sorted(v1_cols - v2_cols)[:30]:
                print(f'  {c}')
        if v2_cols - v1_cols:
            print(f'\n=== v2-only columns ({len(v2_cols - v1_cols)}) ===')
            for c in sorted(v2_cols - v1_cols)[:30]:
                print(f'  {c}')

        if first_diff_t:
            from collections import Counter
            t_counter = Counter(first_diff_t.values())
            print('\n=== first divergence timestep histogram (top 10) ===')
            for t, n in t_counter.most_common(10):
                print(f'  t={t:>5}  ({n} columns first diverge here)')

        # Persist a per-column TSV next to the script so we can iterate.
        out_tsv = os.path.expanduser(
            f'~/scan_columns_seed{args.seed}_gen{args.gen}.tsv')
        with open(out_tsv, 'w') as f:
            f.write('column\tstatus\tfirst_diff_t\n')
            for c in sorted(results):
                t = first_diff_t.get(c, '')
                f.write(f'{c}\t{results[c]}\t{t}\n')
        print(f'\nPer-column results -> {out_tsv}')

    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    main()
