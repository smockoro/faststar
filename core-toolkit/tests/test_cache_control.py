"""CacheControlMiddlewareのテスト。"""

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from core_toolkit.middleware.cache_control import CacheControlMiddleware


async def _ok(request: Request) -> Response:
    return Response(content="ok")


async def _with_cache_control(request: Request) -> Response:
    return Response(content="ok", headers={"Cache-Control": "public, max-age=3600"})


async def _server_error(request: Request) -> Response:
    return Response(status_code=500, content="error")


def _create_app(handler=_ok, **kwargs: object) -> Starlette:
    app = Starlette(routes=[Route("/", handler)])
    app.add_middleware(CacheControlMiddleware, **kwargs)
    return app


async def _get(app: Starlette) -> Response:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.get("/")


@pytest.mark.asyncio
async def test_default():
    """デフォルト値 no-store, max-age=0 が付与される。"""
    resp = await _get(_create_app())
    assert resp.headers["cache-control"] == "no-store, max-age=0"


@pytest.mark.asyncio
async def test_custom_value():
    """カスタム値が反映される。"""
    resp = await _get(_create_app(value="no-store"))
    assert resp.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_on_500():
    """500レスポンスでも付与される。"""
    resp = await _get(_create_app(_server_error))
    assert resp.status_code == 500
    assert resp.headers["cache-control"] == "no-store, max-age=0"


@pytest.mark.asyncio
async def test_appends_to_existing():
    """アプリが設定済みのCache-Controlに加えてミドルウェアのものが追加される。"""
    resp = await _get(_create_app(_with_cache_control))
    values = resp.headers.get_list("cache-control")
    assert len(values) == 2
    assert "public, max-age=3600" in values
    assert "no-store, max-age=0" in values


@pytest.mark.asyncio
async def test_duplicated_stack():
    """2重スタックするとヘッダーが2個付与される。"""
    app = Starlette(routes=[Route("/", _ok)])
    app.add_middleware(CacheControlMiddleware)
    app.add_middleware(CacheControlMiddleware)
    resp = await _get(app)
    assert len(resp.headers.get_list("cache-control")) == 2


@pytest.mark.asyncio
async def test_websocket_scope_passthrough():
    """websocketスコープはスルーされる。"""
    received: list[str] = []

    async def bare_app(scope: object, receive: object, send: object) -> None:
        received.append(scope["type"])  # type: ignore[index]

    await CacheControlMiddleware(bare_app)({"type": "websocket"}, None, None)
    assert received == ["websocket"]
