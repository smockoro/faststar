"""ObjectStorage基底クラス（論理名解決・put_file/download_fileの便利メソッド・
MultipartUploadの自動abort）の単体テスト。

具象実装はcore_toolkit.object_storage.memory.InMemoryObjectStorageで別途
テストするため（tests/test_object_storage_memory.py）、ここでは基本操作を
差し替え可能な最小のフェイクだけを使い、基底クラス自身のロジックのみを
検証する。
"""

from collections.abc import AsyncIterator, Mapping
from pathlib import Path

import pytest

from core_toolkit.object_storage.base import (
    MultipartUpload,
    ObjectInfo,
    ObjectNotFoundError,
    ObjectStorage,
    UnknownBucketError,
)


class RecordingMultipartUpload(MultipartUpload):
    def __init__(self, bucket: str, key: str) -> None:
        self.bucket = bucket
        self.key = key
        self.parts: dict[int, bytes] = {}
        self.completed = False
        self.aborted = False

    async def upload_part(self, part_number: int, data: bytes) -> None:
        self.parts[part_number] = data

    async def complete(self) -> ObjectInfo:
        self.completed = True
        body = b"".join(self.parts[n] for n in sorted(self.parts))
        return ObjectInfo(
            bucket=self.bucket,
            key=self.key,
            size=len(body),
            content_type=None,
            etag=None,
            last_modified=None,
            metadata={},
        )

    async def abort(self) -> None:
        self.aborted = True


class FakeStorage(ObjectStorage):
    """テスト用の最小実装。dataはメモリの``dict``に保持する。"""

    def __init__(self, buckets: Mapping[str, str]) -> None:
        super().__init__(buckets)
        self._objects: dict[tuple[str, str], bytes] = {}
        self.multipart_sessions: list[RecordingMultipartUpload] = []
        self.fail_upload_part_number: int | None = None

    async def put(
        self, bucket, key, data, *, content_type=None, metadata=None
    ) -> ObjectInfo:
        physical = self._resolve_bucket(bucket)
        self._objects[(physical, key)] = data
        return ObjectInfo(
            bucket=bucket,
            key=key,
            size=len(data),
            content_type=content_type,
            etag=None,
            last_modified=None,
            metadata=metadata or {},
        )

    async def get(self, bucket, key) -> bytes:
        physical = self._resolve_bucket(bucket)
        try:
            return self._objects[(physical, key)]
        except KeyError:
            raise ObjectNotFoundError(f"{bucket}/{key}") from None

    async def get_stream(
        self, bucket, key, *, chunk_size=64 * 1024
    ) -> AsyncIterator[bytes]:
        data = await self.get(bucket, key)
        for i in range(0, len(data), chunk_size):
            yield data[i : i + chunk_size]

    async def head(self, bucket, key) -> ObjectInfo:
        data = await self.get(bucket, key)
        return ObjectInfo(
            bucket=bucket,
            key=key,
            size=len(data),
            content_type=None,
            etag=None,
            last_modified=None,
            metadata={},
        )

    async def delete(self, bucket, key) -> None:
        physical = self._resolve_bucket(bucket)
        self._objects.pop((physical, key), None)

    async def list(self, bucket, prefix=""):
        physical = self._resolve_bucket(bucket)
        for (b, k), data in self._objects.items():
            if b == physical and k.startswith(prefix):
                yield ObjectInfo(
                    bucket=bucket,
                    key=k,
                    size=len(data),
                    content_type=None,
                    etag=None,
                    last_modified=None,
                    metadata={},
                )

    async def presigned_download_url(self, bucket, key, *, expires) -> str:
        raise NotImplementedError

    async def presigned_upload_url(
        self, bucket, key, *, expires, content_type=None
    ) -> str:
        raise NotImplementedError

    async def begin_multipart(
        self, bucket, key, *, content_type=None, metadata=None
    ) -> MultipartUpload:
        self._resolve_bucket(bucket)
        session = RecordingMultipartUpload(bucket, key)
        if self.fail_upload_part_number is not None:
            original = session.upload_part

            async def failing_upload_part(part_number, data):
                if part_number == self.fail_upload_part_number:
                    raise RuntimeError("boom")
                await original(part_number, data)

            session.upload_part = failing_upload_part  # type: ignore[method-assign]
        self.multipart_sessions.append(session)
        return session


@pytest.fixture
def storage() -> FakeStorage:
    return FakeStorage({"uploads": "uploads-x7f3"})


@pytest.mark.asyncio
async def test_resolve_bucket_returns_physical_name(storage: FakeStorage):
    await storage.put("uploads", "a.txt", b"hello")
    assert await storage.get("uploads", "a.txt") == b"hello"


@pytest.mark.asyncio
async def test_resolve_bucket_raises_for_unknown_logical_name(storage: FakeStorage):
    with pytest.raises(UnknownBucketError, match="uploads"):
        await storage.put("unknown", "a.txt", b"hello")


@pytest.mark.asyncio
async def test_exists_returns_true_when_object_present(storage: FakeStorage):
    await storage.put("uploads", "a.txt", b"hello")
    assert await storage.exists("uploads", "a.txt") is True


@pytest.mark.asyncio
async def test_exists_returns_false_when_object_missing(storage: FakeStorage):
    assert await storage.exists("uploads", "missing.txt") is False


@pytest.mark.asyncio
async def test_put_file_uses_single_put_below_threshold(
    storage: FakeStorage, tmp_path: Path
):
    path = tmp_path / "small.bin"
    path.write_bytes(b"x" * 10)

    info = await storage.put_file("uploads", "small.bin", path, multipart_threshold=100)

    assert info.size == 10
    assert storage.multipart_sessions == []
    assert await storage.get("uploads", "small.bin") == b"x" * 10


@pytest.mark.asyncio
async def test_put_file_uses_multipart_above_threshold(
    storage: FakeStorage, tmp_path: Path
):
    path = tmp_path / "large.bin"
    path.write_bytes(b"a" * 30)

    info = await storage.put_file(
        "uploads", "large.bin", path, multipart_threshold=10, part_size=10
    )

    assert info.size == 30
    assert len(storage.multipart_sessions) == 1
    session = storage.multipart_sessions[0]
    assert session.completed is True
    assert sorted(session.parts) == [1, 2, 3]


@pytest.mark.asyncio
async def test_put_file_aborts_multipart_on_part_failure(
    storage: FakeStorage, tmp_path: Path
):
    path = tmp_path / "large.bin"
    path.write_bytes(b"a" * 30)
    storage.fail_upload_part_number = 2

    with pytest.raises(RuntimeError, match="boom"):
        await storage.put_file(
            "uploads", "large.bin", path, multipart_threshold=10, part_size=10
        )

    session = storage.multipart_sessions[0]
    assert session.aborted is True
    assert session.completed is False


@pytest.mark.asyncio
async def test_download_file_writes_stream_to_path(
    storage: FakeStorage, tmp_path: Path
):
    await storage.put("uploads", "a.txt", b"hello world")
    dest = tmp_path / "out.txt"

    await storage.download_file("uploads", "a.txt", dest)

    assert dest.read_bytes() == b"hello world"


class _AbortFailingUpload(RecordingMultipartUpload):
    async def abort(self) -> None:
        raise RuntimeError("abort failed")


@pytest.mark.asyncio
async def test_aexit_does_not_mask_original_exception_when_abort_fails():
    upload = _AbortFailingUpload("uploads", "a.bin")

    with pytest.raises(ValueError, match="original"):
        async with upload:
            raise ValueError("original")
