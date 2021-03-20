import os
import time
import tempfile
from wsgiref.util import FileWrapper

import boto3

from mittab import settings


BACKUP_PREFIX = settings.BACKUPS["prefix"]
BUCKET_NAME   = settings.BACKUPS["bucket_name"]
SUFFIX        = ".dump.sql"

def with_backup_dir(func):
    def wrapper(*args, **kwargs):
        if not os.path.exists(BACKUP_PREFIX): os.makedirs(BACKUP_PREFIX)
        func(*args, **kwargs)
    return wrapper

class LocalFilesystem:
    @with_backup_dir
    def all(self):
        return map(lambda name: name[:-len(SUFFIX)], os.listdir(BACKUP_PREFIX))

    @with_backup_dir
    def store_fileobj(self, key, f):
        dst_filename = os.path.join(BACKUP_PREFIX, key + SUFFIX)
        with open(dst_filename, "wb+") as destination:
            for chunk in f.chunks():
                destination.write(chunk)

    def get_fileobj(self, key):
        return open(self._get_backup_filename(key), "rb")

    def exists(self, key):
        return os.path.exists(self._get_backup_filename(key))

    def _get_backup_filename(self, key):
        if len(key) < len(SUFFIX) or not key.endswith(SUFFIX): key += SUFFIX
        return os.path.join(BACKUP_PREFIX, key)


class ObjectStorage:
    def __init__(self):
        if not BUCKET_NAME: raise ValueError('Need bucket name for S3 storage')
        if not BACKUP_PREFIX: raise ValueError('Need backup path for S3 storage')

        self.s3_client = boto3.client('s3')

    def all(self):
        paginator = self.s3_client.get_paginator('list_objects_v2')
        to_return = []
        for page in paginator.paginate(Bucket=BUCKET_NAME, Prefix=BACKUP_PREFIX):
            keys = map(
                    lambda obj: obj['Key'][len(BACKUP_PREFIX):-len(SUFFIX)],
                    page['Contents'])
            to_return += list(keys)
        return to_return

    def store_fileobj(self, key, f):
        self.s3_client.upload_fileobj(f, BUCKET_NAME, self._object_path(key))

    def get_fileobj(self, key):
        return self.s3_client.download_fileobj(BUCKET_NAME, self._object_path(key))

    def exists(self, key):
        try:
            self.s3_client.head_object(Bucket=BUCKET_NAME, Key=self._object_path(key))
        except ClientError as e:
            return int(e.response['Error']['Code']) != 404
        return True

    def _object_path(self, key):
        return "%s/%s%s" % (BACKUP_PREFIX, key, SUFFIX)
