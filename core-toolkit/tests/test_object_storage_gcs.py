"""GcsObjectStorage（core_toolkit.object_storage.gcs）の単体テスト。

gcloud-aio-storageの実クライアントはネットワークI/Oを要するため、GCS JSON
APIの挙動（オブジェクトリソースのフィールド、エラー時に
``aiohttp.ClientResponseError``を送出すること、composeによる結合等）を
模した最小のフェイククライアントを使う。署名付きURL生成は実際の暗号署名
（サービスアカウント鍵またはIAM API）を要するため、ここではテストせず
実バックエンドに対する検証に委ねる（設計spec参照）。
"""

from datetime import UTC, datetime, timedelta

import aiohttp
import pytest
from gcloud.aio.storage import Blob

from core_toolkit.object_storage.base import (
    BucketNotFoundError,
    ObjectNotFoundError,
    ObjectStorageError,
    PermissionDeniedError,
)
from core_toolkit.object_storage.gcs import GcsObjectStorage
from tests.object_storage_contract import ObjectStorageContract


def _response_error(status: int, message: str = "error") -> aiohttp.ClientResponseError:
    request_info = aiohttp.RequestInfo(
        url=aiohttp.client.URL("http://fake"),
        method="GET",
        headers={},
        real_url=aiohttp.client.URL("http://fake"),
    )
    return aiohttp.ClientResponseError(
        request_info=request_info, history=(), status=status, message=message
    )


# gcloud-aio-authはレスポンスボディを``message``に含める。バケット不在時の
# GCS JSON APIのレスポンスを模す。
_BUCKET_NOT_FOUND_MESSAGE = (
    'Not Found: {"error": {"code": 404, "message": '
    '"The specified bucket does not exist.", "errors": [{"message": '
    '"The specified bucket does not exist.", "domain": "global", '
    '"reason": "notFound"}]}}'
)


class _FakeStreamResponse:
    def __init__(self, data: bytes) -> None:
        self._data = data
        self._pos = 0

    async def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            chunk, self._pos = self._data[self._pos :], len(self._data)
        else:
            chunk = self._data[self._pos : self._pos + size]
            self._pos += len(chunk)
        return chunk

    async def __aenter__(self) -> _FakeStreamResponse:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None


class FakeGcsStorage:
    """GCS JSON APIの挙動を模したフェイク。"""

    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], dict] = {}
        self.compose_calls: list[tuple[str, list[str]]] = []

    def _resource(self, bucket: str, name: str) -> dict:
        obj = self.objects[(bucket, name)]
        return {
            "name": name,
            "bucket": bucket,
            "size": str(len(obj["data"])),
            "contentType": obj["content_type"],
            "etag": "fake-etag",
            "updated": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "metadata": obj["metadata"],
        }

    async def upload(
        self,
        bucket,
        object_name,
        file_data,
        *,
        content_type=None,
        metadata=None,
        **_kwargs,
    ):
        self.objects[(bucket, object_name)] = {
            "data": file_data,
            "content_type": content_type,
            "metadata": dict(metadata or {}),
        }
        return self._resource(bucket, object_name)

    async def download(self, bucket, object_name, **_kwargs) -> bytes:
        try:
            return self.objects[(bucket, object_name)]["data"]
        except KeyError:
            raise _response_error(404) from None

    async def download_stream(self, bucket, object_name, **_kwargs):
        data = await self.download(bucket, object_name)
        return _FakeStreamResponse(data)

    async def download_metadata(self, bucket, object_name, **_kwargs) -> dict:
        try:
            return self._resource(bucket, object_name)
        except KeyError:
            raise _response_error(404) from None

    async def delete(self, bucket, object_name, **_kwargs) -> str:
        try:
            del self.objects[(bucket, object_name)]
        except KeyError:
            raise _response_error(404) from None
        return ""

    async def list_objects(self, bucket, *, params=None, **_kwargs) -> dict:
        prefix = (params or {}).get("prefix", "")
        items = [
            self._resource(b, name)
            for (b, name) in self.objects
            if b == bucket and name.startswith(prefix)
        ]
        return {"items": items} if items else {}

    async def compose(
        self, bucket, object_name, source_object_names, *, content_type=None, **_kwargs
    ) -> dict:
        self.compose_calls.append((object_name, list(source_object_names)))
        if len(source_object_names) > 32:
            raise _response_error(400, "too many source objects")
        try:
            body = b"".join(
                self.objects[(bucket, name)]["data"] for name in source_object_names
            )
        except KeyError:
            raise _response_error(404) from None
        # 実APIと同様、destinationにはcontentTypeしか渡せずmetadataは空になる
        self.objects[(bucket, object_name)] = {
            "data": body,
            "content_type": content_type,
            "metadata": {},
        }
        return self._resource(bucket, object_name)

    async def patch_metadata(self, bucket, object_name, metadata, **_kwargs) -> dict:
        try:
            obj = self.objects[(bucket, object_name)]
        except KeyError:
            raise _response_error(404) from None
        if "metadata" in metadata:
            obj["metadata"] = {**obj["metadata"], **metadata["metadata"]}
        if "contentType" in metadata:
            obj["content_type"] = metadata["contentType"]
        return self._resource(bucket, object_name)

    def tmp_object_names(self) -> list[str]:
        return [
            name for (_, name) in self.objects if name.startswith(".multipart-tmp/")
        ]


