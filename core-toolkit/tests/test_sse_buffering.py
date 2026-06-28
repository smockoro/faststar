"""SSEBufferingMiddlewareのテスト。"""

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from core_toolkit.middleware.sse_buffering import SSEBufferingMiddleware


async def _sse(request: Request) -> Response:
    return Response(content="data: hello\n\n", media_type="text/event-stream")


async def _sse_with_charset(request: Request) -> Response:
    return Response(
        content="data: hello\n\n",
        headers={"Content-Type": "text/event-stream; charset=utf-8"},
    )


async def _json(request: Request) -> Response:
    return Response(content='{"ok":true}', media_type="application/json")


async def _html(request: Request) -> Response:
    return Response(content="<h1>hello</h1>", media_type="text/html")


async def _no_content_type(request: Request) -> Response:
    return Response(status_code=204)


def _create_app(handler) -> Starlette:
    app = Starlette(routes=[Route("/", handler)])
    app.add_middleware(SSEBufferingMiddleware)
    return app


async def _get(app: Starlette) -> Response:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.get("/")


@pytest.mark.asyncio
async def test_adds_header_for_sse():
    """Content-Typeがtext/event-streamのときx-accel-buffering: noを付与する。"""
    resp = await _get(_create_app(_sse))
    assert resp.headers["x-accel-buffering"] == "no"


@pytest.mark.asyncio
async def test_adds_header_for_sse_with_charset():
    """Content-Typeにcharset付きのtext/event-streamでも付与する。"""
    resp = await _get(_create_app(_sse_with_charset))
    assert resp.headers["x-accel-buffering"] == "no"


@pytest.mark.asyncio
async def test_skips_json():
    """JSONレスポンスにはx-accel-bufferingを付与しない。"""
    resp = await _get(_create_app(_json))
    assert "x-accel-buffering" not in resp.headers


@pytest.mark.asyncio
async def test_skips_html():
    """text/htmlにはx-accel-bufferingを付与しない。"""
    resp = await _get(_create_app(_html))
    assert "x-accel-buffering" not in resp.headers


@pytest.mark.asyncio
async def test_skips_no_content_type():
    """Content-Typeヘッダーがないレスポンスには付与しない。"""
    resp = await _get(_create_app(_no_content_type))
    assert "x-accel-buffering" not in resp.headers


@pytest.mark.asyncio
async def test_websocket_scope_passthrough():
    """websocketスコープはスルーされる。"""
    received: list[str] = []

    async def bare_app(scope: object, receive: object, send: object) -> None:
        received.append(scope["type"])  # type: ignore[index]

    await SSEBufferingMiddleware(bare_app)({"type": "websocket"}, None, None)
    assert received == ["websocket"]
