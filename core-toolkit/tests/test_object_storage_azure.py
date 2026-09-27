"""AzureObjectStorage（core_toolkit.object_storage.azure）の単体テスト。

azure-storage-blob(.aio)の実クライアントはネットワークI/Oを要するため、
Blob Service REST APIの挙動（``ResourceNotFoundError``、``stage_block``/
``commit_block_list``によるブロックベースのマルチパート等）を模した
最小のフェイククライアントを使う。``generate_blob_sas``は純粋なHMAC署名
計算のみでネットワークI/Oを要さないため、署名付きURL生成は実際に呼び出して
検証する。
"""

import base64
from datetime import timedelta

import pytest
from azure.core.exceptions import HttpResponseError, ResourceNotFoundError

from core_toolkit.object_storage.azure import AzureObjectStorage
from core_toolkit.object_storage.base import (
    NotSupportedError,
    ObjectNotFoundError,
    ObjectStorageError,
)
from tests.object_storage_contract import ObjectStorageContract


class _ContentSettings:
    def __init__(self, content_type):
        self.content_type = content_type


class _FakeBlobProperties:
    def __init__(self, size, content_type, metadata, name=None):
        self.size = size
        self.content_settings = _ContentSettings(content_type)
        self.etag = "fake-etag"
        self.last_modified = None
        self.metadata = metadata
        self.name = name


class _FakeDownloader:
    def __init__(self, data: bytes) -> None:
        self._data = data

    async def readall(self) -> bytes:
        return self._data

    async def chunks(self):
        chunk_size = 16
        for i in range(0, len(self._data), chunk_size):
            yield self._data[i : i + chunk_size]


class FakeBlobClient:
    def __init__(self, store: dict, container: str, blob_name: str) -> None:
        self._store = store
        self._container = container
        self._blob_name = blob_name
        self.url = f"https://fake.blob.core.windows.net/{container}/{blob_name}"
        # Azure実サービスでは、ステージ済みブロックはコミットされるまで
        # このBlobClientインスタンス（＝同一クライアント接続）の外からは
        # 見えない未確定な状態にある。get_blob_properties/download_blob等で
        # 見えてしまうと「ステージのみでexistsがTrueになる」誤ったシミュレーションに
        # なるため、確定済みの`_store`とは別にインスタンスローカルで保持する。
        self._staged_blocks: dict[str, bytes] = {}

    def _key(self):
        return (self._container, self._blob_name)

    async def upload_blob(
        self, data, *, overwrite=True, metadata=None, content_settings=None, **_kwargs
    ):
        content_type = content_settings.content_type if content_settings else None
        self._store[self._key()] = {
            "data": data,
            "content_type": content_type,
            "metadata": dict(metadata or {}),
            "blocks": {},
        }
        return {}

    async def download_blob(self, **_kwargs):
        try:
            obj = self._store[self._key()]
        except KeyError:
            raise ResourceNotFoundError("blob not found") from None
        return _FakeDownloader(obj["data"])

    async def get_blob_properties(self, **_kwargs):
        try:
            obj = self._store[self._key()]
        except KeyError:
            raise ResourceNotFoundError("blob not found") from None
        return _FakeBlobProperties(
            len(obj["data"]), obj["content_type"], obj["metadata"]
        )

    async def delete_blob(self, **_kwargs):
        try:
            del self._store[self._key()]
        except KeyError:
            raise ResourceNotFoundError("blob not found") from None

    async def stage_block(self, block_id: str, data: bytes, **_kwargs):
        self._staged_blocks[block_id] = data
        return {}

    async def commit_block_list(
        self, block_list, *, content_settings=None, metadata=None, **_kwargs
    ):
        body = b"".join(self._staged_blocks[block.id] for block in block_list)
        content_type = content_settings.content_type if content_settings else None
        self._store[self._key()] = {
            "data": body,
            "content_type": content_type,
            "metadata": dict(metadata or {}),
            "blocks": {},
        }
        return {}


class FakeContainerClient:
    def __init__(self, store: dict, container: str) -> None:
        self._store = store
        self._container = container

    def get_blob_client(self, blob_name: str) -> FakeBlobClient:
        return FakeBlobClient(self._store, self._container, blob_name)

    async def list_blobs(self, name_starts_with: str | None = None, **_kwargs):
        prefix = name_starts_with or ""
        for (container, name), obj in list(self._store.items()):
            if container == self._container and name.startswith(prefix):
                yield _FakeBlobProperties(
                    len(obj["data"]), obj["content_type"], obj["metadata"], name=name
                )


class FakeBlobServiceClient:
    """Blob Service REST APIの挙動を模したフェイク。"""

    account_name = "fakeaccount"

    def __init__(self) -> None:
        self._store: dict[tuple[str, str], dict] = {}

    def get_container_client(self, container: str) -> FakeContainerClient:
        return FakeContainerClient(self._store, container)


class TestAzureObjectStorageContract(ObjectStorageContract):
    @pytest.fixture
    def storage(self) -> AzureObjectStorage:
        return AzureObjectStorage({"uploads": "uploads-x7f3"}, FakeBlobServiceClient())

    @pytest.fixture
    def logical_bucket(self) -> str:
        return "uploads"


@pytest.mark.asyncio
async def test_get_missing_blob_raises_object_not_found():
    storage = AzureObjectStorage({"uploads": "uploads-x7f3"}, FakeBlobServiceClient())
    with pytest.raises(ObjectNotFoundError):
        await storage.get("uploads", "missing.txt")


@pytest.mark.asyncio
async def test_delete_missing_blob_does_not_raise():
    storage = AzureObjectStorage({"uploads": "uploads-x7f3"}, FakeBlobServiceClient())
    await storage.delete("uploads", "missing.txt")


@pytest.mark.asyncio
async def test_presigned_download_url_raises_without_account_key():
    storage = AzureObjectStorage({"uploads": "uploads-x7f3"}, FakeBlobServiceClient())
    with pytest.raises(NotSupportedError):
        await storage.presigned_download_url(
            "uploads", "a.txt", expires=timedelta(minutes=5)
        )


@pytest.mark.asyncio
async def test_presigned_download_url_returns_url_with_sas_signature():
    account_key = base64.b64encode(b"x" * 32).decode()
    storage = AzureObjectStorage(
        {"uploads": "uploads-x7f3"},
        FakeBlobServiceClient(),
        account_key=account_key,
    )

    url = await storage.presigned_download_url(
        "uploads", "a.txt", expires=timedelta(minutes=5)
    )

    assert url.startswith("https://fake.blob.core.windows.net/uploads-x7f3/a.txt?")
    assert "sig=" in url


@pytest.mark.asyncio
async def test_generic_http_response_error_is_wrapped_as_object_storage_error():
    class FailingBlobClient(FakeBlobClient):
        async def download_blob(self, **_kwargs):
            raise HttpResponseError("boom")

    class FailingContainerClient(FakeContainerClient):
        def get_blob_client(self, blob_name):
            return FailingBlobClient(self._store, self._container, blob_name)

    class FailingBlobServiceClient(FakeBlobServiceClient):
        def get_container_client(self, container):
            return FailingContainerClient(self._store, container)

    storage = AzureObjectStorage(
        {"uploads": "uploads-x7f3"}, FailingBlobServiceClient()
    )
    with pytest.raises(ObjectStorageError):
        await storage.get("uploads", "a.txt")
