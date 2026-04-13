from ecoli.library.ecoli_step import EcoliProcess as Process


class GlobalClock(Process):
    """
    Track global time for Steps that do not rely on vivarium-core's built-in
    time stepping (see :ref:`timesteps`).
    """

    name = "global_clock"

    config_schema = {}


    def inputs(self):
        return {
            'global_time': 'float{0.0}',
            'next_update_time': 'map[float]',
        }

    def outputs(self):
        return {
            'global_time': 'float',
        }


    def ports_schema(self):
        return {
            "global_time": {"_default": 0.0, "_updater": "accumulate"},
            "next_update_time": {"*": {}},
        }

    def calculate_timestep(self, interval_or_states, states=None):
        """Calculate the minimum time until a manually time-stepped process
        needs to update.

        Bridges v1 signature ``(states)`` and v2 signature ``(interval, state)``.
        """
        if states is None:
            # v1 call: calculate_timestep(states)
            view = interval_or_states
        else:
            # v2 call: calculate_timestep(interval, state)
            view = states
        return min(
            next_update_time - view["global_time"]
            for next_update_time in view["next_update_time"].values()
        )

    def next_update(self, timestep, states):
        """
        The timestep that we increment global_time by is the same minimum time step
        that we calculated in calculate_timestep. This guarantees that we never
        accidentally skip over a process update time.
        """
        return {"global_time": timestep}
