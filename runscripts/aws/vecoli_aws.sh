#!/usr/bin/env bash
# vEcoli AWS workflow CLI (unified namespaced version).
#
# Single entry point for everything we keep running by hand: provision a
# head, build/push the Docker image, launch a workflow, monitor progress,
# compare outputs across paths.
#
# Variant (Nextflow / MP-single-node / Ray-cluster) is selected by the
# active config's ``aws.deploy_mode`` field — the CLI is a thin
# dispatcher; the per-variant work lives in
# ``bootstrap_head[_mp|_ray].sh`` under this directory. Adding a new
# variant means: new config + new bootstrap script + one case branch
# in ``_resolve_variant``. No top-level commands to add.
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

# --- 1. Global env (overridable) -------------------------------------------
PROFILE="${VECOLI_AWS_PROFILE:-stanford-sso}"
REGION="${VECOLI_AWS_REGION:-us-gov-west-1}"
CONFIG_REL="${VECOLI_AWS_CONFIG:-configs/comparison_10s_16g_v2_aws.json}"
CONFIG_ABS="$REPO_ROOT/$CONFIG_REL"
KEY_FILE="${VECOLI_AWS_KEY:-$HOME/.ssh/vecoli-head-key.pem}"

# Legacy/manual overrides — leave unset to let _resolve_variant pick
# defaults from the config's deploy_mode.
HEAD_NAME_OVERRIDE="${VECOLI_AWS_HEAD_NAME:-}"
TMUX_SESSION_OVERRIDE="${VECOLI_AWS_TMUX:-}"
HEAD_INSTANCE_TYPE_OVERRIDE="${HEAD_INSTANCE_TYPE:-}"

aws_cli() { aws --profile "$PROFILE" --region "$REGION" "$@"; }

# --- 2. Config loading ------------------------------------------------------
# read_cfg fails on missing keys; read_cfg_opt returns empty.
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

EXP_ID=$(read_cfg "['experiment_id']")
OUT_URI=$(read_cfg "['emitter_arg']['out_uri']")
DEPLOY_MODE=$(read_cfg_opt "['aws']['deploy_mode']")
QUEUE=$(read_cfg_opt "['aws']['batch_queue']")
BUCKET="${OUT_URI#s3://}"; BUCKET="${BUCKET%%/*}"
PREFIX="${OUT_URI#s3://$BUCKET/}"
ACCOUNT_ID=""  # lazy — only computed when needed (image push)

# --- 3. Variant resolution --------------------------------------------------
# Sets globals based on active config's deploy_mode + per-variant defaults
# (overridable via config aws.head_name / aws.tmux_session and via the
# legacy VECOLI_AWS_HEAD_NAME / VECOLI_AWS_TMUX env vars).
HEAD_NAME=""
HEAD_INSTANCE_TYPE=""
BOOTSTRAP_SCRIPT=""
TMUX_SESSION=""
EXTRA_FILES=()
_resolve_variant() {
  local cfg_head cfg_tmux
  cfg_head=$(read_cfg_opt "['aws']['head_name']")
  cfg_tmux=$(read_cfg_opt "['aws']['tmux_session']")

  case "${DEPLOY_MODE:-nextflow_batch}" in
    mp_single_node)
      HEAD_NAME="${HEAD_NAME_OVERRIDE:-${cfg_head:-vecoli-v2-mp-head}}"
      HEAD_INSTANCE_TYPE="${HEAD_INSTANCE_TYPE_OVERRIDE:-c7g.metal}"
      BOOTSTRAP_SCRIPT="$SCRIPT_DIR/bootstrap_head_mp.sh"
      TMUX_SESSION="${TMUX_SESSION_OVERRIDE:-${cfg_tmux:-vecoli-v2-mp}}"
      EXTRA_FILES=()
      ;;
    ray_cluster)
      HEAD_NAME="${HEAD_NAME_OVERRIDE:-${cfg_head:-vecoli-v2-ray-head}}"
      HEAD_INSTANCE_TYPE="${HEAD_INSTANCE_TYPE_OVERRIDE:-t4g.large}"
      BOOTSTRAP_SCRIPT="$SCRIPT_DIR/bootstrap_head_ray.sh"
      TMUX_SESSION="${TMUX_SESSION_OVERRIDE:-${cfg_tmux:-vecoli-v2-ray}}"
      EXTRA_FILES=("$SCRIPT_DIR/ec2_cluster_ray.py")
      ;;
    nextflow_batch|"")
      HEAD_NAME="${HEAD_NAME_OVERRIDE:-${cfg_head:-vecoli-v2-head}}"
      HEAD_INSTANCE_TYPE="${HEAD_INSTANCE_TYPE_OVERRIDE:-t4g.large}"
      BOOTSTRAP_SCRIPT="$SCRIPT_DIR/bootstrap_head.sh"
      TMUX_SESSION="${TMUX_SESSION_OVERRIDE:-${cfg_tmux:-vecoli-v2}}"
      EXTRA_FILES=()
      ;;
    *)
      echo "Unknown aws.deploy_mode: $DEPLOY_MODE" >&2
      echo "Expected: nextflow_batch | mp_single_node | ray_cluster" >&2
      exit 1
      ;;
  esac
}
_resolve_variant

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
  aws_cli batch list-jobs --job-queue "$QUEUE" --job-status "$1" \
    --query 'length(jobSummaryList)' --output text 2>/dev/null || echo "?"
}
account_id() {
  if [[ -z "$ACCOUNT_ID" ]]; then
    ACCOUNT_ID=$(aws_cli sts get-caller-identity --query Account --output text)
  fi
  echo "$ACCOUNT_ID"
}

