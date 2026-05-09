#!/usr/bin/env python3
"""ec2_cluster_ray.py — provision a Ray cluster on EC2 via SSM (NOT
``ray up``), run the composite_lineage_ray workflow, tear down.

Thin wrapper around
:py:class:`process_bigraph.protocols.clusters.ec2_ssm.EC2SSMRayCluster`
— same upstream machinery spatio-flux uses for the COMETS comparison.

Why SSM and not ``ray up``: see
``process_bigraph/protocols/clusters/ec2_ssm.py`` lines 1–54 for the
full rationale. Short version: Ray's autoscaler hardcodes assumptions
(rsync over ssh, IAM auto-create, bash --login -c -i shells) that
break on restricted-egress / minimum-AMI cloud environments like
GovCloud. The SSM-based path uses ``aws ec2 run-instances`` directly,
SSM Agent for the control plane, and ``docker run`` with a pre-baked
image — needs only ``ec2:RunInstances`` + ``ssm:SendCommand`` which
the existing ECR role already has via Batch.

Required env (set by bootstrap_head_ray.sh — if running this directly
from a workstation, set them yourself):

    IMAGE_URI        ECR worker image URI (vecoli:ray or similar; must
                     have ray[default] installed inside)
    OUT_URI          s3://... where parquet output goes
    SIM_DATA_URI     s3:// path to simData.cPickle
    CONFIG_RELPATH   path inside the container to the lineage config
                     (default: configs/comparison_10s_16g_v2_ray_aws.json)

Optional:
    N_WORKERS         default 5 (matches the cluster shape)
    N_SEEDS           default 10
    GENERATIONS       default 16
    MAX_DURATION      default 3000.0 (sim-seconds per generation)
    HEAD_INSTANCE_TYPE   default m5.2xlarge
    WORKER_INSTANCE_TYPE default m5.4xlarge
    IAM_INSTANCE_PROFILE default ECR (reuse v2-aws Batch profile)
    BAKED_AMI_ID         override default ECS-AL2 image
    KEEP_CLUSTER         1 to skip terminate at end (debugging)
    CLUSTER_ID           default "vecoli-ray-<unix-timestamp>"
    WORKER_SUBNET_ID     default subnet-08621613bcb558caa (SMS API VPC
                         private, NAT-routed). MUST be a subnet with
                         NAT egress so the SSM agent on each worker can
                         reach ssm.us-gov-west-1.amazonaws.com — public
                         subnets without auto-assign-public-IPv4 don't
                         work. The driver node (this script) stays in
                         whatever subnet it was launched in (public).
"""
from __future__ import annotations

import os
import sys
import time

# Try the canonical upstream import. If process-bigraph isn't fully
# installed (rare — bootstrap_head_ray.sh installs it via uv sync),
# error clearly rather than silently mis-import.
try:
    from process_bigraph.protocols.clusters.ec2_ssm import EC2SSMRayCluster
except ImportError as e:
    raise SystemExit(
        "EC2SSMRayCluster not found. "
        "Install with: uv pip install 'process-bigraph[ec2-ssm]' "
        "(or full process-bigraph in a venv). "
        f"Original: {e!r}"
    )


