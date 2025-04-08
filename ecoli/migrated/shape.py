"""
=====
Shape
=====

``Shape`` is used to calculate shape properties using 3D capsule geometry.
Outputs `length and `surface_area` are determined from inputs `volume` and `width`.
These variables are required to plug into a `Lattice Environment`
"""

import math

from scipy.constants import N_A
from process_bigraph import Step
from vivarium.library.units import units, Quantity


PI = math.pi
AVOGADRO = N_A / units.mol


def length_from_volume(volume, width):
    """
    get cell length from volume, using the following equation for capsule volume, with V=volume, r=radius,
    a=length of cylinder without rounded caps, l=total length:

    :math:`V = (4/3)*PI*r^3 + PI*r^2*a`
    :math:`l = a + 2*r`
    """
    radius = width / 2
    cylinder_length = (volume - (4 / 3) * PI * radius**3) / (PI * radius**2)
    total_length = cylinder_length + 2 * radius
    return total_length


def volume_from_length(length, width):
    """
    get volume from length and width, using 3D capsule geometry
    """
    radius = width / 2
    cylinder_length = length - width
    volume = cylinder_length * (PI * radius**2) + (4 / 3) * PI * radius**3
    return volume


def surface_area_from_length(length, width):
    """
    get surface area from length and width, using 3D capsule geometry

    :math:`SA = 4*PI*r^2 + 2*PI*r*a`
    """
    radius = width / 2
    cylinder_length = length - width
    surface_area = 4 * PI * radius**2 + 2 * PI * radius * cylinder_length
    return surface_area


def mmol_to_counts_from_volume(volume):
    """mmol_to_counts has units L/mmol"""
    return (volume * AVOGADRO).to(units.L / units.mmol)


