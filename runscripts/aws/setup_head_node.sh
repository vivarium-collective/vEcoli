#!/usr/bin/env bash
# Provision an EC2 head node in Stanford GovCloud for the v2 vEcoli workflow.
# Mirrors the conditions of the v1 atlantis run (sim35-comparison_test_6-4b7b)
# so v1 and v2 outputs land in the same S3 bucket for direct comparison.
#
# Performs all AWS write ops needed before the workflow can run:
#   - create EC2 key pair (saved to ~/.ssh/<KEY_NAME>.pem) if missing
#   - create security group with SSH ingress from this machine's public IP
#   - launch a t4g.medium head node into the public subnet of the v1 VPC
#     with the existing ECR instance profile attached
#
# Targets the 'vecoli-arm' Batch queue (SLR-based, VALID). The original
# smsvpctest-vecoli-task-arm64 queue (which atlantis used for the v1 run)
# is currently INVALID due to a launch-template UserData format issue and
# can't be edited because its CE pre-dates the Batch service-linked role.
# That's a separate cleanup — vecoli-arm uses the same Graviton fleet, has
# 2000 vCPU headroom, and produces equivalent simulation outputs.

set -euo pipefail

PROFILE="${AWS_PROFILE:-stanford-sso}"
REGION="${AWS_REGION:-us-gov-west-1}"

# Discovered from the v1 atlantis run's compute environment.
VPC_ID="vpc-013f0c1012b271b06"
PUBLIC_SUBNET_ID="subnet-06cc68d397fb72760"   # us-gov-west-1a, public
INSTANCE_PROFILE_NAME="ECR"

# Defaults: t4g.large (8 GB RAM) for v1/v2-aws Nextflow workflows. The
# MP-single-node variant overrides HEAD_INSTANCE_TYPE=c7g.metal (64
# vCPU bare-metal) via env. ROOT_VOL_GIB scales with HEAD_INSTANCE_TYPE.
HEAD_INSTANCE_TYPE="${HEAD_INSTANCE_TYPE:-t4g.large}"
HEAD_NAME="${HEAD_NAME:-vecoli-v2-head}"
KEY_NAME="${KEY_NAME:-vecoli-head-key}"
KEY_FILE="${HOME}/.ssh/${KEY_NAME}.pem"
SG_NAME="${SG_NAME:-vecoli-head-ssh-sg}"
ROOT_VOL_GIB="${ROOT_VOL_GIB:-30}"

aws_cli() { aws --profile "$PROFILE" --region "$REGION" "$@"; }

echo "Caller identity:"
aws_cli sts get-caller-identity --query '{Account:Account,Arn:Arn}' --output table

USER_IP=$(curl -s -4 ifconfig.me)
[[ -z "$USER_IP" ]] && { echo "could not detect public IPv4"; exit 1; }
echo "Public IP for SSH ingress: ${USER_IP}/32"

# Pick AMI arch from instance type: Graviton families end in `g` (c7g,
# m7g, r7g, t4g), plus legacy `a1`. Everything else (Intel/AMD x86) is
# x86_64.
_family="${HEAD_INSTANCE_TYPE%%.*}"
_letters="${_family//[0-9]/}"
if [[ "${_letters: -1}" == "g" || "$_family" == "a1" ]]; then
  AMI_ARCH="arm64"
else
  AMI_ARCH="x86_64"
fi
AMI_ID=$(aws_cli ec2 describe-images \
  --owners amazon \
  --filters \
    "Name=name,Values=al2023-ami-*-${AMI_ARCH}" \
    "Name=state,Values=available" \
  --query 'sort_by(Images, &CreationDate)[-1].ImageId' \
  --output text)
echo "AL2023 ${AMI_ARCH} AMI: ${AMI_ID}"

