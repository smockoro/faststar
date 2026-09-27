"""InMemoryObjectStorage: ローカル開発・ユニットテスト用のObjectStorage実装。

プロセス外にはデータを持ち出さない。署名付きURLは``NotSupportedError``を
送出する（署名付きURLは実バックエンドに対してのみ検証すべきという方針の
ため。詳細は設計spec参照）。
"""

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from core_toolkit.object_storage.base import (
    MAX_PART_NUMBER,
    MIN_PART_SIZE,
    MultipartUpload,
    NotSupportedError,
    ObjectInfo,
    ObjectNotFoundError,
    ObjectStorage,
    ObjectStorageError,
)


@dataclass
class _StoredObject:
    data: bytes
    content_type: str | None
    metadata: dict[str, str]
    last_modified: datetime


class InMemoryObjectStorage(ObjectStorage):
    """メモリ上の``dict``にオブジェクトを保持するObjectStorage実装。

    Args:
        buckets: 論理バケット名から物理バケット名へのマッピング。
        min_part_size: マルチパートで最後以外のパートに要求する最小サイズ。
            実バックエンド（既定5 MiB）と同じ制約をテストで検証しやすくする
            ため、テストではより小さい値に差し替えられる。
    """

    def __init__(
        self, buckets: Mapping[str, str], *, min_part_size: int = MIN_PART_SIZE
    ) -> None:
        super().__init__(buckets)
        self._min_part_size = min_part_size
        self._objects: dict[tuple[str, str], _StoredObject] = {}

    async def put(
        self, bucket, key, data, *, content_type=None, metadata=None
    ) -> ObjectInfo:
        physical = self._resolve_bucket(bucket)
        stored = _StoredObject(
            data=data,
            content_type=content_type,
            metadata=dict(metadata or {}),
            last_modified=datetime.now(UTC),
        )
        self._objects[(physical, key)] = stored
        return self._to_info(bucket, key, stored)

    async def get(self, bucket, key) -> bytes:
        return self._get_stored(bucket, key).data

    async def get_stream(
        self, bucket, key, *, chunk_size=64 * 1024
    ) -> AsyncIterator[bytes]:
        data = self._get_stored(bucket, key).data
        for i in range(0, len(data), chunk_size):
            yield data[i : i + chunk_size]

    async def head(self, bucket, key) -> ObjectInfo:
        return self._to_info(bucket, key, self._get_stored(bucket, key))

    async def delete(self, bucket, key) -> None:
        physical = self._resolve_bucket(bucket)
        self._objects.pop((physical, key), None)

    async def list(self, bucket, prefix="") -> AsyncIterator[ObjectInfo]:
        physical = self._resolve_bucket(bucket)
        for (b, k), stored in list(self._objects.items()):
            if b == physical and k.startswith(prefix):
                yield self._to_info(bucket, k, stored)

    async def presigned_download_url(self, bucket, key, *, expires: timedelta) -> str:
        raise NotSupportedError(
            "InMemoryObjectStorage does not support presigned URLs. "
            "Test presigned URL behavior against a real backend."
        )

    async def presigned_upload_url(
        self, bucket, key, *, expires: timedelta, content_type=None
    ) -> str:
        raise NotSupportedError(
            "InMemoryObjectStorage does not support presigned URLs. "
            "Test presigned URL behavior against a real backend."
        )

    async def begin_multipart(
        self, bucket, key, *, content_type=None, metadata=None
    ) -> MultipartUpload:
        physical = self._resolve_bucket(bucket)
        return _InMemoryMultipartUpload(
            store=self,
            bucket=bucket,
            physical_bucket=physical,
            key=key,
            content_type=content_type,
            metadata=dict(metadata or {}),
            min_part_size=self._min_part_size,
        )

    def _get_stored(self, bucket: str, key: str) -> _StoredObject:
        physical = self._resolve_bucket(bucket)
        try:
            return self._objects[(physical, key)]
        except KeyError:
            raise ObjectNotFoundError(f"{bucket}/{key} not found") from None

    def _to_info(self, bucket: str, key: str, stored: _StoredObject) -> ObjectInfo:
        return ObjectInfo(
            bucket=bucket,
            key=key,
            size=len(stored.data),
            content_type=stored.content_type,
            etag=None,
            last_modified=stored.last_modified,
            metadata=dict(stored.metadata),
        )


class _InMemoryMultipartUpload(MultipartUpload):
    def __init__(
        self,
        *,
        store: InMemoryObjectStorage,
        bucket: str,
        physical_bucket: str,
        key: str,
        content_type: str | None,
        metadata: dict[str, str],
        min_part_size: int,
    ) -> None:
        self._store = store
        self._bucket = bucket
        self._key = key
        self._content_type = content_type
        self._metadata = metadata
        self._min_part_size = min_part_size
        self._parts: dict[int, bytes] = {}

    async def upload_part(self, part_number: int, data: bytes) -> None:
        if not (1 <= part_number <= MAX_PART_NUMBER):
            raise ObjectStorageError(
                f"part_number must be between 1 and {MAX_PART_NUMBER}, "
                f"got {part_number}"
            )
        self._parts[part_number] = data

    async def complete(self) -> ObjectInfo:
        numbers = sorted(self._parts)
        for number in numbers[:-1]:
            if len(self._parts[number]) < self._min_part_size:
                raise ObjectStorageError(
                    f"part {number} is smaller than the minimum part size "
                    f"({self._min_part_size} bytes) for a non-final part"
                )
        body = b"".join(self._parts[n] for n in numbers)
        return await self._store.put(
            self._bucket,
            self._key,
            body,
            content_type=self._content_type,
            metadata=self._metadata,
        )

    async def abort(self) -> None:
        self._parts.clear()
