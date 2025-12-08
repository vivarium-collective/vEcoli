#!/bin/bash
#SBATCH --time=2:00:00
#SBATCH --cpus-per-task 1
#SBATCH --mem=4GB
#SBATCH --partition=vivarium
#SBATCH --qos=vivarium
#SBATCH --output=/home/FCAM/svc_vivarium/prod/htclogs/submit-nf-sms_ccam.out
#SBATCH --nodelist=mantis-039

set -e

## set up java and nextflow
local_bin=$HOME/.local/bin
export JAVA_HOME=$local_bin/java-22
export PATH=$JAVA_HOME/bin:$local_bin:$PATH

## use single thread for BLAS for better performance and reproducibility
export OMP_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export VECLIB_MAXIMUM_THREADS=1

# latest_hash=079c43c
vecoli_dir=/home/FCAM/svc_vivarium/workspace/vEcoli

config_path="${vecoli_dir}/configs/sms_ccam.json"
# interpreter="${vecoli_dir}/.venv/bin/python"

container_image=/home/FCAM/svc_vivarium/workspace/images/vecoli-079c43c.sif
runscript=/home/FCAM/svc_vivarium/workspace/vEcoli/runscripts/container/interactive_ccam.sh
chmod +x $runscript

# -p "${HOME}/workspace/api_outputs:/out:rw" \
"${runscript}" -i "${container_image}" \
    -a \
    -p "${HOME}/workspace/vEcoli:/vEcoli" \
    -p "${HOME}/workspace/api_outputs:/tmp/out:rw" \
    -p "${JAVA_HOME}:${JAVA_HOME}" \
    -p "${HOME}/.local/bin:${HOME}/.local/bin" \
    -p "${HOME}/.local/share/uv:/home/FCAM/svc_vivarium/.local/share/uv" \
    -c "uv run /vEcoli/runscripts/workflow.py --config ${config_path} --outdir /tmp/out"


