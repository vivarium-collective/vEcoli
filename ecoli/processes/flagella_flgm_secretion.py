import numpy as np

from vivarium.core.process import Step
from ecoli.library.schema import numpy_schema, bulk_name_to_idx, counts
from ecoli.processes.registries import topology_registry


NAME = "ecoli-flagella-flgm-secretion"
TOPOLOGY = {
    "bulk": ("bulk",),
    "timestep": ("timestep",),
    "next_update_time": ("next_update_time", "flagella_flgm_secretion"),
    "global_time": ("global_time",),
}
topology_registry.register(NAME, TOPOLOGY)


class FlagellaFlgMSecretion(Step):
    """
    Depletes cytoplasmic FlgM (G369-MONOMER[c]) at a rate proportional to
    the number of complete flagella (CPLX0-7452[j]).

    Biology: Once the hook-basal body is assembled and the filament is added
    (forming CPLX0-7452, the complete flagellum), the type III secretion channel
    actively pumps cytoplasmic FlgM out of the cell. As cytoplasmic FlgM drops,
    the FLGM-FLIA-CPLX equilibrium (equilibrium_reactions.tsv) shifts toward
    releasing free FliA (EG11355-MONOMER[c]). Free FliA then acts as sigma-28,
    activating Class III flagella promoters (fliC, motAB, cheAW, etc.).

    This process is the Class II -> Class III gate. Without it, FlgM accumulates
    indefinitely and pe rmanently sequesters FliA, preventing Class III expression.
    With it, Class III genes activate only after flagellum assembly — producing the
    timed transcriptional cascade seen experimentally (Kalir et al. 2001).

    We use CPLX0-7452[j] (complete flagellum) rather than FLAGELLAR-MOTOR-COMPLEX[j]
    because in a cell with assembled flagella, FLAGELLAR-MOTOR-COMPLEX is consumed
    as a subunit of CPLX0-7452. CPLX0-7452 is the persistent structure through
    which FlgM is secreted at steady state.

    Runs as a Step so it can be ordered after the complexation process and before
    transcript initiation each timestep.

    Ordered in flow:
        ecoli-complexation -> ecoli-flagella-flgm-secretion -> ecoli-transcript-initiation
    """

    name = NAME
    topology = TOPOLOGY
    defaults = {
        "bulk_molecule_ids": [],
        "flgM_id": "G369-MONOMER[c]",
        # CPLX0-7452 is the complete flagellum (hook-basal body + filament), [j] compartment.
        # FLAGELLAR-MOTOR-COMPLEX is consumed as a subunit of CPLX0-7452, so it is 0
        # in cells with fully assembled flagella.
        "hbb_id": "CPLX0-7452[j]",
        # FlgM molecules exported per complete flagellum per second.
        # Scaling factor: calibrated from FlgM half-life measurements in Salmonella.
        # FlgM t1/2 drops from ~30 min (no HBB) to ~2 min (with HBB), giving a
        # net secretion rate of ~5.4e-3 /s. Distributed across ~4 flagella at
        # ~100 cytoplasmic FlgM gives ~0.1 molecules/flagellum/s.
        # Hughes KT et al. (1993) Science 262:1277. Aldridge PD & Hughes KT (2002)
        # Curr Opin Microbiol 5:160.
        "secretion_rate": 0.1,
    }

    def __init__(self, parameters=None):
        super().__init__(parameters)
        bulk_ids = self.parameters["bulk_molecule_ids"]
        self.flgM_idx = bulk_name_to_idx(self.parameters["flgM_id"], bulk_ids)
        self.hbb_idx = bulk_name_to_idx(self.parameters["hbb_id"], bulk_ids)
        self.secretion_rate = self.parameters["secretion_rate"]

    def ports_schema(self):
        return {
            "bulk": numpy_schema("bulk"),
            "timestep": {"_default": 2.0},
            "next_update_time": {
                "_default": 0.0,
                "_updater": "set",
                "_divider": "set",
            },
            "global_time": {"_default": 0.0},
        }

    def next_update(self, timestep, states):
        if states["next_update_time"] > states["global_time"]:
            return {}

        hbb_count = counts(states["bulk"], self.hbb_idx)
        flgM_count = counts(states["bulk"], self.flgM_idx)

        exported = 0
        if hbb_count > 0 and flgM_count > 0:
            # Export rate: secretion_rate molecules per HBB per second, scaled by timestep.
            # Clamped to available FlgM so counts never go negative.
            exported = min(
                flgM_count, round(hbb_count * self.secretion_rate * states["timestep"])
            )

        return {
            "bulk": [(self.flgM_idx, -exported)],
            "next_update_time": states["global_time"] + states["timestep"],
        }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def _make_bulk(flgM_count, hbb_count):
    return np.array(
        [
            ("G369-MONOMER[c]", flgM_count),
            ("CPLX0-7452[j]", hbb_count),
        ],
        dtype=[("id", "U50"), ("count", int)],
    )


