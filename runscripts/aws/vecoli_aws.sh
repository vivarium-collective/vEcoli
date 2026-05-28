#!/usr/bin/env bash
# vEcoli AWS workflow CLI (unified namespaced version).
#
# Single entry point for everything we keep running by hand: provision a
# head, build/push the Docker image, launch a workflow, monitor progress,
# compare outputs across paths.
#
# Variant (Nextflow / MP-single-node / Ray-cluster) is the FIRST
# positional after the subcmd (e.g. ``run launch v1``). The CLI is a
# thin dispatcher; the per-variant work lives in
# ``bootstrap_head[_mp|_ray].sh`` under this directory. Adding a new
# variant means: new config + new bootstrap script + alias entries in
# ``_alias_to_config`` and ``_state_key_for``. No top-level commands
# to add.
#
# Each variant has its own auto-managed experiment_id at
# ``.vecoli-aws-state/<variant>.experiment-id`` (gitignored). ``run
# launch <variant>`` rotates the ID; downstream subcmds read it.
#
# Namespaces:
#   head     EC2 head-node lifecycle (setup, start, stop, ssh, ...)
#   image    Docker image lifecycle (build, push, pull, list)
#   run      workflow execution (launch, resume, cancel, status, tail)
#   cache    Nextflow .nextflow/ S3 cache (push, pull, ls, rm)
#   compare  output analysis (parity matrix, report, export)
#
# Legacy top-level commands (setup, launch, status, ...) still work —
# they forward to the namespaced equivalents.
#
# Usage: runscripts/aws/vecoli_aws.sh <namespace> <subcmd> [args]
#        runscripts/aws/vecoli_aws.sh help

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# --- 1. Global env (variant-independent) ------------------------------------
PROFILE="${VECOLI_AWS_PROFILE:-stanford-sso}"
REGION="${VECOLI_AWS_REGION:-us-gov-west-1}"
KEY_FILE="${VECOLI_AWS_KEY:-$HOME/.ssh/vecoli-head-key.pem}"
STATE_DIR="${REPO_ROOT}/.vecoli-aws-state"
ACCOUNT_ID=""  # lazy — only computed when needed (image push)

# Legacy/manual overrides — leave unset to let _use_variant pick
# defaults from the config's deploy_mode.
HEAD_NAME_OVERRIDE="${VECOLI_AWS_HEAD_NAME:-}"
TMUX_SESSION_OVERRIDE="${VECOLI_AWS_TMUX:-}"
HEAD_INSTANCE_TYPE_OVERRIDE="${HEAD_INSTANCE_TYPE:-}"

aws_cli() { aws --profile "$PROFILE" --region "$REGION" "$@"; }

# --- 2. Alias registry (dynamic) -------------------------------------------
# All variant-specific subcmds (head, run, cache) take an alias as their
# first positional arg, e.g. ``run launch v1``. The alias keys into a
# config via the registry at $STATE_DIR/aliases.tsv (TSV: <alias>\t<config>).
# Pre-seeded on first read with the four built-in entries (v1/v2/mp/ray);
# add more with ``experiment new <alias> <config>``. A literal config
# path (``configs/foo.json``) also works as an ad-hoc one-shot. Falls
# back to VECOLI_AWS_CONFIG env var if neither is supplied.
_REGISTRY="$STATE_DIR/aliases.tsv"
# Registry schema: TSV with 4 columns — alias, config, method, image_tag.
# ``method`` is one of: batch | multiprocessing | ray | ray_colony | comparison (canonical), and
# determines the bootstrap script + default head instance type at
# ``head setup`` / ``run launch`` time.
# ``image_tag`` is the Docker tag (e.g. ``vecoli:v2-comparison-arm64``)
# associated with the alias. Used by ``image build/push/pull <alias>``,
# and (for ray) auto-resolved into IMAGE_URI for the worker cluster.
# May be empty for aliases that don't use an image (e.g. mp).
# Built-in defaults — written to the registry on first read so the user
# starts with the same 4 entries the original hardcoded version had.
_seed_registry_if_missing() {
  if [[ ! -f "$_REGISTRY" ]]; then
    mkdir -p "$STATE_DIR"
    cat > "$_REGISTRY" <<'EOF'
v1	configs/comparison_10s_16g_v1_aws.json	batch	vecoli:v2-comparison-arm64
v2	configs/comparison_10s_16g_v2_aws.json	batch	vecoli:v2-comparison-arm64
mp	configs/comparison_10s_16g_v2_mp_aws.json	multiprocessing
ray	configs/comparison_10s_16g_v2_ray_aws.json	ray	vecoli:v2-ray-arm64
compare	configs/compare_head.json	comparison
EOF
    echo "Seeded $_REGISTRY with default aliases (v1/v2/mp/ray/compare)" >&2
  fi
  # Backfill ``compare`` for users whose registry was seeded before
  # this alias existed; idempotent.
  if [[ -f "$_REGISTRY" ]] && ! grep -qE '^compare\s' "$_REGISTRY" 2>/dev/null; then
    printf 'compare\tconfigs/compare_head.json\tcomparison\n' >> "$_REGISTRY"
    echo "Backfilled 'compare' alias in $_REGISTRY" >&2
  fi
  _migrate_registry_2col_to_3col
  _migrate_registry_3col_to_4col
}

# Migrate aliases.tsv from the older 2-column format (alias, config) to
# the 3-column format (alias, config, method). Fills method from a
# heuristic on alias name when not present.
_migrate_registry_2col_to_3col() {
  [[ -f "$_REGISTRY" ]] || return 0
  local first; first=$(head -1 "$_REGISTRY" 2>/dev/null || echo "")
  [[ -z "$first" ]] && return 0
  local cols; cols=$(printf '%s' "$first" | awk -F'\t' '{print NF}')
  if [[ "$cols" -ge 3 ]]; then return 0; fi  # already migrated
  echo "Migrating $_REGISTRY 2→3 cols (adding method column)..." >&2
  local tmp; tmp=$(mktemp)
  while IFS=$'\t' read -r a cfg _; do
    [[ -z "$a" ]] && continue
    local m=""
    case "$a" in
      v*)              m="batch" ;;
      mp*)             m="multiprocessing" ;;
      ray*)            m="ray" ;;
      *)
        echo "  WARNING: alias '$a' has no inferable method; defaulting to 'batch'." >&2
        echo "    Override with: $(basename "$0") head setup $a <method>" >&2
        m="batch" ;;
    esac
    printf '%s\t%s\t%s\n' "$a" "$cfg" "$m" >> "$tmp"
  done < "$_REGISTRY"
  mv "$tmp" "$_REGISTRY"
}

# Migrate 3-column to 4-column (adding image_tag). Defaults inferred
# from method: batch → vecoli:v2-comparison-arm64 (matches existing v1/v2
# configs); ray → vecoli:v2-ray-arm64; multiprocessing → empty (no
# image needed for the local-venv MP runner).
_migrate_registry_3col_to_4col() {
  [[ -f "$_REGISTRY" ]] || return 0
  local first; first=$(head -1 "$_REGISTRY" 2>/dev/null || echo "")
  [[ -z "$first" ]] && return 0
  local cols; cols=$(printf '%s' "$first" | awk -F'\t' '{print NF}')
  if [[ "$cols" -ge 4 ]]; then return 0; fi
  echo "Migrating $_REGISTRY 3→4 cols (adding image_tag column)..." >&2
  local tmp; tmp=$(mktemp)
  while IFS=$'\t' read -r a cfg m _; do
    [[ -z "$a" ]] && continue
    local img=""
    case "$m" in
      batch)           img="vecoli:v2-comparison-arm64" ;;
      ray)             img="vecoli:v2-ray-arm64" ;;
      multiprocessing) img="" ;;
      *)               img="" ;;
    esac
    printf '%s\t%s\t%s\t%s\n' "$a" "$cfg" "$m" "$img" >> "$tmp"
  done < "$_REGISTRY"
  mv "$tmp" "$_REGISTRY"
}
# Look up an alias's config path. Returns "" if not found and the arg
# isn't a path. Trailing whitespace stripped.
_alias_to_config() {
  local key="$1"
  _seed_registry_if_missing
  # Tab-separated: <alias>\t<config_path>; pull the second column when
  # the first column matches exactly. awk cleanly handles whitespace.
  local cfg
  cfg=$(awk -F'\t' -v k="$key" '$1==k { print $2; exit }' "$_REGISTRY")
  if [[ -n "$cfg" ]]; then
    echo "$cfg"; return 0
  fi
  # Fallback: an explicit path (allows ``run launch configs/foo.json``)
  if [[ -f "$REPO_ROOT/$1" ]]; then
    echo "$1"; return 0
  fi
  echo ""
}
# Reverse: state-file key for a config path. Looks up the registry to
# find which alias maps to this config; if multiple, returns the first.
# If none, returns basename(config) sans .json (for ad-hoc paths).
_state_key_for() {
  local cfg="$1"
  _seed_registry_if_missing
  local k
  k=$(awk -F'\t' -v c="$cfg" '$2==c { print $1; exit }' "$_REGISTRY")
  if [[ -n "$k" ]]; then
    echo "$k"
  else
    basename "$cfg" .json
  fi
}
# List all registered aliases (one per line, "<alias>\t<config>"). Used
# by ``experiment list`` and the dashboard.
_registry_list() {
  _seed_registry_if_missing
  cat "$_REGISTRY"
}
# Idempotent registry write: add or update <alias> → <config> + <method>
# + <image_tag>. Any of method/image_tag passed as "" preserves the
# existing value (so callers can update one column without clobbering
# the other).
_registry_set() {
  local key="$1" cfg="$2" method="${3:-}" image="${4:-}"
  _seed_registry_if_missing
  # Carry over existing values for fields the caller left blank.
  if [[ -z "$method" ]]; then
    method=$(awk -F'\t' -v k="$key" '$1==k { print $3; exit }' "$_REGISTRY")
  fi
  if [[ -z "$image" ]]; then
    image=$(awk -F'\t' -v k="$key" '$1==k { print $4; exit }' "$_REGISTRY")
  fi
  local tmp; tmp=$(mktemp)
  awk -F'\t' -v k="$key" '$1!=k { print }' "$_REGISTRY" > "$tmp"
  printf '%s\t%s\t%s\t%s\n' "$key" "$cfg" "$method" "$image" >> "$tmp"
  mv "$tmp" "$_REGISTRY"
}
# Remove an alias from the registry. No-op if absent.
_registry_unset() {
  local key="$1"
  _seed_registry_if_missing
  local tmp; tmp=$(mktemp)
  awk -F'\t' -v k="$key" '$1!=k { print }' "$_REGISTRY" > "$tmp"
  mv "$tmp" "$_REGISTRY"
}
# Lookup the method for an alias. Returns "" if alias unknown or method
# column empty.
_alias_to_method() {
  local key="$1"
  _seed_registry_if_missing
  awk -F'\t' -v k="$key" '$1==k { print $3; exit }' "$_REGISTRY"
}
# Lookup the image_tag (e.g. ``vecoli:v2-comparison-arm64``) registered
# for an alias. Returns "" if alias unknown or image column empty (e.g.
# multiprocessing aliases don't use an image).
_alias_to_image() {
  local key="$1"
  _seed_registry_if_missing
  awk -F'\t' -v k="$key" '$1==k { print $4; exit }' "$_REGISTRY"
}
# Build the full ECR URI for a tag: <account>.dkr.ecr.<region>.amazonaws.com/<tag>
_ecr_uri_for_tag() {
  local tag="$1"
  local acct; acct=$(account_id)
  echo "${acct}.dkr.ecr.${REGION}.amazonaws.com/${tag}"
}
# Normalize a user-supplied method value to one of:
#   batch | multiprocessing | ray | ray_colony | comparison
# Echoes the canonical name on success, "" on unknown input.
_normalize_method() {
  case "$1" in
    batch|nextflow_batch)              echo "batch" ;;
    mp|multiprocessing|mp_single_node) echo "multiprocessing" ;;
    ray|ray_cluster)                   echo "ray" ;;
    # ray_colony: sibling of ray. Same Ray-on-EC2-via-SSM
    # infrastructure (bootstrap_head_ray_colony.sh +
    # ec2_cluster_ray_colony.py), but invokes the greenfield
    # ``run_colony_ray.py`` instead of ``run_composite_lineage_ray.py``.
    # Each Ray actor runs one colony (greenfield in-place divide,
    # cells multiply 1 → 2^target_doublings inside the actor).
    ray_colony|ray_colony_cluster)     echo "ray_colony" ;;
    comparison|compare|comparison_head) echo "comparison" ;;
    *) echo "" ;;
  esac
}
# Strip the auto-rotated ``_YYYYMMDD-HHMMSS`` suffix that
# ``_persist_new_exp_id`` appends, leaving the BASE_EXP_ID (= the
# config's ``experiment_id`` field). The S3 layout is
# ``vecoli-output/<base>/<full_exp_id>/...`` — anything that needs
# to walk into a run's history dir must use the base for the outer
# segment, not the full timestamped ID.
#
# Examples:
#   comparison_10s_16g_v1_aws_2026_05_20260510-064538
#     → comparison_10s_16g_v1_aws_2026_05
#   comparison_10s_16g_v1_aws_2026_05  (no suffix)
#     → comparison_10s_16g_v1_aws_2026_05  (passthrough)
_exp_id_base() {
  local exp="$1"
  # Match exactly ``_8digits-6digits`` at end. Glob is ASCII-class.
  echo "${exp%_[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]-[0-9][0-9][0-9][0-9][0-9][0-9]}"
}

# Map a canonical method to the deploy_mode string the rest of the code
# (and the per-variant config) speaks.
_method_to_deploy_mode() {
  case "$1" in
    batch)           echo "nextflow_batch" ;;
    multiprocessing) echo "mp_single_node" ;;
    ray)             echo "ray_cluster" ;;
    ray_colony)      echo "ray_colony_cluster" ;;
    comparison)      echo "comparison_head" ;;
    *) echo "" ;;
  esac
}

# --- 3. Config loading helpers ----------------------------------------------
# read_cfg fails on missing keys; read_cfg_opt returns empty. Both read
# from the global $CONFIG_ABS, which _use_variant sets per-call.
read_cfg() {
  [[ -f "$CONFIG_ABS" ]] || { echo "missing config: $CONFIG_ABS" >&2; exit 1; }
  python3 -c "import json; print(json.load(open('$CONFIG_ABS'))$1)"
}
read_cfg_opt() {
  [[ -f "$CONFIG_ABS" ]] || return 0
  python3 -c "import json
c = json.load(open('$CONFIG_ABS'))
try:
    print(c$1)
except (KeyError, IndexError, TypeError):
    pass" 2>/dev/null
}

