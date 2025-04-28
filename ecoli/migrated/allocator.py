"""
=========
MIGRATED: Allocator
=========

Reads requests from PartionedProcesses, and allocates molecules according to
process priorities.
"""

import numpy as np
from process_bigraph import Step

from ecoli.library.schema import counts, numpy_schema, bulk_name_to_idx, listener_schema


NAME = "allocator"
TOPOLOGY = {
    "request": ("request",),
    "allocate": ("allocate",),
    "bulk": ("bulk",),
    "listeners": ("listeners",),
    "allocator_rng": ("allocator_rng",),
}
# topology_registry.register(NAME, TOPOLOGY)
# # Register "allocator-1", "allocator-2", "allocator-3" to support
# # multi-tiered partitioning scheme
# topology_registry.register(NAME + "-1", TOPOLOGY)
# topology_registry.register(NAME + "-2", TOPOLOGY)
# topology_registry.register(NAME + "-3", TOPOLOGY)


class NegativeCountsError(Exception):
    pass


class Allocator(Step):
    """Allocator Step"""

    config_schema = {
        "molecule_names": "list[string]",
        "process_names": "list[string]",
        "custom_priorities": "tree",
        "seed": "integer",
        "name": "string",
        "assert_positive_counts": "boolean",
    }

    def __init__(self, config=None, core=None):
        super().__init__(config, core)

        # class attributes taken from 1.0 implementation:
        self.name = self.config.get("name", "allocator")
        self.assert_positive_counts = self.config.get("assert_positive_counts", True)

        self.atp_idx: int | np.ndarray | None = None

        self.moleculeNames = self.config["molecule_names"]
        self.n_molecules = len(self.moleculeNames)
        self.mol_name_to_idx = {
            name: idx for idx, name in enumerate(self.moleculeNames)
        }
        self.mol_idx_to_name = {
            idx: name for idx, name in enumerate(self.moleculeNames)
        }
        self.processNames = self.config["process_names"]
        self.n_processes = len(self.processNames)
        self.proc_name_to_idx = {
            name: idx for idx, name in enumerate(self.processNames)
        }
        self.proc_idx_to_name = {
            idx: name for idx, name in enumerate(self.processNames)
        }
        self.processPriorities = np.zeros(len(self.processNames))
        for process, custom_priority in self.config["custom_priorities"].items():
            if process not in self.proc_name_to_idx.keys():
                continue
            self.processPriorities[self.proc_name_to_idx[process]] = custom_priority
        self.seed = self.config["seed"]

        # Helper indices for Numpy indexing
        self.molecule_idx = None

    def ports_schema(self):
        ports = {
            "bulk": numpy_schema("bulk"),
            "request": {
                process: {
                    "bulk": {
                        "_default": [],
                        "_emit": False,
                        "_divider": "null",
                        "_updater": "set",
                    }
                }
                for process in self.processNames
            },
            "allocate": {
                process: {
                    "bulk": {
                        "_default": [],
                        "_emit": False,
                        "_divider": "null",
                        "_updater": "set",
                    }
                }
                for process in self.processNames
            },
            "listeners": {
                "atp": listener_schema(
                    {
                        "atp_requested": ([0] * self.n_processes, self.processNames),
                        "atp_allocated_initial": (
                            [0] * self.n_processes,
                            self.processNames,
                        ),
                        # Use blame functionality to get ATP consumed per process
                        # 'atp_allocated_final': ([0] * self.n_processes,
                        #     self.processNames)
                    }
                )
            },
            "allocator_rng": {"_default": np.random.RandomState(seed=self.seed)},
        }
        return ports

    def inputs(self):
        # TODO: register types like bulk via registry in json
        return {
            "bulk": "tree",
            "request": {process: {"bulk": "list"} for process in self.processNames},
            "listeners": {
                "atp": {
                    "atp_requested": "array",
                    "atp_allocated_initial": "array",
                }
            },
            # "allocator_rng": "any"  # TODO: what should we do here: is rng serializable?
        }

    def outputs(self):
        return {
            "request": "tree[array]",
            "allocate": "tree[array]",
            "listeners": {
                "atp": {
                    "atp_requested": "array",
                    "atp_allocated_initial": "array",
                }
            },
        }

    def update(self, state):
        random_state_k = np.random.RandomState(seed=self.seed)

        if self.molecule_idx is None:
            self.molecule_idx = bulk_name_to_idx(
                self.moleculeNames, state["bulk"]["id"]
            )
            self.atp_idx = bulk_name_to_idx("ATP[c]", state["bulk"]["id"])

        total_counts: np.ndarray = counts(state["bulk"], self.molecule_idx)
        original_totals = total_counts.copy()
        counts_requested: np.ndarray = np.zeros(
            (self.n_molecules, self.n_processes), dtype=int
        )
        # Keep track of which process indices are in current partitioning layer
        proc_idx_in_layer = []
        for process in state["request"]:
            proc_idx = self.proc_name_to_idx[process]
            if len(state["request"][process]["bulk"]) > 0:
                proc_idx_in_layer.append(proc_idx)
            for req_idx, req in state["request"][process]["bulk"]:
                counts_requested[req_idx, proc_idx] += req

        if self.assert_positive_counts and np.any(counts_requested < 0):
            raise NegativeCountsError(
                "Negative value(s) in counts_requested:\n"
                + "\n".join(
                    "{} in {} ({})".format(
                        self.mol_idx_to_name[molIndex],
                        self.proc_idx_to_name[processIndex],
                        counts_requested[molIndex, processIndex],
                    )
                    for molIndex, processIndex in zip(*np.where(counts_requested < 0))
                )
            )

        # Calculate partition
        raw_partitioned_counts: np.ndarray = calculate_partition(
            self.processPriorities,
            counts_requested,
            total_counts,
            random_state_k,
            # states["allocator_rng"],
        )

        partitioned_counts = raw_partitioned_counts.astype(int, copy=False)

        if self.assert_positive_counts and np.any(partitioned_counts < 0):
            raise NegativeCountsError(
                "Negative value(s) in partitioned_counts:\n"
                + "\n".join(
                    "{} in {} ({})".format(
                        self.mol_idx_to_name[molIndex],
                        self.proc_idx_to_name[processIndex],
                        counts_requested[molIndex, processIndex],
                    )
                    for molIndex, processIndex in zip(*np.where(partitioned_counts < 0))
                )
            )

        # Ensure we are not overdrafting any molecules
        counts_unallocated = original_totals - np.sum(partitioned_counts, axis=-1)

        if self.assert_positive_counts and np.any(counts_unallocated < 0):
            raise NegativeCountsError(
                "Negative value(s) in counts_unallocated:\n"
                + "\n".join(
                    "{} ({})".format(
                        self.mol_idx_to_name[molIndex], counts_unallocated[molIndex]
                    )
                    for molIndex in np.where(counts_unallocated < 0)[0]
                )
            )

        # Only update listener ATP counts for processes in
        # current partitioning layer
        non_zero_mask = counts_requested[self.atp_idx, :] != 0
        curr_atp_req = np.array(state["listeners"]["atp"]["atp_requested"]).copy()
        curr_atp_alloc = np.array(
            state["listeners"]["atp"]["atp_allocated_initial"]
        ).copy()
        curr_atp_req[non_zero_mask] = counts_requested[self.atp_idx, non_zero_mask]
        curr_atp_alloc[non_zero_mask] = partitioned_counts[self.atp_idx, non_zero_mask]

        update = {
            "request": {process: {"bulk": []} for process in state["request"]},
            "allocate": {
                process: {"bulk": partitioned_counts[:, self.proc_name_to_idx[process]]}
                for process in state["request"]
            },
            "listeners": {
                "atp": {
                    "atp_requested": curr_atp_req,
                    "atp_allocated_initial": curr_atp_alloc,
                }
            },
        }

        return update


