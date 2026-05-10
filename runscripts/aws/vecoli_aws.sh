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
# ``method`` is one of: batch | multiprocessing | ray | comparison (canonical), and
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
ray	configs/comparison_10s_16g_v2_ray_aws.json	ray	vecoli:v2-ray-amd64
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
# configs); ray → vecoli:v2-ray-amd64; multiprocessing → empty (no
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
      ray)             img="vecoli:v2-ray-amd64" ;;
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
#   batch | multiprocessing | ray | comparison
# Echoes the canonical name on success, "" on unknown input.
_normalize_method() {
  case "$1" in
    batch|nextflow_batch)              echo "batch" ;;
    mp|multiprocessing|mp_single_node) echo "multiprocessing" ;;
    ray|ray_cluster)                   echo "ray" ;;
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
      HEAD_INSTANCE_TYPE="${HEAD_INSTANCE_TYPE_OVERRIDE:-t4g.large}"
      BOOTSTRAP_SCRIPT="$SCRIPT_DIR/bootstrap_head_ray.sh"
      TMUX_SESSION="${TMUX_SESSION_OVERRIDE:-${cfg_tmux:-$default_tmux}}"
      EXTRA_FILES=("$SCRIPT_DIR/ec2_cluster_ray.py")
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
      echo "Expected: nextflow_batch | mp_single_node | ray_cluster | comparison_head" >&2
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
  # Auto-resolve IMAGE_URI for ray when not set explicitly via env. The
  # alias's registered image_tag (4th column of aliases.tsv) gets
  # combined with account_id + region into the full ECR URI that
  # ec2_cluster_ray.py expects. Ad-hoc env override still wins.
  local resolved_image_uri="${IMAGE_URI:-}"
  if [[ -z "$resolved_image_uri" && "$DEPLOY_MODE" == "ray_cluster" ]]; then
    local _tag; _tag=$(_alias_to_image "$STATE_KEY")
    if [[ -n "$_tag" ]]; then
      resolved_image_uri=$(_ecr_uri_for_tag "$_tag")
      echo "Auto-resolved IMAGE_URI from alias '$STATE_KEY': $resolved_image_uri"
    fi
  fi
  local image_env=""
  [[ -n "$resolved_image_uri" ]] && image_env="IMAGE_URI='$resolved_image_uri' "
  # Thread the CLI-resolved EXP_ID so workflow.py / run_composite_lineage_*.py
  # use the unique sidecar-tracked ID rather than the BASE id from config.
  local exp_id_env=""
  [[ -n "${EXP_ID:-}" ]] && exp_id_env="EXPERIMENT_ID='$EXP_ID' "
  echo "running bootstrap (variant=${DEPLOY_MODE:-nextflow_batch}, config=${CONFIG_REL}, session=${TMUX_SESSION}, exp_id=${EXP_ID})..."
  ssh -i "$KEY_FILE" "ec2-user@$dns" \
    "${extra_env}${config_env}${session_env}${sim_data_env}${image_env}${exp_id_env}bash ~/$(basename "$BOOTSTRAP_SCRIPT")"
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
      echo "Unknown method '$requested_method' — expected: batch | multiprocessing | ray | comparison" >&2
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
    echo "Run: $(basename "$0") head setup $STATE_KEY <batch|multiprocessing|ray>" >&2
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
  echo "If your laptop's IP changed: $(basename "$0") head refresh-sg"
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

  local rows; rows=$(aws_cli ec2 describe-instances \
    --filters "Name=tag:Name,Values=vecoli-*" \
              "Name=instance-state-name,Values=running,pending,stopping,stopped" \
    --query 'Reservations[].Instances[].[InstanceId,Tags[?Key==`Name`]|[0].Value,State.Name]' \
    --output text 2>/dev/null)

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
    echo "Nothing to clean up: no vecoli-* heads, no Batch queues registered."
    return 0
  fi

  if [[ -n "$rows" ]]; then
    echo "Heads matching vecoli-*:"
    echo "$rows" | awk '{ printf "  %-22s  %-30s  %s\n", $1, $2, $3 }'
  else
    echo "No vecoli-* heads currently provisioned."
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
    echo "Terminating EC2 heads..."
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
    echo "  method:    batch | multiprocessing | ray | comparison (optional; settable" >&2
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
      echo "Unknown method '$method_in' — expected: batch | multiprocessing | ray | comparison" >&2
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
    echo "  Set method: $(basename "$0") head setup $alias_name <batch|multiprocessing|ray>"
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
    echo "  prefer ``run launch <alias> --build`` which builds on the head node" >&2
    echo "  (t4g.large = ARM64) — or run head ssh + build-and-push-ecr.sh there." >&2
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
    echo "no running head ($HEAD_NAME) — run ``head setup $STATE_KEY`` first" >&2
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
  if (( resume == 0 )); then
    ssh -i "$KEY_FILE" -o StrictHostKeyChecking=no "ec2-user@$dns" \
      "tmux has-session -t '$TMUX_SESSION' 2>/dev/null \
         && (echo '  killing stale tmux session: $TMUX_SESSION'; \
             tmux kill-session -t '$TMUX_SESSION') \
         || true"
  fi

  _run_bootstrap_on_head "$extra_env"
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
ns_run_cancel() {
  local dns; dns=$(require_running_dns)
  echo "Killing tmux session '$TMUX_SESSION' on $dns..."
  ssh -i "$KEY_FILE" "ec2-user@$dns" "tmux kill-session -t $TMUX_SESSION 2>/dev/null || echo '  (no session to kill)'"
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
  if [[ -n "$QUEUE" ]]; then
    echo
    if (( since_ms > 0 )); then
      printf '  Batch %-22s  this run    queue total\n' "$QUEUE"
    else
      printf '  Batch %-22s     count\n' "$QUEUE"
    fi
    for s in SUBMITTED PENDING RUNNABLE STARTING RUNNING SUCCEEDED FAILED; do
      local total; total=$(job_count "$s")
      if (( since_ms > 0 )); then
        local recent; recent=$(_batch_count_since "$s" "$since_ms")
        # Skip rows that are zero in both columns to keep the table tight.
        if [[ "$recent" == "0" && "$total" == "0" ]]; then continue; fi
        printf '    %-12s %8s    %8s\n' "$s" "$recent" "$total"
      else
        if [[ "$total" == "0" ]]; then continue; fi
        printf '    %-12s %12s\n' "$s" "$total"
      fi
    done
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
    echo "  Tmux not running but head is. Last 20 lines of ~/${TMUX_SESSION}_workflow.log:"
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
ns_run_tail() {
  local log="~/${TMUX_SESSION}_workflow.log"
  exec ssh -i "$KEY_FILE" "ec2-user@$(require_running_dns)" \
    "F=${log/#\~/\$HOME}; \
     [[ -f \$F ]] || F=\$HOME/v2_workflow.log; \
     tail -f \$F | sed -u 's/\\x1b\\[[0-9;]*[a-zA-Z]//g; s/\\x1b\\][0-9];[^\\x07]*\\x07//g'"
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
    local cmd="F=\$HOME/${TMUX_SESSION}_workflow.log; \
      [[ -f \$F ]] || F=\$HOME/v2_workflow.log; \
      [[ -f \$F ]] || { echo 'no driver log on head'; exit 0; }; \
      ${n:+tail -n $n }cat \$F | \
      sed -u 's/\\x1b\\[[0-9;]*[a-zA-Z]//g; s/\\x1b\\][0-9];[^\\x07]*\\x07//g'"
    [[ -n "$n" ]] && cmd="F=\$HOME/${TMUX_SESSION}_workflow.log; \
      [[ -f \$F ]] || F=\$HOME/v2_workflow.log; \
      [[ -f \$F ]] || { echo 'no driver log on head'; exit 0; }; \
      tail -n $n \$F | \
      sed -u 's/\\x1b\\[[0-9;]*[a-zA-Z]//g; s/\\x1b\\][0-9];[^\\x07]*\\x07//g'"
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
        echo " 'compare report' to fetch it from S3 first.)"
      fi
      ;;
  esac
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
_compare_default_v1_id() {
  local f="$STATE_DIR/v1.experiment-id"
  if [[ -f "$f" ]]; then local v; v=$(<"$f"); echo "${v//$'\n'/}"
  else echo "comparison_10s_16g_v1_aws"; fi
}
_compare_default_v2_id() {
  local f="$STATE_DIR/v2.experiment-id"
  if [[ -f "$f" ]]; then local v; v=$(<"$f"); echo "${v//$'\n'/}"
  else echo "$EXP_ID"; fi
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
      --no-fetch|--cached) no_fetch=1; shift ;;
      -h|--help)
        cat >&2 <<USAGE
usage: $(basename "$0") compare report [--gens 1,2] [--seeds 0,1,...]
                                       [--v1-id ID] [--v2-id ID]
                                       [--extra-ids label=ID,...]
                                       [--out doc/v1_v2_report_4way.md]
                                       [--no-history] [--no-fetch]

Defaults pull v1/v2/mp/ray IDs from .vecoli-aws-state/<alias>.experiment-id
sidecars. ``--extra-ids`` (or VECOLI_EXTRA_IDS) overrides the auto-discovered
non-v1/non-v2 alias sidecars (e.g. mp, ray) and turns the table into N-way.
``--out`` (or VECOLI_REPORT_OUT) controls the output path; default is
``doc/v1_v2_report.md``. Assets land under ``_static/<stem>_assets/``.
``--no-fetch`` (or ``--cached``) skips ALL S3 sync steps and runs the
report against whatever is already in ``out/<exp>/`` on the head — much
faster for iterating on report formatting after the first sync.
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

# --- 10. Help / dispatch ----------------------------------------------------
cmd_help() {
  cat <<EOF
Usage: $(basename "$0") <namespace> <subcmd> <alias> [args]
       $(basename "$0") help

Aliases are dynamic. Built-in defaults (auto-seeded on first use):
  v1       configs/comparison_10s_16g_v1_aws.json     nextflow_batch
  v2       configs/comparison_10s_16g_v2_aws.json     nextflow_batch
  mp       configs/comparison_10s_16g_v2_mp_aws.json  mp_single_node
  ray      configs/comparison_10s_16g_v2_ray_aws.json ray_cluster
  compare  configs/compare_head.json                  comparison_head

Add more with ``experiment new <alias> <config>``. The registry lives at
.vecoli-aws-state/aliases.tsv (gitignored). ``experiment list`` prints
the current map.

Each alias is independent: its own config, head (Name tag
``vecoli-<alias>-head``), tmux session (``vecoli-<alias>``), and
experiment_id sidecar (``.vecoli-aws-state/<alias>.experiment-id``).
``run launch <alias>`` rotates the sidecar id; downstream subcmds read
it until the next launch.

Namespaces:
  experiment <subcmd>             alias lifecycle (no alias arg on `new`/`list`)
    new <alias> <config> [<method>] [-f]
                                  register alias→config (method optional;
                                  can be set later via `head setup`)
    list                          show registered aliases + configs + methods
    end <alias> [--terminate-head] [--rm]
                                  cancel running work + clear sidecar;
                                  --terminate-head also kills the EC2,
                                  --rm also unregisters the alias
    rm <alias> [-f]               unregister (refuses if head still running)

  head <subcmd> <alias>           EC2 head-node lifecycle
    setup [<method>]   provision new head; method is batch | multiprocessing
                       | ray. Required on first setup of a new alias;
                       optional after (registry remembers). Picks the
                       bootstrap script + default instance type.
    setup-ray-iam      one-time IAM grant for Ray cluster mode (run from
                       laptop with admin perms; idempotent)
    terminate-all [--cancel-jobs]
                       NUKE every EC2 tagged vecoli-* (confirmed prompt).
                       --cancel-jobs ALSO terminates active Batch jobs
                       across all queues in the alias registry (jobs
                       outlive their heads otherwise; they run on
                       Batch-managed compute and keep billing).
                       Does NOT touch: Batch queue/compute env, ECR
                       images, S3 outputs, sidecars, alias registry.
    list             show every non-terminated EC2 tagged with the
                     alias's HEAD_NAME (surfaces dupes)
    dedupe           keep the oldest running head, terminate the rest
    sync [-c]        rsync local vEcoli repo → alias's head; -c also
                     docker-cp's host code into the running vecoli_ray
                     container (skip image rebuild for head-side code
                     changes)
    rebuild          terminate + setup
    reboot | stop | start | terminate
    refresh-sg       re-add current public IP to SSH SG
    dns | ssh [cmd] | attach

  image <subcmd> <alias>      Docker image lifecycle (image_tag is per-alias)
    build [--tag TAG] [--platform PLATFORM] [--cloud]
                          build image for <alias> using its registered
                          image_tag (4th col of aliases.tsv). --tag
                          overrides for this build only. Auto-infers
                          platform from tag (-arm64 / -amd64) and
                          cross-builds via buildx if host arch differs.
    push  [--tag TAG]     ECR login + tag + push for <alias>'s image
    pull  [--tag TAG]     pull <alias>'s image from ECR + retag locally
    list  [REPO]          list all ECR repository images (no alias)

  run <subcmd> <alias>        workflow execution
    launch [--resume] [--build] [--from-origin]
                          assign new experiment_id, scp bootstrap,
                          start workflow in tmux. ``--resume`` reuses
                          the variant's sidecar experiment_id (must
                          have been launched once before).
                          Defaults:
                            - skip in-workflow image rebuild (image
                              namespace owns build/push). Use ``--build``
                              to let workflow.py rebuild+push.
                            - rsync local working tree → head and tell
                              bootstrap to skip ``git reset``. Lets
                              local edits run without commit + push.
                              Use ``--from-origin`` for clean / production
                              runs that pull origin/composite instead.
    resume                shorthand for ``launch --resume``
    id                    print the active experiment_id for this alias
                          (sidecar value, or BASE if no run yet)
    cancel                kill tmux + terminate Batch jobs (if any)
    status                head + tmux + Batch (if any) + last S3 writes
    jobs [STATUS]         list Batch jobs (Nextflow only)
    tail                  tail tmux log on head (live, follows)
    log [-n N]            print driver log + variant-specific
                          experiment log (S3 lineage_ray.log for
                          Ray, trace CSV for nextflow). Use after a
                          run finishes/fails to diagnose without
                          attaching to tmux.

  cache <subcmd> <alias>      Nextflow .nextflow/ S3 cache (push|pull|ls|rm)

  compare <subcmd>            pairwise output analysis (defaults to ``compare``
                              alias's head; override with VECOLI_AWS_CONFIG_VARIANT)
    bootstrap             one-time setup on the comparison head: scp +
                          run bootstrap_head_compare.sh (clone vEcoli,
                          uv sync). Run AFTER ``head setup compare``.
    parity [SEED] [GEN]   diff v1 vs v2 bulk at SEED/GEN (default 0/3)
    full-parity           run compute_full_parity.py on head — every
                          parquet column at every common timestep
                          (NOT just bulk). Defaults v1-id/v2-id from
                          .vecoli-aws-state/{v1,v2}.experiment-id
                          sidecars. Args: --seeds 0,1 --gens 1,2
                          --v1-id ID --v2-id ID
    gens                  max gen reached per seed in v1 vs v2 sidecar
                          (override with VECOLI_V1_ID / VECOLI_V2_ID env)
    report [--gens 1,2] [--seeds 0,1] [--v1-id ID] [--v2-id ID]
           [--extra-ids label=ID,...] [--no-history]
                          fetch+render N-way markdown report. v1/v2/mp/
                          ray IDs default to .vecoli-aws-state/<alias>.experiment-id
                          sidecars (extras auto-discovered for any
                          non-v1/v2/compare alias with a sidecar).
    export [html|pdf]     convert report to single-file artifact

Examples:
  $(basename "$0") experiment new myrun configs/foo.json batch vecoli:my-tag-arm64
  $(basename "$0") experiment list                          # show all aliases + methods + images
  $(basename "$0") head setup myrun batch                   # first time: pin method
  $(basename "$0") image build myrun                        # build image registered for myrun
  $(basename "$0") image push  myrun                        # push to ECR
  $(basename "$0") run launch myrun                         # rsync local, fresh exp_id
  $(basename "$0") run tail myrun                           # follow myrun's log
  $(basename "$0") run id myrun                             # active experiment_id
  $(basename "$0") run launch myrun --from-origin           # pull origin instead of rsync
  $(basename "$0") run launch myrun --build                 # rebuild image (on the head)
  $(basename "$0") experiment end myrun --terminate-head --rm   # tear down + remove
  $(basename "$0") head terminate-all --cancel-jobs         # full cleanup
                                                            #   (jobs + heads;
                                                            #    Batch queue stays)

Env overrides (rarely needed; CLI args take precedence):
  VECOLI_AWS_PROFILE   AWS CLI profile (default: $PROFILE)
  VECOLI_AWS_REGION    AWS region      (default: $REGION)
  VECOLI_AWS_CONFIG    config path     (used when no <alias> supplied)
  VECOLI_AWS_KEY       SSH key         (default: $KEY_FILE)
  VECOLI_AWS_HEAD_NAME EC2 Name tag    (override alias-derived default)
  VECOLI_AWS_TMUX      tmux session    (override alias-derived default)
  HEAD_INSTANCE_TYPE   override default for the active alias
  SIM_DATA_S3_URI      S3 URI of pre-built simData.cPickle (skips parca on head)
  IMAGE_URI            ECR URI for Ray cluster image (Ray alias only)

To add a new execution mode (batch / mp / ray are built in):
  1. New bootstrap: runscripts/aws/bootstrap_head_<mode>.sh
  2. Add a case branch in _use_variant for the new aws.deploy_mode value.
  3. Configs using that mode get picked up automatically.
EOF
}

# Variant-aware namespace dispatcher: consume optional variant token (first
# non-flag arg after the subcmd), call _use_variant, then forward the rest
# to the named function.
_dispatch_variant() {
  local fn="$1"; shift
  local v=""
  if [[ "${1:-}" =~ ^[^-] && -n "${1:-}" ]]; then
    v="$1"; shift
  fi
  _use_variant "$v" || exit 1
  "$fn" "$@"
}

# Top-level dispatch — namespace OR legacy alias.
cmd="${1:-help}"; shift || true
case "$cmd" in
  # Namespaces -------------------------------------------------------------
  head)
    sub="${1:-help}"; shift || true
    case "$sub" in
      setup)         _dispatch_variant ns_head_setup "$@" ;;
      setup-ray-iam) _dispatch_variant ns_head_setup_ray_iam "$@" ;;
      terminate)     _dispatch_variant ns_head_terminate "$@" ;;
      terminate-all) ns_head_terminate_all "$@" ;;
      list)          _dispatch_variant ns_head_list "$@" ;;
      dedupe)        _dispatch_variant ns_head_dedupe "$@" ;;
      sync)          _dispatch_variant ns_head_sync "$@" ;;
      rebuild)       _dispatch_variant ns_head_rebuild "$@" ;;
      reboot)        _dispatch_variant ns_head_reboot "$@" ;;
      stop)          _dispatch_variant ns_head_stop "$@" ;;
      start)         _dispatch_variant ns_head_start "$@" ;;
      refresh-sg)    _dispatch_variant ns_head_refresh_sg "$@" ;;
      dns)           _dispatch_variant ns_head_dns "$@" ;;
      ssh)           _dispatch_variant ns_head_ssh "$@" ;;
      attach)        _dispatch_variant ns_head_attach "$@" ;;
      help|*)        cmd_help ;;
    esac
    ;;
  experiment)
    sub="${1:-help}"; shift || true
    case "$sub" in
      new)  ns_experiment_new "$@" ;;
      list) ns_experiment_list "$@" ;;
      end)  _dispatch_variant ns_experiment_end "$@" ;;
      rm)   _dispatch_variant ns_experiment_rm "$@" ;;
      help|*) cmd_help ;;
    esac
    ;;
  image)
    # build/push/pull each take an alias (image_tag is per-alias in
    # the registry). list is variant-independent (lists ECR repo).
    sub="${1:-help}"; shift || true
    case "$sub" in
      build) _dispatch_variant ns_image_build "$@" ;;
      push)  _dispatch_variant ns_image_push  "$@" ;;
      pull)  _dispatch_variant ns_image_pull  "$@" ;;
      list)  ns_image_list "$@" ;;
      help|*) cmd_help ;;
    esac
    ;;
  run)
    sub="${1:-help}"; shift || true
    case "$sub" in
      launch) _dispatch_variant ns_run_launch "$@" ;;
      resume) _dispatch_variant ns_run_resume "$@" ;;
      id)     _dispatch_variant ns_run_id     "$@" ;;
      cancel) _dispatch_variant ns_run_cancel "$@" ;;
      status) _dispatch_variant ns_run_status "$@" ;;
      jobs)   _dispatch_variant ns_run_jobs   "$@" ;;
      tail)   _dispatch_variant ns_run_tail   "$@" ;;
      log)    _dispatch_variant ns_run_log    "$@" ;;
      help|*) cmd_help ;;
    esac
    ;;
  cache)
    # cache <subcmd> <variant>: peel subcmd FIRST, then variant inline
    # (can't reuse _dispatch_variant because it consumes variant from
    # position 0, but we want it from position 1 here).
    sub="${1:-help}"; shift || true
    case "$sub" in
      push|pull|ls|rm)
        v=$(_consume_variant_arg "${1:-}"); [[ -n "$v" ]] && shift
        _use_variant "$v" || exit 1
        ns_cache "$sub" "$@"
        ;;
      help|*) cmd_help ;;
    esac
    ;;
  compare)
    # compare uses v1/v2 sidecars internally for the ID defaults, but the
    # SSH target (HEAD_NAME) needs to be the comparison head — not v2's
    # workflow head. Default to the ``compare`` alias so the dedicated
    # compare head is used; override with VECOLI_AWS_CONFIG_VARIANT to
    # run on a workflow head instead. The compare config still points at
    # the same shared bucket so BUCKET/PREFIX resolve identically.
    _use_variant "${VECOLI_AWS_CONFIG_VARIANT:-compare}" || exit 1
    sub="${1:-help}"; shift || true
    case "$sub" in
      parity) ns_compare_parity "$@" ;;
      full-parity) ns_compare_full_parity "$@" ;;
      gens)   ns_compare_gens "$@" ;;
      report) ns_compare_report "$@" ;;
      export) ns_compare_export "$@" ;;
      bootstrap)
        # One-shot: scp + run bootstrap_head_compare.sh on the
        # comparison head. ``head setup`` only provisions the EC2;
        # this installs uv + clones vEcoli + uv sync. After this
        # the report / parity subcmds can run.
        _run_bootstrap_on_head ;;
      help|*) cmd_help ;;
    esac
    ;;
  # Legacy aliases ---------------------------------------------------------
  # Plain aliases — same variant-positional behavior as the namespaced
  # forms (e.g. ``setup v1`` ≡ ``head setup v1``).
  setup)         _dispatch_variant ns_head_setup "$@" ;;
  rebuild)       _dispatch_variant ns_head_rebuild "$@" ;;
  reboot)        _dispatch_variant ns_head_reboot "$@" ;;
  stop)          _dispatch_variant ns_head_stop "$@" ;;
  start)         _dispatch_variant ns_head_start "$@" ;;
  refresh-sg)    _dispatch_variant ns_head_refresh_sg "$@" ;;
  terminate)     _dispatch_variant ns_head_terminate "$@" ;;
  dns)           _dispatch_variant ns_head_dns "$@" ;;
  ssh)           _dispatch_variant ns_head_ssh "$@" ;;
  attach)        _dispatch_variant ns_head_attach "$@" ;;
  bootstrap|launch) _dispatch_variant ns_run_launch "$@" ;;
  resume)        _dispatch_variant ns_run_resume "$@" ;;
  status)        _dispatch_variant ns_run_status "$@" ;;
  jobs)          _dispatch_variant ns_run_jobs "$@" ;;
  tail)          _dispatch_variant ns_run_tail "$@" ;;
  report)
    _use_variant "${VECOLI_AWS_CONFIG_VARIANT:-compare}" || exit 1
    ns_compare_report "$@" ;;
  export)
    _use_variant "${VECOLI_AWS_CONFIG_VARIANT:-compare}" || exit 1
    ns_compare_export "$@" ;;
  # Variant-suffixed legacies (setup-mp / launch-ray / etc.) are removed —
  # use the new positional form instead (``head setup mp``, ``run launch ray``).
  help|-h|--help) cmd_help ;;
  *)
    echo "Unknown command: $cmd" >&2
    echo
    cmd_help
    exit 1
    ;;
esac
