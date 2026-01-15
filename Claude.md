# Claude.md - vEcoli Project Documentation

## Project Overview

vEcoli (Vivarium E. coli) is a port of the Covert Lab's E. coli Whole Cell Model to the Vivarium framework. It provides a comprehensive computational simulation of E. coli cellular processes including metabolism, gene expression, chromosome replication, and cell division.

The project supports running simulations across multiple computing environments: local machines, HPC clusters (Stanford Sherlock, CCAM), and cloud platforms (Google Cloud, AWS).

## Repository Structure

Key directories:
- `runscripts/` - Workflow orchestration, simulation runners, and container definitions
- `configs/` - JSON configuration files for different simulation scenarios
- `ecoli/` - Core simulation code (processes, composites, analysis)
- `reconstruction/` - Data reconstruction and parameter calculation

## Workflow Architecture

### Core Workflow Script: `runscripts/workflow.py`

This is the main entry point for running simulations. It orchestrates a four-stage pipeline:

1. **ParCa (Parameter Calculator)** - Generates simulation parameters from experimental data
2. **Variant Creation** - Creates modified simulation data for different experimental conditions
3. **Cell Simulations** - Runs individual cell simulations
4. **Analysis** - Aggregates and analyzes simulation results

### Command-Line Interface

```bash
python3 runscripts/workflow.py --config <config_path> [--resume <experiment_id>] [--build-only]
```

| Argument | Description |
|----------|-------------|
| `--config` | Path to JSON config file (default: `configs/default.json`) |
| `--resume` | Resume a failed workflow with given experiment ID |
| `--build-only` | Generate workflow files without execution |

### Configuration Merging

When a user config is provided, it merges with `configs/default.json`. Special list keys are **concatenated** (not overwritten):
- `save_times`
- `add_processes`
- `exclude_processes`
- `processes`
- `engine_process_reports`
- `initial_state_overrides`

## Environment-Specific Invocation

### 1. Local (Standard Profile)

The default for development and testing. No container required.

```bash
# Simple local run
python3 runscripts/workflow.py --config configs/test_installation.json

# Build only (inspect generated files)
python3 runscripts/workflow.py --config configs/default.json --build-only
```

**Executor:** Local
**Profile:** `standard`
**Container:** None

### 2. Stanford Sherlock HPC

Sherlock uses SLURM for job scheduling and Apptainer for containerization.

**Configuration:**
```json
{
  "experiment_id": "my-experiment",
  "sherlock": {
    "container_image": "/path/to/image.sif",
    "build_image": true,
    "hyperqueue": false,
    "jenkins": false
  }
}
```

**Invocation:**
```bash
python3 runscripts/workflow.py --config configs/test_sherlock.json
```

**What happens:**
1. If `build_image: true`, submits SBATCH job to build Apptainer image
2. Generates `nextflow_job.sh` SBATCH script
3. Submits workflow via `sbatch`
4. Nextflow uses SLURM executor

**Key SLURM settings (from `config.template`):**
- Queue: `owners,normal`
- CPUs: 1 per task
- Memory: 4GB (scales on OOM retry up to 3x)
- Time: 4 hours (scales on timeout)
- Max retries: 3
- Queue size limit: 2000 jobs

**HyperQueue Mode:**
When `hyperqueue: true`, uses HyperQueue for better resource utilization with many small jobs. Changes profile to `sherlock_hq`.

**Environment setup (add to `~/.bash_profile`):**
```bash
module load system git java/21.0.4 python/3.12.1
export PATH=$PATH:$GROUP_HOME/vEcoli_env
```

### 3. Google Cloud (gcloud Profile)

Uses Google Batch for execution with Docker containers.

**Configuration:**
```json
{
  "experiment_id": "cloud-experiment",
  "gcloud": {
    "container_image": "my-image-name",
    "build_image": true
  },
  "emitter_arg": {
    "out_uri": "gs://my-bucket/results"
  }
}
```

