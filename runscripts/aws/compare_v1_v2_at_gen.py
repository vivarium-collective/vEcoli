"""Compare v1 vs v2 history parquet at a chosen seed/generation.

Reads both runs' history parquet directly from S3, aligns rows by `time`,
and diffs the `bulk` molecule-count vectors element-wise. Reports per-row
n_diff/max_abs/L1 plus a summary of where divergence first appears.

Usage (from any machine with AWS creds + polars/pyarrow/s3fs):
    uv run python runscripts/aws/compare_v1_v2_at_gen.py --seed 0 --gen 3

Defaults compare the AWS v1 atlantis run vs the v2 head-node run.
"""
import argparse
import os
import subprocess
import sys
import tempfile
import numpy as np
import polars as pl
import pyarrow.dataset as pa_ds


def load_bulk(bucket, prefix, exp, seed, gen, agent_id=None, tmpdir=None):
    if agent_id is None:
        agent_id = '0' * gen
    s3_uri = (f's3://{bucket}/{prefix}/{exp}/{exp}/history/'
              f'experiment_id={exp}/variant=0/lineage_seed={seed}/'
              f'generation={gen}/agent_id={agent_id}/')
    local = os.path.join(tmpdir, exp, f'seed{seed}_gen{gen}')
    os.makedirs(local, exist_ok=True)
    print(f'  syncing {s3_uri} -> {local}')
    subprocess.run(['aws', 's3', 'sync', s3_uri, local,
                    '--no-progress', '--only-show-errors',
                    '--exclude', '*', '--include', '*.pq'],
                   check=True)
    dataset = pa_ds.dataset(local, format='parquet')
    tbl = dataset.to_table(columns=['time', 'bulk'])
    return pl.from_arrow(tbl).sort('time')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--seed', type=int, default=0)
    p.add_argument('--gen', type=int, default=3)
    p.add_argument('--v1-id', default='comparison_10s_16g_v1_aws')
    p.add_argument('--v2-id', default='comparison_10s_16g_v2_aws_listener_fix')
    p.add_argument('--bucket',
                   default='smsvpctest-shared-sharedbucket60d199d6-abfvwv0day91')
    p.add_argument('--prefix', default='vecoli-output')
    p.add_argument('--region', default='us-gov-west-1')
    p.add_argument('--samples', type=int, default=12,
                   help='approx number of timesteps to print in detail')
    args = p.parse_args()

    tmp = tempfile.mkdtemp(prefix='compare_v1_v2_')
    print(f'Staging parquet under {tmp}')
    print(f'Loading v1 {args.v1_id!r} seed={args.seed} gen={args.gen} ...')
    v1 = load_bulk(args.bucket, args.prefix, args.v1_id, args.seed, args.gen, tmpdir=tmp)
    print(f'Loading v2 {args.v2_id!r} seed={args.seed} gen={args.gen} ...')
    v2 = load_bulk(args.bucket, args.prefix, args.v2_id, args.seed, args.gen, tmpdir=tmp)
    print(f'  v1 rows: {len(v1)}, time range: '
          f'{v1["time"].min()} .. {v1["time"].max()}')
    print(f'  v2 rows: {len(v2)}, time range: '
          f'{v2["time"].min()} .. {v2["time"].max()}')

    common = v1.join(v2, on='time', suffix='_v2', how='inner').sort('time')
    if len(common) == 0:
        print('No common timesteps — cannot compare.')
        sys.exit(1)
    print(f'Common timesteps: {len(common)}')

    b1 = np.array(common['bulk'].to_list())       # (n_steps, n_species)
    b2 = np.array(common['bulk_v2'].to_list())
    if b1.shape != b2.shape:
        print(f'bulk shape mismatch: v1={b1.shape}, v2={b2.shape}')
        sys.exit(1)
    diffs = np.abs(b1.astype(np.int64) - b2.astype(np.int64))
    n_diff = (diffs > 0).sum(axis=1)
    max_abs = diffs.max(axis=1)
    l1 = diffs.sum(axis=1)
    times = common['time'].to_numpy()

    n_species = b1.shape[1]
    n_identical = int((n_diff == 0).sum())
    first_diff_idx = int(np.argmax(n_diff > 0)) if n_diff.any() else -1
    first_diff_time = int(times[first_diff_idx]) if first_diff_idx >= 0 else None

    # Pick approximately `samples` rows, evenly spaced, plus the first row
    # where divergence appears (so we always see the moment of departure).
    step = max(1, len(common) // args.samples)
    show = sorted(set(list(range(0, len(common), step)) +
                      [len(common) - 1] +
                      ([first_diff_idx] if first_diff_idx >= 0 else [])))

    print()
    print(f'  {"time":>6} | {"n_diff":>6}/{n_species:<6} | '
          f'{"max_abs":>8} | {"L1":>10}  status')
    print('  ' + '-' * 60)
    for i in show:
        status = 'IDENTICAL' if n_diff[i] == 0 else ''
        if i == first_diff_idx and n_diff[i] != 0:
            status = 'FIRST DIFF'
        print(f'  {int(times[i]):>6} | {int(n_diff[i]):>6}/{n_species:<6} | '
              f'{int(max_abs[i]):>8} | {int(l1[i]):>10}  {status}')

    print()
    print('Summary')
    print(f'  Identical timesteps    : {n_identical} / {len(common)}')
    print(f'  First divergence at t  : {first_diff_time}')
    print(f'  Max n_diff (any time)  : {int(n_diff.max())} / {n_species}')
    print(f'  Max |delta| (any time) : {int(max_abs.max())}')
    print(f'  Max L1   (any time)    : {int(l1.max())}')
    if n_identical == len(common):
        print('  >>> bit-parity confirmed for bulk over this generation.')
    elif first_diff_time == int(times[0]):
        print('  >>> diverges at t=0 of this generation '
              '(check daughter handoff or upstream gen).')
    else:
        # Approximate when the first divergence happens within the gen
        frac = first_diff_idx / max(1, len(common) - 1)
        print(f'  >>> diverges {frac:.0%} of the way through the generation.')


if __name__ == '__main__':
    main()