# --- 4. Variant resolution + sidecar experiment_id --------------------------
# _use_variant <v1|v2|mp|ray|path|""> sets ALL variant-derived globals:
#   CONFIG_REL, CONFIG_ABS, STATE_KEY, STATE_FILE,
#   BASE_EXP_ID (from config), EXP_ID (sidecar if present, else BASE),
#   OUT_URI, BUCKET, PREFIX, DEPLOY_MODE, QUEUE,
#   HEAD_NAME, HEAD_INSTANCE_TYPE, BOOTSTRAP_SCRIPT, TMUX_SESSION,
#   EXTRA_FILES.
# Empty arg → fall back to VECOLI_AWS_CONFIG env var; if also empty,
# error (so users get a clear "specify a variant" message rather than
# a silent default).
CONFIG_REL=""; CONFIG_ABS=""; STATE_KEY=""; STATE_FILE=""
BASE_EXP_ID=""; EXP_ID=""; OUT_URI=""; BUCKET=""; PREFIX=""
DEPLOY_MODE=""; QUEUE=""
HEAD_NAME=""; HEAD_INSTANCE_TYPE=""; BOOTSTRAP_SCRIPT=""
TMUX_SESSION=""; EXTRA_FILES=()
_use_variant() {
  local v="${1:-}"
  # STATE_KEY is the alias name when one was provided (so two aliases
  # sharing the same config still get distinct sidecars and head names).
  # If only VECOLI_AWS_CONFIG is set, derive a key from the config path.
  if [[ -z "$v" ]]; then
    CONFIG_REL="${VECOLI_AWS_CONFIG:-}"
    if [[ -z "$CONFIG_REL" ]]; then
      echo "Missing alias. Registered aliases:" >&2
      _registry_list | awk -F'\t' '{ printf "  %-15s %s\n", $1, $2 }' >&2
      echo "Or register a new one: $(basename "$0") experiment new <alias> <config>" >&2
      echo "Or set VECOLI_AWS_CONFIG=configs/<your>.json for a custom config." >&2
      return 1
    fi
    # Env-config fallback: best-effort reverse lookup, else basename.
    STATE_KEY=$(_state_key_for "$CONFIG_REL")
  else
    local cfg; cfg=$(_alias_to_config "$v")
    if [[ -z "$cfg" ]]; then
      echo "Unknown alias '$v'. Registered aliases:" >&2
      _registry_list | awk -F'\t' '{ printf "  %-15s %s\n", $1, $2 }' >&2
      echo "Register: $(basename "$0") experiment new $v <config>" >&2
      return 1
    fi
    CONFIG_REL="$cfg"
    # Alias path: use the alias verbatim as the state key. This is what
    # decouples ``vecoli new exp_a v1.json`` and ``vecoli new exp_b v1.json``
    # from sharing a sidecar / head when they happen to share a config.
    if [[ "$v" == */* || "$v" == *.json ]]; then
      # User passed a literal path, not an alias.
      STATE_KEY=$(basename "$v" .json)
    else
      STATE_KEY="$v"
    fi
  fi
  CONFIG_ABS="$REPO_ROOT/$CONFIG_REL"
  [[ -f "$CONFIG_ABS" ]] || { echo "missing config: $CONFIG_ABS" >&2; return 1; }
  STATE_FILE="$STATE_DIR/${STATE_KEY}.experiment-id"

  BASE_EXP_ID=$(read_cfg "['experiment_id']")
  OUT_URI=$(read_cfg "['emitter_arg']['out_uri']")
  # Method/deploy_mode resolution: alias registry wins, config field is
  # the legacy fallback. Lets the same config be reused as multiple
  # aliases pointing at different execution methods.
  local _registered_method
  _registered_method=$(_alias_to_method "$STATE_KEY")
  if [[ -n "$_registered_method" ]]; then
    DEPLOY_MODE=$(_method_to_deploy_mode "$_registered_method")
  else
    DEPLOY_MODE=$(read_cfg_opt "['aws']['deploy_mode']")
  fi
  QUEUE=$(read_cfg_opt "['aws']['batch_queue']")
  BUCKET="${OUT_URI#s3://}"; BUCKET="${BUCKET%%/*}"
  PREFIX="${OUT_URI#s3://$BUCKET/}"

  # Active EXP_ID: sidecar (last ``run launch <alias>``) wins.
  if [[ -f "$STATE_FILE" ]]; then
    EXP_ID=$(<"$STATE_FILE"); EXP_ID="${EXP_ID//$'\n'/}"
  else
    EXP_ID="$BASE_EXP_ID"
  fi

  # Per-alias defaults: head_name and tmux_session derive from the alias
  # (``vecoli-<alias>-head`` / ``vecoli-<alias>``) rather than hardcoded
  # ``vecoli-v2-*`` strings, so any new alias gets its own head + session
  # automatically. Config explicitly setting aws.head_name / tmux_session
  # still wins. Mode controls bootstrap script + default instance type.
  local cfg_head cfg_tmux
  cfg_head=$(read_cfg_opt "['aws']['head_name']")
  cfg_tmux=$(read_cfg_opt "['aws']['tmux_session']")
  local default_head="vecoli-${STATE_KEY}-head"
  local default_tmux="vecoli-${STATE_KEY}"
  case "${DEPLOY_MODE:-nextflow_batch}" in
    mp_single_node)
      HEAD_NAME="${HEAD_NAME_OVERRIDE:-${cfg_head:-$default_head}}"
      HEAD_INSTANCE_TYPE="${HEAD_INSTANCE_TYPE_OVERRIDE:-c7g.metal}"
      BOOTSTRAP_SCRIPT="$SCRIPT_DIR/bootstrap_head_mp.sh"
      TMUX_SESSION="${TMUX_SESSION_OVERRIDE:-${cfg_tmux:-$default_tmux}}"
      EXTRA_FILES=()
      ;;
    ray_cluster)
      HEAD_NAME="${HEAD_NAME_OVERRIDE:-${cfg_head:-$default_head}}"
      # Driver = build host. Use modern x86 (matches c7i.* workers).
      # Earlier c7g/Graviton experiment hit a Ray-actor-on-Graviton
      # SIGILL inside numba JIT that affected both fresh + production
      # images; see memory:ray_on_c7g_sigill. Reverted to x86 here so
      # ``run launch ray --build`` produces an x86 image natively.
      HEAD_INSTANCE_TYPE="${HEAD_INSTANCE_TYPE_OVERRIDE:-c7i.large}"
      BOOTSTRAP_SCRIPT="$SCRIPT_DIR/bootstrap_head_ray.sh"
      TMUX_SESSION="${TMUX_SESSION_OVERRIDE:-${cfg_tmux:-$default_tmux}}"
      EXTRA_FILES=("$SCRIPT_DIR/ec2_cluster_ray.py")
      ;;
    ray_colony_cluster)
      # Same Ray-on-EC2 driver topology as ray_cluster; only the
      # script that runs on the cluster changes. Each Ray actor runs
      # one colony via the greenfield ``run_colony_ray.py`` (cells
      # multiply 1 → 2^target_doublings in-place via the
      # schema-driven _divide sentinel).
      HEAD_NAME="${HEAD_NAME_OVERRIDE:-${cfg_head:-$default_head}}"
      HEAD_INSTANCE_TYPE="${HEAD_INSTANCE_TYPE_OVERRIDE:-c7i.large}"
      BOOTSTRAP_SCRIPT="$SCRIPT_DIR/bootstrap_head_ray_colony.sh"
      TMUX_SESSION="${TMUX_SESSION_OVERRIDE:-${cfg_tmux:-$default_tmux}}"
      EXTRA_FILES=("$SCRIPT_DIR/ec2_cluster_ray_colony.py")
      ;;
    nextflow_batch|"")
      HEAD_NAME="${HEAD_NAME_OVERRIDE:-${cfg_head:-$default_head}}"
      HEAD_INSTANCE_TYPE="${HEAD_INSTANCE_TYPE_OVERRIDE:-t4g.large}"
      BOOTSTRAP_SCRIPT="$SCRIPT_DIR/bootstrap_head.sh"
      TMUX_SESSION="${TMUX_SESSION_OVERRIDE:-${cfg_tmux:-$default_tmux}}"
      EXTRA_FILES=()
      ;;
    comparison_head)
      # Dedicated head for v1 ↔ v2 comparison work (parity scans, report).
      # No docker / nextflow / ECR — just uv + Python over in-region S3.
      # Needs more disk than the workflow heads to hold synced parquet.
      HEAD_NAME="${HEAD_NAME_OVERRIDE:-${cfg_head:-$default_head}}"
      HEAD_INSTANCE_TYPE="${HEAD_INSTANCE_TYPE_OVERRIDE:-m7g.xlarge}"
      BOOTSTRAP_SCRIPT="$SCRIPT_DIR/bootstrap_head_compare.sh"
      TMUX_SESSION="${TMUX_SESSION_OVERRIDE:-${cfg_tmux:-$default_tmux}}"
      EXTRA_FILES=()
      ;;
    *)
      echo "Unknown aws.deploy_mode: $DEPLOY_MODE" >&2
      echo "Expected: nextflow_batch | mp_single_node | ray_cluster | ray_colony_cluster | comparison_head" >&2
      return 1
      ;;
  esac
}

# Generate fresh per-launch EXP_ID and persist it. Called from
# ``ns_run_launch`` immediately before the bootstrap is invoked.
_persist_new_exp_id() {
  mkdir -p "$STATE_DIR"
  local stamp; stamp=$(date -u +%Y%m%d-%H%M%S)
  EXP_ID="${BASE_EXP_ID}_${stamp}"
  printf '%s\n' "$EXP_ID" > "$STATE_FILE"
  echo "Assigned experiment_id=$EXP_ID  (state: $STATE_FILE)"
}

# Eat a variant token if present at $1 of the caller's arg list (anything
# not starting with ``-``). Echoes the consumed variant (or empty), so
# the caller can ``shift`` accordingly:
#     local v; v=$(_consume_variant_arg "${1:-}"); [[ -n "$v" ]] && shift
_consume_variant_arg() {
  case "${1:-}" in
    -*|"") echo "" ;;
    *)     echo "$1" ;;
  esac
}

# --- 4. AWS / SSH / instance helpers ---------------------------------------
get_instance_id() {
  aws_cli ec2 describe-instances \
    --filters "Name=tag:Name,Values=${HEAD_NAME}" \
              "Name=instance-state-name,Values=running,pending,stopping,stopped" \
    --query 'Reservations[0].Instances[0].InstanceId' --output text 2>/dev/null
}
get_running_dns() {
  aws_cli ec2 describe-instances \
    --filters "Name=tag:Name,Values=${HEAD_NAME}" \
              "Name=instance-state-name,Values=running" \
    --query 'Reservations[0].Instances[0].PublicDnsName' --output text 2>/dev/null
}
require_running_dns() {
  local dns; dns=$(get_running_dns)
  [[ -n "$dns" && "$dns" != "None" ]] || { echo "no running head ($HEAD_NAME)" >&2; exit 1; }
  echo "$dns"
}
job_count() {
  [[ -n "$QUEUE" ]] || { echo "-"; return; }
  # ``length(jobSummaryList)`` evaluates per-page; with >1000 jobs the
  # AWS CLI paginates and prints "1000\n70" instead of "1070". Pull
  # actual jobIds and count them (awk handles empty input cleanly).
  aws_cli batch list-jobs --job-queue "$QUEUE" --job-status "$1" \
    --query 'jobSummaryList[*].jobId' --output text 2>/dev/null \
    | tr '\t\r' '\n' \
    | awk 'NF { n++ } END { print n+0 }'
}

# Count Batch jobs in <status> created at or after <epoch_ms>. With
# since_ms=0, returns the unfiltered total (same as job_count). Used by
# ``run status`` to scope counts to the active experiment_id, ignoring
# the queue's stale history.
_batch_count_since() {
  local status="$1" since_ms="${2:-0}"
  [[ -n "$QUEUE" ]] || { echo "-"; return; }
  aws_cli batch list-jobs --job-queue "$QUEUE" --job-status "$status" \
    --query 'jobSummaryList[*].createdAt' --output text 2>/dev/null \
    | tr '\t' '\n' \
    | awk -v since="$since_ms" 'since==0 || $1+0 >= since+0 {n++} END {print n+0}'
}

# Server-side AFTER_CREATED_AT filter: one paginated call returns every
# job in the queue created since this experiment launched, with status
# included. We group client-side instead of issuing 7 separate per-status
# calls. Used by ``run status`` to keep the dashboard cheap even when the
# queue's lifetime SUCCEEDED count is in the thousands.
#
# Prints ``status<TAB>count`` lines, ordered by lifecycle phase (SUBMITTED
# → FAILED), only for statuses with at least one job.
_batch_counts_since_filtered() {
  local since_ms="$1"
  [[ -n "$QUEUE" ]] || return
  (( since_ms > 0 )) || return
  aws_cli batch list-jobs --job-queue "$QUEUE" \
    --filters "name=AFTER_CREATED_AT,values=$since_ms" \
    --query 'jobSummaryList[*].status' --output text 2>/dev/null \
    | tr '\t' '\n' \
    | awk 'NF { c[$1]++ }
           END {
             n = split("SUBMITTED PENDING RUNNABLE STARTING RUNNING SUCCEEDED FAILED", ord, " ")
             for (i=1; i<=n; i++) if (c[ord[i]] > 0) printf "%s\t%d\n", ord[i], c[ord[i]]
           }'
}

# EXP_ID layout from ``_persist_new_exp_id`` is ``<base>_YYYYMMDD-HHMMSS``
# (UTC). Pull the trailing timestamp segment and convert to epoch ms so
# it can be compared with Batch ``createdAt`` / S3 ``LastModified``.
# Returns 0 when the EXP_ID has no parseable suffix (e.g. user is
# running a base-id-only legacy run).
_exp_id_to_epoch_ms() {
  local stamp="${1##*_}"
  if [[ "$stamp" =~ ^[0-9]{8}-[0-9]{6}$ ]]; then
    local d="${stamp:0:8}" t="${stamp:9:6}"
    local iso="${d:0:4}-${d:4:2}-${d:6:2}T${t:0:2}:${t:2:2}:${t:4:2}Z"
    date -u -d "$iso" +%s%3N 2>/dev/null || echo 0
  else
    echo 0
  fi
}

# Format a duration in seconds as a short human string (``2h 14m``,
# ``45s``, ``3d 1h``). Used by ``run status`` for "launched X ago" and
# "last write X ago" lines.
_human_duration() {
  local s="$1"
  (( s < 0 )) && s=0
  if   (( s < 60 ));    then echo "${s}s"
  elif (( s < 3600 ));  then echo "$((s/60))m $((s%60))s"
  elif (( s < 86400 )); then echo "$((s/3600))h $((s%3600/60))m"
  else                       echo "$((s/86400))d $((s%86400/3600))h"
  fi
}
account_id() {
  if [[ -z "$ACCOUNT_ID" ]]; then
    ACCOUNT_ID=$(aws_cli sts get-caller-identity --query Account --output text)
  fi
  echo "$ACCOUNT_ID"
}

# Rsync the local vEcoli repo onto the head's ~/vEcoli/. Used by
# ``head sync`` and (by default) ``run launch`` so the head executes
# whatever's in the local working tree without requiring a commit + push.
# Idempotent: installs rsync on the head once, then skips.
# $1: head DNS (caller supplies — already validated).
_rsync_repo_to_head() {
  local dns="$1"
  if ! ssh -i "$KEY_FILE" -o StrictHostKeyChecking=no \
       "ec2-user@${dns}" "command -v rsync >/dev/null 2>&1"; then
    echo "Installing rsync on head (one-time)..."
    ssh -i "$KEY_FILE" -o StrictHostKeyChecking=no \
      "ec2-user@${dns}" "sudo dnf -y install rsync >/dev/null"
  fi
  # Excludes:
  #   .venv             host arch may differ; venv rebuilt on head
  #   .git              head bootstrap manages this (when not skipping
  #                     git reset). When skipping reset, head trusts
  #                     rsynced files; .git mismatch is irrelevant.
  #   out               sim outputs — don't ship
  #   __pycache__/*.pyc regenerated on import
  #   .ruff_cache/.pytest_cache  dev-only caches
  #   .nextflow/nextflow_temp/trace--*  Nextflow per-run state on local
  #   .claude           per-user assistant state — never ship
  #   .vecoli-aws-state CLI sidecars — head doesn't need them
  rsync -azP \
    --exclude='.venv' \
    --exclude='.git' \
    --exclude='out' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='.ruff_cache' \
    --exclude='.pytest_cache' \
    --exclude='.nextflow' \
    --exclude='nextflow_temp' \
    --exclude='trace--*.csv' \
    --exclude='.claude' \
    --exclude='.vecoli-aws-state' \
    -e "ssh -i $KEY_FILE -o StrictHostKeyChecking=no" \
    "${REPO_ROOT}/" "ec2-user@${dns}:~/vEcoli/"
  # If pyproject.toml has editable sources pointing at sibling repos,
  # rsync those siblings too so the head's ``uv sync`` can resolve
  # them. Match on names in [tool.uv.sources] blocks like
  # ``foo = { path = "../foo", editable = true }``.
  local siblings
  siblings=$(awk '/^\[tool\.uv\.sources\]/{inblock=1; next}
                  /^\[/{inblock=0}
                  inblock && /path = "\.\.\/[^"]+"/ {
                      match($0, /"\.\.\/([^"]+)"/, m); print m[1]
                  }' "${REPO_ROOT}/pyproject.toml")
  if [[ -n "$siblings" ]]; then
    for sib in $siblings; do
      local sib_dir="${REPO_ROOT}/../${sib}"
      if [[ -d "$sib_dir" ]]; then
        echo "Rsyncing sibling editable dep ${sib} → ec2-user@${dns}:~/${sib}/"
        rsync -azP \
          --exclude='.venv' --exclude='.git' \
          --exclude='__pycache__' --exclude='*.pyc' \
          --exclude='.ruff_cache' --exclude='.pytest_cache' \
          -e "ssh -i $KEY_FILE -o StrictHostKeyChecking=no" \
          "${sib_dir}/" "ec2-user@${dns}:~/${sib}/"
      else
        echo "WARNING: pyproject.toml references editable sibling '${sib}'" >&2
        echo "  but ${sib_dir} doesn't exist locally — head ``uv sync`` will fail." >&2
      fi
    done
  fi
}

# Common pattern: scp a bootstrap script (+ extra files) to head, ssh-run
# it with config + session + experiment env. Used by ``run launch`` and
# ``run resume``.
# $1: extra env to prepend (e.g. "RESUME=1 BUILD_IMAGE=1 ").
_run_bootstrap_on_head() {
  local extra_env="${1:-}"
  local dns; dns=$(require_running_dns)
  local files=("$BOOTSTRAP_SCRIPT" "${EXTRA_FILES[@]}")
  local names=()
  for f in "${files[@]}"; do names+=("$(basename "$f")"); done
  echo "scp ${names[*]} -> $dns"
  scp -i "$KEY_FILE" "${files[@]}" "ec2-user@$dns:~/"
  local config_env="CONFIG_RELPATH='${CONFIG_REL}' "
  local session_env="SESSION='$TMUX_SESSION' "
  local sim_data_env=""
  [[ -n "${SIM_DATA_S3_URI:-}" ]] && sim_data_env="SIM_DATA_S3_URI='$SIM_DATA_S3_URI' "
  # Auto-resolve IMAGE_URI for ray-based deploy modes when not set
  # explicitly via env. The alias's registered image_tag (4th column of
  # aliases.tsv) gets combined with account_id + region into the full
  # ECR URI that ec2_cluster_ray*.py expects. Ad-hoc env override still
  # wins. ``ray_colony_cluster`` was previously skipped here, falling
  # through to a hardcoded ``vecoli:v2-comparison-arm64`` default in
  # bootstrap_head_ray_colony.sh — an x86_64 colony cluster would then
  # pull the arm64 image and fail at exec with "exec format error".
  local resolved_image_uri="${IMAGE_URI:-}"
  if [[ -z "$resolved_image_uri" ]] \
       && [[ "$DEPLOY_MODE" == "ray_cluster" \
              || "$DEPLOY_MODE" == "ray_colony_cluster" ]]; then
    local _tag; _tag=$(_alias_to_image "$STATE_KEY")
    if [[ -n "$_tag" ]]; then
      resolved_image_uri=$(_ecr_uri_for_tag "$_tag")
      echo "Auto-resolved IMAGE_URI from alias '$STATE_KEY': $resolved_image_uri"
    fi
  fi
  local image_env=""
  [[ -n "$resolved_image_uri" ]] && image_env="IMAGE_URI='$resolved_image_uri' "
  # Image-tag env so bootstrap_head_ray.sh's optional ``BUILD_IMAGE=1``
  # path knows which tag to build/push. Always passed when an image_tag
  # is registered for the alias — cheap, used only when BUILD_IMAGE=1.
  local image_tag_env=""
  local _img_tag; _img_tag=$(_alias_to_image "$STATE_KEY")
  [[ -n "$_img_tag" ]] && image_tag_env="IMAGE_TAG='$_img_tag' "
  # Thread the CLI-resolved EXP_ID so workflow.py / run_composite_lineage_*.py
  # use the unique sidecar-tracked ID rather than the BASE id from config.
  local exp_id_env=""
  [[ -n "${EXP_ID:-}" ]] && exp_id_env="EXPERIMENT_ID='$EXP_ID' "
  echo "running bootstrap (variant=${DEPLOY_MODE:-nextflow_batch}, config=${CONFIG_REL}, session=${TMUX_SESSION}, exp_id=${EXP_ID})..."
  ssh -i "$KEY_FILE" "ec2-user@$dns" \
    "${extra_env}${config_env}${session_env}${sim_data_env}${image_env}${image_tag_env}${exp_id_env}bash ~/$(basename "$BOOTSTRAP_SCRIPT")"
}

# --- 5. ``head`` namespace --------------------------------------------------
ns_head_setup() {
  # Optional method positional: ``head setup <alias> <method>``. When
  # supplied, validates and pins the alias's method in the registry
  # (then re-resolves variant globals so HEAD_INSTANCE_TYPE +
  # BOOTSTRAP_SCRIPT match the new method). When omitted, uses the
  # method already on file for the alias.
  local requested_method="${1:-}"
  if [[ -n "$requested_method" ]]; then
    local canon; canon=$(_normalize_method "$requested_method")
    if [[ -z "$canon" ]]; then
      echo "Unknown method '$requested_method' — expected: batch | multiprocessing | ray | ray_colony | comparison" >&2
      return 1
    fi
    local current; current=$(_alias_to_method "$STATE_KEY")
    if [[ "$current" != "$canon" ]]; then
      echo "Setting method for alias '$STATE_KEY': ${current:-(unset)} → $canon"
      _registry_set "$STATE_KEY" "$CONFIG_REL" "$canon"
      # Re-resolve so HEAD_NAME / HEAD_INSTANCE_TYPE / BOOTSTRAP_SCRIPT
      # reflect the new method before we provision.
      _use_variant "$STATE_KEY"
    fi
  fi

  # Final guard: refuse to provision if no method is on file at this
  # point — every other subcmd derives behaviour from method, so
  # leaving it unset would just defer the failure.
  if [[ -z "$(_alias_to_method "$STATE_KEY")" ]]; then
    echo "Alias '$STATE_KEY' has no method registered." >&2
    echo "Run: $(basename "$0") head setup $STATE_KEY <batch|multiprocessing|ray|ray_colony>" >&2
    return 1
  fi

  # Idempotent: if a head with this tag already exists, reuse it
  # (start it if stopped). Only provision a new instance when none
  # exists — without this guard, ``head setup`` called twice creates
  # duplicate EC2 instances tagged with the same Name, leaving an
  # orphan and confusing every subsequent CLI command.
  local id state
  id=$(get_instance_id)
  if [[ -n "$id" && "$id" != "None" ]]; then
    state=$(aws_cli ec2 describe-instances --instance-ids "$id" \
      --query 'Reservations[0].Instances[0].State.Name' --output text)
    case "$state" in
      running)
        echo "Head $HEAD_NAME already running ($id). " \
             "Use 'head terminate' first to provision fresh."
        return 0 ;;
      stopped)
        echo "Head $HEAD_NAME exists but stopped ($id) — starting..."
        aws_cli ec2 start-instances --instance-ids "$id" >/dev/null
        aws_cli ec2 wait instance-running --instance-ids "$id"
        return 0 ;;
      pending|stopping)
        echo "Head $HEAD_NAME is $state ($id) — waiting..."
        if [[ "$state" == "pending" ]]; then
          aws_cli ec2 wait instance-running --instance-ids "$id"
        else
          aws_cli ec2 wait instance-stopped --instance-ids "$id"
          aws_cli ec2 start-instances --instance-ids "$id" >/dev/null
          aws_cli ec2 wait instance-running --instance-ids "$id"
        fi
        return 0 ;;
    esac
  fi
  echo "Provisioning new head ($HEAD_NAME, $HEAD_INSTANCE_TYPE) for method '$(_alias_to_method "$STATE_KEY")'..."
  # Disk default scales with method: comparison heads sync ~tens-of-GB
  # of parquet from S3, workflow heads only need room for nextflow logs.
  local default_root_gib=30
  [[ "${DEPLOY_MODE:-}" == "comparison_head" ]] && default_root_gib=200
  HEAD_INSTANCE_TYPE="$HEAD_INSTANCE_TYPE" \
    HEAD_NAME="$HEAD_NAME" \
    ROOT_VOL_GIB="${ROOT_VOL_GIB:-$default_root_gib}" \
    bash "$SCRIPT_DIR/setup_head_node.sh"
}
ns_head_terminate() {
  local id; id=$(get_instance_id)
  [[ -n "$id" && "$id" != "None" ]] || { echo "no head to terminate"; return; }
  read -r -p "Terminate $id ($HEAD_NAME)? [y/N] " ans
  [[ "$ans" == "y" || "$ans" == "Y" ]] || { echo "aborted"; return; }
  aws_cli ec2 terminate-instances --instance-ids "$id" >/dev/null
  echo "Waiting for terminated state..."
  aws_cli ec2 wait instance-terminated --instance-ids "$id"
  echo "Terminated."
}
ns_head_list() {
  # List ALL non-terminated instances tagged with this head's Name —
  # surfaces duplicates created by the pre-idempotent ``head setup``.
  echo "Heads tagged Name=${HEAD_NAME}:"
  aws_cli ec2 describe-instances \
    --filters "Name=tag:Name,Values=${HEAD_NAME}" \
              "Name=instance-state-name,Values=running,pending,stopping,stopped" \
    --query 'Reservations[].Instances[].[InstanceId,State.Name,LaunchTime,PublicDnsName,InstanceType]' \
    --output table
}

# Cross-alias listing of every vEcoli-owned EC2. Filter Name=vecoli-*
# captures BOTH:
#   - driver heads      (vecoli-{alias}-head, e.g. vecoli-v2-ray-head)
#   - Ray cluster nodes (vecoli-ray-{timestamp}-{head,worker} since
#                        ec2_cluster_ray.py defaults cluster_id to
#                        vecoli-ray-* — confirmed at line 229-230)
# Safe against spatio-flux (sf-* cluster_id) and any non-vEcoli
# instance in the account.
#
# Output (tab-separated, one row per instance):
#   InstanceId<TAB>Name<TAB>State<TAB>InstanceType<TAB>LaunchTime
_list_vecoli_instances() {
  aws_cli ec2 describe-instances \
    --filters "Name=tag:Name,Values=vecoli-*" \
              "Name=instance-state-name,Values=running,pending,stopping,stopped" \
    --query 'Reservations[].Instances[].[InstanceId,Tags[?Key==`Name`]|[0].Value,State.Name,InstanceType,LaunchTime]' \
    --output text 2>/dev/null
}

# Render rows from ``_list_vecoli_instances`` grouped by category.
# Categories (matched on Name tag):
#   driver-head   vecoli-{alias}-head       (per-alias workflow driver)
#   ray-cluster   vecoli-ray-<digits>-*     (EC2SSMRayCluster head/workers)
#   other         anything else (vecoli-*)  (unexpected — investigate)
# Prints per-category subheading + count and a final total. Empty
# stdin → "(none)" line + total 0.
_print_vecoli_instances() {
  awk -F'\t' '
    BEGIN { ORDER[1]="driver-head"; ORDER[2]="ray-cluster"; ORDER[3]="other"; N_CAT=3 }
    NF == 0 { next }
    {
      name=$2
      if (name ~ /^vecoli-ray-[0-9]+-(head|worker)$/) cat = "ray-cluster"
      else if (name ~ /-head$/)                       cat = "driver-head"
      else                                             cat = "other"
      rows[cat] = rows[cat] sprintf("  %-22s  %-32s  %-10s  %-14s  %s\n", $1, name, $3, $4, $5)
      counts[cat]++
      total++
    }
    END {
      if (total == 0) { print "  (none)"; exit }
      for (i = 1; i <= N_CAT; i++) {
        c = ORDER[i]
        if (!counts[c]) continue
        printf "── %s (%d) ──\n", c, counts[c]
        printf "  %-22s  %-32s  %-10s  %-14s  %s\n", "INSTANCE-ID", "NAME", "STATE", "TYPE", "LAUNCHED"
        printf "%s", rows[c]
      }
      printf "Total: %d instance(s).\n", total
    }
  '
}

ns_head_list_all() {
  # Read-only dry-run of what ``head terminate-all`` would target.
  # Use this BEFORE terminate-all when you want to see scope first
  # (e.g., are there orphaned Ray cluster workers from a dead driver?).
  echo "All vEcoli-owned EC2 (Name=vecoli-*, any state):"
  _list_vecoli_instances | _print_vecoli_instances
}
ns_head_dedupe() {
  # Terminate every running/pending instance tagged with HEAD_NAME
  # EXCEPT the one with the earliest LaunchTime (the original — it's
  # the one any in-flight ``run launch`` is targeting). Stopped
  # instances are left alone (might be the user's saved snapshot).
  local rows; rows=$(aws_cli ec2 describe-instances \
    --filters "Name=tag:Name,Values=${HEAD_NAME}" \
              "Name=instance-state-name,Values=running,pending" \
    --query 'sort_by(Reservations[].Instances[], &LaunchTime)[].[InstanceId,LaunchTime]' \
    --output text)
  if [[ -z "$rows" ]]; then
    echo "No running heads tagged ${HEAD_NAME}; nothing to dedupe."
    return 0
  fi
  local n; n=$(echo "$rows" | wc -l | tr -d ' ')
  if [[ "$n" -le 1 ]]; then
    echo "Only one running head tagged ${HEAD_NAME}; nothing to dedupe."
    echo "$rows"
    return 0
  fi
  local keep; keep=$(echo "$rows" | head -n 1 | awk '{print $1}')
  local kill_ids; kill_ids=$(echo "$rows" | tail -n +2 | awk '{print $1}')
  echo "Heads tagged ${HEAD_NAME} (oldest first):"
  echo "$rows"
  echo
  echo "Will keep oldest:    $keep"
  echo "Will terminate:      $(echo "$kill_ids" | tr '\n' ' ')"
  read -r -p "Proceed? [y/N] " ans
  [[ "$ans" == "y" || "$ans" == "Y" ]] || { echo "aborted"; return 1; }
  # shellcheck disable=SC2086
  aws_cli ec2 terminate-instances --instance-ids $kill_ids >/dev/null
  echo "Waiting for terminated state..."
  # shellcheck disable=SC2086
  aws_cli ec2 wait instance-terminated --instance-ids $kill_ids
  echo "Terminated. Remaining: $keep"
}
ns_head_rebuild() { ns_head_terminate; ns_head_setup; }
ns_head_sync() {
  # Rsync the local vEcoli repo onto the head, optionally also pushing
  # the current local code INTO the head's running Docker container
  # (vecoli_ray) via ``docker cp``. This is the fast iteration path:
  # no git push, no image rebuild — just sync what changed.
  #
  # Workflow:
  #   1. Edit code locally.
  #   2. ``head sync``     →  ~/vEcoli on head matches local.
  #   3. ``head sync -c``  →  /vEcoli inside the head's vecoli_ray
  #                            container ALSO matches local. Needed
  #                            for changes the cluster driver runs in
  #                            the container (run_composite_lineage_ray.py,
  #                            ec2_cluster_ray.py-side configs, etc.).
  #   4. ``run launch``    →  bootstrap as usual.
  #
  # Caveat: only the head's container is updated. Worker containers
  # still run whatever's in the image. For changes to actor / sim
  # code (ecoli/, wholecell/, reconstruction/), an image rebuild +
  # push is still required so workers get the new code.
  #
  # Args (optional):
  #   -c, --container   also docker-cp host code into vecoli_ray
  local include_container=0
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -c|--container) include_container=1; shift ;;
      *) echo "usage: head sync [-c|--container]" >&2; return 1 ;;
    esac
  done
  local dns; dns=$(require_running_dns)
  echo "Rsyncing local repo → ec2-user@${dns}:~/vEcoli/ ..."
  _rsync_repo_to_head "$dns"
  echo "  Host repo synced."

  if (( include_container )); then
    # Docker cp the same subdirs into the running vecoli_ray
    # container (the cluster's head container). Skip .venv (the
    # image's venv is at /vEcoli/.venv and may not match host arch).
    # Only push the volatile dirs the driver actually reads at
    # runtime — keeps the cp small and fast.
    local container="${VECOLI_CONTAINER:-vecoli_ray}"
    echo "Pushing local subdirs → docker container '${container}' on head ..."
    ssh -i "$KEY_FILE" "ec2-user@${dns}" \
      "if ! docker ps --format '{{.Names}}' | grep -qx '${container}'; then
         echo 'Container ${container} not running on head — skip cp.'; exit 0
       fi
       for d in runscripts configs ecoli wholecell reconstruction; do
         if [[ -d ~/vEcoli/\$d ]]; then
           echo \"  docker cp ~/vEcoli/\$d ${container}:/vEcoli/\"
           docker cp ~/vEcoli/\$d ${container}:/vEcoli/
         fi
       done
       echo '  Container code synced.'"
  fi
}
ns_head_setup_ray_iam() {
  # One-time grant of Ray cluster-management perms to the head's
  # instance profile, plus creation of the worker instance profile
  # ``ray-process-bigraph-node``. Idempotent. Must be run from a
  # machine with IAM admin rights (your laptop, not the head).
  echo "Granting Ray cluster-management IAM policy to head's instance profile..."
  bash "$SCRIPT_DIR/setup_ray_iam.sh"
}

# Create a Gateway VPC endpoint for S3 in the Ray cluster's VPC so workers
# in the private subnet write to S3 directly (point-to-point) instead of
# funneling through the shared NAT gateway. Gateway endpoints are FREE —
# they're a route-table entry routing s3:* traffic to AWS-internal links.
#
# Idempotent: if an S3 Gateway endpoint already exists in this VPC, just
# reports it and exits. Otherwise discovers the VPC's route tables,
# prompts before creation, attaches the endpoint to every RT.
#
# Subnet defaults to subnet-08621613bcb558caa (the SMS API private
# subnet used by ec2_cluster_ray.py); override with VECOLI_RAY_SUBNET.
ns_head_setup_s3_endpoint() {
  local subnet_id="${VECOLI_RAY_SUBNET:-subnet-08621613bcb558caa}"
  local region; region=$(aws_cli configure get region 2>/dev/null \
                          || echo "us-gov-west-1")
  local service_name="com.amazonaws.${region}.s3"

  echo "Resolving VPC for subnet ${subnet_id}..."
  local vpc_id
  vpc_id=$(aws_cli ec2 describe-subnets --subnet-ids "$subnet_id" \
    --query 'Subnets[0].VpcId' --output text 2>/dev/null)
  if [[ -z "$vpc_id" || "$vpc_id" == "None" ]]; then
    echo "Could not resolve VPC for subnet ${subnet_id}" >&2
    echo "Check VECOLI_RAY_SUBNET env or the default in ec2_cluster_ray.py" >&2
    return 1
  fi
  echo "  VPC: ${vpc_id}"

  # Idempotency check — any existing S3 Gateway endpoint in this VPC.
  local existing
  existing=$(aws_cli ec2 describe-vpc-endpoints \
    --filters "Name=vpc-id,Values=${vpc_id}" \
              "Name=service-name,Values=${service_name}" \
              "Name=vpc-endpoint-type,Values=Gateway" \
    --query 'VpcEndpoints[0].VpcEndpointId' --output text 2>/dev/null)
  if [[ -n "$existing" && "$existing" != "None" ]]; then
    echo "S3 Gateway endpoint already exists: ${existing}"
    # An endpoint that's not attached to the worker subnet's route table
    # does NOTHING for that subnet — traffic still hits the NAT gateway.
    # Verify by cross-referencing: subnet → route_table_id → does this
    # RT appear in the endpoint's RouteTableIds?
    echo
    echo "Checking whether subnet ${subnet_id} actually uses this endpoint..."
    local subnet_rt
    subnet_rt=$(aws_cli ec2 describe-route-tables \
      --filters "Name=association.subnet-id,Values=${subnet_id}" \
      --query 'RouteTables[0].RouteTableId' --output text 2>/dev/null)
    if [[ -z "$subnet_rt" || "$subnet_rt" == "None" ]]; then
      # Subnet uses VPC's main RT (no explicit association).
      subnet_rt=$(aws_cli ec2 describe-route-tables \
        --filters "Name=vpc-id,Values=${vpc_id}" \
                  "Name=association.main,Values=true" \
        --query 'RouteTables[0].RouteTableId' --output text 2>/dev/null)
      echo "  Subnet uses VPC's main route table: ${subnet_rt}"
    else
      echo "  Subnet's route table: ${subnet_rt}"
    fi
    local endpoint_rts
    endpoint_rts=$(aws_cli ec2 describe-vpc-endpoints \
      --vpc-endpoint-ids "$existing" \
      --query 'VpcEndpoints[0].RouteTableIds' --output text 2>/dev/null)
    echo "  Endpoint serves route tables: ${endpoint_rts}"
    # AWS CLI ``--output text`` separates list items with TABS, not spaces;
    # tr to newlines + grep -Fx for an exact line match avoids both
    # the tab/space ambiguity and any partial-string false positives.
    if printf '%s\n' "$endpoint_rts" | tr '\t' '\n' \
         | grep -qFx "$subnet_rt"; then
      echo "  → Worker traffic IS routing through the S3 endpoint (NAT bypassed)."
    else
      echo "  → Endpoint exists but does NOT include the subnet's route table."
      echo "    Worker S3 writes still go through the NAT gateway."
      read -r -p "Add subnet RT ${subnet_rt} to endpoint ${existing}? [y/N] " ans
      if [[ "$ans" == "y" || "$ans" == "Y" ]]; then
        aws_cli ec2 modify-vpc-endpoint \
          --vpc-endpoint-id "$existing" \
          --add-route-table-ids "$subnet_rt" >/dev/null \
          && echo "    Attached ${subnet_rt} to ${existing}." \
          || echo "    Modify failed." >&2
      fi
    fi
    return 0
  fi

  # Find ALL route tables in the VPC. Attaching to all of them means any
  # subnet that routes through any of these RTs gets the S3 shortcut —
  # no need to enumerate worker-vs-head subnets.
  local rts
  rts=$(aws_cli ec2 describe-route-tables \
    --filters "Name=vpc-id,Values=${vpc_id}" \
    --query 'RouteTables[].RouteTableId' --output text 2>/dev/null)
  if [[ -z "$rts" ]]; then
    echo "No route tables found in VPC ${vpc_id}" >&2
    return 1
  fi
  echo "  Route tables (will attach to all): ${rts}"
  echo "  Service: ${service_name}"
  echo
  read -r -p "Create S3 Gateway endpoint? [y/N] " ans
  [[ "$ans" == "y" || "$ans" == "Y" ]] || { echo "aborted"; return; }

  local endpoint_id
  endpoint_id=$(aws_cli ec2 create-vpc-endpoint \
    --vpc-id "$vpc_id" \
    --service-name "$service_name" \
    --vpc-endpoint-type Gateway \
    --route-table-ids $rts \
    --query 'VpcEndpoint.VpcEndpointId' --output text)
  if [[ -n "$endpoint_id" && "$endpoint_id" != "None" ]]; then
    echo
    echo "Created S3 Gateway endpoint: ${endpoint_id}"
    echo "Workers' S3 writes will now bypass the NAT gateway."
    echo "Restart the Ray cluster (vecoli_aws.sh run launch ray) to pick up"
    echo "the new routes — existing in-flight workers may already benefit"
    echo "via route-table refresh."
  else
    echo "Endpoint creation failed" >&2
    return 1
  fi
}
ns_head_reboot() {
  local id; id=$(get_instance_id)
  [[ -n "$id" && "$id" != "None" ]] || { echo "no head"; return 1; }
  aws_cli ec2 reboot-instances --instance-ids "$id"
  echo "Reboot signaled. Status checks should be 'ok' in ~60s."
}
ns_head_stop() {
  local id; id=$(get_instance_id)
  [[ -n "$id" && "$id" != "None" ]] || { echo "no head"; return 1; }
  aws_cli ec2 stop-instances --instance-ids "$id" >/dev/null
  echo "Stopping $id... (preserves EBS root volume)"
  aws_cli ec2 wait instance-stopped --instance-ids "$id"
  echo "Stopped. Use 'head start' to bring back. Public IP will change."
}
ns_head_start() {
  local id; id=$(get_instance_id)
  [[ -n "$id" && "$id" != "None" ]] || { echo "no stopped head; use 'head setup' first"; return 1; }
  aws_cli ec2 start-instances --instance-ids "$id" >/dev/null
  echo "Starting $id..."
  aws_cli ec2 wait instance-running --instance-ids "$id"
  local dns; dns=$(get_running_dns)
  echo "Running. New public DNS: $dns"
  echo "If your laptop's IP changed: $(basename "$0") head refresh-sg $STATE_KEY"
}
ns_head_refresh_sg() {
  local sg_id ip
  sg_id=$(aws_cli ec2 describe-security-groups \
    --filters "Name=group-name,Values=vecoli-head-ssh-sg" \
    --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null)
  [[ -n "$sg_id" && "$sg_id" != "None" ]] || { echo "no SG vecoli-head-ssh-sg"; return 1; }
  ip=$(curl -s -4 ifconfig.me)
  echo "Authorizing SSH from ${ip}/32 to ${sg_id}..."
  aws_cli ec2 authorize-security-group-ingress \
    --group-id "$sg_id" --protocol tcp --port 22 --cidr "${ip}/32" 2>/dev/null \
    && echo "Added." || echo "Already present (no-op)."
}
ns_head_dns() { get_running_dns; }
ns_head_ssh() { exec ssh -i "$KEY_FILE" "ec2-user@$(require_running_dns)" "$@"; }
ns_head_attach() {
  exec ssh -i "$KEY_FILE" -t "ec2-user@$(require_running_dns)" "tmux attach -t $TMUX_SESSION"
}

# Terminate every EC2 instance whose Name tag is `vecoli-*`. Useful for
# the "shut everything off" case after a debugging session. Lists first
# and prompts before terminating. Skips already-terminated instances.
#
# With --cancel-jobs, also terminates active Batch jobs (SUBMITTED →
# RUNNING) across the unique queues found in the alias registry. Heads
# alone don't kill Batch jobs — those run on Batch-managed compute, so
# they keep billing after the head is gone unless explicitly canceled.
ns_head_terminate_all() {
  local cancel_jobs=0
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --cancel-jobs|--cancel) cancel_jobs=1; shift ;;
      *) echo "head terminate-all: unknown arg '$1' (expected --cancel-jobs)" >&2
         return 1 ;;
    esac
  done

  local rows; rows=$(_list_vecoli_instances)

  # Discover unique Batch queues from the alias registry. Multiple
  # aliases often share a queue (v1 and v2 both use vecoli-arm), so
  # dedupe to avoid double-cancellation.
  local queues=""
  if (( cancel_jobs == 1 )); then
    _seed_registry_if_missing
    while IFS=$'\t' read -r a cfg _; do
      [[ -z "$a" ]] && continue
      [[ -f "$REPO_ROOT/$cfg" ]] || continue
      local q
      q=$(CONFIG_ABS="$REPO_ROOT/$cfg" read_cfg_opt "['aws']['batch_queue']")
      if [[ -n "$q" && "$queues" != *":${q}:"* ]]; then
        queues="${queues}:${q}:"
      fi
    done < "$_REGISTRY"
  fi

  if [[ -z "$rows" && -z "$queues" ]]; then
    echo "Nothing to clean up: no vecoli-* instances, no Batch queues registered."
    return 0
  fi

  if [[ -n "$rows" ]]; then
    echo "vEcoli-owned EC2 to terminate (Name=vecoli-*):"
    echo "$rows" | _print_vecoli_instances
  else
    echo "No vecoli-* instances currently provisioned."
  fi
  if [[ -n "$queues" ]]; then
    echo
    echo "Will also cancel active Batch jobs in queues:"
    for q in $(echo "$queues" | tr ':' ' '); do
      [[ -n "$q" ]] && echo "  $q"
    done
  fi
  echo
  read -r -p "Proceed with cleanup? [y/N] " ans
  [[ "$ans" == "y" || "$ans" == "Y" ]] || { echo "aborted"; return 1; }

  # 1. Cancel Batch jobs first — heads going down won't stop running
  #    Batch tasks (those run on Batch-managed compute, not the head).
  if (( cancel_jobs == 1 )) && [[ -n "$queues" ]]; then
    echo
    echo "Canceling active Batch jobs..."
    for q in $(echo "$queues" | tr ':' ' '); do
      [[ -z "$q" ]] && continue
      echo "  queue $q:"
      local n_canceled=0
      for s in SUBMITTED PENDING RUNNABLE STARTING RUNNING; do
        local ids
        ids=$(aws_cli batch list-jobs --job-queue "$q" --job-status "$s" \
              --query 'jobSummaryList[*].jobId' --output text 2>/dev/null \
              | tr '\t\r' '\n' | grep -v '^$' || true)
        for id in $ids; do
          if aws_cli batch terminate-job --job-id "$id" \
               --reason "head terminate-all" >/dev/null 2>&1; then
            n_canceled=$(( n_canceled + 1 ))
            echo "    terminated $id ($s)"
          fi
        done
      done
      if (( n_canceled == 0 )); then
        echo "    (no active jobs)"
      fi
    done
  fi

  # 2. Terminate EC2 heads.
  if [[ -n "$rows" ]]; then
    echo
    local ids; ids=$(echo "$rows" | awk '{print $1}' | tr '\n' ' ')
    echo "Terminating EC2 instances (heads + any Ray cluster workers)..."
    # shellcheck disable=SC2086
    aws_cli ec2 terminate-instances --instance-ids $ids >/dev/null
    echo "Waiting for terminated state..."
    # shellcheck disable=SC2086
    aws_cli ec2 wait instance-terminated --instance-ids $ids
    echo "Terminated $(echo "$ids" | wc -w | tr -d ' ') instances."
  fi
}

# --- 5b. ``experiment`` namespace -------------------------------------------
# Manage the alias registry. Each registered alias is independent:
# its own config, head, tmux session, and experiment_id sidecar.
ns_experiment_new() {
  local force=0
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -f|--force) force=1; shift ;;
      -*) echo "experiment new: unknown flag '$1'" >&2; return 1 ;;
      *) break ;;
    esac
  done
  local alias_name="${1:-}" cfg="${2:-}" method_in="${3:-}" image_in="${4:-}"
  if [[ -z "$alias_name" || -z "$cfg" ]]; then
    echo "usage: experiment new [-f] <alias> <config_path> [<method>] [<image_tag>]" >&2
    echo "  method:    batch | multiprocessing | ray | ray_colony | comparison (optional; settable" >&2
    echo "             at ``head setup <alias> <method>`` time)" >&2
    echo "  image_tag: e.g. vecoli:my-tag-arm64 (optional; settable later" >&2
    echo "             via ``experiment new -f <alias> <cfg> <method> <tag>``)" >&2
    return 1
  fi
  if [[ ! "$alias_name" =~ ^[a-zA-Z][a-zA-Z0-9_-]*$ ]]; then
    echo "invalid alias '$alias_name' — use letters/digits/_/-, must start with a letter" >&2
    return 1
  fi
  if [[ ! -f "$REPO_ROOT/$cfg" ]]; then
    echo "config not found: $REPO_ROOT/$cfg" >&2
    return 1
  fi
  # Reject aliases that look like flags or subcommand names so we can't
  # accidentally shadow them.
  case "$alias_name" in
    help|launch|status|tail|log|cancel|jobs|id|setup|terminate|new|list|end|rm|ssh|attach|push|pull|build|all)
      echo "alias '$alias_name' clashes with a reserved subcommand name; pick another." >&2
      return 1 ;;
  esac
  local method=""
  if [[ -n "$method_in" ]]; then
    method=$(_normalize_method "$method_in")
    if [[ -z "$method" ]]; then
      echo "Unknown method '$method_in' — expected: batch | multiprocessing | ray | ray_colony | comparison" >&2
      return 1
    fi
  fi
  _seed_registry_if_missing
  local existing; existing=$(_alias_to_config "$alias_name")
  if [[ -n "$existing" && "$existing" != "$cfg" && $force -eq 0 ]]; then
    echo "alias '$alias_name' already maps to $existing — pass -f to overwrite" >&2
    return 1
  fi
  _registry_set "$alias_name" "$cfg" "$method" "$image_in"
  local summary="Registered: $alias_name → $cfg"
  [[ -n "$method" ]] && summary+="  method=$method"
  [[ -n "$image_in" ]] && summary+="  image=$image_in"
  echo "$summary"
  if [[ -z "$method" ]]; then
    echo "  Set method: $(basename "$0") head setup $alias_name <batch|multiprocessing|ray|ray_colony>"
  fi
  if [[ -z "$image_in" && "$method" != "multiprocessing" ]]; then
    echo "  Set image:  $(basename "$0") experiment new -f $alias_name $cfg ${method:-<method>} <image:tag>"
  fi
}

ns_experiment_list() {
  _seed_registry_if_missing
  printf "%-12s  %-16s  %-30s  %s\n" \
    "ALIAS" "METHOD" "IMAGE_TAG" "EXPERIMENT_ID (active)"
  printf "%-12s  %-16s  %-30s  %s\n" \
    "------------" "----------------" "------------------------------" \
    "----------------------"
  while IFS=$'\t' read -r alias_name cfg method image; do
    [[ -z "$alias_name" ]] && continue
    # Active experiment_id = sidecar contents (set by ``run launch
    # <alias>``); falls back to "(no run yet)" when no sidecar exists.
    local exp_file="$STATE_DIR/${alias_name}.experiment-id"
    local exp_id="(no run yet)"
    if [[ -f "$exp_file" ]]; then
      exp_id=$(<"$exp_file")
      exp_id="${exp_id//$'\n'/}"
    fi
    printf "%-12s  %-16s  %-30s  %s\n" \
      "$alias_name" "${method:-(unset)}" "${image:-(none)}" "$exp_id"
    # Indented continuation: full config path + sidecar path so the user
    # can quickly find/edit either without a separate command.
    printf "%-12s  config: %s\n" "" "$cfg"
  done < "$_REGISTRY"
}

# Soft-stop an experiment: cancels any running work + clears the
# experiment_id sidecar, but leaves the head alive (cheap to keep) and
# the alias registered. Pass --terminate-head to also kill the EC2,
# --rm to also unregister the alias.
ns_experiment_end() {
  local terminate_head=0 unregister=0
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --terminate-head|--terminate) terminate_head=1; shift ;;
      --rm) unregister=1; shift ;;
      *) echo "experiment end: unknown arg '$1' (expected --terminate-head | --rm)" >&2
         return 1 ;;
    esac
  done
  echo "Ending experiment '$STATE_KEY' (config=$CONFIG_REL):"
  # 1. Kill tmux + Batch jobs (existing behavior).
  ns_run_cancel
  # 2. Clear sidecar.
  if [[ -f "$STATE_FILE" ]]; then
    rm -f "$STATE_FILE"
    echo "  removed sidecar: $STATE_FILE"
  fi
  # 3. Optional head terminate.
  if (( terminate_head == 1 )); then
    local id; id=$(get_instance_id)
    if [[ -n "$id" && "$id" != "None" ]]; then
      echo "  terminating head $id ($HEAD_NAME)..."
      aws_cli ec2 terminate-instances --instance-ids "$id" >/dev/null
      aws_cli ec2 wait instance-terminated --instance-ids "$id"
      echo "  terminated."
    else
      echo "  (no head to terminate)"
    fi
  fi
  # 4. Optional alias removal.
  if (( unregister == 1 )); then
    _registry_unset "$STATE_KEY"
    echo "  unregistered alias '$STATE_KEY'"
  fi
}

# Hard remove: refuses if the alias still has a running head or tmux
# session — user must ``experiment end --terminate-head`` first.
ns_experiment_rm() {
  local force=0
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -f|--force) force=1; shift ;;
      *) echo "experiment rm: unknown arg '$1'" >&2; return 1 ;;
    esac
  done
  if (( force == 0 )); then
    local id; id=$(get_instance_id)
    if [[ -n "$id" && "$id" != "None" ]]; then
      echo "alias '$STATE_KEY' still has an EC2 head ($id, $HEAD_NAME)." >&2
      echo "Run ``experiment end $STATE_KEY --terminate-head`` first, or pass -f." >&2
      return 1
    fi
  fi
  _registry_unset "$STATE_KEY"
  rm -f "$STATE_FILE"
  echo "Unregistered: $STATE_KEY"
}

# --- 6. ``image`` namespace -------------------------------------------------
# Wraps runscripts/container/build-image.sh + the ECR docker tag/push
# pipeline that was previously documented in copy-pasted markdown.
IMAGE_TAG_DEFAULT="vecoli:v2-comparison-arm64"
# Resolve the tag for an alias, with optional ``-t TAG`` override that
# does NOT update the registry. Errors when the alias has no registered
# image_tag and no -t was passed.
_image_tag_for_alias() {
  local override="$1"
  if [[ -n "$override" ]]; then echo "$override"; return 0; fi
  local tag; tag=$(_alias_to_image "$STATE_KEY")
  if [[ -z "$tag" ]]; then
    echo "Alias '$STATE_KEY' has no image_tag in registry." >&2
    echo "  Set one: $(basename "$0") experiment new -f $STATE_KEY $CONFIG_REL ${DEPLOY_MODE:-} <image:tag>" >&2
    echo "  Or override for this call only: --tag <image:tag>" >&2
    return 1
  fi
  echo "$tag"
}

ns_image_build() {
  # ``image build <alias> [--tag TAG] [--platform PLATFORM] [--cloud]``
  # Reads the alias's registered image_tag from .vecoli-aws-state/aliases.tsv;
  # ``--tag`` overrides for this build only (registry untouched).
  #
  # Arch guard: infers target platform from the tag name (``-arm64`` →
  # linux/arm64, ``-amd64`` → linux/amd64), detects host/target
  # mismatch, and auto-cross-builds via ``docker buildx`` when needed.
  # x86_64 laptops can no longer silently produce amd64 layers tagged
  # ``...-arm64`` (which is what corrupted batch's ECR image earlier).
  local override_tag="" local_build=1 platform=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -t|--tag)   override_tag="$2"; shift 2 ;;
      --platform) platform="$2"; shift 2 ;;
      -l|--local) local_build=1; shift ;;
      --cloud)    local_build=0; shift ;;
      *) echo "image build: unknown arg $1" >&2; return 1 ;;
    esac
  done
  local tag; tag=$(_image_tag_for_alias "$override_tag") || return 1

  # Infer target platform from tag if the user didn't pass --platform.
  if [[ -z "$platform" ]]; then
    case "$tag" in
      *arm64*|*aarch64*) platform="linux/arm64" ;;
      *amd64*|*x86_64*)  platform="linux/amd64" ;;
    esac
  fi

  local host_arch; host_arch=$(uname -m)
  local needs_cross=0
  if [[ -n "$platform" ]]; then
    case "${host_arch}:${platform}" in
      x86_64:linux/arm64|amd64:linux/arm64)            needs_cross=1 ;;
      aarch64:linux/amd64|arm64:linux/amd64)           needs_cross=1 ;;
    esac
  fi
  if (( needs_cross == 1 )); then
    echo "WARNING: cross-building $tag — host=${host_arch} target=${platform}." >&2
    echo "  This is slow (5–15 min via QEMU). For native ARM64 batch builds," >&2
    echo "  prefer ``$(basename "$0") run launch $STATE_KEY --build`` which" >&2
    echo "  builds on the head node (t4g.large = ARM64), or ssh into the head" >&2
    echo "  via ``$(basename "$0") head ssh $STATE_KEY`` and build there." >&2
  fi

  echo "Building Docker image for alias '$STATE_KEY': $tag (local=$local_build, platform=${platform:-host})..."
  cd "$REPO_ROOT"
  local args=(-i "$tag")
  [[ $local_build -eq 1 ]] && args+=(-l)
  [[ -n "$platform" ]] && args+=(-p "$platform")
  bash runscripts/container/build-image.sh "${args[@]}"
}

ns_image_push() {
  # ``image push <alias> [--tag TAG]`` — ECR login + tag + push for the
  # alias's image. Prints the full ECR URI on success (also useful when
  # ``run launch <ray_alias>`` auto-resolves IMAGE_URI).
  local override_tag=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -t|--tag) override_tag="$2"; shift 2 ;;
      *) echo "image push: unknown arg $1" >&2; return 1 ;;
    esac
  done
  local tag; tag=$(_image_tag_for_alias "$override_tag") || return 1
  local remote; remote=$(_ecr_uri_for_tag "$tag")
  local ecr_host="${remote%/*}"
  echo "Logging in to ECR ($ecr_host)..."
  aws_cli ecr get-login-password \
    | docker login --username AWS --password-stdin "$ecr_host" >/dev/null
  echo "Tagging $tag -> $remote"
  docker tag "$tag" "$remote"
  echo "Pushing $remote..."
  docker push "$remote"
  echo "Done. ECR URI for alias '$STATE_KEY':"
  echo "  $remote"
}

ns_image_pull() {
  # ``image pull <alias> [--tag TAG]`` — pull from ECR, retag locally.
  local override_tag=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -t|--tag) override_tag="$2"; shift 2 ;;
      *) echo "image pull: unknown arg $1" >&2; return 1 ;;
    esac
  done
  local tag; tag=$(_image_tag_for_alias "$override_tag") || return 1
  local remote; remote=$(_ecr_uri_for_tag "$tag")
  local ecr_host="${remote%/*}"
  aws_cli ecr get-login-password \
    | docker login --username AWS --password-stdin "$ecr_host" >/dev/null
  docker pull "$remote"
  docker tag "$remote" "$tag"
  echo "Pulled $remote -> local $tag"
}

ns_image_list() {
  # Variant-independent — just lists ECR repository contents.
  local repo="${1:-vecoli}"
  echo "ECR repository: $repo"
  aws_cli ecr describe-images --repository-name "$repo" \
    --query 'sort_by(imageDetails,&imagePushedAt)[*].[imageTags[0],imagePushedAt,imageSizeInBytes]' \
    --output table 2>/dev/null
}

# Compare the alias's ECR image push time to the most recent git commit
# touching tracked code (ecoli/ + runscripts/). Flags whether the image
# Batch tasks will pull is up to date with your working tree.
#
# Why this matters: ``run launch`` defaults to ``--no-build-image``, so
# v1/v2 NF tasks reuse the cached ECR image. If you commit code (e.g. a
# threading pin) but don't pass ``--build`` on next launch, the AWS
# container runs PRE-commit code — silently — and your assumptions
# about what's deployed are wrong. This check makes that explicit.
ns_image_age() {
  local img_tag; img_tag=$(_alias_to_image "$STATE_KEY")
  if [[ -z "$img_tag" ]]; then
    echo "Alias '$STATE_KEY' has no image registered (method=${DEPLOY_MODE})"
    echo "  (mp/multiprocessing aliases don't need an image)"
    return 0
  fi
  local repo="${img_tag%%:*}"
  local tag="${img_tag##*:}"
  local pushed
  pushed=$(aws_cli ecr describe-images --repository-name "$repo" \
    --image-ids "imageTag=$tag" \
    --query 'imageDetails[0].imagePushedAt' --output text 2>/dev/null)
  if [[ -z "$pushed" || "$pushed" == "None" ]]; then
    echo "No image '${img_tag}' in ECR. Build with:"
    echo "  $(basename "$0") image build $STATE_KEY"
    return 1
  fi
  echo "Alias:         $STATE_KEY"
  echo "ECR image:     $img_tag"
  echo "Last pushed:   $pushed"

  # Latest git change to tracked source code that would be baked into
  # the image. Tracks both runscripts/ (driver) and ecoli/ (engine).
  local last_commit
  last_commit=$(cd "$REPO_ROOT" 2>/dev/null && \
    git log -1 --format='%aI %h %s' -- ecoli runscripts 2>/dev/null)
  if [[ -z "$last_commit" ]]; then
    echo "Latest commit: (no git history; can't determine staleness)"
    return 0
  fi
  echo "Latest commit: $last_commit"

  # Convert both to epoch for comparison.
  local pushed_epoch latest_epoch latest_iso
  latest_iso=$(echo "$last_commit" | awk '{print $1}')
  pushed_epoch=$(date -d "$pushed"     +%s 2>/dev/null)
  latest_epoch=$(date -d "$latest_iso" +%s 2>/dev/null)
  if [[ -z "$pushed_epoch" || -z "$latest_epoch" ]]; then
    echo "(could not parse timestamps for comparison)"
    return 0
  fi
  echo
  if (( pushed_epoch < latest_epoch )); then
    local hr=$(( (latest_epoch - pushed_epoch) / 3600 ))
    local mn=$(( ((latest_epoch - pushed_epoch) / 60) % 60 ))
    echo "→ STALE — image is ${hr}h ${mn}m older than the latest source commit."
    echo "  Batch tasks for this alias are running pre-commit code."
    echo "  Rebuild + relaunch with:"
    echo "    $(basename "$0") run launch $STATE_KEY --build"
    return 2
  else
    echo "→ IN SYNC — image was pushed after the latest source commit."
  fi
}

# --- 7. ``run`` namespace ---------------------------------------------------
ns_run_launch() {
  # Defaults match the fast-iteration loop:
  #   - skip in-workflow image rebuild (image namespace owns build/push;
  #     otherwise workflow.py prompts on stdin and hangs the head tmux)
  #   - rsync local working tree to the head + tell bootstrap to skip
  #     ``git reset --hard origin/composite`` so local edits actually
  #     run without requiring commit + push
  # Flags flip these:
  #   --build         rebuild + push image during the workflow run
  #   --from-origin   skip rsync, let bootstrap pull origin/composite
  #                   (use for clean / production runs from a tagged commit)
  #   --resume        reuse the variant's sidecar experiment_id
  local extra_env="" resume=0 build=0 from_origin=0
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --resume)              resume=1; shift ;;
      --build|--build-image) build=1;  shift ;;
      --from-origin)         from_origin=1; shift ;;
      *) echo "run launch: unknown arg '$1' (expected --resume | --build | --from-origin)" >&2
         return 1 ;;
    esac
  done
  (( resume == 1 )) && extra_env+="RESUME=1 "
  (( build  == 1 )) && extra_env+="BUILD_IMAGE=1 "

  if (( resume == 1 )); then
    if [[ -z "$EXP_ID" ]]; then
      echo "run launch --resume $STATE_KEY: no experiment_id known." >&2
      echo "Run a fresh ``run launch $STATE_KEY`` first." >&2
      return 1
    fi
    echo "Resuming experiment_id=$EXP_ID  (state: $STATE_FILE)"
  else
    # Fresh launch: rotate the per-variant sidecar.
    _persist_new_exp_id
  fi

  local dns; dns=$(get_running_dns)
  if [[ -z "$dns" || "$dns" == "None" ]]; then
    echo "no running head ($HEAD_NAME)." >&2
    echo "Run: $(basename "$0") head setup $STATE_KEY" >&2
    return 1
  fi

  # Auto-rsync local repo to head unless --from-origin was passed.
  # SKIP_GIT_RESET=1 tells the bootstrap to trust the rsynced files
  # rather than blowing them away with ``git reset --hard``.
  if (( from_origin == 0 )); then
    echo "Rsyncing local repo → ec2-user@${dns}:~/vEcoli/ (skipping .git/.venv/out/)..."
    _rsync_repo_to_head "$dns"
    extra_env+="SKIP_GIT_RESET=1 "
  else
    echo "(--from-origin) skipping local rsync; bootstrap will pull origin/composite."
  fi

  # Kill any stale tmux session for this variant before re-launching
  # (only on fresh launch — resume reattaches the running session).
  # For ray/ray_colony, also terminate the prior cluster's EC2s — tmux
  # kill SIGKILLs python before ``with cluster:`` __exit__ runs, so
  # without this the cluster head + workers from the previous launch
  # keep running (and cost $$).
  if (( resume == 0 )); then
    ssh -i "$KEY_FILE" -o StrictHostKeyChecking=no "ec2-user@$dns" \
      "tmux has-session -t '$TMUX_SESSION' 2>/dev/null \
         && (echo '  killing stale tmux session: $TMUX_SESSION'; \
             tmux kill-session -t '$TMUX_SESSION') \
         || true"
    _terminate_ray_cluster_for_alias "$dns"
  fi

  _run_bootstrap_on_head "$extra_env"

  local cli; cli=$(basename "$0")
  echo
  echo "Launched alias=$STATE_KEY  session=$TMUX_SESSION  exp_id=$EXP_ID"
  echo "Next:"
  echo "  $cli run tail   $STATE_KEY    # follow live tmux log"
  echo "  $cli run status $STATE_KEY    # head + tmux + Batch + S3 dashboard"
  echo "  $cli run log    $STATE_KEY    # driver + cluster log (post-run)"
  echo "  $cli run cancel $STATE_KEY    # kill tmux + Batch jobs"
}
ns_run_resume() { ns_run_launch --resume "$@"; }

# Print the active experiment_id (sidecar value, or BASE if no run yet).
ns_run_id() {
  if [[ -f "$STATE_FILE" ]]; then
    echo "$EXP_ID  (from $STATE_FILE)"
  else
    echo "$EXP_ID  (no sidecar — using config base; ``run launch $STATE_KEY`` to assign)"
  fi
}
# Terminate the most-recent Ray cluster spawned by this alias's driver.
# The driver prints ``→ cluster_id=vecoli-ray-<timestamp>`` to its
# workflow log on first bringup; we grep the latest such line, then
# terminate every EC2 instance tagged ``process-bigraph-cluster=<id>``.
# Safe for non-ray aliases: returns silently if no cluster_id is found.
#
# When the driver is killed via ``tmux kill-session``, Python is
# SIGKILLed before the ``with cluster:`` context manager can run its
# __exit__ → the head/worker EC2s leak. This is the recovery path.
_terminate_ray_cluster_for_alias() {
  local dns="$1"
  # Only meaningful for the ray family.
  case "$DEPLOY_MODE" in
    ray_cluster|ray_colony_cluster) ;;
    *) return 0 ;;
  esac
  local log="\$HOME/${TMUX_SESSION}_workflow.log"
  local cluster_id
  cluster_id=$(ssh -i "$KEY_FILE" -o StrictHostKeyChecking=no \
      "ec2-user@$dns" "grep -oE 'cluster_id=vecoli-ray-[0-9]+' $log 2>/dev/null | tail -1 | sed 's/^cluster_id=//'" \
      2>/dev/null | tr -d '\r')
  if [[ -z "$cluster_id" ]]; then
    echo "  (no cluster_id found in $log on head — nothing to terminate)"
    return 0
  fi
  echo "Terminating Ray cluster instances tagged process-bigraph-cluster=$cluster_id..."
  local ids
  ids=$(aws_cli ec2 describe-instances \
        --filters "Name=tag:process-bigraph-cluster,Values=$cluster_id" \
                  "Name=instance-state-name,Values=pending,running,stopping,stopped" \
        --query 'Reservations[].Instances[].InstanceId' --output text 2>/dev/null \
        | tr '\t\r' '\n' | awk 'NF')
  if [[ -z "$ids" ]]; then
    echo "  (no live instances for cluster_id=$cluster_id)"
    return 0
  fi
  # shellcheck disable=SC2086
  aws_cli ec2 terminate-instances --instance-ids $ids >/dev/null
  for id in $ids; do echo "  terminated $id"; done
}

ns_run_cancel() {
  local dns; dns=$(require_running_dns)
  echo "Killing tmux session '$TMUX_SESSION' on $dns..."
  ssh -i "$KEY_FILE" "ec2-user@$dns" "tmux kill-session -t $TMUX_SESSION 2>/dev/null || echo '  (no session to kill)'"
  # Ray/colony: also nuke the cluster EC2s the driver spawned. tmux
  # kill SIGKILLs python before its ``with cluster:`` __exit__ can run.
  _terminate_ray_cluster_for_alias "$dns"
  if [[ -n "$QUEUE" ]]; then
    echo "Canceling RUNNING + RUNNABLE Batch jobs in queue $QUEUE..."
    for s in RUNNING RUNNABLE STARTING; do
      local ids
      ids=$(aws_cli batch list-jobs --job-queue "$QUEUE" --job-status "$s" \
        --query 'jobSummaryList[*].jobId' --output text 2>/dev/null | tr -d '\r')
      for id in $ids; do
        aws_cli batch terminate-job --job-id "$id" --reason "vecoli-aws cancel" >/dev/null
        echo "  terminated $id ($s)"
      done
    done
  fi
}
ns_run_status() {
  # Coherent, EXP_ID-scoped report. Sections:
  #   1. Header        — alias / method / experiment_id / config / output URI
  #   2. Lifecycle     — when launched, time since
  #   3. Infrastructure — head EC2 state + tmux session liveness
  #   4. Workload      — Batch counts SCOPED to this experiment (vs queue total)
  #   5. Output        — S3 object count, last write age, last 3 writes
  #   6. Tail-on-crash — last 20 lines of driver log when tmux died
  local method; method=$(_alias_to_method "$STATE_KEY")
  local since_ms; since_ms=$(_exp_id_to_epoch_ms "$EXP_ID")
  local now_s; now_s=$(date -u +%s)

  # 1. Header ----------------------------------------------------------------
  local header_w=72
  printf '%s\n' "$(printf '═%.0s' $(seq 1 $header_w))"
  printf '  %s  /  %s  /  %s\n' \
    "$STATE_KEY" "${method:-?}" "$EXP_ID"
  printf '%s\n' "$(printf '═%.0s' $(seq 1 $header_w))"
  printf '  Config       %s\n' "$CONFIG_REL"
  printf '  Output       s3://%s/%s/%s/\n' "$BUCKET" "$PREFIX" "$EXP_ID"

  # 2. Lifecycle -------------------------------------------------------------
  if (( since_ms > 0 )); then
    local since_s=$(( since_ms / 1000 ))
    local age=$(( now_s - since_s ))
    local launched_at
    launched_at=$(date -u -d "@$since_s" +'%Y-%m-%d %H:%M:%S UTC' 2>/dev/null || echo "?")
    printf '  Launched     %s  (%s ago)\n' "$launched_at" "$(_human_duration "$age")"
  else
    printf '  Launched     (no sidecar timestamp — using BASE id from config)\n'
  fi

  # 3. Infrastructure --------------------------------------------------------
  echo
  local id state dns tmux_alive=0
  id=$(get_instance_id)
  if [[ -z "$id" || "$id" == "None" ]]; then
    printf '  Head         %-25s  none provisioned\n' "$HEAD_NAME"
  else
    state=$(aws_cli ec2 describe-instances --instance-ids "$id" \
      --query 'Reservations[0].Instances[0].State.Name' --output text 2>/dev/null)
    dns=$(aws_cli ec2 describe-instances --instance-ids "$id" \
      --query 'Reservations[0].Instances[0].PublicDnsName' --output text 2>/dev/null)
    printf '  Head         %-25s  %-9s  %s\n' "$HEAD_NAME" "${state:-?}" "$id"
    [[ -n "${dns:-}" && "$dns" != "None" ]] && printf '  DNS          %s\n' "$dns"
    if [[ "$state" == "running" && -n "${dns:-}" && "$dns" != "None" ]]; then
      if ssh -i "$KEY_FILE" -o StrictHostKeyChecking=no -o ConnectTimeout=5 \
           "ec2-user@$dns" "tmux ls 2>/dev/null | grep -qF '$TMUX_SESSION'" 2>/dev/null; then
        printf '  Tmux         %-25s  alive\n' "$TMUX_SESSION"
        tmux_alive=1
      else
        printf '  Tmux         %-25s  dead (workflow ended)\n' "$TMUX_SESSION"
      fi
    fi
  fi

  # 4. Workload --------------------------------------------------------------
  # One server-side filtered list-jobs call (AFTER_CREATED_AT=exp launch),
  # grouped by status client-side. Replaces a 14-call per-status loop that
  # scanned the queue's lifetime SUCCEEDED history on every invocation.
  if [[ -n "$QUEUE" ]]; then
    echo
    if (( since_ms > 0 )); then
      printf '  Batch %-22s  this run\n' "$QUEUE"
      local rows
      rows=$(_batch_counts_since_filtered "$since_ms")
      if [[ -z "$rows" ]]; then
        printf '    (no jobs created since launch yet)\n'
      else
        while IFS=$'\t' read -r s c; do
          printf '    %-12s %8s\n' "$s" "$c"
        done <<< "$rows"
      fi
    else
      printf '  Batch %-22s  (no exp timestamp — skipping queue scan)\n' "$QUEUE"
      printf '    Use: %s run jobs %s [STATUS] for a per-status listing.\n' \
        "$(basename "$0")" "$STATE_KEY"
    fi
  fi

  # 5. Output ----------------------------------------------------------------
  echo
  local exp_root="s3://$BUCKET/$PREFIX/$EXP_ID/"
  printf '  Output       %s\n' "$exp_root"
  local s3_lines
  s3_lines=$(aws_cli s3 ls --recursive --summarize "$exp_root" 2>/dev/null || true)
  local n_objects total_bytes
  n_objects=$(echo "$s3_lines" | awk '/Total Objects:/ {print $NF}')
  total_bytes=$(echo "$s3_lines" | awk '/Total Size:/ {print $NF}')
  if [[ -z "$n_objects" || "$n_objects" == "0" ]]; then
    printf '    (no objects yet under EXP_ID prefix — workflow may still be in setup)\n'
  else
    # Format size into a readable unit
    local size_h="$total_bytes"
    if [[ "$total_bytes" =~ ^[0-9]+$ ]]; then
      if   (( total_bytes >= 1073741824 )); then
        size_h=$(awk -v b="$total_bytes" 'BEGIN { printf "%.1f GB", b/1073741824 }')
      elif (( total_bytes >= 1048576 )); then
        size_h=$(awk -v b="$total_bytes" 'BEGIN { printf "%.1f MB", b/1048576 }')
      elif (( total_bytes >= 1024 )); then
        size_h=$(awk -v b="$total_bytes" 'BEGIN { printf "%.1f KB", b/1024 }')
      else
        size_h="${total_bytes} B"
      fi
    fi
    printf '    files       %s objects, %s\n' "$n_objects" "$size_h"

    # Filter to data lines only (ignore the trailing "Total Objects:" /
    # "Total Size:" summary lines, which can have leading whitespace).
    # Data lines start with YYYY-MM-DD, so a date-prefix awk match is
    # the cleanest filter.
    local data_lines
    data_lines=$(echo "$s3_lines" | awk '/^[0-9]{4}-[0-9]{2}-[0-9]{2} /')

    # ``aws s3 ls`` prints timestamps in the user's LOCAL time (not UTC),
    # per the AWS CLI docs. Parse as local (date -d, NOT -u -d) for the
    # epoch diff, then re-format as UTC for display so this matches the
    # "Launched ... UTC" line above.
    local last_line
    last_line=$(echo "$data_lines" | sort -k1,2 | tail -1)
    if [[ -n "$last_line" ]]; then
      local last_date last_time last_s
      last_date=$(echo "$last_line" | awk '{print $1}')
      last_time=$(echo "$last_line" | awk '{print $2}')
      last_s=$(date -d "${last_date} ${last_time}" +%s 2>/dev/null || echo 0)
      if (( last_s > 0 )); then
        local age=$(( now_s - last_s ))
        local last_utc; last_utc=$(date -u -d "@$last_s" +'%Y-%m-%d %H:%M:%S' 2>/dev/null)
        printf '    last write  %s ago  (%s UTC)\n' \
          "$(_human_duration "$age")" "$last_utc"
      fi
    fi
    printf '    recent\n'
    echo "$data_lines" | sort -k1,2 | tail -3 | while read -r d t sz path; do
      local s; s=$(date -d "$d $t" +%s 2>/dev/null || echo 0)
      if (( s > 0 )); then
        local utc; utc=$(date -u -d "@$s" +'%Y-%m-%d %H:%M:%S')
        printf '      %s UTC  %8s  %s\n' "$utc" "$sz" "$path"
      else
        printf '      %s %s  %8s  %s\n' "$d" "$t" "$sz" "$path"
      fi
    done
  fi

  # 6. Tail-on-crash ---------------------------------------------------------
  if [[ -n "${dns:-}" && "${state:-}" == "running" && "$tmux_alive" -eq 0 ]]; then
    echo
    echo "  Tmux dead, head alive — last 20 driver-log lines below."
    echo "  Full diagnostics: $(basename "$0") run log $STATE_KEY"
    ssh -i "$KEY_FILE" -o StrictHostKeyChecking=no "ec2-user@$dns" \
      "F=\$HOME/${TMUX_SESSION}_workflow.log; \
       [[ -f \$F ]] || F=\$HOME/v2_workflow.log; \
       [[ -f \$F ]] && tail -20 \"\$F\" | sed 's/^/    /' \
       || echo '    (no log file at \$F)'" 2>/dev/null
  fi
  echo
}
ns_run_jobs() {
  [[ -n "$QUEUE" ]] || { echo "no Batch queue (variant=${DEPLOY_MODE})"; return; }
  local s="${1:-RUNNING}"
  aws_cli batch list-jobs --job-queue "$QUEUE" --job-status "$s" \
    --query 'jobSummaryList[*].[jobName,createdAt]' --output table
}

# Resolve a config key while walking ``inherit_from`` parents (vEcoli
# configs commonly inherit shape — n_init_sims / generations — from a
# base file like comparison_10s_16g.json). Returns empty if the key is
# absent at every level. ``inherit_from`` paths resolve against
# ``<repo>/configs/`` to match ecoli_master_sim.py's loader behavior.
# Usage: ``_read_cfg_with_inherit "['n_init_sims']"``.
_read_cfg_with_inherit() {
  local key="$1"
  python3 - "$CONFIG_ABS" "$REPO_ROOT/configs" "$key" <<'PYEOF'
import json, os, sys
config_path, config_dir, key = sys.argv[1], sys.argv[2], sys.argv[3]
def walk(path):
    if not os.path.exists(path): return {}
    cfg = json.load(open(path))
    parents = cfg.get('inherit_from', [])
    if isinstance(parents, str): parents = [parents]
    merged = {}
    for p in parents:
        parent_path = p if os.path.isabs(p) else os.path.join(config_dir, p)
        merged.update(walk(parent_path))
    cfg.pop('inherit_from', None)
    merged.update(cfg)
    return merged
c = walk(config_path)
try:
    exec(f"print(c{key})")
except (KeyError, IndexError, TypeError, NameError):
    pass
PYEOF
}

# Coverage matrix: which (lineage_seed, generation) partitions have any
# output in S3 under the active EXP_ID? Renders a seeds × gens grid
# (X = present, . = missing) plus a summary count + miss list. Works
# across all variants because the hive layout
# (``history/experiment_id=*/variant=*/lineage_seed=*/generation=*/...``)
# is the same regardless of engine.
#
# Optional overrides for non-standard configs:
#   --seeds N    expected number of init seeds (default: config)
#   --gens N     expected number of generations (default: config)
ns_run_coverage() {
  local n_seeds="" n_gens=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --seeds) n_seeds="$2"; shift 2 ;;
      --gens)  n_gens="$2";  shift 2 ;;
      -h|--help)
        echo "usage: run coverage <alias> [--seeds N] [--gens N]"
        echo "Prints a seeds × gens grid showing which (lineage_seed, generation)"
        echo "partitions have output under the active EXP_ID."
        return 0 ;;
      *) echo "run coverage: unknown arg '$1'" >&2; return 1 ;;
    esac
  done

  # Auto-detect shape from config (walks inherit_from). Fall through to
  # legacy defaults if the config is unusual.
  [[ -z "$n_seeds" ]] && n_seeds=$(_read_cfg_with_inherit "['n_init_sims']")
  [[ -z "$n_gens"  ]] && n_gens=$(_read_cfg_with_inherit "['generations']")
  [[ -z "$n_seeds" ]] && n_seeds=10
  [[ -z "$n_gens"  ]] && n_gens=16

  local hist_root="s3://${BUCKET}/${PREFIX}/${EXP_ID}/history/"
  printf 'Coverage for %s\n' "$EXP_ID"
  printf 'Expected: %s seeds × %s gens = %s cell-gens\n' \
    "$n_seeds" "$n_gens" "$((n_seeds * n_gens))"
  printf 'Scanning %s ...\n' "$hist_root"

  # One recursive S3 listing, distill to distinct partition keys.
  local pairs
  pairs=$(aws_cli s3 ls --recursive "$hist_root" 2>/dev/null \
    | grep -oE 'lineage_seed=[0-9]+/generation=[0-9]+' \
    | sort -u)

  if [[ -z "$pairs" ]]; then
    echo "  (no history/ output found under $hist_root)"
    return 0
  fi

  echo
  PAIRS_DATA="$pairs" N_SEEDS="$n_seeds" N_GENS="$n_gens" python3 <<'PYEOF'
