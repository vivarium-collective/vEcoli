"""Ray-actor process setup helpers.

Ray spawns actor processes that do NOT inherit driver env vars or
module-level monkey-patches. Each runscript that runs vEcoli code
inside a Ray actor has historically duplicated the same setup:

  * Set ``AWS_REGION`` / ``AWS_DEFAULT_REGION`` so boto3 / polars /
    fsspec don't default to us-east-1 (a HeadObject against a
    GovCloud bucket returns 400 Bad Request).
  * Pin numeric libraries to single-thread to avoid oversubscription
    on multi-actor nodes.
  * Monkey-patch ``s3fs.S3FileSystem._mkdir`` to skip ``CreateBucket``
    (the ECR/Batch IAM role doesn't have the permission and the
    bucket pre-exists anyway).

Call :py:func:`setup_ray_actor_process` once at the top of any code
that runs inside a Ray actor (entry to ``@ray.remote`` function, or
inside a process-bigraph type-provider). Idempotent — uses
``os.environ.setdefault`` so existing overrides survive.

See also:
  * ``runscripts/run_colony_ray.py`` — original implementation
    (kept inline so its module-level patch fires on driver import too).
  * ``ecoli/library/bigraph_types.py:load_sim_data_provider`` —
    consumer (calls this before LoadSimData touches fsspec).
"""
import os


def _patch_s3fs_skip_create_bucket() -> None:
    """Monkey-patch ``s3fs.S3FileSystem._mkdir`` to never call
    ``CreateBucket``. All buckets in this deployment pre-exist; this
    short-circuit avoids the GovCloud + aiobotocore quirk that
    otherwise fails the makedirs-on-S3 call inside ``open(..., "wb")``
    paths. Safe no-op when s3fs isn't installed."""
    try:
        import s3fs
    except ImportError:
        return

    async def _mkdir_no_create_bucket(self, path, acl=False,
                                       create_parents=True, **kwargs):
        return

    s3fs.S3FileSystem._mkdir = _mkdir_no_create_bucket


def setup_ray_actor_process(aws_region: str = 'us-gov-west-1') -> None:
    """One-stop setup for code running inside a Ray actor process.

    Args:
        aws_region: AWS region for boto3 / fsspec / polars. Default
            matches the GovCloud deployment. Pass an empty string to
            skip setting the env vars (e.g. when running against
            commercial AWS with credentials from a different chain).

    Idempotent: uses ``os.environ.setdefault`` for env vars, so an
    explicit prior override (e.g. user-set env at ``ray start`` time)
    survives.
    """
    if aws_region:
        os.environ.setdefault('AWS_REGION', aws_region)
        os.environ.setdefault('AWS_DEFAULT_REGION', aws_region)
    # Numerical-library thread pins. ``ray start`` propagates env on
    # most clusters but not all; defensive setdefault here means a
    # missing pin doesn't oversubscribe a 32-actor node.
    for k in ('OMP_NUM_THREADS', 'MKL_NUM_THREADS', 'OPENBLAS_NUM_THREADS',
              'NUMBA_NUM_THREADS', 'NUMEXPR_NUM_THREADS',
              'VECLIB_MAXIMUM_THREADS', 'POLARS_MAX_THREADS'):
        os.environ.setdefault(k, '1')
    try:
        from threadpoolctl import threadpool_limits
        threadpool_limits(limits=1)
    except ImportError:
        pass
    _patch_s3fs_skip_create_bucket()