def main() -> int:
    image_uri = os.environ["IMAGE_URI"]
    out_uri = os.environ["OUT_URI"]
    sim_data_uri = os.environ["SIM_DATA_URI"]
    config_relpath = os.environ.get(
        "CONFIG_RELPATH", "configs/comparison_10s_16g_v2_ray_aws.json")

    n_workers = int(os.environ.get("N_WORKERS") or "5")
    n_seeds = int(os.environ.get("N_SEEDS") or "10")
    generations = int(os.environ.get("GENERATIONS") or "16")
    max_duration = float(os.environ.get("MAX_DURATION") or "3000.0")

    head_type = os.environ.get("HEAD_INSTANCE_TYPE") or "m5.2xlarge"
    worker_type = os.environ.get("WORKER_INSTANCE_TYPE") or "m5.4xlarge"
    iam_profile = os.environ.get("IAM_INSTANCE_PROFILE") or "ECR"
    baked_ami_id = os.environ.get("BAKED_AMI_ID") or None
    keep_cluster = bool(int(os.environ.get("KEEP_CLUSTER") or "0"))
    cluster_id = os.environ.get(
        "CLUSTER_ID") or f"vecoli-ray-{int(time.time())}"
    # GovCloud workers MUST land in a private subnet that has NAT egress
    # so the SSM agent can reach ssm.us-gov-west-1.amazonaws.com. The
    # head node is in a public subnet (so it can drive boto3/S3 with a
    # public IP) but workers are policy-forbidden from getting public
    # IPs. Default = the SMS API VPC's private subnet (same one
    # spatio-flux uses; both clusters share the VPC's NAT gateway).
    # Override via WORKER_SUBNET_ID env. EC2SSMRayCluster's
    # ``subnet_id`` controls workers AND head; if it's the private
    # subnet, head gets no public IP either — but it doesn't need one
    # since it talks to S3 via the same NAT (or via S3 VPC endpoint
    # if one exists).
    worker_subnet_id = (
        os.environ.get("WORKER_SUBNET_ID")
        or "subnet-08621613bcb558caa"  # SMS API VPC private, NAT-routed
    )

    print(f"→ image={image_uri}", flush=True)
    print(f"→ cluster_id={cluster_id}  workers={n_workers} "
          f"(head={head_type}, worker={worker_type})", flush=True)
    print(f"→ out_uri={out_uri}", flush=True)
    print(f"→ sim_data_uri={sim_data_uri}", flush=True)

    print(f"→ subnet={worker_subnet_id} (must have NAT egress to SSM)",
          flush=True)
    cluster = EC2SSMRayCluster(
        image_uri=image_uri,
        n_workers=n_workers,
        cluster_id=cluster_id,
        keep_cluster=keep_cluster,
        head_instance_type=head_type,
        worker_instance_type=worker_type,
        iam_profile=iam_profile,
        baked_ami_id=baked_ami_id,
        subnet_id=worker_subnet_id,
        # Container name is grep-able by ops scripts (cancel-current-run,
        # diagnose-current-run, etc). Keep distinct from spatio-flux's.
        container_name="vecoli_ray",
    )

    try:
        with cluster:
            log_path = "/tmp/lineage_ray.log"
            # Extract just the bucket name from the s3 URI for diag.
            bucket_for_diag = out_uri.split("/")[2] if out_uri.startswith(
                "s3://") else "(non-s3 out_uri)"
            command = (
                f"set +e; "
                f"cd /vEcoli; "
                f": > {log_path}; "
                f"export AWS_REGION={cluster.region} "
                f"AWS_DEFAULT_REGION={cluster.region}; "
                # === IAM diagnostic block ===
                # Run aws CLI bucket-level queries from inside the
                # cluster head's container (same instance role as
                # aiobotocore in the experiment) BEFORE the experiment
                # starts. Tells us at a glance whether the cluster head
                # has the bucket-level S3 perms granted by
                # setup_ray_iam.sh:VEcoliRayS3BucketRead, vs whether
                # aiobotocore is doing something different from CLI.
                f"echo '[diag] === IAM check on cluster head (role) ==='"
                f" >> {log_path}; "
                f"aws sts get-caller-identity 2>&1 | head -3 "
                f"  >> {log_path}; "
                f"echo '[diag] aws s3api head-bucket {bucket_for_diag}'"
                f" >> {log_path}; "
                f"aws --region {cluster.region} s3api head-bucket "
                f"  --bucket {bucket_for_diag} 2>&1 "
                f"  >> {log_path}; "
                f"echo \"[diag] head-bucket exit: $?\" >> {log_path}; "
                f"echo '[diag] aws s3api get-bucket-location'"
                f" >> {log_path}; "
                f"aws --region {cluster.region} s3api get-bucket-location "
                f"  --bucket {bucket_for_diag} 2>&1 "
                f"  >> {log_path}; "
                f"echo \"[diag] get-bucket-location exit: $?\" "
                f"  >> {log_path}; "
                f"echo '[diag] === end IAM check ===' >> {log_path}; "
                # === end diagnostic ===
                # POLARS_MAX_THREADS=1 to avoid oversubscription on the
                # multi-actor head — same setup MP runner uses.
                f"POLARS_MAX_THREADS=1 "
                f"python -u runscripts/run_composite_lineage_ray.py "
                f"  --config {config_relpath} "
                f"  --sim_data_path {sim_data_uri} "
                f"  --out_uri {out_uri} "
                f"  --n_seeds {n_seeds} "
                f"  --generations {generations} "
                f"  --max_duration {max_duration} "
                f"  --ray_address auto "
                f"  > {log_path} 2>&1; "
                f"rc=$?; "
                f'echo "=== experiment exit: $rc ===" >> {log_path}; '
                f"aws --region {cluster.region} s3 cp {log_path} "
                f"  {out_uri}/lineage_ray.log; "
                f"exit $rc"
            )
            print(f"→ running composite_lineage_ray "
                  f"({n_seeds} seeds × {generations} gens)", flush=True)
            cluster.exec_blocking_on_head(
                command,
                log_path=log_path,
                # 16 gens × ~10 min/gen + setup buffer = ~3hr ceiling.
                # Override via TIMEOUT_S env if needed.
                timeout_s=int(os.environ.get("TIMEOUT_S") or "10800"),
                poll_interval=30.0,
            )
        print("✅ composite_lineage_ray complete", flush=True)
        return 0
    except Exception:
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
