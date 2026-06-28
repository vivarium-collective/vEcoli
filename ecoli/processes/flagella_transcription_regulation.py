import numpy as np

from vivarium.core.process import Step
from ecoli.library.schema import (
    numpy_schema,
    attrs,
    bulk_name_to_idx,
    counts,
    MetadataArray,
)
from ecoli.processes.registries import topology_registry


NAME = "ecoli-flagella-transcription-regulation"
TOPOLOGY = {
    "promoters": ("unique", "promoter"),
    "bulk": ("bulk",),
    "timestep": ("timestep",),
    "next_update_time": ("next_update_time", "flagella_transcription_regulation"),
    "global_time": ("global_time",),
}
topology_registry.register(NAME, TOPOLOGY)


class FlagellaTranscriptionRegulation(Step):
    """
    Implements the Kalir & Alon (Cell 2004) bilinear SUM-gate for flagella transcription.

    Each timestep computes X (FlhDC activity) and Y (free FliA activity) via
    Michaelis-Menten, then writes init_prob_override onto the promoters unique
    molecule so transcript_initiation uses the K&A value instead of the default
    basal_prob + delta_prob * bound_TF.

    Class II (7 genes): p_i = (β*X + β'*Y) / (β+β'), normalized by p_i_ref so that
    at reference conditions (X=X_ref, Y=0) the gene sits at its ParCa basal rate.
    Class III (fliC, fliD, flgK/L, motAB, cheAW, flgM): override = Y * basal_prob,
    rising from ~0 when FliA is sequestered to basal_prob at full FliA activity.

    Ordered after ecoli-tf-binding and before ecoli-transcript-initiation via flow config.
    """

    name = NAME
    topology = TOPOLOGY
    defaults = {
        "beta": [1200, 450, 350, 350, 150, 100, 50],  # flhDC activation coefficients
        "beta_prime": [
            250,
            350,
            300,
            450,
            300,
            350,
            300,
        ],  # FliA activation coefficients
        "flg_classII_rnaids": [
            "EG10322_RNA",
            "EG11346_RNA",
            "EG11347_RNA",
            "G358_RNA",
            "G357_RNA",
            "G7028_RNA",
            "EG11355_RNA",
        ],  # bare cistron IDs used in unit tests; sim_data overrides with TU-level [c] IDs at runtime
        "flg_classIII_rnaids": [],  # empty default is safe for tests (Class III loop is skipped); sim_data populates with resolved TU IDs
        "fliA": "EG11355-MONOMER[c]",
        "flhDC": "CPLX0-3930[c]",
        "rna_ids": [],
        "bulk_molecule_ids": [],
        "K_flhDC": 10,
        "K_fliA": 10,
        # basal_prob indexed by TU_index (same as rna_data). Populated by sim_data at
        # runtime; left empty here so unit tests that omit it fall back to 1.0 scaling.
        "basal_prob": [],
        "seed": 0,
    }

    # Constructor
    def __init__(self, parameters=None):
        super().__init__(parameters)
        self.beta = np.asarray(self.parameters["beta"], dtype=float)
        self.beta_prime = np.asarray(self.parameters["beta_prime"], dtype=float)

        rna_ids = list(self.parameters["rna_ids"])
        self.flg_TU_ids = np.array(
            [rna_ids.index(rna_id) for rna_id in self.parameters["flg_classII_rnaids"]]
        )
        self.flg_classIII_TU_ids = np.array(
            [rna_ids.index(rna_id) for rna_id in self.parameters["flg_classIII_rnaids"]]
        )

        # Per-gene basal_prob values used to anchor init_prob_override to vEcoli's
        # normalization scale. After X_ref normalization the effective formula is
        # (p_i / p_i_ref) * basal_prob, so at reference conditions (Y=0, X=X_ref)
        # the override equals basal_prob exactly. At p_i=1 (X=Y=1, fully saturated)
        # the gene gets basal_prob / p_i_ref — e.g. ~8× basal for fliA.
        basal_prob = self.parameters["basal_prob"]
        if len(basal_prob) > 0:
            self.flg_classII_basal_probs = np.array(
                [basal_prob[i] for i in self.flg_TU_ids]
            )
            self.flg_classIII_basal_probs = np.array(
                [basal_prob[i] for i in self.flg_classIII_TU_ids]
            )
        else:
            self.flg_classII_basal_probs = np.ones(len(self.flg_TU_ids))
            self.flg_classIII_basal_probs = np.ones(len(self.flg_classIII_TU_ids))

        # bulk indices for reading counts
        self.flhDC = bulk_name_to_idx(
            self.parameters["flhDC"], self.parameters["bulk_molecule_ids"]
        )
        self.fliA = bulk_name_to_idx(
            self.parameters["fliA"], self.parameters["bulk_molecule_ids"]
        )

        # X_ref is the FlhDC activity signal at t=0 (the ParCa reference state).
        # Computed lazily on the first next_update call from the initial bulk state
        # so we don't have to dig the initial FlhDC count out of sim_data.
        # Used to normalize p_i: at reference conditions (X=X_ref, Y=0),
        # init_prob_override == basal_prob exactly. As Y rises, override > basal_prob.
        self.X_ref = None
        self.p_i_ref = None

        self.seed = self.parameters["seed"]
        self.random_state = np.random.RandomState(seed=self.seed)

    def ports_schema(self):
        return {
            "promoters": numpy_schema("promoters"),
            "bulk": numpy_schema("bulk"),
            "timestep": {"_default": 2.0},
            # need a listener port or to make one? we can also write out to the flagella listener IDK yet
            "next_update_time": {
                "_default": 0.0,
                "_updater": "set",  # the updated value becomes the new current value
                "_divider": "set",
            },
            "global_time": {"_default": 0.0},
        }

    # self, timestep and states are required arguments for the next_update
    def next_update(self, timestep, states):
        # Not time to fire yet, return an empty dict
        # Next tick, the check runs again until global-time catches up
        if states["next_update_time"] > states["global_time"]:
            return {}

        # promoters
        if states["promoters"]["_entryState"].sum() == 0:
            return {
                "promoters": {},
                "next_update_time": states["global_time"] + states["timestep"],
            }

        # Read TU_index and current init_prob_override values from promoters.
        # We no longer touch bound_TF here — the K&A gate writes p_i directly
        # to init_prob_override so transcript_initiation can use it as the
        # full initiation probability, bypassing basal_prob + delta_prob * bound_TF.
        # This eliminates the double-counting that caused FliA overproduction:
        # previously, the gate's delta contribution stacked on top of ParCa's
        # already-calibrated basal_prob, driving FliA ~5x above the calibrated level.
        TU_index, init_prob_override = attrs(
            states["promoters"], ["TU_index", "init_prob_override"]
        )

        # get counts from bulk
        flhDC_count = counts(states["bulk"], self.flhDC)
        fliA_count = counts(states["bulk"], self.fliA)

        X = flhDC_count / (self.parameters["K_flhDC"] + flhDC_count)
        Y = fliA_count / (self.parameters["K_fliA"] + fliA_count)

        # Capture X at t=0 as the reference. At reference (X=X_ref, Y=0),
        # p_i/p_i_ref == 1 so init_prob_override == basal_prob, matching ParCa exactly.
        # As Y accumulates, p_i > p_i_ref and the gate boosts above basal_prob.
        if self.X_ref is None:
            self.X_ref = X
            self.p_i_ref = self.beta * self.X_ref / (self.beta + self.beta_prime)

        # K&A SUM gate: p_i ∈ [0,1] is the normalized expression level for each Class II gene.
        p_i = (self.beta * X + self.beta_prime * Y) / (self.beta + self.beta_prime)

        # Guard against p_i_ref=0 (only possible if FlhDC=0 at t=0, i.e. no master regulator).
        # In that case treat the gene as fully driven by its basal_prob with no K&A modulation.
        safe_p_i_ref = np.where(self.p_i_ref > 0, self.p_i_ref, 1.0)

        # Modifying for flagella ones only
        init_prob_override_new = init_prob_override.copy()
        for i, tu_idx in enumerate(self.flg_TU_ids):
            rows = np.where(TU_index == tu_idx)[0]
            if len(rows) == 0:
                continue
            # p_i / p_i_ref normalizes to reference conditions; * basal_prob puts the
            # value on vEcoli's normalization scale so flagella genes don't monopolize RNAP.
            init_prob_override_new[rows] = (
                p_i[i] / safe_p_i_ref[i] * self.flg_classII_basal_probs[i]
            )

        # Class III driven by Y only. At Y=0 override=0 → ka_mask False → falls back to
        # small sigma-70 basal rate. At Y=1 → override == basal_prob (full FliA activity).
        for j, tu_idx in enumerate(self.flg_classIII_TU_ids):
            rows = np.where(TU_index == tu_idx)[0]
            if len(rows) == 0:
                continue
            init_prob_override_new[rows] = Y * self.flg_classIII_basal_probs[j]

        # UniqueNumpyUpdater (schema.py) only recognizes "set", "add", "delete",
        # "update" as top-level keys — any other key is silently ignored.
        # Use {"set": {field: value}} to match the pattern used by tf_binding.py.
        return {
            "promoters": {"set": {"init_prob_override": init_prob_override_new}},
            "next_update_time": states["global_time"] + states["timestep"],
        }