# Common pattern: scp a bootstrap script (+ extra files) to head, ssh-run
# it with config + session env. Used by ``run launch`` and ``run resume``.
# $1: extra env to prepend (e.g. "RESUME=1 ").
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
  local image_env=""
  [[ -n "${IMAGE_URI:-}" ]] && image_env="IMAGE_URI='$IMAGE_URI' "
  echo "running bootstrap (variant=${DEPLOY_MODE:-nextflow_batch}, config=${CONFIG_REL}, session=${TMUX_SESSION})..."
  ssh -i "$KEY_FILE" "ec2-user@$dns" \
    "${extra_env}${config_env}${session_env}${sim_data_env}${image_env}bash ~/$(basename "$BOOTSTRAP_SCRIPT")"
}

# --- 5. ``head`` namespace --------------------------------------------------
ns_head_setup() {
  echo "Provisioning new head ($HEAD_NAME, $HEAD_INSTANCE_TYPE) for variant '${DEPLOY_MODE:-nextflow_batch}'..."
  HEAD_INSTANCE_TYPE="$HEAD_INSTANCE_TYPE" \
    HEAD_NAME="$HEAD_NAME" \
    ROOT_VOL_GIB="${ROOT_VOL_GIB:-30}" \
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
ns_head_rebuild() { ns_head_terminate; ns_head_setup; }
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

# --- 6. ``image`` namespace -------------------------------------------------
# Wraps runscripts/container/build-image.sh + the ECR docker tag/push
# pipeline that was previously documented in copy-pasted markdown.
IMAGE_TAG_DEFAULT="vecoli:v2-comparison-arm64"
ns_image_build() {
  # Default to local Docker build (suitable for the AWS workflow,
  # which then ECR-pushes via ``image push``). The underlying
  # build-image.sh script defaults to Google Cloud Build, which only
  # makes sense for the GCP path. Pass ``--cloud`` to override.
  local tag="$IMAGE_TAG_DEFAULT" local_build=1
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -t|--tag)   tag="$2"; shift 2 ;;
      -l|--local) local_build=1; shift ;;
      --cloud)    local_build=0; shift ;;
      *) echo "image build: unknown arg $1" >&2; return 1 ;;
    esac
  done
  echo "Building Docker image: $tag (local=$local_build)..."
  cd "$REPO_ROOT"
  local args=(-i "$tag")
  [[ $local_build -eq 1 ]] && args+=(-l)
  bash runscripts/container/build-image.sh "${args[@]}"
}
ns_image_push() {
  local tag="$IMAGE_TAG_DEFAULT"
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -t|--tag) tag="$2"; shift 2 ;;
      *) echo "image push: unknown arg $1" >&2; return 1 ;;
    esac
  done
  local acct; acct=$(account_id)
  local ecr_host="${acct}.dkr.ecr.${REGION}.amazonaws.com"
  local remote="${ecr_host}/${tag}"
  echo "Logging in to ECR ($ecr_host)..."
  aws_cli ecr get-login-password \
    | docker login --username AWS --password-stdin "$ecr_host" >/dev/null
  echo "Tagging $tag -> $remote"
  docker tag "$tag" "$remote"
  echo "Pushing $remote..."
  docker push "$remote"
  echo "Done. Image URI for downstream use:"
  echo "  $remote"
}
ns_image_list() {
  local repo="${1:-vecoli}"
  echo "ECR repository: $repo"
  aws_cli ecr describe-images --repository-name "$repo" \
    --query 'sort_by(imageDetails,&imagePushedAt)[*].[imageTags[0],imagePushedAt,imageSizeInBytes]' \
    --output table 2>/dev/null
}
ns_image_pull() {
  local tag="$IMAGE_TAG_DEFAULT"
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -t|--tag) tag="$2"; shift 2 ;;
      *) echo "image pull: unknown arg $1" >&2; return 1 ;;
    esac
  done
  local acct; acct=$(account_id)
  local remote="${acct}.dkr.ecr.${REGION}.amazonaws.com/${tag}"
  aws_cli ecr get-login-password \
    | docker login --username AWS --password-stdin "${acct}.dkr.ecr.${REGION}.amazonaws.com" >/dev/null
  docker pull "$remote"
  docker tag "$remote" "$tag"
  echo "Pulled $remote -> local $tag"
}

