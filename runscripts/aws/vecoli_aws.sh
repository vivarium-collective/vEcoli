#!/usr/bin/env bash
# vEcoli AWS Batch workflow CLI.
#
# One entry point for the operations we keep running by hand:
#   provision a head node, bootstrap/launch the workflow, monitor, ssh in,
#   compare v1 vs v2 outputs, recover when the head wedges.
#
# Defaults read from configs/comparison_10s_16g_v2_aws.json — bucket, queue,
# region, experiment_id all come from there. Override via env vars below.
#
# Usage: runscripts/aws/vecoli_aws.sh <command> [args]
# See `help`.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

PROFILE="${VECOLI_AWS_PROFILE:-stanford-sso}"
REGION="${VECOLI_AWS_REGION:-us-gov-west-1}"
CONFIG_REL="${VECOLI_AWS_CONFIG:-configs/comparison_10s_16g_v2_aws.json}"
CONFIG_ABS="$REPO_ROOT/$CONFIG_REL"
KEY_FILE="${VECOLI_AWS_KEY:-$HOME/.ssh/vecoli-head-key.pem}"
HEAD_NAME="${VECOLI_AWS_HEAD_NAME:-vecoli-v2-head}"
TMUX_SESSION="${VECOLI_AWS_TMUX:-vecoli-v2}"

aws_cli() { aws --profile "$PROFILE" --region "$REGION" "$@"; }

# Pull config-derived values via python so we don't reimplement JSON parsing.
read_cfg() {
  [[ -f "$CONFIG_ABS" ]] || { echo "missing config: $CONFIG_ABS" >&2; exit 1; }
  python3 -c "import json,sys; c=json.load(open('$CONFIG_ABS')); print(c$1)"
}
EXP_ID=$(read_cfg "['experiment_id']")
QUEUE=$(read_cfg "['aws']['batch_queue']")
OUT_URI=$(read_cfg "['emitter_arg']['out_uri']")
BUCKET="${OUT_URI#s3://}"; BUCKET="${BUCKET%%/*}"
PREFIX="${OUT_URI#s3://$BUCKET/}"   # e.g. vecoli-output/<exp_id>

# --- helpers ---------------------------------------------------------------
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
  aws_cli batch list-jobs --job-queue "$QUEUE" --job-status "$1" \
    --query 'length(jobSummaryList)' --output text 2>/dev/null || echo "?"
}

# --- commands --------------------------------------------------------------
cmd_setup() {
  echo "Provisioning new head node ($HEAD_NAME, t4g.large)..."
  bash "$SCRIPT_DIR/setup_head_node.sh"
}

cmd_dns() {
  get_running_dns
}

cmd_status() {
  local id state dns
  id=$(get_instance_id)
  if [[ -z "$id" || "$id" == "None" ]]; then
    echo "Head: none (tag Name=$HEAD_NAME)"
  else
    state=$(aws_cli ec2 describe-instances --instance-ids "$id" \
      --query 'Reservations[0].Instances[0].State.Name' --output text)
    dns=$(aws_cli ec2 describe-instances --instance-ids "$id" \
      --query 'Reservations[0].Instances[0].PublicDnsName' --output text)
    echo "Head: $id  state=$state  dns=${dns:-N/A}"
  fi
  echo
  echo "Batch queue $QUEUE:"
  for s in SUBMITTED PENDING RUNNABLE STARTING RUNNING SUCCEEDED FAILED; do
    printf '  %-10s %s\n' "$s" "$(job_count "$s")"
  done
  echo
  echo "Last S3 writes for $EXP_ID:"
  aws_cli s3 ls --recursive "s3://$BUCKET/$PREFIX/" 2>/dev/null \
    | sort -k1,2 | tail -3 | awk '{print "  "$1, $2, "  ", $4}'
}

cmd_jobs() {
  local s="${1:-RUNNING}"
  aws_cli batch list-jobs --job-queue "$QUEUE" --job-status "$s" \
    --query 'jobSummaryList[*].[jobName,createdAt]' --output table
}