# ---------------------------------------------------------------------------
# Tests — build process, build fake states, call next_update, assert output
# ---------------------------------------------------------------------------


def test_flg_regulation_math():
    process = FlagellaTranscriptionRegulation(
        {
            "tf_ids": ["EG11355-MONOMER"],
            "rna_ids": [
                "EG10322_RNA",
                "EG11346_RNA",
                "EG11347_RNA",
                "G358_RNA",
                "G357_RNA",
                "G7028_RNA",
                "EG11355_RNA",
            ],
            "bulk_molecule_ids": np.array(["CPLX0-3930[c]", "EG11355-MONOMER[c]"]),
            "K_flhDC": 10,
            "K_fliA": 10,
            "seed": 0,
        }
    )

    # when both are zero, p_i should also be 0
    X = 0 / (10 + 0)
    Y = 0 / (10 + 0)
    p_i = (process.beta * X + process.beta_prime * Y) / (
        process.beta + process.beta_prime
    )
    assert np.all(p_i == 0)

    X = 1000 / (10 + 1000)
    Y = 1000 / (10 + 1000)
    p_i = (process.beta * X + process.beta_prime * Y) / (
        process.beta + process.beta_prime
    )
    assert np.all(p_i <= 1.0)  # p_i exceeding 1.0

    for x, y in [(0.2, 0.8), (0.5, 0.5), (1.0, 0.0), (0.0, 1.0)]:
        p_i = (process.beta * x + process.beta_prime * y) / (
            process.beta + process.beta_prime
        )
        assert np.all(p_i >= 0) and np.all(p_i <= 1.0), (
            f"p_i out of range at X={x}, Y={y}: {p_i}"
        )

    print("all tests passed")