**Invocation:**
```bash
gcloud config set project YOUR_PROJECT
gcloud config set compute/region us-central1
python3 runscripts/workflow.py --config configs/cloud.json
```

**What happens:**
1. If `build_image: true`, submits to Cloud Build
2. Uploads image to Artifact Registry: `{REGION}-docker.pkg.dev/{PROJECT}/vecoli/{IMAGE}`
3. Runs Nextflow with `google-batch` executor
4. Uses spot instances with dynamic machine type selection

**Executor:** `google-batch`
**Container:** Docker from Artifact Registry

### 4. CCAM (Multi-Institution HPC)

For HPC clusters using SLURM with Singularity.

**Environment setup (`.hpc_env` file in project root):**
```bash
SLURM_PARTITION=your_partition
SLURM_QOS=your_qos
SLURM_NODE_LIST=node[001-010]  # optional
SLURM_LOG_BASE_PATH=/path/to/logs
```

**Configuration:**
```json
{
  "experiment_id": "ccam-experiment",
  "ccam": {
    "container_image": "/path/to/image.sif",
    "build_image": true,
    "direct": false,
    "wait": false
  }
}
```

**Invocation:**
```bash
python3 runscripts/workflow.py --config your_ccam_config.json
```

**Direct mode:** Set `"direct": true` to run Nextflow directly (useful when already inside a SLURM allocation).

**Executor:** SLURM
**Container:** Singularity

### 5. AWS CDK

For AWS-based SLURM clusters using Singularity containers. This profile was tested and validated on AWS ParallelCluster.

**Environment setup:**

Create a `.hpc_env` file or export these environment variables:
```bash
export SLURM_PARTITION=jobs-queue
export SLURM_LOG_BASE_PATH=/path/to/slurm/logs
```

**Configuration:**
```json
{
  "experiment_id": "aws-experiment",
  "parca_options": {"cpus": 2},
  "emitter": "parquet",
  "emitter_arg": {
    "out_dir": "/mnt/fsx/path/to/output"
  },
  "aws_cdk": {
    "container_image": "/path/to/image.sif",
    "build_image": false
  },
  "analysis_options": {
    "single": {"mass_fraction_summary": {}}
  }
}
```

**Invocation:**
```bash
export SLURM_PARTITION=jobs-queue
export SLURM_LOG_BASE_PATH=/path/to/logs
source .venv/bin/activate
python runscripts/workflow.py --config configs/my_aws_config.json
```

**What happens:**
1. Generates SBATCH script for Nextflow orchestration
2. Submits to SLURM queue (default: `jobs-queue`)
3. Nextflow submits individual task jobs to SLURM
4. Uses Singularity containers for execution

**Resource Settings (from `config.template`):**
- ParCa: cpus × 2GB memory, 1h time limit
- Simulations: 1 CPU, 4GB memory (scales on OOM), 4h time (scales on timeout)
- Max retries: 3
- Error handling: retry on OOM (137), time limit (140), preemption (143)

**Executor:** SLURM
**Container:** Singularity

## Nextflow Profiles Summary

| Profile | Executor | Container | Use Case |
|---------|----------|-----------|----------|
| `standard` | local | none | Local development |
| `gcloud` | google-batch | Docker | Google Cloud |
| `sherlock` | slurm | Apptainer | Stanford HPC |
| `sherlock_hq` | hq (HyperQueue) | Apptainer | Stanford HPC with HyperQueue |
| `ccam` | slurm | Singularity | CCAM HPC |
| `aws_cdk` | slurm | Singularity | AWS SLURM |

## Container Building

### build-image.sh (`runscripts/container/build-image.sh`)

```bash
# Local Docker build
./runscripts/container/build-image.sh -i my-image -l

# Google Cloud Build (default)
./runscripts/container/build-image.sh -i my-image

# Apptainer/Singularity build
./runscripts/container/build-image.sh -i my-image.sif -a
```