import os
n_seeds = int(os.environ['N_SEEDS'])
n_gens  = int(os.environ['N_GENS'])
pairs = set()
for line in os.environ['PAIRS_DATA'].splitlines():
    line = line.strip()
    if not line:
        continue
    seed_part, gen_part = line.split('/')
    s = int(seed_part.split('=')[1])
    g = int(gen_part.split('=')[1])
    pairs.add((s, g))

# Parquet emitter sets generation = len(agent_id), so partition values
# run 1..N_GENS (not 0..N_GENS-1). Internal generation index i maps to
# partition value i+1. We label columns with both for clarity.
gen_range = range(1, n_gens + 1)
gen_hdr = ''.join(f'{g:>3}' for g in gen_range)
print(f"  seed\\gen  {gen_hdr}    (gen = len(agent_id); internal gen = partition - 1)")
print(f"  {'-' * (10 + 3 * n_gens)}")

missing = []
for s in range(n_seeds):
    row = ''.join(('  X' if (s, g) in pairs else '  .')
                  for g in gen_range)
    print(f"  {s:>4}     {row}")
    for g in gen_range:
        if (s, g) not in pairs:
            missing.append((s, g))

total = n_seeds * n_gens
present = total - len(missing)
print()
print(f"  Present: {present}/{total}  ({100*present/total:.1f}%)")
if missing:
    print(f"  Missing: {len(missing)} cell-gen(s)")
    head = ", ".join(f"(seed={s},gen={g})" for s, g in missing[:20])
    if len(missing) <= 20:
        print(f"    {head}")
    else:
        print(f"    (first 20 of {len(missing)}): {head}")