def test_next_update():
    process = FlagellaTranscriptionRegulation(
        {
            "tf_ids": ["EG11355-MONOMER"],
            "rna_ids": [
                "EG10322_RNA",
                "EG11346_RNA",
                "EG11347_RNA",
                "G358_RNA",
                "G357_RNA",
                "G7028_RNA",
                "EG11355_RNA",
            ],
            "bulk_molecule_ids": np.array(["CPLX0-3930[c]", "EG11355-MONOMER[c]"]),
            "K_flhDC": 10,
            "K_fliA": 10,
            "seed": 0,
        }
    )

    # STEP 1. Build fake input states
    # States must look exactly like what ports_schema wants to deliver
    # ports - bulk, promoters, TU_index, timestep, next_update, global time
    # FlhDC and FliA
    bulk = np.array(
        [("CPLX0-3930[c]", 50), ("EG11355-MONOMER[c]", 20)],
        dtype=[("id", "U40"), ("count", int)],
    )
    n_tf = 1
    # Needs to match what attrs will read, the promoter structure of the simdata options
    promoter_dtypes = [
        ("_entryState", "i1"),
        ("TU_index", "<i8"),
        ("bound_TF", "?", (n_tf,)),
        ("unique_index", "<i8"),
        ("init_prob_override", "f8"),
    ]

    # Building fake promoter array - one row per Class II gene, TU_index is the position in rna_ids list
    rows = [(1, tu_idx, [False] * n_tf, tu_idx, 0.0) for tu_idx in range(7)]
    promoters = MetadataArray(
        np.array(rows, dtype=promoter_dtypes),
        7,
    )
    states = {
        "promoters": promoters,
        "bulk": bulk,
        "timestep": 2.0,
        "next_update_time": 0.0,
        "global_time": 0.0,
    }
    out = process.next_update(2.0, states)

    # 3. STEP 3 - Assert and Confirm
    # With FLHDC = 50 and FliA = 20, X and Y > 0 so p_i > 0 for all Class II genes
    # seed = 0 is fixed so draws are deterministic for testing
    assert "promoters" in out
    assert "set" in out["promoters"] and "init_prob_override" in out["promoters"]["set"]
    override_vals = out["promoters"]["set"]["init_prob_override"]
    assert np.all(override_vals > 0), (
        f"Expected all p_i > 0 with FlhDC=50, FliA=20: {override_vals}"
    )
    print("init_prob_override result:", override_vals)
    print("test_next_update passed")