class TestGcsObjectStorageContract(ObjectStorageContract):
    @pytest.fixture
    def storage(self) -> GcsObjectStorage:
        return GcsObjectStorage({"uploads": "uploads-x7f3"}, FakeGcsStorage())

    @pytest.fixture
    def logical_bucket(self) -> str:
        return "uploads"


@pytest.mark.asyncio
async def test_get_missing_object_raises_object_not_found():
    storage = GcsObjectStorage({"uploads": "uploads-x7f3"}, FakeGcsStorage())
    with pytest.raises(ObjectNotFoundError):
        await storage.get("uploads", "missing.txt")


@pytest.mark.asyncio
async def test_delete_missing_object_does_not_raise():
    storage = GcsObjectStorage({"uploads": "uploads-x7f3"}, FakeGcsStorage())
    await storage.delete("uploads", "missing.txt")


@pytest.mark.asyncio
async def test_multipart_complete_composes_more_than_32_parts():
    storage = GcsObjectStorage({"uploads": "uploads-x7f3"}, FakeGcsStorage())
    upload = await storage.begin_multipart("uploads", "big.bin")
    for i in range(1, 35):
        await upload.upload_part(i, bytes([i % 256]))

    info = await upload.complete()

    assert info.size == 34
    assert await storage.get("uploads", "big.bin") == bytes(
        i % 256 for i in range(1, 35)
    )


@pytest.mark.asyncio
async def test_generic_response_error_is_wrapped_as_object_storage_error():
    class FailingStorage(FakeGcsStorage):
        async def download(self, bucket, object_name, **_kwargs):
            raise _response_error(500)

    storage = GcsObjectStorage({"uploads": "uploads-x7f3"}, FailingStorage())
    with pytest.raises(ObjectStorageError):
        await storage.get("uploads", "a.txt")


def _storage(fake: FakeGcsStorage | None = None) -> GcsObjectStorage:
    return GcsObjectStorage({"uploads": "uploads-x7f3"}, fake or FakeGcsStorage())


# --- C1: 同じキーへの並行マルチパート ---


@pytest.mark.asyncio
async def test_concurrent_multipart_sessions_on_same_key_do_not_corrupt_each_other():
    fake = FakeGcsStorage()
    storage = _storage(fake)
    first = await storage.begin_multipart("uploads", "same.bin")
    second = await storage.begin_multipart("uploads", "same.bin")

    # パート番号が重なるように交互に送信する
    await first.upload_part(1, b"A1")
    await second.upload_part(1, b"B1")
    await first.upload_part(2, b"A2")
    await second.upload_part(2, b"B2")

    await first.complete()
    assert await storage.get("uploads", "same.bin") == b"A1A2"

    await second.complete()
    assert await storage.get("uploads", "same.bin") == b"B1B2"
    assert fake.tmp_object_names() == []


@pytest.mark.asyncio
async def test_multipart_tmp_names_are_scoped_by_session():
    fake = FakeGcsStorage()
    storage = _storage(fake)
    first = await storage.begin_multipart("uploads", "same.bin")
    second = await storage.begin_multipart("uploads", "same.bin")

    await first.upload_part(1, b"A")
    await second.upload_part(1, b"B")

    names = fake.tmp_object_names()
    assert len(names) == 2
    assert all(name.startswith(".multipart-tmp/same.bin/") for name in names)


