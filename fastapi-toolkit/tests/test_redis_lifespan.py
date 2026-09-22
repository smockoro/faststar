"""RedisLifespanResource/get_redis_clientがDepends経由で正しく解決される
ことを検証する統合テスト。

RedisLifespanResource自体の起動・終了処理はcore-toolkit側
（core_toolkit/tests/test_redis_lifespan.py）でテスト済みのため、ここでは
FastAPI固有の部分――``Annotated[T, Depends(...)]``が実際のエンドポイントで
解決されること――だけを検証する。
"""

from typing import Annotated

import pytest
from fakeredis.aioredis import FakeRedis
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis

from fastapi_toolkit.lifespan import create_lifespan
from fastapi_toolkit.redis_lifespan import RedisLifespanResource, get_redis_client

CacheRedisClient = Annotated[Redis, Depends(get_redis_client("cache"))]


@pytest.mark.asyncio
async def test_redis_client_resolves_via_depends(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("core_toolkit.redis_lifespan.Redis", FakeRedis)

    app = FastAPI()

    @app.put("/cache/{key}")
    async def write(key: str, value: str, redis: CacheRedisClient) -> dict[str, bool]:
        await redis.set(key, value)
        return {"ok": True}

    @app.get("/cache/{key}")
    async def read(key: str, redis: CacheRedisClient) -> dict[str, str | None]:
        result = await redis.get(key)
        return {"value": result.decode() if result is not None else None}

    lifespan = create_lifespan(RedisLifespanResource("cache", "redis://localhost:6379/0"))
    async with lifespan(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.put("/cache/greeting", params={"value": "hello"})
            resp = await client.get("/cache/greeting")

    assert resp.status_code == 200
    assert resp.json() == {"value": "hello"}


@pytest.mark.asyncio
async def test_redis_client_returns_none_for_missing_key(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("core_toolkit.redis_lifespan.Redis", FakeRedis)

    app = FastAPI()

    @app.get("/cache/{key}")
    async def read(key: str, redis: CacheRedisClient) -> dict[str, str | None]:
        result = await redis.get(key)
        return {"value": result.decode() if result is not None else None}

    lifespan = create_lifespan(RedisLifespanResource("cache", "redis://localhost:6379/0"))
    async with lifespan(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/cache/missing")

    assert resp.status_code == 200
    assert resp.json() == {"value": None}
