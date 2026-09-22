"""redis_lifespanの統合テスト。"""

import pytest
from fakeredis.aioredis import FakeRedis
from starlette.applications import Starlette
from starlette.requests import Request

from core_toolkit.lifespan import create_lifespan
from core_toolkit.redis_lifespan import RedisLifespanResource, get_redis_client


def _make_request(app: Starlette) -> Request:
    return Request({"type": "http", "app": app})


@pytest.mark.asyncio
async def test_get_redis_client_returns_registered_client(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr("core_toolkit.redis_lifespan.Redis", FakeRedis)
    app = Starlette()
    lifespan = create_lifespan(
        RedisLifespanResource("cache", "redis://localhost:6379/0")
    )

    async with lifespan(app):
        client = get_redis_client("cache")(_make_request(app))
        await client.set("k", "v")
        assert await client.get("k") == b"v"


def test_get_redis_client_raises_runtime_error_when_missing():
    app = Starlette()

    with pytest.raises(RuntimeError, match="cache"):
        get_redis_client("cache")(_make_request(app))


@pytest.mark.asyncio
async def test_multiple_clients_registered_independently(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr("core_toolkit.redis_lifespan.Redis", FakeRedis)
    app = Starlette()
    lifespan = create_lifespan(
        RedisLifespanResource("cache", "redis://localhost:6379/0"),
        RedisLifespanResource("session", "redis://localhost:6379/1"),
    )

    async with lifespan(app):
        cache_client = get_redis_client("cache")(_make_request(app))
        session_client = get_redis_client("session")(_make_request(app))
        assert cache_client is not session_client


@pytest.mark.asyncio
async def test_redis_lifespan_resource_closes_client_on_exit(
    monkeypatch: pytest.MonkeyPatch,
):
    closed = False

    class TrackingFakeRedis(FakeRedis):
        async def aclose(self, *args, **kwargs):
            nonlocal closed
            closed = True
            await super().aclose(*args, **kwargs)

    monkeypatch.setattr("core_toolkit.redis_lifespan.Redis", TrackingFakeRedis)
    app = Starlette()
    lifespan = create_lifespan(
        RedisLifespanResource("cache", "redis://localhost:6379/0")
    )

    async with lifespan(app):
        pass

    assert closed
