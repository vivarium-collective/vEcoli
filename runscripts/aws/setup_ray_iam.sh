#!/usr/bin/env bash
# One-time IAM setup for the Ray-on-EC2 deploy mode.
#
# Two pieces are needed:
#
#   1. The HEAD node's instance profile (``ECR``) must be allowed to
#      launch/manage worker EC2 instances and drive them via SSM. That's
#      the policy attached here as ``VEcoliRayClusterMgr``.
#
#   2. The WORKER instances themselves need an instance profile that
#      lets the SSM agent register and lets Docker pull the vEcoli image
#      from ECR. Created here as ``ray-process-bigraph-node`` (the
#      default name EC2SSMRayCluster looks for).
#
# Run this ONCE from a machine with IAM admin privileges (your laptop
# with the stanford-sso profile, not the head node — the head's role
# can't grant itself perms). After this lands, ``vecoli_aws.sh run
# launch`` for the Ray variant will work.
#
# Idempotent: re-running checks for existing policies/roles/profiles
# and only updates / no-ops as needed.

set -euo pipefail

PROFILE="${AWS_PROFILE:-stanford-sso}"
REGION="${AWS_REGION:-us-gov-west-1}"

# Names — match what setup_head_node.sh and EC2SSMRayCluster expect.
HEAD_INSTANCE_PROFILE="${HEAD_INSTANCE_PROFILE:-ECR}"
HEAD_ROLE_NAME="${HEAD_ROLE_NAME:-ECR}"
HEAD_POLICY_NAME="${HEAD_POLICY_NAME:-VEcoliRayClusterMgr}"

WORKER_ROLE_NAME="${WORKER_ROLE_NAME:-ray-process-bigraph-node}"
WORKER_INSTANCE_PROFILE="${WORKER_INSTANCE_PROFILE:-ray-process-bigraph-node}"

aws_cli() { aws --profile "$PROFILE" --region "$REGION" "$@"; }
aws_iam() { aws --profile "$PROFILE" iam "$@"; }

ACCOUNT_ID=$(aws_cli sts get-caller-identity --query Account --output text)
PARTITION="aws-us-gov"
WORKER_ROLE_ARN="arn:${PARTITION}:iam::${ACCOUNT_ID}:role/${WORKER_ROLE_NAME}"

echo "Account: $ACCOUNT_ID  Region: $REGION  Partition: $PARTITION"

# --- 1. Worker role + instance profile -------------------------------------
# Trust policy: EC2 service can assume this role (so RunInstances can
# attach it to a worker as its instance-profile).
WORKER_TRUST=$(cat <<EOF
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {"Service": "ec2.amazonaws.com"},
    "Action": "sts:AssumeRole"
  }]
}
EOF
)

if ! aws_iam get-role --role-name "$WORKER_ROLE_NAME" >/dev/null 2>&1; then
  echo "Creating worker role $WORKER_ROLE_NAME"
  aws_iam create-role \
    --role-name "$WORKER_ROLE_NAME" \
    --assume-role-policy-document "$WORKER_TRUST" \
    --description "Ray worker instance profile for vEcoli composite_lineage_ray" \
    >/dev/null
else
  echo "Worker role $WORKER_ROLE_NAME exists"
fi

# AWS-managed policies — partition-aware ARN.
for managed in \
    "arn:${PARTITION}:iam::aws:policy/AmazonSSMManagedInstanceCore" \
    "arn:${PARTITION}:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"; do
  if aws_iam list-attached-role-policies --role-name "$WORKER_ROLE_NAME" \
        --query 'AttachedPolicies[].PolicyArn' --output text \
        | tr '\t' '\n' | grep -qx "$managed"; then
    echo "  ✓ $WORKER_ROLE_NAME already has $(basename "$managed")"
  else
    echo "  + attaching $(basename "$managed") to $WORKER_ROLE_NAME"
    aws_iam attach-role-policy --role-name "$WORKER_ROLE_NAME" --policy-arn "$managed"
  fi
done

if ! aws_iam get-instance-profile --instance-profile-name "$WORKER_INSTANCE_PROFILE" >/dev/null 2>&1; then
  echo "Creating worker instance profile $WORKER_INSTANCE_PROFILE"
  aws_iam create-instance-profile --instance-profile-name "$WORKER_INSTANCE_PROFILE" >/dev/null
  aws_iam add-role-to-instance-profile \
    --instance-profile-name "$WORKER_INSTANCE_PROFILE" \
    --role-name "$WORKER_ROLE_NAME"
  # IAM consistency: profile creation can lag a few seconds before EC2
  # can attach it. Don't sleep here — the next caller (cluster bring-up)
  # retries on RunInstances if needed.
