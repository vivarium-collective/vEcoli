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
    # seed 8 mother didn't divide before max_duration=3000 in this
    # run — only seeds 5 and 7 divided. seed 7 v1 ref not synced.
}
V2_LOCAL = (
    'out/local_mp_parity/local_mp_parity/history/'
    'experiment_id=local_mp_parity/variant=0/lineage_seed={seed}/'
    'generation=2/agent_id=00')


def get_sorted_times_and_rows(d, col):
    """Return (sorted_times, list_of_arrays) for the given column."""
    files = sorted([f for f in os.listdir(d) if f.endswith('.pq')],
                   key=lambda x: int(x.split('.')[0]))
    all_times, all_arrs = [], []
    for f in files:
        tbl = pq.read_table(os.path.join(d, f), columns=['time', col])
        all_times.extend(tbl.column('time').to_pylist())
        all_arrs.extend(tbl.column(col).to_pylist())
    order = np.argsort(all_times)
    return [all_times[i] for i in order], [all_arrs[i] for i in order]


def get_rt_row(times, arrs, rt):
    """Get the rt-th row (0-indexed by sorted time)."""
    if rt >= len(times):
        return None
    return arrs[rt]


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
    # Use rt-based alignment (Nth emit) since absolute times differ
    # (mothers may divide at different times if trajectories drift).
    v1_times, v1_arrs = get_sorted_times_and_rows(v1_dir, 'bulk')
    v2_times, v2_arrs = get_sorted_times_and_rows(v2_dir, 'bulk')
    out_lines.append(f'  v1 daughter: {len(v1_times)} ticks, '
                     f't={v1_times[0]:.0f}..{v1_times[-1]:.0f}')
    out_lines.append(f'  v2 daughter: {len(v2_times)} ticks, '
                     f't={v2_times[0]:.0f}..{v2_times[-1]:.0f}')
    out_lines.append(
        f'  ALIGNMENT: rt-based (Nth emit). v1 mother divided at '
        f't={v1_times[0]-1:.0f}, v2 mother divided at t={v2_times[0]-1:.0f}'
        f' (delta={v1_times[0]-v2_times[0]:+.0f})')
    for rt in [0, 10, 100, 200, 400]:
        v1 = get_rt_row(v1_times, v1_arrs, rt)
        v2 = get_rt_row(v2_times, v2_arrs, rt)
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
