"""Full-column parity scan: stream v1 parquet from S3, read v2 locally.

Designed for the local-iteration loop where v2 lives on disk
(``out/v2_lineage_seed12_2gen/...``) and v1 reference parquet is on
the production S3 bucket (``comparison_10s_16g_v1_aws_2026_05``).

No local sync. PyArrow's S3FileSystem streams parquet pages on demand
during the dataset scan, so RAM stays bounded and we skip the
sync-to-disk wait.

Usage:
    uv run python runscripts/scan_columns_stream.py \\
        --v1-s3 s3://BUCKET/vecoli-output/comparison_10s_16g_v1_aws_2026_05/comparison_10s_16g_v1_aws_2026_05/history/experiment_id=comparison_10s_16g_v1_aws_2026_05/variant=0/lineage_seed=12/generation=2/agent_id=00/ \\
        --v2-path out/v2_lineage_seed12_2gen/lineage_2g_local/history/experiment_id=lineage_2g_local/variant=0/lineage_seed=12/generation=2/agent_id=00 \\
        --region us-east-1

Per-column status buckets: identical / t0_only / diverged / nan_only_in_v2 /
shape_mismatch / unsupported. Grouped by listener family (first 2 dotted segments).
"""
import argparse
import math
import os
import sys
from collections import defaultdict

import numpy as np
import pyarrow.dataset as pa_ds
import pyarrow.fs


# --- comparison kernel (lifted from runscripts/aws/scan_all_columns.py) ---

def col_status(a, b):
    if a.shape != b.shape:
        return 'shape_mismatch'
    if a.dtype == object or b.dtype == object:
        try:
            diffs = []
            for i, (x, y) in enumerate(zip(a, b)):
                eq = True
                if isinstance(x, np.ndarray) and isinstance(y, np.ndarray):
                    if x.shape != y.shape:
                        eq = False
                    elif (x.dtype == object and len(x) > 0
                          and isinstance(x[0], np.ndarray)):
                        try:
                            ax = np.stack(x.tolist())
                            ay = np.stack(y.tolist())
                            eq = (ax.shape == ay.shape
                                  and np.array_equal(
                                      ax, ay,
                                      equal_nan=(ax.dtype.kind == 'f')))
                        except Exception:
                            eq = True
                            for xi, yi in zip(x, y):
                                if (isinstance(xi, np.ndarray)
                                        and isinstance(yi, np.ndarray)
                                        and xi.shape == yi.shape
                                        and np.array_equal(xi, yi)):
                                    continue
                                eq = False; break
                    else:
                        eq = np.array_equal(
                            x, y, equal_nan=(x.dtype.kind == 'f'))
                elif isinstance(x, list) and isinstance(y, list):
                    try:
                        ax = np.asarray(x)
                        ay = np.asarray(y)
                        if (ax.dtype == object
                                and len(x) > 0
                                and isinstance(x[0], np.ndarray)):
                            ax = np.stack(x)
                            ay = np.stack(y)
                        if ax.shape != ay.shape:
                            eq = False
                        else:
                            kind = ax.dtype.kind if ax.dtype != object else 'O'
                            if kind == 'f':
                                eq = np.array_equal(ax, ay, equal_nan=True)
                            elif kind in ('i', 'u', 'b'):
                                eq = np.array_equal(ax, ay)
                            else:
                                eq = True
                                for xi, yi in zip(x, y):
                                    if xi is yi:
                                        continue
                                    try:
                                        same = (isinstance(xi, np.ndarray)
                                                and isinstance(yi, np.ndarray)
                                                and xi.shape == yi.shape
                                                and np.array_equal(xi, yi))
                                    except Exception:
                                        same = False
                                    if same:
                                        continue
                                    if xi == yi:
                                        continue
                                    if (isinstance(xi, float)
                                            and isinstance(yi, float)
                                            and math.isnan(xi)
                                            and math.isnan(yi)):
                                        continue
                                    eq = False; break
                    except Exception:
                        if len(x) != len(y):
                            eq = False
                        else:
                            eq = True
                            for xi, yi in zip(x, y):
                                if xi == yi:
                                    continue
                                if (isinstance(xi, float)
                                        and isinstance(yi, float)
                                        and math.isnan(xi)
                                        and math.isnan(yi)):
                                    continue
                                eq = False; break
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
    if a.dtype.kind == 'f':
        a_nan = np.isnan(a)
        b_nan = np.isnan(b)
        if (b_nan & ~a_nan).any():
            return 'nan_only_in_v2'
        if np.array_equal(a, b, equal_nan=True):
            return 'identical'
        if len(a) > 1 and np.array_equal(a[1:], b[1:], equal_nan=True):
            return 't0_only'
        return 'diverged'
    if np.array_equal(a, b):
        return 'identical'
    if len(a) > 1 and np.array_equal(a[1:], b[1:]):
        return 't0_only'
    return 'diverged'


