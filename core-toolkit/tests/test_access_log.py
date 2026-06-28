"""AccessLogMiddlewareのテスト。"""

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from core_toolkit.middleware.access_log import AccessLogMiddleware


def _create_app() -> Starlette:
    async def homepage(request: Request) -> JSONResponse:
        return JSONResponse({"ok": True})

    async def health(request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    app = Starlette(
        routes=[
            Route("/", homepage),
            Route("/livez", health),
            Route("/readyz", health),
            Route("/metrics", health),
        ]
    )
    app.add_middleware(AccessLogMiddleware)
    return app


async def _client(app: Starlette) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_normal_request_passes():
    app = _create_app()
    async with await _client(app) as client:
        resp = await client.get("/")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


@pytest.mark.asyncio
async def test_health_endpoints_pass():
    app = _create_app()
    async with await _client(app) as client:
        for path in ["/livez", "/readyz", "/metrics"]:
            resp = await client.get(path)
            assert resp.status_code == 200
