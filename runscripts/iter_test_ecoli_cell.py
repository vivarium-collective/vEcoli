"""Fast iteration test for ``EcoliCellProcess``.

Validates that ``EcoliCellProcess`` produces byte-identical inner
state to the per-gen path (``EcoliSim._run_composite_inner`` with
``engine: composite`` and the same daughter JSON). If yes,
``EcoliCellProcess.__init__`` is a correct drop-in replacement —
we can then wire it into MP/Ray.

Direct state comparison (bypasses parquet emit complications): both
constructions feed the same daughter JSON + same per-gen seed; their
``cell_state['bulk']`` arrays must match byte-for-byte at t=0
(post-build, pre-tick) and again after running 10 ticks forward.

Iteration cycle: ~15s wall.
"""
import argparse
import os
import sys
import time

import numpy as np

from bigraph_schema import BASE_TYPES, Core
from process_bigraph.types.process import (
    register_types as register_pb_types)
from process_bigraph import Composite

from ecoli.composites.ecoli_cell_process import EcoliCellProcess
from ecoli.composites.ecoli_composite import build_ecoli_document
from ecoli.experiments.ecoli_master_sim import EcoliSim
from ecoli.library.bigraph_types import ECOLI_TYPES
from ecoli.library.json_state import get_state_from_file
from ecoli.library.sim_data import LoadSimData


DEFAULT_DAUGHTER = (
    'out/comparison_10s_16g_v2_local/daughter_states/'
    'variant=0/seed=0/generation=1/agent_id=0/daughter_state_0.json')


def _bulk_of(composite_or_cell):
    """Pull the cell's bulk count array from a composite or cell."""
    state = (composite_or_cell.inner_state
             if hasattr(composite_or_cell, 'inner_state')
             else composite_or_cell.state)
    agent_id = next(iter(state['agents']))
    return np.asarray(state['agents'][agent_id]['bulk']['count'])


def _build_via_per_gen(args, sim_config, daughter_cell, core):
    """Construct the cell via the per-gen path (build_ecoli_document
    directly + Composite)."""
    cfg = dict(sim_config)
    cfg['seed'] = 1
    cfg['agent_id'] = '00'
    cfg['sim_data_path'] = args.sim_data_path
    cfg['initial_state'] = daughter_cell
    cfg['initial_state_file'] = None
    lsd = LoadSimData(**cfg)
    state = build_ecoli_document(core, cfg, load_sim_data=lsd)
    composite = Composite(
        {'schema': {}, 'state': state, 'run_steps_on_init': True},
        core=core)
    composite.to_run = []
    return composite


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--config',
        default='configs/composites/lineage_2g_local.json')
    parser.add_argument(
        '--sim_data_path',
        default='out/comparison_10s_16g_v2_local/variant_sim_data/0.cPickle')
    parser.add_argument('--daughter', default=DEFAULT_DAUGHTER)
    parser.add_argument(
        '--n_ticks', type=int, default=10,
        help='ticks to advance both cells before comparing')
    args = parser.parse_args()

    if not os.path.isfile(args.daughter):
        print(f"daughter JSON missing: {args.daughter}", file=sys.stderr)
        sys.exit(2)

    # Resolve full sim_config the same way EcoliSim does.
    sim = EcoliSim.from_file(args.config)
    sim.config['sim_data_path'] = args.sim_data_path
    sim.config['lineage_seed'] = 0
    sim.config['seed'] = 1
    sim.config['agent_id'] = '00'
    sim.config['initial_state_file'] = None
    sim.processes = sim._retrieve_processes(
        sim.processes, sim.add_processes, sim.exclude_processes,
        sim.swap_processes)
    sim.topology = sim._retrieve_topology(
        sim.topology, sim.processes, sim.swap_processes,
        sim.log_updates)
    sim.process_configs = sim._retrieve_process_configs(
        sim.process_configs, sim.processes)
    sim_config = dict(sim.config)
    sim_config['agent_id'] = '00'

    # Daughter cell state (split bulk + unique).
    raw = get_state_from_file(path=args.daughter)
    daughter_cell = (raw['agents'][next(iter(raw['agents']))]
                     if 'agents' in raw else raw)

    print("=== iter_test_ecoli_cell (state parity) ===", flush=True)
    print(f"  daughter:   {args.daughter}", flush=True)
    print(f"  n_ticks:    {args.n_ticks}", flush=True)

    # Build via the two paths in turn. Use SEPARATE cores so neither
    # build accidentally mutates the other's state.
    print("\nBuilding via per-gen path...", flush=True)
    t0 = time.time()
    core_a = Core(BASE_TYPES)
    register_pb_types(core_a)
    core_a.register_types(ECOLI_TYPES)
    composite_a = _build_via_per_gen(args, sim_config, daughter_cell, core_a)
    print(f"  built in {time.time()-t0:.2f}s", flush=True)

    print("\nBuilding via EcoliCellProcess...", flush=True)
    t0 = time.time()
    core_b = Core(BASE_TYPES)
    register_pb_types(core_b)
    core_b.register_types(ECOLI_TYPES)
    cell_b = EcoliCellProcess(
        config={
            'lineage_seed': 0,
            'agent_id': '00',
            'sim_data_path': args.sim_data_path,
            'initial_state': daughter_cell,
            'sim_config': sim_config,
        },
        core=core_b,
    )
    print(f"  built in {time.time()-t0:.2f}s", flush=True)

    # Compare at t=0
    bulk_a0 = _bulk_of(composite_a)
    bulk_b0 = _bulk_of(cell_b)
    print(f"\n--- t=0 comparison ---", flush=True)
    print(f"  shapes: per-gen={bulk_a0.shape}, "
          f"EcoliCellProcess={bulk_b0.shape}", flush=True)
    diff0 = np.abs(bulk_a0.astype(np.int64) - bulk_b0.astype(np.int64))
    n_off0 = int((diff0 > 0).sum())
    print(f"  n_diff: {n_off0}/{len(diff0)}, "
          f"max_abs: {int(diff0.max())}, l1: {int(diff0.sum())}",
          flush=True)
    t0_ok = (n_off0 == 0)
    print(f"  t=0:    {'OK' if t0_ok else 'DIVERGE'}", flush=True)

    # Tick both forward N ticks
    print(f"\nTicking both forward {args.n_ticks} ticks...", flush=True)
    t0 = time.time()
    for _ in range(args.n_ticks):
        composite_a.run(1.0)
        cell_b.inner_composite.run(1.0)
    print(f"  ticked in {time.time()-t0:.2f}s", flush=True)

    bulk_aN = _bulk_of(composite_a)
    bulk_bN = _bulk_of(cell_b)
    print(f"\n--- t={args.n_ticks} comparison ---", flush=True)
    diffN = np.abs(bulk_aN.astype(np.int64) - bulk_bN.astype(np.int64))
    n_offN = int((diffN > 0).sum())
    print(f"  n_diff: {n_offN}/{len(diffN)}, "
          f"max_abs: {int(diffN.max())}, l1: {int(diffN.sum())}",
          flush=True)
    tN_ok = (n_offN == 0)
    print(f"  t={args.n_ticks}: {'OK' if tN_ok else 'DIVERGE'}", flush=True)

    if t0_ok and tN_ok:
        print(f"\nPASS: EcoliCellProcess matches per-gen path "
              f"byte-for-byte at t=0 and t={args.n_ticks}.")
        sys.exit(0)
    print(f"\nFAIL: divergence detected.")
    sys.exit(1)


if __name__ == '__main__':
    main()
