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
    BucketNotFoundError,
    NotSupportedError,
    ObjectNotFoundError,
    ObjectStorageError,
    PermissionDeniedError,
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


def _storage_error(
    cls: type[HttpResponseError], error_code: str, status_code: int
) -> HttpResponseError:
    """azure-storage-blobが送出する例外を模す（``error_code``は
    ``x-ms-error-code``ヘッダ由来でコンテナ不在/blob不在の区別に使われる）。"""
    error = cls(f"{error_code}\nErrorCode:{error_code}")
    error.error_code = error_code
    error.status_code = status_code
    return error


def _blob_not_found() -> HttpResponseError:
    return _storage_error(ResourceNotFoundError, "BlobNotFound", 404)


def _container_not_found() -> HttpResponseError:
    return _storage_error(ResourceNotFoundError, "ContainerNotFound", 404)


class FakeBlobClient:
    def __init__(self, service: FakeBlobServiceClient, container: str, blob_name: str):
        self._service = service
        self._store = service._store
        self._container = container
        self._blob_name = blob_name
        self.url = f"https://fake.blob.core.windows.net/{container}/{blob_name}"

    def _key(self):
        return (self._container, self._blob_name)

    def _check_container(self) -> None:
        if self._container in self._service.missing_containers:
            raise _container_not_found()

    @property
    def _staged_blocks(self) -> dict[str, bytes]:
        # Azure実サービスでは、未コミットブロックはblob単位でサーバー側に保持され、
        # 同じblobを指す全てのクライアント（＝同じキーへの別セッション）から
        # 共有される。コミットされるまでget_blob_properties/download_blob等からは
        # 見えないため、確定済みの`_store`とは別に保持する。
        return self._service.staged.setdefault(self._key(), {})

    async def upload_blob(
        self, data, *, overwrite=True, metadata=None, content_settings=None, **_kwargs
    ):
        self._check_container()
        content_type = content_settings.content_type if content_settings else None
        self._store[self._key()] = {
            "data": data,
            "content_type": content_type,
            "metadata": dict(metadata or {}),
        }
        # Put Blobは、そのblobの未コミットブロックをすべて破棄する
        self._service.staged.pop(self._key(), None)
        return {}

    async def download_blob(self, **_kwargs):
        self._check_container()
        try:
            obj = self._store[self._key()]
        except KeyError:
            raise _blob_not_found() from None
        return _FakeDownloader(obj["data"])

    async def get_blob_properties(self, **_kwargs):
        self._check_container()
        try:
            obj = self._store[self._key()]
        except KeyError:
            raise _blob_not_found() from None
        return _FakeBlobProperties(
            len(obj["data"]), obj["content_type"], obj["metadata"]
        )

    async def delete_blob(self, **_kwargs):
        self._check_container()
        try:
            del self._store[self._key()]
        except KeyError:
            raise _blob_not_found() from None

    async def stage_block(self, block_id: str, data: bytes, **_kwargs):
        self._check_container()
        staged = self._staged_blocks
        # 同一blob内の未コミットブロックIDは全て同じ長さである必要がある
        if any(len(existing) != len(block_id) for existing in staged):
            raise _storage_error(HttpResponseError, "InvalidBlobOrBlock", 400)
        staged[block_id] = data
        return {}

    async def commit_block_list(
        self, block_list, *, content_settings=None, metadata=None, **_kwargs
    ):
        self._check_container()
        staged = self._staged_blocks
        if any(block.id not in staged for block in block_list):
            raise _storage_error(HttpResponseError, "InvalidBlockList", 400)
        body = b"".join(staged[block.id] for block in block_list)
        content_type = content_settings.content_type if content_settings else None
        self._store[self._key()] = {
            "data": body,
            "content_type": content_type,
            "metadata": dict(metadata or {}),
        }
        # Put Block Listの成功後、リストに含まれない未コミットブロックは破棄される
        self._service.staged.pop(self._key(), None)
        return {}


