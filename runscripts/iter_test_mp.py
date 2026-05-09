"""Fast MP parity test: 2 seeds × gen 1 only via daughter JSON
handoff, run in parallel via multiprocessing.Pool with shared
parent-loaded sim_data.

Validates that:
  - Parent-loaded sim_data is inherited by workers via fork (COW)
  - Each worker independently produces byte-parity output for its
    seed (no shared state corruption between workers)

Iteration cycle: ~30s wall (vs ~50+ min for full gen 0 + gen 1 MP).
"""
import multiprocessing
import os
import shutil
import subprocess
import sys
import time

# Set in parent before fork; workers inherit via COW.
_PRELOADED_SIM_DATA = None
_SIM_DATA_PATH = None
_OUT_DIR = None
_CONFIG_PATH = None


def _run_one_seed(seed):
    os.environ.setdefault('POLARS_MAX_THREADS', '1')
    from ecoli.experiments.ecoli_master_sim import EcoliSim

    daughter_path = (
        f'out/comparison_10s_16g_v2_local/daughter_states/'
        f'variant=0/seed={seed}/generation=1/agent_id=0/'
        f'daughter_state_0.json')
    if not os.path.isfile(daughter_path):
        return seed, f'missing daughter JSON: {daughter_path}', 0.0

    sim = EcoliSim.from_file(_CONFIG_PATH)
    sim.config['engine'] = 'composite'
    sim.config['sim_data_path'] = _SIM_DATA_PATH
    sim.config['initial_state_file'] = os.path.abspath(daughter_path)
    sim.config['lineage_seed'] = seed
    sim.config['seed'] = seed + 1            # gen 1 seed
    sim.config['agent_id'] = '00'
    sim.config['max_duration'] = 15.0
    sim.config['emitter_arg'] = {
        'out_dir': _OUT_DIR, 'threaded': False}
    sim.config['daughter_outdir'] = None
    # Inject parent-loaded pickle so worker doesn't re-read from disk.
    sim._preloaded_sim_data = _PRELOADED_SIM_DATA
    t0 = time.time()
    sim.run()
    return seed, 'ok', time.time() - t0


def main():
    global _PRELOADED_SIM_DATA, _SIM_DATA_PATH, _OUT_DIR, _CONFIG_PATH
    _SIM_DATA_PATH = os.path.abspath(
        'out/comparison_10s_16g_v2_local/variant_sim_data/0.cPickle')
    _OUT_DIR = os.path.abspath('out/iter_test_mp')
    _CONFIG_PATH = os.path.abspath(
        'configs/composites/lineage_2g_local.json')

    if not os.path.isfile(_SIM_DATA_PATH):
        sys.exit(f"sim_data missing: {_SIM_DATA_PATH}")

    if os.path.isdir(_OUT_DIR):
        shutil.rmtree(_OUT_DIR)
    os.makedirs(_OUT_DIR, exist_ok=True)

    print(f"[iter_test_mp] Loading sim_data once in parent...",
          flush=True)
    t0 = time.time()
    from ecoli.library.sim_data import LoadSimData
    _PRELOADED_SIM_DATA = LoadSimData(
        sim_data_path=_SIM_DATA_PATH, seed=0).sim_data
    print(f"  Loaded in {time.time()-t0:.2f}s. Spawning 2 workers...",
          flush=True)

    ctx = multiprocessing.get_context('fork')
    t_start = time.time()
    with ctx.Pool(processes=2) as pool:
        results = pool.map(_run_one_seed, [0, 1])
    elapsed = time.time() - t_start
    print(f"\n[iter_test_mp] Done in {elapsed:.1f}s.", flush=True)
    for seed, status, dt in results:
        print(f"  seed={seed}: {status} ({dt:.1f}s)", flush=True)

    # Parity check both seeds.
    experiment_id = 'lineage_2g_local'
    lineage_dir = os.path.join(_OUT_DIR, experiment_id)
    print(f"\n=== parity check (first 10 daughter ticks per seed) ===",
          flush=True)
    overall = True
    for seed in [0, 1]:
        print(f"\n--- seed {seed} ---", flush=True)
        rc = subprocess.run([
            'uv', 'run', '--no-sync', 'python',
            'runscripts/check_first5_parity.py',
            '--lineage', lineage_dir,
            '--reference', 'out/comparison_10s_16g_v2_local',
            '--seed', str(seed),
            '--n-ticks', '10',
        ], check=False)
        if rc.returncode != 0:
            overall = False
    sys.exit(0 if overall else 1)


if __name__ == '__main__':
    main()
