import os
import time
import tempfile
from wsgiref.util import FileWrapper

from botocore.exceptions import ClientError
import boto3
from mittab import settings


BACKUP_PREFIX = settings.BACKUPS["prefix"]
BUCKET_NAME   = settings.BACKUPS["bucket_name"]
S3_ENDPOINT   = settings.BACKUPS["s3_endpoint"]
SUFFIX        = ".dump.sql"

def with_backup_dir(func):
    def wrapper(*args, **kwargs):
        if not os.path.exists(BACKUP_PREFIX): os.makedirs(BACKUP_PREFIX)
        return func(*args, **kwargs)
    return wrapper

class LocalFilesystem:
    @with_backup_dir
    def keys(self):
        return [ name[:-len(SUFFIX)] for name in os.listdir(BACKUP_PREFIX) ]

    @with_backup_dir
    def __setitem__(self, key, content):
        dst_filename = os.path.join(BACKUP_PREFIX, key + SUFFIX)
        with open(dst_filename, "wb+") as destination:
            destination.write(content)

    def __getitem__(self, key):
        with open(self._get_backup_filename(key), "rb") as f:
            return f.read()

    def __contains__(self, key):
        return os.path.exists(self._get_backup_filename(key))

    def _get_backup_filename(self, key):
        if len(key) < len(SUFFIX) or not key.endswith(SUFFIX): key += SUFFIX
        return os.path.join(BACKUP_PREFIX, key)


class ObjectStorage:
    def __init__(self):
        if not BUCKET_NAME: raise ValueError('Need bucket name for S3 storage')
        if not BACKUP_PREFIX: raise ValueError('Need backup path for S3 storage')

        if S3_ENDPOINT is None:
            self.s3_client = boto3.client('s3')
        else:
            self.s3_client = boto3.client('s3', endpoint_url=S3_ENDPOINT)

    def keys(self):
        paginator = self.s3_client.get_paginator('list_objects_v2')
        to_return = []
        for page in paginator.paginate(Bucket=BUCKET_NAME, Prefix=BACKUP_PREFIX):
            keys = map(
                    lambda obj: obj['Key'][(len(BACKUP_PREFIX) + 1):-len(SUFFIX)],
                    page.get('Contents', []))
            to_return += list(keys)
        return to_return

    def __contains__(self, key):
        try:
            self.s3_client.head_object(Bucket=BUCKET_NAME, Key=self._object_path(key))
        except ClientError as e:
            return int(e.response['Error']['Code']) != 404
        return True

    def __setitem__(self, key, content):
        with tempfile.TemporaryFile(mode='w+b') as f:
            f.write(content)
            self.s3_client.upload_fileobj(f, BUCKET_NAME, self._object_path(key))

    def __getitem__(self, key):
        with tempfile.TemporaryFile(mode='w+b') as fp:
            try:
                self.s3_client.download_fileobj(
                        BUCKET_NAME,
                        self._object_path(key),
                        fp)
            except ClientError as e:
                if int(e.response['Error']['Code']) == 404:
                    return KeyError(key)
                else:
                    raise e
            return fp.read()

    def _object_path(self, key):
        return "%s/%s%s" % (BACKUP_PREFIX, key, SUFFIX)
