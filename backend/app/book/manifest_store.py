from __future__ import annotations

import json
from typing import Optional

import boto3

from ..config import settings
from ..exceptions import S3StorageError
from .manifest import BookManifest


_s3 = boto3.client(
    "s3",
    aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
    aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
    region_name=settings.AWS_REGION_NAME,
    endpoint_url=settings.AWS_ENDPOINT_URL,
)


def manifest_s3_key(slug: str) -> str:
    return f"templates/{slug}/manifest.json"


def load_manifest(slug: str) -> BookManifest:
    """
    Load and validate book manifest from S3.

    Expected location:
      s3://{S3_BUCKET_NAME}/templates/{slug}/manifest.json
    """
    key = manifest_s3_key(slug)
    try:
        obj = _s3.get_object(Bucket=settings.S3_BUCKET_NAME, Key=key)
        raw = obj["Body"].read()
        data = json.loads(raw.decode("utf-8"))
    except Exception as e:
        raise S3StorageError(f"Failed to load manifest from S3 key '{key}': {e}")

    # Allow manifest to omit slug; enforce it from the request context.
    if not isinstance(data, dict):
        raise S3StorageError(f"Invalid manifest JSON at '{key}': expected object")
    data.setdefault("slug", slug)

    try:
        return BookManifest.parse_obj(data)
    except Exception as e:
        raise S3StorageError(f"Manifest validation failed for '{key}': {e}")


