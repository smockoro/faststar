"""HttpxLifespanResource/get_httpx_clientがDepends経由で正しく解決される
ことを検証する統合テスト。

HttpxLifespanResource自体の起動・終了処理はcore-toolkit側
（core_toolkit/tests/test_httpx_lifespan.py）でテスト済みのため、ここでは
FastAPI固有の部分――``Annotated[T, Depends(...)]``が実際のエンドポイントで
解決されること――だけを検証する。
"""

from typing import Annotated

import httpx
import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient

from fastapi_toolkit.httpx_lifespan import HttpxLifespanResource, get_httpx_client
from fastapi_toolkit.lifespan import create_lifespan

BackendClient = Annotated[httpx.AsyncClient, Depends(get_httpx_client("backend"))]


@pytest.mark.asyncio
async def test_httpx_client_resolves_via_depends():
    app = FastAPI()

    @app.get("/whoami")
    async def whoami(client: BackendClient) -> dict[str, bool]:
        return {"closed": client.is_closed}

    lifespan = create_lifespan(HttpxLifespanResource("backend"))
    async with lifespan(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/whoami")

    assert resp.status_code == 200
    assert resp.json() == {"closed": False}
