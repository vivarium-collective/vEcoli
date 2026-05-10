"""Verify checkpoint save→load→save preserves state.

Phase 1 (slow, ~5 min): build a fresh v2 composite, run to t=T,
save bundle ``A``.

Phase 2 (fast, ~10s): load bundle A with ``skip_reseed_on_load=True``,
re-save as bundle ``B``. (No simulation steps between load and
re-save.)

Verify: A == B at the document.json level for the fields we care
about (allocator_rng, per-process rng_state, bulk, unique).

If A != B, the load path is dropping or transforming state — the
checkpoint isn't a true checkpoint and any iteration on top of it
would produce results that don't match continuous runs.

Usage:
    uv run --no-sync python runscripts/verify_checkpoint_roundtrip.py \
        [--at 10]
"""
import argparse
import json
import os
import sys
from contextlib import chdir

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def cmd():
    parser = argparse.ArgumentParser()
    parser.add_argument('--at', type=float, default=10.0,
                        help='sim-time to checkpoint at')
    parser.add_argument('--bundle-a', default='out/checkpoint_roundtrip/A')
    parser.add_argument('--bundle-b', default='out/checkpoint_roundtrip/B')
    args = parser.parse_args()

    with chdir(ROOT):
        os.makedirs(os.path.dirname(args.bundle_a), exist_ok=True)
        from ecoli.experiments.ecoli_master_sim import EcoliSim

        # ---- Phase 1: fresh build → run to t=T → save A ----
        print(f'[phase 1] fresh build, run to t={args.at}, save -> {args.bundle_a}',
              flush=True)
        sim_a = EcoliSim.from_file()
        sim_a.config['engine'] = 'composite'
        sim_a.max_duration = int(args.at)
        sim_a.emitter = 'null'
        sim_a.divide = True
        sim_a.config['composite_checkpoint_at'] = args.at
        sim_a.config['composite_checkpoint_dir'] = args.bundle_a
        sim_a.run()
        assert os.path.isfile(os.path.join(args.bundle_a, 'document.json')), \
            f'A not saved: {args.bundle_a}'
        print(f'[phase 1] ✓ saved A', flush=True)

        # ---- Phase 2: load A with skip_reseed_on_load=True → save B ----
        print(f'\n[phase 2] load {args.bundle_a} with '
              f'skip_reseed_on_load=True, re-save -> {args.bundle_b}',
              flush=True)
        sim_b = EcoliSim.from_file()
        sim_b.config['engine'] = 'composite'
        sim_b.max_duration = int(args.at)  # don't step further
        sim_b.emitter = 'null'
        sim_b.divide = True
        sim_b.config['initial_state_file'] = args.bundle_a
        sim_b.config['skip_reseed_on_load'] = True
        sim_b.config['composite_checkpoint_at'] = args.at
        sim_b.config['composite_checkpoint_dir'] = args.bundle_b
        sim_b.run()
        assert os.path.isfile(os.path.join(args.bundle_b, 'document.json')), \
            f'B not saved: {args.bundle_b}'
        print(f'[phase 2] ✓ saved B', flush=True)

        # ---- Diff document.json(A, B) ----
        with open(os.path.join(args.bundle_a, 'document.json')) as f:
            doc_a = json.load(f)
        with open(os.path.join(args.bundle_b, 'document.json')) as f:
            doc_b = json.load(f)

        # Quick check: allocator_rng
        agent_a = doc_a['state']['agents']['0']
        agent_b = doc_b['state']['agents']['0']

        rng_a = agent_a.get('allocator_rng', {})
        rng_b = agent_b.get('allocator_rng', {})
        rng_a_key = rng_a.get('key', [])
        rng_b_key = rng_b.get('key', [])

        print(f'\n=== allocator_rng ===')
        print(f'  A.alg = {rng_a.get("alg")}, key[:5] = {rng_a_key[:5]}')
        print(f'  B.alg = {rng_b.get("alg")}, key[:5] = {rng_b_key[:5]}')
        print(f'  identical: {rng_a == rng_b}')

        # Per-process rng_state
        print(f'\n=== per-process rng_state ===')
        a_proc = agent_a.get('process', {})
        b_proc = agent_b.get('process', {})
        for name in sorted(set(a_proc.keys()) | set(b_proc.keys())):
            ra = a_proc.get(name, {}).get('rng_state', None)
            rb = b_proc.get(name, {}).get('rng_state', None)
            if ra is None and rb is None:
                continue
            ka = ra.get('key', [])[:3] if isinstance(ra, dict) else 'None'
            kb = rb.get('key', [])[:3] if isinstance(rb, dict) else 'None'
            ok = '✓' if ra == rb else '✗'
            print(f'  {ok} {name}: A.key[:3]={ka}  B.key[:3]={kb}')

        # Top-level state diff
        print(f'\n=== top-level state.agents.0 keys ===')
        diffs = []
        all_keys = sorted(set(agent_a.keys()) | set(agent_b.keys()))
        for k in all_keys:
            va, vb = agent_a.get(k), agent_b.get(k)
            ok = '✓' if va == vb else '✗'
            print(f'  {ok} {k}')
            if va != vb:
                diffs.append(k)

        print()
        if not diffs and rng_a == rng_b:
            print('✅ ROUND-TRIP CLEAN: A == B for all checked fields')
            return 0
        else:
            print(f'❌ ROUND-TRIP DIVERGENT: differing top-level keys = {diffs}')
            return 1


if __name__ == '__main__':
    sys.exit(cmd())
