"""object_storage_lifespan / CurrentObjectStorage の統合テスト。

Client(app)（インメモリトランスポート）はサーバーのlifespanを実際に
entryするため、ツール関数からCurrentObjectStorage(name)がDepends経由で
正しく解決されることをend-to-endで検証する。scheme="memory"を使うことで
バックエンドSDKへの依存無しにテストできる。
"""

import pytest
from core_toolkit.object_storage import ObjectStorage
from fastmcp import Client, FastMCP

from fastmcp_toolkit.object_storage_lifespan import (
    CurrentObjectStorage,
    _get_object_storage,
    object_storage_lifespan,
)


@pytest.mark.asyncio
async def test_tool_resolves_object_storage_via_depends():
    app = FastMCP(
        "test",
        lifespan=object_storage_lifespan("main", "memory", {"uploads": "uploads-x7f3"}),
    )

    @app.tool
    async def write_and_read(
        key: str, value: str, storage: ObjectStorage = CurrentObjectStorage("main")
    ) -> str:
        await storage.put("uploads", key, value.encode())
        data = await storage.get("uploads", key)
        return data.decode()

    async with Client(app) as client:
        result = await client.call_tool("write_and_read", {"key": "k", "value": "v"})

    assert result.data == "v"


@pytest.mark.asyncio
async def test_multiple_named_backends_are_independent():
    app = FastMCP(
        "test",
        lifespan=object_storage_lifespan("main", "memory", {"uploads": "bucket-a"})
        | object_storage_lifespan("archive", "memory", {"uploads": "bucket-b"}),
    )

    @app.tool
    async def compare(
        main: ObjectStorage = CurrentObjectStorage("main"),
        archive: ObjectStorage = CurrentObjectStorage("archive"),
    ) -> bool:
        await main.put("uploads", "k", b"main-value")
        return main is not archive and await archive.exists("uploads", "k") is False

    async with Client(app) as client:
        result = await client.call_tool("compare", {})

    assert result.data is True


@pytest.mark.asyncio
async def test_tool_raises_runtime_error_when_lifespan_not_registered():
    app = FastMCP("test")

    @app.tool
    async def whoami(storage: ObjectStorage = CurrentObjectStorage("main")) -> str:
        return type(storage).__name__

    async with Client(app) as client:
        # RPC境界を越えるとサーバー側の詳細な例外メッセージ（"lifespan_context[...] is
        # not set..."）は失われ、クライアントに届くのはDepends解決対象の
        # パラメータ名を含む"Failed to resolve dependency '<param>' for <fn>"のみ
        # になる（fastmcp-toolkitのdb_lifespan/redis_lifespan/valkey_lifespanで
        # 確認済み）。そのため登録名"main"ではなくパラメータ名"storage"でmatchする。
        with pytest.raises(Exception, match=r"Failed to resolve dependency 'storage'"):
            await client.call_tool("whoami", {})


def test_get_object_storage_error_message_includes_registration_hint():
    from types import SimpleNamespace

    get_storage = _get_object_storage("main")
    with pytest.raises(RuntimeError, match="main"):
        get_storage(SimpleNamespace(lifespan_context={}))
