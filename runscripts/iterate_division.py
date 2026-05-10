"""Iterate the division test fast by checkpointing just before division.

Phase 1 (one-time, slow): run fresh v2 composite until just before the
expected division time, save as bundle. Target sim-time defaults to
1800s (cell cycle is ~2400-3000s).

Phase 2 (fast, repeatable): load the checkpoint, run for ``--extra``
more sim-seconds with ``divide=True``. Reports whether division fired
and where daughter bundles were written.

Usage:
    # One-time setup (slow)
    python runscripts/iterate_division.py save \\
        --checkpoint-at 1800 --checkpoint-dir out/pre_divide

    # Fast iteration — repeat after changing division logic
    python runscripts/iterate_division.py run \\
        --checkpoint-dir out/pre_divide \\
        --extra 1800 \\
        --daughter-outdir out/div_iter
"""
import argparse, os, sys, time
from contextlib import chdir

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def cmd_save(args):
    """Build or resume composite, run to checkpoint-at, save bundle.

    ``divide=True`` at build time so the Division step and its
    wired state (``division_threshold`` store, etc.) are part of the
    checkpoint. Otherwise loading with ``divide=True`` would add the
    Division step to a state that never ran it, and the
    ``mass_distribution`` threshold would never be seeded.

    With ``--from-bundle``, resumes from an existing bundle and only
    runs the delta to ``checkpoint_at`` — so iterating a pre-division
    checkpoint forward by ±N seconds doesn't re-simulate from t=0.

    NOTE: division time is seed-dependent. Calibrate
    ``--checkpoint-at`` per seed:
      seed 0,1: divide ≈ t=2530, checkpoint at ~2400 works
      seed 12:  divide ≈ t=2970, checkpoint at ~2900 works
      seed 13:  divide ≈ t=2730 (varies), checkpoint at ~2600
    Pick a value 70-150s before expected divide.
    """
    with chdir(ROOT):
        from ecoli.experiments.ecoli_master_sim import EcoliSim
        sim = EcoliSim.from_file()
        sim.config['engine'] = 'composite'
        if args.seed is not None:
            sim.config['lineage_seed'] = args.seed
            sim.config['seed'] = args.seed
        sim.max_duration = int(args.checkpoint_at)
        # When --parquet-out is given, emit parquet alongside the
        # bundle save. Use sim.config (not sim.emitter attribute) —
        # the attribute path doesn't always propagate to the
        # composite engine's emitter selection.
        if args.parquet_out:
            sim.config['emitter'] = 'parquet'
            sim.config['emitter_arg'] = {
                'out_dir': os.path.abspath(args.parquet_out),
                'threaded': False,
            }
        else:
            sim.config['emitter'] = 'null'
        sim.divide = True
        sim.config['composite_checkpoint_at'] = args.checkpoint_at
        sim.config['composite_checkpoint_dir'] = args.checkpoint_dir
        if args.from_bundle:
            sim.config['initial_state_file'] = args.from_bundle
            source = f'resuming from {args.from_bundle}'
        else:
            source = (f'fresh build (seed={args.seed})' if args.seed is not None
                      else 'fresh build')

        print(f'[save] {source}, running to sim-t={args.checkpoint_at}s, '
              f'saving to {args.checkpoint_dir}/', flush=True)
        t0 = time.monotonic()
        sim.run()
        print(f'[save] done in {time.monotonic()-t0:.1f}s wall', flush=True)
        doc = os.path.join(args.checkpoint_dir, 'document.json')
        if not os.path.isfile(doc):
            print(f'[save] ✗ bundle not written at {args.checkpoint_dir}')
            return 1
        print(f'[save] ✓ bundle at {args.checkpoint_dir}/ '
              f'(document.json size={os.path.getsize(doc)/1e6:.1f} MB)',
              flush=True)
        return 0


def cmd_run(args):
    """Load checkpoint, run with divide=True, report division."""
    with chdir(ROOT):
        os.makedirs(args.daughter_outdir, exist_ok=True)
        from ecoli.experiments.ecoli_master_sim import EcoliSim
        sim = EcoliSim.from_file()
        sim.config['engine'] = 'composite'
        sim.max_duration = int(args.extra)
        sim.emitter = 'null'
        sim.divide = True
        sim.config['initial_state_file'] = args.checkpoint_dir
        # Mid-cycle resume — preserve saved rng_state + allocator_rng
        # so the resumed run continues the SAME RNG sequence the
        # original continuous run had. Without this, the load path
        # reseeds processes at gen-start (v1-mirror), which is wrong
        # for within-gen resume and changes mother bulk at divide
        # time — daughters then diverge from v1.
        sim.config['skip_reseed_on_load'] = True
        sim.daughter_outdir = args.daughter_outdir

        print(f'[run] loading {args.checkpoint_dir}, running up to {args.extra}s '
              f'with divide=True', flush=True)
        t0 = time.monotonic()
        sim.run()
        print(f'[run] done in {time.monotonic()-t0:.1f}s wall', flush=True)

        d0 = os.path.join(args.daughter_outdir, 'daughter_state_0.json')
        d1 = os.path.join(args.daughter_outdir, 'daughter_state_1.json')
        if os.path.isfile(d0) and os.path.isfile(d1):
            print(f'[run] ✓ division fired, daughters:\n  {d0}\n  {d1}',
                  flush=True)
            return 0
        print(f'[run] ✗ division did NOT fire within {args.extra}s extra '
              f'(no daughter JSONs written)', flush=True)
        return 1


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest='cmd', required=True)

    p_save = sub.add_parser('save', help='Create pre-division checkpoint')
    p_save.add_argument(
        '--checkpoint-at', type=float, default=2400.0,
        help='Sim-time to checkpoint at (seed-dependent; '
             'see docstring. seed 0,1 → 2400; seed 12 → 2900)')
    p_save.add_argument('--seed', type=int, default=None,
                        help='lineage_seed for the cell (default: config)')
    p_save.add_argument('--checkpoint-dir', default='out/pre_divide')
    p_save.add_argument('--from-bundle', default=None,
                        help='Resume from an existing bundle directory '
                        'instead of building fresh from sim_data')
    p_save.add_argument(
        '--parquet-out', default=None,
        help='If set, also emit parquet to this dir during the build. '
             'Useful for verifying checkpoint-state parity vs a v1 '
             'reference run before trusting the bundle for iteration.')

    p_run = sub.add_parser('run', help='Load checkpoint + run to division')
    p_run.add_argument('--checkpoint-dir', default='out/pre_divide')
    p_run.add_argument('--extra', type=float, default=1800.0,
                       help='Max extra sim-seconds (should exceed cycle remainder)')
    p_run.add_argument('--daughter-outdir', default='out/div_iter')

    args = p.parse_args()
    if args.cmd == 'save':
        return cmd_save(args)
    return cmd_run(args)


if __name__ == '__main__':
    sys.exit(main())