class FakeContainerClient:
    def __init__(self, service: FakeBlobServiceClient, container: str) -> None:
        self._service = service
        self._store = service._store
        self._container = container

    def get_blob_client(self, blob_name: str) -> FakeBlobClient:
        return FakeBlobClient(self._service, self._container, blob_name)

    async def list_blobs(self, name_starts_with: str | None = None, **_kwargs):
        if self._container in self._service.missing_containers:
            raise _container_not_found()
        prefix = name_starts_with or ""
        for (container, name), obj in list(self._store.items()):
            if container == self._container and name.startswith(prefix):
                yield _FakeBlobProperties(
                    len(obj["data"]), obj["content_type"], obj["metadata"], name=name
                )


class FakeBlobServiceClient:
    """Blob Service REST APIの挙動を模したフェイク。"""

    account_name = "fakeaccount"

    def __init__(self, *, missing_containers: set[str] | None = None) -> None:
        self._store: dict[tuple[str, str], dict] = {}
        self.staged: dict[tuple[str, str], dict[str, bytes]] = {}
        self.missing_containers = set(missing_containers or ())

    def get_container_client(self, container: str) -> FakeContainerClient:
        return FakeContainerClient(self, container)


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
            return FailingBlobClient(self._service, self._container, blob_name)

    class FailingBlobServiceClient(FakeBlobServiceClient):
        def get_container_client(self, container):
            return FailingContainerClient(self, container)

    storage = AzureObjectStorage(
        {"uploads": "uploads-x7f3"}, FailingBlobServiceClient()
    )
    with pytest.raises(ObjectStorageError):
        await storage.get("uploads", "a.txt")


def _storage(service: FakeBlobServiceClient | None = None) -> AzureObjectStorage:
    return AzureObjectStorage(
        {"uploads": "uploads-x7f3"}, service or FakeBlobServiceClient()
    )


# --- C1: 同じキーへの並行マルチパート ---


@pytest.mark.asyncio
async def test_concurrent_multipart_sessions_on_same_key_do_not_corrupt_each_other():
    storage = _storage()
    first = await storage.begin_multipart("uploads", "same.bin")
    second = await storage.begin_multipart("uploads", "same.bin")

    # パート番号が重なるように交互に送信する（未コミットブロックはblob単位で共有）
    await first.upload_part(1, b"A1")
    await second.upload_part(1, b"B1")
    await first.upload_part(2, b"A2")
    await second.upload_part(2, b"B2")

    await first.complete()
    assert await storage.get("uploads", "same.bin") == b"A1A2"

    # Azureは Put Block List 成功時にリスト外の未コミットブロックを破棄するため、
    # 後からcompleteしたセッションは失敗する。データが混ざらないことが重要。
    with pytest.raises(ObjectStorageError):
        await second.complete()
    assert await storage.get("uploads", "same.bin") == b"A1A2"


@pytest.mark.asyncio
async def test_concurrent_multipart_session_completing_first_wins_intact():
    storage = _storage()
    first = await storage.begin_multipart("uploads", "same.bin")
    second = await storage.begin_multipart("uploads", "same.bin")
    await first.upload_part(1, b"A1")
    await second.upload_part(1, b"B1")
    await second.upload_part(2, b"B2")
    await first.upload_part(2, b"A2")

    await second.complete()

    assert await storage.get("uploads", "same.bin") == b"B1B2"


@pytest.mark.asyncio
async def test_block_ids_are_unique_per_session_and_fixed_length():
    service = FakeBlobServiceClient()
    storage = _storage(service)
    first = await storage.begin_multipart("uploads", "same.bin")
    second = await storage.begin_multipart("uploads", "same.bin")

    await first.upload_part(1, b"a")
    await second.upload_part(1, b"b")
    await first.upload_part(10_000, b"c")

    block_ids = list(service.staged[("uploads-x7f3", "same.bin")])
    assert len(set(block_ids)) == 3
    assert len({len(block_id) for block_id in block_ids}) == 1


