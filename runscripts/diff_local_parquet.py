"""Local-only parquet column diff for fast divergence hunting.

Takes two local history-parquet directories (v1 and v2) and reports
which columns first diverge at which timestep — *every* column, not
just bulk. No AWS / no S3 — strictly works against parquet already
on disk (e.g. v1 reference at out/iter_test_v1_seed12/, v2 from
iter_division_pickle at out/iter_division_seed12/).

This is the inner loop of the divergence-hunting cycle: edit code →
run iter_division_pickle → run this → see what column drifted.

Usage:
    uv run --no-sync python runscripts/diff_local_parquet.py \\
        out/iter_test_v1_seed12/gen2/.../agent_id=00 \\
        out/iter_division_seed12/.../agent_id=00 \\
        --first-n 20
"""
from __future__ import annotations

import argparse
import fnmatch
import os
import sys

import numpy as np
import polars as pl
import pyarrow.dataset as pa_ds


def _load(path: str) -> tuple[pl.DataFrame, list[str]]:
    """Read a parquet dir column-by-column.

    Doing one ``to_table([col])`` per column dodges the
    ``Expected all lists to be of size=N but index K had size=0``
    error that bites when one column is row-jagged across fragments
    (the "unified" path tries to merge schemas and chokes). We just
    read what we can and skip what fails.

    Returns (DataFrame, [columns_we_couldn't_read])."""
    ds = pa_ds.dataset(path, format='parquet')
    all_cols = [f.name for f in ds.schema]
    if 'time' not in all_cols:
        raise RuntimeError(f'no time column in {path}')
    # Always start with the time column; any column that fails to
    # load is dropped (skipped from the comparison) — better to
    # report most columns than zero.
    df = pl.from_arrow(ds.to_table(columns=['time'])).sort('time')
    skipped: list[str] = []
    for col in all_cols:
        if col == 'time':
            continue
        try:
            tbl = ds.to_table(columns=['time', col])
            sub = pl.from_arrow(tbl).sort('time')
            df = df.join(sub, on='time', how='left')
        except Exception as e:
            skipped.append(f'{col}: {type(e).__name__}')
    return df, skipped


def _columns_differ(s1: pl.Series, s2: pl.Series, atol: float):
    """Return ``(first_diff_idx, max_abs_or_None, n_diff_rows)`` or
    ``None`` if all elements agree (NaN-aware, atol-tolerant)."""
    if s1.dtype != s2.dtype:
        return 0, None, len(s1)
    n = len(s1)
    if n == 0:
        return None
    # Numeric vectorized path
    try:
        a = s1.to_numpy()
        b = s2.to_numpy()
    except Exception:
        a = b = None
    if a is not None and b is not None and a.shape == b.shape:
        if np.issubdtype(a.dtype, np.floating):
            mask = (~np.isclose(a, b, equal_nan=True, atol=atol)
                    if atol else
                    ~np.isclose(a, b, equal_nan=True, atol=0, rtol=0))
            if not mask.any():
                return None
            diffs = np.abs(a - b)
            diffs = np.where(np.isfinite(diffs), diffs, 0)
            return (int(np.argmax(mask)),
                    float(np.nanmax(diffs)),
                    int(mask.sum()))
        if np.issubdtype(a.dtype, np.integer) or a.dtype == bool:
            mask = a != b
            if not mask.any():
                return None
            diffs = np.abs(a.astype(np.int64) - b.astype(np.int64))
            return (int(np.argmax(mask)),
                    int(diffs.max()),
                    int(mask.sum()))
    # List / nested path
    a_l = s1.to_list()
    b_l = s2.to_list()
    first = None
    n_diff = 0
    max_abs = None
    for i, (x, y) in enumerate(zip(a_l, b_l)):
        if x is None and y is None:
            continue
        if (x is None) != (y is None):
            if first is None: first = i
            n_diff += 1
            continue
        try:
            ax, ay = np.asarray(x), np.asarray(y)
        except Exception:
            if x != y:
                if first is None: first = i
                n_diff += 1
            continue
        if ax.shape != ay.shape:
            if first is None: first = i
            n_diff += 1
            continue
        if np.issubdtype(ax.dtype, np.floating):
            if not np.allclose(ax, ay, equal_nan=True, atol=atol):
                if first is None: first = i
                n_diff += 1
                d = float(np.nanmax(np.abs(ax - ay)))
                max_abs = d if max_abs is None else max(max_abs, d)
        else:
            if not np.array_equal(ax, ay):
                if first is None: first = i
                n_diff += 1
                try:
                    d = float(np.abs(ax.astype(np.int64)
                                     - ay.astype(np.int64)).max())
                    max_abs = d if max_abs is None else max(max_abs, d)
                except Exception:
                    pass
    return None if first is None else (first, max_abs, n_diff)


def _fmt(x, max_len=80):
    s = repr(x)
    return s[:max_len] + '…' if len(s) > max_len else s


