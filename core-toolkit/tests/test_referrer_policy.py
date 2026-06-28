"""ReferrerPolicyMiddlewareのテスト。"""

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from core_toolkit.middleware.referrer_policy import ReferrerPolicyMiddleware


async def _ok(request: Request) -> Response:
    return Response(content="ok")


def _create_app(**kwargs: object) -> Starlette:
    app = Starlette(routes=[Route("/", _ok)])
    app.add_middleware(ReferrerPolicyMiddleware, **kwargs)
    return app


async def _get(app: Starlette) -> Response:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.get("/")


@pytest.mark.asyncio
async def test_default():
    """デフォルト値 no-referrer が付与される。"""
    resp = await _get(_create_app())
    assert resp.headers["referrer-policy"] == "no-referrer"


@pytest.mark.asyncio
async def test_custom_policy():
    """カスタムポリシーが反映される。"""
    resp = await _get(_create_app(policy="strict-origin-when-cross-origin"))
    assert resp.headers["referrer-policy"] == "strict-origin-when-cross-origin"


@pytest.mark.asyncio
async def test_origin_policy():
    """origin ポリシーが反映される。"""
    resp = await _get(_create_app(policy="origin"))
    assert resp.headers["referrer-policy"] == "origin"


@pytest.mark.asyncio
async def test_preserves_existing_headers():
    """他のヘッダーを破壊しない。"""

    async def handler(request: Request) -> Response:
        return Response(content="ok", headers={"X-Custom": "value"})

    app = Starlette(routes=[Route("/", handler)])
    app.add_middleware(ReferrerPolicyMiddleware)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/")
    assert resp.headers["x-custom"] == "value"
    assert resp.headers["referrer-policy"] == "no-referrer"


@pytest.mark.asyncio
async def test_websocket_scope_passthrough():
    """websocketスコープはスルーされる。"""
    received: list[str] = []

    async def bare_app(scope: object, receive: object, send: object) -> None:
        received.append(scope["type"])  # type: ignore[index]

    await ReferrerPolicyMiddleware(bare_app)({"type": "websocket"}, None, None)
    assert received == ["websocket"]
