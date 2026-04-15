"""Quick test of the composite_checkpoint_at mechanism.

Runs EcoliSim with engine=composite, composite_checkpoint_at=60,
composite_checkpoint_dir=<dir>. Verifies a bundle is written and
state at t=60 can be loaded and run for 10 more seconds.

Usage:
    python runscripts/test_checkpoint.py
"""
import os, sys, time
from contextlib import chdir

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    with chdir(ROOT):
        checkpoint_dir = 'out/checkpoint_test/cp_at_60'

        # Phase 1: build + run to t=60 + save
        from ecoli.experiments.ecoli_master_sim import EcoliSim
        sim = EcoliSim.from_file()
        sim.config['engine'] = 'composite'
        sim.max_duration = 100
        sim.emitter = 'null'
        sim.divide = False
        sim.config['composite_checkpoint_at'] = 60
        sim.config['composite_checkpoint_dir'] = checkpoint_dir
        print(f'[phase 1] build + run to t=60 + save bundle', flush=True)
        t0 = time.monotonic()
        sim.run()
        print(f'[phase 1] done in {time.monotonic()-t0:.1f}s', flush=True)

        if not os.path.isdir(checkpoint_dir):
            print(f'[phase 1] ✗ checkpoint bundle not written')
            return 1
        doc = os.path.join(checkpoint_dir, 'document.json')
        print(f'[phase 1] ✓ bundle at {checkpoint_dir}, '
              f'document.json size={os.path.getsize(doc)} bytes', flush=True)

        # Phase 2: load bundle + run 10s more (no checkpoint_at this time)
        sim2 = EcoliSim.from_file()
        sim2.config['engine'] = 'composite'
        sim2.max_duration = 10
        sim2.emitter = 'null'
        sim2.divide = False
        sim2.config['initial_state_file'] = checkpoint_dir
        print(f'\n[phase 2] load bundle + run 10s more', flush=True)
        t0 = time.monotonic()
        sim2.run()
        print(f'[phase 2] done in {time.monotonic()-t0:.1f}s '
              f'(should include bundle load + 10s sim)', flush=True)

        print('\n✓ PASS: checkpoint save + load + resume works')
        return 0


if __name__ == '__main__':
    sys.exit(main())
