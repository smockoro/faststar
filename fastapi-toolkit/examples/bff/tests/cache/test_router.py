"""Controller層の結合テスト。

差し替える層の粒度を3パターン示す:

- ``get_cache_redis_client`` を差し替える: Repository/Usecaseは本物を
  使い、Redisの代わりにfakeredisで動かす（配線全体の疎通確認）。
- ``get_cache_repository`` を差し替える: Redisにすら触れず、
  Controller〜Usecaseの結線だけを検証する。
- 何も差し替えず実際のapp lifespanに入る: ``app.py`` が
  ``RedisLifespanResource("cache", ...)`` で登録する名前と
  ``dependencies.py`` が ``get_redis_client("cache")`` で解決する名前が
  一致していることまで含めてend-to-endで検証する（差し替え系のテストは
  ``dependency_overrides`` で常に迂回してしまうため、この経路だけが
  登録名と解決名の対応を実際にチェックする）。
"""

import pytest
from fakeredis.aioredis import FakeRedis
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def _override_redis_client():
    from bff.app import app
    from bff.cache.dependencies import get_cache_redis_client

    # 同一インスタンスを使い回さないと、write用リクエストとread用リクエストで
    # 別々のFakeRedisが解決されてしまい、書き込みが読み込み側に反映されない。
    fake_redis = FakeRedis()
    app.dependency_overrides[get_cache_redis_client] = lambda: fake_redis
    yield
    app.dependency_overrides.pop(get_cache_redis_client, None)


async def test_read_cache_returns_404_when_key_missing(_override_redis_client):
    from bff.app import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/cache/missing-key")

    assert resp.status_code == 404


async def test_write_then_read_cache_roundtrip(_override_redis_client):
    from bff.app import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        write_resp = await client.put("/cache/greeting", json={"value": "hello"})
        read_resp = await client.get("/cache/greeting")

    assert write_resp.status_code == 200
    assert write_resp.json() == {"key": "greeting", "value": "hello"}
    assert read_resp.status_code == 200
    assert read_resp.json() == {"key": "greeting", "value": "hello"}


async def test_read_cache_with_repository_override_bypasses_redis(_override_redis_client):
    from bff.app import app
    from bff.cache.dependencies import get_cache_repository
    from bff.cache.domain import CacheRepository

    class FixedCacheRepository(CacheRepository):
        async def get(self, key: str) -> str | None:
            return "fixed-value" if key == "known" else None

        async def set(self, key: str, value: str) -> None:
            raise AssertionError("write should not be called in this test")

    app.dependency_overrides[get_cache_repository] = lambda: FixedCacheRepository()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/cache/known")
    finally:
        app.dependency_overrides.pop(get_cache_repository, None)

    assert resp.status_code == 200
    assert resp.json() == {"key": "known", "value": "fixed-value"}


async def test_write_then_read_cache_roundtrip_via_real_lifespan(monkeypatch: pytest.MonkeyPatch):
    """dependency_overridesを一切使わず、実際のapp lifespanを経由して
    registration→resolutionの対応（"cache"という名前の一致）を検証する。
    """
    monkeypatch.setattr("core_toolkit.redis_lifespan.Redis", FakeRedis)

    from bff.app import app

    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            write_resp = await client.put("/cache/greeting", json={"value": "hello"})
            read_resp = await client.get("/cache/greeting")

    assert write_resp.status_code == 200
    assert write_resp.json() == {"key": "greeting", "value": "hello"}
    assert read_resp.status_code == 200
    assert read_resp.json() == {"key": "greeting", "value": "hello"}
