"""One-time: run the v2 composite engine until just before the first
division (sim t=2400, ~130s before t=2530 division), and save a bundle.

Subsequent EcoliCellProcess / division-iteration tests load this
bundle and run forward ~150s — covers the divide + a few daughter
ticks — making iteration cycles ~30s wall instead of ~10 min.

Usage:
    uv run python runscripts/save_pre_divide_checkpoint.py \\
        [--at 2400] \\
        [--out_dir out/checkpoint_pre_divide]
"""
import argparse
import os
import time

from ecoli.experiments.ecoli_master_sim import EcoliSim


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--config',
        default='configs/composites/lineage_2g_local.json')
    parser.add_argument(
        '--sim_data_path',
        default='out/comparison_10s_16g_v2_local/variant_sim_data/0.cPickle')
    parser.add_argument(
        '--at', type=float, default=2400.0,
        help='sim-time to checkpoint at (must be < first division)')
    parser.add_argument(
        '--out_dir', default='out/checkpoint_pre_divide')
    args = parser.parse_args()

    if not os.path.isfile(args.sim_data_path):
        raise SystemExit(f"sim_data_path missing: {args.sim_data_path}")

    sim = EcoliSim.from_file(args.config)
    # Run via the standard composite engine (NOT composite_lineage —
    # we want one cell, halt at checkpoint, save bundle, exit).
    sim.config['engine'] = 'composite'
    sim.config['sim_data_path'] = args.sim_data_path
    sim.config['lineage_seed'] = 0
    sim.config['seed'] = 0
    sim.config['agent_id'] = '0'
    sim.config['composite_checkpoint_at'] = args.at
    sim.config['composite_checkpoint_dir'] = args.out_dir
    # Don't emit parquet for this checkpoint run (not the data we
    # care about — we're after the bundle).
    sim.config['emitter'] = 'parquet'
    sim.config['emitter_arg'] = {
        'out_dir': os.path.join(args.out_dir, '_emit_unused'),
        'threaded': False}
    # Keep max_duration well above checkpoint_at so the run actually
    # gets there (composite_checkpoint_at relies on the sim ticking
    # up to the requested time).
    sim.config['max_duration'] = max(args.at + 100.0, 3000.0)

    print(f"Saving pre-divide bundle at t={args.at}s to {args.out_dir} ...",
          flush=True)
    t0 = time.time()
    sim.run()
    print(f"Done in {time.time()-t0:.1f}s wall.", flush=True)
    print(f"Bundle: {args.out_dir}/document.json + arrays/", flush=True)


if __name__ == '__main__':
    main()
