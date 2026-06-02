# Colony Scaling Analysis: Powers of 2 up to 2¹⁴ Cells

What it takes to run a whole-cell vEcoli colony at each scale — node
count, cluster shape, wall time, cost — assuming the cell-as-Composite
pattern with Ray distribution. Optimized for **demo-friendly wall time**
(1-12 hours, fits in a working day) at each scale.

## Constants & assumptions

- **Per-cell tick cost**: ~0.16 s wall / sim_sec when running natively
  (matches v1 vivarium pace on the cell-as-Composite + ray
  architecture validated in this session)
- **Doubling time**: ~2700 sim_sec (typical glucose-M9 cell cycle at
  37 °C) — verified by the working `probe_cell_as_composite.py` run
- **Per-cell incremental memory**: ~10 MB (bulk arrays + unique
  molecules + listeners) — sim_data (~700 MB) is shared per node via
  Ray plasma store, NOT duplicated per actor
- **Ray actor overhead**: ~200 MB per actor (Python interpreter +
  framework state)
- **EC2 pricing** (us-east-1 spot, approximate):
  - c7i.4xlarge (16 vCPU, 32 GB): ~$0.40/hr
  - r7i.4xlarge (16 vCPU, 128 GB): ~$0.50/hr
  - r7i.8xlarge (32 vCPU, 256 GB): ~$1.00/hr
  - r7i.16xlarge (64 vCPU, 512 GB): ~$2.00/hr
- **Memory rule of thumb**: r-family (memory-optimized) once cells per
  node exceeds ~10, because sim_data + per-actor overhead crowds out
  the c-family RAM budget

## Wall-time model

For colony of N cells with D = log₂(N) doublings, parallelized across
A actors:

```
Wall time = D × 2700 × ceil(N / A) × 0.16 s   +   Ray RPC overhead
```

The `ceil(N/A)` factor is **cells per actor at the final generation**.
Earlier generations have fewer cells, so they're effectively free.
The total run is bounded by the last doubling's wall time × number of
doublings.

Cost stays roughly constant regardless of node count — total compute
work is invariant. Adding more nodes trades dollars for wall time
linearly until Ray RPC overhead dominates (typically below ~2 cells
per actor).

## Scale-by-scale plan

| 2^N | Cells | Doublings | Cluster | Total actors | Cells/actor end | Wall time | Total cost |
|----:|------:|----------:|--------|-------------:|----------------:|----------:|-----------:|
| 2⁴ | 16 | 4 | 1× **c7i.4xlarge** | ~14 | ~2 | ~1.6 hr | **~$0.65** |
| 2⁵ | 32 | 5 | 1× **r7i.4xlarge** | ~14 | ~3 | ~2.5 hr | **~$1.30** |
| 2⁶ | 64 | 6 | 1× **r7i.8xlarge** | ~30 | ~3 | ~2.9 hr | **~$3** |
| 2⁷ | 128 | 7 | 1× **r7i.16xlarge** | ~60 | ~3 | ~3.4 hr | **~$7** |
| 2⁸ | 256 | 8 | 2× **r7i.16xlarge** | ~120 | ~3 | ~3.8 hr | **~$15** |
| 2⁹ | 512 | 9 | 4× **r7i.16xlarge** | ~240 | ~3 | ~4.3 hr | **~$35** |
| 2¹⁰ | 1024 | 10 | 8× **r7i.16xlarge** | ~480 | ~3 | ~4.8 hr | **~$77** |
| 2¹¹ | 2048 | 11 | 16× **r7i.16xlarge** | ~960 | ~3 | ~5.3 hr | **~$170** |
| 2¹² | 4096 | 12 | 32× **r7i.16xlarge** | ~1900 | ~3 | ~5.8 hr | **~$370** |
| 2¹³ | 8192 | 13 | 64× **r7i.16xlarge** | ~3800 | ~3 | ~6.2 hr | **~$800** |
| 2¹⁴ | 16384 | 14 | 128× **r7i.16xlarge** | ~7600 | ~3 | ~6.7 hr | **~$1700** |

Costs above are **spot-pricing wall hours × node count**. On-demand
pricing roughly 2.5× higher. AWS Savings Plans or reserved capacity
~30-40 % cheaper for repeated runs.

### Halving wall time

Each doubling of node count roughly halves wall time at the same
total cost (until RPC overhead dominates). The above table picks
~3 cells/actor at the final generation as the sweet spot. For half
the wall time at 2¹⁴:

| 2¹⁴ time-optimized | Cells | Cluster | Total actors | Cells/actor end | Wall time | Total cost |
|---:|------:|--------|-------------:|----------------:|----------:|-----------:|
| Standard | 16384 | 128× r7i.16xlarge | ~7600 | 3 | ~6.7 hr | ~$1700 |
| Wide | 16384 | 256× r7i.16xlarge | ~15000 | 1-2 | ~3.5 hr | ~$1800 |
| Slow + cheap | 16384 | 64× r7i.16xlarge | ~3800 | 5-6 | ~12.5 hr | ~$1600 |

### Saving cost at small scale

For 2⁴ – 2⁷ the cluster fits on a single instance, so you save the
multi-node Ray head + worker overhead. This is the right regime for
algorithmic + correctness work.

## Scientific value at each scale