# --- 7. ``run`` namespace ---------------------------------------------------
ns_run_launch() {
  local extra_env=""
  [[ "${1:-}" == "--resume" ]] && extra_env="RESUME=1 "
  _run_bootstrap_on_head "$extra_env"
}
ns_run_resume() { ns_run_launch --resume; }
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
  local id state dns tmux_alive=0
  id=$(get_instance_id)
  if [[ -z "$id" || "$id" == "None" ]]; then
    echo "Head ($HEAD_NAME): none"
  else
    state=$(aws_cli ec2 describe-instances --instance-ids "$id" \
      --query 'Reservations[0].Instances[0].State.Name' --output text)
    dns=$(aws_cli ec2 describe-instances --instance-ids "$id" \
      --query 'Reservations[0].Instances[0].PublicDnsName' --output text)
    echo "Head ($HEAD_NAME): $id  state=$state  dns=${dns:-N/A}"
    if [[ "$state" == "running" ]]; then
      echo "Tmux session '$TMUX_SESSION':"
      if ssh -i "$KEY_FILE" -o StrictHostKeyChecking=no "ec2-user@$dns" \
          "tmux ls 2>/dev/null | grep -qF '$TMUX_SESSION'" 2>/dev/null; then
        echo "  (running)"
        tmux_alive=1
      else
        echo "  (not running)"
      fi
    fi
  fi
  echo
  echo "Variant: ${DEPLOY_MODE:-nextflow_batch}"
  if [[ -n "$QUEUE" ]]; then
    echo "Batch queue $QUEUE:"
    for s in SUBMITTED PENDING RUNNABLE STARTING RUNNING SUCCEEDED FAILED; do
      printf '  %-10s %s\n' "$s" "$(job_count "$s")"
    done
  fi
  echo
  echo "Last S3 writes for $EXP_ID:"
  aws_cli s3 ls --recursive "s3://$BUCKET/$PREFIX/" 2>/dev/null \
    | sort -k1,2 | tail -3 | awk '{print "  "$1, $2, "  ", $4}'
  # Diagnostic: if the head is running but tmux isn't, the workflow
  # finished or crashed. Show the tail of the log so the user can
  # see why without an extra ssh.
  if [[ -n "${dns:-}" && "${state:-}" == "running" && "$tmux_alive" -eq 0 ]]; then
    echo
    echo "Tmux not running but head is. Last 20 lines of ~/${TMUX_SESSION}_workflow.log:"
    ssh -i "$KEY_FILE" -o StrictHostKeyChecking=no "ec2-user@$dns" \
      "F=\$HOME/${TMUX_SESSION}_workflow.log; \
       [[ -f \$F ]] || F=\$HOME/v2_workflow.log; \
       [[ -f \$F ]] && tail -20 \"\$F\" | sed 's/^/  /' \
       || echo '  (no log file at \$F)'" 2>/dev/null
  fi
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
  local v1_id="${VECOLI_V1_ID:-comparison_10s_16g_v1_aws}"
  local v2_id="${VECOLI_V2_ID:-$EXP_ID}"
  local tmpdir; tmpdir=$(mktemp -d)
  trap 'rm -rf "$tmpdir"' RETURN

  _ns_max_gens_for() {
    # S3 history layout:
    #   vecoli-output/<exp>/<exp>/history/experiment_id=<exp>/
    #     variant=0/lineage_seed=<N>/generation=<M>/agent_id=*/...
    # We list once and pull the max gen seen per seed.
    local exp="$1"
    aws_cli s3 ls "s3://$BUCKET/vecoli-output/$exp/$exp/history/" \
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
ns_compare_report() {
  local v1_id="${VECOLI_V1_ID:-comparison_10s_16g_v1_aws}"
  local v2_id="${VECOLI_V2_ID:-$EXP_ID}"
  local seeds="${VECOLI_REPORT_SEEDS:-0,1,2,3,4,5,6,7,8,9}"
  local gens="${VECOLI_REPORT_GENS:-1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16}"
  local include_history="${VECOLI_INCLUDE_HISTORY:-1}"
  local dns; dns=$(require_running_dns)
  echo "Pushing latest scripts to head..."
  ssh -i "$KEY_FILE" "ec2-user@$dns" 'mkdir -p ~/vEcoli/runscripts/aws'
  scp -i "$KEY_FILE" \
      "$SCRIPT_DIR/fetch_and_compare.sh" \
      "$SCRIPT_DIR/compare_v1_v2_at_gen.py" \
      "$SCRIPT_DIR/compute_parity_matrix.py" \
      "ec2-user@$dns:~/vEcoli/runscripts/aws/"
  scp -i "$KEY_FILE" "$REPO_ROOT/runscripts/v1_v2_report.py" "ec2-user@$dns:~/vEcoli/runscripts/"
  echo "Running fetch + report on head ($v1_id vs $v2_id)..."
  ssh -i "$KEY_FILE" "ec2-user@$dns" "set -e; cd ~/vEcoli && \
    V1_ID='$v1_id' V2_ID='$v2_id' SEEDS='$seeds' GENS='$gens' \
    INCLUDE_HISTORY='$include_history' \
    BUCKET='$BUCKET' PREFIX='${PREFIX%%/*}' \
    bash runscripts/aws/fetch_and_compare.sh"
  echo "Pulling rendered report to local doc/..."
  scp -i "$KEY_FILE" "ec2-user@$dns:~/vEcoli/doc/v1_v2_report.md" "$REPO_ROOT/doc/" || true
  rsync -a -e "ssh -i $KEY_FILE" \
    "ec2-user@$dns:~/vEcoli/doc/_static/v1_v2_report_assets/" \
    "$REPO_ROOT/doc/_static/v1_v2_report_assets/" 2>/dev/null \
    || scp -ri "$KEY_FILE" "ec2-user@$dns:~/vEcoli/doc/_static/v1_v2_report_assets" "$REPO_ROOT/doc/_static/"
  echo "Report:  $REPO_ROOT/doc/v1_v2_report.md"
}
ns_compare_export() {
  local fmt="${1:-html}"
  local src="$REPO_ROOT/doc/v1_v2_report.md"
  [[ -f "$src" ]] || { echo "no $src yet — run 'compare report' first" >&2; return 1; }
  command -v pandoc >/dev/null \
    || { echo "pandoc not installed: sudo apt install pandoc" >&2; return 1; }
  case "$fmt" in
    html)
      local out="$REPO_ROOT/doc/v1_v2_report.html"
      local embed_flag="--embed-resources"
      pandoc --help 2>&1 | grep -q -- '--embed-resources' || embed_flag="--self-contained"
      pandoc -s "$embed_flag" \
        --metadata title="vEcoli v1 vs v2" \
        --resource-path="$REPO_ROOT/doc:$REPO_ROOT/doc/_static" \
        -o "$out" "$src"
      echo "Wrote $out ($(du -h "$out" | cut -f1))"
      ;;
    pdf)
      command -v weasyprint >/dev/null \
        || { echo "weasyprint not installed: uv pip install weasyprint" >&2; return 1; }
      local out="$REPO_ROOT/doc/v1_v2_report.pdf"
      pandoc --pdf-engine=weasyprint \
        --resource-path="$REPO_ROOT/doc:$REPO_ROOT/doc/_static" \
        -o "$out" "$src"
      echo "Wrote $out ($(du -h "$out" | cut -f1))"
      ;;
    *) echo "usage: $(basename "$0") compare export [html|pdf]" >&2; return 1 ;;
  esac
}

