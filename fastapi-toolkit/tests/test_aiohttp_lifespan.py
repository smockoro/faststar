"""AioHttpLifespanResource/get_aiohttp_clientがDepends経由で正しく解決される
ことを検証する統合テスト。

AioHttpLifespanResource自体の起動・終了処理はcore-toolkit側
（core_toolkit/tests/test_aiohttp_lifespan.py）でテスト済みのため、ここでは
FastAPI固有の部分――``Annotated[T, Depends(...)]``が実際のエンドポイントで
解決されること――だけを検証する。
"""

from typing import Annotated

import aiohttp
import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient

from fastapi_toolkit.aiohttp_lifespan import AioHttpLifespanResource, get_aiohttp_client
from fastapi_toolkit.lifespan import create_lifespan

BackendClient = Annotated[aiohttp.ClientSession, Depends(get_aiohttp_client("backend"))]


@pytest.mark.asyncio
async def test_aiohttp_client_resolves_via_depends():
    app = FastAPI()

    @app.get("/whoami")
    async def whoami(session: BackendClient) -> dict[str, bool]:
        return {"closed": session.closed}

    lifespan = create_lifespan(AioHttpLifespanResource("backend"))
    async with lifespan(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/whoami")

    assert resp.status_code == 200
    assert resp.json() == {"closed": False}