PYEOF
}
ns_run_tail() {
  local log="~/${TMUX_SESSION}_workflow.log"
  exec ssh -i "$KEY_FILE" "ec2-user@$(require_running_dns)" \
    "F=${log/#\~/\$HOME}; \
     [[ -f \$F ]] || F=\$HOME/v2_workflow.log; \
     tail -f \$F | sed -u 's/\\x1b\\[[0-9;]*[a-zA-Z]//g; s/\\x1b\\][0-9];[^\\x07]*\\x07//g'"
}

# Head-state diagnostic script body — printed to stdout, intended to
# be fed as the remote command for an ssh call. Used by ``run diag``
# directly and inlined by ``run log`` when neither the workflow log
# nor the bootstrap log exists (the situation where normal log paths
# are dry and you need to know what's actually on the instance).
#
# What each section answers:
#   whoami/HOME/pwd    — rule out path-resolution surprises (logs in
#                        a different $HOME, ec2-user vs root)
#   ~/ contents        — what files ARE on the head (we expect
#                        ${TMUX_SESSION}_workflow.log and
#                        bootstrap_${TMUX_SESSION}.log)
#   tmux ls            — session really gone vs. hidden by a name typo
#   running procs      — leftover driver / ray / tmux processes that
#                        should have cleaned up after workflow end
#   uptime -s          — when THIS instance came up. If after the
#                        experiment launch timestamp, this head is a
#                        replacement and the original head (with the
#                        real logs) is gone.
#   df -h              — full disk wipes tee'd writes silently
#   dmesg OOM          — kernel-killed processes (driver runs in tmux
#                        with no memory cgroup; a Python OOM kills tee)
#   cloud-init-output  — head's own startup log (bootstrap chatter
#                        before the script's own logfile is open)
#   /var/log/messages  — systemd / kernel errors
#
# TMUX_SESSION expands LOCALLY (heredoc with unquoted EOF) so printed
# filenames match the alias. ``\$HOME`` etc. are escaped so they
# expand REMOTELY.
_head_diag_remote_script() {
  cat <<DIAG_EOF
echo '=== whoami / HOME / pwd ==='
whoami; echo HOME=\$HOME; pwd
echo
echo '=== ~/ contents (looking for ${TMUX_SESSION}_workflow.log, bootstrap_${TMUX_SESSION}.log) ==='
ls -la \$HOME/ 2>/dev/null | head -40
echo
echo '=== tmux sessions ==='
tmux ls 2>&1 || true
echo
echo '=== running python/ray/ec2_cluster/tmux procs ==='
ps -ef | grep -E 'python|ray|ec2_cluster|tmux' | grep -v grep | head -20 || true
echo
echo '=== instance uptime -s (compare to experiment launch timestamp) ==='
uptime -s
echo
echo '=== disk usage ==='
df -h \$HOME /tmp /var/log 2>/dev/null | head -10 || true
echo
echo '=== dmesg: OOM kills ==='
sudo dmesg 2>/dev/null | grep -iE 'killed process|out of memory|oom' | tail -5 || echo '  (none)'
echo
echo '=== cloud-init-output tail (when bootstrap ran on this instance) ==='
sudo tail -20 /var/log/cloud-init-output.log 2>/dev/null || echo '  (no cloud-init-output)'
echo
echo '=== /var/log/messages errors (last 10) ==='
sudo grep -iE 'error|fail|kill' /var/log/messages 2>/dev/null | tail -10 || echo '  (no /var/log/messages access)'
DIAG_EOF
}

