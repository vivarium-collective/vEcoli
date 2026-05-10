"""Run v1 (vivarium) for one seed across 2 generations: gen 0 to
divide, save daughter state, then gen 1 starting from daughter for
just a few ticks (enough for parity comparison vs v2).

Usage:
    uv run --no-sync python runscripts/iter_test_v1_two_gen.py 12

Env overrides:
    V1_OUT_DIR        override the output directory (default
                      ``out/iter_test_v1_seed{N}``).
    V1_SIM_DATA_PATH  override the sim_data file (default the local
                      variant_sim_data path). Use this to run v1
                      against an exact-match sim_data (e.g. v1's
                      production parca output) when comparing to v2.
    OMP_NUM_THREADS / MKL_NUM_THREADS / OPENBLAS_NUM_THREADS / etc.
                      v1 normally runs multi-threaded BLAS; export
                      these = 1 BEFORE invoking python to match the
                      single-threaded v2-MP runner exactly.

Outputs:
    {V1_OUT_DIR}/
      EXPERIMENT_ID_PLACEHOLDER/history/.../generation=1/...     gen 0 mother
      daughter_states/daughter_state_0.json                       saved at divide
      gen2/EXPERIMENT_ID_PLACEHOLDER/history/.../generation=2/... gen 1 daughter, first 10 ticks
"""
import os
import sys
import time

if __name__ == "__main__":
    seed = int(sys.argv[1]) if len(sys.argv) > 1 else 12
    base_out = os.path.abspath(
        os.environ.get("V1_OUT_DIR") or f"out/iter_test_v1_seed{seed}")
    sim_data_path = os.path.abspath(
        os.environ.get("V1_SIM_DATA_PATH")
        or "out/comparison_10s_16g_v2_local/variant_sim_data/0.cPickle")
    if not os.path.isfile(sim_data_path):
        sys.exit(f"sim_data missing: {sim_data_path}")

    from ecoli.experiments.ecoli_master_sim import EcoliSim

    # ---------- gen 0 ----------
    out_dir_gen0 = base_out
    os.makedirs(out_dir_gen0, exist_ok=True)
    daughter_outdir = os.path.join(out_dir_gen0, "daughter_states")
    os.makedirs(daughter_outdir, exist_ok=True)

    sim = EcoliSim.from_file(
        os.path.abspath("configs/comparison_10s_16g_v1.json"))
    sim.config["engine"] = "vivarium"
    sim.config["lineage_seed"] = seed
    sim.config["seed"] = seed
    sim.config["agent_id"] = "0"
    sim.config["max_duration"] = 3000.0
    sim.config["sim_data_path"] = sim_data_path
    sim.config["emitter_arg"] = {
        "out_dir": out_dir_gen0, "threaded": False}
    sim.config["daughter_outdir"] = daughter_outdir

    print(f"[v1 gen 0] seed={seed} out={out_dir_gen0}", flush=True)
    t0 = time.time()
    sim.build_ecoli()
    try:
        sim.run()
    except SystemExit:
        # ecoli_master_sim.update_experiment calls sys.exit() after
        # handling DivisionDetected (line ~804). Catch it here so
        # the script continues to gen 1 instead of dying.
        pass
    finally:
        try:
            sim.ecoli_experiment.emitter.finalize()
        except Exception:
            pass
    print(f"[v1 gen 0] done in {time.time()-t0:.1f}s wall.", flush=True)

    # Confirm daughter saved.
    daughter_path = os.path.join(daughter_outdir, "daughter_state_0.json")
    if not os.path.isfile(daughter_path):
        sys.exit(f"daughter_state_0.json missing: {daughter_path}")
    print(f"  daughter saved: {daughter_path}", flush=True)

    # ---------- gen 1 (daughter): only 10 ticks, enough for parity ----------
    out_dir_gen1 = os.path.join(base_out, "gen2")
    os.makedirs(out_dir_gen1, exist_ok=True)

    sim2 = EcoliSim.from_file(
        os.path.abspath("configs/comparison_10s_16g_v1.json"))
    sim2.config["engine"] = "vivarium"
    sim2.config["lineage_seed"] = seed
    # gen 1 seed: same convention as v2 composite_lineage (seed_library
    # increments per gen). Use seed+1 as the gen-1 RNG seed.
    sim2.config["seed"] = seed + 1
    sim2.config["agent_id"] = "00"
    sim2.config["max_duration"] = 10.0  # short — just enough for first ticks
    sim2.config["sim_data_path"] = sim_data_path
    sim2.config["initial_state_file"] = daughter_path
    sim2.config["emitter_arg"] = {
        "out_dir": out_dir_gen1, "threaded": False}
    sim2.config["daughter_outdir"] = None  # we won't save its daughters

    print(f"\n[v1 gen 1] seed={seed+1} agent_id=00 from {daughter_path}",
          flush=True)
    print(f"  out={out_dir_gen1}, max_duration=10s", flush=True)
    t0 = time.time()
    sim2.build_ecoli()
    # v1 raises both:
    #   - SystemExit (after handling DivisionDetected)
    #   - TimeLimitError (when max_duration reached without divide,
    #     and config["fail_at_max_duration"] is True — which is
    #     default in comparison_10s_16g.json)
    # For our 10-second daughter test we WILL hit max_duration without
    # divide; catch both as "normal completion."
    from ecoli.experiments.ecoli_master_sim import TimeLimitError
    try:
        sim2.run()
    except (SystemExit, TimeLimitError):
        pass
    finally:
        try:
            sim2.ecoli_experiment.emitter.finalize()
        except Exception:
            pass
    print(f"[v1 gen 1] done in {time.time()-t0:.1f}s wall.", flush=True)
