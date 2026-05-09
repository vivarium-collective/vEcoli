"""Fast iteration on the gen-1 build path.

Loads a known-good daughter cell JSON (from the per-gen Nextflow
output, which has byte parity vs v1), builds gen 1, runs forward
~10 ticks, and checks first 10 ticks are byte-identical to the
per-gen reference's gen 1 parquet.

This exercises the same construction path
(``build_ecoli_document(seed=lineage_seed+gen, initial_state=daughter)``)
that ``EcoliCellProcess.__init__`` will use. Iteration cycle: ~20s
wall (vs ~10 min for a full gen 0 + gen 1 run).

**This is the default iteration runner for any change to the
gen-1-build / daughter-handoff / EcoliCellProcess construction
path.** If you find yourself running test_composite_lineage.py just
to verify a change to that path, stop and use this instead.

Why daughter JSON, not the t=2400 bundle: the framework's
save_bundle/load_bundle round-trip is lossy for some types
(Unum/Quantity per memory:serialize_roundtrip_status). The per-gen
Nextflow path's daughter JSON is byte-correct by construction (it's
what the comparison framework already uses).

Usage:
    uv run python runscripts/iter_test_division.py

Optional knobs:
    --daughter <path>      daughter_state_0.json from per-gen output
    --max_duration 15      sim seconds to run forward
    --n_ticks 10           daughter ticks to compare
"""
import argparse
import os
import shutil
import subprocess
import sys
import time

from ecoli.experiments.ecoli_master_sim import EcoliSim


DEFAULT_DAUGHTER = (
    'out/comparison_10s_16g_v2_local/daughter_states/'
    'variant=0/seed=0/generation=1/agent_id=0/daughter_state_0.json')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--config',
        default='configs/composites/lineage_2g_local.json')
    parser.add_argument(
        '--sim_data_path',
        default='out/comparison_10s_16g_v2_local/variant_sim_data/0.cPickle')
    parser.add_argument(
        '--daughter', default=DEFAULT_DAUGHTER,
        help='daughter_state_0.json from a known-good per-gen run')
    parser.add_argument(
        '--out_dir', default='out/iter_test_div')
    parser.add_argument(
        '--max_duration', type=float, default=15.0,
        help='sim-seconds to run gen 1 forward (default 15; covers '
             '10+ daughter ticks)')
    parser.add_argument(
        '--reference',
        default='out/comparison_10s_16g_v2_local',
        help='per-gen reference for parity comparison')
    parser.add_argument(
        '--n_ticks', type=int, default=10,
        help='daughter ticks to compare')
    args = parser.parse_args()

    if not os.path.isfile(args.daughter):
        print(f"daughter JSON missing: {args.daughter}", file=sys.stderr)
        sys.exit(2)

    # Wipe previous output to avoid stale parquet from earlier iterations.
    if os.path.isdir(args.out_dir):
        shutil.rmtree(args.out_dir)
    os.makedirs(args.out_dir, exist_ok=True)

    # Run a single gen 1 build via the standard composite engine —
    # exactly the path the per-gen Nextflow workflow takes. This is
    # the byte-parity baseline that EcoliCellProcess will need to
    # match.
    sim = EcoliSim.from_file(args.config)
    sim.config['engine'] = 'composite'  # per-gen, not lineage
    sim.config['sim_data_path'] = args.sim_data_path
    sim.config['initial_state_file'] = os.path.abspath(args.daughter)
    sim.config['lineage_seed'] = 0
    sim.config['seed'] = 1                # gen 1 seed (lineage_seed + 1)
    sim.config['agent_id'] = '00'         # gen 1 agent_id
    sim.config['max_duration'] = args.max_duration
    sim.config['emitter_arg'] = {
        'out_dir': args.out_dir, 'threaded': False}
    sim.config['daughter_outdir'] = None

    print(f"=== iter_test_division (gen 1 build path) ===", flush=True)
    print(f"  daughter:      {args.daughter}", flush=True)
    print(f"  out_dir:       {args.out_dir}", flush=True)
    print(f"  max_duration:  {args.max_duration}s", flush=True)
    print(f"  reference:     {args.reference}", flush=True)
    print(f"  n_ticks check: {args.n_ticks}", flush=True)
    t0 = time.time()
    sim.run()
    print(f"\n=== run completed in {time.time()-t0:.1f}s wall ===",
          flush=True)

    experiment_id = sim.experiment_id
    lineage_dir = os.path.join(args.out_dir, experiment_id)
    print(f"\n=== parity check (first {args.n_ticks} daughter ticks) ===",
          flush=True)
    rc = subprocess.run([
        'uv', 'run', '--no-sync', 'python',
        'runscripts/check_first5_parity.py',
        '--lineage', lineage_dir,
        '--reference', args.reference,
        '--seed', '0',
        '--n-ticks', str(args.n_ticks),
    ], check=False)
    sys.exit(rc.returncode)


if __name__ == '__main__':
    main()