# Head-state diagnostic. Useful when ``run log`` finds nothing —
# auto-invoked from ``run log`` in that case; also callable directly.
ns_run_diag() {
  local dns; dns=$(get_running_dns)
  if [[ -z "$dns" || "$dns" == "None" ]]; then
    echo "(head $HEAD_NAME not running — nothing to diagnose)"
    return 0
  fi
  echo "=== Head-state diagnostic on $dns (alias=$STATE_KEY, session=$TMUX_SESSION) ==="
  ssh -i "$KEY_FILE" -o StrictHostKeyChecking=no "ec2-user@$dns" \
    "$(_head_diag_remote_script)" 2>/dev/null || true
}

ns_run_log() {
  # Pull both the cluster's experiment log (uploaded to S3 at
  # workflow end) and the head node's driver log (still on the
  # local instance disk). Useful after a failed/finished run when
  # you want to see WHY it ended without attaching to tmux.
  #
  # Args (optional):
  #   -n N    show last N lines instead of full log (default: full)
  local n=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -n) n="$2"; shift 2 ;;
      *)  echo "usage: run log [-n N]" >&2; return 1 ;;
    esac
  done

  echo "=== Driver log (head node ~/${TMUX_SESSION}_workflow.log) ==="
  local dns; dns=$(get_running_dns)
  if [[ -n "$dns" && "$dns" != "None" ]]; then
    # Try workflow log first; if it doesn't exist, fall back to the
    # bootstrap log (bootstrap_<SESSION>.log) which is tee'd by every
    # bootstrap_head*.sh from the moment it starts — captures crashes
    # that occur before tmux launch, when no workflow log was ever
    # opened. ``tail -n N`` if -n was given, full file otherwise.
    local strip_ansi="sed -u 's/\\x1b\\[[0-9;]*[a-zA-Z]//g; s/\\x1b\\][0-9];[^\\x07]*\\x07//g'"
    local reader="cat"
    [[ -n "$n" ]] && reader="tail -n $n"
    # Inline the head-state diagnostic in the no-logs branch so a
    # single ssh call reports both "nothing here" AND the state
    # needed to figure out why (replacement head, OOM kill, full
    # disk, etc.). One ssh roundtrip keeps the common log-found
    # path cheap.
    local diag_script; diag_script=$(_head_diag_remote_script)
    local cmd
    cmd="WF=\$HOME/${TMUX_SESSION}_workflow.log
[[ -f \$WF ]] || WF=\$HOME/v2_workflow.log
if [[ -f \$WF ]]; then
  $reader \$WF | $strip_ansi
else
  BL=\$HOME/bootstrap_${TMUX_SESSION}.log
  if [[ -f \$BL ]]; then
    echo '(no workflow log — bootstrap never reached tmux launch.'
    echo ' showing bootstrap log instead: ~/bootstrap_${TMUX_SESSION}.log)'
    echo
    $reader \$BL | $strip_ansi
  else
    echo 'no driver log on head (and no bootstrap log either —'
    echo 'either head is fresh or bootstrap died before redirect setup)'
    echo
    echo '=== Head-state diagnostic ==='
$diag_script
  fi
fi"
    ssh -i "$KEY_FILE" "ec2-user@$dns" "$cmd" || true
  else
    echo "(head $HEAD_NAME not running — skipping driver log)"
  fi

  # Variant-specific cluster log:
  #   ray_cluster      → s3://.../lineage_ray.log (uploaded by
  #                      ec2_cluster_ray.py at workflow end)
  #   mp_single_node   → driver log only (the MP runner stdouts to
  #                      tmux, no S3 upload)
  #   nextflow_batch   → per-task .command.{out,err} on each Batch
  #                      task; we surface ``trace--<exp>--*.csv`` /
  #                      analyses dir but task stderr lives in CW
  echo
  case "${DEPLOY_MODE:-nextflow_batch}" in
    ray_cluster)
      local exp_log="s3://${BUCKET}/${PREFIX}/${EXP_ID}/lineage_ray.log"
      echo "=== Cluster experiment log (${exp_log}) ==="
      if aws_cli s3 ls "$exp_log" >/dev/null 2>&1; then
        if [[ -n "$n" ]]; then
          aws_cli s3 cp "$exp_log" - 2>/dev/null \
            | tail -n "$n" \
            | sed -u 's/\x1b\[[0-9;]*[a-zA-Z]//g'
        else
          aws_cli s3 cp "$exp_log" - 2>/dev/null \
            | sed -u 's/\x1b\[[0-9;]*[a-zA-Z]//g'
        fi
      else
        echo "(no cluster log at $exp_log — workflow either still running"
        echo " or didn't reach the upload step)"
      fi
      ;;
    mp_single_node)
      echo "(MP variant: experiment log == driver log above; the MP"
      echo " runner streams to tmux and isn't uploaded separately)"
      ;;
    nextflow_batch|"")
      # Pull the stderr of any FAILED nextflow tasks. Nextflow's
      # driver log contains lines like
      #   [xx/yyyyyy] NOTE: Process `runParca` failed
      # where xx/yyy is the workdir hash. We scan the driver log for
      # those, then aws s3 cp each task's .command.err.
      # Layout: <bucket>/<config.out_uri_path>/<EXP_ID>/nextflow/nextflow_workdirs
      # PREFIX already contains the config-level path; just append EXP_ID once.
      local nf_workroot="s3://${BUCKET}/${PREFIX}/${EXP_ID}/nextflow/nextflow_workdirs"
      local failed_hashes=""
      if [[ -n "$dns" && "$dns" != "None" ]]; then
        # Re-fetch the driver log fresh (the earlier ssh call already
        # printed it; here we just need the failed-task hashes).
        failed_hashes=$(ssh -i "$KEY_FILE" "ec2-user@$dns" \
          "F=\$HOME/${TMUX_SESSION}_workflow.log; \
           [[ -f \$F ]] || F=\$HOME/v2_workflow.log; \
           [[ -f \$F ]] && grep -oE '\\[[0-9a-f]{2}/[0-9a-f]{6,}\\] NOTE: Process .* failed' \$F \
             | grep -oE '[0-9a-f]{2}/[0-9a-f]{6,}' | sort -u" 2>/dev/null || true)
      fi
      if [[ -n "$failed_hashes" ]]; then
        echo "=== Failed Nextflow task stderr ==="
        for hash in $failed_hashes; do
          echo
          echo "--- $hash ---"
          # ``|| true`` on the err_key pipeline because grep returns 1
          # when no .command.err is found (or when the s3 ls path is
          # empty), and set -e + pipefail would kill the script before
          # we can print the helpful "no .command.err" fallback.
          local err_key
          err_key=$(aws_cli s3 ls --recursive "${nf_workroot}/${hash}" 2>/dev/null \
                   | awk '{print $NF}' \
                   | grep '/.command.err$' \
                   | head -1 || true)
          if [[ -n "$err_key" ]]; then
            if [[ -n "${n:-}" ]]; then
              aws_cli s3 cp "s3://${BUCKET}/${err_key}" - 2>/dev/null | tail -n "$n" || true
            else
              aws_cli s3 cp "s3://${BUCKET}/${err_key}" - 2>/dev/null || true
            fi
          else
            echo "(no .command.err under ${nf_workroot}/${hash})"
          fi
        done
      fi

      # When .command.err is missing from S3, the task usually failed
      # at the Batch / Docker / IAM layer before Nextflow's wrapper
      # could upload its stderr. The container's actual output lives
      # in CloudWatch Logs (/aws/batch/job). Surface that automatically
      # so the user doesn't have to chase logStreamName by hand.
      #
      # Filter to jobs created since the active EXP_ID was assigned, so
      # the table reflects ONLY this run's failures (not the queue's
      # stale history). EXP_ID's trailing _YYYYMMDD-HHMMSS is the
      # sidecar timestamp; convert to epoch ms (the field Batch uses).
      if [[ -n "$QUEUE" ]]; then
        echo
        local since_ms=0
        local stamp="${EXP_ID##*_}"  # last _-segment, e.g. 20260510-062723
        if [[ "$stamp" =~ ^[0-9]{8}-[0-9]{6}$ ]]; then
          local d="${stamp:0:8}" t="${stamp:9:6}"
          local iso="${d:0:4}-${d:4:2}-${d:6:2}T${t:0:2}:${t:2:2}:${t:4:2}Z"
          since_ms=$(date -u -d "$iso" +%s%3N 2>/dev/null || echo 0)
        fi
        echo "=== Recent FAILED Batch jobs in $QUEUE${since_ms:+  (since $stamp)} ==="
        # Pull jobId + createdAt, filter by since_ms, take 5 most recent.
        local failed_rows
        failed_rows=$(aws_cli batch list-jobs --job-queue "$QUEUE" \
            --job-status FAILED \
            --query 'jobSummaryList[*].[jobId,createdAt]' \
            --output text 2>/dev/null \
          | awk -v since="$since_ms" 'since==0 || $2 >= since { print $1, $2 }' \
          | sort -k2,2 -nr \
          | head -5 || true)
        local failed_job_ids
        failed_job_ids=$(echo "$failed_rows" | awk '{print $1}' | grep -v '^$' || true)
        if [[ -z "$failed_job_ids" ]]; then
          echo "  (no FAILED jobs since this launch)"
        else
          # shellcheck disable=SC2086
          aws_cli batch describe-jobs --jobs $failed_job_ids \
            --query 'jobs[*].[jobId,jobName,createdAt,statusReason,container.exitCode]' \
            --output table 2>/dev/null || true
          for jid in $failed_job_ids; do
            local stream
            stream=$(aws_cli batch describe-jobs --jobs "$jid" \
              --query 'jobs[0].container.logStreamName' --output text 2>/dev/null \
              || echo "")
            if [[ -z "$stream" || "$stream" == "None" ]]; then
              continue
            fi
            local jobname
            jobname=$(aws_cli batch describe-jobs --jobs "$jid" \
              --query 'jobs[0].jobName' --output text 2>/dev/null || echo "?")
            echo
            echo "--- CloudWatch /aws/batch/job  $jobname  ($jid) ---"
            echo "    stream: $stream"
            local cw_lines="${n:-30}"
            aws_cli logs get-log-events --log-group-name /aws/batch/job \
              --log-stream-name "$stream" --limit "$cw_lines" \
              --query 'events[*].message' --output text 2>/dev/null \
              | tr '\t' '\n' | sed 's/^/  /' \
              || echo "  (couldn't fetch CloudWatch events; check /aws/batch/job manually)"
          done
        fi
      fi

      local trace; trace=$(ls -t "$REPO_ROOT"/trace--"$EXP_ID"--*.csv 2>/dev/null | head -1)
      if [[ -n "$trace" ]]; then
        echo
        echo "=== Nextflow trace (${trace##*/}) ==="
        echo "Sample (first 5 task rows):"
        head -1 "$trace"
        head -6 "$trace" | tail -5
        echo
        echo "(For per-task stderr beyond the failed ones above, check"
        echo " CloudWatch Logs group /aws/batch/job for jobName"
        echo " matching '${EXP_ID}_*'.)"
      else
        echo
        echo "(No local trace--${EXP_ID}--*.csv yet — run"
        echo " ``$(basename "$0") compare report`` to fetch it from S3 first.)"
      fi
      ;;
  esac
}

