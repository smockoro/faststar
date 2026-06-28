"""COOPMiddlewareのテスト。"""

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from core_toolkit.middleware.coop import COOPMiddleware


async def _ok(request: Request) -> Response:
    return Response(content="ok")


def _create_app(**kwargs: object) -> Starlette:
    app = Starlette(routes=[Route("/", _ok)])
    app.add_middleware(COOPMiddleware, **kwargs)
    return app


async def _get(app: Starlette) -> Response:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.get("/")


@pytest.mark.asyncio
async def test_default():
    """デフォルト値 same-origin が付与される。"""
    resp = await _get(_create_app())
    assert resp.headers["cross-origin-opener-policy"] == "same-origin"


@pytest.mark.asyncio
async def test_custom_policy():
    """カスタムポリシーが反映される。"""
    resp = await _get(_create_app(policy="same-origin-allow-popups"))
    assert resp.headers["cross-origin-opener-policy"] == "same-origin-allow-popups"


@pytest.mark.asyncio
async def test_unsafe_none_policy():
    """unsafe-none ポリシーが反映される。"""
    resp = await _get(_create_app(policy="unsafe-none"))
    assert resp.headers["cross-origin-opener-policy"] == "unsafe-none"


@pytest.mark.asyncio
async def test_websocket_scope_passthrough():
    """websocketスコープはスルーされる。"""
    received: list[str] = []

    async def bare_app(scope: object, receive: object, send: object) -> None:
        received.append(scope["type"])  # type: ignore[index]

    await COOPMiddleware(bare_app)({"type": "websocket"}, None, None)
    assert received == ["websocket"]
