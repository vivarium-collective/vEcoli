"""Compare v1 vs v2 gen_2 trajectories from the moment of load.

Both runs emit history at 1Hz. Loading the daughter bundle (v2) and
loading the daughter JSON (v1) should produce identical state at the
same sim_time. Any per-tick divergence then tells us where v2's load
deviates from v1's.

Usage:
    python runscripts/diff_gen2_trajectories.py [--seed 0]
"""
import argparse
import glob
import os

import polars as pl


def load_gen2(experiment_id, lineage_seed):
    pattern = (f'/home/youdonotexist/code/vEcoli/out/{experiment_id}/history/'
               f'experiment_id={experiment_id}/variant=0/'
               f'lineage_seed={lineage_seed}/generation=2/agent_id=00/*.pq')
    files = sorted(glob.glob(pattern))
    if not files:
        return None
    df = pl.concat([pl.read_parquet(f) for f in files])
    return df.sort('time')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--seed', default='0')
    p.add_argument('--n-rows', type=int, default=10,
                   help='number of early rows to print')
    args = p.parse_args()

    v1 = load_gen2('two_generations_v1', args.seed)
    v2 = load_gen2('two_generations_v2', args.seed)
    if v1 is None or v2 is None:
        print('Missing data'); return

    print(f'V1 gen_2 seed={args.seed}: {len(v1)} rows, t {v1["time"][0]:.0f}..{v1["time"][-1]:.0f}')
    print(f'V2 gen_2 seed={args.seed}: {len(v2)} rows, t {v2["time"][0]:.0f}..{v2["time"][-1]:.0f}')

    # Align by sim time. v2 may emit a few rows ahead/behind v1 boundaries.
    common_cols = sorted(set(v1.columns) & set(v2.columns))
    print(f'Common cols: {len(common_cols)}, '
          f'v1-only: {len(set(v1.columns) - set(v2.columns))}, '
          f'v2-only: {len(set(v2.columns) - set(v1.columns))}')

    # Pick scalar time-series columns to compare
    scalar_cols = []
    for c in common_cols:
        if c == 'time':
            continue
        # Use the first non-null sample
        try:
            v = v1[c][0]
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                scalar_cols.append(c)
        except Exception:
            pass

    # Compute a few summary stats for divergence at FIRST common time
    t1_set = set(v1['time'].to_list())
    t2_set = set(v2['time'].to_list())
    common_t = sorted(t1_set & t2_set)
    if not common_t:
        print('No overlapping time points')
        return
    t0 = common_t[0]
    print(f'\nFirst overlapping time: t={t0}')
    v1_at = v1.filter(pl.col('time') == t0).row(0, named=True)
    v2_at = v2.filter(pl.col('time') == t0).row(0, named=True)
    print(f'\n=== INITIAL state at t={t0} (top scalar diffs by abs %) ===')
    diffs = []
    for c in scalar_cols:
        a = v1_at.get(c)
        b = v2_at.get(c)
        if a is None or b is None:
            continue
        try:
            af, bf = float(a), float(b)
        except (TypeError, ValueError):
            continue
        if af == 0 and bf == 0:
            continue
        denom = max(abs(af), abs(bf), 1e-12)
        pct = abs(af - bf) / denom * 100
        diffs.append((pct, c, af, bf))
    diffs.sort(reverse=True)
    for pct, c, a, b in diffs[:30]:
        print(f'  {pct:6.2f}%  {c[:60]:60s}  v1={a:>14.4g}  v2={b:>14.4g}')

    # Trajectory: first n_rows
    print(f'\n=== Trajectory (first {args.n_rows} rows of cell_mass) ===')
    print(f'{"sim_t":>8s}  {"v1 cell_mass":>14s}  {"v2 cell_mass":>14s}  {"Δ%":>8s}')
    for t in common_t[:args.n_rows]:
        a = v1.filter(pl.col('time') == t)['listeners__mass__cell_mass'][0]
        b = v2.filter(pl.col('time') == t)['listeners__mass__cell_mass'][0]
        delta = (b - a) / a * 100 if a else float('nan')
        print(f'{t:8.0f}  {a:14.2f}  {b:14.2f}  {delta:+7.2f}%')


if __name__ == '__main__':
    main()