# Render a runtime curve from the alias's driver workflow log.
#
# Parses the ``[ray-colony] heartbeat: sim_time=Xs cells=N wall=Ws``
# lines that run_colony_ray.py emits each minute of wall-clock and
# plots sim-seconds-per-wall-second vs sim_time (top panel) alongside
# cell count (bottom panel), with vertical markers at observed
# divisions. Useful for diagnosing per-cell slowdown as a colony grows.
#
# Args (optional):
#   -o PATH   output PNG path (default: out/colony_runtime_<exp_id>.png)
ns_run_timing() {
  local out_path=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -o|--output) out_path="$2"; shift 2 ;;
      -h|--help)
        cat >&2 <<USAGE
usage: $(basename "$0") run timing <alias> [-o path/to/plot.png]

Fetches the alias's driver workflow log from its head node, parses the
heartbeat lines, and renders a runtime curve PNG locally. Works while
the run is in flight (heartbeats are written as the sim advances) and
after completion.
USAGE
        return 0 ;;
      *) echo "run timing: unknown arg '$1'" >&2; return 1 ;;
    esac
  done
  [[ -z "$out_path" ]] && out_path="out/colony_runtime_${EXP_ID}.png"

  local dns; dns=$(require_running_dns)
  local tmpdir; tmpdir=$(mktemp -d)
  # Clean up tmpdir even on failure paths. ``RETURN`` traps in bash are
  # global (not function-scoped), so this fires again when the outer
  # _dispatch_variant returns — at which point ``tmpdir`` is no longer
  # in scope. ``${tmpdir:-}`` makes the second firing a no-op rm of ""
  # rather than tripping ``set -u``.
  trap 'rm -rf "${tmpdir:-}"' RETURN

  echo "Fetching workflow log from $dns:~/${TMUX_SESSION}_workflow.log..."
  scp -i "$KEY_FILE" -o StrictHostKeyChecking=no \
      "ec2-user@$dns:~/${TMUX_SESSION}_workflow.log" \
      "$tmpdir/log" 2>/dev/null || {
    echo "  no log at ~/${TMUX_SESSION}_workflow.log on head" >&2
    echo "  (alias has no run yet, or the driver hasn't logged anything)" >&2
    return 1
  }

  mkdir -p "$(dirname "$REPO_ROOT/$out_path")"
  if [[ -x "$REPO_ROOT/.venv/bin/python" ]]; then
    PY=( "$REPO_ROOT/.venv/bin/python" )
  else
    PY=( uv run --no-sync python )
  fi
  "${PY[@]}" "$REPO_ROOT/runscripts/plot_colony_runtime.py" \
    --log "$tmpdir/log" \
    -o "$REPO_ROOT/$out_path" \
    --title "$STATE_KEY  ${EXP_ID}"
  echo "Plot: $REPO_ROOT/$out_path"
}

# --- 8. ``cache`` namespace -------------------------------------------------
ns_cache() {
  local sub="${1:-help}"; shift || true
  local s3_cache="s3://${BUCKET}/_cache/${EXP_ID}/.nextflow"
  case "$sub" in
    push)
      local dns; dns=$(require_running_dns)
      echo "Pushing ~/vEcoli/.nextflow/ -> ${s3_cache}/"
      ssh -i "$KEY_FILE" "ec2-user@$dns" "set -e; cd ~/vEcoli && \
        aws s3 sync .nextflow/ '${s3_cache}/' --no-progress --only-show-errors && \
        echo 'cache pushed (\$(du -sh .nextflow/ | cut -f1))'"
      ;;
    pull)
      local dns; dns=$(require_running_dns)
      echo "Pulling ${s3_cache}/ -> ~/vEcoli/.nextflow/"
      ssh -i "$KEY_FILE" "ec2-user@$dns" "set -e; cd ~/vEcoli && mkdir -p .nextflow && \
        aws s3 sync '${s3_cache}/' .nextflow/ --no-progress --only-show-errors && \
        echo 'cache pulled (\$(du -sh .nextflow/ | cut -f1))'"
      ;;
    ls)
      aws_cli s3 ls "${s3_cache}/" --recursive --summarize 2>&1 | tail -5
      ;;
    rm)
      read -r -p "Delete ${s3_cache}/? [y/N] " ans
      [[ "$ans" == "y" || "$ans" == "Y" ]] || { echo "aborted"; return; }
      aws_cli s3 rm "${s3_cache}/" --recursive
      ;;
    *)
      cat <<EOF
Usage: $(basename "$0") cache <push|pull|ls|rm>
  push  back up head's ~/.nextflow/ to ${s3_cache}/
  pull  restore ~/.nextflow/ on head from ${s3_cache}/
  ls    list cached snapshot
  rm    delete cached snapshot (confirmed)
EOF
      ;;
  esac
}

# --- 9. ``compare`` namespace -----------------------------------------------
# Default ``compare`` v1/v2 IDs from the per-variant sidecars
# (.vecoli-aws-state/v1.experiment-id and v2.experiment-id) so the most
# recent runs are compared without having to remember the auto-generated
# IDs. Env vars (VECOLI_V1_ID / VECOLI_V2_ID) still override.
#
# When no sidecar exists, fall through to the v1/v2 alias's config
# experiment_id field (via _resolve_alias_coords). This keeps the
# default tied to the alias's own config rather than hardcoded strings
# or the active variant's $EXP_ID (which under ``compare report``
# resolves to ``compare_head`` — wrong for the v2 column).
_compare_default_v1_id() {
  local coords; coords=$(_resolve_alias_coords v1 2>/dev/null || true)
  if [[ -n "$coords" ]]; then
    local exp; IFS=$'\t' read -r exp _ <<<"$coords"
    echo "$exp"
  else
    echo "comparison_10s_16g_v1_aws"  # last-ditch literal
  fi
}
_compare_default_v2_id() {
  local coords; coords=$(_resolve_alias_coords v2 2>/dev/null || true)
  if [[ -n "$coords" ]]; then
    local exp; IFS=$'\t' read -r exp _ <<<"$coords"
    echo "$exp"
  else
    echo "$EXP_ID"  # last-ditch (caller's active variant)
  fi
}

# Read sidecar for an arbitrary alias (mp, ray, ...). Returns empty if
# alias has never been launched (no sidecar).
_compare_default_id_for() {
  local alias_name="$1"
  local f="$STATE_DIR/${alias_name}.experiment-id"
  [[ -f "$f" ]] || { echo ""; return; }
  local v; v=$(<"$f"); echo "${v//$'\n'/}"
}

# Build the ``extra_ids`` env value the report consumes from any
# alias sidecar we find that isn't v1/v2/compare. By convention
# mp/ray sidecars hold full experiment_ids; we emit them as
# ``label=full_id`` pairs, comma-separated. User-supplied
# VECOLI_EXTRA_IDS still wins (set non-empty to override).
_compare_auto_extra_ids() {
  local out=""
  while IFS=$'\t' read -r alias_name _cfg _method _img; do
    [[ -z "$alias_name" ]] && continue
    case "$alias_name" in
      v1|v2|compare) continue ;;  # primary roles + compare alias itself
    esac
    local id; id=$(_compare_default_id_for "$alias_name")
    [[ -z "$id" ]] && continue  # no sidecar yet → skip
    out+="${out:+,}${alias_name}=${id}"
  done < "$_REGISTRY"
  echo "$out"
}

# Validate v1/v2/extra experiment ids before invoking fetch_and_compare
# on the head. Each id must:
#   1. Be non-empty.
#   2. Start with its alias's config experiment_id base (catches the
#      v2-falls-through-to-compare_head bug where the sidecar is
#      missing and the default picks up another variant's id).
#   3. Have at least one object under
#      s3://<bucket>/<prefix>/<exp_id>/ — catches typos and stale
#      sidecars pointing at runs that never produced output.
# Warnings (non-fatal):
#   - id has no _YYYYMMDD-HHMMSS suffix (looks like a config base
#     rather than a stamped run id; probably not what you want).
#   - alias has no registry entry (skip the S3 check).
# Echoes a one-line OK/ERROR per id to stderr. Returns 0 if every id
# clears the fatal checks, 1 otherwise.
_compare_validate_ids() {
  local v1_id="$1" v2_id="$2" extra_ids="$3"
  local fatal=0 warns=0
  echo "Validating experiment ids..." >&2

  local pairs=("v1=$v1_id" "v2=$v2_id")
  if [[ -n "$extra_ids" ]]; then
    local raw
    for raw in $(echo "$extra_ids" | tr ',' ' '); do
      [[ -z "$raw" ]] && continue
      pairs+=("$raw")
    done
  fi

  local sfx_re='_[0-9]{8}-[0-9]{6}$'
  local entry label eid coords bucket prefix base
  for entry in "${pairs[@]}"; do
    label="${entry%%=*}"
    eid="${entry#*=}"
    if [[ -z "$eid" ]]; then
      echo "  ERROR ${label}: experiment_id is empty" >&2
      fatal=$((fatal + 1))
      continue
    fi
    coords=$(_resolve_alias_coords "$label" 2>/dev/null || true)
    if [[ -z "$coords" ]]; then
      echo "  WARN  ${label}='${eid}': not a registered alias; skipping S3 check" >&2
      warns=$((warns + 1))
      continue
    fi
    IFS=$'\t' read -r _ bucket prefix base _ <<<"$coords"
    if [[ "$eid" != "$base"* ]]; then
      echo "  ERROR ${label}='${eid}': doesn't begin with ${label} alias base '${base}'" >&2
      echo "        Fix: pass --${label}-id <real_id>, or 'run launch ${label}' to populate the sidecar." >&2
      fatal=$((fatal + 1))
      continue
    fi
    if ! [[ "$eid" =~ $sfx_re ]]; then
      echo "  WARN  ${label}='${eid}': no _YYYYMMDD-HHMMSS suffix (looks like a config base, not a stamped run)" >&2
      warns=$((warns + 1))
    fi
    local first_key
    first_key=$(aws_cli s3api list-objects-v2 \
      --bucket "$bucket" --prefix "${prefix}/${eid}/" \
      --max-keys 1 --query 'Contents[0].Key' --output text 2>/dev/null || true)
    if [[ -z "$first_key" || "$first_key" == "None" ]]; then
      echo "  ERROR ${label}='${eid}': no S3 objects at s3://${bucket}/${prefix}/${eid}/" >&2
      fatal=$((fatal + 1))
    else
      echo "  OK    ${label}='${eid}'" >&2
    fi
  done

  echo >&2
  if (( fatal > 0 )); then
    echo "compare report: ${fatal} id error(s), ${warns} warning(s) — aborting." >&2
    echo "Pass --force to skip validation." >&2
    return 1
  fi
  if (( warns > 0 )); then
    echo "compare report: ${warns} warning(s); continuing." >&2
  else
    echo "compare report: all ids validated." >&2
  fi
  return 0
}

ns_compare_parity() {
  local seed="${1:-0}" gen="${2:-3}"
  uv run --no-sync python "$SCRIPT_DIR/compare_v1_v2_at_gen.py" \
    --seed "$seed" --gen "$gen"
}
# Show how far each seed's lineage made it (max generation reached) in
# v1 vs the active config's v2. Catches early-halt parity divergences
# that ``compare parity`` misses (parity is per-seed-and-gen, this is
# per-seed-across-gens).
ns_compare_gens() {
  local v1_id="${VECOLI_V1_ID:-$(_compare_default_v1_id)}"
  local v2_id="${VECOLI_V2_ID:-$(_compare_default_v2_id)}"
  local tmpdir; tmpdir=$(mktemp -d)
  trap 'rm -rf "$tmpdir"' RETURN

  _ns_max_gens_for() {
    # S3 history layout:
    #   vecoli-output/<base>/<full_exp_id>/history/experiment_id=<full_exp_id>/
    #     variant=0/lineage_seed=<N>/generation=<M>/agent_id=*/...
    # ``base`` = config's ``experiment_id`` (no timestamp);
    # ``full_exp_id`` = base + ``_YYYYMMDD-HHMMSS`` after run launch.
    # We list once and pull the max gen seen per seed.
    local exp="$1"
    local base; base=$(_exp_id_base "$exp")
    aws_cli s3 ls "s3://$BUCKET/vecoli-output/$base/$exp/history/" \
      --recursive 2>/dev/null \
      | grep -oE 'lineage_seed=[0-9]+/generation=[0-9]+' \
      | awk -F'[=/]' '
          { seed=$2+0; gen=$4+0; if (gen>max[seed]) max[seed]=gen }
          END { for (s in max) print s, max[s] }
        ' \
      | sort -n -k1
  }

  echo "Reading v1 ($v1_id) gens from S3..."
  _ns_max_gens_for "$v1_id" > "$tmpdir/v1.txt"
  echo "Reading v2 ($v2_id) gens from S3..."
  _ns_max_gens_for "$v2_id" > "$tmpdir/v2.txt"

  if [[ ! -s "$tmpdir/v1.txt" && ! -s "$tmpdir/v2.txt" ]]; then
    echo "No history found in S3 for either run — wrong bucket/prefix?" >&2
    return 1
  fi

  echo
  printf "%-6s  %-8s  %-8s  %s\n" "seed" "v1_gen" "v2_gen" "delta"
  printf "%-6s  %-8s  %-8s  %s\n" "----" "------" "------" "-----"
  # Join on seed; missing rows in either side become "-".
  join -a 1 -a 2 -e "-" -o 0,1.2,2.2 "$tmpdir/v1.txt" "$tmpdir/v2.txt" \
    | awk '{
        v1=$2; v2=$3;
        if (v1=="-" || v2=="-") { delta="?" }
        else { delta=v2-v1; if (delta>0) delta="+"delta }
        printf "%-6s  %-8s  %-8s  %s\n", $1, v1, v2, delta
      }'

  # Summary: how many seeds reached the same gen in both runs
  local match total
  match=$(join "$tmpdir/v1.txt" "$tmpdir/v2.txt" | awk '$2==$3' | wc -l)
  total=$(wc -l < "$tmpdir/v1.txt")
  echo
  echo "Match: $match / $total seeds reached the same generation in v1 and v2."
  if (( match < total )); then
    echo "Seeds where v2 fell short of v1 are parity-divergence candidates."
  fi
}

# Resolve <alias> → echo "<exp_id>\t<bucket>\t<prefix>\t<base>\t<config_rel>".
# Doesn't touch globals (so it's safe to call for two aliases in one
# function). Sidecar value used as exp_id when present, else config base.
_resolve_alias_coords() {
  local key="$1"
  local cfg; cfg=$(_alias_to_config "$key")
  if [[ -z "$cfg" ]]; then
    echo "Unknown alias: $key" >&2; return 1
  fi
  local cfg_abs="$REPO_ROOT/$cfg"
  if [[ ! -f "$cfg_abs" ]]; then
    echo "Missing config: $cfg_abs" >&2; return 1
  fi
  local base out_uri bucket prefix exp_id
  base=$(python3 -c "import json; print(json.load(open('$cfg_abs'))['experiment_id'])")
  out_uri=$(python3 -c "import json; print(json.load(open('$cfg_abs'))['emitter_arg']['out_uri'])")
  bucket="${out_uri#s3://}"; bucket="${bucket%%/*}"
  prefix="${out_uri#s3://$bucket/}"
  exp_id="$base"
  local sidecar="$STATE_DIR/${key}.experiment-id"
  if [[ -f "$sidecar" ]]; then
    exp_id=$(<"$sidecar"); exp_id="${exp_id//$'\n'/}"
  fi
  printf '%s\t%s\t%s\t%s\t%s\n' "$exp_id" "$bucket" "$prefix" "$base" "$cfg"
}

# Per-gen wall comparison between two aliases via S3 file-mtime proxy.
# The LAST file written under .../lineage_seed=N/generation=M/ approximates
# gen finish; gen wall = max(mtime_M) - max(mtime_{M-1}) per seed (with
# launch_time from the EXP_ID timestamp suffix as the gen-1 baseline).
# See memory:perf_mp_vs_nf_2026_05_10 for the methodology.
ns_compare_time() {
  local a="${1:-}" b="${2:-}"
  if [[ -z "$a" || "$a" == --* || -z "$b" || "$b" == --* ]]; then
    echo "usage: $(basename "$0") compare time <alias_a> <alias_b> [--gens 1,2,...] [--until N]" >&2
    return 1
  fi
  shift 2
  local gens_csv="" until_n=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --gens|--generations) gens_csv="$2"; shift 2 ;;
      --until)              until_n="$2";  shift 2 ;;
      *) echo "compare time: unknown arg '$1'" >&2; return 1 ;;
    esac
  done
  if [[ -n "$until_n" ]]; then
    if [[ -z "$gens_csv" ]]; then
      gens_csv=$(seq -s, 1 "$until_n")
    else
      echo "compare time: --gens and --until are mutually exclusive" >&2; return 1
    fi
  fi

  local a_coords b_coords
  a_coords=$(_resolve_alias_coords "$a") || return 1
  b_coords=$(_resolve_alias_coords "$b") || return 1
  local a_exp a_bucket a_prefix
  IFS=$'\t' read -r a_exp a_bucket a_prefix _ _ <<<"$a_coords"
  local b_exp b_bucket b_prefix
  IFS=$'\t' read -r b_exp b_bucket b_prefix _ _ <<<"$b_coords"

  local tmpdir; tmpdir=$(mktemp -d)
  trap 'rm -rf "$tmpdir"' RETURN

  # out_uri = s3://<bucket>/<prefix-incl-base>; after auto-rotation the
  # actual files live under <prefix>/<full_exp_id>/history/...
  echo "Listing S3 history for $a (exp=$a_exp)..."
  aws_cli s3 ls --recursive "s3://$a_bucket/$a_prefix/$a_exp/history/" \
    > "$tmpdir/a.txt" 2>/dev/null || true
  echo "Listing S3 history for $b (exp=$b_exp)..."
  aws_cli s3 ls --recursive "s3://$b_bucket/$b_prefix/$b_exp/history/" \
    > "$tmpdir/b.txt" 2>/dev/null || true

  if [[ ! -s "$tmpdir/a.txt" && ! -s "$tmpdir/b.txt" ]]; then
    echo "No history under either S3 prefix — wrong bucket/prefix?" >&2
    return 1
  fi

  python3 - "$a" "$b" "$tmpdir/a.txt" "$tmpdir/b.txt" "$a_exp" "$b_exp" "$gens_csv" <<'PY'
import sys, re, datetime, statistics

a_label, b_label, a_path, b_path, a_exp, b_exp, gens_csv = sys.argv[1:8]
gen_filter = set()
if gens_csv:
    gen_filter = set(int(x) for x in gens_csv.split(",") if x.strip())

PAT = re.compile(r"lineage_seed=(\d+)/generation=(\d+)")

def gen_finish(path):
    """Returns {(seed, gen): max_epoch} from `aws s3 ls --recursive` output."""
    finish = {}
    try:
        f = open(path)
    except FileNotFoundError:
        return finish
    with f:
        for line in f:
            parts = line.split(None, 3)
            if len(parts) < 4:
                continue
            date_s, time_s, _size, key = parts
            m = PAT.search(key)
            if not m:
                continue
            seed, gen = int(m.group(1)), int(m.group(2))
            try:
                # aws s3 ls prints in LOCAL time; naive .timestamp() converts
                # local→epoch correctly.
                e = datetime.datetime.strptime(
                    f"{date_s} {time_s}", "%Y-%m-%d %H:%M:%S"
                ).timestamp()
            except ValueError:
                continue
            cur = finish.get((seed, gen), 0)
            if e > cur:
                finish[(seed, gen)] = e
    return finish

def parse_launch(exp_id):
    """EXP_ID's trailing _YYYYMMDD-HHMMSS is the UTC launch stamp."""
    m = re.search(r"_(\d{8})-(\d{6})$", exp_id)
    if not m:
        return None
    d, t = m.group(1), m.group(2)
    iso = f"{d[:4]}-{d[4:6]}-{d[6:]}T{t[:2]}:{t[2:4]}:{t[4:]}+00:00"
    return datetime.datetime.fromisoformat(iso).timestamp()

def per_gen_walls(finish, launch_epoch):
    """Returns {seed: {gen: wall_seconds}}. Uses launch as gen-1 baseline."""
    by_seed = {}
    for (s, g), e in finish.items():
        by_seed.setdefault(s, {})[g] = e
    walls = {}
    for s, gens in by_seed.items():
        prev = launch_epoch if launch_epoch else min(gens.values())
        sw = {}
        for g in sorted(gens):
            sw[g] = max(0.0, gens[g] - prev)
            prev = gens[g]
        walls[s] = sw
    return walls

def filter_gens(walls, keep):
    if not keep:
        return walls
    return {s: {g: v for g, v in gw.items() if g in keep}
            for s, gw in walls.items()}

a_walls = filter_gens(per_gen_walls(gen_finish(a_path), parse_launch(a_exp)),
                      gen_filter)
b_walls = filter_gens(per_gen_walls(gen_finish(b_path), parse_launch(b_exp)),
                      gen_filter)

