"""ObjectStorageLifespanResource/get_object_storageがDepends経由で正しく
解決されることを検証する統合テスト。

ObjectStorageLifespanResource自体の起動・終了処理はcore-toolkit側
（core_toolkit/tests/test_object_storage_lifespan.py）でテスト済みのため、
ここではFastAPI固有の部分――``Annotated[T, Depends(...)]``が実際の
エンドポイントで解決されること――だけを検証する。scheme="memory"を使うことで
バックエンドSDKへの依存無しにテストできる。
"""

from typing import Annotated

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient

from fastapi_toolkit.lifespan import create_lifespan
from fastapi_toolkit.object_storage_lifespan import (
    ObjectStorage,
    ObjectStorageLifespanResource,
    get_object_storage,
)

MainStorage = Annotated[ObjectStorage, Depends(get_object_storage("main"))]


@pytest.mark.asyncio
async def test_object_storage_resolves_via_depends():
    app = FastAPI()

    @app.put("/objects/{key}")
    async def write(key: str, value: str, storage: MainStorage) -> dict[str, bool]:
        await storage.put("uploads", key, value.encode())
        return {"ok": True}

    @app.get("/objects/{key}")
    async def read(key: str, storage: MainStorage) -> dict[str, str]:
        data = await storage.get("uploads", key)
        return {"value": data.decode()}

    lifespan = create_lifespan(ObjectStorageLifespanResource("main", "memory", {"uploads": "uploads-x7f3"}))
    async with lifespan(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.put("/objects/greeting", params={"value": "hello"})
            resp = await client.get("/objects/greeting")

    assert resp.status_code == 200
    assert resp.json() == {"value": "hello"}


@pytest.mark.asyncio
async def test_object_storage_raises_error_response_for_unknown_bucket():
    app = FastAPI()

    @app.get("/objects/{key}")
    async def read(key: str, storage: MainStorage) -> dict[str, str]:
        data = await storage.get("uploads", key)
        return {"value": data.decode()}

    lifespan = create_lifespan(ObjectStorageLifespanResource("main", "memory", {"uploads": "uploads-x7f3"}))
    async with lifespan(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            with pytest.raises(Exception, match="not found"):
                await client.get("/objects/missing")
