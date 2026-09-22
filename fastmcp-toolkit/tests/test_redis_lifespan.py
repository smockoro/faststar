"""redis_lifespan / CurrentRedisClient の統合テスト。

Client(app)（インメモリトランスポート）はサーバーのlifespanを実際に
entryするため、ツール関数からCurrentRedisClient(name)がDepends経由で
正しく解決されることをend-to-endで検証する。
"""

import pytest
from fakeredis.aioredis import FakeRedis
from fastmcp import Client, FastMCP
from redis.asyncio import Redis

from fastmcp_toolkit.redis_lifespan import CurrentRedisClient, redis_lifespan


@pytest.mark.asyncio
async def test_tool_resolves_redis_client_via_depends(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("fastmcp_toolkit.redis_lifespan.Redis", FakeRedis)
    await FakeRedis.from_url("redis://localhost:6379/0").flushall()

    app = FastMCP("test", lifespan=redis_lifespan("cache", "redis://localhost:6379/0"))

    @app.tool
    async def set_and_get(
        key: str, value: str, redis: Redis = CurrentRedisClient("cache")
    ) -> str:
        await redis.set(key, value)
        result = await redis.get(key)
        return result.decode() if result else ""

    async with Client(app) as client:
        result = await client.call_tool("set_and_get", {"key": "k", "value": "v"})

    assert result.data == "v"


@pytest.mark.asyncio
async def test_multiple_named_clients_are_independent(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("fastmcp_toolkit.redis_lifespan.Redis", FakeRedis)
    await FakeRedis.from_url("redis://localhost:6379/0").flushall()
    await FakeRedis.from_url("redis://localhost:6379/1").flushall()

    app = FastMCP(
        "test",
        lifespan=redis_lifespan("cache", "redis://localhost:6379/0")
        | redis_lifespan("session", "redis://localhost:6379/1"),
    )

    @app.tool
    async def compare(
        cache: Redis = CurrentRedisClient("cache"),
        session: Redis = CurrentRedisClient("session"),
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
    async def whoami(redis: Redis = CurrentRedisClient("cache")) -> str:
        return type(redis).__name__

    async with Client(app) as client:
        # RPC境界を越えるとサーバー側の詳細な例外メッセージ（"redis_clients['cache']
        # is not set..."）は失われ、クライアントに届くのはDepends解決対象の
        # パラメータ名を含む "Failed to resolve dependency '<param>' for <fn>" のみ
        # になる（fastmcp-toolkitのdb_lifespanで確認済み、a9b2fb9参照）。
        # そのため登録名 "cache" ではなくパラメータ名 "redis" でmatchする。
        with pytest.raises(Exception, match=r"Failed to resolve dependency 'redis'"):
            await client.call_tool("whoami", {})


def test_get_redis_client_error_message_includes_registration_hint():
    from types import SimpleNamespace

    from fastmcp_toolkit.redis_lifespan import _get_redis_client

    get_client = _get_redis_client("cache")
    with pytest.raises(RuntimeError, match="cache"):
        get_client(SimpleNamespace(lifespan_context={}))
