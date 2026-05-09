"""Byte-parity check across ALL parquet history columns.

Replaces ``check_first5_parity.py``'s bulk-only check with a full
per-column comparison. Two parquet datasets are byte-identical at
the first N timesteps iff every (column × timestep) pair matches.

Usage:
    uv run python runscripts/check_full_parity.py \\
        --lineage out/iter_test_mp/lineage_2g_local \\
        --reference out/comparison_10s_16g_v2_local \\
        --seed 0 --n-ticks 10
"""
import argparse
import os
import sys

import numpy as np
import polars as pl
import pyarrow.dataset as pa_ds


def _load_gen2(experiment_dir, seed):
    """Load all columns from a seed's gen-2 (post-first-divide)
    parquet partition."""
    name = experiment_dir.rstrip('/').split('/')[-1]
    path = (f"{experiment_dir}/history/"
            f"experiment_id={name}/variant=0/"
            f"lineage_seed={seed}/generation=2/agent_id=00")
    if not os.path.isdir(path):
        return None, None
    ds = pa_ds.dataset(path, format='parquet')
    cols = list(ds.schema.names)
    df = pl.from_arrow(ds.to_table()).sort('time')
    return df, cols


def _values_equal(a, b):
    """Compare two parquet cell values for byte-identity. Handles
    scalars, lists/arrays, and nested ragged lists (object arrays)."""
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    # Scalar comparison fast path.
    if not isinstance(a, (list, tuple, np.ndarray)) and not isinstance(
            b, (list, tuple, np.ndarray)):
        if isinstance(a, float) and np.isnan(a) and isinstance(
                b, float) and np.isnan(b):
            return True
        return a == b
    try:
        a_arr = np.asarray(a)
        b_arr = np.asarray(b)
    except Exception:
        return list(a) == list(b)
    if a_arr.shape != b_arr.shape:
        return False
    if a_arr.dtype == object:
        # Nested / ragged: recurse element-wise.
        for x, y in zip(a_arr.flat, b_arr.flat):
            if not _values_equal(x, y):
                return False
        return True
    if a_arr.dtype.kind in ('f', 'c'):
        return bool(np.array_equal(a_arr, b_arr, equal_nan=True))
    return bool(np.array_equal(a_arr, b_arr))


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--lineage', required=True)
    p.add_argument('--reference', required=True)
    p.add_argument('--seed', type=int, default=0)
    p.add_argument('--n-ticks', type=int, default=10)
    p.add_argument('--max-bad-cols', type=int, default=10,
                   help='print at most this many divergent columns')
    args = p.parse_args()

    lin, lin_cols = _load_gen2(args.lineage, args.seed)
    ref, ref_cols = _load_gen2(args.reference, args.seed)
    if lin is None or ref is None:
        print(f"FAIL: missing data\n"
              f"  lineage:   {args.lineage}\n"
              f"  reference: {args.reference}",
              file=sys.stderr)
        sys.exit(2)

    common_cols = sorted(set(lin_cols) & set(ref_cols))
    extra_lin = sorted(set(lin_cols) - set(ref_cols))
    extra_ref = sorted(set(ref_cols) - set(lin_cols))
    print(f"Columns: lineage={len(lin_cols)}, reference={len(ref_cols)}, "
          f"common={len(common_cols)}")
    if extra_lin:
        print(f"  lineage-only ({len(extra_lin)}): "
              f"{extra_lin[:5]}{'...' if len(extra_lin) > 5 else ''}")
    if extra_ref:
        print(f"  reference-only ({len(extra_ref)}): "
              f"{extra_ref[:5]}{'...' if len(extra_ref) > 5 else ''}")

    # Filter to first N ticks at-or-after reference's start time
    # (lineage may include pre-divide rows in gen 2 partition due to
    # parquet emitter buffering).
    ref_start = int(ref['time'].min())
    lin = lin.filter(pl.col('time') >= ref_start).head(args.n_ticks)
    ref = ref.head(args.n_ticks)

    joined = lin.join(ref, on='time', how='inner', suffix='_ref')
    if len(joined) == 0:
        print(f"FAIL: no overlapping timesteps")
        sys.exit(1)

    # For each common column, compare lin vs ref at every timestep.
    # Pre-extract column values as Python lists once per column to
    # avoid polars Series gotchas in the inner loop.
    n_ticks = len(joined)
    times_list = joined['time'].to_list()
    bad_cols = {}  # col_name -> first divergent tick
    for col in common_cols:
        if col == 'time':
            continue
        ref_col = col + '_ref'
        if ref_col not in joined.columns:
            continue
        a_list = joined[col].to_list()
        b_list = joined[ref_col].to_list()
        for i in range(n_ticks):
            if not _values_equal(a_list[i], b_list[i]):
                bad_cols[col] = (i, int(times_list[i]))
                break

    n_total = len(common_cols) - 1  # exclude 'time'
    n_bad = len(bad_cols)
    print(f"\nResult: {n_total - n_bad}/{n_total} columns identical "
          f"across {n_ticks} ticks (t={ref_start}..{ref_start+n_ticks-1})")

    if n_bad:
        print(f"\nDivergent columns ({n_bad}):")
        for col, (tick_idx, t) in list(bad_cols.items())[:args.max_bad_cols]:
            print(f"  {col}: first diverges at t={t}")
        if n_bad > args.max_bad_cols:
            print(f"  ... and {n_bad - args.max_bad_cols} more")
        print(f"\nFAIL: not byte-identical across all columns")
        sys.exit(1)

    print(f"\nPASS: byte-identical across all "
          f"{n_total} data columns × {n_ticks} ticks")
    sys.exit(0)


if __name__ == '__main__':
    main()
