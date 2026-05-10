"""Full-column v1↔v2 parity check — every parquet column at every
common timestep, per (seed, gen) cell.

Replaces the bulk-only ``compute_parity_matrix.py`` as the canonical
parity check. Per memory:feedback_parity_scope, never claim
"bit-identical" without enumerating which columns were checked —
this tool checks ALL of them.

For each cell:
  1. ``aws s3 sync`` v1 + v2 history parquet into a tempdir
  2. Read the FULL set of columns from each
  3. Inner-join on ``time``; for every column that appears in both,
     find the first timestep where they differ
  4. Append two rows to the output:
       a) summary row in <output> — overall pass/fail per cell, with
          name of first-divergent column and its first_diff_t
       b) detailed per-column rows in <output>.cols — every divergent
          column's first_diff_t + max_abs (numeric) or "≠" (other)

Re-runnable: cells with their summary row already present are skipped.

Output schemas:
  <output>:
    seed  gen  n_cols_checked  n_cols_identical  n_cols_divergent
    first_div_col  first_div_t  v1_t_max  v2_t_max  status

  <output>.cols:
    seed  gen  column  first_diff_t  max_abs  n_diff_rows  notes
"""
from __future__ import annotations

import argparse
import fnmatch
import os
import re
import shutil
import subprocess
import sys
import tempfile

import numpy as np
import polars as pl
import pyarrow.dataset as pa_ds


_TS_SUFFIX = re.compile(r'_\d{8}-\d{6}$')


def _exp_base(exp):
    """Strip ``_YYYYMMDD-HHMMSS`` auto-rotation suffix to get the
    config's ``experiment_id`` (the outer S3 dir name)."""
    return _TS_SUFFIX.sub('', exp)


def _hist_remote(bucket, prefix, exp, seed, gen):
    agent_id = '0' * gen
    base = _exp_base(exp)
    return (f's3://{bucket}/{prefix}/{base}/{exp}/history/'
            f'experiment_id={exp}/variant=0/lineage_seed={seed}/'
            f'generation={gen}/agent_id={agent_id}/')


def sync_cell(bucket, prefix, exp, seed, gen, dest):
    s3 = _hist_remote(bucket, prefix, exp, seed, gen)
    os.makedirs(dest, exist_ok=True)
    rc = subprocess.run(
        ['aws', 's3', 'sync', s3, dest,
         '--no-progress', '--only-show-errors',
         '--exclude', '*', '--include', '*.pq'],
        check=False, capture_output=True, text=True)
    if rc.returncode != 0:
        sys.stderr.write(f'  sync failed for {s3}: {rc.stderr.strip()}\n')
        return False
    return any(f.endswith('.pq') for f in os.listdir(dest))


def load_full(local_dir):
    """Read the full parquet (all columns) into a polars DataFrame
    sorted by time. Some columns are huge (bulk = 16321 ints/row);
    we still read them — the caller wants every column compared."""
    ds = pa_ds.dataset(local_dir, format='parquet')
    return pl.from_arrow(ds.to_table()).sort('time')


def _columns_differ(s1: pl.Series, s2: pl.Series, atol: float):
    """Return ``(first_diff_idx, max_abs, n_diff_rows)`` or ``None``
    if all elements are identical (NaN-aware, atol-tolerant for
    floats). first_diff_idx is the index into the (sorted) joined
    DataFrame; the caller turns it into a sim-time."""
    if s1.dtype != s2.dtype:
        # Type mismatch — treat as fully-divergent so the caller
        # surfaces it loudly.
        return 0, None, len(s1)
    n = len(s1)
    if n == 0:
        return None
    # ---- Numeric path (vectorized) -----------------------------------
    try:
        a = s1.to_numpy()
        b = s2.to_numpy()
    except Exception:
        a = b = None
    if a is not None and b is not None and a.shape == b.shape:
        if np.issubdtype(a.dtype, np.floating):
            if atol == 0:
                mask = ~np.isclose(a, b, equal_nan=True, atol=0, rtol=0)
            else:
                mask = ~np.isclose(a, b, equal_nan=True, atol=atol)
            if not mask.any():
                return None
            diffs = np.abs(a - b)
            diffs[~np.isfinite(diffs)] = 0  # NaN-NaN handled above
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
    # ---- List / nested path (per-element compare) --------------------
    a_l = s1.to_list()
    b_l = s2.to_list()
    first = None
    n_diff = 0
    max_abs: float | None = None
    for i, (x, y) in enumerate(zip(a_l, b_l)):
        if x is None and y is None:
            continue
        if (x is None) != (y is None):
            if first is None:
                first = i
            n_diff += 1
            continue
        try:
            ax = np.asarray(x)
            ay = np.asarray(y)
        except Exception:
            if x != y:
                if first is None:
                    first = i
                n_diff += 1
            continue
        if ax.shape != ay.shape:
            if first is None:
                first = i
            n_diff += 1
            continue
        if np.issubdtype(ax.dtype, np.floating):
            if not np.allclose(ax, ay, equal_nan=True, atol=atol):
                if first is None:
                    first = i
                n_diff += 1
                d = float(np.nanmax(np.abs(ax - ay)))
                max_abs = d if max_abs is None else max(max_abs, d)
        else:
            if not np.array_equal(ax, ay):
                if first is None:
                    first = i
                n_diff += 1
                try:
                    d = float(np.abs(ax.astype(np.int64)
                                     - ay.astype(np.int64)).max())
                    max_abs = d if max_abs is None else max(max_abs, d)
                except Exception:
                    pass
    return None if first is None else (first, max_abs, n_diff)


