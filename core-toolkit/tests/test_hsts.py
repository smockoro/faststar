"""HSTSMiddlewareのテスト。"""

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from core_toolkit.middleware.hsts import HSTSMiddleware


async def _ok(request: Request) -> Response:
    return Response(content="ok")


async def _server_error(request: Request) -> Response:
    return Response(status_code=500, content="error")


def _create_app(handler=_ok, **kwargs: object) -> Starlette:
    app = Starlette(routes=[Route("/", handler)])
    app.add_middleware(HSTSMiddleware, **kwargs)
    return app


async def _get(app: Starlette) -> Response:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.get("/")


@pytest.mark.asyncio
async def test_default():
    """デフォルト値 max-age=63072000; includeSubDomains が付与される。"""
    resp = await _get(_create_app())
    expected = "max-age=63072000; includeSubDomains"
    assert resp.headers["strict-transport-security"] == expected


@pytest.mark.asyncio
async def test_custom_max_age():
    """カスタムmax_ageが反映される。"""
    resp = await _get(_create_app(max_age=31536000))
    expected = "max-age=31536000; includeSubDomains"
    assert resp.headers["strict-transport-security"] == expected


@pytest.mark.asyncio
async def test_no_subdomains():
    """include_subdomains=FalseのときincludeSubDomainsが省略される。"""
    resp = await _get(_create_app(max_age=31536000, include_subdomains=False))
    assert resp.headers["strict-transport-security"] == "max-age=31536000"


@pytest.mark.asyncio
async def test_max_age_zero():
    """max_age=0はHSTSの無効化を表す。includeSubDomainsは引き続き付与される。"""
    resp = await _get(_create_app(max_age=0))
    assert resp.headers["strict-transport-security"] == "max-age=0; includeSubDomains"


@pytest.mark.asyncio
async def test_max_age_zero_no_subdomains():
    """max_age=0でinclude_subdomains=Falseの組み合わせ。"""
    resp = await _get(_create_app(max_age=0, include_subdomains=False))
    assert resp.headers["strict-transport-security"] == "max-age=0"


@pytest.mark.asyncio
async def test_on_500():
    """500レスポンスでも付与される。"""
    resp = await _get(_create_app(_server_error))
    assert resp.status_code == 500
    assert "strict-transport-security" in resp.headers


@pytest.mark.asyncio
async def test_preserves_existing_headers():
    """他のヘッダーを破壊しない。"""

    async def handler(request: Request) -> Response:
        return Response(content="ok", headers={"X-My-Header": "value"})

    app = Starlette(routes=[Route("/", handler)])
    app.add_middleware(HSTSMiddleware)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/")
    assert resp.headers["x-my-header"] == "value"
    assert "strict-transport-security" in resp.headers


@pytest.mark.asyncio
async def test_websocket_scope_passthrough():
    """websocketスコープはスルーされる。"""
    received: list[str] = []

    async def bare_app(scope: object, receive: object, send: object) -> None:
        received.append(scope["type"])  # type: ignore[index]

    await HSTSMiddleware(bare_app)({"type": "websocket"}, None, None)
    assert received == ["websocket"]