# --- Key pair ---------------------------------------------------------------
mkdir -p "${HOME}/.ssh"
if [[ ! -f "${KEY_FILE}" ]]; then
  if aws_cli ec2 describe-key-pairs --key-names "${KEY_NAME}" >/dev/null 2>&1; then
    echo "Key pair '${KEY_NAME}' exists in AWS but ${KEY_FILE} is missing locally."
    echo "Either delete the AWS key pair or restore the .pem and rerun."
    exit 1
  fi
  echo "Creating key pair ${KEY_NAME} -> ${KEY_FILE}"
  aws_cli ec2 create-key-pair \
    --key-name "${KEY_NAME}" \
    --query 'KeyMaterial' --output text > "${KEY_FILE}"
  chmod 400 "${KEY_FILE}"
else
  echo "Reusing existing key file ${KEY_FILE}"
fi

# --- Security group ---------------------------------------------------------
SG_ID=$(aws_cli ec2 describe-security-groups \
  --filters "Name=vpc-id,Values=${VPC_ID}" "Name=group-name,Values=${SG_NAME}" \
  --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null || true)
if [[ -z "${SG_ID}" || "${SG_ID}" == "None" ]]; then
  SG_ID=$(aws_cli ec2 create-security-group \
    --group-name "${SG_NAME}" \
    --description "SSH ingress for vEcoli head node" \
    --vpc-id "${VPC_ID}" \
    --query 'GroupId' --output text)
  echo "Created SG ${SG_ID}"
else
  echo "Reusing SG ${SG_ID}"
fi

# Idempotent SSH ingress add — ignore "rule already exists"
aws_cli ec2 authorize-security-group-ingress \
  --group-id "${SG_ID}" \
  --protocol tcp --port 22 --cidr "${USER_IP}/32" 2>/dev/null \
  && echo "Authorized SSH from ${USER_IP}/32" \
  || echo "SSH ingress from ${USER_IP}/32 already present"

# --- Launch instance --------------------------------------------------------
INSTANCE_ID=$(aws_cli ec2 run-instances \
  --image-id "${AMI_ID}" \
  --instance-type "${HEAD_INSTANCE_TYPE}" \
  --key-name "${KEY_NAME}" \
  --subnet-id "${PUBLIC_SUBNET_ID}" \
  --security-group-ids "${SG_ID}" \
  --iam-instance-profile "Name=${INSTANCE_PROFILE_NAME}" \
  --associate-public-ip-address \
  --block-device-mappings "DeviceName=/dev/xvda,Ebs={VolumeSize=${ROOT_VOL_GIB},VolumeType=gp3}" \
  --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=${HEAD_NAME}}]" \
  --query 'Instances[0].InstanceId' --output text)
echo "Launched ${INSTANCE_ID}; waiting for running state..."

aws_cli ec2 wait instance-running --instance-ids "${INSTANCE_ID}"

PUB_DNS=$(aws_cli ec2 describe-instances --instance-ids "${INSTANCE_ID}" \
  --query 'Reservations[0].Instances[0].PublicDnsName' --output text)
PUB_IP=$(aws_cli ec2 describe-instances --instance-ids "${INSTANCE_ID}" \
  --query 'Reservations[0].Instances[0].PublicIpAddress' --output text)

cat <<EOF

Head node ready.
  Instance:   ${INSTANCE_ID}
  Public IP:  ${PUB_IP}
  Public DNS: ${PUB_DNS}

Next step (copy bootstrap, then SSH and run it):

  scp -i ${KEY_FILE} runscripts/aws/bootstrap_head.sh ec2-user@${PUB_DNS}:~/
  ssh -i ${KEY_FILE} ec2-user@${PUB_DNS} 'bash ~/bootstrap_head.sh'

To stop (saves money, keeps EBS):
  aws --profile ${PROFILE} --region ${REGION} ec2 stop-instances --instance-ids ${INSTANCE_ID}
To terminate (destroys EBS):
  aws --profile ${PROFILE} --region ${REGION} ec2 terminate-instances --instance-ids ${INSTANCE_ID}

EOF
