"""cache_server.py（fastmcp-toolkitのredis_lifespanサンプル）の動作確認テスト。

examples/配下はパッケージ化されていないフラットスクリプト構成のため、この
テストもexamples/に同居させ、``cd fastmcp-toolkit && uv run pytest
examples/test_cache.py -v`` のように実行する。
"""

import pytest
from cache_server import app
from fakeredis.aioredis import FakeRedis
from fastmcp import Client


@pytest.mark.asyncio
async def test_write_then_read_cache_roundtrip(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("fastmcp_toolkit.redis_lifespan.Redis", FakeRedis)
    await FakeRedis.from_url("redis://localhost:6379/0").flushall()

    async with Client(app) as client:
        await client.call_tool("write_cache", {"key": "greeting", "value": "hello"})
        result = await client.call_tool("read_cache", {"key": "greeting"})

    assert result.data == "hello"


@pytest.mark.asyncio
async def test_read_cache_returns_empty_string_when_missing(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr("fastmcp_toolkit.redis_lifespan.Redis", FakeRedis)

    async with Client(app) as client:
        result = await client.call_tool("read_cache", {"key": "missing"})

    assert result.data == ""
