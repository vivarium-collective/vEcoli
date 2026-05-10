"""Find the first listener column that diverges between v1 and v2 for
a single (seed, gen) cell.

The parity matrix only checks the ``bulk`` column — fine for catching
gross divergence, useless for finding *where* timing differs when bulk
is identical. This tool reads ALL columns, joins on time, and reports
the first time any column differs, plus the worst diff per column.

Usage:
    uv run --no-sync python runscripts/aws/diff_columns.py \\
        --v1-id comparison_10s_16g_v1_aws \\
        --v2-id comparison_10s_16g_v2_mp_aws \\
        --seed 0 --gen 1 \\
        --bucket smsvpctest-shared-sharedbucket60d199d6-abfvwv0day91 \\
        --prefix vecoli-output

Reads parquet directly via fsspec (no s3 sync needed). Local-internet
slow (~2 min/cell) but fine for one-off divergence hunting.
"""
from __future__ import annotations
import argparse
import os
import sys

import numpy as np
import polars as pl
import pyarrow.dataset as pa_ds


def _hist_uri(bucket, prefix, exp, seed, gen):
    agent_id = '0' * gen
    return (f's3://{bucket}/{prefix}/{exp}/{exp}/history/'
            f'experiment_id={exp}/variant=0/lineage_seed={seed}/'
            f'generation={gen}/agent_id={agent_id}/')


def _load(uri):
    """Read every column except the giant bulk array (separate path).
    Returns polars DataFrame sorted by time."""
    ds = pa_ds.dataset(uri, format='parquet')
    cols = [f.name for f in ds.schema if f.name != 'bulk']
    tbl = ds.to_table(columns=cols)
    return pl.from_arrow(tbl).sort('time')


def _series_diverges(s1, s2):
    """Return True iff at least one element differs (NaN-aware).
    Handles list-of-numbers columns by element-wise compare."""
    if s1.dtype != s2.dtype:
        return True
    try:
        a = s1.to_numpy()
        b = s2.to_numpy()
    except Exception:
        # list / nested types: compare via to_list()
        a = s1.to_list()
        b = s2.to_list()
        if len(a) != len(b):
            return True
        for x, y in zip(a, b):
            if (x is None) != (y is None):
                return True
            if x is None:
                continue
            try:
                if not np.array_equal(np.asarray(x), np.asarray(y),
                                      equal_nan=True):
                    return True
            except Exception:
                if x != y:
                    return True
        return False
    if a.shape != b.shape:
        return True
    if np.issubdtype(a.dtype, np.floating):
        return not np.array_equal(a, b, equal_nan=True)
    return not np.array_equal(a, b)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--v1-id', required=True)
    p.add_argument('--v2-id', required=True)
    p.add_argument('--seed', type=int, required=True)
    p.add_argument('--gen', type=int, required=True)
    p.add_argument('--bucket', required=True)
    p.add_argument('--prefix', default='vecoli-output')
    p.add_argument('--first-n', type=int, default=10,
                   help='Show first N divergent columns (sorted by '
                   'first-divergence time, earliest first)')
    p.add_argument('--include', default='',
                   help='Comma-separated substrings: only check '
                   'columns whose name contains one of these. '
                   '(default: all listener columns)')
    args = p.parse_args()

    os.environ.setdefault('AWS_PROFILE', 'stanford-sso')
    os.environ.setdefault('AWS_REGION', 'us-gov-west-1')

    v1_uri = _hist_uri(args.bucket, args.prefix, args.v1_id,
                       args.seed, args.gen)
    v2_uri = _hist_uri(args.bucket, args.prefix, args.v2_id,
                       args.seed, args.gen)
    print(f'v1: {v1_uri}', flush=True)
    print(f'v2: {v2_uri}', flush=True)

    print('loading v1...', flush=True)
    v1 = _load(v1_uri)
    print(f'  v1: {len(v1)} rows, {len(v1.columns)} cols', flush=True)
    print('loading v2...', flush=True)
    v2 = _load(v2_uri)
    print(f'  v2: {len(v2)} rows, {len(v2.columns)} cols', flush=True)

    common = sorted(set(v1.columns) & set(v2.columns) - {'time'})
    if args.include:
        keys = [k.strip() for k in args.include.split(',') if k.strip()]
        common = [c for c in common if any(k in c for k in keys)]
    print(f'\ncomparing {len(common)} columns...', flush=True)

    # Inner-join on time to get matched rows
    j = v1.join(v2, on='time', suffix='_v2', how='inner').sort('time')
    print(f'common timesteps: {len(j)}', flush=True)
    if len(j) == 0:
        print('no overlap; bailing')
        return 1

    times = j['time'].to_numpy()
    # For each column: find first differing row index
    col_first_diff = []
    for col in common:
        s1 = j[col]
        s2 = j[f'{col}_v2']
        try:
            if not _series_diverges(s1, s2):
                continue
            # Find first differing index
            try:
                a = s1.to_numpy()
                b = s2.to_numpy()
                if np.issubdtype(a.dtype, np.floating):
                    mask = ~np.isclose(a, b, equal_nan=True, atol=0)
                else:
                    mask = a != b
                idx = int(np.argmax(mask)) if mask.any() else 0
            except Exception:
                a_l = s1.to_list()
                b_l = s2.to_list()
                idx = 0
                for i, (x, y) in enumerate(zip(a_l, b_l)):
                    try:
                        if not np.array_equal(np.asarray(x),
                                              np.asarray(y),
                                              equal_nan=True):
                            idx = i; break
                    except Exception:
                        if x != y: idx = i; break
            col_first_diff.append((int(times[idx]), col))
        except Exception as e:
            print(f'  ! {col}: {e}', flush=True)

    col_first_diff.sort()
    if not col_first_diff:
        print('\n✓ ALL COLUMNS BIT-IDENTICAL across all common timesteps')
        return 0

    print(f'\n=== {len(col_first_diff)} divergent columns '
          f'(showing first {args.first_n} by earliest divergence) ===')
    for t, col in col_first_diff[:args.first_n]:
        # Show actual values at first divergence
        row_idx = int(np.argmax(times == t))
        v1v = j[col][row_idx]
        v2v = j[f'{col}_v2'][row_idx]
        # Truncate long arrays/strings
        def _fmt(x):
            s = repr(x)
            return s[:80] + '...' if len(s) > 80 else s
        print(f'  t={t:>5}  {col}')
        print(f'    v1: {_fmt(v1v)}')
        print(f'    v2: {_fmt(v2v)}')


if __name__ == '__main__':
    sys.exit(main() or 0)
