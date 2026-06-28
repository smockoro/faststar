"""FrameGuardMiddlewareのテスト。"""

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from core_toolkit.middleware.frame_guard import FrameGuardMiddleware


async def _ok(request: Request) -> Response:
    return Response(content="ok")


def _create_app(**kwargs: object) -> Starlette:
    app = Starlette(routes=[Route("/", _ok)])
    app.add_middleware(FrameGuardMiddleware, **kwargs)
    return app


async def _get(app: Starlette) -> Response:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.get("/")


@pytest.mark.asyncio
async def test_default():
    """デフォルト値 DENY が付与される。"""
    resp = await _get(_create_app())
    assert resp.headers["x-frame-options"] == "DENY"


@pytest.mark.asyncio
async def test_sameorigin():
    """action=SAMEORIGINが反映される。"""
    resp = await _get(_create_app(action="SAMEORIGIN"))
    assert resp.headers["x-frame-options"] == "SAMEORIGIN"


@pytest.mark.asyncio
async def test_websocket_scope_passthrough():
    """websocketスコープはスルーされる。"""
    received: list[str] = []

    async def bare_app(scope: object, receive: object, send: object) -> None:
        received.append(scope["type"])  # type: ignore[index]

    await FrameGuardMiddleware(bare_app)({"type": "websocket"}, None, None)
    assert received == ["websocket"]