| 2^N | Cells | What unlocks |
|----:|------:|--------------|
| 2⁴ | 16 | Minimum for visible inter-cell heterogeneity. Variance on growth rate. Useful sanity check that lineage stochasticity is propagating correctly. |
| 2⁵-2⁶ | 32-64 | Rough population mean ± std for doubling time, mass per cell, mRNA expression by gene. |
| 2⁷ | 128 | Statistically solid mean ± std on per-cell distributions. First scale where the growth curve plot looks "real". |
| 2⁸ | 256 | Publication-quality growth curve with per-cell variance bars. Headline "we run microcolonies on Ray" demo. |
| 2⁹-2¹⁰ | 512-1024 | Rare-phenotype detection visible (1 % subpopulation = 5-10 cells, statistically meaningful). Bistable regimes start being characterizable. |
| 2¹¹-2¹² | 2048-4096 | Full distribution shape (long tails visible). Lineage drift over generations measurable. |
| 2¹³-2¹⁴ | 8192-16384 | Comparable to a real microcolony at ~2-3 hr of growth from single-cell inoculum on agar, or a 16 nL droplet at stationary phase. Approaches scale where wet-lab single-cell-tracking experiments would top out. |

## Hard scaling risks to verify

Each of these is the kind of thing that could turn the analysis above
into a lie. Verify each on a smaller-scale run before committing to
the 2¹⁴ target:

1. **sim_data sharing via plasma**: confirm `LoadSimData` instance
   lands in Ray's object store once per node, not per actor. If
   duplicated: 64 actors × 700 MB = 45 GB redundant pickles per
   node — the r7i.16xlarge starts to feel tight. Check via Ray
   dashboard `Object Store Memory` metric.
2. **Environment-update process scaling**: currently a single
   sequential summation of all cells' exchange vectors per tick. At
   16384 cells × 30 exchange species × 8 bytes = ~4 MB per tick of
   data flowing to the outer. NumPy vectorized sum is plenty fast
   in-process, but the *Ray-RPC of those vectors* to the outer is the
   real cost. May need to shard the outer too — partial sums per
   shard, then top-level merge.
3. **Outer composite framework cost**: at 16384 process_paths,
   `find_instance_paths` + `_build_view_project_cache` per tick walk
   16k entries. Empirically O(N) per outer tick. Current `merge`
   skip-when-empty patch handles this for the inner cell, but the
   outer's bookkeeping has not been profiled at 10k+ cells.
4. **Mass balance precision**: tiny floating-point rounding errors
   accumulate over thousands of divisions. Each divide does a
   binomial bulk split via the type-driven walk; over 14 generations
   of 16k cells, cumulative error could become measurable. Need a
   per-run total-mass invariant check (Σ env + Σ cells should be flat
   within ε across the whole run).
5. **Ray actor startup time**: at 4096+ actors, even 100 ms per
   actor startup adds 6+ minutes of cold start. Should warm-start the
   pool once at the beginning, or pre-spawn before mother divides.
6. **Cell-cell heterogeneity tracking**: at 16k cells, just storing
   per-cell time-series for downstream analysis is non-trivial.
   Per-cell parquet partition path means 16k subdirectories.
   File-system-friendly batching strategy needed.

## Recommended staircase

A realistic ramp from working state to the headline 2¹⁴ demo:

1. **2⁴ (16 cells)**: lock down on a single c7i.4xlarge to validate
   correctness of multi-generation dynamics. ~$1, half-day's work.
2. **2⁶ (64 cells)**: verify scientific picture on r7i.8xlarge. ~$3.
   Decide if scaling is worth pushing. This is the regime where
   the growth-curve + mass-balance plots become believable.
3. **2⁸ (256 cells)**: first multi-node Ray cluster run. Catches all
   the inter-node bottlenecks. ~$15.
4. **2¹⁰ (1024 cells)**: verify environment-update scaling, mass
   balance, sim_data plasma sharing. ~$77.
5. **2¹² (4096 cells)**: full bottleneck verification at scale. Catch
   anything quadratic before it hurts. ~$370.
6. **2¹⁴ (16384 cells)**: headline run. ~$1700.

Each step's wall time is short enough to iterate the same day.
Bottlenecks compound across scales — a problem invisible at 256 cells
can dominate at 16384.

## What the 2¹⁴ demo looks like

- **Run**: 38000 sim_sec of biological time (~10.5 hours of cell life
  at 37 °C) starting from 1 cell, ending with ~16384 cells
- **Wall time**: ~6.7 hours on a 128-node r7i.16xlarge cluster
- **Cost**: ~$1700 spot-pricing
- **Output**: per-cell time-series in parquet, single combined
  history and configuration tables for the outer composite
- **Headline plots**:
  - Cell count over time (log scale): exponential phase visible
  - [Glucose] depletion in shared media
  - [Acetate] excretion building up (E. coli overflow metabolism
    above ~0.5 hr⁻¹ growth rate)
  - Population distribution of doubling time at each generation
  - Mass balance invariant (`total atoms = const`) within ε
- **Comparison to real biology**: comparable to a microcolony at
  ~2-3 hr of growth from single-cell inoculum on agar (the regime
  before density limits kick in). Smaller than a saturated flask
  culture by 5 orders of magnitude.

Date: 2026-05-29
