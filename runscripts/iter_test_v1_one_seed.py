"""Run v1 (vivarium engine) locally for a single seed × gen 0, with
the same sim_data + max_duration as the v2 MP test. Lets us see
whether v1 ALSO halts on this seed (model property) or divides
(then v2's halt is a parity regression).

Usage:
    uv run --no-sync python runscripts/iter_test_v1_one_seed.py 12
"""
import os
import sys
import time

if __name__ == "__main__":
    seed = int(sys.argv[1]) if len(sys.argv) > 1 else 12
    out_dir = os.path.abspath(f"out/iter_test_v1_seed{seed}")
    sim_data_path = os.path.abspath(
        "out/comparison_10s_16g_v2_local/variant_sim_data/0.cPickle")
    if not os.path.isfile(sim_data_path):
        sys.exit(f"sim_data missing: {sim_data_path}")
    os.makedirs(out_dir, exist_ok=True)

    from ecoli.experiments.ecoli_master_sim import EcoliSim

    sim = EcoliSim.from_file(
        os.path.abspath("configs/comparison_10s_16g_v1.json"))
    # Ensure v1 engine.
    sim.config["engine"] = "vivarium"
    sim.config["lineage_seed"] = seed
    sim.config["seed"] = seed
    sim.config["agent_id"] = "0"
    sim.config["max_duration"] = 3000.0
    sim.config["sim_data_path"] = sim_data_path
    sim.config["emitter_arg"] = {"out_dir": out_dir, "threaded": False}
    # Set a real daughter_outdir — None crashes on division because
    # ecoli_master_sim's DivisionDetected handler calls
    # cloud_path_join(self.daughter_outdir, ...). The crash happens
    # BEFORE emitter.finalize(), so any buffered post-2800 emit data
    # is silently lost. With a real path the run completes cleanly
    # and finalize flushes the full t=0..t_divide range.
    daughter_outdir = os.path.join(out_dir, "daughter_states")
    os.makedirs(daughter_outdir, exist_ok=True)
    sim.config["daughter_outdir"] = daughter_outdir

    print(f"[v1 single-seed] seed={seed} out={out_dir}", flush=True)
    print(f"  sim_data: {sim_data_path}", flush=True)
    t0 = time.time()
    sim.build_ecoli()  # v1 path needs this
    try:
        sim.run()
    finally:
        # Belt-and-suspenders: ensure parquet emitter flushes its
        # buffer even if sim.run() raises. parquet_emitter.finalize is
        # idempotent so calling it twice is fine.
        try:
            sim.ecoli_experiment.emitter.finalize()
        except Exception as e:
            print(f"  warn: emitter.finalize raised: {e}", flush=True)
    print(f"\n[v1 single-seed] done in {time.time()-t0:.1f}s wall.",
          flush=True)