# --- I2: 一時オブジェクトのリーク・list()からの除外 ---


@pytest.mark.asyncio
async def test_multipart_complete_removes_all_tmp_objects_for_many_parts():
    fake = FakeGcsStorage()
    storage = _storage(fake)
    upload = await storage.begin_multipart("uploads", "big.bin")
    for i in range(1, 100):
        await upload.upload_part(i, bytes([i % 256]))

    await upload.complete()

    assert await storage.get("uploads", "big.bin") == bytes(
        i % 256 for i in range(1, 100)
    )
    assert fake.tmp_object_names() == []


@pytest.mark.asyncio
async def test_multipart_with_32_or_fewer_parts_composes_directly_once():
    fake = FakeGcsStorage()
    storage = _storage(fake)
    upload = await storage.begin_multipart("uploads", "direct.bin")
    for i in range(1, 33):
        await upload.upload_part(i, b"x")

    await upload.complete()

    assert len(fake.compose_calls) == 1
    destination, sources = fake.compose_calls[0]
    assert destination == "direct.bin"
    assert len(sources) == 32


@pytest.mark.asyncio
async def test_abort_after_failed_complete_removes_parts_and_staging_objects():
    class FailingFinalCompose(FakeGcsStorage):
        async def compose(self, bucket, object_name, source_object_names, **kwargs):
            if object_name == "big.bin":
                raise _response_error(500)
            return await super().compose(
                bucket, object_name, source_object_names, **kwargs
            )

    fake = FailingFinalCompose()
    storage = _storage(fake)
    upload = await storage.begin_multipart("uploads", "big.bin")
    for i in range(1, 40):  # 32超なのでstagingが作られる
        await upload.upload_part(i, b"x")

    with pytest.raises(ObjectStorageError):
        await upload.complete()
    assert any("staging" in name for name in fake.tmp_object_names())

    await upload.abort()

    assert fake.tmp_object_names() == []
    assert await storage.exists("uploads", "big.bin") is False


@pytest.mark.asyncio
async def test_async_with_aborts_and_cleans_up_when_complete_fails():
    class FailingCompose(FakeGcsStorage):
        async def compose(self, *args, **kwargs):
            raise _response_error(500)

    fake = FailingCompose()
    storage = _storage(fake)

    with pytest.raises(ObjectStorageError):
        async with await storage.begin_multipart("uploads", "a.bin") as upload:
            await upload.upload_part(1, b"a")
            await upload.complete()

    assert fake.tmp_object_names() == []


@pytest.mark.asyncio
async def test_abort_ignores_already_deleted_tmp_objects():
    fake = FakeGcsStorage()
    storage = _storage(fake)
    upload = await storage.begin_multipart("uploads", "a.bin")
    await upload.upload_part(1, b"a")
    await upload.upload_part(2, b"b")
    # 外部要因で一時オブジェクトが既に消えている状況
    for name in fake.tmp_object_names():
        del fake.objects[("uploads-x7f3", name)]

    await upload.abort()  # 404を無視して例外を出さない


@pytest.mark.asyncio
async def test_list_excludes_leaked_multipart_tmp_objects():
    fake = FakeGcsStorage()
    storage = _storage(fake)
    await storage.put("uploads", "real.txt", b"1")
    await fake.upload("uploads-x7f3", ".multipart-tmp/real.txt/abc/part00001", b"x")

    keys = [info.key async for info in storage.list("uploads")]

    assert keys == ["real.txt"]


@pytest.mark.asyncio
async def test_upload_part_rejects_out_of_range_part_number():
    upload = await _storage().begin_multipart("uploads", "a.bin")
    with pytest.raises(ObjectStorageError, match="part_number"):
        await upload.upload_part(0, b"a")


# --- I3: マルチパートのmetadata ---


@pytest.mark.asyncio
async def test_multipart_without_metadata_skips_patch():
    class NoPatch(FakeGcsStorage):
        async def patch_metadata(self, *args, **kwargs):
            raise AssertionError("patch_metadata must not be called")

    storage = _storage(NoPatch())
    upload = await storage.begin_multipart(
        "uploads", "a.bin", content_type="text/plain"
    )
    await upload.upload_part(1, b"a")

    info = await upload.complete()

    assert info.content_type == "text/plain"