def calculate_partition(
    process_priorities: np.ndarray,
    counts_requested: np.ndarray,
    total_counts: np.ndarray,
    random_state: np.random.RandomState,
):
    priorityLevels = np.sort(np.unique(process_priorities))[::-1]

    partitioned_counts = np.zeros_like(counts_requested)

    for priorityLevel in priorityLevels:
        processHasPriority = priorityLevel == process_priorities

        requests = counts_requested[:, processHasPriority].copy()

        total_requested = requests.sum(axis=1)
        excess_request_mask = (total_requested > total_counts) & (total_requested > 0)

        # Get fractional request for molecules that have excess request
        # compared to available counts
        fractional_requests = (
            requests[excess_request_mask, :]
            * total_counts[excess_request_mask, np.newaxis]
            / total_requested[excess_request_mask, np.newaxis]
        )

        # Distribute fractional counts to ensure full allocation of excess
        # request molecules
        remainders = fractional_requests % 1
        options = np.arange(remainders.shape[1])
        for idx, remainder in enumerate(remainders):
            total_remainder = remainder.sum()
            count = int(np.round(total_remainder))
            if count > 0:
                allocated_indices = random_state.choice(
                    options, size=count, p=remainder / total_remainder, replace=False
                )
                fractional_requests[idx, allocated_indices] += 1
        requests[excess_request_mask, :] = fractional_requests

        allocations = requests.astype(np.int64)
        partitioned_counts[:, processHasPriority] = allocations
        total_counts -= allocations.sum(axis=1)
    return partitioned_counts
