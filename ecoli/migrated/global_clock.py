from process_bigraph import Process


class GlobalClock(Process):
    """
    Track global time for Steps that do not rely on process-bigraph's built-in
    time stepping (see :ref:`timesteps`). TODO: is `timesteps` still applicable in process-bigraph?
    """

    def inputs(self):
        return {
            "global_time": "float",
            "next_update_time": "tree[float]",
        }

    def outputs(self):
        return {
            "global_time": "float",
            "next_update_time": "tree[float]",
        }

    def calculate_timestep(self, state):
        """
        Subtract global time from next update times for each manually time-stepped
        processes to calculate time until a process updates. Use that time as the
        time step for this process so vivarium-core's internal simulation clock
        advances by the same amount of time and processes that do not rely on
        this manual time stepping stay in sync with the ones that do.
        """
        return min(
            next_update_time - state["global_time"]
            for next_update_time in state["next_update_time"].values()
        )

    def update(self, state, interval):
        """
        The timestep that we increment global_time by is the same minimum time step
        that we calculated in calculate_timestep. This guarantees that we never
        accidentally skip over a process update time.
        """
        return {"global_time": interval}