# --- 10. Help / dispatch ----------------------------------------------------
cmd_help() {
  cat <<EOF
Usage: $(basename "$0") <namespace> <subcmd> [args]
       $(basename "$0") help

Active config:  $CONFIG_REL
Variant:        ${DEPLOY_MODE:-nextflow_batch}
Head name:      $HEAD_NAME ($HEAD_INSTANCE_TYPE)
Tmux session:   $TMUX_SESSION
Bootstrap:      $(basename "$BOOTSTRAP_SCRIPT")

Namespaces (recommended):
  head <subcmd>     EC2 head-node lifecycle
    setup           provision new head ($HEAD_INSTANCE_TYPE)
    setup-ray-iam   one-time IAM grant for Ray cluster mode (run from
                    laptop with admin perms; idempotent)
    rebuild         terminate + setup
    reboot | stop | start | terminate
    refresh-sg      re-add current public IP to SSH SG
    dns | ssh [cmd] | attach

  image <subcmd>    Docker image lifecycle
    build [-t TAG] [--cloud]
                          build image (default: local Docker; tag: $IMAGE_TAG_DEFAULT)
                          --cloud uses Google Cloud Build (only relevant for GCP path)
    push  [-t TAG]        ECR login + tag + push
    pull  [-t TAG]        pull from ECR + retag locally
    list  [REPO]          list ECR images (default repo: vecoli)

  run <subcmd>      workflow execution (variant from config)
    launch [--resume]     scp bootstrap, start workflow in tmux
    resume                shorthand for ``launch --resume``
    cancel                kill tmux + terminate Batch jobs (if any)
    status                head + tmux + Batch (if any) + last S3 writes
    jobs [STATUS]         list Batch jobs (Nextflow only)
    tail                  tail tmux log on head

  cache <subcmd>    Nextflow .nextflow/ S3 cache (push|pull|ls|rm)

  compare <subcmd>  output analysis
    parity [SEED] [GEN]   diff v1 vs v2 bulk at SEED/GEN (default 0/3)
    gens                  max gen reached per seed in v1 vs active v2
                          (override v1 with VECOLI_V1_ID env)
    report                fetch+render v1 vs v2 markdown report
    export [html|pdf]     convert report to single-file artifact

Legacy (forwards to new commands; kept for backwards compat):
  setup, setup-mp, setup-ray  →  head setup    (variant from config)
  launch, launch-mp, launch-ray  →  run launch
  bootstrap, resume  →  run launch [--resume]
  status | jobs | tail | attach | ssh | dns  →  run/head equivalents
  rebuild | reboot | stop | start | terminate | refresh-sg  →  head equivalents
  compare | report | export  →  compare equivalents

Env overrides:
  VECOLI_AWS_PROFILE   AWS CLI profile (default: $PROFILE)
  VECOLI_AWS_REGION    AWS region      (default: $REGION)
  VECOLI_AWS_CONFIG    config path     (default: $CONFIG_REL)
  VECOLI_AWS_KEY       SSH key         (default: $KEY_FILE)
  VECOLI_AWS_HEAD_NAME EC2 Name tag    (override config-derived: $HEAD_NAME)
  VECOLI_AWS_TMUX      tmux session    (override config-derived: $TMUX_SESSION)
  HEAD_INSTANCE_TYPE   override default for the active variant
  SIM_DATA_S3_URI      S3 URI of pre-built simData.cPickle (skips parca on head)
  IMAGE_URI            ECR URI for Ray cluster image (Ray variant only)

To add a new variant:
  1. New config: configs/<your_variant>.json with aws.deploy_mode field
  2. New bootstrap: runscripts/aws/bootstrap_head_<variant>.sh
  3. Add a case branch in _resolve_variant. Done.
EOF
}