### Interactive Container Usage

```bash
# Docker from Artifact Registry
./runscripts/container/interactive.sh -i my-image

# Apptainer with bind mounts
./runscripts/container/interactive.sh -i my-image.sif -a -p /data

# Development mode (editable install)
./runscripts/container/interactive.sh -i my-image.tar -l -d

# Execute command non-interactively
./runscripts/container/interactive.sh -i my-image -c "python runscripts/workflow.py --config config.json"
```

## Key Configuration Parameters

### Simulation Control

```json
{
  "experiment_id": "my-exp",          // Required: unique identifier
  "suffix_time": true,                 // Auto-append timestamp
  "seed": 0,                           // Base random seed
  "lineage_seed": 0,                   // Initial seed for lineage
  "n_init_sims": 1,                    // Number of initial simulations
  "generations": 3,                    // Number of cell divisions
  "single_daughters": true,            // Only simulate one daughter
  "max_duration": 10800.0              // Max sim time (seconds)
}
```

### Output Configuration

```json
{
  "emitter": "parquet",                // Output format: timeseries, parquet, database
  "emitter_arg": {
    "out_dir": "./vecoli_output",     // Local output
    "out_uri": "gs://bucket/output",  // Cloud URI (alternative)
    "threaded": false                  // Disable for HPC
  }
}
```

### ParCa Options

```json
{
  "parca_options": {
    "cpus": 4,
    "operons": true,
    "ribosome_fitting": true,
    "rnapoly_fitting": true,
    "save_intermediates": false
  }
}
```

### Analysis Options

```json
{
  "analysis_options": {
    "cpus": 2,
    "single": {"mass_fraction_summary": {}},
    "multiseed": {"protein_counts_validation": {}},
    "multivariant": {"doubling_time_hist": {}},
    "parca": {"expression_analysis": {}}
  }
}
```

Analysis scope levels:
- `single` - Individual cell data
- `multidaughter` - Sister cell comparisons
- `multigeneration` - Multi-generational lineages
- `multiseed` - Cross-replicate analysis
- `multivariant` - Variant comparison
- `parca` - Parameter calculator analysis

## Output Structure

```
{out_dir}/{experiment_id}/
├── nextflow/
│   ├── main.nf                    # Generated workflow
│   ├── nextflow.config            # Nextflow configuration
│   ├── workflow_config.json       # Full runtime config
│   ├── nextflow_job.sh            # SBATCH script (HPC)
│   └── nextflow_workdirs/         # Task working directories
├── parca/
│   └── kb/
│       ├── simData.cPickle
│       ├── rawData.cPickle
│       └── ...
├── variant_sim_data/              # Variant parameters
│   ├── metadata.json
│   ├── 0.cPickle
│   └── ...
├── history/                       # Simulation output (Parquet)
├── daughter_states/               # Cell division outputs
└── analysis/                      # Analysis results
```

## Error Handling and Recovery

### Automatic Retries

Nextflow automatically retries tasks on:
- Exit code 137: OOM (memory scaled up)
- Exit code 140: SLURM time limit (time scaled up)
- Exit code 143: SLURM preemption
- Max integer: Unknown errors

### Resume Failed Workflow

```bash
python3 runscripts/workflow.py --config config.json --resume my-exp_20250115-120000
```

This:
1. Skips image building
2. Skips workflow file generation
3. Adds `-resume` to Nextflow command
4. Reuses cached task outputs

## Jenkins CI Integration

Jenkins pipelines are defined in `runscripts/jenkins/Jenkinsfile/`. The workflow wrapper script is `runscripts/jenkins/workflow.sh`.

**Jenkins-specific config:**
```json
{
  "sherlock": {
    "jenkins": true,      // Adds #SBATCH --wait, streams output
    "build_image": true
  }
}
```

## Important Notes