def _make_process(secretion_rate=0.1):
    return FlagellaFlgMSecretion(
        {
            "bulk_molecule_ids": np.array(["G369-MONOMER[c]", "CPLX0-7452[j]"]),
            "secretion_rate": secretion_rate,
        }
    )


def _make_states(flgM_count, hbb_count, global_time=0.0, next_update_time=0.0):
    return {
        "bulk": _make_bulk(flgM_count, hbb_count),
        "timestep": 2.0,
        "global_time": global_time,
        "next_update_time": next_update_time,
    }


def test_no_hbb_no_export():
    """When no HBB is assembled, FlgM must not be exported."""
    proc = _make_process()
    out = proc.next_update(2.0, _make_states(flgM_count=50, hbb_count=0))
    delta = dict(out["bulk"])[proc.flgM_idx]
    assert delta == 0, f"Expected 0 export with no HBB, got {delta}"
    print("test_no_hbb_no_export passed")


def test_no_flgm_no_export():
    """When FlgM is already zero, export must not go negative."""
    proc = _make_process()
    out = proc.next_update(2.0, _make_states(flgM_count=0, hbb_count=3))
    delta = dict(out["bulk"])[proc.flgM_idx]
    assert delta == 0, f"Expected 0 export with no FlgM, got {delta}"
    print("test_no_flgm_no_export passed")


def test_export_scales_with_hbb():
    """More assembled HBBs -> more FlgM exported per timestep."""
    proc = _make_process(secretion_rate=1.0)
    out1 = proc.next_update(2.0, _make_states(flgM_count=100, hbb_count=1))
    out2 = proc.next_update(2.0, _make_states(flgM_count=100, hbb_count=3))
    d1 = abs(dict(out1["bulk"])[proc.flgM_idx])
    d2 = abs(dict(out2["bulk"])[proc.flgM_idx])
    assert d2 >= d1, f"Expected more export with more HBBs: {d1} vs {d2}"
    print(f"test_export_scales_with_hbb passed  (1 HBB -> {d1}, 3 HBBs -> {d2})")


def test_export_clamped_to_available():
    """Exported amount is clamped to available FlgM — never goes negative."""
    proc = _make_process(secretion_rate=100.0)  # very high rate
    out = proc.next_update(2.0, _make_states(flgM_count=5, hbb_count=10))
    delta = dict(out["bulk"])[proc.flgM_idx]
    assert delta >= -5, f"FlgM count would go negative: delta={delta}"
    assert delta <= 0, f"Expected a non-positive delta, got {delta}"
    print(f"test_export_clamped_to_available passed  (delta={delta}, available=5)")


def test_guard_skips_early():
    """next_update_time guard: if not yet time to run, return empty update."""
    proc = _make_process()
    states = _make_states(
        flgM_count=50, hbb_count=3, global_time=0.0, next_update_time=5.0
    )
    out = proc.next_update(2.0, states)
    assert out == {}, f"Expected empty update when guard fires, got {out}"
    print("test_guard_skips_early passed")


def test_next_update_time_advances():
    """After a real update, next_update_time should advance by one timestep."""
    proc = _make_process()
    states = _make_states(
        flgM_count=50, hbb_count=1, global_time=10.0, next_update_time=10.0
    )
    out = proc.next_update(2.0, states)
    assert out["next_update_time"] == 12.0, (
        f"Expected next_update_time=12.0, got {out['next_update_time']}"
    )
    print("test_next_update_time_advances passed")


if __name__ == "__main__":
    test_no_hbb_no_export()
    test_no_flgm_no_export()
    test_export_scales_with_hbb()
    test_export_clamped_to_available()
    test_guard_skips_early()
    test_next_update_time_advances()
    print("\nAll tests passed.")