# Top-level dispatch — namespace OR legacy alias.
cmd="${1:-help}"; shift || true
case "$cmd" in
  # Namespaces -------------------------------------------------------------
  head)
    sub="${1:-help}"; shift || true
    case "$sub" in
      setup)         ns_head_setup "$@" ;;
      setup-ray-iam) ns_head_setup_ray_iam "$@" ;;
      terminate)     ns_head_terminate "$@" ;;
      rebuild)       ns_head_rebuild "$@" ;;
      reboot)        ns_head_reboot "$@" ;;
      stop)          ns_head_stop "$@" ;;
      start)         ns_head_start "$@" ;;
      refresh-sg)    ns_head_refresh_sg "$@" ;;
      dns)           ns_head_dns "$@" ;;
      ssh)           ns_head_ssh "$@" ;;
      attach)        ns_head_attach "$@" ;;
      help|*)        cmd_help ;;
    esac
    ;;
  image)
    sub="${1:-help}"; shift || true
    case "$sub" in
      build) ns_image_build "$@" ;;
      push)  ns_image_push "$@" ;;
      pull)  ns_image_pull "$@" ;;
      list)  ns_image_list "$@" ;;
      help|*) cmd_help ;;
    esac
    ;;
  run)
    sub="${1:-help}"; shift || true
    case "$sub" in
      launch) ns_run_launch "$@" ;;
      resume) ns_run_resume "$@" ;;
      cancel) ns_run_cancel "$@" ;;
      status) ns_run_status "$@" ;;
      jobs)   ns_run_jobs "$@" ;;
      tail)   ns_run_tail "$@" ;;
      help|*) cmd_help ;;
    esac
    ;;
  cache)   ns_cache "$@" ;;
  compare)
    sub="${1:-help}"; shift || true
    case "$sub" in
      parity) ns_compare_parity "$@" ;;
      gens)   ns_compare_gens "$@" ;;
      report) ns_compare_report "$@" ;;
      export) ns_compare_export "$@" ;;
      help|*) cmd_help ;;
    esac
    ;;
  # Legacy aliases ---------------------------------------------------------
  # Variant-suffixed legacies print a notice (variant now comes from
  # config — the suffix is redundant and can mislead if the active
  # config doesn't match). Plain aliases are silent equivalents.
  setup-mp|setup-ray)
    echo "Note: '$cmd' is deprecated — variant comes from config. Forwarding to: head setup (variant=${DEPLOY_MODE:-nextflow_batch})" >&2
    ns_head_setup "$@" ;;
  launch-mp|launch-ray)
    echo "Note: '$cmd' is deprecated — variant comes from config. Forwarding to: run launch (variant=${DEPLOY_MODE:-nextflow_batch})" >&2
    ns_run_launch "$@" ;;
  setup)         ns_head_setup "$@" ;;
  rebuild)       ns_head_rebuild "$@" ;;
  reboot)        ns_head_reboot "$@" ;;
  stop)          ns_head_stop "$@" ;;
  start)         ns_head_start "$@" ;;
  refresh-sg)    ns_head_refresh_sg "$@" ;;
  terminate)     ns_head_terminate "$@" ;;
  dns)           ns_head_dns "$@" ;;
  ssh)           ns_head_ssh "$@" ;;
  attach)        ns_head_attach "$@" ;;
  bootstrap|launch) ns_run_launch "$@" ;;
  resume)        ns_run_resume "$@" ;;
  status)        ns_run_status "$@" ;;
  jobs)          ns_run_jobs "$@" ;;
  tail)          ns_run_tail "$@" ;;
  report)        ns_compare_report "$@" ;;
  export)        ns_compare_export "$@" ;;
  # ``compare`` without subcmd is the legacy form: `compare [SEED] [GEN]`
  # Top-level dispatch already routed `compare <sub>` to the namespace
  # block above; reach here only if top-level matched but inner didn't.
  help|-h|--help) cmd_help ;;
  *)
    echo "Unknown command: $cmd" >&2
    echo
    cmd_help
    exit 1
    ;;
esac