else
  echo "Worker instance profile $WORKER_INSTANCE_PROFILE exists"
fi

# --- 2. Head policy: cluster management perms ------------------------------
# Scoped tightly: the head can launch/terminate/describe instances, send
# SSM commands, and pass the worker role — but only the worker role
# (PassRole resource is the worker arn). No iam:* outside PassRole.
HEAD_POLICY_DOC=$(cat <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ClusterEC2Management",
      "Effect": "Allow",
      "Action": [
        "ec2:RunInstances",
        "ec2:TerminateInstances",
        "ec2:DescribeInstances",
        "ec2:DescribeImages",
        "ec2:DescribeSubnets",
        "ec2:DescribeSecurityGroups",
        "ec2:DescribeKeyPairs",
        "ec2:DescribeNetworkInterfaces",
        "ec2:DescribeVolumes",
        "ec2:CreateTags",
        "ec2:AuthorizeSecurityGroupIngress",
        "ec2:RevokeSecurityGroupIngress"
      ],
      "Resource": "*"
    },
    {
      "Sid": "ClusterSSMControl",
      "Effect": "Allow",
      "Action": [
        "ssm:SendCommand",
        "ssm:GetCommandInvocation",
        "ssm:ListCommandInvocations",
        "ssm:DescribeInstanceInformation",
        "ssm:GetParameter",
        "ssm:GetParameters"
      ],
      "Resource": "*"
    },
    {
      "Sid": "PassWorkerRole",
      "Effect": "Allow",
      "Action": "iam:PassRole",
      "Resource": "${WORKER_ROLE_ARN}",
      "Condition": {
        "StringEquals": {"iam:PassedToService": "ec2.amazonaws.com"}
      }
    }
  ]
}
EOF
)

if aws_iam get-role-policy \
      --role-name "$HEAD_ROLE_NAME" \
      --policy-name "$HEAD_POLICY_NAME" >/dev/null 2>&1; then
  echo "Updating inline policy $HEAD_POLICY_NAME on $HEAD_ROLE_NAME"
else
  echo "Adding inline policy $HEAD_POLICY_NAME to $HEAD_ROLE_NAME"
fi
aws_iam put-role-policy \
  --role-name "$HEAD_ROLE_NAME" \
  --policy-name "$HEAD_POLICY_NAME" \
  --policy-document "$HEAD_POLICY_DOC"

# --- 3. Head role also needs SSM agent registration -----------------------
# EC2SSMRayCluster uses the SAME instance profile for both head and
# workers. The cluster head and workers all run the SSM agent and must
# register with SSM. The agent uses the instance role's perms for the
# initial DescribeInstanceInformation / UpdateInstanceInformation
# handshake — without AmazonSSMManagedInstanceCore the agent boots but
# never registers, and EC2SSMRayCluster.__enter__ fails with "SSM agent
# not online after 240s". Attach the AWS-managed policy to ECR (it
# already has S3 + ECR perms; this just adds SSM agent registration).
SSM_INSTANCE_CORE_ARN="arn:${PARTITION}:iam::aws:policy/AmazonSSMManagedInstanceCore"
if aws_iam list-attached-role-policies --role-name "$HEAD_ROLE_NAME" \
      --query 'AttachedPolicies[].PolicyArn' --output text \
      | tr '\t' '\n' | grep -qx "$SSM_INSTANCE_CORE_ARN"; then
  echo "  ✓ $HEAD_ROLE_NAME already has AmazonSSMManagedInstanceCore"
else
  echo "  + attaching AmazonSSMManagedInstanceCore to $HEAD_ROLE_NAME"
  aws_iam attach-role-policy --role-name "$HEAD_ROLE_NAME" \
    --policy-arn "$SSM_INSTANCE_CORE_ARN"
fi

cat <<EOF

IAM setup complete.

  Worker instance profile: $WORKER_INSTANCE_PROFILE  (used by Ray workers)
    AmazonSSMManagedInstanceCore + AmazonEC2ContainerRegistryReadOnly

  Head role: $HEAD_ROLE_NAME  (the head's existing instance profile)
    Inline policy '$HEAD_POLICY_NAME' grants:
      - ec2 manage (Run/Terminate/Describe/Tag/SG-ingress)
      - ssm SendCommand / GetCommandInvocation / GetParameter
      - iam:PassRole scoped to ${WORKER_ROLE_ARN}

Now you can:
  VECOLI_AWS_CONFIG=configs/comparison_10s_16g_v2_ray_aws.json \\
    SIM_DATA_S3_URI=s3://... \\
    ./runscripts/aws/vecoli_aws.sh run launch

EOF
