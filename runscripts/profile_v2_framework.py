"""Profile v2 to break down where time goes outside of process
``next_update`` calls.

Loads a fresh-built composite (or checkpoint), runs N ticks, and
reports:
  1. Per-tick total / framework / process-update breakdown
     (uses ``Composite.framework_time`` and ``process_update_time``).
  2. Top framework functions by cumulative time (cProfile),
     excluding anything under process ``invoke``/``next_update``.
  3. Time spent in key framework hot paths (apply_updates,
     reconcile, realize, view, project, emit_history).

Usage:
    uv run python runscripts/profile_v2_framework.py --ticks 50
"""
import argparse
import cProfile
import os
import pstats
import sys
import time
from io import StringIO
from contextlib import chdir

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def build_composite(sim_data_path):
    from ecoli.experiments.ecoli_master_sim import EcoliSim
    from ecoli.library.bigraph_types import ECOLI_TYPES
    from ecoli.composites.ecoli_composite import build_composite_native
    from process_bigraph import Composite
    from process_bigraph.types.process import (
        register_types as register_process_bigraph_types)
    from bigraph_schema import Core, BASE_TYPES

    sim = EcoliSim.from_file('out/two_generations_v2/nextflow/workflow_config_stripped.json')
    sim.config['sim_data_path'] = sim_data_path
    sim.config['variant'] = 0
    sim.config['seed'] = 0
    sim.config['lineage_seed'] = 0
    sim.config['agent_id'] = '0'
    sim.processes = sim._retrieve_processes(
        sim.processes, sim.add_processes,
        sim.exclude_processes, sim.swap_processes)
    sim.topology = sim._retrieve_topology(
        sim.topology, sim.processes, sim.swap_processes, sim.log_updates)
    sim.process_configs = sim._retrieve_process_configs(
        sim.process_configs, sim.processes)

    core = Core(BASE_TYPES)
    register_process_bigraph_types(core)
    core.register_types(ECOLI_TYPES)
    state = build_composite_native(core, sim.config)
    ecoli = Composite({'schema': {}, 'state': state,
                       'run_steps_on_init': True}, core=core)
    ecoli.to_run = []
    return ecoli


def run_with_breakdown(ecoli, ticks):
    """Run N 1-second ticks, accumulating timing per tick. Returns
    list of (total_ms, framework_ms, process_ms) per tick."""
    breakdown = []
    for _ in range(ticks):
        t0 = time.monotonic()
        ecoli.run(1.0)
        total_ms = (time.monotonic() - t0) * 1000.0
        fw_ms = ecoli.framework_time * 1000.0
        proc_ms = ecoli.process_update_time * 1000.0
        breakdown.append((total_ms, fw_ms, proc_ms))
    return breakdown


def summarize_breakdown(breakdown, warmup):
    samples = breakdown[warmup:]
    if not samples:
        return
    n = len(samples)
    tot = sum(t[0] for t in samples) / n
    fw = sum(t[1] for t in samples) / n
    proc = sum(t[2] for t in samples) / n
    other = tot - fw - proc
    print(f'  per-tick wall (avg of {n} ticks, {warmup} warmup discarded):')
    print(f'    total       : {tot:7.2f} ms')
    print(f'    process     : {proc:7.2f} ms ({100*proc/tot:5.1f}%)')
    print(f'    framework   : {fw:7.2f} ms ({100*fw/tot:5.1f}%)')
    print(f'    other       : {other:7.2f} ms ({100*other/tot:5.1f}%)')


_PROCESS_CODE_MARKERS = (
    '/ecoli/processes/',
    '/wholecell/',
    '/reconstruction/',
    '/numba/',
    '/scipy/integrate/',
    '/scipy/optimize/',
    '/scipy/sparse/',
    'cvxpy/',
    'sympy/',
    '/swiglpk/',
)


def _is_process_code(file):
    if not isinstance(file, str):
        file = str(file)
    return any(m in file for m in _PROCESS_CODE_MARKERS)


def filter_framework_funcs(stats, top=25):
    """Print top framework functions by TOTAL time (= time in the
    function itself, excluding callees). This isolates framework
    overhead from time spent in processes' own bodies. Also filters
    out process / numerics / model libraries entirely."""
    ps = pstats.Stats(stats, stream=StringIO())
    framework_entries = []
    for (file, line, func), (ncalls, _ncalls, tottime, cumtime, callers) in ps.stats.items():
        if _is_process_code(file):
            continue
        framework_entries.append((tottime, cumtime, ncalls, file, line, func))
    framework_entries.sort(key=lambda e: -e[0])
    print(f'  top {top} framework functions by TOTAL (own) time '
          f'(excl. processes/numerics/model libs):')
    print(f'    {"tot_s":>8} {"cum_s":>8} {"calls":>8}  func')
    for tot, cum, ncalls, file, line, func in framework_entries[:top]:
        short_file = file.split('/site-packages/', 1)[-1].split('/code/', 1)[-1]
        print(f'    {tot:>8.3f} {cum:>8.3f} {ncalls:>8}  {func} ({short_file}:{line})')


def hot_framework_paths(stats, top=15):
    """Show framework functions ranked by total framework cost,
    grouped by file/module. Helps identify which subsystem (apply,
    realize, view, project, emit) dominates."""
    ps = pstats.Stats(stats, stream=StringIO())
    by_module = {}
    for (file, line, func), (ncalls, _ncalls, tottime, cumtime, callers) in ps.stats.items():
        if _is_process_code(file):
            continue
        if not isinstance(file, str):
            file = str(file)
        # Group by (last directory + filename)
        parts = file.replace('\\', '/').split('/')
        key = '/'.join(parts[-2:]) if len(parts) >= 2 else file
        by_module[key] = by_module.get(key, 0) + tottime
    ordered = sorted(by_module.items(), key=lambda kv: -kv[1])
    print(f'  framework total-time by file ({top} largest):')
    print(f'    {"tot_s":>8}  module/file')
    for k, v in ordered[:top]:
        print(f'    {v:>8.3f}  {k}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--ticks', type=int, default=30)
    ap.add_argument('--warmup', type=int, default=5)
    ap.add_argument('--sim-data-path',
                    default='out/two_generations_v2/variant_sim_data/0.cPickle')
    ap.add_argument('--profile-out', default='/tmp/profile_v2.prof')
    args = ap.parse_args()

    with chdir(ROOT):
        print(f'[profile] building composite...')
        t0 = time.monotonic()
        ecoli = build_composite(args.sim_data_path)
        print(f'  build time: {time.monotonic()-t0:.1f}s')
        print()

        print(f'[profile] running {args.ticks} ticks under cProfile...')
        pr = cProfile.Profile()
        pr.enable()
        breakdown = run_with_breakdown(ecoli, args.ticks)
        pr.disable()
        pr.dump_stats(args.profile_out)
        print(f'  dumped profile to {args.profile_out}')
        print()

        summarize_breakdown(breakdown, args.warmup)
        print()
        hot_framework_paths(args.profile_out, top=15)
        print()
        filter_framework_funcs(args.profile_out, top=30)


if __name__ == '__main__':
    main()
