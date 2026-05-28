#!/usr/bin/env bash
# Generate the missing ``analyses/`` plots for an experiment whose
# runner skipped the analysis step (Ray, MP). Drives
# runscripts/analysis.py once over the experiment's parquet output,
# then uploads each ``analysis=<name>`` leaf to S3 under the
# experiment's ``analyses/`` prefix in the layout the Nextflow
# publishDir would have produced — which is what
# runscripts/v1_v2_report.py expects to find when rendering the N-way
# report.
#
# Runs ON the compare head (which has uv + .venv with
# numpy/polars/duckdb from bootstrap_head_compare.sh) and is invoked
# via the ``compare analyze`` subcommand of vecoli_aws.sh. The CLI
# resolves alias → config + exp_id + sim_data_uri + bucket/prefix and
# threads them in via env vars before running this script over SSH.
#
# Required env vars:
#   CONFIG_RELPATH    config path relative to ~/vEcoli (e.g.
#                     ``configs/comparison_10s_16g_v2_ray_aws.json``).
#   EXP_ID            stamped experiment_id (e.g.
#                     ``..._20260527-231830``).
#   SIM_DATA_URI      s3:// URI of the simData.cPickle the run used.
#   BUCKET            output bucket name (no scheme / trailing slash).
#   PREFIX            output prefix-with-base under the bucket
#                     (e.g. ``vecoli-output/comparison_10s_16g_v2_ray_aws``).
#
# Optional:
#   VALIDATION_URI    validation data path. Defaults to
#                     ``${SIM_DATA_URI%/*}/validationData.cPickle``.
#   ANALYSIS_NAME     space-separated subset of analysis script names
#                     (default: every entry in the config's
#                     analysis_options).
#   ANALYSIS_TYPES    space-separated subset of analysis types — e.g.
#                     ``single multiseed`` (default: every type in
#                     analysis_options).
#   CPUS              cpus for DuckDB threadpool (default: 4).

set -euo pipefail

REPO_ROOT="${HOME}/vEcoli"
cd "$REPO_ROOT"

# Same Python resolution as fetch_and_compare.sh — prefer the
# bootstrap-created .venv directly so we don't depend on PATH.
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
if [[ -x "${REPO_ROOT}/.venv/bin/python" ]]; then
  PY=( "${REPO_ROOT}/.venv/bin/python" )
elif command -v uv >/dev/null; then
  PY=( uv run --no-sync python )
else
  echo "ERROR: no project Python on compare head." >&2
  echo "Run: runscripts/aws/vecoli_aws.sh compare bootstrap" >&2
  exit 1
fi
echo "Using Python: ${PY[*]}"

: "${CONFIG_RELPATH:?CONFIG_RELPATH required}"
: "${EXP_ID:?EXP_ID required}"
: "${SIM_DATA_URI:?SIM_DATA_URI required}"
: "${BUCKET:?BUCKET required}"
: "${PREFIX:?PREFIX required}"
VALIDATION_URI="${VALIDATION_URI:-${SIM_DATA_URI%/*}/validationData.cPickle}"
CPUS="${CPUS:-4}"

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
PLOTS="$WORK/plots"
META="$WORK/metadata.json"
mkdir -p "$PLOTS"
# Synthetic variant metadata for a single-variant lineage run.
# analysis.py reads metadata.keys()[0] as the variant name and
# {"0": "baseline"} maps variant 0 → "baseline".
printf '{"null": {"0": "baseline"}}\n' > "$META"

ANALYSIS_ARGS=()
if [[ -n "${ANALYSIS_NAME:-}" ]]; then
  ANALYSIS_ARGS+=(--analysis_name $ANALYSIS_NAME)
fi
if [[ -n "${ANALYSIS_TYPES:-}" ]]; then
  ANALYSIS_ARGS+=(-t $ANALYSIS_TYPES)
fi

echo "Running analysis.py for $EXP_ID"
echo "  config:          $CONFIG_RELPATH"
echo "  sim_data:        $SIM_DATA_URI"
echo "  validation_data: $VALIDATION_URI"
echo "  outdir:          $PLOTS"
"${PY[@]}" runscripts/analysis.py \
  --config "$CONFIG_RELPATH" \
  --experiment_id "$EXP_ID" \
  --variant 0 \
  --sim_data_path "$SIM_DATA_URI" \
  --validation_data_path "$VALIDATION_URI" \
  --variant_metadata_path "$META" \
  --cpus "$CPUS" \
  -o "$PLOTS" \
  "${ANALYSIS_ARGS[@]}"

# analysis.py output structure:
#   $PLOTS/experiment_id=<EXP_ID>/<middle>/analysis=<NAME>/<files>
# where <middle> is empty (multivariant), or
# ``variant=N`` (multiseed), ``variant=N/lineage_seed=S``
# (multigeneration), or ``variant=N/lineage_seed=S/generation=G/
# agent_id=A`` (single).
#
# Nextflow publishDir layout (what v1_v2_report.py expects):
#   s3://$BUCKET/$PREFIX/$EXP_ID/analyses/<middle>/plots/analysis=<NAME>/
#
# Walk each ``analysis=*`` leaf, strip ``experiment_id=<EXP_ID>/`` from
# the front, insert ``plots/`` before the analysis segment, and copy.
EXP_OUT="$PLOTS/experiment_id=$EXP_ID"
if [[ ! -d "$EXP_OUT" ]]; then
  echo "ERROR: analysis.py produced nothing at $EXP_OUT" >&2
  echo "       (expected at least one analysis= leaf — check analysis.py logs above.)" >&2
  exit 1
fi

echo
echo "Uploading plots to s3://$BUCKET/$PREFIX/$EXP_ID/analyses/..."
count=0
while IFS= read -r leaf; do
  rel="${leaf#$EXP_OUT/}"            # variant=…/…/analysis=NAME  or  analysis=NAME
  analysis_part="${rel##*/}"         # analysis=NAME
  if [[ "$analysis_part" == "$rel" ]]; then
    middle=""                        # multivariant — leaf is direct child
  else
    middle="${rel%/$analysis_part}"  # everything before the trailing analysis=NAME
  fi
  if [[ -z "$middle" ]]; then
    s3_path="s3://$BUCKET/$PREFIX/$EXP_ID/analyses/plots/$analysis_part"
  else
    s3_path="s3://$BUCKET/$PREFIX/$EXP_ID/analyses/$middle/plots/$analysis_part"
  fi
  echo "  + $rel  →  $s3_path"
  aws s3 cp --recursive --no-progress --only-show-errors "$leaf" "$s3_path"
  count=$((count + 1))
done < <(find "$EXP_OUT" -type d -name 'analysis=*')

echo
echo "Uploaded $count analysis dir(s) to s3://$BUCKET/$PREFIX/$EXP_ID/analyses/"