cmd_bootstrap() {
  local resume_env=""
  [[ "${1:-}" == "resume" ]] && resume_env="RESUME=1 "
  local dns; dns=$(require_running_dns)
  echo "scp bootstrap_head.sh -> $dns"
  scp -i "$KEY_FILE" "$SCRIPT_DIR/bootstrap_head.sh" "ec2-user@$dns:~/"
  # Pass through CONFIG_RELPATH and SESSION (default to v2) so the same
  # head can host parallel v1 and v2 workflows in separate tmux sessions.
  local config_env="CONFIG_RELPATH='${CONFIG_REL}' "
  local session_env=""
  [[ -n "${SESSION:-}" ]] && session_env="SESSION='$SESSION' "
  echo "running ${resume_env:-fresh} bootstrap (config=${CONFIG_REL}, session=${SESSION:-vecoli-v2})..."
  ssh -i "$KEY_FILE" "ec2-user@$dns" "${resume_env}${config_env}${session_env}bash ~/bootstrap_head.sh"
}
cmd_launch()  { cmd_bootstrap; }
cmd_resume()  { cmd_bootstrap resume; }

cmd_ssh()    { exec ssh -i "$KEY_FILE" "ec2-user@$(require_running_dns)" "$@"; }
cmd_attach() { exec ssh -i "$KEY_FILE" -t "ec2-user@$(require_running_dns)" "tmux attach -t $TMUX_SESSION"; }
cmd_tail()   { exec ssh -i "$KEY_FILE" "ec2-user@$(require_running_dns)" "tail -f ~/v2_workflow.log"; }

cmd_reboot() {
  local id; id=$(get_instance_id)
  [[ -n "$id" && "$id" != "None" ]] || { echo "no head"; return 1; }
  aws_cli ec2 reboot-instances --instance-ids "$id"
  echo "Reboot signaled. Status checks should be 'ok' in ~60s."
}

cmd_stop() {
  local id; id=$(get_instance_id)
  [[ -n "$id" && "$id" != "None" ]] || { echo "no head"; return 1; }
  aws_cli ec2 stop-instances --instance-ids "$id" >/dev/null
  echo "Stopping $id... (preserves EBS root volume incl. ~/.nextflow/)"
  aws_cli ec2 wait instance-stopped --instance-ids "$id"
  echo "Stopped. Use 'start' to bring back. Public IP will change."
}

cmd_start() {
  local id; id=$(get_instance_id)
  [[ -n "$id" && "$id" != "None" ]] || { echo "no stopped head; use 'setup' to provision new"; return 1; }
  aws_cli ec2 start-instances --instance-ids "$id" >/dev/null
  echo "Starting $id..."
  aws_cli ec2 wait instance-running --instance-ids "$id"
  local dns; dns=$(get_running_dns)
  echo "Running. New public DNS: $dns"
  echo "If your laptop's IP changed, refresh SSH ingress with:"
  echo "  ./runscripts/aws/vecoli_aws.sh refresh-sg"
}

cmd_refresh_sg() {
  # Re-add current public IP to vecoli-head-ssh-sg (idempotent).
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

cmd_cache() {
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

cmd_terminate() {
  local id; id=$(get_instance_id)
  [[ -n "$id" && "$id" != "None" ]] || { echo "no head to terminate"; return; }
  read -r -p "Terminate $id ($HEAD_NAME)? [y/N] " ans
  [[ "$ans" == "y" || "$ans" == "Y" ]] || { echo "aborted"; return; }
  aws_cli ec2 terminate-instances --instance-ids "$id" >/dev/null
  echo "Waiting for terminated state..."
  aws_cli ec2 wait instance-terminated --instance-ids "$id"
  echo "Terminated."
}

cmd_rebuild() {
  cmd_terminate
  cmd_setup
  echo
  echo "Next: $(basename "$0") resume   # or 'launch' for a fresh experiment"
}

cmd_compare() {
  local seed="${1:-0}" gen="${2:-3}"
  uv run --no-sync python "$SCRIPT_DIR/compare_v1_v2_at_gen.py" \
    --seed "$seed" --gen "$gen"
}

# Convert doc/v1_v2_report.md to a self-contained shareable artifact.
# Default: HTML with images base64-embedded (single file, browser-native).
# Pass 'pdf' for PDF via weasyprint (needs `pip install weasyprint`).
cmd_export() {
  local fmt="${1:-html}"
  local src="$REPO_ROOT/doc/v1_v2_report.md"
  [[ -f "$src" ]] || { echo "no $src yet — run 'report' first" >&2; return 1; }
  command -v pandoc >/dev/null \
    || { echo "pandoc not installed: sudo apt install pandoc" >&2; return 1; }
  case "$fmt" in
    html)
      local out="$REPO_ROOT/doc/v1_v2_report.html"
      # --embed-resources is pandoc >=2.19; --self-contained is the legacy
      # alias still accepted by 2.x but removed in 3.x. Try new, fall back.
      local embed_flag="--embed-resources"
      pandoc --help 2>&1 | grep -q -- '--embed-resources' \
        || embed_flag="--self-contained"
      pandoc -s "$embed_flag" \
        --metadata title="vEcoli v1 vs v2" \
        --resource-path="$REPO_ROOT/doc:$REPO_ROOT/doc/_static" \
        -o "$out" "$src"
      echo "Wrote $out ($(du -h "$out" | cut -f1), single-file, mailable)"
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
    *)
      echo "usage: $(basename "$0") export [html|pdf]" >&2
      return 1
      ;;
  esac
}

