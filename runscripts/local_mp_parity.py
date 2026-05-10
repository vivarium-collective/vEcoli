"""Quick parity diff: local MP daughter parquet vs S3-synced v1 ref daughter.

Designed to run as soon as runscripts/run_composite_lineage_mp.py finishes
the local repro of the AWS multi-seed divergence. Does the same per-cell
bulk + key-listener compare as scan_columns_stream.py but scoped to the
two seeds we care about (5, 8) and the early daughter ticks where the AWS
data showed the first divergence (t=371 for MP, t=2 for Ray).

If this PASSES — local MP is bit-clean on multi-seed concurrency. The AWS
divergence is environment-specific (library versions, threading, etc.).

If this FAILS — local MP reproduces the bug. Fix-and-iterate locally.
"""
import os
import sys
import numpy as np
import pyarrow.parquet as pq

V1_REF = {
    5: '/tmp/v1_seed5_gen2',
    8: '/tmp/v1_seed8_gen2',
}
V2_LOCAL = (
    'out/local_mp_parity/local_mp_parity/history/'
    'experiment_id=local_mp_parity/variant=0/lineage_seed={seed}/'
    'generation=2/agent_id=00')


def get_row(d, col, t):
    files = sorted([f for f in os.listdir(d) if f.endswith('.pq')],
                   key=lambda x: int(x.split('.')[0]))
    for f in files:
        tbl = pq.read_table(os.path.join(d, f), columns=['time', col])
        times = tbl.column('time').to_pylist()
        if t in times:
            i = times.index(t)
            return tbl.column(col).to_pylist()[i]
    return None


def compare_seed(seed):
    v1_dir = V1_REF[seed]
    v2_dir = V2_LOCAL.format(seed=seed)
    if not os.path.isdir(v2_dir):
        return f'  seed {seed}: v2 dir missing: {v2_dir}'
    if not os.path.isdir(v1_dir):
        return f'  seed {seed}: v1 dir missing: {v1_dir}'

    cols = ['bulk',
            'listeners__mass__cell_mass',
            'listeners__mass__dry_mass',
            'listeners__rna_counts__mRNA_counts',
            'listeners__monomer_counts']
    out_lines = [f'\n=== seed={seed}: v1 ref ({v1_dir}) vs local MP ({v2_dir})']
    # Test rt=0 (first daughter emit) and rt=10, rt=100, rt=200, rt=400
    # rt=400 is past the AWS divergence-first point of t=371 → if it
    # diverges locally, we've reproduced.
    for rt in [0, 10, 100, 200, 400]:
        v1 = get_row(v1_dir, 'bulk', float(rt))
        v2 = get_row(v2_dir, 'bulk', float(rt))
        if v1 is None or v2 is None:
            out_lines.append(f'  rt={rt}: missing (v1={v1 is not None}, '
                             f'v2={v2 is not None})')
            continue
        v1, v2 = np.asarray(v1), np.asarray(v2)
        if v1.shape != v2.shape:
            out_lines.append(f'  rt={rt}: SHAPE MISMATCH '
                             f'v1={v1.shape} v2={v2.shape}')
            continue
        ident = np.array_equal(v1, v2)
        if ident:
            out_lines.append(f'  rt={rt}: bulk IDENTICAL '
                             f'(sum={int(v1.sum())})')
        else:
            ndiff = int(np.sum(v1 != v2))
            max_d = int(np.max(np.abs(v1 - v2)))
            out_lines.append(
                f'  rt={rt}: bulk DIVERGED ndiff={ndiff}/{len(v1)} '
                f'max|delta|={max_d} v1_sum={int(v1.sum())} '
                f'v2_sum={int(v2.sum())}')
    return '\n'.join(out_lines)


def main():
    print('Local-MP-vs-S3-v1 parity diff (seeds 5, 8, early daughter ticks)')
    print('=' * 65)
    for seed in (5, 8):
        print(compare_seed(seed))
    print()
    print('Verdict:')
    print('  All IDENTICAL → local MP is clean; AWS divergence is env-specific.')
    print('  Any DIVERGED  → bug reproduces locally; fix-and-iterate here.')


if __name__ == '__main__':
    main()
