"""Pre-run v1 and v2 to a checkpoint, save both, then iterate the division test.

Usage:
    # Step 1: generate checkpoint (slow, one-time)
    python runscripts/checkpoint_divide.py save --checkpoint-time 1800 \
        --checkpoint-dir out/divide_checkpoint

    # Step 2: load checkpoint + run to divide (fast, repeat)
    python runscripts/checkpoint_divide.py run --checkpoint-dir out/divide_checkpoint \
        [--division-threshold 500] [--extra-duration 600]

Layout of `--checkpoint-dir`:
    v2_bundle/            # v2 composite bundle (document.json + arrays/)
    v1_state.json         # v1 initial_state file (load via sim.initial_state_file)
    meta.json             # duration used, seed, config snapshot

Strategy: run v1 and v2 fresh in parallel to `--checkpoint-time` simulated
seconds. Save v2 via its bundle format, save v1 via its state-dump mechanism.
Then the `run` command loads each back and runs until division or the
extra-duration cap, comparing final bulk.
"""
import argparse, json, os, pickle, subprocess, sys, tempfile, time
from contextlib import chdir

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _build_v2(duration, divide=False, division_threshold=None):
    from ecoli.experiments.ecoli_master_sim import EcoliSim
    from ecoli.composites.ecoli_composite import build_composite_native
    from ecoli.library.bigraph_types import ECOLI_TYPES
    from process_bigraph import Composite
    from bigraph_schema import allocate_core

    sim = EcoliSim.from_file()
    sim.max_duration = int(duration)
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
    composite = Composite({'schema': {}, 'state': state}, core=core)
    return composite, core


def _load_v2_bundle(bundle_dir):
    from ecoli.library.bigraph_types import ECOLI_TYPES
    from process_bigraph import Composite
    from bigraph_schema import allocate_core
    core = allocate_core()
    core.register_types(ECOLI_TYPES)
    return Composite.load_bundle(bundle_dir, core=core), core


def _get_cell(composite):
    if 'agents' in composite.state:
        agents = composite.state['agents']
        return agents[next(iter(agents))]
    return composite.state


def _dump_v1_state(sim, out_path):
    """Extract v1 state as JSON for later --initial_state_file."""
    from ecoli.library.schema import not_a_process
    state = sim.ecoli_experiment.state.get_value(condition=not_a_process)
    # Convert to JSON-safe form
    from vivarium.core.serialize import serialize_value
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w') as f:
        json.dump(serialize_value(state), f)


def cmd_save(args):
    with chdir(ROOT):
        os.makedirs(args.checkpoint_dir, exist_ok=True)
        meta = {
            'checkpoint_time': args.checkpoint_time,
            'seed': args.seed,
        }
        with open(os.path.join(args.checkpoint_dir, 'meta.json'), 'w') as f:
            json.dump(meta, f)

        # --- v2 ---
        print(f"[v2] building + running {args.checkpoint_time}s ...", flush=True)
        t0 = time.monotonic()
        comp, _ = _build_v2(args.checkpoint_time, divide=False)
        comp.run(float(args.checkpoint_time))
        print(f"[v2] sim done in {time.monotonic()-t0:.1f}s wall", flush=True)

        v2_bundle_dir = os.path.join(args.checkpoint_dir, 'v2_bundle')
        comp.save_bundle(v2_bundle_dir)
        print(f"[v2] saved bundle → {v2_bundle_dir}/", flush=True)

        # --- v1 ---
        print(f"[v1] building + running {args.checkpoint_time}s ...", flush=True)
        t0 = time.monotonic()
        from ecoli.experiments.ecoli_master_sim import EcoliSim
        sim = EcoliSim.from_file()
        sim.max_duration = int(args.checkpoint_time)
        sim.emitter = 'null'
        sim.divide = False
        sim.build_ecoli()
        sim.run()
        print(f"[v1] sim done in {time.monotonic()-t0:.1f}s wall", flush=True)

        v1_state_path = os.path.join(args.checkpoint_dir, 'v1_state.json')
        _dump_v1_state(sim, v1_state_path)
        print(f"[v1] saved state → {v1_state_path}", flush=True)

        print(f"\nCheckpoint saved to {args.checkpoint_dir}/", flush=True)