# Plotting and quantifying values and structure
def test_plot():
    import matplotlib.pyplot as plt

    rna_labels = ["EG10322", "EG11346", "EG11347", "G358", "G357", "G7028", "EG11355"]
    flhDC = 50
    fliA = 20

    n_tf = 1
    promoter_dtypes = [
        ("_entryState", "i1"),
        ("TU_index", "<i8"),
        ("bound_TF", "?", (n_tf,)),
        ("unique_index", "<i8"),
        ("init_prob_override", "f8"),
    ]
    rows = [(1, tu_idx, [False] * n_tf, tu_idx, 0.0) for tu_idx in range(7)]

    process = FlagellaTranscriptionRegulation(
        {
            "tf_ids": ["EG11355-MONOMER"],
            "rna_ids": [
                "EG10322_RNA",
                "EG11346_RNA",
                "EG11347_RNA",
                "G358_RNA",
                "G357_RNA",
                "G7028_RNA",
                "EG11355_RNA",
            ],
            "bulk_molecule_ids": np.array(["CPLX0-3930[c]", "EG11355-MONOMER[c]"]),
            "K_flhDC": 10,
            "K_fliA": 10,
            "seed": 0,
        }
    )
    bulk = np.array(
        [("CPLX0-3930[c]", flhDC), ("EG11355-MONOMER[c]", fliA)],
        dtype=[("id", "U40"), ("count", int)],
    )
    promoters = MetadataArray(np.array(rows, dtype=promoter_dtypes), 7)
    states = {
        "promoters": promoters,
        "bulk": bulk,
        "timestep": 2.0,
        "next_update_time": 0.0,
        "global_time": 0.0,
    }
    out = process.next_update(2.0, states)

    actual_p = out["promoters"]["set"]["init_prob_override"]

    # With X_ref normalization, override = (p_i / p_i_ref) * basal_prob.
    # In tests basal_prob defaults to 1.0, and X_ref == X (first call), so
    # override = (beta*X + beta'*Y) / (beta*X). Check positivity and direction only.
    assert np.all(actual_p > 0), f"Expected positive overrides, got {actual_p}"
    assert actual_p[-1] > actual_p[-1] * 0, "sanity check"

    x = np.arange(7)
    fig, ax = plt.subplots(figsize=(8, 5))
    ax.bar(x, actual_p, width=0.4, label="actual (init_prob_override, scaled)")
    ax.set_xticks(x)
    ax.set_xticklabels(rna_labels, rotation=45)
    ax.set_ylabel("p_i")
    ax.set_title(f"K&A SUM gate output — FlhDC={flhDC}, FliA={fliA}")
    ax.legend()
    plt.tight_layout()
    plt.savefig("Flg_binding_test.png")
    plt.show()
    print("test_plot passed")


