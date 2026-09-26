"""valkey_lifespan / CurrentValkeyClient の統合テスト。

Client(app)（インメモリトランスポート）はサーバーのlifespanを実際に
entryするため、ツール関数からCurrentValkeyClient(name)がDepends経由で
正しく解決されることをend-to-endで検証する。
"""

import pytest
from fastmcp import Client, FastMCP
from glide import GlideClient

from fastmcp_toolkit.valkey_lifespan import CurrentValkeyClient, valkey_lifespan


class FakeGlideClient:
    def __init__(self) -> None:
        self.closed = False
        self._store: dict[str, str] = {}

    async def close(self) -> None:
        self.closed = True

    async def set(self, key: str, value: str) -> None:
        self._store[key] = value

    async def get(self, key: str) -> str | None:
        return self._store.get(key)


@pytest.mark.asyncio
async def test_tool_resolves_valkey_client_via_depends(monkeypatch: pytest.MonkeyPatch):
    async def fake_create(config):
        return FakeGlideClient()

    monkeypatch.setattr(GlideClient, "create", fake_create)

    app = FastMCP("test", lifespan=valkey_lifespan("cache", "localhost", 6379))

    @app.tool
    async def set_and_get(
        key: str, value: str, valkey: GlideClient = CurrentValkeyClient("cache")
    ) -> str:
        await valkey.set(key, value)
        result = await valkey.get(key)
        return result or ""

    async with Client(app) as client:
        result = await client.call_tool("set_and_get", {"key": "k", "value": "v"})

    assert result.data == "v"


@pytest.mark.asyncio
async def test_multiple_named_clients_are_independent(monkeypatch: pytest.MonkeyPatch):
    async def fake_create(config):
        return FakeGlideClient()

    monkeypatch.setattr(GlideClient, "create", fake_create)

    app = FastMCP(
        "test",
        lifespan=valkey_lifespan("cache", "localhost", 6379)
        | valkey_lifespan("session", "localhost", 6380),
    )

    @app.tool
    async def compare(
        cache: GlideClient = CurrentValkeyClient("cache"),
        session: GlideClient = CurrentValkeyClient("session"),
    ) -> bool:
        # objectとしての別物性だけでなく、"cache"への書き込みが"session"側の
        # キーには見えないこと（名前ごとに正しくキー分けされていること）も
        # 検証する。
        await cache.set("k", "cache-value")
        session_value = await session.get("k")
        return cache is not session and session_value is None

    async with Client(app) as client:
        result = await client.call_tool("compare", {})

    assert result.data is True


@pytest.mark.asyncio
async def test_tool_raises_runtime_error_when_lifespan_not_registered():
    app = FastMCP("test")

    @app.tool
    async def whoami(valkey: GlideClient = CurrentValkeyClient("cache")) -> str:
        return type(valkey).__name__

    async with Client(app) as client:
        # RPC境界を越えるとサーバー側の詳細な例外メッセージ（"valkey_clients['cache']
        # is not set..."）は失われ、クライアントに届くのはDepends解決対象の
        # パラメータ名を含む "Failed to resolve dependency '<param>' for <fn>" のみ
        # になる（fastmcp-toolkitのdb_lifespan/redis_lifespanで確認済み）。
        # そのため登録名 "cache" ではなくパラメータ名 "valkey" でmatchする。
        with pytest.raises(Exception, match=r"Failed to resolve dependency 'valkey'"):
            await client.call_tool("whoami", {})


def test_get_valkey_client_error_message_includes_registration_hint():
    from types import SimpleNamespace

    from fastmcp_toolkit.valkey_lifespan import _get_valkey_client

    get_client = _get_valkey_client("cache")
    with pytest.raises(RuntimeError, match="cache"):
        get_client(SimpleNamespace(lifespan_context={}))