def cmd_run(args):
    """Load both checkpoints, run extra duration with division enabled."""
    with chdir(ROOT):
        # --- v2 bundle load + run ---
        import numpy as np
        from ecoli.processes.cell_division import DivisionDetected

        v2_bundle_dir = os.path.join(args.checkpoint_dir, 'v2_bundle')
        print(f"[v2] loading bundle from {v2_bundle_dir} ...", flush=True)
        t0 = time.monotonic()
        comp, _ = _load_v2_bundle(v2_bundle_dir)
        load_t = time.monotonic() - t0
        print(f"[v2] loaded in {load_t:.1f}s", flush=True)

        # Override division_threshold if provided (simplest: not currently
        # wired through bundle load). For now just run.
        cell = _get_cell(comp)
        v2_init = cell['bulk']['count'].copy()

        t0 = time.monotonic()
        try:
            comp.run(float(args.extra_duration))
        except DivisionDetected:
            pass
        run_t = time.monotonic() - t0

        v2_divided = 'agents' in comp.state and len(comp.state['agents']) > 1
        cell = _get_cell(comp)
        v2_final = cell['bulk']['count'].copy()
        print(f"[v2] ran {args.extra_duration}s in {run_t:.1f}s wall, divided={v2_divided}", flush=True)

        # --- v1 run from checkpoint (separate subprocess) ---
        v1_state = os.path.join(args.checkpoint_dir, 'v1_state.json')
        if args.skip_v1:
            print("[v1] skipped", flush=True)
            return

        print(f"[v1] running from {v1_state} for {args.extra_duration}s ...", flush=True)
        t0 = time.monotonic()
        v1_runtime, v1_init, v1_final, v1_divided = _run_v1_from_state(
            v1_state, args.extra_duration, args.division_threshold)
        print(f"[v1] done in {time.monotonic()-t0:.1f}s wall, divided={v1_divided}", flush=True)

        # Compare
        changed_v1 = (v1_init != v1_final).sum()
        changed_v2 = (v2_init != v2_final).sum()
        both = (v1_init != v1_final) & (v2_init != v2_final)
        n_both = both.sum()
        print(f"\nv1 changed: {changed_v1}, v2 changed: {changed_v2}, both: {n_both}")
        if n_both > 1:
            d1 = (v1_final[both] - v1_init[both]).astype(float)
            d2 = (v2_final[both] - v2_init[both]).astype(float)
            corr = np.corrcoef(d1, d2)[0, 1]
            print(f"correlation: {corr:.6f}")


def _run_v1_from_state(state_json, duration, division_threshold):
    """Run v1 in subprocess starting from state_json, return results."""
    script = f"""
import pickle, sys, time, json
sys.path.insert(0, '.')
from ecoli.experiments.ecoli_master_sim import EcoliSim
from ecoli.library.schema import not_a_process
from ecoli.processes.cell_division import DivisionDetected

sim = EcoliSim.from_file()
sim.max_duration = {duration}
sim.emitter = 'null'
sim.divide = True
sim.division_threshold = {division_threshold!r}
sim.initial_state_file = None  # we set initial_state directly below
sim.build_ecoli()

# Inject saved state
with open({state_json!r}) as f:
    saved = json.load(f)
# saved is {{'bulk': {{...}}, 'listeners': {{...}}, ...}} — merge into initial
from vivarium.library.dict_utils import deep_merge
sim.generated_initial_state = deep_merge(sim.generated_initial_state, saved)

t0 = time.monotonic()
try:
    sim.run()
except DivisionDetected:
    pass
runtime = time.monotonic() - t0

state = sim.ecoli_experiment.state.get_value(condition=not_a_process)
if 'agents' in state and state['agents']:
    agent = state['agents'][next(iter(state['agents']))]
    final = agent['bulk']['count'].copy() if 'bulk' in agent else None
    divided = len(state['agents']) > 1
else:
    final = state['bulk']['count'].copy() if 'bulk' in state else None
    divided = False

# initial comes from the loaded state — for now, just use sim.generated_initial_state
init_bulk = saved.get('bulk', {{}}).get('count', [])
import numpy as np
if isinstance(init_bulk, list):
    init_bulk = np.array(init_bulk)

with open(sys.argv[1], 'wb') as f:
    pickle.dump((runtime, init_bulk, final, divided), f)
"""
    with tempfile.NamedTemporaryFile(suffix='.pkl', delete=False) as tmp:
        tmp_path = tmp.name
    proc = subprocess.Popen(
        [sys.executable, '-u', '-c', script, tmp_path],
        stdout=sys.stdout, stderr=subprocess.STDOUT)
    proc.wait()
    if proc.returncode != 0:
        raise RuntimeError(f"v1 from-state failed rc={proc.returncode}")
    with open(tmp_path, 'rb') as f:
        return pickle.load(f)


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest='cmd', required=True)

    p_save = sub.add_parser('save')
    p_save.add_argument('--checkpoint-time', type=float, default=1800.0,
                        help='Simulated seconds before checkpoint (slow, one-time)')
    p_save.add_argument('--checkpoint-dir', default='out/divide_checkpoint')
    p_save.add_argument('--seed', type=int, default=0)

    p_run = sub.add_parser('run')
    p_run.add_argument('--checkpoint-dir', default='out/divide_checkpoint')
    p_run.add_argument('--extra-duration', type=float, default=600.0,
                       help='Additional simulated seconds after checkpoint')
    p_run.add_argument('--division-threshold', default=None,
                       help='Override division threshold for the run')
    p_run.add_argument('--skip-v1', action='store_true',
                       help='Only run v2 side')

    args = p.parse_args()
    if args.cmd == 'save':
        cmd_save(args)
    else:
        cmd_run(args)


if __name__ == '__main__':
    main()
