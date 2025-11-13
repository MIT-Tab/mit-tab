import os
import tempfile

from botocore.exceptions import ClientError
import boto3
from mittab import settings


BACKUP_PREFIX = settings.BACKUPS["prefix"]
BUCKET_NAME = settings.BACKUPS["bucket_name"]
S3_ENDPOINT = settings.BACKUPS["s3_endpoint"]
SUFFIX = ".dump.sql"


class LocalFilesystem:
    def __init__(self, prefix=BACKUP_PREFIX, suffix=SUFFIX):
        self.prefix = prefix
        self.suffix = suffix

    def _ensure_dir(self):
        if not os.path.exists(self.prefix):
            os.makedirs(self.prefix)

    def keys(self):
        self._ensure_dir()
        return [
            name[:-len(self.suffix)]
            for name in os.listdir(self.prefix)
            if name.endswith(self.suffix)
        ]

    def __setitem__(self, key, content):
        self._ensure_dir()
        dst_filename = self._get_backup_filename(key)
        with open(dst_filename, "wb+") as destination:
            destination.write(content)

    def __getitem__(self, key):
        self._ensure_dir()
        with open(self._get_backup_filename(key), "rb") as f:
            return f.read()

    def __contains__(self, key):
        return os.path.exists(self._get_backup_filename(key))

    def _get_backup_filename(self, key):
        if len(key) < len(self.suffix) or not key.endswith(self.suffix):
            key += self.suffix
        return os.path.join(self.prefix, key)


class ObjectStorage:
    def __init__(self, prefix=BACKUP_PREFIX, suffix=SUFFIX):
        if not BUCKET_NAME:
            raise ValueError("Need bucket name for S3 storage")
        if not BACKUP_PREFIX:
            raise ValueError("Need backup path for S3 storage")

        self.prefix = prefix
        self.suffix = suffix

        if S3_ENDPOINT is None:
            self.s3_client = boto3.client("s3")
        else:
            self.s3_client = boto3.client("s3", endpoint_url=S3_ENDPOINT)

    def keys(self):
        paginator = self.s3_client.get_paginator("list_objects_v2")
        to_return = []
        for page in paginator.paginate(Bucket=BUCKET_NAME, Prefix=self.prefix):
            keys = map(
                lambda obj: obj["Key"][(len(self.prefix) + 1):-len(self.suffix)],
                page.get("Contents", []))
            to_return += list(keys)
        return to_return

    def __contains__(self, key):
        try:
            self.s3_client.head_object(Bucket=BUCKET_NAME, Key=self._object_path(key))
        except ClientError as e:
            return int(e.response["Error"]["Code"]) != 404
        return True

    def __setitem__(self, key, content):
        with tempfile.TemporaryFile(mode="w+b") as f:
            f.write(content)
            f.seek(0)
            self.s3_client.upload_fileobj(f, BUCKET_NAME, self._object_path(key))

    def __getitem__(self, key):
        with tempfile.TemporaryFile(mode="w+b") as f:
            try:
                self.s3_client.download_fileobj(
                    BUCKET_NAME,
                    self._object_path(key),
                    f)
            except ClientError as e:
                if int(e.response["Error"]["Code"]) == 404:
                    return KeyError(key)
                else:
                    raise e
            f.seek(0)
            return f.read()

    def _object_path(self, key):
        return f"{self.prefix}/{key}{self.suffix}"
