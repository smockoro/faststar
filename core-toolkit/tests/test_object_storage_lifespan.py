"""object_storage_lifespanの統合テスト。scheme="memory"を使うことで、
バックエンドSDKのモンキーパッチ無しに名前付き登録・取得・
論理名の独立性を検証できる。
"""

import pytest
from starlette.applications import Starlette
from starlette.requests import Request

from core_toolkit.lifespan import create_lifespan
from core_toolkit.object_storage.memory import InMemoryObjectStorage
from core_toolkit.object_storage_lifespan import (
    ObjectStorageLifespanResource,
    get_object_storage,
)


def _make_request(app: Starlette) -> Request:
    return Request({"type": "http", "app": app})


@pytest.mark.asyncio
async def test_get_object_storage_returns_registered_storage():
    app = Starlette()
    lifespan = create_lifespan(
        ObjectStorageLifespanResource("main", "memory", {"uploads": "uploads-x7f3"})
    )

    async with lifespan(app):
        storage = get_object_storage("main")(_make_request(app))
        assert isinstance(storage, InMemoryObjectStorage)
        await storage.put("uploads", "a.txt", b"hello")
        assert await storage.get("uploads", "a.txt") == b"hello"


def test_get_object_storage_raises_runtime_error_when_missing():
    app = Starlette()

    with pytest.raises(RuntimeError, match="main"):
        get_object_storage("main")(_make_request(app))


@pytest.mark.asyncio
async def test_multiple_named_backends_are_independent():
    app = Starlette()
    lifespan = create_lifespan(
        ObjectStorageLifespanResource("main", "memory", {"uploads": "uploads-x7f3"}),
        ObjectStorageLifespanResource("archive", "memory", {"uploads": "archive-a91c"}),
    )

    async with lifespan(app):
        main_storage = get_object_storage("main")(_make_request(app))
        archive_storage = get_object_storage("archive")(_make_request(app))
        assert main_storage is not archive_storage

        await main_storage.put("uploads", "k", b"main-value")
        assert await archive_storage.exists("uploads", "k") is False