@pytest.mark.asyncio
async def test_upload_part_rejects_out_of_range_part_number():
    upload = await _storage().begin_multipart("uploads", "a.bin")
    with pytest.raises(ObjectStorageError, match="part_number"):
        await upload.upload_part(10_001, b"a")


# --- I4: BucketNotFoundError ---


@pytest.mark.asyncio
async def test_missing_container_raises_bucket_not_found():
    storage = _storage(FakeBlobServiceClient(missing_containers={"uploads-x7f3"}))
    with pytest.raises(BucketNotFoundError):
        await storage.put("uploads", "a.txt", b"a")
    with pytest.raises(BucketNotFoundError):
        await storage.get("uploads", "a.txt")
    with pytest.raises(BucketNotFoundError):
        await storage.head("uploads", "a.txt")
    with pytest.raises(BucketNotFoundError):
        async for _ in storage.get_stream("uploads", "a.txt"):
            pass


@pytest.mark.asyncio
async def test_delete_on_missing_container_is_not_treated_as_success():
    storage = _storage(FakeBlobServiceClient(missing_containers={"uploads-x7f3"}))
    with pytest.raises(BucketNotFoundError):
        await storage.delete("uploads", "a.txt")


@pytest.mark.asyncio
async def test_list_on_missing_container_raises_bucket_not_found():
    storage = _storage(FakeBlobServiceClient(missing_containers={"uploads-x7f3"}))
    with pytest.raises(BucketNotFoundError):
        async for _ in storage.list("uploads"):
            pass


@pytest.mark.asyncio
async def test_multipart_on_missing_container_raises_bucket_not_found():
    storage = _storage(FakeBlobServiceClient(missing_containers={"uploads-x7f3"}))
    upload = await storage.begin_multipart("uploads", "a.bin")
    with pytest.raises(BucketNotFoundError):
        await upload.upload_part(1, b"a")


@pytest.mark.asyncio
async def test_error_code_as_storage_error_code_enum_is_recognized():
    from azure.storage.blob._shared.models import StorageErrorCode

    class EnumCodeBlobClient(FakeBlobClient):
        async def get_blob_properties(self, **_kwargs):
            error = ResourceNotFoundError("ContainerNotFound")
            error.error_code = StorageErrorCode.CONTAINER_NOT_FOUND
            raise error

    class EnumCodeContainerClient(FakeContainerClient):
        def get_blob_client(self, blob_name):
            return EnumCodeBlobClient(self._service, self._container, blob_name)

    class EnumCodeService(FakeBlobServiceClient):
        def get_container_client(self, container):
            return EnumCodeContainerClient(self, container)

    with pytest.raises(BucketNotFoundError):
        await _storage(EnumCodeService()).head("uploads", "a.txt")


# --- I5: 例外の正規化 ---


class _FailingListService(FakeBlobServiceClient):
    def __init__(self, error: HttpResponseError) -> None:
        super().__init__()
        self._error = error

    def get_container_client(self, container):
        error = self._error

        class FailingListContainer(FakeContainerClient):
            async def list_blobs(self, name_starts_with=None, **_kwargs):
                raise error
                yield  # pragma: no cover - 非同期ジェネレータにするため

        return FailingListContainer(self, container)


@pytest.mark.asyncio
async def test_list_wraps_generic_http_response_error():
    storage = _storage(_FailingListService(HttpResponseError("boom")))
    with pytest.raises(ObjectStorageError) as exc_info:
        async for _ in storage.list("uploads"):
            pass
    assert isinstance(exc_info.value.__cause__, HttpResponseError)


@pytest.mark.asyncio
async def test_list_maps_permission_denied():
    denied = _storage_error(HttpResponseError, "AuthorizationFailure", 403)
    storage = _storage(_FailingListService(denied))
    with pytest.raises(PermissionDeniedError):
        async for _ in storage.list("uploads"):
            pass
