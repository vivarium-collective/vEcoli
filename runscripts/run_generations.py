"""Run v2 composite through multiple generations via division.

Each generation:
  1. Build composite (gen 0) or load daughter bundle (gen >=1)
  2. Run until DivisionDetected (or until `max_duration`)
  3. Save one daughter as bundle for next generation

Success criterion: each generation actually divides and its daughter can
initialize the next generation.

Usage:
    python runscripts/run_generations.py [--generations 2]
                                         [--max-duration 5000]
                                         [--division-threshold 500]
                                         [--outdir out/generations]
"""
import argparse, copy, os, sys, time
from contextlib import chdir

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _build_v2(max_duration, divide, division_threshold):
    from ecoli.experiments.ecoli_master_sim import EcoliSim
    from ecoli.composites.ecoli_composite import build_composite_native
    from ecoli.library.bigraph_types import ECOLI_TYPES
    from process_bigraph import Composite
    from bigraph_schema import allocate_core

    sim = EcoliSim.from_file()
    sim.max_duration = int(max_duration)
    sim.emitter = 'null'
    sim.divide = divide
    if division_threshold is not None:
        sim.division_threshold = division_threshold
    sim.processes = sim._retrieve_processes(
        sim.processes, sim.add_processes, sim.exclude_processes, sim.swap_processes)
    sim.topology = sim._retrieve_topology(
        sim.topology, sim.processes, sim.swap_processes, sim.log_updates)
    sim.process_configs = sim._retrieve_process_configs(
        sim.process_configs, sim.processes)

    core = allocate_core()
    core.register_types(ECOLI_TYPES)
    state = build_composite_native(core, sim.config)
    return Composite({'schema': {}, 'state': state}, core=core), core


def _load_daughter_bundle(bundle_dir):
    from ecoli.library.bigraph_types import ECOLI_TYPES
    from process_bigraph import Composite
    from bigraph_schema import allocate_core
    core = allocate_core()
    core.register_types(ECOLI_TYPES)
    return Composite.load_bundle(bundle_dir, core=core), core


def _extract_daughter_composite(comp, daughter_id):
    """After DivisionDetected, the mother's composite.state has
    `agents: {daughter_id_0: {...}, daughter_id_1: {...}}`. Extract
    the specified daughter and wrap it as a fresh composite-ready
    state tree."""
    agents = comp.state['agents']
    if daughter_id not in agents:
        raise KeyError(f'daughter {daughter_id} not in {list(agents)}')
    daughter_state = agents[daughter_id]
    # Fresh `agents` store with just this daughter (using daughter_id so
    # subsequent division uses consistent IDs).
    return {'agents': {daughter_id: daughter_state}}


def _run_until_divide(comp, max_duration):
    from ecoli.processes.cell_division import DivisionDetected
    t0 = time.monotonic()
    divided = False
    try:
        comp.run(float(max_duration))
    except DivisionDetected:
        divided = True
    wall = time.monotonic() - t0
    return divided, wall


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--generations', type=int, default=2)
    p.add_argument('--max-duration', type=float, default=5000.0,
                   help='Per-gen sim-second cap (should comfortably exceed'
                        ' cell cycle ~2400s)')
    p.add_argument('--division-threshold', type=float, default=None)
    p.add_argument('--outdir', default='out/generations')
    args = p.parse_args()

    with chdir(ROOT):
        os.makedirs(args.outdir, exist_ok=True)

        print(f'\n=== Generation 0: building fresh ===', flush=True)
        t0 = time.monotonic()
        comp, _ = _build_v2(args.max_duration, divide=True,
                            division_threshold=args.division_threshold)
        print(f'[gen0] built in {time.monotonic()-t0:.1f}s', flush=True)

        gen_results = []

        for gen in range(args.generations):
            divided, wall = _run_until_divide(comp, args.max_duration)
            agents = comp.state.get('agents', {})
            agent_ids = list(agents.keys())
            print(f'[gen{gen}] ran {wall:.1f}s wall, divided={divided}, '
                  f'agents={agent_ids}', flush=True)
            gen_results.append({
                'gen': gen,
                'wall': wall,
                'divided': divided,
                'agents': agent_ids,
            })
            if not divided:
                print(f'[gen{gen}] did NOT divide within max-duration, stopping')
                break
            if len(agents) < 2:
                print(f'[gen{gen}] only {len(agents)} agent post-divide, stopping')
                break
            # Pick first daughter
            daughter_id = sorted(agents.keys())[0]
            bundle_dir = os.path.join(args.outdir, f'gen_{gen}_daughter_{daughter_id}')
            # Build a daughter-only state tree and wrap as a new composite
            daughter_state = _extract_daughter_composite(comp, daughter_id)
            # We need to rebuild the composite around ONLY this daughter.
            # Simplest: save mother's composite to bundle, then load, then
            # prune other daughters. For now: just save mother state and
            # treat the daughter as the seed for next gen via Composite.
            print(f'[gen{gen}] saving daughter {daughter_id} bundle...', flush=True)
            comp.save_bundle(bundle_dir)
            # For next gen, load and prune
            if gen + 1 < args.generations:
                comp, _ = _load_daughter_bundle(bundle_dir)
                # Prune to just one daughter so next gen runs one cell
                if 'agents' in comp.state and len(comp.state['agents']) > 1:
                    keep_id = sorted(comp.state['agents'].keys())[0]
                    for aid in list(comp.state['agents']):
                        if aid != keep_id:
                            del comp.state['agents'][aid]
                    print(f'[gen{gen+1}] loaded daughter {keep_id}', flush=True)

        print('\n=== Summary ===')
        for r in gen_results:
            print(r)


if __name__ == '__main__':
    main()