all_gens = set()
for sw in (a_walls, b_walls):
    for s in sw:
        all_gens.update(sw[s].keys())
sorted_gens = sorted(all_gens)
if not sorted_gens:
    print("No matching generations found (filter too narrow?).")
    sys.exit(1)

def fmt(s):
    return "    -" if s is None else f"{s/60:5.1f}"

def print_table(label, exp_id, walls):
    seeds = sorted(walls.keys())
    print(f"\n{label}  (exp={exp_id})")
    if not seeds:
        print("  (no data)")
        return
    print("  seed " + "  ".join(f"  g{g:<3}" for g in sorted_gens))
    for s in seeds:
        row = "  ".join(fmt(walls[s].get(g)) for g in sorted_gens)
        print(f"  {s:>4}  {row}")

print_table(a_label, a_exp, a_walls)
print_table(b_label, b_exp, b_walls)

print("\nPer-gen median wall (min) and ratio:")
print(f"  {'gen':>4}  {a_label:>10}  {b_label:>10}  {'ratio':>7}")
for g in sorted_gens:
    a_vals = [w[g] for w in a_walls.values() if g in w]
    b_vals = [w[g] for w in b_walls.values() if g in w]
    a_med = statistics.median(a_vals) if a_vals else None
    b_med = statistics.median(b_vals) if b_vals else None
    if a_med is not None and b_med is not None and b_med > 0:
        ratio = a_med / b_med
        print(f"  {g:>4}  {a_med/60:>10.1f}  {b_med/60:>10.1f}  {ratio:>6.2f}x")
    else:
        print(f"  {g:>4}  {fmt(a_med):>10}  {fmt(b_med):>10}  {'—':>7}")
PY
}

ns_compare_full_parity() {
  # Run runscripts/aws/compute_full_parity.py on the head node —
  # diffs EVERY parquet column (not just bulk) for v1 vs v2 across
  # the requested seeds × gens, writes summary + per-column TSVs to
  # ``out/full_parity__<v1>__<v2>.tsv{,.cols}`` and pulls them back.
  #
  # Args:
  #   --seeds  comma-list (default: env or 0..9)
  #   --gens   comma-list (default: env or 1..16)
  local seeds="${VECOLI_REPORT_SEEDS:-0,1,2,3,4,5,6,7,8,9}"
  local gens="${VECOLI_REPORT_GENS:-1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16}"
  local v1_id="${VECOLI_V1_ID:-$(_compare_default_v1_id)}"
  local v2_id="${VECOLI_V2_ID:-$(_compare_default_v2_id)}"
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --seeds) seeds="$2"; shift 2 ;;
      --gens)  gens="$2";  shift 2 ;;
      --v1-id) v1_id="$2"; shift 2 ;;
      --v2-id) v2_id="$2"; shift 2 ;;
      *) echo "usage: compare full-parity [--seeds 0,1] [--gens 1,2] [--v1-id ID] [--v2-id ID]" >&2; return 1 ;;
    esac
  done
  local dns; dns=$(require_running_dns)
  local out_summary="out/full_parity__${v1_id}__${v2_id}.tsv"
  echo "Pushing tool to head..."
  ssh -i "$KEY_FILE" "ec2-user@$dns" 'mkdir -p ~/vEcoli/runscripts/aws ~/vEcoli/out'
  scp -i "$KEY_FILE" \
      "$SCRIPT_DIR/compute_full_parity.py" \
      "ec2-user@$dns:~/vEcoli/runscripts/aws/"
  echo "Running full-column parity (v1=${v1_id}, v2=${v2_id}, "\
       "seeds=${seeds}, gens=${gens}) on head..."
  echo "  This is ALL columns × all (seed,gen) cells — ~1-2 min/cell"
  echo "  on in-region S3 = roughly $(( $(echo "$seeds" | tr ',' '\n' | wc -l) \
       * $(echo "$gens" | tr ',' '\n' | wc -l) * 90 / 60 )) min total."
  ssh -i "$KEY_FILE" "ec2-user@$dns" "set -e; cd ~/vEcoli && \
    uv run --no-sync python runscripts/aws/compute_full_parity.py \
      --v1-id '$v1_id' --v2-id '$v2_id' \
      --seeds '$seeds' --gens '$gens' \
      --bucket '$BUCKET' --prefix '${PREFIX%%/*}' \
      --output '$out_summary'"
  echo "Pulling results to local..."
  scp -i "$KEY_FILE" \
      "ec2-user@$dns:~/vEcoli/${out_summary}" \
      "ec2-user@$dns:~/vEcoli/${out_summary}.cols" \
      "$REPO_ROOT/out/" 2>&1 | tail -5 || true
  echo "Summary: $REPO_ROOT/${out_summary}"
  echo "Per-col: $REPO_ROOT/${out_summary}.cols"
}

# Rsync the local repo to the compare head, then run
# ``bootstrap_head_compare.sh`` over ssh to install uv + create
# .venv with numpy/polars. Wrapper around ``_run_bootstrap_on_head``
# that adds the rsync step — otherwise the head ends up with just the
# bootstrap script and no pyproject.toml, so ``uv sync`` errors out.
# ``run launch`` rsyncs before bootstrap on its own, so this wrapper
# is needed only for the standalone ``compare bootstrap`` entry point.
ns_compare_bootstrap() {
  local dns; dns=$(require_running_dns)
  echo "Rsyncing local repo → ec2-user@${dns}:~/vEcoli/ (skipping .git/.venv/out/)..."
  _rsync_repo_to_head "$dns"
  _run_bootstrap_on_head ""
}

# Generate the analyses/ plots that Ray/MP runners skipped, then push
# them to S3 under the alias's experiment_id. Resolves the alias to
# its config + stamped exp_id + sim_data URI + bucket/prefix, then runs
# runscripts/aws/run_post_hoc_analysis.sh on the compare head (which
# already has uv + .venv with the analysis stack).
#
# Usage: $(basename "$0") compare analyze <alias> [--analysis_name NAMES]
#                                                  [--types TYPES] [--cpus N]
ns_compare_analyze() {
  local target_alias="" analysis_names="" analysis_types="" cpus=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --analysis_name|--analysis-name) analysis_names="$2"; shift 2 ;;
      --types|-t)                      analysis_types="$2"; shift 2 ;;
      --cpus|-n)                       cpus="$2"; shift 2 ;;
      -h|--help)
        cat >&2 <<USAGE
usage: $(basename "$0") compare analyze <alias> [--analysis_name "n1 n2 ..."]
                                                [--types "single multiseed"]
                                                [--cpus N]

Drive runscripts/analysis.py for <alias> on the compare head and upload
plots to S3 under the alias's experiment_id, so ``compare report``
picks them up. Useful for Ray/MP runs that produced parquet but not
analyses (Nextflow v1/v2 runs already publish their own analyses/).

  --analysis_name  restrict to specific analysis script name(s)
                   (e.g. "mass_fraction_summary ecocyc_table")
  --types          restrict to specific analysis types
                   (any of: multiexperiment multivariant multiseed
                   multigeneration multidaughter single parca)
  --cpus           DuckDB threadpool size (default: 4)
USAGE
        return 0 ;;
      -*) echo "compare analyze: unknown flag '$1'" >&2; return 1 ;;
      *)
        if [[ -z "$target_alias" ]]; then target_alias="$1"; shift
        else echo "compare analyze: unexpected positional '$1'" >&2; return 1
        fi ;;
    esac
  done
  if [[ -z "$target_alias" ]]; then
    echo "compare analyze: missing required <alias> (e.g. ray, mp)" >&2
    return 1
  fi

  local coords; coords=$(_resolve_alias_coords "$target_alias") || return 1
  local target_exp target_bucket target_prefix target_base target_cfg
  IFS=$'\t' read -r target_exp target_bucket target_prefix target_base target_cfg <<<"$coords"

  if [[ ! -f "$REPO_ROOT/$target_cfg" ]]; then
    echo "compare analyze: config not found at $REPO_ROOT/$target_cfg" >&2
    return 1
  fi
  # Pull sim_data_path out of the alias config. Composite-lineage configs
  # (ray, mp) carry the s3 URI directly; nextflow configs leave it null
  # (parca generates it). For an alias whose config has sim_data_path:null,
  # there's no post-hoc path here — that's a Nextflow run that already
  # produced analyses, so this subcommand shouldn't be called on it.
  local sim_data_uri
  sim_data_uri=$(python3 -c "
import json, sys
with open('$REPO_ROOT/$target_cfg') as f:
    cfg = json.load(f)
v = cfg.get('sim_data_path')
print(v if v else '', end='')
")
  if [[ -z "$sim_data_uri" || "$sim_data_uri" == "None" ]]; then
    echo "compare analyze: $target_alias config has no sim_data_path." >&2
    echo "  This subcommand is for composite-lineage runs (mp/ray) that reuse a" >&2
    echo "  pre-existing parca simData.cPickle. Nextflow runs (v1/v2) already publish" >&2
    echo "  their own analyses/." >&2
    return 1
  fi

  echo "Resolved:"
  echo "  alias       = $target_alias"
  echo "  exp_id      = $target_exp"
  echo "  config      = $target_cfg"
  echo "  sim_data    = $sim_data_uri"
  echo "  s3 target   = s3://$target_bucket/$target_prefix/$target_exp/analyses/"

  local dns; dns=$(require_running_dns)
  echo "Pushing analysis scripts to compare head..."
  ssh -i "$KEY_FILE" "ec2-user@$dns" 'mkdir -p ~/vEcoli/runscripts/aws'
  scp -i "$KEY_FILE" \
      "$SCRIPT_DIR/run_post_hoc_analysis.sh" \
      "ec2-user@$dns:~/vEcoli/runscripts/aws/"
  scp -i "$KEY_FILE" \
      "$REPO_ROOT/runscripts/analysis.py" \
      "ec2-user@$dns:~/vEcoli/runscripts/"

  local env_pairs="CONFIG_RELPATH='$target_cfg' EXP_ID='$target_exp'"
  env_pairs+=" SIM_DATA_URI='$sim_data_uri'"
  env_pairs+=" BUCKET='$target_bucket' PREFIX='$target_prefix'"
  [[ -n "$analysis_names" ]] && env_pairs+=" ANALYSIS_NAME='$analysis_names'"
  [[ -n "$analysis_types" ]] && env_pairs+=" ANALYSIS_TYPES='$analysis_types'"
  [[ -n "$cpus" ]] && env_pairs+=" CPUS='$cpus'"

  echo "Running run_post_hoc_analysis.sh on head..."
  ssh -i "$KEY_FILE" "ec2-user@$dns" \
    "set -e; cd ~/vEcoli && $env_pairs bash runscripts/aws/run_post_hoc_analysis.sh"
  echo
  echo "Done. Re-run 'compare report' to pick up the new plots for $target_alias."
}

# Walk the alias registry, list each alias's S3 prefix, pick the
# lexicographically latest ``<base>_YYYYMMDD-HHMMSS`` directory, and
# write that id back to .vecoli-aws-state/<alias>.experiment-id.
# After running, ``compare report`` auto-discovers a full v1/v2/mp/ray
# comparison without the user having to remember stamped ids.
ns_compare_discover() {
  local dry_run=0 only="" force=0
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --dry-run|-n) dry_run=1; shift ;;
      --alias)      only="$2"; shift 2 ;;
      --force)      force=1; shift ;;
      -h|--help)
        cat >&2 <<USAGE
usage: $(basename "$0") compare discover [--alias <name>] [--dry-run] [--force]

For each non-``compare`` alias in the registry, list its S3 prefix and
write the lexicographically latest stamped run id (<base>_YYYYMMDD-HHMMSS)
to .vecoli-aws-state/<alias>.experiment-id. Subsequent ``compare report``
calls then pick up the freshest run for each alias automatically.

  --alias <name>   only discover the named alias
  --dry-run        show what would be written, don't touch sidecars
  --force          overwrite an existing sidecar even if it lexically
                   sorts higher than the latest stamped S3 dir
USAGE
        return 0 ;;
      *) echo "compare discover: unknown arg '$1'" >&2; return 1 ;;
    esac
  done

  echo "Scanning S3 for latest experiment ids per alias..." >&2

  local found=0 wrote=0 skipped=0
  while IFS=$'\t' read -r alias_name _cfg _method _img; do
    [[ -z "$alias_name" ]] && continue
    case "$alias_name" in
      compare) continue ;;  # not a workflow alias
    esac
    if [[ -n "$only" && "$only" != "$alias_name" ]]; then
      continue
    fi

    local coords; coords=$(_resolve_alias_coords "$alias_name" 2>/dev/null || true)
    if [[ -z "$coords" ]]; then
      echo "  skip ${alias_name}: alias not resolvable" >&2
      continue
    fi
    local bucket prefix
    IFS=$'\t' read -r _ bucket prefix _ _ <<<"$coords"

    # `aws s3 ls` on a prefix returns directory entries as ``PRE <name>/``.
    # Pick the lexicographically max stamped entry — timestamps in the
    # _YYYYMMDD-HHMMSS suffix sort correctly as strings.
    local listing
    listing=$(aws_cli s3 ls "s3://${bucket}/${prefix}/" 2>/dev/null || true)
    if [[ -z "$listing" ]]; then
      echo "  skip ${alias_name}: nothing under s3://${bucket}/${prefix}/" >&2
      continue
    fi
    local latest
    latest=$(echo "$listing" \
      | awk '/^[[:space:]]*PRE / { sub(/\/$/, "", $NF); print $NF }' \
      | grep -E '_[0-9]{8}-[0-9]{6}$' \
      | sort \
      | tail -1)
    if [[ -z "$latest" ]]; then
      echo "  skip ${alias_name}: no stamped runs under s3://${bucket}/${prefix}/" >&2
      continue
    fi

    found=$((found + 1))
    local sidecar="$STATE_DIR/${alias_name}.experiment-id"
    local cur_sidecar=""
    if [[ -f "$sidecar" ]]; then
      cur_sidecar=$(<"$sidecar"); cur_sidecar="${cur_sidecar//$'\n'/}"
    fi

    if [[ -n "$cur_sidecar" && "$cur_sidecar" == "$latest" ]]; then
      echo "  ok   ${alias_name}: sidecar already at ${latest}" >&2
      continue
    fi
    if (( force == 0 )) && [[ -n "$cur_sidecar" && "$cur_sidecar" > "$latest" ]]; then
      echo "  skip ${alias_name}: sidecar='${cur_sidecar}' lexically > S3 latest='${latest}' (use --force)" >&2
      skipped=$((skipped + 1))
      continue
    fi

    if (( dry_run == 1 )); then
      echo "  DRY  ${alias_name}: would write ${latest} → ${sidecar}" >&2
    else
      mkdir -p "$STATE_DIR"
      printf '%s\n' "$latest" > "$sidecar"
      echo "  wrote ${alias_name}: ${latest} → ${sidecar}" >&2
      wrote=$((wrote + 1))
    fi
  done < "$_REGISTRY"

  echo >&2
  if (( dry_run == 1 )); then
    echo "compare discover (dry-run): ${found} alias(es) had a stamped run in S3." >&2
  else
    echo "compare discover: wrote ${wrote} sidecar(s), skipped ${skipped}." >&2
  fi
}

ns_compare_report() {
  # CLI flags + env-var fallbacks (CLI wins). Defaults: v1/v2/extras
  # come from sidecars, seeds = 0..9, gens = 1..16.
  local v1_id="${VECOLI_V1_ID:-}"
  local v2_id="${VECOLI_V2_ID:-}"
  local seeds="${VECOLI_REPORT_SEEDS:-}"
  local gens="${VECOLI_REPORT_GENS:-}"
  local extra_ids="${VECOLI_EXTRA_IDS:-}"
  local engine_cost="${VECOLI_ENGINE_COST:-}"
  local include_history="${VECOLI_INCLUDE_HISTORY:-1}"
  local out_path="${VECOLI_REPORT_OUT:-}"
  local no_fetch=0
  local force=0
  local fetch_analyses=1
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --gens)        gens="$2"; shift 2 ;;
      --seeds)       seeds="$2"; shift 2 ;;
      --v1-id)       v1_id="$2"; shift 2 ;;
      --v2-id)       v2_id="$2"; shift 2 ;;
      --extra-ids)   extra_ids="$2"; shift 2 ;;
      --engine-cost) engine_cost="$2"; shift 2 ;;
      --out)         out_path="$2"; shift 2 ;;
      --no-history)  include_history=0; shift ;;
      --no-analyses) fetch_analyses=0; shift ;;
      --no-fetch|--cached) no_fetch=1; shift ;;
      --force|--no-validate) force=1; shift ;;
      -h|--help)
        cat >&2 <<USAGE
usage: $(basename "$0") compare report          [<flags>]      # both: fetch + parity
       $(basename "$0") compare report analysis [<flags>]      # fast: plots only
       $(basename "$0") compare report diff     [<flags>]      # slow: parity only

Flags:
  --gens 1,2,...            seeds for per-cell plots (default: 1-16)
  --seeds 0,1,...           ids for per-cell plots (default: 0-9)
  --v1-id ID, --v2-id ID    override sidecar-resolved experiment ids
  --extra-ids label=ID,...  override sidecar-discovered mp/ray etc.
  --out path                report path (default doc/v1_v2_report.md);
                            assets land under _static/<stem>_assets/
  --no-analyses             skip the per-experiment analyses/ fetch
                            (implied by ``diff`` subcmd)
  --no-history              skip the bulk-parity-matrix computation
                            (implied by ``analysis`` subcmd; ~30-60 min saved)
  --no-fetch (--cached)     skip ALL S3 sync — render report against
                            cached out/<exp>/ on the head only
  --force (--no-validate)   bypass the S3 id-existence pre-check

