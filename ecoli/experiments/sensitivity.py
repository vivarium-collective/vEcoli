import numpy as np


def sensitivity_analysis(simulate, X, delta=1e-3):
    """
    simulate: function that takes a dict X and returns dict of observables Y
    X: dict of input parameters {param_name: value}
    delta: fraction to perturb each input
    """
    base_output = simulate(X)  # run simulation at nominal X
    sensitivity_map = {}

    for param, value in X.items():
        # perturb parameter
        dX = X.copy()
        dX[param] = value * (1 + delta)
        perturbed_output = simulate(dX)

        # calculate sensitivities
        effects = []
        for obs, y0 in base_output.items():
            y1 = perturbed_output[obs]
            # relative change as weight
            weight = (y1 - y0) / (y0 if y0 != 0 else 1.0)  # avoid divide by zero
            if abs(weight) > 1e-6:  # ignore negligible effects
                effects.append((obs, weight))

        sensitivity_map[param] = effects

    return sensitivity_map
