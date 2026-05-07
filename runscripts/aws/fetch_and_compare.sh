#!/usr/bin/env bash
# Pull v1 + v2 workflow outputs from S3 and render the comparison report.
# Run this on the head node (or anywhere with AWS creds for the bucket and a
# vEcoli checkout) AFTER both workflows have completed.
#
# Downloads only what runscripts/v1_v2_report.py actually needs:
#   - analyses/ (small: HTML plots + TSV tables)
#   - nextflow/nextflow_workdirs/**/.command.sh + division_time.sh
#     (text-only, used to extract per-seed/gen division times)
#   - top-level trace--<exp>--*.csv if Nextflow uploaded one
# Everything else (history/parquet, daughter_states, parca/kb) stays in S3.
#
# Usage:
#   runscripts/aws/fetch_and_compare.sh                    # use defaults
#   V2_ID=other_exp runscripts/aws/fetch_and_compare.sh    # override v2 id
#
# Variables (override via env):
#   V1_ID, V2_ID      experiment ids (S3 prefix names)
#   BUCKET, PREFIX    S3 location
#   SEEDS, GENS       comma-lists for per-cell mass_fraction plots in report

set -euo pipefail

V1_ID="${V1_ID:-comparison_10s_16g_v1_aws}"
V2_ID="${V2_ID:-comparison_10s_16g_v2_aws_listener_fix}"
BUCKET="${BUCKET:-smsvpctest-shared-sharedbucket60d199d6-abfvwv0day91}"
PREFIX="${PREFIX:-vecoli-output}"
SEEDS="${SEEDS:-0,1}"
GENS="${GENS:-1,2}"

# Resolve repo root from this script's location (works whether invoked from
# repo root or via absolute path).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${REPO_ROOT}"

aws_sync() {
  # aws s3 sync with --include filters and --exclude default
  aws s3 sync "$1" "$2" "${@:3}" --no-progress --only-show-errors
}

for exp in "${V1_ID}" "${V2_ID}"; do
  echo "=== Fetching ${exp} ==="
  base_remote="s3://${BUCKET}/${PREFIX}/${exp}/${exp}"
  base_local="out/${exp}"
  mkdir -p "${base_local}"

  # 1. Analyses: small HTML/TSV plots, the report renders these to PNG
  aws_sync "${base_remote}/analyses/" "${base_local}/analyses/"

  # 2. Nextflow workdirs: only the text files used for division-time scraping
  aws_sync "${base_remote}/nextflow/nextflow_workdirs/" \
           "${base_local}/nextflow/nextflow_workdirs/" \
           --exclude "*" \
           --include "*.command.sh" \
           --include "*division_time.sh"

  # 3. Top-level trace CSV (best-effort — atlantis-driven runs may not have one)
  aws s3 cp "${base_remote}/nextflow/" "${REPO_ROOT}/" \
    --recursive \
    --exclude "*" \
    --include "trace--${exp}--*.csv" \
    --no-progress --only-show-errors 2>/dev/null || true

  echo "  Local: ${base_local}/"
done

# 4. Optional: compute the all-timestep bulk parity matrix. INCLUDE_HISTORY=1
# triggers per-cell s3 sync + diff (~30-60 min on the head, in-region S3).
# The matrix file is incremental — re-running adds missing cells only.
if [[ "${INCLUDE_HISTORY:-0}" == "1" ]]; then
  echo
  echo "=== Computing bulk parity matrix ==="
  PARITY_TSV="out/parity_matrix__${V1_ID}__${V2_ID}.tsv"
  if command -v uv >/dev/null; then PY="uv run --no-sync python"; else PY="python"; fi
  $PY runscripts/aws/compute_parity_matrix.py \
    --v1-id "${V1_ID}" --v2-id "${V2_ID}" \
    --bucket "${BUCKET}" --prefix "${PREFIX}" \
    --seeds "${SEEDS}" --gens "${GENS}" \
    --output "${PARITY_TSV}"
  PARITY_FLAG=( --parity-matrix "${PARITY_TSV}" )
else
  PARITY_FLAG=()
fi

echo
echo "=== Generating report ==="
if command -v uv >/dev/null; then PY="uv run --no-sync python"; else PY="python"; fi
$PY runscripts/v1_v2_report.py \
  --v1-id "${V1_ID}" --v2-id "${V2_ID}" \
  --seeds "${SEEDS}" --gens "${GENS}" \
  "${PARITY_FLAG[@]}"

echo
echo "Report:  doc/v1_v2_report.md"
echo "Assets:  doc/_static/v1_v2_report_assets/"
