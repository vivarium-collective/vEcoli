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
#   EXTRA_IDS="mp=cmp_v2_mp,ray=cmp_v2_ray" \              # 4-way: pull MP/Ray
#     runscripts/aws/fetch_and_compare.sh                  #   traces too
#
# Variables (override via env):
#   V1_ID, V2_ID      experiment ids (S3 prefix names)
#   EXTRA_IDS         comma-separated additional experiment ids for
#                     N-way wall-clock comparison (composite_lineage MP,
#                     Ray, etc.). Each can be ``label=experiment_id``.
#   BUCKET, PREFIX    S3 location
#   SEEDS, GENS       comma-lists for per-cell mass_fraction plots in report

set -euo pipefail

V1_ID="${V1_ID:-comparison_10s_16g_v1_aws}"
V2_ID="${V2_ID:-comparison_10s_16g_v2_aws_listener_fix}"
BUCKET="${BUCKET:-smsvpctest-shared-sharedbucket60d199d6-abfvwv0day91}"
PREFIX="${PREFIX:-vecoli-output}"
SEEDS="${SEEDS:-0,1}"
GENS="${GENS:-1,2}"
EXTRA_IDS="${EXTRA_IDS:-}"
# ENGINE_COST: per-engine cost spec for engines without trace CSVs
# (mp/ray). See runscripts/v1_v2_report.py --engine-cost docs.
# Example:
# ENGINE_COST="mp=single:c7g.metal:1200,ray=cluster:t4g.large:c7g.metal:4:800"
ENGINE_COST="${ENGINE_COST:-}"

# Resolve repo root from this script's location (works whether invoked from
# repo root or via absolute path).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${REPO_ROOT}"

aws_sync() {
  # aws s3 sync with --include filters and --exclude default
  aws s3 sync "$1" "$2" "${@:3}" --no-progress --only-show-errors
}

# Extract bare experiment_ids from EXTRA_IDS (which may use
# ``label=exp_id`` format) for the fetch loop.
extra_ids_bare=""
if [[ -n "${EXTRA_IDS}" ]]; then
  extra_ids_bare=$(echo "${EXTRA_IDS}" \
    | tr ',' '\n' \
    | sed -E 's/^[^=]*=//' \
    | tr '\n' ' ')
fi

for exp in "${V1_ID}" "${V2_ID}" ${extra_ids_bare}; do
  [[ -z "${exp}" ]] && continue
  # S3 layout is ``<prefix>/<base>/<full_exp_id>/...`` where base is the
  # config's ``experiment_id`` field (no timestamp) and full_exp_id is
  # base + ``_YYYYMMDD-HHMMSS`` after run launch. Strip the suffix to
  # get the base; legacy IDs without a suffix pass through unchanged.
  base="${exp%_[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]-[0-9][0-9][0-9][0-9][0-9][0-9]}"
  base_remote="s3://${BUCKET}/${PREFIX}/${base}/${exp}"
  base_local="out/${exp}"
  mkdir -p "${base_local}"

  if [[ "${NO_FETCH:-0}" == "1" ]]; then
    echo "=== Skipping fetch for ${exp} (NO_FETCH=1) ==="
    if [[ ! -d "${base_local}/analyses" ]]; then
      echo "  WARNING: ${base_local}/analyses missing — report will show (missing) plots." >&2
    fi
    continue
  fi
  echo "=== Fetching ${exp} ==="

  # 1. Analyses: small HTML/TSV plots, the report renders these to PNG
  aws_sync "${base_remote}/analyses/" "${base_local}/analyses/"

  # 2. Nextflow workdirs: only the text files used for division-time scraping
  aws_sync "${base_remote}/nextflow/nextflow_workdirs/" \
           "${base_local}/nextflow/nextflow_workdirs/" \
           --exclude "*" \
           --include "*.command.sh" \
           --include "*division_time.sh"

  # 3. Top-level trace CSV (best-effort — atlantis-driven runs may not
  #    have one; composite_lineage MP/Ray runners emit a synthetic
  #    trace + cost_meta sidecar via runscripts/synthetic_trace.py).
  aws s3 cp "${base_remote}/nextflow/" "${REPO_ROOT}/" \
    --recursive \
    --exclude "*" \
    --include "trace--${exp}--*.csv" \
    --include "cost_meta--${exp}.json" \
    --no-progress --only-show-errors 2>/dev/null || true

  echo "  Local: ${base_local}/"