class Shape(Step):
    """Shape Step

    Derives cell length and surface area from width and volume.

    Ports:

    * **cell_global**: Should be given the agent's boundary store.
      Contains variables: **volume**, **width**, **length**, and
      **surface_area**.
    * **periplasm_global**: Contains the **volume** variable for the
      volume of the periplasm.

    Arguments:
        parameters (dict): A dictionary that can contain the
            following configuration options:

            * **width** (:py:class:`float`): Initial width of the cell in
              microns
    """

    config_schema = {
        "width": {
            "_default": 1.0,
            "_type": "float"
        },
        "periplasm_fraction": {
            "_default": 0.2,
            "_type": "float"
        },
        "cytoplasm_fraction": {
            "_default": 0.8,
            "_type": "float"
        },
        "initial_cell_volume": {
            "_default": 1.2,
            "_type": "float"
        },
        "initial_mass": {"_default": 1339, "_type": "integer"},
    }

    def __init__(self, config=None, core=None):
        super().__init__(config, core)
        self.outer_to_inner_area = (
            math.pow(self.config["cytoplasm_fraction"], 1 / 3) ** 2
        )

    def initial_state(self):
        cell_volume = Quantity(
            value=self.config["initial_cell_volume"],
            units=units.fL
        )
        width = Quantity(self.config["width"], units=units.um)
        length = length_from_volume(cell_volume, width)
        outer_surface_area = surface_area_from_length(length, width)
        inner_surface_area = self.outer_to_inner_area * outer_surface_area

        assert (
            self.config["periplasm_fraction"]
            + self.config["cytoplasm_fraction"]
            == 1
        )
        periplasm_volume = cell_volume * self.config["periplasm_fraction"]
        cytoplasm_volume = cell_volume * self.config["cytoplasm_fraction"]

        mass = Quantity(value=self.config["initial_mass"], units=units.fg)
        cell_mmol_to_counts = mmol_to_counts_from_volume(cell_volume)
        periplasm_mmol_to_counts = mmol_to_counts_from_volume(periplasm_volume)
        cytoplasm_mmol_to_counts = mmol_to_counts_from_volume(cytoplasm_volume)

        return {
            "cell_global": {
                "volume": cell_volume,
                "width": width,
                "length": length,
                "outer_surface_area": outer_surface_area,
                "inner_surface_area": inner_surface_area,
                "mmol_to_counts": cell_mmol_to_counts.m if isinstance(cell_mmol_to_counts, Quantity) else cell_mmol_to_counts,
                "mass": mass,
            },
            "listener_cell_mass": mass.magnitude,
            "listener_cell_volume": cell_volume.magnitude,
            "periplasm_global": {
                "volume": periplasm_volume,
                "mmol_to_counts": periplasm_mmol_to_counts.m if isinstance(periplasm_mmol_to_counts, Quantity) else periplasm_mmol_to_counts,
            },
            "cytoplasm_global": {
                "volume": cytoplasm_volume,
                "mmol_to_counts": cytoplasm_mmol_to_counts.m if isinstance(cytoplasm_mmol_to_counts, Quantity) else cytoplasm_mmol_to_counts,
            },
        }

    def inputs(self):
        return {
            "cell_global": "tree",
            "periplasm_global": "tree",
            "cytoplasm_global": "tree",
            "listener_cell_volume": "float"
        }

    def outputs(self):
        return {
            "cell_global": "tree",
            "listener_cell_mass": "float",
            "listener_cell_volume": "float",
            "periplasm_global": "tree",
            "cytoplasm_global": "tree"
        }

    def update(self, state):
        for port in ("cell_global", "periplasm_global", "cytoplasm_global"):
            for variable, value in state[port].items():
                if not isinstance(value, Quantity):
                    state[port][variable] = Quantity(value)
                # assert isinstance(
                #     value, Quantity
                # ), f"{variable}={value} is not a Quantity"

        width = state["cell_global"]["width"]
        cell_volume = state["listener_cell_volume"] * units.fL

        assert (
            self.config["periplasm_fraction"]
            + self.config["cytoplasm_fraction"]
            == 1
        )
        periplasm_volume = cell_volume * self.config["periplasm_fraction"]
        cytoplasm_volume = cell_volume * self.config["cytoplasm_fraction"]

        # calculate length and surface area
        length = length_from_volume(cell_volume, width)
        outer_surface_area = surface_area_from_length(length, width)
        inner_surface_area = self.outer_to_inner_area * outer_surface_area

        cell_mmol_to_counts = mmol_to_counts_from_volume(cell_volume)
        periplasm_mmol_to_counts = mmol_to_counts_from_volume(periplasm_volume)
        cytoplasm_mmol_to_counts = mmol_to_counts_from_volume(cytoplasm_volume)

        update = {
            "cell_global": {
                "length": length,
                "outer_surface_area": outer_surface_area,
                "inner_surface_area": inner_surface_area,
                "mmol_to_counts": cell_mmol_to_counts.m if isinstance(cell_mmol_to_counts, Quantity) else cell_mmol_to_counts,
                "mass": state["listener_cell_mass"] * units.fg,
                "volume": cell_volume,
            },
            "periplasm_global": {
                "volume": periplasm_volume,
                "mmol_to_counts": periplasm_mmol_to_counts.m if isinstance(periplasm_mmol_to_counts, Quantity) else periplasm_mmol_to_counts,
            },
            "cytoplasm_global": {
                "volume": cytoplasm_volume,
                "mmol_to_counts": cytoplasm_mmol_to_counts.m if isinstance(cytoplasm_mmol_to_counts, Quantity) else cytoplasm_mmol_to_counts,
            },
        }
        return update

    # TODO: make the schema for cell_global more specific than just a tree
    # def ports_schema(self):
    #     schema = {
    #         "cell_global": {
    #             "volume": {
    #                 "_default": 0 * units.fL,
    #                 "_updater": "set",
    #                 "_emit": True,
    #                 "_divider": "split",
    #             },
    #             "width": {
    #                 "_default": 0 * units.um,
    #                 "_updater": "set",
    #                 "_emit": True,
    #                 "_divider": "set",
    #             },
    #             "length": {
    #                 "_default": 0 * units.um,
    #                 "_updater": "set",
    #                 "_emit": True,
    #                 "_divider": "split",
    #             },
    #             "outer_surface_area": {
    #                 "_default": 0 * units.um**2,
    #                 "_updater": "set",
    #                 "_emit": True,
    #                 "_divider": "split",
    #             },
    #             "inner_surface_area": {
    #                 "_default": 0 * units.um**2,
    #                 "_updater": "set",
    #                 "_emit": True,
    #                 "_divider": "split",
    #             },
    #             "mmol_to_counts": {
    #                 "_default": 0 / units.millimolar,
    #                 "_emit": True,
    #                 "_divider": "split",
    #                 "_updater": "set",
    #             },
    #             "mass": {
    #                 "_default": 0 * units.fg,
    #                 "_updater": "set",
    #                 "_emit": True,
    #                 "_divider": "split",
    #             },
    #         },
    #         "listener_cell_mass": {
    #             "_default": self.parameters["initial_mass"].magnitude,  # fg
    #         },
    #         "listener_cell_volume": {
    #             "_default": self.parameters["initial_cell_volume"].magnitude,  # fL
    #         },
    #         "periplasm_global": {
    #             "volume": {
    #                 "_default": self.parameters["initial_cell_volume"]
    #                 * self.parameters["periplasm_fraction"],  # fL
    #                 "_emit": True,
    #                 "_divider": "split",
    #                 "_updater": "set",
    #             },
    #             "mmol_to_counts": {
    #                 "_default": 0 / units.millimolar,
    #                 "_emit": True,
    #                 "_divider": "split",
    #                 "_updater": "set",
    #             },
    #         },
    #         "cytoplasm_global": {
    #             "volume": {
    #                 "_default": self.parameters["initial_cell_volume"]
    #                 * self.parameters["cytoplasm_fraction"],  # fL
    #                 "_emit": True,
    #                 "_divider": "split",
    #                 "_updater": "set",
    #             },
    #             "mmol_to_counts": {
    #                 "_default": 0 / units.millimolar,
    #                 "_emit": True,
    #                 "_divider": "split",
    #                 "_updater": "set",
    #             },
    #         },
    #     }
    #     return schema
