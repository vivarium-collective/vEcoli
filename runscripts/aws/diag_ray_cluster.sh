#!/usr/bin/env bash
# Diagnose why workers aren't reaching SSM. Three failure modes ranked
# by frequency on GovCloud:
#
#   1. Worker subnet doesn't auto-assign public IPv4 → no internet
#      → can't reach ``ssm.us-gov-west-1.amazonaws.com`` → agent
#      never registers.
#   2. ``ray-process-bigraph-node`` instance profile missing or
#      missing policies → agent has no creds to call SSM API.
#   3. SG egress blocks 443 outbound → agent can't reach SSM.
#
# Run from your laptop with the stanford-sso profile.

set -euo pipefail

PROFILE="${AWS_PROFILE:-stanford-sso}"
REGION="${AWS_REGION:-us-gov-west-1}"
SUBNET_ID="${SUBNET_ID:-subnet-06cc68d397fb72760}"
SG_ID="${SG_ID:-sg-0cd911a8bc6f86caf}"
WORKER_PROFILE="${WORKER_PROFILE:-ray-process-bigraph-node}"

aws_cli() { aws --profile "$PROFILE" --region "$REGION" "$@"; }
ok() { echo "  ✓ $1"; }
warn() { echo "  ⚠ $1"; }
err() { echo "  ✗ $1"; }

echo "=== 1. Subnet auto-assign-public-IPv4 ==="
MAP_PUB=$(aws_cli ec2 describe-subnets --subnet-ids "$SUBNET_ID" \
  --query 'Subnets[0].MapPublicIpOnLaunch' --output text 2>/dev/null || echo "MISSING")
if [[ "$MAP_PUB" == "True" ]]; then
  ok "subnet $SUBNET_ID has MapPublicIpOnLaunch=true (workers will get public IPs)"
else
  err "subnet $SUBNET_ID has MapPublicIpOnLaunch=$MAP_PUB"
  warn "workers will have no public IP → can't reach SSM → agent never registers"
  echo
  echo "  Fix (one of):"
  echo "  a. Flip the subnet flag (affects ALL future launches in this subnet):"
  echo "       aws --profile $PROFILE --region $REGION \\"
  echo "         ec2 modify-subnet-attribute --subnet-id $SUBNET_ID \\"
  echo "         --map-public-ip-on-launch"
  echo "  b. Add SSM VPC endpoints (more isolated, no internet needed):"
  echo "       see: aws ssm setup-ssm-vpc-endpoints docs"
  echo
fi

echo
echo "=== 2. Worker instance profile ==="
if aws --profile "$PROFILE" iam get-instance-profile \
      --instance-profile-name "$WORKER_PROFILE" >/dev/null 2>&1; then
  ok "instance profile '$WORKER_PROFILE' exists"
  ROLE=$(aws --profile "$PROFILE" iam get-instance-profile \
    --instance-profile-name "$WORKER_PROFILE" \
    --query 'InstanceProfile.Roles[0].RoleName' --output text)
  echo "    role: $ROLE"
  POLICIES=$(aws --profile "$PROFILE" iam list-attached-role-policies \
    --role-name "$ROLE" --query 'AttachedPolicies[].PolicyName' --output text)
  echo "    attached policies: $POLICIES"
  if grep -qw "AmazonSSMManagedInstanceCore" <<<"$POLICIES"; then
    ok "AmazonSSMManagedInstanceCore present (SSM agent can register)"
  else
    err "missing AmazonSSMManagedInstanceCore — SSM agent will fail to authenticate"
    echo "    Fix: re-run ./runscripts/aws/vecoli_aws.sh head setup-ray-iam"
  fi
  if grep -qw "AmazonEC2ContainerRegistryReadOnly" <<<"$POLICIES"; then
    ok "AmazonEC2ContainerRegistryReadOnly present (Docker pull from ECR works)"
  else
    warn "missing AmazonEC2ContainerRegistryReadOnly — workers can't pull image from ECR"
  fi
else
  err "instance profile '$WORKER_PROFILE' does not exist"
  echo "    Fix: ./runscripts/aws/vecoli_aws.sh head setup-ray-iam (run from laptop)"
fi

echo
echo "=== 3. Cluster SG egress ==="
EGRESS=$(aws_cli ec2 describe-security-groups --group-ids "$SG_ID" \
  --query 'SecurityGroups[0].IpPermissionsEgress' --output json 2>/dev/null || echo "[]")
if grep -q '"-1"' <<<"$EGRESS"; then
  ok "SG $SG_ID allows all egress (SSM endpoints reachable on 443)"
else
  warn "SG $SG_ID has scoped egress: $EGRESS"
  echo "    SSM agent needs outbound 443 to *.amazonaws.com"
fi

echo
echo "=== 4. Recent SSM-registered instances in account ==="
COUNT=$(aws_cli ssm describe-instance-information \
  --query 'length(InstanceInformationList)' --output text 2>/dev/null || echo "DENIED")
echo "  Currently registered: $COUNT instance(s)"

echo
echo "=== Summary ==="
echo "If item 1 fails: this is the root cause. Flip the subnet flag and retry."
echo "If items 2-3 fail: re-run setup-ray-iam from laptop."