def family(col):
    parts = col.split('__') if '__' in col else col.split('.')
    if len(parts) <= 1:
        return col
    return '.'.join(parts[:2])


# --- dataset helpers ---

def make_dataset(uri, region):
    """Build a pyarrow dataset for a parquet directory at uri.

    If uri starts with ``s3://``, uses S3FileSystem with the given
    region (anonymous=False, picks up the user's AWS creds via the
    default chain). Else treats as local path.
    """
    if uri.startswith('s3://'):
        fs = pyarrow.fs.S3FileSystem(region=region)
        # strip the s3:// prefix for the dataset path
        path = uri[len('s3://'):].rstrip('/')
        return pa_ds.dataset(path, filesystem=fs, format='parquet')
    return pa_ds.dataset(os.path.abspath(uri), format='parquet')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--v1-s3', required=True,
                   help='s3:// URI to v1 parquet dir (or local path)')
    p.add_argument('--v2-path', required=True,
                   help='local v2 parquet dir')
    p.add_argument('--region', default='us-east-1')
    p.add_argument('--max-time', type=int, default=None,
                   help='cap comparison to ticks <= this time (debug)')
    p.add_argument('--show-ids-up-to', type=int, default=10,
                   help='show first N divergent column names per family')
    p.add_argument('--align', choices=('auto', 'absolute', 'shift', 'rt'),
                   default='auto',
                   help="alignment mode: absolute (same t), shift (v2=v1+offset), "
                        "rt (i-th emit on each side), auto (pick from times)")
    args = p.parse_args()

    print(f'v1: {args.v1_s3}')
    print(f'v2: {args.v2_path}')
    print('Building datasets...', flush=True)
    v1_ds = make_dataset(args.v1_s3, args.region)
    v2_ds = make_dataset(args.v2_path, args.region)

    v1_cols = set(v1_ds.schema.names)
    v2_cols = set(v2_ds.schema.names)
    common = sorted(v1_cols & v2_cols)
    print(f'\nschema  v1={len(v1_cols)}  v2={len(v2_cols)}')
    print(f'  v1-only: {len(v1_cols - v2_cols)}')
    print(f'  v2-only: {len(v2_cols - v1_cols)}')
    print(f'  common:  {len(common)}\n', flush=True)

    # Time alignment
    print('Reading time columns...', flush=True)
    v1_t = np.asarray(v1_ds.to_table(columns=['time']).column('time').to_pylist())
    v2_t = np.asarray(v2_ds.to_table(columns=['time']).column('time').to_pylist())
    print(f'  v1 ticks: {len(v1_t)}  range [{v1_t.min()} .. {v1_t.max()}]')
    print(f'  v2 ticks: {len(v2_t)}  range [{v2_t.min()} .. {v2_t.max()}]', flush=True)

    # Alignment strategy:
    # - 'absolute': v1[t] vs v2[t] (same absolute time)
    # - 'shift':    v1[t] vs v2[t+offset] where offset = v2.min - v1.min
    #               (e.g. v1 daughter at rt=0 has abs_t=2527, v2 at abs_t=2970)
    # - 'rt':       v1[i-th emit] vs v2[i-th emit] (relative tick index)
    if args.align == 'auto':
        if v1_t.min() == v2_t.min():
            mode = 'absolute'
        else:
            mode = 'rt'
    else:
        mode = args.align

    if mode == 'absolute':
        offset = 0.0
    elif mode == 'shift':
        offset = float(v2_t.min() - v1_t.min())
    print(f'  alignment mode: {mode} (v1.min={v1_t.min()}, v2.min={v2_t.min()})')

    v1_keep, v2_keep = [], []
    if mode == 'rt':
        v1_t_sorted = np.argsort(v1_t)
        v2_t_sorted = np.argsort(v2_t)
        v1_t_idx = v1_t[v1_t_sorted]
        v2_t_idx = v2_t[v2_t_sorted]
        n = min(len(v1_t_sorted), len(v2_t_sorted))
        for k in range(n):
            i = int(v1_t_sorted[k])
            j = int(v2_t_sorted[k])
            if args.max_time is not None and k > args.max_time:
                break
            v1_keep.append(i); v2_keep.append(j)
    else:
        v2_idx = {int(t): i for i, t in enumerate(v2_t)}
        for i, t in enumerate(v1_t):
            target = int(t + offset)
            j = v2_idx.get(target)
            if j is None:
                continue
            if args.max_time is not None and t > args.max_time:
                continue
            v1_keep.append(i); v2_keep.append(j)
    v1_keep = np.array(v1_keep, dtype=np.int64)
    v2_keep = np.array(v2_keep, dtype=np.int64)
    print(f'  common timesteps: {len(v1_keep)}\n', flush=True)
    if len(v1_keep) == 0:
        print('No overlapping timesteps — bailing.')
        sys.exit(1)

    # Per-column scan
    skip_cols = {'time'}
    results = {}  # col -> status
    diverged_summary = {}  # col -> (first_diff_idx, max_delta)

    for k, col in enumerate(common):
        if col in skip_cols:
            continue
        try:
            v1_arr = np.asarray(v1_ds.to_table(columns=[col]).column(col).to_pylist())
            v2_arr = np.asarray(v2_ds.to_table(columns=[col]).column(col).to_pylist())
        except Exception as e:
            results[col] = 'unsupported'
            continue
        if len(v1_arr) < len(v1_keep) or len(v2_arr) < len(v2_keep):
            results[col] = 'shape_mismatch'
            continue
        a = v1_arr[v1_keep]
        b = v2_arr[v2_keep]
        status = col_status(a, b)
        results[col] = status
        if (k + 1) % 25 == 0:
            print(f'  scanned {k+1}/{len(common)}', flush=True)

    # Bucket
    by_status = defaultdict(list)
    for col, s in results.items():
        by_status[s].append(col)

    print('\n=== summary ===')
    for s in ('identical', 't0_only', 'diverged', 'nan_only_in_v2',
              'shape_mismatch', 'unsupported'):
        cols = by_status.get(s, [])
        print(f'  {s:18s}: {len(cols)}')

    print('\n=== divergence by family ===')
    fam_status = defaultdict(lambda: defaultdict(list))
    for col, s in results.items():
        fam_status[family(col)][s].append(col)
    families = sorted(fam_status.keys())
    for fam in families:
        statuses = fam_status[fam]
        non_id = sum(len(v) for k, v in statuses.items()
                     if k != 'identical')
        if non_id == 0:
            continue
        print(f'\n  {fam}:')
        for s, cols in statuses.items():
            if s == 'identical':
                continue
            print(f'    {s} ({len(cols)}):')
            for c in cols[:args.show_ids_up_to]:
                print(f'      {c}')
            if len(cols) > args.show_ids_up_to:
                print(f'      ... +{len(cols)-args.show_ids_up_to} more')


if __name__ == '__main__':
    main()
