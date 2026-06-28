"""DNSPrefetchControlMiddlewareのテスト。"""

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from core_toolkit.middleware.dns_prefetch_control import DNSPrefetchControlMiddleware


async def _ok(request: Request) -> Response:
    return Response(content="ok")


async def _get(app: Starlette) -> Response:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.get("/")


@pytest.mark.asyncio
async def test_adds_header():
    """x-dns-prefetch-control: off が付与される。"""
    app = Starlette(routes=[Route("/", _ok)])
    app.add_middleware(DNSPrefetchControlMiddleware)
    resp = await _get(app)
    assert resp.headers["x-dns-prefetch-control"] == "off"


@pytest.mark.asyncio
async def test_websocket_scope_passthrough():
    """websocketスコープはスルーされる。"""
    received: list[str] = []

    async def bare_app(scope: object, receive: object, send: object) -> None:
        received.append(scope["type"])  # type: ignore[index]

    await DNSPrefetchControlMiddleware(bare_app)({"type": "websocket"}, None, None)
    assert received == ["websocket"]
