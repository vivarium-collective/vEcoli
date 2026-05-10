"""Find which reaction differs in base_reaction_fluxes between v1 and v2 daughter at rt=0.

V1 daughter has 536 nz, V2 has 537 nz. Identify the extra nz reaction in v2
(or the suppressed nz in v1) and report it by name.
"""
import sys
import numpy as np
import pyarrow.parquet as pq

V1_DIR = "out/iter_test_v1_seed12/EXPERIMENT_ID_PLACEHOLDER/history/experiment_id=EXPERIMENT_ID_PLACEHOLDER/variant=0/lineage_seed=12/generation=1/agent_id=0"
V2_DIR = "out/iter_division_seed12/EXPERIMENT_ID_PLACEHOLDER/history/experiment_id=EXPERIMENT_ID_PLACEHOLDER/variant=0/lineage_seed=12/generation=2/agent_id=00"

V1_CONFIG = "out/iter_test_v1_seed12/EXPERIMENT_ID_PLACEHOLDER/configuration/experiment_id=EXPERIMENT_ID_PLACEHOLDER/variant=0"
V2_CONFIG = "out/iter_division_seed12/EXPERIMENT_ID_PLACEHOLDER/configuration/experiment_id=EXPERIMENT_ID_PLACEHOLDER/variant=0"

COL = "listeners__fba_results__base_reaction_fluxes"


def first_row_array(parquet_dir, col):
    import os
    files = [f for f in os.listdir(parquet_dir) if f.endswith('.pq')]
    # numeric sort by leading integer in filename
    files.sort(key=lambda x: int(x.split('.')[0]))
    p = os.path.join(parquet_dir, files[0])
    t = pq.read_table(p, columns=['time', col])
    times = t.column('time').to_pylist()
    arr = t.column(col).to_pylist()
    # rt=0 = first emitted row (smallest time)
    order = np.argsort(times)
    idx0 = order[0]
    print(f"  {parquet_dir.split('/')[-1]}: files={files[:3]}..., first file={files[0]}, n_rows={len(times)}, t_min={times[idx0]}")
    return np.asarray(arr[idx0], dtype=float), times[idx0]


def base_reaction_ids(config_dir):
    import os
    # configuration parquet has reactions schema; look in base_reaction_ids
    # (may be inside a struct or at root). Try the schema first.
    files = sorted(f for f in os.listdir(config_dir) if f.endswith('.pq'))
    p = os.path.join(config_dir, files[0])
    schema = pq.read_schema(p)
    cols_with_base = [c for c in schema.names if 'base_reaction' in c.lower() and 'id' in c.lower()]
    print(f"  config columns with base_reaction*id*: {cols_with_base}")
    if not cols_with_base:
        # try fba_results.reactionIDs or similar
        cols_with_rxn = [c for c in schema.names if 'reaction' in c.lower() and 'id' in c.lower()]
        print(f"  fallback reaction*id* cols: {cols_with_rxn[:10]}")
        if cols_with_rxn:
            cols_with_base = [cols_with_rxn[0]]
    if not cols_with_base:
        return None
    t = pq.read_table(p, columns=cols_with_base)
    val = t.column(cols_with_base[0]).to_pylist()
    if val and isinstance(val[0], list):
        return val[0]
    return val


def get_row_at_time(parquet_dir, col, target_time):
    import os
    files = [f for f in os.listdir(parquet_dir) if f.endswith('.pq')]
    files.sort(key=lambda x: int(x.split('.')[0]))
    for f in files:
        p = os.path.join(parquet_dir, f)
        t = pq.read_table(p, columns=['time', col])
        times = t.column('time').to_pylist()
        if target_time in times:
            arr = t.column(col).to_pylist()
            i = times.index(target_time)
            return np.asarray(arr[i], dtype=float), target_time
    return None, None