# --- I4: BucketNotFoundError ---


class _MissingBucketStorage(FakeGcsStorage):
    """物理バケットが存在しない状態を模す（全操作がバケット不在の404）。"""

    def _missing(self):
        return _response_error(404, _BUCKET_NOT_FOUND_MESSAGE)

    async def upload(self, *args, **kwargs):
        raise self._missing()

    async def download(self, *args, **kwargs):
        raise self._missing()

    async def download_metadata(self, *args, **kwargs):
        raise self._missing()

    async def delete(self, *args, **kwargs):
        raise self._missing()

    async def list_objects(self, *args, **kwargs):
        raise self._missing()


@pytest.mark.asyncio
async def test_missing_bucket_raises_bucket_not_found_for_put_get_head():
    storage = _storage(_MissingBucketStorage())
    with pytest.raises(BucketNotFoundError):
        await storage.put("uploads", "a.txt", b"a")
    with pytest.raises(BucketNotFoundError):
        await storage.get("uploads", "a.txt")
    with pytest.raises(BucketNotFoundError):
        await storage.head("uploads", "a.txt")


@pytest.mark.asyncio
async def test_delete_on_missing_bucket_is_not_treated_as_success():
    storage = _storage(_MissingBucketStorage())
    with pytest.raises(BucketNotFoundError):
        await storage.delete("uploads", "a.txt")


@pytest.mark.asyncio
async def test_list_on_missing_bucket_raises_bucket_not_found():
    storage = _storage(_MissingBucketStorage())
    with pytest.raises(BucketNotFoundError):
        async for _ in storage.list("uploads"):
            pass


# --- I5: 例外の正規化 ---


@pytest.mark.asyncio
async def test_list_wraps_generic_response_error():
    class FailingList(FakeGcsStorage):
        async def list_objects(self, *args, **kwargs):
            raise _response_error(500)

    storage = _storage(FailingList())
    with pytest.raises(ObjectStorageError):
        async for _ in storage.list("uploads"):
            pass


@pytest.mark.asyncio
async def test_upload_part_normalizes_permission_denied():
    class DeniedUpload(FakeGcsStorage):
        async def upload(self, *args, **kwargs):
            raise _response_error(403)

    upload = await _storage(DeniedUpload()).begin_multipart("uploads", "a.bin")
    with pytest.raises(PermissionDeniedError):
        await upload.upload_part(1, b"a")


@pytest.mark.asyncio
async def test_complete_normalizes_compose_error():
    class FailingCompose(FakeGcsStorage):
        async def compose(self, *args, **kwargs):
            raise _response_error(500)

    upload = await _storage(FailingCompose()).begin_multipart("uploads", "a.bin")
    await upload.upload_part(1, b"a")
    with pytest.raises(ObjectStorageError) as exc_info:
        await upload.complete()
    assert isinstance(exc_info.value.__cause__, aiohttp.ClientResponseError)


@pytest.mark.asyncio
async def test_abort_normalizes_delete_error():
    class FailingDelete(FakeGcsStorage):
        async def delete(self, *args, **kwargs):
            raise _response_error(500)

    upload = await _storage(FailingDelete()).begin_multipart("uploads", "a.bin")
    await upload.upload_part(1, b"a")
    with pytest.raises(ObjectStorageError):
        await upload.abort()


@pytest.mark.asyncio
async def test_presigned_url_longer_than_7_days_raises_object_storage_error():
    storage = _storage()
    with pytest.raises(ObjectStorageError, match="7 days"):
        await storage.presigned_download_url(
            "uploads", "a.txt", expires=timedelta(days=8)
        )


@pytest.mark.asyncio
async def test_presigned_url_value_error_from_sdk_is_normalized(monkeypatch):
    async def fail(self, *args, **kwargs):
        raise ValueError("private key is invalid or unsupported")

    monkeypatch.setattr(Blob, "get_signed_url", fail)
    storage = _storage()
    with pytest.raises(ObjectStorageError):
        await storage.presigned_upload_url(
            "uploads", "a.txt", expires=timedelta(minutes=5)
        )
