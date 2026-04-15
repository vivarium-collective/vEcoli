"""Fast bundle save/load fidelity probe.

Builds a fresh v2 composite from sim_data, snapshots its in-memory
state (dict leaves + every process instance's ``__dict__``), saves a
bundle, loads it back in a fresh Composite, snapshots again, and
diffs.

This is the targeted test for task #5 — no need to run to division.
Any field dropped or changed by the save/load cycle will show up here.

Usage:
    python runscripts/probe_bundle_fidelity.py \
        [--config configs/two_generations_v2.json] [--max-report 50]
"""
import argparse
import os
import sys
import tempfile


def build_composite(config_path):
    from ecoli.experiments.ecoli_master_sim import EcoliSim
    from ecoli.library.bigraph_types import ECOLI_TYPES
    from ecoli.composites.ecoli_composite import build_composite_native
    from process_bigraph import Composite
    from bigraph_schema import allocate_core

    sim = EcoliSim.from_file(filepath=config_path)
    sim.config['engine'] = 'composite'
    sim.config['emitter'] = 'null'
    sim.processes = sim._retrieve_processes(
        sim.processes, sim.add_processes,
        sim.exclude_processes, sim.swap_processes)
    sim.topology = sim._retrieve_topology(
        sim.topology, sim.processes, sim.swap_processes, sim.log_updates)
    sim.process_configs = sim._retrieve_process_configs(
        sim.process_configs, sim.processes)
    core = allocate_core()
    core.register_types(ECOLI_TYPES)
    state = build_composite_native(core, sim.config)
    ecoli = Composite({'schema': {}, 'state': state}, core=core)
    return ecoli, core


def load_composite(bundle_dir):
    from ecoli.library.bigraph_types import ECOLI_TYPES
    from process_bigraph import Composite
    from bigraph_schema import allocate_core
    core = allocate_core()
    core.register_types(ECOLI_TYPES)
    return Composite.load_bundle(bundle_dir, core=core), core


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--config', default='configs/two_generations_v2.json')
    p.add_argument('--max-report', type=int, default=60)
    args = p.parse_args()

    sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
    from diff_bundle_roundtrip import deep_snapshot, diff_snapshots

    print('Building fresh composite...', flush=True)
    import time as _t
    t0 = _t.time()
    pre, _ = build_composite(args.config)
    print(f'  built in {_t.time()-t0:.1f}s', flush=True)

    print('Snapshotting pre-save state...', flush=True)
    snap_pre = deep_snapshot(pre)
    print(f'  entries: {len(snap_pre["entries"])}, '
          f'instances: {len(snap_pre["instances"])}', flush=True)

    out_dir = tempfile.mkdtemp(prefix='probe_fidelity_')
    print(f'Saving bundle -> {out_dir}', flush=True)
    pre.save_bundle(out_dir)

    print('Loading bundle back (fresh core)...', flush=True)
    post, _ = load_composite(out_dir)

    print('Snapshotting post-load state...', flush=True)
    snap_post = deep_snapshot(post)
    print(f'  entries: {len(snap_post["entries"])}, '
          f'instances: {len(snap_post["instances"])}', flush=True)

    print('\nDiffing...', flush=True)
    diffs = diff_snapshots(snap_pre, snap_post)

    by_kind = {}
    for d in diffs:
        by_kind.setdefault(d[0], []).append(d[1:])

    print(f'\n=== Total diffs: {len(diffs)} ===')
    for kind in sorted(by_kind):
        items = by_kind[kind]
        print(f'\n-- {kind} ({len(items)}) --')
        for entry in items[:args.max_report]:
            print('  ' + ' | '.join(str(x) for x in entry))
        if len(items) > args.max_report:
            print(f'  ... {len(items) - args.max_report} more')


if __name__ == '__main__':
    main()
