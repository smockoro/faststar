"""object_storage.factory.open_object_storageのスキーム分岐テスト。

各バックエンドの実際のI/Oはs3.py/gcs.py/azure.pyそれぞれの単体テストで
検証済みのため、ここではscheme文字列に応じて正しいSDK呼び出しへ
ディスパッチされること、bucketsとclient_kwargsがそのまま渡ることのみを
検証する。
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest

from core_toolkit.object_storage.azure import AzureObjectStorage
from core_toolkit.object_storage.factory import open_object_storage
from core_toolkit.object_storage.gcs import GcsObjectStorage
from core_toolkit.object_storage.memory import InMemoryObjectStorage
from core_toolkit.object_storage.s3 import S3ObjectStorage


@pytest.mark.asyncio
async def test_memory_scheme_returns_in_memory_storage():
    async with open_object_storage("memory", {"uploads": "uploads-x7f3"}) as storage:
        assert isinstance(storage, InMemoryObjectStorage)
        await storage.put("uploads", "a.txt", b"hello")
        assert await storage.get("uploads", "a.txt") == b"hello"


@pytest.mark.asyncio
async def test_unknown_scheme_raises_value_error():
    with pytest.raises(ValueError, match="unknown"):
        async with open_object_storage("unknown", {}):
            pass


@pytest.mark.asyncio
async def test_s3_scheme_dispatches_to_aiobotocore(monkeypatch: pytest.MonkeyPatch):
    import aiobotocore.session

    captured: dict[str, Any] = {}
    sentinel_client = object()

    @asynccontextmanager
    async def fake_create_client(
        service_name: str, **kwargs: Any
    ) -> AsyncIterator[Any]:
        captured["service_name"] = service_name
        captured["kwargs"] = kwargs
        yield sentinel_client

    class FakeSession:
        def create_client(self, service_name: str, **kwargs: Any):
            return fake_create_client(service_name, **kwargs)

    monkeypatch.setattr(aiobotocore.session, "get_session", lambda: FakeSession())

    async with open_object_storage(
        "s3", {"uploads": "uploads-x7f3"}, region_name="us-east-1"
    ) as storage:
        assert isinstance(storage, S3ObjectStorage)

    assert captured["service_name"] == "s3"
    assert captured["kwargs"] == {"region_name": "us-east-1"}


@pytest.mark.asyncio
async def test_gs_scheme_dispatches_to_gcloud_aio_storage(
    monkeypatch: pytest.MonkeyPatch,
):
    import gcloud.aio.storage

    captured: dict[str, Any] = {}

    class FakeStorage:
        def __init__(self, **kwargs: Any) -> None:
            captured["kwargs"] = kwargs

        async def __aenter__(self) -> FakeStorage:
            return self

        async def __aexit__(self, *exc_info: Any) -> None:
            return None

    monkeypatch.setattr(gcloud.aio.storage, "Storage", FakeStorage)

    async with open_object_storage(
        "gs", {"uploads": "uploads-x7f3"}, api_root="http://fake-gcs:9000"
    ) as storage:
        assert isinstance(storage, GcsObjectStorage)

    assert captured["kwargs"] == {"api_root": "http://fake-gcs:9000"}


@pytest.mark.asyncio
async def test_azure_scheme_dispatches_to_blob_service_client(
    monkeypatch: pytest.MonkeyPatch,
):
    import azure.storage.blob.aio

    captured: dict[str, Any] = {}

    class FakeBlobServiceClient:
        def __init__(
            self, account_url: str, *, credential: Any = None, **kwargs: Any
        ) -> None:
            captured["account_url"] = account_url
            captured["credential"] = credential
            captured["kwargs"] = kwargs

        async def __aenter__(self) -> FakeBlobServiceClient:
            return self

        async def __aexit__(self, *exc_info: Any) -> None:
            return None

    monkeypatch.setattr(
        azure.storage.blob.aio, "BlobServiceClient", FakeBlobServiceClient
    )

    async with open_object_storage(
        "azure",
        {"uploads": "uploads-x7f3"},
        account_url="https://example.blob.core.windows.net",
        credential="fake-key",
        account_key="fake-key",
    ) as storage:
        assert isinstance(storage, AzureObjectStorage)

    assert captured["account_url"] == "https://example.blob.core.windows.net"
    assert captured["credential"] == "fake-key"
    assert captured["kwargs"] == {}
