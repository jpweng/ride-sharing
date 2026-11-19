"""
Where profile photos are kept (profiles.py): an Amazon S3 bucket, private.
Photos never have a public address: the app reads them back and serves
them to those who may see the profile.

Switched on by its setting, off without it:
  - PHOTO_BUCKET: the bucket's name (infra/photos.tf creates it and lets the
    server's role read, write and delete its objects; PHOTO_REGION, default
    the SES Region's default, eu-north-1).
  - PHOTO_DIR: a folder instead, for development (ignored when PHOTO_BUCKET
    is set).
  - Neither: no photos; the profile page doesn't offer to add one.

A photo is a JPEG the browser has already cut square and shrunk (so it
carries no location or camera data); here only its size and signature are
checked.
"""
import logging
import os
import re
from pathlib import Path

logger = logging.getLogger("uvicorn.error")

MAX_BYTES = 400_000
DEFAULT_REGION = "eu-north-1"
_KEY = re.compile(r"^profiles/\d+-[0-9a-f]{16}\.jpg$")


class PhotoError(Exception):
    """The photo couldn't be stored or read (the storage failed or isn't set up)."""


def is_jpeg(data: bytes) -> bool:
    return len(data) > 4 and data[:3] == b"\xff\xd8\xff" and data[-2:] == b"\xff\xd9"


class S3Storage:
    def __init__(self, bucket: str, region: str):
        import boto3  # only needed in production

        self.bucket = bucket
        self.client = boto3.client("s3", region_name=region)

    def put(self, key: str, data: bytes) -> None:
        self._call("put_object", Bucket=self.bucket, Key=key, Body=data, ContentType="image/jpeg")

    def get(self, key: str) -> bytes | None:
        from botocore.exceptions import ClientError

        try:
            return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
                return None
            raise PhotoError(str(e)) from e
        except Exception as e:  # botocore's own errors, the network
            raise PhotoError(str(e)) from e

    def delete(self, key: str) -> None:
        self._call("delete_object", Bucket=self.bucket, Key=key)

    def _call(self, operation: str, **params) -> None:
        try:
            getattr(self.client, operation)(**params)
        except Exception as e:
            raise PhotoError(str(e)) from e


class DirStorage:
    """A folder, for development."""

    def __init__(self, root: str):
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        return self.root / key

    def put(self, key: str, data: bytes) -> None:
        self._path(key).parent.mkdir(parents=True, exist_ok=True)
        self._path(key).write_bytes(data)

    def get(self, key: str) -> bytes | None:
        return self._path(key).read_bytes() if self._path(key).is_file() else None

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)


_storage = None
_storage_for = None


def storage():
    """The configured storage, or None when photos are switched off."""
    global _storage, _storage_for
    bucket = (os.environ.get("PHOTO_BUCKET") or "").strip()
    folder = (os.environ.get("PHOTO_DIR") or "").strip()
    wanted = ("s3", bucket) if bucket else ("dir", folder) if folder else None
    if wanted != _storage_for:
        _storage_for = wanted
        if wanted is None:
            _storage = None
        elif wanted[0] == "s3":
            _storage = S3Storage(bucket, (os.environ.get("PHOTO_REGION") or DEFAULT_REGION).strip())
        else:
            _storage = DirStorage(folder)
    return _storage


def enabled() -> bool:
    return storage() is not None


def valid_key(key: str | None) -> bool:
    return bool(key and _KEY.match(key))