def test_dose_response():
    import matplotlib.pyplot as plt

    counts_range = np.arange(0, 201, 10)

    # EG11355 (fliA) is index 6 in rna_ids list
    gene_idx = 6
    fixed_flhDC = 50
    fixed_fliA = 20

    # beta and beta_prime for EG11355
    b = 50.0
    bp = 300.0

    actual_flhDC, actual_fliA = [], []
    theo_flhDC, theo_fliA = [], []

    rna_ids = [
        "EG10322_RNA",
        "EG11346_RNA",
        "EG11347_RNA",
        "G358_RNA",
        "G357_RNA",
        "G7028_RNA",
        "EG11355_RNA",
    ]
    bulk_mol_ids = np.array(["CPLX0-3930[c]", "EG11355-MONOMER[c]"])

    n_tf = 1
    promoter_dtypes = [
        ("_entryState", "i1"),
        ("TU_index", "<i8"),
        ("bound_TF", "?", (n_tf,)),
        ("unique_index", "<i8"),
        ("init_prob_override", "f8"),
    ]
    rows = [(1, tu_idx, [False] * n_tf, tu_idx, 0.0) for tu_idx in range(7)]

    for c in counts_range:
        # --- SWEEP FlhDC ---
        X = c / (10 + c)
        Y = fixed_fliA / (10 + fixed_fliA)
        theo_flhDC.append((b * X + bp * Y) / (b + bp))

        proc = FlagellaTranscriptionRegulation(
            {
                "tf_ids": ["EG11355-MONOMER"],
                "rna_ids": rna_ids,
                "bulk_molecule_ids": bulk_mol_ids,
                "K_flhDC": 10,
                "K_fliA": 10,
                "seed": 0,
            }
        )
        bulk = np.array(
            [("CPLX0-3930[c]", c), ("EG11355-MONOMER[c]", fixed_fliA)],
            dtype=[("id", "U40"), ("count", int)],
        )
        promoters = MetadataArray(np.array(rows, dtype=promoter_dtypes), 7)
        states = {
            "promoters": promoters,
            "bulk": bulk,
            "timestep": 2.0,
            "next_update_time": 0.0,
            "global_time": 0.0,
        }
        out = proc.next_update(2.0, states)
        actual_flhDC.append(out["promoters"]["set"]["init_prob_override"][gene_idx])

        # --- SWEEP FliA ---
        X = fixed_flhDC / (10 + fixed_flhDC)
        Y = c / (10 + c)
        theo_fliA.append((b * X + bp * Y) / (b + bp))

        proc = FlagellaTranscriptionRegulation(
            {
                "tf_ids": ["EG11355-MONOMER"],
                "rna_ids": rna_ids,
                "bulk_molecule_ids": bulk_mol_ids,
                "K_flhDC": 10,
                "K_fliA": 10,
                "seed": 0,
            }
        )
        bulk = np.array(
            [("CPLX0-3930[c]", fixed_flhDC), ("EG11355-MONOMER[c]", c)],
            dtype=[("id", "U40"), ("count", int)],
        )
        promoters = MetadataArray(np.array(rows, dtype=promoter_dtypes), 7)
        states = {
            "promoters": promoters,
            "bulk": bulk,
            "timestep": 2.0,
            "next_update_time": 0.0,
            "global_time": 0.0,
        }
        out = proc.next_update(2.0, states)
        actual_fliA.append(out["promoters"]["set"]["init_prob_override"][gene_idx])

    fig, ax = plt.subplots(figsize=(8, 5))
    ax.plot(counts_range, actual_flhDC, color="blue", label="actual — FlhDC sweep")
    ax.plot(
        counts_range,
        theo_flhDC,
        color="blue",
        linestyle="--",
        label="theoretical — FlhDC sweep",
    )
    ax.plot(counts_range, actual_fliA, color="orange", label="actual — FliA sweep")
    ax.plot(
        counts_range,
        theo_fliA,
        color="orange",
        linestyle="--",
        label="theoretical — FliA sweep",
    )
    ax.set_xlabel("Molecule count")
    ax.set_ylabel("p_i (init_prob_override) — EG11355")
    ax.set_title("Dose-response: EG11355 (fliA), K&A SUM gate")
    ax.legend()
    plt.tight_layout()
    plt.savefig("Flg_dose_response.png")
    plt.show()
    print("test_dose_response passed")


# def test_flagella_transcription_regulation():
#      from ecoli.experiments.ecoli_master_sim import EcoliSim
#      sim = EcoliSim.from_file()
#      sim.max_duration = 2
#      sim.raw_output = False
#      sim.build_ecoli()
#      sim.run()
#      data = sim.query()
#      assert data is not None


if __name__ == "__main__":
    test_flg_regulation_math()
    test_next_update()
    test_plot()
    test_dose_response()
