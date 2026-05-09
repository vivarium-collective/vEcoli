"""Fast Ray parity test: 2 seeds × gen 1 only via daughter JSON
handoff, run in parallel via two ``@ray.remote`` actors with shared
driver-loaded sim_data (placed in Ray's object store via ``ray.put``).

Validates that:
  - Driver-loaded sim_data is correctly transferred to actors via
    ``ray.put`` / ``ray.get``
  - Each Ray actor independently produces byte-parity output for
    its seed (same code path as MP, just different process boundary)

Iteration cycle: ~30s wall.
"""
import os
import shutil
import subprocess
import sys
import time

import ray


@ray.remote
class GenOneActor:
    """Builds a single gen-1 cell (from daughter JSON) and runs it
    forward 15 sim seconds. Mirrors iter_test_mp's worker but in a
    Ray actor."""

    def __init__(self, sim_data, sim_data_path):
        # ray.get unpacks the deserialized SimulationDataEcoli from
        # the object store into the actor's address space.
        self._sim_data = sim_data
        self._sim_data_path = sim_data_path

    def run_seed(self, seed, config_path, out_dir):
        os.environ.setdefault('POLARS_MAX_THREADS', '1')
        from ecoli.experiments.ecoli_master_sim import EcoliSim

        daughter_path = (
            f'out/comparison_10s_16g_v2_local/daughter_states/'
            f'variant=0/seed={seed}/generation=1/agent_id=0/'
            f'daughter_state_0.json')
        if not os.path.isfile(daughter_path):
            return seed, f'missing daughter JSON: {daughter_path}', 0.0

        sim = EcoliSim.from_file(config_path)
        sim.config['engine'] = 'composite'
        sim.config['sim_data_path'] = self._sim_data_path
        sim.config['initial_state_file'] = os.path.abspath(daughter_path)
        sim.config['lineage_seed'] = seed
        sim.config['seed'] = seed + 1
        sim.config['agent_id'] = '00'
        sim.config['max_duration'] = 15.0
        sim.config['emitter_arg'] = {
            'out_dir': out_dir, 'threaded': False}
        sim.config['daughter_outdir'] = None
        sim._preloaded_sim_data = self._sim_data
        t0 = time.time()
        sim.run()
        return seed, 'ok', time.time() - t0


def main():
    sim_data_path = os.path.abspath(
        'out/comparison_10s_16g_v2_local/variant_sim_data/0.cPickle')
    out_dir = os.path.abspath('out/iter_test_ray')
    config_path = os.path.abspath(
        'configs/composites/lineage_2g_local.json')

    if not os.path.isfile(sim_data_path):
        sys.exit(f"sim_data missing: {sim_data_path}")

    if os.path.isdir(out_dir):
        shutil.rmtree(out_dir)
    os.makedirs(out_dir, exist_ok=True)

    print("[iter_test_ray] Loading sim_data once in driver...",
          flush=True)
    t0 = time.time()
    from ecoli.library.sim_data import LoadSimData
    base = LoadSimData(sim_data_path=sim_data_path, seed=0)
    print(f"  Loaded in {time.time()-t0:.2f}s. "
          f"Initializing Ray (with .rayignore for working_dir)...",
          flush=True)

    ray.init(num_cpus=4, log_to_driver=False)
    sd_ref = ray.put(base.sim_data)
    print("  ray.init + ray.put done. Spawning 2 actors...",
          flush=True)

    actors = [GenOneActor.remote(sd_ref, sim_data_path)
              for _ in range(2)]
    futures = [actors[i].run_seed.remote(i, config_path, out_dir)
               for i in range(2)]

    t_start = time.time()
    results = ray.get(futures)
    elapsed = time.time() - t_start
    print(f"\n[iter_test_ray] Done in {elapsed:.1f}s.", flush=True)
    for seed, status, dt in results:
        print(f"  seed={seed}: {status} ({dt:.1f}s)", flush=True)

    ray.shutdown()

    # Parity check both seeds
    experiment_id = 'lineage_2g_local'
    lineage_dir = os.path.join(out_dir, experiment_id)
    print("\n=== parity check (first 10 daughter ticks per seed) ===",
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
