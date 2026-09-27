"""ObjectStorage抽象化（S3/GCS/Azure Blob/インメモリの差し替え対応）。"""

from core_toolkit.object_storage.base import (
    BucketNotFoundError,
    MultipartUpload,
    NotSupportedError,
    ObjectInfo,
    ObjectNotFoundError,
    ObjectStorage,
    ObjectStorageError,
    PermissionDeniedError,
    UnknownBucketError,
)

__all__ = [
    "BucketNotFoundError",
    "MultipartUpload",
    "NotSupportedError",
    "ObjectInfo",
    "ObjectNotFoundError",
    "ObjectStorage",
    "ObjectStorageError",
    "PermissionDeniedError",
    "UnknownBucketError",
]
