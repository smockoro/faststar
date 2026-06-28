"""NoSniffMiddlewareのテスト。"""

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from core_toolkit.middleware.no_sniff import NoSniffMiddleware


async def _ok(request: Request) -> JSONResponse:
    return JSONResponse({"ok": True})


async def _not_found(request: Request) -> Response:
    return Response(status_code=404, content="not found")


def _create_app(*extra_routes: Route) -> Starlette:
    routes = [Route("/", _ok), *extra_routes]
    app = Starlette(routes=routes)
    app.add_middleware(NoSniffMiddleware)
    return app


async def _get(app: Starlette, path: str = "/") -> Response:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.get(path)


def test_adds_header_sync():
    """ミドルウェアを直接インスタンス化できる。"""
    NoSniffMiddleware(app=lambda s, r, send: None)


@pytest.mark.asyncio
async def test_adds_header():
    """x-content-type-options: nosniffが付与される。"""
    resp = await _get(_create_app())
    assert resp.headers["x-content-type-options"] == "nosniff"


@pytest.mark.asyncio
async def test_on_404():
    """404レスポンスでも付与される。"""
    app = Starlette(routes=[Route("/", _not_found)])
    app.add_middleware(NoSniffMiddleware)
    resp = await _get(app)
    assert resp.status_code == 404
    assert resp.headers["x-content-type-options"] == "nosniff"


@pytest.mark.asyncio
async def test_preserves_existing_headers():
    """アプリが設定した他のヘッダーを破壊しない。"""

    async def handler(request: Request) -> Response:
        return Response(content="ok", headers={"X-Custom": "value"})

    app = Starlette(routes=[Route("/", handler)])
    app.add_middleware(NoSniffMiddleware)
    resp = await _get(app)
    assert resp.headers["x-custom"] == "value"
    assert resp.headers["x-content-type-options"] == "nosniff"


@pytest.mark.asyncio
async def test_duplicated_stack():
    """同一ミドルウェアを2重スタックするとヘッダーが2個付与される。"""
    app = Starlette(routes=[Route("/", _ok)])
    app.add_middleware(NoSniffMiddleware)
    app.add_middleware(NoSniffMiddleware)
    resp = await _get(app)
    assert len(resp.headers.get_list("x-content-type-options")) == 2


@pytest.mark.asyncio
async def test_appends_when_already_present():
    """アプリがヘッダーを付与済みでもミドルウェアが追加する。"""

    async def handler(request: Request) -> Response:
        return Response(content="ok", headers={"X-Content-Type-Options": "nosniff"})

    app = Starlette(routes=[Route("/", handler)])
    app.add_middleware(NoSniffMiddleware)
    resp = await _get(app)
    assert len(resp.headers.get_list("x-content-type-options")) == 2


@pytest.mark.asyncio
async def test_websocket_scope_passthrough():
    """websocketスコープはミドルウェアをスルーして次のアプリに渡る。"""
    received: list[str] = []

    async def bare_app(scope: object, receive: object, send: object) -> None:
        received.append(scope["type"])  # type: ignore[index]

    await NoSniffMiddleware(bare_app)({"type": "websocket"}, None, None)
    assert received == ["websocket"]