# Render the v1↔v2 markdown report. Runs on the head (in-region S3 fetch is
# fast). Pulls the rendered markdown + assets back to local doc/.
cmd_report() {
  local v1_id="${VECOLI_V1_ID:-sim35-comparison_test_6-4b7b}"
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

cmd_help() {
  cat <<EOF
Usage: $(basename "$0") <command> [args]

Lifecycle:
  setup            Provision a new head node (t4g.large)
  rebuild          Terminate existing head + provision fresh
  reboot           Reboot the head (preserves IP)
  stop             Stop the head, preserving EBS (incl. ~/.nextflow/ cache)
  start            Start a stopped head (public IP changes)
  refresh-sg       Re-add current public IP to the SSH security group
  terminate        Terminate the head (confirmed)
  cache push|pull  Sync ~/.nextflow/ to/from s3://.../_cache/<exp>/

Workflow:
  launch           scp bootstrap and start a fresh workflow run
  resume           same, but with RESUME=1 (Nextflow -resume)
  bootstrap [resume]
                   alias: launch / resume

Observe:
  status           head state + Batch job counts + last S3 writes
  jobs [STATUS]    list Batch jobs (default RUNNING)
  tail             tail ~/v2_workflow.log on head
  attach           SSH and attach tmux '$TMUX_SESSION'
  ssh [cmd...]     SSH into head (interactive or run command)
  dns              print head's public DNS

Compare:
  compare [SEED] [GEN]
                   diff v1 vs v2 bulk vectors at SEED/GEN (default 0/3)
  report           fetch v1+v2 outputs on head, render v1_v2_report.md, pull to local
                   env: VECOLI_V1_ID, VECOLI_V2_ID, VECOLI_REPORT_SEEDS,
                        VECOLI_REPORT_GENS, VECOLI_INCLUDE_HISTORY
  export [FMT]     convert doc/v1_v2_report.md to a single-file artifact
                   FMT: html (default, self-contained) or pdf (needs weasyprint)

Config (read from $CONFIG_REL):
  experiment_id    $EXP_ID
  batch_queue      $QUEUE
  out_uri          $OUT_URI

Env overrides:
  VECOLI_AWS_PROFILE   AWS CLI profile (default: $PROFILE)
  VECOLI_AWS_REGION    AWS region      (default: $REGION)
  VECOLI_AWS_CONFIG    config path     (default: $CONFIG_REL)
  VECOLI_AWS_KEY       SSH key         (default: $KEY_FILE)
  VECOLI_AWS_HEAD_NAME EC2 Name tag    (default: $HEAD_NAME)
  VECOLI_AWS_TMUX      tmux session    (default: $TMUX_SESSION)
EOF
}

# --- dispatch --------------------------------------------------------------
cmd="${1:-help}"; shift || true
case "$cmd" in
  setup)      cmd_setup      "$@" ;;
  rebuild)    cmd_rebuild    "$@" ;;
  reboot)     cmd_reboot     "$@" ;;
  stop)       cmd_stop       "$@" ;;
  start)      cmd_start      "$@" ;;
  refresh-sg) cmd_refresh_sg "$@" ;;
  cache)      cmd_cache      "$@" ;;
  terminate)  cmd_terminate  "$@" ;;
  bootstrap)  cmd_bootstrap  "$@" ;;
  launch)     cmd_launch     "$@" ;;
  resume)     cmd_resume     "$@" ;;
  status)     cmd_status     "$@" ;;
  jobs)       cmd_jobs       "$@" ;;
  ssh)        cmd_ssh        "$@" ;;
  attach)     cmd_attach     "$@" ;;
  tail)       cmd_tail       "$@" ;;
  dns)        cmd_dns        "$@" ;;
  compare)    cmd_compare    "$@" ;;
  report)     cmd_report     "$@" ;;
  export)     cmd_export     "$@" ;;
  help|-h|--help) cmd_help ;;
  *) echo "Unknown command: $cmd" >&2; echo; cmd_help; exit 1 ;;
esac
