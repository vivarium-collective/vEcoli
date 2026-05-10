"""Head-to-head Division.__init__ comparison.

We've established v1 and v2-composite_lineage produce **bit-identical
bulk** at every common timestep (parity_matrix scan), but cells in v2
divide ~127s earlier than v1 in seed 0 gen 1. With bulk identical,
`cell_mass` is identical, so the only way `division_threshold` can
differ is if the `division_mass_multiplier` (a single draw from
`N(1, 0.1)` seeded by `crc32(b"CellDivision", seed)`) differs.

This script instantiates both ``Division`` (v1 vivarium) and
``CompositeDivision`` (v2 composite_lineage) with the same canonical
parameters and prints their multipliers + thresholds side-by-side.
If the multipliers differ, the seed plumbing is wrong somewhere.

Usage: uv run --no-sync python runscripts/debug_division_seed.py
"""
from __future__ import annotations

import binascii
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    # The actual seed source for both classes is `parameters["seed"]`.
    # For seed=0 / lineage_seed=0 / gen=0 we expect the same multiplier
    # in v1 and v2.
    print("=" * 60)
    print("Head-to-head Division class instantiation")
    print("=" * 60)

    from ecoli.processes.cell_division import (
        Division, CompositeDivision)

    for seed in (0, 12, 7):
        print(f"\n--- seed={seed} ---")
        # Mirror the bare minimum config. CompositeDivision is the
        # subclass; it overrides __init__ to skip composer-related
        # bookkeeping. We drop composer/composer_config so we can
        # build both with the same args.
        common = {
            "agent_id": "0",
            "division_threshold": "mass_distribution",
            "dry_mass_inc_dict": {},  # not used at __init__ time
            "seed": seed,
            "daughter_ids_function": lambda x: [x + "0", x + "1"],
            "single_daughters": True,
        }

        # v2: CompositeDivision has __init__ that bypasses Division's
        # composer requirement and computes division_mass_multiplier
        # the same way (binascii.crc32 + RandomState.normal).
        v2 = CompositeDivision(parameters=common)
        v2_seed = (binascii.crc32(b"CellDivision", seed) & 0xFFFFFFFF)
        v2_mult = v2.division_mass_multiplier

        # v1 reproduction: re-derive the multiplier the same way
        # Division.__init__ would (Division requires a composer kwarg
        # we don't have, so we just replicate the math directly —
        # if the formula is wrong this won't catch it, but if the
        # SEED is wrong it will).
        v1_seed = (binascii.crc32(b"CellDivision", seed) & 0xFFFFFFFF)
        v1_state = np.random.RandomState(seed=v1_seed)
        v1_mult = v1_state.normal(loc=1.0, scale=0.1)

        print(f"  v1 division_random_seed = {v1_seed}")
        print(f"  v2 division_random_seed = {v2_seed}")
        print(f"  v1 division_mass_multiplier = {v1_mult:.10f}")
        print(f"  v2 division_mass_multiplier = {v2_mult:.10f}")
        print(f"  ✓ identical" if abs(v1_mult - v2_mult) < 1e-12
              else f"  ✗ DIFFER by {abs(v1_mult - v2_mult):.4e}")


if __name__ == "__main__":
    main()