def main():
    print("=== Compare V1 daughter at multiple ticks vs V2 daughter ===\n")
    # V1 starts at t=0; V2 daughter time continues from mother (t=2970)
    for v1_rt, v2_rt in [(0, 2970), (1, 2971), (2, 2972), (3, 2973)]:
        v1_arr, v1_t = get_row_at_time(V1_DIR, COL, float(v1_rt))
        v2_arr, v2_t = get_row_at_time(V2_DIR, COL, float(v2_rt))
        if v1_arr is None or v2_arr is None:
            print(f"v1_rt={v1_rt} v2_rt={v2_rt}: v1_found={v1_arr is not None} v2_found={v2_arr is not None}")
            continue
        v1_nz = int(np.count_nonzero(v1_arr))
        v2_nz = int(np.count_nonzero(v2_arr))
        identical = np.array_equal(v1_arr, v2_arr)
        print(f"v1_rt={v1_rt} (t={v1_t}) v1_nz={v1_nz} v1_sum={np.nansum(v1_arr):.6f}")
        print(f"v2_rt={v2_rt} (t={v2_t}) v2_nz={v2_nz} v2_sum={np.nansum(v2_arr):.6f}")
        print(f"  identical: {identical}")
        if not identical:
            diff = ~np.isclose(v1_arr, v2_arr, atol=0, rtol=0, equal_nan=True)
            n_diff = int(np.sum(diff))
            print(f"  n_diff: {n_diff}")
        print()
    # Also try alignment: v1 rt=1 vs v2 rt=0 (v2 may have emitted first row from "post first tick")
    print("=== Alignment hypothesis: v1 rt=N vs v2 rt=N-1 ===")
    for offset in [(1, 2970), (2, 2971), (3, 2972)]:
        v1_arr, v1_t = get_row_at_time(V1_DIR, COL, float(offset[0]))
        v2_arr, v2_t = get_row_at_time(V2_DIR, COL, float(offset[1]))
        if v1_arr is None or v2_arr is None:
            print(f"offset={offset}: missing data")
            continue
        identical = np.array_equal(v1_arr, v2_arr)
        v1_nz = int(np.count_nonzero(v1_arr))
        v2_nz = int(np.count_nonzero(v2_arr))
        print(f"v1@rt={offset[0]} ({v1_nz}nz, sum={np.nansum(v1_arr):.6f}) "
              f"vs v2@rt={offset[1]-2970} ({v2_nz}nz, sum={np.nansum(v2_arr):.6f}): identical={identical}")
        if not identical:
            diff = ~np.isclose(v1_arr, v2_arr, atol=0, rtol=0, equal_nan=True)
            print(f"  n_diff={int(np.sum(diff))}, max_abs_delta={np.max(np.abs(v1_arr-v2_arr)):.6e}")
        print()
    # Old code path bypass
    return
    v1_arr, v1_t = first_row_array(V1_DIR, COL)
    v2_arr, v2_t = first_row_array(V2_DIR, COL)
    print(f"v1 t={v1_t}, v2 t={v2_t}")
    print(f"v1 shape={v1_arr.shape}, v2 shape={v2_arr.shape}")
    print(f"v1 nz={np.count_nonzero(v1_arr)}, v2 nz={np.count_nonzero(v2_arr)}")
    print(f"v1 sum={np.nansum(v1_arr):.10f}, v2 sum={np.nansum(v2_arr):.10f}")

    # find differing indices
    diff_mask = ~np.isclose(v1_arr, v2_arr, atol=0, rtol=0, equal_nan=True)
    diff_idx = np.where(diff_mask)[0]
    print(f"\n{len(diff_idx)} indices differ exactly")

    # Categorize
    v1_zero_v2_nz = np.where((v1_arr == 0) & (v2_arr != 0))[0]
    v2_zero_v1_nz = np.where((v2_arr == 0) & (v1_arr != 0))[0]
    both_nz = np.where(diff_mask & (v1_arr != 0) & (v2_arr != 0))[0]
    print(f"  v1=0 v2!=0: {len(v1_zero_v2_nz)} indices: {v1_zero_v2_nz[:20].tolist()}")
    print(f"  v2=0 v1!=0: {len(v2_zero_v1_nz)} indices: {v2_zero_v1_nz[:20].tolist()}")
    print(f"  both nz, differ: {len(both_nz)} indices")

    # Get reaction names
    print("\n=== V1 base_reaction_ids ===")
    v1_ids = base_reaction_ids(V1_CONFIG)
    if v1_ids is not None:
        print(f"  total ids: {len(v1_ids)}")
        if len(v1_zero_v2_nz):
            print(f"  v1=0 v2!=0 reactions:")
            for i in v1_zero_v2_nz[:10]:
                print(f"    [{i}] {v1_ids[i] if i < len(v1_ids) else '?'}: v1={v1_arr[i]} v2={v2_arr[i]}")
        if len(v2_zero_v1_nz):
            print(f"  v2=0 v1!=0 reactions:")
            for i in v2_zero_v1_nz[:10]:
                print(f"    [{i}] {v1_ids[i] if i < len(v1_ids) else '?'}: v1={v1_arr[i]} v2={v2_arr[i]}")

    # Show a few both-nz differences (smallest deltas)
    if len(both_nz):
        deltas = np.abs(v1_arr[both_nz] - v2_arr[both_nz])
        order = np.argsort(deltas)
        print(f"\n  both-nz deltas: min={deltas.min():.2e}, max={deltas.max():.2e}")
        print(f"  largest 5 deltas:")
        for i in both_nz[order[-5:]][::-1]:
            print(f"    [{i}] {v1_ids[i] if v1_ids and i < len(v1_ids) else '?'}: v1={v1_arr[i]:.6e} v2={v2_arr[i]:.6e} d={v1_arr[i]-v2_arr[i]:.6e}")


if __name__ == '__main__':
    main()
