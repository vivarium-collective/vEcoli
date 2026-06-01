#!/usr/bin/env python3
"""ec2_cluster_ray_colony.py — provision a Ray cluster on EC2 via SSM (NOT
``ray up``), run the colony_ray workflow, tear down.

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


def _read_sim_knobs_from_config(config_relpath):
    """Walk the inherited-config chain and extract (n_seeds,
    generations, max_duration) from the merged config.

    Mirrors EcoliSim.from_file's load semantics: ``default.json`` is
    the implicit base, then each ``inherit_from`` parent is layered,
    then the child config wins. Standalone (no vEcoli import) since
    this script runs on the head node's driver venv.
    """
    import json
    seen = set()

    def _resolve(path):
        path = os.path.abspath(path)
        if path in seen:
            return {}
        seen.add(path)
        with open(path) as f:
            cfg = json.load(f)
        merged = {}
        cfg_dir = os.path.dirname(path)
        # Implicit default.json base
        if os.path.basename(path) != 'default.json':
            for cand in (
                    os.path.join(cfg_dir, 'default.json'),
                    os.path.join(cfg_dir, '..', 'configs', 'default.json')):
                if os.path.isfile(cand):
                    merged.update(_resolve(cand))
                    break
        # Explicit inherit_from
        for parent_rel in cfg.get('inherit_from') or []:
            for cand in (
                    os.path.join(cfg_dir, parent_rel),
                    os.path.join(cfg_dir, '..', 'configs', parent_rel),
                    os.path.join(cfg_dir, parent_rel + '.json')):
                if os.path.isfile(cand):
                    merged.update(_resolve(cand))
                    break
        merged.update(cfg)
        return merged

    cfg = _resolve(config_relpath)
    # Colony config schema differs from lineage:
    #   - ``target_doublings`` instead of ``generations``
    #   - ``n_init_sims`` still meaningful (M parallel colonies, one
    #     per actor — same as M lineages — though the current driver
    #     runs only 1 actor).
    n_seeds = int(cfg.get('n_init_sims') or 1)
    target_doublings = int(cfg.get('target_doublings') or 1)
    max_duration = float(cfg.get('max_duration') or 10800.0)
    return n_seeds, target_doublings, max_duration


def _read_aws_ray_int(config_relpath, key):
    """Read ``aws.ray.<key>`` from the merged config. Returns the
    string value (so it can feed ``int()`` directly), or ``None``
    when absent. Used for cluster-topology knobs like ``max_workers``,
    ``head_instance_type``, etc. — config is authoritative; env vars
    only override when set."""
    import json
    seen = set()

    def _resolve(path):
        path = os.path.abspath(path)
        if path in seen:
            return {}
        seen.add(path)
        with open(path) as f:
            cfg = json.load(f)
        merged = {}
        cfg_dir = os.path.dirname(path)
        if os.path.basename(path) != 'default.json':
            for cand in (
                    os.path.join(cfg_dir, 'default.json'),
                    os.path.join(cfg_dir, '..', 'configs', 'default.json')):
                if os.path.isfile(cand):
                    merged.update(_resolve(cand))
                    break
        for parent_rel in cfg.get('inherit_from') or []:
            for cand in (
                    os.path.join(cfg_dir, parent_rel),
                    os.path.join(cfg_dir, '..', 'configs', parent_rel),
                    os.path.join(cfg_dir, parent_rel + '.json')):
                if os.path.isfile(cand):
                    merged.update(_resolve(cand))
                    break
        merged.update(cfg)
        return merged

    cfg = _resolve(config_relpath)
    aws = cfg.get('aws') or {}
    ray_cfg = aws.get('ray') or {}
    val = ray_cfg.get(key)
    return None if val is None else str(val)


def main() -> int:
    image_uri = os.environ["IMAGE_URI"]
    out_uri = os.environ["OUT_URI"]
    sim_data_uri = os.environ["SIM_DATA_URI"]
    config_relpath = os.environ.get(
        "CONFIG_RELPATH", "configs/comparison_10s_16g_v2_ray_aws.json")
    # CLI-supplied experiment_id (vecoli_aws.sh assigns one per
    # ``run launch ray`` and persists it in a sidecar). Forwarded as
    # ``--experiment-id`` to run_colony_cac_ray.py so all S3
    # output / synthetic_trace land under the unique ID.
    experiment_id = os.environ.get("EXPERIMENT_ID", "")

    # Per-run sim settings (n_seeds, target_doublings, max_duration) come
    # from the config — we only need them locally for log messages
    # and (n_seeds) as the n_workers fallback. Walk the inherited-
    # config chain so values can live in any parent.
    n_seeds, target_doublings, max_duration = _read_sim_knobs_from_config(
        config_relpath)
    # Cluster sizing: prefer config's ``aws.ray.max_workers`` (the
    # config is the source of truth for the cluster topology). Env
    # ``N_WORKERS`` overrides for ad-hoc swaps. Final fallback is
    # n_seeds (one worker per lineage actor).
    # Colony architecture (option a): ONE Ray actor per colony, with
    # cells growing 1 → 2^target_doublings INSIDE that actor. The
    # actor itself needs enough vCPU + RAM for the final cell count
    # (each cell costs ~1 vCPU and ~500 MB at peak). Default
    # n_workers = n_seeds (= number of independent parallel colonies);
    # WORKER_INSTANCE_TYPE auto-picks based on target_doublings unless
    # explicitly overridden via env/config.
    n_workers = int(
        os.environ.get("N_WORKERS")
        or _read_aws_ray_int(config_relpath, "max_workers")
        or str(n_seeds))

    # Auto-sizing table for the per-actor instance based on the
    # expected final cell count (2^target_doublings). Each cell needs
    # ~1 vCPU and ~500 MB RAM at peak. Pick the smallest c7i.* that
    # fits; fall back to m7g for very large targets where memory
    # dominates. Override via WORKER_INSTANCE_TYPE / config aws.ray.
    # worker_instance_type if you need spot capacity / different family.
    def _autopick_worker_for_target(td):
        final_n = 2 ** td
        if final_n <= 2:   return "c7i.xlarge"     # 4 vCPU, 8 GB
        if final_n <= 8:   return "c7i.2xlarge"    # 8 vCPU, 16 GB
        if final_n <= 32:  return "c7i.8xlarge"    # 32 vCPU, 64 GB
        if final_n <= 128: return "c7i.24xlarge"   # 96 vCPU, 192 GB
        # 256+ cells: option (a) breaks down — switch to option (b)
        # (cells-as-actors). For now warn + return largest c7i.
        print(f"⚠️  target_doublings={td} → {final_n} cells will not "
              f"fit comfortably in a single Ray actor (option-a "
              f"architecture). Consider the cells-as-actors design.",
              flush=True)
        return "c7i.48xlarge"  # 192 vCPU, 384 GB

    head_type = (os.environ.get("HEAD_INSTANCE_TYPE")
                 or _read_aws_ray_int(config_relpath, "head_instance_type")
                 or "m5.2xlarge")
    worker_type = (os.environ.get("WORKER_INSTANCE_TYPE")
                   or _read_aws_ray_int(config_relpath, "worker_instance_type")
                   or _autopick_worker_for_target(target_doublings))
    iam_profile = os.environ.get("IAM_INSTANCE_PROFILE") or "ECR"
    baked_ami_id = os.environ.get("BAKED_AMI_ID") or None
    # process-bigraph 1.4.8 (PyPI, what's installed by vEcoli's frozen
    # uv.lock) defaults to the x86 ECS-optimized AMI even when launching
    # Graviton instances → InvalidParameterValue. Detect arch here from
    # the worker instance type and pass an ARM AMI explicitly when
    # needed. Upstream fix exists locally in the sibling but isn't
    # installed; this in-vEcoli workaround unblocks until v1.4.9+ ships.
    if baked_ami_id is None:
        def _instance_arch(it: str) -> str:
            family = it.split(".", 1)[0]
            i = 0
            while i < len(family) and family[i].isalpha():
                i += 1
            while i < len(family) and family[i].isdigit():
                i += 1
            if i < len(family) and family[i] == "g":
                return "arm64"
            if family == "a1":
                return "arm64"
            return "x86_64"
        worker_arch = _instance_arch(worker_type)
        head_arch = _instance_arch(head_type)
        if worker_arch != head_arch:
            raise SystemExit(
                f"Mixed-arch cluster (head={head_type}/{head_arch} vs "
                f"worker={worker_type}/{worker_arch}) requires explicit "
                f"BAKED_AMI_ID env.")
        if worker_arch == "arm64":
            import boto3
            ssm = boto3.client("ssm", region_name="us-gov-west-1")
            baked_ami_id = ssm.get_parameter(
                Name="/aws/service/ecs/optimized-ami/amazon-linux-2/arm64/recommended/image_id"
            )["Parameter"]["Value"]
            print(f"→ arch=arm64 auto-resolved baked_ami_id={baked_ami_id}",
                  flush=True)
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
                # ``run_colony_cac_ray.py`` is the cell-as-Composite
                # pipeline (mother + daughters as ray:Composite /
                # ray:EcoliCellComposite, sharing one actor pool per
                # worker node via load_sim_data_provider). Replaced
                # ``run_colony_ray.py`` (legacy _divide sentinel,
                # single actor for whole colony).
                f"POLARS_MAX_THREADS=1 "
                f"python -u runscripts/run_colony_cac_ray.py "
                f"  --config {config_relpath} "
                f"  --ray_address auto "
                f"""{f"  --experiment-id {experiment_id} " if experiment_id else ""}"""
                f"  > {log_path} 2>&1; "
                f"rc=$?; "
                f'echo "=== experiment exit: $rc ===" >> {log_path}; '
                f"aws --region {cluster.region} s3 cp {log_path} "
                f"  {out_uri}/lineage_ray.log; "
                f"exit $rc"
            )
            print(f"→ running colony_ray "
                  f"({n_seeds} colonies × {target_doublings} doublings "
                  f"= {2 ** target_doublings} final cells/colony)", flush=True)
            cluster.exec_blocking_on_head(
                command,
                log_path=log_path,
                # 16 gens × ~10 min/gen + setup buffer = ~3hr ceiling.
                # Override via TIMEOUT_S env if needed.
                timeout_s=int(os.environ.get("TIMEOUT_S") or "10800"),
                poll_interval=30.0,
            )
        print("✅ colony_ray complete", flush=True)
        return 0
    except Exception:
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