def main():
    p = argparse.ArgumentParser()
    p.add_argument('v1_dir')
    p.add_argument('v2_dir')
    p.add_argument('--first-n', type=int, default=10,
                   help='Show top N divergent columns (sorted by '
                   'earliest-divergence time, then col name)')
    p.add_argument('--all', action='store_true',
                   help='List ALL divergent columns (overrides --first-n)')
    p.add_argument('--filter', default='',
                   help='Only show columns containing this substring')
    p.add_argument('--ignore', default='',
                   help='Comma-separated fnmatch patterns to skip')
    p.add_argument('--atol', type=float, default=0.0,
                   help='Float tolerance (default: 0 = exact)')
    p.add_argument('--show-values', action='store_true',
                   help='Print v1/v2 values at first divergence row')
    p.add_argument('--align', choices=('absolute', 'relative'),
                   default='relative',
                   help='absolute: join on raw time column. '
                   'relative: subtract min(time) from each side first '
                   '(default — works when v1 starts at t=0 and v2 '
                   'starts at some absolute t).')
    args = p.parse_args()

    if not os.path.isdir(args.v1_dir):
        print(f'no v1 dir: {args.v1_dir}', file=sys.stderr); return 1
    if not os.path.isdir(args.v2_dir):
        print(f'no v2 dir: {args.v2_dir}', file=sys.stderr); return 1

    print(f'v1: {args.v1_dir}')
    print(f'v2: {args.v2_dir}')
    print('loading...', flush=True)
    v1, v1_skip = _load(args.v1_dir)
    v2, v2_skip = _load(args.v2_dir)
    print(f'  v1: {len(v1)} rows × {len(v1.columns)} cols'
          + (f'  ({len(v1_skip)} cols unreadable)' if v1_skip else ''))
    print(f'  v2: {len(v2)} rows × {len(v2.columns)} cols'
          + (f'  ({len(v2_skip)} cols unreadable)' if v2_skip else ''))

    only_v1 = sorted(set(v1.columns) - set(v2.columns))
    only_v2 = sorted(set(v2.columns) - set(v1.columns))
    common = sorted((set(v1.columns) & set(v2.columns)) - {'time'})
    if args.ignore:
        pats = [p.strip() for p in args.ignore.split(',') if p.strip()]
        common = [c for c in common
                  if not any(fnmatch.fnmatch(c, p) for p in pats)]
    if only_v1:
        print(f'  only-v1 cols: {len(only_v1)} (e.g. {only_v1[:3]})')
    if only_v2:
        print(f'  only-v2 cols: {len(only_v2)} (e.g. {only_v2[:3]})')
    print(f'comparing {len(common)} common columns...', flush=True)

    if args.align == 'relative':
        v1 = v1.with_columns((pl.col('time') - v1['time'].min()).alias('rt'))
        v2 = v2.with_columns((pl.col('time') - v2['time'].min()).alias('rt'))
        join_key = 'rt'
        print(f'  aligning on relative-time '
              f'(v1 t0={int(v1["time"].min())}, '
              f'v2 t0={int(v2["time"].min())})')
    else:
        join_key = 'time'

    j = v1.join(v2, on=join_key, suffix='_v2', how='inner').sort(join_key)
    if len(j) == 0:
        print(f'no overlap on {join_key}'); return 1
    times = j[join_key].to_numpy()
    print(f'common {join_key}s: {len(j)} '
          f'({join_key}={int(times[0])}..{int(times[-1])})')

    diverged = []
    errors = []
    for col in common:
        try:
            res = _columns_differ(j[col], j[f'{col}_v2'], args.atol)
        except Exception as e:
            errors.append((col, repr(e)))
            continue
        if res is None:
            continue
        idx, max_abs, n_diff = res
        diverged.append({
            'col': col, 'first_t': int(times[idx]),
            'first_idx': idx, 'max_abs': max_abs, 'n_diff': n_diff})

    diverged.sort(key=lambda d: (d['first_t'], d['col']))
    print()
    print(f'{"=" * 60}')
    if not diverged and not errors:
        print(f'✓ ALL {len(common)} COLUMNS IDENTICAL '
              f'across {len(j)} common timesteps')
        return 0
    show = diverged
    if args.filter:
        show = [d for d in show if args.filter in d['col']]
    if not args.all:
        show = show[:args.first_n]
    print(f'✗ {len(diverged)}/{len(common)} columns diverge'
          + (f' (filtered: {args.filter}, '
             f'showing {len(show)})' if args.filter
             else f' (showing {"all" if args.all else f"first {args.first_n}"})'))
    print(f'{"=" * 60}')
    for d in show:
        ma = (f' max|Δ|={d["max_abs"]:.6g}'
              if d['max_abs'] is not None else '')
        print(f"  t={d['first_t']:>5}  {d['col']}  "
              f"({d['n_diff']} rows differ{ma})")
        if args.show_values:
            col = d['col']
            v2_col = col + '_v2'
            v1v = j[col][d['first_idx']]
            v2v = j[v2_col][d['first_idx']]
            print(f"    v1: {_fmt(v1v)}")
            print(f"    v2: {_fmt(v2v)}")
    if errors:
        print(f'\n[{len(errors)} compare errors:]')
        for c, e in errors[:5]:
            print(f'  {c}: {e}')
    return 1


if __name__ == '__main__':
    sys.exit(main())