1. **Sherlock Performance:** Set `"threaded": false` in `emitter_arg` when using Parquet emitter on Sherlock (each sim gets 1 CPU).

2. **Output Directory:** Must use absolute paths for `out_dir`. On Sherlock, use `$SCRATCH` paths (e.g., `/scratch/users/username`).

3. **Experiment ID:** Cannot contain special characters that change when URL-encoded.

4. **Sim Data Reuse:** Set `sim_data_path` to skip ParCa and reuse existing simulation data.

5. **Environment Variables for CCAM/AWS:** Load from `.hpc_env` file (uses `python-dotenv`).

## File Reference

| File | Purpose |
|------|---------|
| `runscripts/workflow.py` | Main workflow orchestrator |
| `runscripts/sim.py` | Single simulation runner |
| `runscripts/analysis.py` | Analysis script |
| `runscripts/parca.py` | Parameter calculator runner |
| `runscripts/create_variants.py` | Variant generator |
| `runscripts/nextflow/config.template` | Nextflow config template |
| `runscripts/nextflow/template.nf` | Workflow template |
| `runscripts/nextflow/sim.nf` | Simulation process definitions |
| `runscripts/nextflow/analysis.nf` | Analysis process definitions |
| `runscripts/container/Dockerfile` | Docker image definition |
| `runscripts/container/Singularity` | Apptainer image definition |
| `runscripts/container/build-image.sh` | Container build script |
| `runscripts/container/interactive.sh` | Interactive container launcher |
| `configs/default.json` | Default configuration |

## Quick Start Examples

### Local Test
```bash
python3 runscripts/workflow.py --config configs/test_installation.json
```

### Sherlock Multi-Generation
```bash
# Create config
cat > my_config.json << 'EOF'
{
  "experiment_id": "lineage-test",
  "generations": 3,
  "n_init_sims": 2,
  "emitter": "parquet",
  "emitter_arg": {
    "out_dir": "/scratch/users/myuser/vecoli",
    "threaded": false
  },
  "sherlock": {
    "container_image": "/oak/stanford/groups/mcovert/images/vecoli.sif",
    "build_image": true
  }
}
EOF

python3 runscripts/workflow.py --config my_config.json
```

### Google Cloud
```bash
python3 runscripts/workflow.py --config configs/cloud.json
```

### AWS CDK SLURM
```bash
# Set environment variables
export SLURM_PARTITION=jobs-queue
export SLURM_LOG_BASE_PATH=/mnt/fsx/logs

# Activate virtual environment
source .venv/bin/activate

# Create config
cat > configs/test_aws_cdk.json << 'EOF'
{
  "experiment_id": "test_aws_cdk",
  "suffix_time": false,
  "parca_options": {"cpus": 2},
  "generations": 1,
  "n_init_sims": 1,
  "emitter": "parquet",
  "emitter_arg": {"out_dir": "/mnt/fsx/output"},
  "aws_cdk": {
    "container_image": "/mnt/fsx/images/vecoli.sif",
    "build_image": false
  },
  "analysis_options": {
    "single": {"mass_fraction_summary": {}}
  }
}
EOF

# Run workflow
python runscripts/workflow.py --config configs/test_aws_cdk.json
```

## Known Issues and Fixes

### AWS CDK Profile Fixes (January 2025)

The original `aws_cdk` profile required several fixes to work properly:

1. **Singularity enablement**: Changed `singularity.enabled = false` to `true` in `config.template`

2. **Resource allocation**: Updated process settings to match `ccam` profile:
   - Memory: 4GB base, scales on OOM
   - Time: 4h base, scales on timeout
   - Added parca-specific settings with 2GB per CPU

3. **Main job time limit**: Fixed in `workflow.py` - changed `#SBATCH --time=07:00` to `#SBATCH --time=7-00:00:00`

4. **Variable reference bug**: Fixed `ccam_config.get("wait")` to `aws_cdk_config.get("wait")` in the aws_cdk section of `workflow.py`
