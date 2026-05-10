#!/usr/bin/env bash
# Bootstrap a head dedicated to v1↔v2 comparison work (parity scans, report).
# Runs ON the head node (AL2023 ARM64) after setup_head_node.sh.
#
# Differs from bootstrap_head.sh: no docker, no nextflow, no ECR — comparison
# is pure Python over S3 parquet via pyarrow + polars. Just installs uv,
# clones vEcoli, and configures AWS region so in-region S3 reads are free.
#
# Idempotent: safe to re-run.

set -euo pipefail

VECOLI_REPO="${VECOLI_REPO:-https://github.com/vivarium-collective/vEcoli.git}"
VECOLI_BRANCH="${VECOLI_BRANCH:-composite}"
VECOLI_DIR="${HOME}/vEcoli"
REGION="us-gov-west-1"

# --- 1. system packages -----------------------------------------------------
# pandoc is NOT in AL2023's default repos and is only needed for
# ``compare export`` (markdown → html/pdf). The .md report itself
# renders fine on GitHub. Install on demand: download the static
# binary from pandoc.org if/when export is needed.
sudo dnf -y update
sudo dnf -y install git tmux gcc gcc-c++ make

# --- 2. AWS region default --------------------------------------------------
aws configure set region "$REGION"

# --- 3. uv (Python package manager) -----------------------------------------
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
  # uv installs to $HOME/.local/bin; make sure that's on PATH for this script
  # AND for subsequent ssh sessions.
  if ! grep -q '\.local/bin' "$HOME/.bashrc" 2>/dev/null; then
    echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$HOME/.bashrc"
  fi
  export PATH="$HOME/.local/bin:$PATH"
fi

# --- 4. Clone vEcoli --------------------------------------------------------
if [[ ! -d "$VECOLI_DIR" ]]; then
  git clone "$VECOLI_REPO" "$VECOLI_DIR"
fi
cd "$VECOLI_DIR"
git fetch origin
git checkout "$VECOLI_BRANCH"
git pull --ff-only origin "$VECOLI_BRANCH"

# --- 5. Pre-sync uv environment so the first ``compare`` command is fast ----
# ``--extra aws`` pulls boto3 + botocore[crt] from uv.lock for in-region
# S3 reads. Matches bootstrap_head.sh / bootstrap_head_mp.sh pattern.
uv sync --frozen --extra aws --no-dev \
  || uv sync --extra aws --no-dev \
  || true

echo "Compare head ready (uv synced, vEcoli at $(git rev-parse --short HEAD 2>/dev/null || echo "?"))."