def compute_cell(v1_df: pl.DataFrame, v2_df: pl.DataFrame,
                 ignore_patterns: list[str], atol: float):
    """Diff every column. Returns
    ``({summary…}, [{column, first_diff_t, max_abs, n_diff_rows, notes}…])``
    or ``(None, [])`` if there are no common timesteps."""
    v1_cols = set(v1_df.columns)
    v2_cols = set(v2_df.columns)
    only_v1 = v1_cols - v2_cols
    only_v2 = v2_cols - v1_cols
    common_cols = sorted((v1_cols & v2_cols) - {'time'})
    if ignore_patterns:
        common_cols = [c for c in common_cols
                       if not any(fnmatch.fnmatch(c, p)
                                  for p in ignore_patterns)]
    j = v1_df.join(v2_df, on='time', suffix='_v2', how='inner').sort('time')
    if len(j) == 0:
        return None, []
    times = j['time'].to_numpy()

    detailed = []
    n_identical = 0
    for col in common_cols:
        s1 = j[col]
        s2 = j[f'{col}_v2']
        try:
            res = _columns_differ(s1, s2, atol)
        except Exception as e:
            detailed.append({
                'column': col, 'first_diff_t': -1,
                'max_abs': None, 'n_diff_rows': 0,
                'notes': f'compare-error: {e!r}'})
            continue
        if res is None:
            n_identical += 1
            continue
        idx, max_abs, n_diff = res
        detailed.append({
            'column': col, 'first_diff_t': int(times[idx]),
            'max_abs': max_abs, 'n_diff_rows': n_diff, 'notes': ''})

    detailed.sort(key=lambda d: (d['first_diff_t'], d['column']))
    first_div = detailed[0] if detailed else None
    summary = {
        'n_cols_checked': len(common_cols),
        'n_cols_identical': n_identical,
        'n_cols_divergent': len(detailed),
        'first_div_col': first_div['column'] if first_div else '',
        'first_div_t': first_div['first_diff_t'] if first_div else -1,
        'v1_t_max': int(v1_df['time'].max()),
        'v2_t_max': int(v2_df['time'].max()),
        'status': ('IDENTICAL' if not detailed else 'DIVERGENT'),
        'only_v1_cols': ','.join(sorted(only_v1)) or '',
        'only_v2_cols': ','.join(sorted(only_v2)) or '',
    }
    return summary, detailed


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--v1-id', default='comparison_10s_16g_v1_aws')
    p.add_argument('--v2-id',
                   default='comparison_10s_16g_v2_aws_listener_fix')
    p.add_argument('--bucket',
                   default='smsvpctest-shared-sharedbucket60d199d6-abfvwv0day91')
    p.add_argument('--prefix', default='vecoli-output')
    p.add_argument('--seeds', default='0,1,2,3,4,5,6,7,8,9')
    p.add_argument('--gens',
                   default='1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16')
    p.add_argument('--output', default='out/full_parity.tsv')
    p.add_argument('--ignore', default='',
                   help='Comma-separated fnmatch patterns. Columns '
                   'matching any pattern are skipped (e.g. '
                   '"__fragment_index,*__filename"). Default: none.')
    p.add_argument('--atol', type=float, default=0.0,
                   help='Absolute tolerance for float columns. 0 = '
                   'exact equality. Bump only if you need to ignore '
                   'genuine FP noise — and document why.')
    p.add_argument('--tmp', default=None,
                   help='per-cell scratch dir')
    args = p.parse_args()

    seeds = [int(s) for s in args.seeds.split(',') if s]
    gens = [int(g) for g in args.gens.split(',') if g]
    ignore_patterns = [p.strip() for p in args.ignore.split(',') if p.strip()]
    tmp = args.tmp or tempfile.mkdtemp(prefix='full_parity_')
    print(f'Scratch: {tmp}')

    os.makedirs(os.path.dirname(args.output) or '.', exist_ok=True)
    summary_cols = ['seed', 'gen', 'n_cols_checked', 'n_cols_identical',
                    'n_cols_divergent', 'first_div_col', 'first_div_t',
                    'v1_t_max', 'v2_t_max', 'status',
                    'only_v1_cols', 'only_v2_cols']
    detail_cols = ['seed', 'gen', 'column', 'first_diff_t',
                   'max_abs', 'n_diff_rows', 'notes']
    detail_path = args.output + '.cols'

    # Resume: cells already in summary file are skipped. If header
    # doesn't match, rotate the file and start fresh.
    done = set()
    if os.path.exists(args.output):
        with open(args.output) as f:
            existing = f.readline().rstrip('\n').split('\t')
        if existing == summary_cols:
            with open(args.output) as f:
                for line in f.readlines()[1:]:
                    parts = line.split('\t')
                    if len(parts) >= 2:
                        done.add((int(parts[0]), int(parts[1])))
        else:
            print(f'  header mismatch ({existing}); rotating to .bak')
            os.rename(args.output, args.output + '.bak')
            with open(args.output, 'w') as f:
                f.write('\t'.join(summary_cols) + '\n')
            with open(detail_path, 'w') as f:
                f.write('\t'.join(detail_cols) + '\n')
    else:
        with open(args.output, 'w') as f:
            f.write('\t'.join(summary_cols) + '\n')
        with open(detail_path, 'w') as f:
            f.write('\t'.join(detail_cols) + '\n')

    total = len(seeds) * len(gens)
    i = 0
    for seed in seeds:
        for gen in gens:
            i += 1
            if (seed, gen) in done:
                print(f'[{i}/{total}] seed={seed} gen={gen} cached, skip')
                continue
            v1_dir = os.path.join(tmp, args.v1_id, f's{seed}g{gen}')
            v2_dir = os.path.join(tmp, args.v2_id, f's{seed}g{gen}')
            try:
                print(f'[{i}/{total}] seed={seed} gen={gen} syncing...',
                      flush=True)
                ok1 = sync_cell(args.bucket, args.prefix, args.v1_id,
                                seed, gen, v1_dir)
                ok2 = sync_cell(args.bucket, args.prefix, args.v2_id,
                                seed, gen, v2_dir)
                if not (ok1 and ok2):
                    print(f'  missing (v1_ok={ok1}, v2_ok={ok2}), skip')
                    continue
                v1 = load_full(v1_dir)
                v2 = load_full(v2_dir)
                summary, detailed = compute_cell(
                    v1, v2, ignore_patterns, args.atol)
                if summary is None:
                    print('  no overlap, skip')
                    continue
                with open(args.output, 'a') as f:
                    f.write('\t'.join([
                        str(seed), str(gen),
                        str(summary['n_cols_checked']),
                        str(summary['n_cols_identical']),
                        str(summary['n_cols_divergent']),
                        summary['first_div_col'],
                        str(summary['first_div_t']),
                        str(summary['v1_t_max']),
                        str(summary['v2_t_max']),
                        summary['status'],
                        summary['only_v1_cols'],
                        summary['only_v2_cols'],
                    ]) + '\n')
                with open(detail_path, 'a') as f:
                    for row in detailed:
                        f.write('\t'.join([
                            str(seed), str(gen), row['column'],
                            str(row['first_diff_t']),
                            ('' if row['max_abs'] is None
                             else f"{row['max_abs']:.6g}"),
                            str(row['n_diff_rows']),
                            row['notes'],
                        ]) + '\n')
                if summary['status'] == 'IDENTICAL':
                    print(f"  IDENTICAL | {summary['n_cols_checked']} cols")
                else:
                    print(f"  DIVERGENT | {summary['n_cols_divergent']}/"
                          f"{summary['n_cols_checked']} cols differ; "
                          f"first: {summary['first_div_col']} @ "
                          f"t={summary['first_div_t']}")
            finally:
                shutil.rmtree(v1_dir, ignore_errors=True)
                shutil.rmtree(v2_dir, ignore_errors=True)

    n_rows = sum(1 for _ in open(args.output)) - 1
    print(f'\nSummary:  {args.output} ({n_rows} cells)')
    print(f'Per-col:  {detail_path}')


if __name__ == '__main__':
    main()
