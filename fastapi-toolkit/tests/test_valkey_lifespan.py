"""ValkeyLifespanResource/get_valkey_clientがDepends経由で正しく解決される
ことを検証する統合テスト。

ValkeyLifespanResource自体の起動・終了処理はcore-toolkit側
（core_toolkit/tests/test_valkey_lifespan.py）でテスト済みのため、ここでは
FastAPI固有の部分――``Annotated[T, Depends(...)]``が実際のエンドポイントで
解決されること――だけを検証する。
"""

from typing import Annotated

import pytest
from fastapi import Depends, FastAPI
from glide import GlideClient
from httpx import ASGITransport, AsyncClient

from fastapi_toolkit.lifespan import create_lifespan
from fastapi_toolkit.valkey_lifespan import ValkeyLifespanResource, get_valkey_client

CacheValkeyClient = Annotated[GlideClient, Depends(get_valkey_client("cache"))]


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
async def test_valkey_client_resolves_via_depends(monkeypatch: pytest.MonkeyPatch):
    async def fake_create(config):
        return FakeGlideClient()

    monkeypatch.setattr(GlideClient, "create", fake_create)

    app = FastAPI()

    @app.put("/cache/{key}")
    async def write(key: str, value: str, valkey: CacheValkeyClient) -> dict[str, bool]:
        await valkey.set(key, value)
        return {"ok": True}

    @app.get("/cache/{key}")
    async def read(key: str, valkey: CacheValkeyClient) -> dict[str, str | None]:
        result = await valkey.get(key)
        return {"value": result}

    lifespan = create_lifespan(ValkeyLifespanResource("cache", "localhost", 6379))
    async with lifespan(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.put("/cache/greeting", params={"value": "hello"})
            resp = await client.get("/cache/greeting")

    assert resp.status_code == 200
    assert resp.json() == {"value": "hello"}


@pytest.mark.asyncio
async def test_valkey_client_returns_none_for_missing_key(monkeypatch: pytest.MonkeyPatch):
    async def fake_create(config):
        return FakeGlideClient()

    monkeypatch.setattr(GlideClient, "create", fake_create)

    app = FastAPI()

    @app.get("/cache/{key}")
    async def read(key: str, valkey: CacheValkeyClient) -> dict[str, str | None]:
        result = await valkey.get(key)
        return {"value": result}

    lifespan = create_lifespan(ValkeyLifespanResource("cache", "localhost", 6379))
    async with lifespan(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/cache/missing")

    assert resp.status_code == 200
    assert resp.json() == {"value": None}