done

# 4. Optional: compute the all-timestep bulk parity matrix. INCLUDE_HISTORY=1
# triggers per-cell s3 sync + diff (~30-60 min on the head, in-region S3).
# The matrix file is incremental — re-running adds missing cells only.
# N-way: one matrix per (v1, other_engine) pair. ``--parity-matrix``
# accepts comma-separated paths so the report renders each side-by-side.
if [[ "${INCLUDE_HISTORY:-0}" == "1" ]]; then
  echo
  if command -v uv >/dev/null; then PY="uv run --no-sync python"; else PY="python"; fi
  PARITY_PATHS=()
  # Pair v1 against v2 + each extra engine
  PAIRS=("v2:${V2_ID}")
  for raw in $(echo "${EXTRA_IDS}" | tr ',' '\n'); do
    [[ -z "$raw" ]] && continue
    label="${raw%%=*}"
    eid="${raw#*=}"
    [[ -z "$eid" ]] && eid="$label"
    PAIRS+=("${label}:${eid}")
  done
  for pair in "${PAIRS[@]}"; do
    label="${pair%%:*}"
    other_id="${pair#*:}"
    echo "=== Computing parity matrix v1 vs ${label} (${other_id}) ==="
    OUT_TSV="out/parity_matrix__v1__${label}.tsv"
    $PY runscripts/aws/compute_parity_matrix.py \
      --v1-id "${V1_ID}" --v2-id "${other_id}" \
      --bucket "${BUCKET}" --prefix "${PREFIX}" \
      --seeds "${SEEDS}" --gens "${GENS}" \
      --output "${OUT_TSV}" || {
        echo "  parity for ${label} failed (likely missing parquet) — skipping" >&2
        continue
      }
    PARITY_PATHS+=("${OUT_TSV}")
  done
  if (( ${#PARITY_PATHS[@]} > 0 )); then
    # IFS-join into the single comma-separated string the report expects
    saved_IFS=$IFS; IFS=,
    PARITY_FLAG=( --parity-matrix "${PARITY_PATHS[*]}" )
    IFS=$saved_IFS
  else
    PARITY_FLAG=()
  fi
else
  PARITY_FLAG=()
fi

echo
echo "=== Generating report ==="
if command -v uv >/dev/null; then PY="uv run --no-sync python"; else PY="python"; fi
EXTRA_FLAG=()
[[ -n "${EXTRA_IDS}" ]] && EXTRA_FLAG=(--extra-ids "${EXTRA_IDS}")
COST_FLAG=()
[[ -n "${ENGINE_COST}" ]] && COST_FLAG=(--engine-cost "${ENGINE_COST}")
# REPORT_OUT controls the output path (default doc/v1_v2_report.md);
# v1_v2_report.py derives the assets dir from the file stem.
REPORT_OUT="${REPORT_OUT:-doc/v1_v2_report.md}"
OUT_FLAG=( --out "${REPORT_OUT}" )
$PY runscripts/v1_v2_report.py \
  --v1-id "${V1_ID}" --v2-id "${V2_ID}" \
  --seeds "${SEEDS}" --gens "${GENS}" \
  "${OUT_FLAG[@]}" \
  "${EXTRA_FLAG[@]}" "${COST_FLAG[@]}" \
  "${PARITY_FLAG[@]}"

REPORT_STEM="$(basename "${REPORT_OUT}" .md)"
REPORT_DIR="$(dirname "${REPORT_OUT}")"
echo
echo "Report:  ${REPORT_OUT}"
echo "Assets:  ${REPORT_DIR}/_static/${REPORT_STEM}_assets/"
