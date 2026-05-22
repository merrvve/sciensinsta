"""
r2_service.py – Cloudflare R2 upload and presigned-URL helpers.

Cloudflare R2 is S3-compatible; we use boto3 with a custom endpoint.

Required environment variables
───────────────────────────────
  R2_ENDPOINT_URL        https://<account_id>.r2.cloudflarestorage.com
  R2_ACCESS_KEY_ID       R2 API token (Access Key ID)
  R2_SECRET_ACCESS_KEY   R2 API token (Secret Access Key)

Optional environment variables
───────────────────────────────
  R2_BUCKET_NAME         bucket name            (default: sciensinsta)
  R2_FOLDER              key prefix             (default: user-docs)
  R2_PRESIGNED_TTL       presigned URL TTL (s)  (default: 3600)
"""

from __future__ import annotations

import os

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError


# ── Internal helpers ──────────────────────────────────────────────────────────

def _cfg(key: str, default: str = "") -> str:
    """Read an env var at call time so load_dotenv() order doesn't matter."""
    return os.getenv(key, default)


def _client():
    """Return a fresh boto3 S3 client pointed at Cloudflare R2."""
    return boto3.client(
        "s3",
        endpoint_url=_cfg("R2_ENDPOINT_URL"),
        aws_access_key_id=_cfg("R2_ACCESS_KEY_ID"),
        aws_secret_access_key=_cfg("R2_SECRET_ACCESS_KEY"),
        config=Config(signature_version="s3v4"),
        region_name="auto",
    )


def _bucket() -> str:
    return _cfg("R2_BUCKET_NAME", "sciensinsta")


def _object_key(filename: str) -> str:
    """Build the full R2 object key: ``user-docs/<filename>``."""
    folder = _cfg("R2_FOLDER", "user-docs")
    return f"{folder}/{filename}"


def _ttl() -> int:
    return int(_cfg("R2_PRESIGNED_TTL", "3600"))


# ── Public API ────────────────────────────────────────────────────────────────

def upload_bytes(
    data: bytes,
    filename: str,
    content_type: str = "application/octet-stream",
) -> str:
    """
    Upload *data* to R2 under ``user-docs/<filename>`` and return the
    object key.

    Parameters
    ----------
    data         : raw file bytes
    filename     : bare filename, e.g. ``abc123.pptx`` or ``abc123.xlsx``
    content_type : MIME type stored alongside the object

    Returns
    -------
    str  The R2 object key (``user-docs/<filename>``).
    """
    key = _object_key(filename)
    _client().put_object(
        Bucket=_bucket(),
        Key=key,
        Body=data,
        ContentType=content_type,
    )
    return key


def object_exists(filename: str) -> bool:
    """Return True if ``user-docs/<filename>`` exists in the bucket."""
    try:
        _client().head_object(Bucket=_bucket(), Key=_object_key(filename))
        return True
    except ClientError as exc:
        if exc.response["Error"]["Code"] in ("404", "NoSuchKey"):
            return False
        raise


def presigned_download_url(filename: str) -> str:
    """
    Generate and return a time-limited presigned GET URL for
    ``user-docs/<filename>``.

    The URL expires after ``R2_PRESIGNED_TTL`` seconds (default 1 h).
    The object is *not* deleted — use R2 lifecycle rules or a cleanup
    task to remove old files.

    Raises
    ------
    FileNotFoundError  if the object does not exist in R2.
    """
    if not object_exists(filename):
        raise FileNotFoundError(f"R2 object not found: user-docs/{filename}")

    return _client().generate_presigned_url(
        "get_object",
        Params={"Bucket": _bucket(), "Key": _object_key(filename)},
        ExpiresIn=_ttl(),
    )
