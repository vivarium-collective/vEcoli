"""Apples-to-apples test: at SAME sim_time, does load-from-bundle make
ticks slower than continuing the same fresh composite?

Procedure:
  1. Build fresh composite (sim_a). Run to ``--warmup`` ticks.
  2. Snapshot timing for the next ``--measure`` ticks of sim_a (= "FRESH
     at t=warmup, no save/load").
  3. Save bundle from sim_a *at the warmup point* (capture state before
     measurement).
  4. Reload bundle into sim_b. Run ``--measure`` ticks. (= "LOADED at
     t=warmup, post-save/load")
  5. Diff per-tick timings + Composite.process_update_time vs framework_time.

If sim_b is slower, the load itself is adding overhead. If they're
equal, the per-tick cost at sim_t=warmup is intrinsic.
"""
import argparse
import os
import sys
import tempfile
import time


def build_composite(config_path):
    from ecoli.experiments.ecoli_master_sim import EcoliSim
    from ecoli.library.bigraph_types import ECOLI_TYPES
    from ecoli.composites.ecoli_composite import build_composite_native
    from process_bigraph import Composite
    from bigraph_schema import allocate_core

    sim = EcoliSim.from_file(filepath=config_path)
    sim.config['engine'] = 'composite'
    sim.config['emitter'] = 'null'
    sim.config['divide'] = False
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
    return Composite({'schema': {}, 'state': state}, core=core)


def load_composite(bundle_dir):
    from ecoli.library.bigraph_types import ECOLI_TYPES
    from process_bigraph import Composite
    from bigraph_schema import allocate_core
    core = allocate_core()
    core.register_types(ECOLI_TYPES)
    return Composite.load_bundle(bundle_dir, core=core)


def measured_run(comp, n_ticks, label):
    """Run n_ticks, accumulate per-bucket timings + framework/process splits."""
    print(f'\n== {label}: {n_ticks} ticks ==', flush=True)
    bucket = max(10, n_ticks // 5)
    overall = time.monotonic()
    bstart = overall
    proc_acc = 0.0
    fw_acc = 0.0
    for i in range(n_ticks):
        comp.run(1.0)
        proc_acc += getattr(comp, 'process_update_time', 0.0)
        fw_acc += getattr(comp, 'framework_time', 0.0)
        if (i + 1) % bucket == 0:
            now = time.monotonic()
            gt = comp.state.get('global_time', 0.0)
            print(f'  ticks {i+2-bucket}..{i+1} (sim_t={gt:.0f}): '
                  f'{(now-bstart):.2f}s wall '
                  f'({(now-bstart)/bucket:.3f}s/tick)', flush=True)
            bstart = now
    total = time.monotonic() - overall
    print(f'  {label} TOTAL: {total:.2f}s wall ({total/n_ticks:.3f}s/tick)',
          flush=True)
    print(f'  {label} process_update_time sum: {proc_acc:.2f}s '
          f'({proc_acc/n_ticks:.3f}s/tick)', flush=True)
    print(f'  {label} framework_time sum:      {fw_acc:.2f}s '
          f'({fw_acc/n_ticks:.3f}s/tick)', flush=True)
    return total, proc_acc, fw_acc


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--config', default='configs/two_generations_v2.json')
    p.add_argument('--warmup', type=int, default=1300,
                   help='Ticks to run before measuring (=approx gen_1 length)')
    p.add_argument('--measure', type=int, default=100,
                   help='Ticks measured after warmup')
    args = p.parse_args()

    print('=== Phase 0: build fresh + warm up to sim_t=%d ===' % args.warmup,
          flush=True)
    sim_a = build_composite(args.config)
    t_warm0 = time.monotonic()
    for i in range(args.warmup):
        sim_a.run(1.0)
        if (i+1) % 200 == 0:
            print(f'  warmup tick {i+1}, sim_t={sim_a.state.get("global_time",0):.0f}',
                  flush=True)
    print(f'Warmup done in {time.monotonic()-t_warm0:.1f}s '
          f'(sim_t now {sim_a.state.get("global_time",0):.1f})', flush=True)

    out_dir = tempfile.mkdtemp(prefix='probe_load_oh_')
    print(f'\n=== Phase 1: save sim_a bundle -> {out_dir} ===', flush=True)
    sim_a.save_bundle(out_dir)

    # Measure FRESH (continuing sim_a) FIRST to avoid having to re-warm.
    print('\n=== Phase 2: measure FRESH (sim_a continued) ===', flush=True)
    fresh_total, fresh_proc, fresh_fw = measured_run(
        sim_a, args.measure, 'FRESH')

    # Now load and measure
    print('\n=== Phase 3: load bundle into sim_b ===', flush=True)
    from process_bigraph.types.process import _shared_processes
    _shared_processes.clear()
    sim_b = load_composite(out_dir)
    print(f'  loaded; sim_t={sim_b.state.get("global_time",0):.1f}', flush=True)

    print('\n=== Phase 4: measure LOADED (sim_b) ===', flush=True)
    loaded_total, loaded_proc, loaded_fw = measured_run(
        sim_b, args.measure, 'LOADED')

    print('\n=== SUMMARY ===')
    print(f'FRESH  ({args.measure} ticks @ sim_t={args.warmup}): '
          f'{fresh_total:.2f}s ({fresh_total/args.measure:.3f}s/tick)')
    print(f'LOADED ({args.measure} ticks @ sim_t={args.warmup}): '
          f'{loaded_total:.2f}s ({loaded_total/args.measure:.3f}s/tick)')
    if fresh_total > 0:
        print(f'LOADED/FRESH wall ratio:  {loaded_total/fresh_total:.2f}x')
    if fresh_proc > 0:
        print(f'LOADED/FRESH proc ratio:  {loaded_proc/fresh_proc:.2f}x')
    if fresh_fw > 0:
        print(f'LOADED/FRESH fw ratio:    {loaded_fw/fresh_fw:.2f}x')


if __name__ == '__main__':
    main()
