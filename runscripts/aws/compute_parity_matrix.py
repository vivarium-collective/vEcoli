"""Compute v1<->v2 bulk-count parity per (seed, gen) over all timesteps.

For each cell:
  1. aws s3 sync just the history parquet for v1 and v2 into a tempdir
  2. read time + bulk columns
  3. inner-join on time, vectorized diff, summarize
  4. append one row to parity_matrix.tsv and free the tempdir

Re-runnable: cells already present in the output TSV are skipped, so you can
extend `--seeds`/`--gens` ranges incrementally without redoing work.

Designed to run on the head node where S3 transfer is in-region. Memory peak
per cell is roughly 2x the cell's bulk array (~250 MB), so 8 GB head is plenty.

Output schema (TSV):
  seed  gen  n_steps  n_identical  first_diff_t  max_abs  max_l1  n_species
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile

import numpy as np
import polars as pl
import pyarrow.dataset as pa_ds


def sync_cell(bucket, prefix, exp, seed, gen, dest):
    # agent_id encodes the binary lineage path: gen zeros wide for the
    # always-take-daughter-0 lineage we run with single_daughters=True.
    agent_id = '0' * gen
    s3 = (f's3://{bucket}/{prefix}/{exp}/{exp}/history/'
          f'experiment_id={exp}/variant=0/lineage_seed={seed}/'
          f'generation={gen}/agent_id={agent_id}/')
    os.makedirs(dest, exist_ok=True)
    rc = subprocess.run(
        ['aws', 's3', 'sync', s3, dest,
         '--no-progress', '--only-show-errors',
         '--exclude', '*', '--include', '*.pq'],
        check=False, capture_output=True, text=True)
    if rc.returncode != 0:
        sys.stderr.write(f'  s3 sync failed for {s3}: {rc.stderr.strip()}\n')
        return False
    return any(f.endswith('.pq') for f in os.listdir(dest))


def load_bulk(local_dir):
    ds = pa_ds.dataset(local_dir, format='parquet')
    tbl = ds.to_table(columns=['time', 'bulk'])
    return pl.from_arrow(tbl).sort('time')


def compute_cell(v1_df, v2_df):
    common = v1_df.join(v2_df, on='time', suffix='_v2', how='inner').sort('time')
    if len(common) == 0:
        return None
    b1 = np.array(common['bulk'].to_list(), dtype=np.int64)
    b2 = np.array(common['bulk_v2'].to_list(), dtype=np.int64)
    if b1.shape != b2.shape:
        return {'shape_mismatch': f'v1={b1.shape} v2={b2.shape}'}
    diffs = np.abs(b1 - b2)
    n_diff = (diffs > 0).sum(axis=1)
    max_abs = diffs.max(axis=1)
    l1 = diffs.sum(axis=1)
    times = common['time'].to_numpy()
    n_identical = int((n_diff == 0).sum())
    first_diff = int(times[np.argmax(n_diff > 0)]) if n_diff.any() else -1
    # Division-time signal: the last (largest) time in each run is the
    # absolute global time at which the cell divided.
    return {
        'n_steps': int(len(common)),
        'n_identical': n_identical,
        'first_diff_t': first_diff,
        'max_abs': int(max_abs.max()),
        'max_l1': int(l1.max()),
        'n_species': int(b1.shape[1]),
        'v1_t_max': int(v1_df['time'].max()),
        'v2_t_max': int(v2_df['time'].max()),
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--v1-id', default='comparison_10s_16g_v1_aws')
    p.add_argument('--v2-id', default='comparison_10s_16g_v2_aws_listener_fix')
    p.add_argument('--bucket',
                   default='smsvpctest-shared-sharedbucket60d199d6-abfvwv0day91')
    p.add_argument('--prefix', default='vecoli-output')
    p.add_argument('--seeds', default='0,1,2,3,4,5,6,7,8,9')
    p.add_argument('--gens',
                   default='1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16')
    p.add_argument('--output', default='out/parity_matrix.tsv')
    p.add_argument('--tmp', default=None,
                   help='per-cell scratch dir; default: mkdtemp under /tmp')
    args = p.parse_args()

    seeds = [int(s) for s in args.seeds.split(',') if s]
    gens = [int(g) for g in args.gens.split(',') if g]
    tmp = args.tmp or tempfile.mkdtemp(prefix='parity_matrix_')
    print(f'Scratch: {tmp}')

    os.makedirs(os.path.dirname(args.output) or '.', exist_ok=True)
    cols = ['seed', 'gen', 'n_steps', 'n_identical',
            'first_diff_t', 'max_abs', 'max_l1', 'n_species',
            'v1_t_max', 'v2_t_max']

    # Resume: skip cells already in the output. If the existing file's
    # header doesn't match the current schema (e.g. older runs missing
    # v1_t_max/v2_t_max), invalidate it and start fresh — avoids quietly
    # serving stale data with missing columns.
    done = set()
    if os.path.exists(args.output):
        with open(args.output) as f:
            existing_header = f.readline().strip().split('\t')
        if existing_header == cols:
            with open(args.output) as f:
                for line in f.readlines()[1:]:
                    fields = line.split('\t')
                    if len(fields) >= 2:
                        done.add((int(fields[0]), int(fields[1])))
        else:
            print(f'header mismatch (have {existing_header}, want {cols}); '
                  f'rebuilding {args.output}')
            os.rename(args.output, args.output + '.bak')
            with open(args.output, 'w') as f:
                f.write('\t'.join(cols) + '\n')
    else:
        with open(args.output, 'w') as f:
            f.write('\t'.join(cols) + '\n')

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
                ok1 = sync_cell(args.bucket, args.prefix, args.v1_id, seed, gen, v1_dir)
                ok2 = sync_cell(args.bucket, args.prefix, args.v2_id, seed, gen, v2_dir)
                if not (ok1 and ok2):
                    print(f'  missing data (v1_ok={ok1}, v2_ok={ok2}), skip')
                    continue
                v1 = load_bulk(v1_dir)
                v2 = load_bulk(v2_dir)
                stats = compute_cell(v1, v2)
                if stats is None or 'shape_mismatch' in stats:
                    print(f'  skipped: {stats}')
                    continue
                with open(args.output, 'a') as f:
                    row = [str(seed), str(gen), str(stats['n_steps']),
                           str(stats['n_identical']),
                           str(stats['first_diff_t']),
                           str(stats['max_abs']), str(stats['max_l1']),
                           str(stats['n_species']),
                           str(stats['v1_t_max']), str(stats['v2_t_max'])]
                    f.write('\t'.join(row) + '\n')
                tag = ('IDENTICAL'
                       if stats['n_identical'] == stats['n_steps']
                       else f"first_diff_t={stats['first_diff_t']}")
                print(f'  {tag} | n_steps={stats["n_steps"]} '
                      f'max_abs={stats["max_abs"]} max_l1={stats["max_l1"]}')
            finally:
                shutil.rmtree(v1_dir, ignore_errors=True)
                shutil.rmtree(v2_dir, ignore_errors=True)

    print(f'\nWrote {args.output} ({sum(1 for _ in open(args.output)) - 1} rows)')


if __name__ == '__main__':
    main()
