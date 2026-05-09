"""Quick parity check: first 5 ticks of daughter (gen 1) cell.

Compares the lineage output's gen 2 partition (post-first-division)
against the per-gen reference. If t=2530..2534 are byte-identical for
the bulk array, division parity holds — we can trust the rest.

Usage:
    uv run python runscripts/check_first5_parity.py \
        --lineage out/lineage_test/lineage_2g_local \
        --reference out/comparison_10s_16g_v2_local \
        --seed 0
"""
import argparse
import sys

import numpy as np
import polars as pl
import pyarrow.dataset as pa_ds


def load_gen2_bulk(experiment_dir, seed):
    path = (f"{experiment_dir}/history/experiment_id="
            f"{experiment_dir.rstrip('/').split('/')[-1]}/variant=0/"
            f"lineage_seed={seed}/generation=2/agent_id=00")
    try:
        ds = pa_ds.dataset(path, format='parquet')
        return pl.from_arrow(ds.to_table(columns=['time', 'bulk'])).sort('time')
    except Exception as e:
        print(f"  could not read {path}: {e}", file=sys.stderr)
        return None


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--lineage', required=True,
                   help='lineage output dir (e.g. out/lineage_test/lineage_2g_local)')
    p.add_argument('--reference', required=True,
                   help='reference (per-gen) dir (e.g. out/comparison_10s_16g_v2_local)')
    p.add_argument('--seed', type=int, default=0)
    p.add_argument('--n-ticks', type=int, default=5,
                   help='compare first N ticks of daughter')
    args = p.parse_args()

    lin = load_gen2_bulk(args.lineage, args.seed)
    ref = load_gen2_bulk(args.reference, args.seed)
    if lin is None or ref is None:
        print('FAIL: missing data')
        sys.exit(1)

    # Both lineage and per-gen daughter parquets start at t=2530 in
    # the per-gen reference; lineage may have leaked pre-division
    # mother rows into gen 2 partition (from emitter buffer flush
    # after partition switch). Filter to first N ticks at-or-after
    # the reference's start time.
    ref_start = int(ref['time'].min())
    lin_post = lin.filter(pl.col('time') >= ref_start).sort('time').head(args.n_ticks)
    ref_post = ref.sort('time').head(args.n_ticks)

    joined = lin_post.join(ref_post, on='time', how='inner', suffix='_ref')
    print(f'Reference start: t={ref_start}')
    print(f'Joined rows: {len(joined)} (target: {args.n_ticks})')

    if len(joined) == 0:
        print('FAIL: no overlap')
        sys.exit(1)

    b_lin = np.array(joined['bulk'].to_list(), dtype=np.int64)
    b_ref = np.array(joined['bulk_ref'].to_list(), dtype=np.int64)
    if b_lin.shape != b_ref.shape:
        print(f'FAIL: shape mismatch lineage={b_lin.shape} ref={b_ref.shape}')
        sys.exit(1)

    diff = np.abs(b_lin - b_ref)
    n_identical = int((diff.sum(axis=1) == 0).sum())
    print(f'Identical timesteps: {n_identical}/{len(joined)}')
    for i in range(len(joined)):
        d = diff[i]
        n_off = int((d > 0).sum())
        max_a = int(d.max())
        l1 = int(d.sum())
        t = int(joined['time'][i])
        flag = 'OK' if d.sum() == 0 else f'DIVERGE max_abs={max_a} l1={l1} n_off={n_off}/{len(d)}'
        print(f'  t={t}: {flag}')

    if n_identical == len(joined):
        print(f'\nPASS: first {args.n_ticks} daughter ticks byte-identical to reference')
        sys.exit(0)
    print(f'\nFAIL: {len(joined) - n_identical}/{len(joined)} timesteps diverge')
    sys.exit(1)


if __name__ == '__main__':
    main()
