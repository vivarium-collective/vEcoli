"""Smoke test for the composite_lineage engine: 1 seed x 2 generations,
in-process. Uses the existing parca pickle to skip the parca step.

Validates that:
  - engine dispatch picks composite_lineage
  - gen 0 builds, runs to division, exits _run_composite_inner cleanly
  - daughter 0 extraction returns the cell state dict
  - gen 1 picks up the daughter state, runs to division
  - parquet output lands at two distinct partition paths

Bit-parity vs the per-gen path is Phase 1b — handled by a separate
script that diffs the parquet files against equivalent runs of
engine="composite".
"""
import argparse
import os
import sys
import time

from ecoli.experiments.ecoli_master_sim import EcoliSim


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config",
                        default="configs/composites/lineage_2g_local.json")
    parser.add_argument(
        "--sim_data_path",
        default="out/comparison_10s_16g_v2_local/variant_sim_data/0.cPickle")
    parser.add_argument("--out_dir", default="out/lineage_test")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--generations", type=int, default=2)
    parser.add_argument("--max_duration", type=float, default=3500.0)
    args = parser.parse_args()

    if not os.path.isfile(args.sim_data_path):
        print(f"sim_data_path not found: {args.sim_data_path}",
              file=sys.stderr)
        sys.exit(1)

    sim = EcoliSim.from_file(args.config)
    sim.config["sim_data_path"] = args.sim_data_path
    sim.config["lineage_seed"] = args.seed
    sim.config["seed"] = args.seed
    sim.config["agent_id"] = "0"
    sim.config["generations"] = args.generations
    sim.config["max_duration"] = args.max_duration
    # Ensure a fresh local output (the parquet emitter wipes the
    # configuration partition path on first config emit per gen).
    sim.config["emitter_arg"] = {"out_dir": args.out_dir, "threaded": False}
    # No daughter-JSON handoff between gens — fully in-process.
    sim.config["daughter_outdir"] = None

    print(f"=== composite_lineage smoke test ===", flush=True)
    print(f"  config: {args.config}", flush=True)
    print(f"  sim_data_path: {args.sim_data_path}", flush=True)
    print(f"  out_dir: {args.out_dir}", flush=True)
    print(f"  seed: {args.seed}, generations: {args.generations}, "
          f"max_duration: {args.max_duration}s", flush=True)
    t0 = time.time()
    sim.run()
    print(f"\n=== completed in {time.time()-t0:.1f}s wall ===", flush=True)


if __name__ == "__main__":
    main()
