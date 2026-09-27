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
from core_toolkit.object_storage.memory import InMemoryObjectStorage

__all__ = [
    "BucketNotFoundError",
    "InMemoryObjectStorage",
    "MultipartUpload",
    "NotSupportedError",
    "ObjectInfo",
    "ObjectNotFoundError",
    "ObjectStorage",
    "ObjectStorageError",
    "PermissionDeniedError",
    "UnknownBucketError",
]