Defaults pull v1/v2/mp/ray IDs from .vecoli-aws-state/<alias>.experiment-id
sidecars. Sub-subcommands ``analysis`` and ``diff`` compose: run
``analysis`` first to see plots fast, then ``diff`` later to fill in
parity — the markdown is re-rendered each invocation from whatever's
cached on the head, so sections fall back to _(missing)_ until populated.
USAGE
        return 0 ;;
      *) echo "compare report: unknown arg '$1'" >&2; return 1 ;;
    esac
  done
  [[ -z "$out_path" ]] && out_path="doc/v1_v2_report.md"
  # Apply defaults for unset values
  [[ -z "$v1_id"     ]] && v1_id=$(_compare_default_v1_id)
  [[ -z "$v2_id"     ]] && v2_id=$(_compare_default_v2_id)
  [[ -z "$seeds"     ]] && seeds="0,1,2,3,4,5,6,7,8,9"
  [[ -z "$gens"      ]] && gens="1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16"
  # Auto-discover mp/ray sidecars when --extra-ids / VECOLI_EXTRA_IDS
  # not explicitly set. User passing ``--extra-ids ""`` is treated as
  # explicit "no extras" (won't trigger auto-discovery).
  if [[ -z "${VECOLI_EXTRA_IDS+x}" && -z "$extra_ids" ]]; then
    extra_ids=$(_compare_auto_extra_ids)
    [[ -n "$extra_ids" ]] && echo "Auto-discovered extras: $extra_ids"
  fi
  if (( force == 0 )); then
    _compare_validate_ids "$v1_id" "$v2_id" "$extra_ids" || return 1
  fi
  local dns; dns=$(require_running_dns)
  echo "Pushing latest scripts to head..."
  ssh -i "$KEY_FILE" "ec2-user@$dns" 'mkdir -p ~/vEcoli/runscripts/aws'
  scp -i "$KEY_FILE" \
      "$SCRIPT_DIR/fetch_and_compare.sh" \
      "$SCRIPT_DIR/compare_v1_v2_at_gen.py" \
      "$SCRIPT_DIR/compute_parity_matrix.py" \
      "ec2-user@$dns:~/vEcoli/runscripts/aws/"
  scp -i "$KEY_FILE" \
      "$REPO_ROOT/runscripts/v1_v2_report.py" \
      "$REPO_ROOT/runscripts/cost.py" \
      "$REPO_ROOT/runscripts/synthetic_trace.py" \
      "ec2-user@$dns:~/vEcoli/runscripts/"
  echo "Running fetch + report on head ($v1_id vs $v2_id${extra_ids:+ + extras: $extra_ids}) -> $out_path"
  ssh -i "$KEY_FILE" "ec2-user@$dns" "set -e; cd ~/vEcoli && \
    V1_ID='$v1_id' V2_ID='$v2_id' SEEDS='$seeds' GENS='$gens' \
    EXTRA_IDS='$extra_ids' ENGINE_COST='$engine_cost' \
    INCLUDE_HISTORY='$include_history' \
    FETCH_ANALYSES='$fetch_analyses' \
    REPORT_OUT='$out_path' \
    NO_FETCH='$no_fetch' \
    BUCKET='$BUCKET' PREFIX='${PREFIX%%/*}' \
    bash runscripts/aws/fetch_and_compare.sh"
  # Pull back the named report + its asset dir (named ``<stem>_assets``)
  local out_stem; out_stem="$(basename "$out_path" .md)"
  local out_dir; out_dir="$(dirname "$out_path")"
  echo "Pulling rendered report to local $out_dir/..."
  mkdir -p "$REPO_ROOT/$out_dir" "$REPO_ROOT/$out_dir/_static"
  scp -i "$KEY_FILE" "ec2-user@$dns:~/vEcoli/$out_path" \
      "$REPO_ROOT/$out_dir/" || true
  rsync -a -e "ssh -i $KEY_FILE" \
    "ec2-user@$dns:~/vEcoli/$out_dir/_static/${out_stem}_assets/" \
    "$REPO_ROOT/$out_dir/_static/${out_stem}_assets/" 2>/dev/null \
    || scp -ri "$KEY_FILE" \
        "ec2-user@$dns:~/vEcoli/$out_dir/_static/${out_stem}_assets" \
        "$REPO_ROOT/$out_dir/_static/"
  echo "Report:  $REPO_ROOT/$out_path"
}
ns_compare_export() {
  # Args: [html|pdf] [path/to/report.md]
  # If only fmt provided, defaults to doc/v1_v2_report.md.
  local fmt="${1:-html}"
  local src="${2:-$REPO_ROOT/doc/v1_v2_report.md}"
  # Allow relative paths (resolve against REPO_ROOT for convenience).
  [[ "$src" = /* ]] || src="$REPO_ROOT/$src"
  [[ -f "$src" ]] || { echo "no $src — run 'compare report --out <path>' first" >&2; return 1; }
  command -v pandoc >/dev/null \
    || { echo "pandoc not installed: sudo apt install pandoc" >&2; return 1; }
  local src_dir; src_dir="$(dirname "$src")"
  local src_stem; src_stem="$(basename "$src" .md)"
  local assets_dir="$src_dir/_static/${src_stem}_assets"
  case "$fmt" in
    html)
      local out="$src_dir/${src_stem}.html"
      local embed_flag="--embed-resources"
      pandoc --help 2>&1 | grep -q -- '--embed-resources' || embed_flag="--self-contained"
      pandoc -s "$embed_flag" \
        --metadata title="vEcoli ${src_stem}" \
        --resource-path="$src_dir:$src_dir/_static:$assets_dir" \
        -o "$out" "$src"
      echo "Wrote $out ($(du -h "$out" | cut -f1))"
      ;;
    pdf)
      command -v weasyprint >/dev/null \
        || { echo "weasyprint not installed: uv pip install weasyprint" >&2; return 1; }
      local out="$src_dir/${src_stem}.pdf"
      pandoc --pdf-engine=weasyprint \
        --resource-path="$src_dir:$src_dir/_static:$assets_dir" \
        -o "$out" "$src"
      echo "Wrote $out ($(du -h "$out" | cut -f1))"
      ;;
    *) echo "usage: $(basename "$0") compare export [html|pdf] [path/to/report.md]" >&2; return 1 ;;
  esac
}


# --- 10. Help -------------------------------------------------------------
# Top-level help — namespaces, alias model, env vars. Per-namespace help
# (``<ns> help``) shows the subcommands for just that namespace.
_help_top() {
  cat <<EOF
Usage: $(basename "$0") <namespace> <subcmd> [<alias>] [args]
       $(basename "$0") <namespace> help   # subcommand details
       $(basename "$0") help               # this screen

Namespaces:
  experiment   alias lifecycle (register, list, end, remove)
  head         EC2 head-node lifecycle (setup, ssh, sync, terminate)
  image        Docker image lifecycle (build, push, pull, list)
  run          workflow execution (launch, status, tail, log, cancel)
  cache        Nextflow .nextflow/ S3 cache (push, pull, ls, rm)
  compare      v1↔v2 output analysis (parity, gens, report, export)

Aliases bind <name> → (config, method, image_tag) in
.vecoli-aws-state/aliases.tsv. Built-in seeds (auto-written on first use):

  v1 v2     comparison_*_aws.json          batch           Nextflow on Batch
  mp        comparison_*_mp_aws.json       multiprocessing single-node
  ray       comparison_*_ray_aws.json      ray             EC2 cluster via SSM
  compare   compare_head.json              comparison      v1↔v2 analysis head

Each alias is independent: own EC2 (Name tag vecoli-<alias>-head), tmux
session (vecoli-<alias>), and experiment_id sidecar
(.vecoli-aws-state/<alias>.experiment-id). ``run launch <alias>`` rotates
the sidecar id; downstream subcmds read it.

Quick start (new alias myrun, batch method):
  $(basename "$0") experiment new myrun configs/foo.json batch vecoli:my-arm64
  $(basename "$0") head setup     myrun
  $(basename "$0") image build    myrun
  $(basename "$0") image push     myrun
  $(basename "$0") run launch     myrun
  $(basename "$0") run status     myrun
  $(basename "$0") experiment end myrun --terminate-head --rm

Env overrides (rarely needed):
  VECOLI_AWS_PROFILE   AWS CLI profile (default: $PROFILE)
  VECOLI_AWS_REGION    AWS region      (default: $REGION)
  VECOLI_AWS_KEY       SSH key         (default: $KEY_FILE)
  VECOLI_AWS_CONFIG    config path     (when no <alias> supplied)
  VECOLI_AWS_HEAD_NAME EC2 Name tag    (override alias-derived default)
  VECOLI_AWS_TMUX      tmux session    (override alias-derived default)
  HEAD_INSTANCE_TYPE   instance size   (override alias-derived default)
  SIM_DATA_S3_URI      S3 URI of pre-built simData.cPickle
  IMAGE_URI            ECR URI for Ray cluster (ray alias only)

Add a new method:
  1. New bootstrap script: runscripts/aws/bootstrap_head_<method>.sh
  2. Add a case branch in _use_variant mapping the method to its bootstrap
     and default instance type.
  3. Register aliases that use it via ``experiment new <alias> <cfg> <method>``.
EOF
}

_help_experiment() {
  cat <<EOF
Usage: $(basename "$0") experiment <subcmd> [args]

  new <alias> <config> [<method>] [<image_tag>] [-f]
                       register alias→config in .vecoli-aws-state/aliases.tsv.
                       method:    batch | multiprocessing | ray | ray_colony | comparison
                                  (optional; settable later via ``head setup``)
                       image_tag: e.g. vecoli:my-arm64 (optional; settable later)
                       -f:        overwrite an existing alias.
  list                 show all registered aliases + methods + images +
                       active experiment_id (sidecar) + config path.
  end <alias> [--terminate-head] [--rm]
                       soft-stop: cancel running work + clear sidecar.
                       --terminate-head: also terminate the EC2 head.
                       --rm: also unregister the alias.
  rm <alias> [-f]      hard remove (refuses if alias still has a running head;
                       -f overrides).
  help                 this screen
EOF
}

_help_head() {
  cat <<EOF
Usage: $(basename "$0") head <subcmd> <alias> [args]

  setup <alias> [<method>]
                       provision an EC2 head for the alias. method must be
                       batch | multiprocessing | ray | ray_colony | comparison; required
                       on first setup of a new alias, optional after (registry
                       remembers). Picks bootstrap script + default instance
                       type. Idempotent: starts a stopped head, reuses a
                       running one.
  setup-ray-iam <alias>
                       one-time IAM grant for ray cluster mode (run from a
                       box with admin perms; idempotent).
  setup-s3-endpoint    create a Gateway VPC endpoint for S3 in the Ray
                       cluster's VPC so workers bypass the NAT gateway
                       on s3 writes. Free, one-time, idempotent.
                       VECOLI_RAY_SUBNET env to override default subnet.
  list <alias>         show every non-terminated EC2 with this alias's
                       HEAD_NAME tag (surfaces dupes).
  list-all             show every vEcoli-owned EC2 (Name=vecoli-*),
                       grouped by category: driver heads / Ray cluster
                       (head + workers) / other. Read-only — use before
                       ``terminate-all`` to preview scope. Filter is
                       safe against spatio-flux (sf-*) and other tenants.
  dedupe <alias>       keep oldest running head, terminate the rest.
  sync <alias> [-c|--container]
                       rsync local repo → ~/vEcoli on head (skips
                       .git/.venv/out/__pycache__). -c also docker-cp's
                       host code into the running vecoli_ray container.
  rebuild <alias>      terminate + setup.
  reboot <alias>       warm reboot (keeps EBS, keeps public IP).
  stop   <alias>       halt (preserves EBS root volume; public IP changes).
  start  <alias>       resume a stopped head.
  terminate <alias>    permanent delete (EBS gone). Confirmed prompt.
  refresh-sg <alias>   re-add current public IP to the SSH security group.
  dns    <alias>       print the running head's public DNS.
  ssh    <alias> [cmd] ssh into the head (executes cmd if provided).
  attach <alias>       ssh + tmux attach to the alias's session.

  terminate-all [--cancel-jobs]
                       NUKE every EC2 tagged vecoli-* (no alias arg, confirmed).
                       Covers driver heads AND orphaned Ray cluster instances
                       (workers + cluster head) since both are named vecoli-*.
                       Run ``list-all`` first to preview the scope.
                       --cancel-jobs ALSO terminates active Batch jobs across
                       every queue in the alias registry (jobs run on
                       Batch-managed compute and outlive their heads).
                       Does NOT touch: Batch queue, ECR images, S3 outputs,
                       sidecars, alias registry, non-vecoli instances.
  help                 this screen
EOF
}

_help_image() {
  cat <<EOF
Usage: $(basename "$0") image <subcmd> <alias> [args]

  build <alias> [--tag TAG] [--platform PLATFORM] [--cloud]
                       build Docker image for <alias> using its registered
                       image_tag (4th col of aliases.tsv). --tag overrides
                       the registry for this build only. Auto-infers platform
                       from tag (-arm64 / -amd64) and cross-builds via
                       ``docker buildx`` if host arch differs.
  push <alias> [--tag TAG]
                       ECR login + tag + push for <alias>'s image. Prints
                       full ECR URI on success (auto-resolved by ``run launch``
                       for ray).
  pull <alias> [--tag TAG]
                       pull <alias>'s image from ECR + retag locally.
  list [REPO]          list ECR repository contents (no alias arg; default
                       repository is ``vecoli``).
  age  <alias>         compare the alias's ECR image push time to the latest
                       git commit under ecoli/+runscripts/. Flags STALE when
                       the image predates a tracked source change — useful
                       to confirm whether NF Batch tasks are actually running
                       your current code (``run launch`` defaults to
                       --no-build-image, so the image is sticky).
  help                 this screen
EOF
}

_help_run() {
  cat <<EOF
Usage: $(basename "$0") run <subcmd> <alias> [args]

  launch <alias> [--resume] [--build] [--from-origin]
                       rotate experiment_id sidecar, rsync local repo → head,
                       start workflow in tmux. Defaults match the iteration
                       loop:
                         - skip in-workflow image rebuild (use ``image build/push``
                           yourself; --build to opt back in).
                         - rsync local working tree to head (skips
                           .git/.venv/out/__pycache__) so local edits run
                           without commit + push. --from-origin to clean-pull
                           origin/composite instead.
                       --resume reuses the last sidecar exp_id (must have
                       launched once before).
  resume <alias>       shorthand for ``launch --resume``.
  id     <alias>       print the active experiment_id (sidecar value, or
                       config base if no run yet).
  cancel <alias>       kill tmux on head + terminate Batch jobs in the
                       alias's queue.
  status <alias>       coherent dashboard: head EC2 + tmux + Batch (scoped
                       to active exp_id) + S3 object count + last write age.
                       Tails the driver log if tmux died.
  jobs   <alias> [STATUS]
                       list Batch jobs in alias's queue (Nextflow only;
                       default STATUS=RUNNING).
  coverage <alias> [--seeds N] [--gens N]
                       (seed × gen) matrix showing which cell-gens have
                       output in S3. Auto-detects shape from config
                       (walks inherit_from). Variant-agnostic.
  tail   <alias>       live-tail tmux log on head (sed-strips ANSI).
  log    <alias> [-n N]
                       print driver log + variant-specific cluster log
                       (lineage_ray.log for ray; nextflow trace + failed-task
                       CloudWatch streams for batch). Use after a run finishes
                       or fails. Auto-prints a head-state diagnostic when
                       neither the workflow log nor the bootstrap log is on
                       the head (see ``diag`` below).
  timing <alias> [-o PATH]
                       parse heartbeat lines in the driver workflow log
                       and render a runtime curve PNG (sim/wall rate +
                       cell count vs sim_time, with division markers).
                       Works mid-run and post-run. Default output:
                       out/colony_runtime_<exp_id>.png.
  diag   <alias>       head-state diagnostic: ~/ contents, tmux ls, running
                       procs, uptime (compare to experiment launch — newer
                       uptime means the head was replaced), disk, dmesg OOM,
                       cloud-init-output, /var/log/messages errors. Auto-fires
                       from ``run log`` when no logs are present; run it
                       directly when you want the state without trying to
                       read a log first.
  help                 this screen
EOF
}

_help_cache() {
  cat <<EOF
Usage: $(basename "$0") cache <subcmd> <alias>

S3-back the head's ~/vEcoli/.nextflow/ work directory under
s3://<out_bucket>/_cache/<exp_id>/.nextflow/. Lets a fresh head pick up where
a previous run left off (Nextflow only).

  push <alias>   sync ~/.nextflow/ on head → S3 cache.
  pull <alias>   sync S3 cache → ~/.nextflow/ on head.
  ls   <alias>   list cached snapshot for the alias's active exp_id.
  rm   <alias>   delete cached snapshot (confirmed).
  help           this screen
EOF
}

_help_compare() {
  cat <<EOF
Usage: $(basename "$0") compare <subcmd> [<alias>] [args]

Pairwise output analysis between v1 and v2 runs. Defaults v1/v2/extras IDs from
the per-alias sidecars (v1.experiment-id, v2.experiment-id, plus any other
alias's sidecar for the N-way report). Runs on <alias>'s head; defaults to
the ``compare`` alias (a dedicated comparison head — m7g.xlarge with 200 GB
EBS for synced parquet).

  bootstrap [<alias>]
                       one-shot: scp + run bootstrap_head_compare.sh to
                       install uv + clone vEcoli + uv sync. Run AFTER
                       ``head setup compare``.
  parity [<alias>] [SEED] [GEN]
                       diff v1 vs v2 bulk at SEED/GEN (default 0/3).
  full-parity [<alias>] [--seeds 0,1] [--gens 1,2] [--v1-id ID] [--v2-id ID]
                       all-columns parity scan via compute_full_parity.py;
                       writes out/full_parity__<v1>__<v2>.tsv{,.cols}.
  gens [<alias>]       max generation reached per seed in v1 vs v2 (catches
                       early-halt seeds parity scans miss).
  time <alias_a> <alias_b> [--gens 1,2,...] [--until N]
                       per-gen wall-time comparison via S3 file-mtime proxy.
                       Two aliases positional (no default). --gens filters
                       output to specific gens; --until N is shorthand for
                       --gens 1,2,...,N. Prints per-seed tables + median
                       ratio per gen (a/b).
  discover [<alias>] [--alias name] [--dry-run] [--force]
                       walk the alias registry, list each alias's S3 prefix,
                       and write the latest stamped <base>_YYYYMMDD-HHMMSS id
                       to .vecoli-aws-state/<alias>.experiment-id. Run this
                       to populate missing sidecars (e.g. after a fresh
                       checkout) so ``compare report`` auto-includes mp/ray.
  analyze <alias> [--analysis_name N] [--types T] [--cpus N]
                       run runscripts/analysis.py for <alias> on the compare
                       head and upload plots to S3 under the alias's
                       experiment_id. Use for Ray/MP runs that produced
                       parquet but skipped analyses/ (Nextflow v1/v2 already
                       publish their own). Must run AFTER ``compare bootstrap``.
  report [analysis|diff] [<alias>] [--gens 1,2] [--seeds 0,1]
         [--v1-id ID] [--v2-id ID] [--extra-ids label=ID,...]
         [--engine-cost N] [--out path]
         [--no-history] [--no-analyses] [--no-fetch] [--force]
                       fetch + render N-way markdown report. ``analysis``
                       sub: fetch plots, skip parity (fast). ``diff`` sub:
                       compute parity, skip plot re-fetch (slow). Bare
                       ``report`` does both. Extras auto-discovered from
                       any alias sidecar (mp, ray, etc.). --no-fetch reuses
                       out/<exp>/ already on head (fast iteration). Each
                       id validated against S3 before SSH'ing — --force skips.
  export [<alias>] [html|pdf] [path/to/report.md]
                       convert markdown report → single-file artifact via
                       pandoc (+ weasyprint for pdf).
  help                 this screen
EOF
}

# --- 11. Dispatch ---------------------------------------------------------

# Consume optional alias token at $1 (any non-flag positional), call
# _use_variant, then forward the rest to <fn>. Used by every namespace
# whose subcmds take an alias as their first positional.
_dispatch_variant() {
  local fn="$1"; shift
  local v=""
  if [[ "${1:-}" =~ ^[^-] && -n "${1:-}" ]]; then
    v="$1"; shift
  fi
  _use_variant "$v" || exit 1
  "$fn" "$@"
}

# Strict variant: consume $1 as alias only if it resolves in the registry
# (or is a literal config path). Otherwise default to <default_alias>.
# Used by ``compare`` whose subcmds also take positional args (seed/gen,
# html/pdf) that mustn't be misread as aliases.
_dispatch_with_default_alias() {
  local fn="$1" default_alias="$2"; shift 2
  local v="$default_alias"
  case "${1:-}" in
    -*|"") ;;
    *)
      local cfg; cfg=$(_alias_to_config "$1")
      if [[ -n "$cfg" ]]; then v="$1"; shift; fi
      ;;
  esac
  _use_variant "$v" || exit 1
  "$fn" "$@"
}

cmd="${1:-help}"; shift || true
case "$cmd" in
  experiment)
    sub="${1:-help}"; shift || true
    case "$sub" in
      new)              ns_experiment_new "$@" ;;
      list)             ns_experiment_list "$@" ;;
      end)              _dispatch_variant ns_experiment_end "$@" ;;
      rm)               _dispatch_variant ns_experiment_rm  "$@" ;;
      help|-h|--help)   _help_experiment ;;
      *) echo "experiment: unknown subcmd '$sub'" >&2; _help_experiment >&2; exit 1 ;;
    esac
    ;;
  head)
    sub="${1:-help}"; shift || true
    case "$sub" in
      setup)            _dispatch_variant ns_head_setup         "$@" ;;
      setup-ray-iam)    _dispatch_variant ns_head_setup_ray_iam "$@" ;;
      setup-s3-endpoint) ns_head_setup_s3_endpoint "$@" ;;
      terminate)        _dispatch_variant ns_head_terminate     "$@" ;;
      terminate-all)    ns_head_terminate_all "$@" ;;
      list)             _dispatch_variant ns_head_list       "$@" ;;
      list-all)         ns_head_list_all "$@" ;;
      dedupe)           _dispatch_variant ns_head_dedupe     "$@" ;;
      sync)             _dispatch_variant ns_head_sync       "$@" ;;
      rebuild)          _dispatch_variant ns_head_rebuild    "$@" ;;
      reboot)           _dispatch_variant ns_head_reboot     "$@" ;;
      stop)             _dispatch_variant ns_head_stop       "$@" ;;
      start)            _dispatch_variant ns_head_start      "$@" ;;
      refresh-sg)       _dispatch_variant ns_head_refresh_sg "$@" ;;
      dns)              _dispatch_variant ns_head_dns        "$@" ;;
      ssh)              _dispatch_variant ns_head_ssh        "$@" ;;
      attach)           _dispatch_variant ns_head_attach     "$@" ;;
      help|-h|--help)   _help_head ;;
      *) echo "head: unknown subcmd '$sub'" >&2; _help_head >&2; exit 1 ;;
    esac
    ;;
  image)
    sub="${1:-help}"; shift || true
    case "$sub" in
      build)            _dispatch_variant ns_image_build "$@" ;;
      push)             _dispatch_variant ns_image_push  "$@" ;;
      pull)             _dispatch_variant ns_image_pull  "$@" ;;
      list)             ns_image_list "$@" ;;
      age)              _dispatch_variant ns_image_age   "$@" ;;
      help|-h|--help)   _help_image ;;
      *) echo "image: unknown subcmd '$sub'" >&2; _help_image >&2; exit 1 ;;
    esac
    ;;
  run)
    sub="${1:-help}"; shift || true
    case "$sub" in
      launch)           _dispatch_variant ns_run_launch "$@" ;;
      resume)           _dispatch_variant ns_run_resume "$@" ;;
      id)               _dispatch_variant ns_run_id     "$@" ;;
      cancel)           _dispatch_variant ns_run_cancel "$@" ;;
      status)           _dispatch_variant ns_run_status "$@" ;;
      jobs)             _dispatch_variant ns_run_jobs   "$@" ;;
      coverage)         _dispatch_variant ns_run_coverage "$@" ;;
      tail)             _dispatch_variant ns_run_tail   "$@" ;;
      log)              _dispatch_variant ns_run_log    "$@" ;;
      timing)           _dispatch_variant ns_run_timing "$@" ;;
      diag)             _dispatch_variant ns_run_diag   "$@" ;;
      help|-h|--help)   _help_run ;;
      *) echo "run: unknown subcmd '$sub'" >&2; _help_run >&2; exit 1 ;;
    esac
    ;;
  cache)
    sub="${1:-help}"; shift || true
    case "$sub" in
      push|pull|ls|rm)
        v=$(_consume_variant_arg "${1:-}"); [[ -n "$v" ]] && shift
        _use_variant "$v" || exit 1
        ns_cache "$sub" "$@"
        ;;
      help|-h|--help)   _help_cache ;;
      *) echo "cache: unknown subcmd '$sub'" >&2; _help_cache >&2; exit 1 ;;
    esac
    ;;
  compare)
    sub="${1:-help}"; shift || true
    case "$sub" in
      parity)           _dispatch_with_default_alias ns_compare_parity      compare "$@" ;;
      full-parity)      _dispatch_with_default_alias ns_compare_full_parity compare "$@" ;;
      gens)             _dispatch_with_default_alias ns_compare_gens        compare "$@" ;;
      time)             ns_compare_time "$@" ;;
      discover)         _dispatch_with_default_alias ns_compare_discover    compare "$@" ;;
      analyze)
        # ``analyze``'s first positional is the *target alias to analyze*
        # (e.g. ray), NOT the head context — the head is always ``compare``.
        # Pin the variant to compare and forward all args.
        _use_variant compare || exit 1
        ns_compare_analyze "$@"
        ;;
      report)
        # Sub-subcommands: ``report analysis`` (fast plots-only) and
        # ``report diff`` (parity matrices only). Bare ``report`` does
        # both, matching the original behavior. Any other first arg
        # (incl. an explicit ``--`` flag) falls through to the default.
        case "${1:-}" in
          analysis) shift; _dispatch_with_default_alias ns_compare_report compare --no-history "$@" ;;
          diff)     shift; _dispatch_with_default_alias ns_compare_report compare --no-analyses "$@" ;;
          *)               _dispatch_with_default_alias ns_compare_report compare "$@" ;;
        esac
        ;;
      export)           _dispatch_with_default_alias ns_compare_export      compare "$@" ;;
      bootstrap)        _dispatch_with_default_alias ns_compare_bootstrap   compare "$@" ;;
      help|-h|--help)   _help_compare ;;
      *) echo "compare: unknown subcmd '$sub'" >&2; _help_compare >&2; exit 1 ;;
    esac
    ;;
  help|-h|--help) _help_top ;;
  *)
    echo "Unknown command: $cmd" >&2
    echo >&2
    _help_top >&2
    exit 1
    ;;
esac
