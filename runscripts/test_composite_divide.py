"""End-to-end test for v2 composite division + daughter handoff.

Gen 0: `engine: composite`, `divide: true`, `daughter_outdir: out/composite_divide/gen0`
       Runs until DivisionDetected; writes two daughter bundles.

Gen 1: `initial_state_file: <daughter_0 bundle>`, same config.
       Loads the daughter bundle, runs until DivisionDetected.

Success = both generations divide AND gen1 starts from a loaded bundle
with correct state (no crash mid-run).

Usage:
    python runscripts/test_composite_divide.py [--max-duration 5000]
                                                [--division-threshold X]
                                                [--outdir out/composite_divide]
"""
import argparse, os, sys, time
from contextlib import chdir

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run_gen(sim_kwargs):
    """Run one generation via EcoliSim with `engine: composite`."""
    from ecoli.experiments.ecoli_master_sim import EcoliSim
    sim = EcoliSim.from_file()
    for k, v in sim_kwargs.items():
        setattr(sim, k, v)
    # Force composite path
    sim.config['engine'] = 'composite'
    sim.run()
    return sim


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--max-duration', type=float, default=5000.0,
                   help='Per-gen max sim seconds (should exceed ~2400 cell cycle)')
    p.add_argument('--division-threshold', type=float, default=None)
    p.add_argument('--outdir', default='out/composite_divide')
    args = p.parse_args()

    with chdir(ROOT):
        os.makedirs(args.outdir, exist_ok=True)

        # --- Gen 0 ---
        gen0_dir = os.path.join(args.outdir, 'gen0')
        os.makedirs(gen0_dir, exist_ok=True)
        print(f'\n=== GEN 0: fresh build, divide=True, daughter_outdir={gen0_dir} ===',
              flush=True)
        t0 = time.monotonic()
        gen0_kwargs = {
            'max_duration': int(args.max_duration),
            'emitter': 'null',
            'divide': True,
            'daughter_outdir': gen0_dir,
        }
        if args.division_threshold is not None:
            gen0_kwargs['division_threshold'] = args.division_threshold
        run_gen(gen0_kwargs)
        gen0_wall = time.monotonic() - t0
        print(f'[gen0] completed in {gen0_wall:.1f}s wall', flush=True)

        # Verify daughter bundles written
        d0 = os.path.join(gen0_dir, 'daughter_state_0')
        d1 = os.path.join(gen0_dir, 'daughter_state_1')
        if not (os.path.isdir(d0) and os.path.isdir(d1)):
            print(f'[gen0] ✗ daughter bundles NOT written — divide probably did '
                  f'not fire', flush=True)
            print(f'  expected: {d0}/ and {d1}/', flush=True)
            return 1
        print(f'[gen0] ✓ daughter bundles: {d0}, {d1}', flush=True)

        # --- Gen 1 from daughter_0 ---
        gen1_dir = os.path.join(args.outdir, 'gen1')
        os.makedirs(gen1_dir, exist_ok=True)
        print(f'\n=== GEN 1: load from {d0}, divide=True, '
              f'daughter_outdir={gen1_dir} ===', flush=True)
        t0 = time.monotonic()
        gen1_kwargs = {
            'max_duration': int(args.max_duration),
            'emitter': 'null',
            'divide': True,
            'daughter_outdir': gen1_dir,
            'initial_state_file': d0,
        }
        if args.division_threshold is not None:
            gen1_kwargs['division_threshold'] = args.division_threshold
        run_gen(gen1_kwargs)
        gen1_wall = time.monotonic() - t0
        print(f'[gen1] completed in {gen1_wall:.1f}s wall', flush=True)

        g1d0 = os.path.join(gen1_dir, 'daughter_state_0')
        g1d1 = os.path.join(gen1_dir, 'daughter_state_1')
        if not (os.path.isdir(g1d0) and os.path.isdir(g1d1)):
            print(f'[gen1] ✗ daughters NOT written — second division did not '
                  f'fire', flush=True)
            return 1
        print(f'[gen1] ✓ daughter bundles: {g1d0}, {g1d1}', flush=True)

        print(f'\n✓ PASS: 2 generations, 4 daughter bundles total', flush=True)
        return 0


if __name__ == '__main__':
    sys.exit(main())
