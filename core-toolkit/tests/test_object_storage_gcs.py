"""GcsObjectStorage（core_toolkit.object_storage.gcs）の単体テスト。

gcloud-aio-storageの実クライアントはネットワークI/Oを要するため、GCS JSON
APIの挙動（オブジェクトリソースのフィールド、エラー時に
``aiohttp.ClientResponseError``を送出すること、composeによる結合等）を
模した最小のフェイククライアントを使う。署名付きURL生成は実際の暗号署名
（サービスアカウント鍵またはIAM API）を要するため、ここではテストせず
実バックエンドに対する検証に委ねる（設計spec参照）。
"""

from datetime import UTC, datetime

import aiohttp
import pytest

from core_toolkit.object_storage.base import ObjectNotFoundError, ObjectStorageError
from core_toolkit.object_storage.gcs import GcsObjectStorage
from tests.object_storage_contract import ObjectStorageContract


def _response_error(status: int) -> aiohttp.ClientResponseError:
    request_info = aiohttp.RequestInfo(
        url=aiohttp.client.URL("http://fake"),
        method="GET",
        headers={},
        real_url=aiohttp.client.URL("http://fake"),
    )
    return aiohttp.ClientResponseError(
        request_info=request_info, history=(), status=status, message="error"
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
        body = b"".join(
            self.objects[(bucket, name)]["data"] for name in source_object_names
        )
        self.objects[(bucket, object_name)] = {
            "data": body,
            "content_type": content_type,
            "metadata": {},
        }
        return self._resource(bucket, object_name)


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
