"""Load an actual gen_1 daughter bundle and time per-bucket tick wall
until division. If wall time grows sharply past sim_t=1600, we've
localized the slowdown to a specific sim_t band.
"""
import argparse
import time


def load_composite(bundle_dir):
    from ecoli.library.bigraph_types import ECOLI_TYPES
    from process_bigraph import Composite
    from bigraph_schema import allocate_core
    core = allocate_core()
    core.register_types(ECOLI_TYPES)
    return Composite.load_bundle(bundle_dir, core=core)


def main():
    p = argparse.ArgumentParser()
    p.add_argument(
        '--bundle',
        default='out/two_generations_v2/daughter_states/variant=0/seed=0/generation=1/agent_id=0/daughter_state_0')
    p.add_argument('--max-ticks', type=int, default=1500)
    p.add_argument('--bucket', type=int, default=100)
    args = p.parse_args()

    print(f'Loading {args.bundle}...', flush=True)
    comp = load_composite(args.bundle)
    print(f'  sim_t start: {comp.state.get("global_time",0):.1f}', flush=True)

    bstart = time.monotonic()
    overall = bstart
    pre_agents = len(comp.state.get('agents', {}))
    for i in range(args.max_ticks):
        comp.run(1.0)
        if (i + 1) % args.bucket == 0:
            now = time.monotonic()
            gt = comp.state.get('global_time', 0.0)
            bucket = now - bstart
            print(f'  tick {i+1:>5} (sim_t={gt:.0f}): '
                  f'{bucket:.1f}s ({bucket/args.bucket:.3f}s/tick)',
                  flush=True)
            bstart = now
            if len(comp.state.get('agents', {})) > pre_agents:
                print(f'  Divided at tick {i+1} (sim_t={gt:.0f})', flush=True)
                break

    total = time.monotonic() - overall
    print(f'\nTotal: {total:.1f}s', flush=True)


if __name__ == '__main__':
    main()
