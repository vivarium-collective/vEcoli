"""Check if v1 daughter rt=0 bulk == v2 daughter rt=0 bulk."""
import os
import numpy as np
import pyarrow.parquet as pq

V1_DIR = "out/iter_test_v1_seed12/gen2/EXPERIMENT_ID_PLACEHOLDER/history/experiment_id=EXPERIMENT_ID_PLACEHOLDER/variant=0/lineage_seed=12/generation=2/agent_id=00"
V2_DIR = "out/iter_division_seed12/EXPERIMENT_ID_PLACEHOLDER/history/experiment_id=EXPERIMENT_ID_PLACEHOLDER/variant=0/lineage_seed=12/generation=2/agent_id=00"


def get_row(parquet_dir, col, target_time):
    files = [f for f in os.listdir(parquet_dir) if f.endswith('.pq')]
    files.sort(key=lambda x: int(x.split('.')[0]))
    for f in files:
        p = os.path.join(parquet_dir, f)
        t = pq.read_table(p, columns=['time', col])
        times = t.column('time').to_pylist()
        if target_time in times:
            arr = t.column(col).to_pylist()
            i = times.index(target_time)
            return np.asarray(arr[i])
    return None


def main():
    # bulk at v1 rt=0 vs v2 rt=2970 (their respective first daughter emits)
    # Is bulk identical at first emit?
    print("=== Test alignment: v1 rt=N vs v2 rt=N-1 (v1 emits before tick, v2 emits after) ===")
    for v1_t, v2_t in [(0.0, None), (1.0, 2970.0), (2.0, 2971.0), (3.0, 2972.0)]:
        for col in ['bulk', 'listeners__mass__cell_mass', 'listeners__mass__dry_mass']:
            v1 = get_row(V1_DIR, col, v1_t) if v1_t is not None else None
            v2 = get_row(V2_DIR, col, v2_t) if v2_t is not None else None
            if v1 is None and v2 is None:
                continue
            if v1 is None:
                print(f"  {col} v1_t={v1_t}: V1 NOT FOUND")
                continue
            if v2 is None:
                print(f"  {col} v1_t={v1_t} only: ", end='')
                if isinstance(v1, np.ndarray) and v1.ndim > 0:
                    print(f"v1 sum={v1.astype(np.int64).sum() if v1.dtype != object else 'obj'}")
                else:
                    print(f"v1={v1}")
                continue
            if isinstance(v1, np.ndarray) and v1.ndim > 0:
                ident = np.array_equal(v1, v2)
                v1s = v1.astype(np.int64).sum() if v1.dtype != object else 'obj'
                v2s = v2.astype(np.int64).sum() if v2.dtype != object else 'obj'
                ndiff = int(np.sum(v1 != v2)) if not ident else 0
                print(f"  {col} v1_t={v1_t} v2_t={v2_t}: v1_sum={v1s} v2_sum={v2s} ident={ident} ndiff={ndiff}")
            else:
                ident = v1 == v2
                print(f"  {col} v1_t={v1_t} v2_t={v2_t}: v1={v1:.4f} v2={v2:.4f} ident={ident} delta={abs(v1-v2):.6e}")
        print()

    # Check whether the daughter rt=0 bulk equals what was saved in daughter_state JSONs
    # (would prove handoff is correct OR show where it diverges)
    import json
    for jf in ['out/daughter_state_0.json', 'out/iter_test_v1_seed12/daughter_states/daughter_state_0.json']:
        if os.path.exists(jf):
            print(f"--- {jf}")
            with open(jf) as f:
                d = json.load(f)
            # bulk is typically a list of [name, count, submass_a, submass_b, ...] tuples
            bulk = d.get('bulk')
            if bulk:
                print(f"  bulk: type={type(bulk).__name__} len={len(bulk)}")
                if isinstance(bulk, list) and bulk and isinstance(bulk[0], (list, tuple)):
                    # extract count column
                    counts = np.array([b[1] for b in bulk])
                    print(f"  bulk counts: dtype={counts.dtype} sum={counts.sum()}")
            # listeners.mass at t=0?
            ms = d.get('listeners', {}).get('mass', {})
            if ms:
                print(f"  listeners.mass keys: {list(ms.keys())[:5]}")
                if 'cell_mass' in ms:
                    print(f"  listeners.mass.cell_mass: {ms['cell_mass']}")


if __name__ == '__main__':
    main()
